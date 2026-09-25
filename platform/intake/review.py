"""Reviewer decisions for internal-non-sensitive admissions (`/intake/reviews`).

A reviewer needs BOTH a current IAM-administered `adm_grant` covering the project
with `review-internal` AND current source access through `Sources.resolve`.
The grant never confers source access; project ownership or in-app operator
groups never confer review authority. The route confers no admin rights.
"""
from __future__ import annotations

import json

from intake import admission, inspect, records
from intake.admission import AdmissionError
from intake.records import INTAKE_OWNER
from workspace.collaboration import CollaborationError
from workspace.ontology_sources import Sources
from workspace.storage import Conflict

MAX_LISTED = 50
PREVIEW_CHARS = 1000
MAX_ROWS_PREVIEW, MAX_CELLS_PREVIEW, MAX_CELL_PREVIEW = 20, 20, 200


def _grant_for(storage, actor, policy, project_id):
    grant = admission.find_grant(storage, actor, policy, project_id)
    if grant is None:
        raise AdmissionError("review-grant-required", 403)
    return grant


def _any_grant(storage, actor, project_id):
    now = storage.clock()
    grants = [g for g in admission._admin_records(storage, "adm_grant")
              if records.is_current(g, now) and g["actor"] == actor
              and admission._in_scope(g["scope"], project_id, deployment_bound=False)]
    if not grants:
        raise AdmissionError("review-grant-required", 403)
    return grants


def _source_access(host, scope, decision, reader):
    try:
        admission._source_current(host, scope, decision, reader)
    except AdmissionError:
        raise AdmissionError("source-access-required", 403) from None


def _pending(host, scope, decision_id):
    decision = admission._load(host, scope, decision_id)
    if decision["status"] != "pending-review" or decision["expiresAt"] <= host.storage.clock():
        raise AdmissionError("decision-not-pending")
    return decision


def decide(host, scope, decision_id, *, approve, reason, claims=None):
    """Approve or reject a pending decision as a current grant holder with source access."""
    if type(approve) is not bool:
        raise AdmissionError("invalid-review", 400)
    # The free-text reason is validated but never stored: it could carry originals.
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise AdmissionError("invalid-review", 400)
    storage, owner, project_id = host.storage, scope["owner"], admission._project(scope)
    decision = _pending(host, scope, decision_id)
    policy = admission._policy_current(storage, decision, project_id)
    grant = _grant_for(storage, scope["actor"], policy, project_id)
    reader = Sources(inspect.context(host, scope, claims))
    _source_access(host, scope, decision, reader)
    admission._read_verified(storage, owner, decision["artifact"]["key"], decision["derivation"]["derivativeHash"],
                             "artifact-changed")
    receipt = json.loads(admission._read_verified(storage, owner, decision["inspection"]["receiptKey"],
                                                  decision["inspection"]["hash"], "inspection-changed",
                                                  maximum=1024 * 1024))
    if receipt.get("blocking"):
        raise AdmissionError("inspection-blocked")
    now = storage.clock()
    review = {"actor": scope["actor"], "grantId": grant["id"], "grantRevision": grant["revision"], "at": now,
              "decision": "approved" if approve else "rejected"}
    stored = {key: value for key, value in decision.items() if key not in records.STORAGE_FIELDS}
    updated = {**stored, "revision": decision["revision"] + 1, "review": review,
               "status": "admitted" if approve else "rejected"}
    if approve:
        updated["expiresAt"] = admission._expiry(now, policy, grant)
    sealed = records.seal("adm_decision", updated)
    fences = [*reader.recheck(), admission._check(INTAKE_OWNER, "adm_policy", policy),
              admission._check(INTAKE_OWNER, "adm_grant", grant),
              {"owner": owner, "kind": "project", "id": project_id, "version": scope["project"]["version"]}]
    unique = {(c["owner"], c["kind"], c["id"]): c for c in fences}
    unique.pop((owner, "adm_decision", decision["id"]), None)
    write = {"owner": owner, "kind": "adm_decision", "item": sealed, "expected_version": decision["version"]}
    # Expiry is invisible to version fences: recheck the pending decision, policy,
    # grant and sources immediately before the commit.
    # The same guard runs before every transaction attempt (including retries).
    authority = admission.Authority(host, reader, [admission._check(owner, "adm_decision", decision),
                                                   admission._check(INTAKE_OWNER, "adm_policy", policy),
                                                   admission._check(INTAKE_OWNER, "adm_grant", grant)],
                                    pending={decision["id"]})
    authority.recheck()
    if approve and sealed["artifact"]["kind"] == "diagram-transcription":
        return _approve_transcription(host, scope, write, list(unique.values()), authority)
    try:
        return storage.put_many([write], checks=list(unique.values()), retry_conflicts=False,
                                before_attempt=authority.recheck)[0]
    except Conflict:
        raise AdmissionError("conflict") from None


