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
    try:
        saved = storage.put_many([{"owner": owner, "kind": "adm_decision", "item": sealed,
                                   "expected_version": decision["version"]}],
                                 checks=list(unique.values()), retry_conflicts=False)[0]
    except Conflict:
        raise AdmissionError("conflict") from None
    if approve and saved["artifact"]["kind"] == "diagram-transcription":
        publish(host, scope, saved)
    return saved


def publish(host, scope, decision):
    """Publish a reviewer-validated transcription as an in-review library revision.

    Idempotent; library approval by a planner/owner remains a separate step.
    """
    from documents.library import publish_transcription
    return publish_transcription(host, scope, decision)


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
    items, cursor, scanned = [], None, 0
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
            try:
                admission._policy_current(storage, decision, project_id)
                _source_access(host, scope, decision, Sources(inspect.context(host, scope, claims)))
                data = admission._read_verified(storage, owner, decision["artifact"]["key"],
                                                decision["derivation"]["derivativeHash"], "artifact-changed")
                receipt = json.loads(admission._read_verified(
                    storage, owner, decision["inspection"]["receiptKey"], decision["inspection"]["hash"],
                    "inspection-changed", maximum=1024 * 1024))
            except (AdmissionError, CollaborationError):
                continue
            try:
                preview = _preview(host, scope, decision, data)
            except (AdmissionError, ValueError, KeyError, TypeError, IndexError):
                continue  # one unreadable derivative never breaks the whole queue
            items.append({"id": decision["id"], "revision": decision["revision"],
                          "source": {k: decision["source"][k] for k in ("sourceKind", "sourceId", "revision")},
                          "title": _title(host, scope, decision), "dataClass": decision["dataClass"],
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
    return {"reviews": items}


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
