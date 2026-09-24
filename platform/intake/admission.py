"""`source-admission/1` decisions, current-authority verification and derivative reads.

`pages_for` is the only function that returns text for model use; it returns the
admitted, identifier-normalized derivative only, in bounded batches, and reruns
`verify` for every batch. Source checks always use `Sources.resolve` (current
authority), never the historical `Sources.authorize`.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets

from intake import derivative, inspect, records
from intake.records import INTAKE_OWNER
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.ontology_sources import Sources
from workspace.storage import Conflict

DECISION_DAYS_MS = 30 * 86_400_000
CURSOR_MS = 300_000
MAX_RESPONSE_BYTES = 512 * 1024
MAX_ADMIN_RECORDS = 1000


class AdmissionError(Exception):
    """A closed admission refusal code; never carries source text or deny-list terms."""

    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def deployment():
    return os.environ.get("INTAKE_DEPLOYMENT", "")


def _now(host):
    return host.storage.clock()


def _project(scope):
    project = scope.get("project") if isinstance(scope, dict) else None
    if not project:
        raise AdmissionError("project-required", 400)
    return project["id"]


def _admin_records(storage, kind):
    """Current-shape administration records; malformed rows are never used."""
    rows, cursor = [], None
    while True:
        page = storage.list_page(INTAKE_OWNER, kind, limit=100, cursor=cursor)
        for row in page["items"]:
            try:
                rows.append(records.validate(kind, row))
            except ValueError:
                continue
        cursor = page.get("cursor")
        if not cursor:
            return rows
        if len(rows) >= MAX_ADMIN_RECORDS:
            raise AdmissionError("admission-records-limit", 503)


def _in_scope(scope_value, project_id, *, deployment_bound=True):
    if deployment_bound and (not deployment() or scope_value.get("deployment") != deployment()):
        return False
    ids = scope_value.get("projectIds")
    return ids == "*" or isinstance(ids, list) and project_id in ids


def current_policy(storage, project_id, *, policy_id=None):
    now = storage.clock()
    policies = [p for p in _admin_records(storage, "adm_policy")
                if records.is_current(p, now) and _in_scope(p["scope"], project_id)
                and (policy_id is None or p["id"] == policy_id)]
    if not policies:
        raise AdmissionError("policy-unavailable")
    if len(policies) > 1:
        raise AdmissionError("policy-ambiguous")
    return policies[0]


def _policy_current(storage, decision, project_id):
    policy = storage.get(INTAKE_OWNER, "adm_policy", decision["policy"]["id"])
    try:
        policy = records.validate("adm_policy", policy) if policy else None
    except ValueError:
        policy = None
    if (not policy or not records.is_current(policy, storage.clock())
            or policy["revision"] != decision["policy"]["revision"] or policy["hash"] != decision["policy"]["hash"]
            or not _in_scope(policy["scope"], project_id) or decision["dataClass"] not in policy["dataClasses"]):
        raise AdmissionError("policy-changed")
    return policy


def _reference_matches(reference, source):
    return all(reference[key] == source[key] for key in ("sourceKind", "sourceId", "revision", "sha256"))


def find_provenance(storage, policy, source, project_id):
    now = storage.clock()
    for record in _admin_records(storage, "adm_provenance"):
        if (records.is_current(record, now) and record["policyId"] == policy["id"]
                and record["policyRevision"] == policy["revision"] and _reference_matches(record["reference"], source)
                and _in_scope(record["scope"], project_id)):
            return record
    return None


def find_grant(storage, actor, policy, project_id):
    now = storage.clock()
    for record in _admin_records(storage, "adm_grant"):
        if (records.is_current(record, now) and record["actor"] == actor and record["policyId"] == policy["id"]
                and "review-internal" in record["operations"]
                and _in_scope(record["scope"], project_id, deployment_bound=False)):
            return record
    return None


def _check(owner, kind, record):
    return {"owner": owner, "kind": kind, "id": record["id"], "version": record["version"]}


def _denylist(host):
    loader = getattr(host, "intake_denylist_loader", None) or derivative.load_denylist
    return loader()


def _blocked(reasons):
    return {"status": "blocked", "blocking": sorted(set(reasons))}


def _read_verified(storage, owner, key, expected, code, maximum=50 * 1024 * 1024):
    if not isinstance(key, str) or not storage.owns_key(owner, key):
        raise AdmissionError(code)
    try:
        info = storage.blob_info(key)
        if info["sha256"] != expected or info["size"] > maximum:
            raise ValueError()
        data = storage.get_blob(key, length=maximum)
    except (ValueError, FileNotFoundError):
        raise AdmissionError(code) from None
    if hashlib.sha256(data).hexdigest() != expected:
        raise AdmissionError(code)
    return data


def _expiry(now, *records_):
    return min([now + DECISION_DAYS_MS, *[r["expiresAt"] for r in records_ if r]])


def decide(host, scope, *, reader, source, data_class, policy, artifact, derivation, receipt, receipt_bytes,
           payload, blocking=(), identity=(), extra_checks=(), extra_blobs=None, content_type="application/json",
           lineage=None):
    """Seal and commit one decision after the current policy/provenance checks.

    `payload` is the canonical derivative object whose sha256 is the derivative
    hash. It and the inspection receipt are written as private immutable blobs.
    """
    storage, owner, project_id = host.storage, scope["owner"], _project(scope)
    now = _now(host)
    blocking = sorted(set(blocking) | set(receipt["blocking"]))
    if data_class not in policy["dataClasses"]:
        blocking = sorted(set(blocking) | {"data-class-ineligible"})
    identifier = "adm-" + schema.digest([project_id, source, policy["id"], policy["revision"], data_class,
                                         artifact["kind"], derivation, receipt["hash"], list(identity)])[:40]
    existing = storage.get(owner, "adm_decision", identifier)
    if existing:
        return records.validate("adm_decision", existing)
    keys = {"pages": storage.key_for(owner, "adm_decision", identifier, artifact.pop("suffix", "pages.json")),
            "inspection": storage.key_for(owner, "adm_decision", identifier, "inspection.json")}
    info = storage.put_blob_once(keys["pages"], payload, content_type)
    receipt_info = storage.put_blob_once(keys["inspection"], receipt_bytes, "application/json")
    for suffix, data in (extra_blobs or {}).items():
        data, kind = data if isinstance(data, tuple) else (data, "application/json")
        storage.put_blob_once(storage.key_for(owner, "adm_decision", identifier, suffix), data, kind)
    if "vision" in artifact:
        vision = dict(artifact["vision"])
        vision["key"] = storage.key_for(owner, "adm_decision", identifier, vision.pop("suffix"))
        artifact = {**artifact, "vision": vision}
    if info["sha256"] != derivation["derivativeHash"]:
        raise AdmissionError("artifact-changed")
    record = {"id": identifier, "revision": 1, "projectId": project_id, "source": source,
              "artifact": {**artifact, "key": keys["pages"], "sha256": info["sha256"]},
              "derivation": derivation,
              "policy": {"id": policy["id"], "revision": policy["revision"], "hash": policy["hash"]},
              "inspection": {"receiptKey": keys["inspection"], "hash": receipt_info["sha256"]},
              "dataClass": data_class}
    if lineage is not None:
        record["lineage"] = lineage
    checks = [_check(INTAKE_OWNER, "adm_policy", policy), *extra_checks]
    if blocking:
        record.update(status="blocked", blocking=blocking, expiresAt=_expiry(now, policy))
    elif data_class in ("synthetic", "public"):
        provenance = (find_provenance(storage, policy, source, project_id)
                      if policy["trustedProvenance"].get(data_class) else None)
        if provenance is None:
            record.update(status="blocked", blocking=["provenance-required"], expiresAt=_expiry(now, policy))
        else:
            record.update(status="admitted", provenance={"id": provenance["id"], "revision": provenance["revision"]},
                          expiresAt=_expiry(now, policy, provenance))
            checks.append(_check(INTAKE_OWNER, "adm_provenance", provenance))
    else:
        record.update(status="pending-review", expiresAt=_expiry(now, policy))
    sealed = records.seal("adm_decision", record)
    project = scope["project"]
    fences = [*reader.recheck(), *checks, {"owner": owner, "kind": "project", "id": project_id,
                                            "version": project["version"]}]
    unique = {(c["owner"], c["kind"], c["id"]): c for c in fences}
    try:
        return storage.put_many([{"owner": owner, "kind": "adm_decision", "item": sealed}],
                                checks=list(unique.values()), retry_conflicts=False)[0]
    except Conflict:
        existing = storage.get(owner, "adm_decision", identifier)
        if existing and records.digest_record(existing) == sealed["hash"]:
            return existing
        raise AdmissionError("conflict") from None


def request(host, scope, source_ref, *, data_class, claims=None):
    """Inspect, normalize and record an admission for a verified actor's source."""
    _project(scope)
    if data_class not in records.DATA_CLASSES:
        return _blocked(["data-class-ineligible"])
    try:
        policy = current_policy(host.storage, _project(scope))
    except AdmissionError as error:
        return _blocked([error.code])
    if data_class not in policy["dataClasses"]:
        return _blocked(["data-class-ineligible"])
    try:
        denylist = derivative.canonical_entries(_denylist(host))
    except derivative.DenylistUnavailable:
        return _blocked(["denylist-unavailable"])
    resolved = inspect.resolve(host, scope, source_ref, claims=claims)
    receipt = inspect.inspect(resolved["pages"], denylist=denylist)
    derived = derivative.normalize(resolved["pages"], denylist)
    rest = derivative.residual(derived["pages"], denylist)
    blocking = (["residual-identifiers"] if rest["identifiers"] else []) + (
        ["redaction-required"] if rest["pii"] else [])
    ref = resolved["ref"]
    source = {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision")}
    return decide(host, scope, reader=resolved["sources"], source=source, data_class=data_class, policy=policy,
                  artifact={"kind": "document-pages", "pages": len(derived["pages"])},
                  derivation={"profile": derivative.PROFILE, "originalHash": resolved["originalHash"],
                              "derivativeHash": derived["derivativeHash"]},
                  receipt=receipt, receipt_bytes=schema.canonical(receipt),
                  payload=schema.canonical(derived["pages"]), blocking=blocking)


def _source_current(host, scope, decision, reader):
    source = decision["source"]
    try:
        if source["sourceKind"] == "prompt-text":
            from intake import prompts
            prompts.resolve(host, scope, decision, reader)
        else:
            reader.resolve(dict(source))
    except CollaborationError:
        raise AdmissionError("source-changed") from None
    except AdmissionError:
        raise


def _load(host, scope, decision_id):
    project_id = _project(scope)
    try:
        decision = host.storage.get(scope["owner"], "adm_decision", decision_id)
        decision = records.validate("adm_decision", decision) if decision else None
    except ValueError:
        decision = None
    if not decision or decision["projectId"] != project_id:
        raise AdmissionError("decision-not-found", 404)
    return decision


def verified(host, scope, decision_id, *, claims=None, sources=None, observe=None):
    """Recheck an admitted decision; returns (decision, derivative bytes)."""
    storage, owner, project_id = host.storage, scope["owner"], _project(scope)
    decision = _load(host, scope, decision_id)
    if not records.is_current(decision, storage.clock()) or decision["status"] != "admitted":
        raise AdmissionError("decision-not-current")
    policy = _policy_current(storage, decision, project_id)
    upstream = [("adm_policy", policy)]
    if "provenance" in decision:
        provenance = storage.get(INTAKE_OWNER, "adm_provenance", decision["provenance"]["id"])
        if (not provenance or not records.is_current(provenance, storage.clock())
                or provenance["revision"] != decision["provenance"]["revision"]
                or not _reference_matches(provenance["reference"], decision["source"])
                or provenance["policyId"] != policy["id"] or provenance["policyRevision"] != policy["revision"]
                or not _in_scope(provenance["scope"], project_id)):
            raise AdmissionError("grant-revoked")
        upstream.append(("adm_provenance", provenance))
    if "review" in decision:
        grant = storage.get(INTAKE_OWNER, "adm_grant", decision["review"]["grantId"])
        if (not grant or not records.is_current(grant, storage.clock())
                or grant["revision"] != decision["review"]["grantRevision"]
                or grant["actor"] != decision["review"]["actor"] or grant["policyId"] != policy["id"]
                or not _in_scope(grant["scope"], project_id, deployment_bound=False)):
            raise AdmissionError("grant-revoked")
        upstream.append(("adm_grant", grant))
    reader = sources or Sources(inspect.context(host, scope, claims))
    _source_current(host, scope, decision, reader)
    data = _read_verified(storage, owner, decision["artifact"]["key"], decision["derivation"]["derivativeHash"],
                          "artifact-changed")
    if decision["artifact"]["sha256"] != decision["derivation"]["derivativeHash"]:
        raise AdmissionError("artifact-changed")
    _read_verified(storage, owner, decision["inspection"]["receiptKey"], decision["inspection"]["hash"],
                   "inspection-changed", maximum=1024 * 1024)
    if "vision" in decision["artifact"]:
        vision = decision["artifact"]["vision"]
        _read_verified(storage, owner, vision["key"], vision["sha256"], "artifact-changed")
        _read_verified(storage, owner, storage.key_for(owner, "adm_decision", decision["id"], "ocr.json"),
                       vision["ocrSha256"], "artifact-changed", maximum=1024 * 1024)
    if observe is not None:
        observe.append(_check(owner, "adm_decision", decision))
        observe.extend(_check(INTAKE_OWNER, kind, record) for kind, record in upstream)
        observe.extend(reader.recheck())
    return decision, data


def verify(host, scope, decision_id, *, claims=None, sources=None, observe=None):
    """Recheck policy, provenance/grant, current source and artifact/inspection hashes."""
    return verified(host, scope, decision_id, claims=claims, sources=sources, observe=observe)[0]


def admission_ref(decision):
    return {"decisionId": decision["id"], "revision": decision["revision"],
            "artifactHash": decision["derivation"]["derivativeHash"]}


def _size(value):
    # ensure_ascii=True is the larger of the two JSON encodings for any text.
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def pages_for(host, scope, decision_id, *, cursor=None, max_bytes=400_000, claims=None):
    """Bounded batches of admitted derivative pages; each batch reruns `verify`."""
    if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_RESPONSE_BYTES:
        raise AdmissionError("invalid-batch-size", 400)
    storage, owner = host.storage, scope["owner"]
    decision, data = verified(host, scope, decision_id, claims=claims)
    binding = schema.digest([decision["id"], decision["revision"], decision["derivation"]["derivativeHash"],
                             scope["actor"]])
    start = 0
    if cursor is not None:
        try:
            schema._identifier(cursor)
            saved = storage.get(owner, "ontology_cursor", cursor)
        except ValueError:
            saved = None
        if (not saved or saved.get("purpose") != "intake-pages" or saved.get("binding") != binding
                or saved.get("expiresAt", 0) <= storage.clock() or type(saved.get("nextPage")) is not int):
            raise AdmissionError("admission-cursor-stale")
        start = saved["nextPage"]
    if decision["artifact"]["kind"] not in ("document-pages", "prompt-text"):
        raise AdmissionError("artifact-kind-unsupported", 422)
    pages = json.loads(data)
    source = dict(decision["source"])
    base = {"pages": [], "cursor": "x" * 56, "total": len(pages),
            "derivativeHash": decision["derivation"]["derivativeHash"]}
    batch, index = [], start
    while index < len(pages):
        entry = {"admissionId": decision["id"], "derivativeHash": decision["derivation"]["derivativeHash"],
                 "sourceRef": source, "page": pages[index]["page"], "text": pages[index]["text"]}
        if _size({**base, "pages": [*batch, entry]}) > max_bytes:
            if not batch:
                raise AdmissionError("admission-page-too-large", 422)
            break
        batch.append(entry)
        index += 1
    next_cursor = None
    if index < len(pages):
        next_cursor = "intake-" + secrets.token_hex(24)
        storage.put(owner, "ontology_cursor", {"id": next_cursor, "projectId": decision["projectId"],
                                               "purpose": "intake-pages", "binding": binding, "nextPage": index,
                                               "expiresAt": storage.clock() + CURSOR_MS})
    return {"pages": batch, "cursor": next_cursor, "total": len(pages),
            "derivativeHash": decision["derivation"]["derivativeHash"]}


def assemble(batches):
    """Consumer-side check: the concatenated batches must hash to `derivativeHash`."""
    if not batches:
        raise AdmissionError("artifact-changed")
    expected = batches[0]["derivativeHash"]
    pages = [{"page": entry["page"], "text": entry["text"]} for batch in batches for entry in batch["pages"]]
    if (any(batch["derivativeHash"] != expected for batch in batches) or len(pages) != batches[0]["total"]
            or derivative.derivative_hash(pages) != expected):
        raise AdmissionError("artifact-changed")
    return pages