def _approve_transcription(host, scope, write, checks, authority):
    """Admit a transcription and publish its in-review library revision in ONE transaction.

    If the publication cannot commit, nothing is written: the decision stays
    `pending-review` and the reviewer can retry. Library approval by a
    planner/owner remains a separate step.
    """
    from documents.errors import DocumentError
    from documents.library import prepare_transcription
    try:
        library, writes = prepare_transcription(host, scope, write["item"])
        # The pending decision, the reviewing grant, the policy and sources are
        # rechecked (with expiry) before every commit attempt, combined with the
        # library's own upstream (image) lineage guard into one aggregate
        # compared with a single fresh clock read taken after both have read.
        return library.commit([write, *writes], extra_checks=checks, guard=authority)[0]
    except DocumentError as error:
        raise AdmissionError(error.code, error.status) from None
    except Conflict:
        raise AdmissionError("conflict") from None


def _title(host, scope, decision):
    if decision["source"]["sourceKind"] != "document-revision":
        return None
    from documents.library import Library
    try:
        return Library(host, scope).document(decision["source"]["sourceId"]).get("title")
    except Exception:  # noqa: BLE001 - a title is optional display metadata
        return None


def _bounded_tables(tables):
    return [[cell[:MAX_CELL_PREVIEW] for cell in row[:MAX_CELLS_PREVIEW]] for row in tables[:MAX_ROWS_PREVIEW]]


def _preview(host, scope, decision, data):
    """A derivative-only preview serialized by artifact kind (never originals or bytes)."""
    kind = decision["artifact"]["kind"]
    if kind == "image":
        # The normalized PNG is not decoded: the reviewer gets an authorized reference
        # to the admitted vision derivative and its verified OCR text.
        from intake import imaging
        artifact, vision = decision["artifact"], decision["artifact"]["vision"]
        admission._read_verified(host.storage, scope["owner"], vision["key"], vision["sha256"], "artifact-changed")
        ocr = imaging._ocr(host, scope, decision)
        return {"count": 1, "derivativePreview": ocr["ocrText"][:PREVIEW_CHARS],
                "image": {"decisionId": decision["id"], "format": vision["format"], "width": artifact["width"],
                          "height": artifact["height"], "sha256": artifact["sha256"],
                          "visionSha256": vision["sha256"], "size": vision["size"]}}
    value = json.loads(data)
    if kind == "diagram-transcription":
        return {"count": 1, "derivativePreview": value["text"][:PREVIEW_CHARS],
                "tables": _bounded_tables(value.get("tables") or [])}
    if kind == "code-collection":  # derivative paths only
        files = value["files"]
        return {"count": len(files), "derivativePreview": "\n".join(f["path"] for f in files[:50])[:PREVIEW_CHARS]}
    # document-pages and prompt-text: ordered derivative pages
    return {"count": len(value), "derivativePreview": value[0]["text"][:PREVIEW_CHARS] if value else ""}


