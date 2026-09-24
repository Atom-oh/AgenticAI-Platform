"""IAM-only administration of `source-admission/1` policy, provenance and grants.

This Lambda has no API Gateway route, Function URL or resource policy for
`apigateway.amazonaws.com` (asserted by `workspace/check_infra.py`). Authority is
the IAM `lambda:InvokeFunction` permission itself; `operator` is an audit label
only, and in-app groups (`platform-operators`/`admin`) confer nothing.

Event: {"op", "record" | "id", "expectedRevision", "operator"}.
"""
from __future__ import annotations

import copy
import uuid

from intake import records
from intake.records import INTAKE_OWNER

_TRANSPORT_FIELDS = ("requestContext", "headers", "routeKey", "rawPath", "multiValueHeaders",
                     "httpMethod", "path", "pathParameters", "queryStringParameters", "body")
_EVENT_FIELDS = frozenset({"op", "record", "id", "expectedRevision", "operator"})
_CREATE = {"put_policy": "adm_policy", "register_provenance": "adm_provenance",
           "grant_reviewer": "adm_grant"}
# op -> (kind, required current status, new status)
_TRANSITIONS = {
    "activate_policy": ("adm_policy", ("draft",), "active"),
    "retire_policy": ("adm_policy", ("draft", "active"), "retired"),
    "revoke_provenance": ("adm_provenance", ("active",), "revoked"),
    "revoke_grant": ("adm_grant", ("active",), "revoked"),
}
_INITIAL_STATUS = {"adm_policy": "draft", "adm_provenance": "active", "adm_grant": "active"}


def storage_factory():
    from workspace.storage import Storage
    return Storage()


class _Refusal(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _transport_ok(event, context):
    if not isinstance(event, dict) or any(field in event for field in _TRANSPORT_FIELDS):
        return False
    arn = getattr(context, "invoked_function_arn", None) if context is not None else None
    return isinstance(arn, str) and arn.startswith("arn:aws:lambda:") and ":function:" in arn


def _audit(storage, op, kind, record, operator):
    now = storage.clock()
    return records.seal("adm_audit", {
        "id": f"audit-{now:016d}-{uuid.uuid4().hex[:16]}", "op": op, "kind": kind,
        "recordId": record["id"], "revision": record["revision"], "operator": operator, "at": now})


def _stored(record):
    return {key: value for key, value in record.items() if key not in records.STORAGE_FIELDS}


def _commit(storage, op, kind, record, expected, operator):
    from workspace.storage import Conflict
    audit = _audit(storage, op, kind, record, operator)
    try:
        written = storage.put_many([
            {"owner": INTAKE_OWNER, "kind": kind, "item": record, "expected_version": expected},
            {"owner": INTAKE_OWNER, "kind": "adm_audit", "item": audit},
        ])
    except Conflict:
        raise _Refusal("conflict") from None
    return written[0]


def _current_policy(storage, identifier, revision):
    policy = storage.get(INTAKE_OWNER, "adm_policy", identifier)
    if (not policy or policy.get("revision") != revision
            or not records.is_current(records.validate("adm_policy", policy), storage.clock())):
        raise _Refusal("policy-not-active")
    return policy


def _create(storage, op, event, operator):
    kind = _CREATE[op]
    body = event.get("record")
    if not isinstance(body, dict) or {"revision", "hash", *records.STORAGE_FIELDS} & body.keys():
        raise _Refusal("invalid-record")
    if body.get("status", _INITIAL_STATUS[kind]) != _INITIAL_STATUS[kind]:
        raise _Refusal("invalid-record")
    expected = event.get("expectedRevision")
    if expected is not None and (type(expected) is not int or expected < 1):
        raise _Refusal("invalid-request")
    previous = storage.get(INTAKE_OWNER, kind, body.get("id")) if isinstance(body.get("id"), str) else None
    if (previous is None) != (expected is None) or previous and previous.get("revision") != expected:
        raise _Refusal("conflict")
    if previous and kind != "adm_policy":
        # Registrations and grants are revoked and reissued, never edited in place.
        raise _Refusal("conflict")
    record = {**copy.deepcopy(body), "status": _INITIAL_STATUS[kind],
              "revision": 1 if previous is None else previous["revision"] + 1}
    try:
        sealed = records.seal(kind, record)
    except (ValueError, TypeError):
        raise _Refusal("invalid-record") from None
    if sealed["expiresAt"] <= storage.clock():
        raise _Refusal("invalid-record")
    if kind == "adm_provenance":
        _current_policy(storage, sealed["policyId"], sealed["policyRevision"])
    if kind == "adm_grant":
        policy = storage.get(INTAKE_OWNER, "adm_policy", sealed["policyId"])
        if not policy or not records.is_current(records.validate("adm_policy", policy), storage.clock()):
            raise _Refusal("policy-not-active")
    return _commit(storage, op, kind, sealed, previous["version"] if previous else None, operator)


def _transition(storage, op, event, operator):
    kind, allowed, status = _TRANSITIONS[op]
    identifier, expected = event.get("id"), event.get("expectedRevision")
    if not isinstance(identifier, str) or type(expected) is not int or expected < 1 or "record" in event:
        raise _Refusal("invalid-request")
    try:
        previous = storage.get(INTAKE_OWNER, kind, identifier)
    except ValueError:
        raise _Refusal("invalid-request") from None
    if not previous:
        raise _Refusal("not-found")
    previous = records.validate(kind, previous)
    if previous["revision"] != expected:
        raise _Refusal("conflict")
    if previous["status"] not in allowed:
        raise _Refusal("invalid-transition")
    if status == "active" and previous["expiresAt"] <= storage.clock():
        raise _Refusal("invalid-transition")
    sealed = records.seal(kind, {**_stored(previous), "status": status, "revision": previous["revision"] + 1})
    return _commit(storage, op, kind, sealed, previous["version"], operator)


def handler(event, context):
    if not _transport_ok(event, context):
        return {"error": "forbidden-transport"}
    op, operator = event.get("op"), event.get("operator")
    if (set(event) - _EVENT_FIELDS or op not in (*_CREATE, *_TRANSITIONS)
            or not isinstance(operator, str) or not records._LABEL.fullmatch(operator)):
        return {"error": "invalid-request"}
    storage = storage_factory()
    try:
        written = (_create if op in _CREATE else _transition)(storage, op, event, operator)
    except _Refusal as refusal:
        return {"error": refusal.code}
    # Metadata only: never echo deny-list material or credentials (none are stored here).
    return {"ok": True, "record": written}