def list_pending(host, scope, *, claims=None):
    """Pending decisions the actor may review; derivative preview only, never originals."""
    storage, owner, project_id = host.storage, scope["owner"], admission._project(scope)
    grants = _any_grant(storage, scope["actor"], project_id)
    policies = {g["policyId"] for g in grants}
    items, authorities, cursor, scanned = [], [], None, 0
    while len(items) < MAX_LISTED:
        page = storage.list_page(owner, "adm_decision", limit=100, cursor=cursor)
        for row in page["items"]:
            scanned += 1
            try:
                decision = records.validate("adm_decision", row)
            except ValueError:
                continue
            if (decision["projectId"] != project_id or decision["status"] != "pending-review"
                    or decision["expiresAt"] <= storage.clock() or decision["policy"]["id"] not in policies):
                continue
            reader = Sources(inspect.context(host, scope, claims))
            try:
                policy = admission._policy_current(storage, decision, project_id)
                _source_access(host, scope, decision, reader)
                data = admission._read_verified(storage, owner, decision["artifact"]["key"],
                                                decision["derivation"]["derivativeHash"], "artifact-changed")
                receipt = json.loads(admission._read_verified(
                    storage, owner, decision["inspection"]["receiptKey"], decision["inspection"]["hash"],
                    "inspection-changed", maximum=1024 * 1024))
            except (AdmissionError, CollaborationError):
                continue
            try:
                preview = _preview(host, scope, decision, data)
                title = _title(host, scope, decision)
                # A pending transcription is reviewable only while its admitted image
                # decision is (recursively) current; its records join this item's fence.
                lineage = (admission._lineage_checks(host, scope, decision, reader, claims)
                           if decision["artifact"]["kind"] == "diagram-transcription" else [])
                # After the last read: the pending decision, its policy, the actor's
                # grants for that policy, the lineage and the source fences must hold.
                authority = admission.Authority(host, reader, [
                    admission._check(owner, "adm_decision", decision),
                    admission._check(INTAKE_OWNER, "adm_policy", policy),
                    *(admission._check(INTAKE_OWNER, "adm_grant", g) for g in grants
                      if g["policyId"] == decision["policy"]["id"]), *lineage], pending={decision["id"]})
                authority.recheck()
            except (AdmissionError, CollaborationError, ValueError, KeyError, TypeError, IndexError):
                continue  # one unreadable or revoked item never breaks the whole queue
            authorities.append(authority)
            items.append({"id": decision["id"], "revision": decision["revision"],
                          "source": {k: decision["source"][k] for k in ("sourceKind", "sourceId", "revision")},
                          "title": title, "dataClass": decision["dataClass"],
                          "artifactKind": decision["artifact"]["kind"],
                          "pageCount": decision["artifact"].get("pages", preview.pop("count")),
                          "inspection": {k: receipt[k] for k in ("pages", "chars", "pii", "identifiers", "blocking")},
                          **{k: v for k, v in preview.items() if k != "count"},
                          "expiresAt": decision["expiresAt"]})
            if len(items) >= MAX_LISTED:
                break
        cursor = page.get("cursor")
        if not cursor or scanned >= 5000:
            break
    # Response-wide final recheck after ALL reads: each item's own reads only
    # collect its deadlines here; a deadline crossed while a later item is being
    # rechecked must also retire an earlier item's already-collected preview, so
    # every retained item is compared with ONE fresh clock read taken after the
    # very last item's last read, not a clock read taken fresh per item.
    _any_grant(storage, scope["actor"], project_id)
    retained = []
    for item, authority in zip(items, authorities):
        try:
            _, deadlines = authority.collect_deadlines()
        except (AdmissionError, CollaborationError):
            continue
        retained.append((item, deadlines))
    now = storage.clock()  # after every retained item's last read
    return {"reviews": [item for item, deadlines in retained
                        if not any(expiry <= now for expiry, _code in deadlines)]}


def route(host, scope, claims, method, parts, body, query):
    """`GET /intake/reviews`, `POST /intake/reviews/{id}`: JWT + current grant only."""
    try:
        if parts == ["reviews"] and method == "GET":
            return 200, list_pending(host, scope, claims=claims)
        if len(parts) == 2 and parts[0] == "reviews" and method == "POST":
            if not isinstance(body, dict) or set(body) - {"approve", "reason"}:
                raise AdmissionError("invalid-review", 400)
            try:
                records._identifier(parts[1])
            except ValueError:
                raise AdmissionError("decision-not-found", 404) from None
            _any_grant(host.storage, scope["actor"], admission._project(scope))
            decision = decide(host, scope, parts[1], approve=body.get("approve"), reason=body.get("reason"),
                              claims=claims)
            return 200, {"decision": {k: decision[k] for k in ("id", "revision", "status", "expiresAt")}}
        return 404, {"error": "Resource not found", "code": "not-found"}
    except AdmissionError as error:
        return error.status, {"error": "Source admission refused", "code": error.code}
