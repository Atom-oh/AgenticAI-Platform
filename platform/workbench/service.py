"""Shared validation, exact evidence bindings and canonical project CAS fences."""
from __future__ import annotations

import base64
import hashlib
import json
import re

from workspace.collaboration import (
    Collaboration, CollaborationError, _hash, _id, _json, _request, _text, _version,
)
from workspace.storage import Conflict

ROLES = frozenset({"owner", "planner", "designer", "developer"})
TOOL_NAMES = ("knowledge.search", "knowledge.read", "dependencies.read",
              "impact.read", "skills.list", "skills.read")
WRITE_ROLES = ROLES
MAX_SOURCES = 20


def fail(status, code, message):
    raise CollaborationError(status, code, message)


UPSTREAM_ADMISSION_KINDS = ("adm_policy", "adm_provenance", "adm_grant", "adm_decision")


def check_source_deadlines(storage, checks, claims=None, deadline=None):
    """Recheck the aggregate deadline after reads and transaction preparation.

    `deadline` is the verified scope's `authorizationExpiresAt` (ms), which binds
    even when the claims carry no `exp` (a server-side context for deferred work).
    """
    deadlines = []
    now = storage.clock()
    if deadline is not None and (type(deadline) is not int or deadline <= now):
        fail(401, "authorization-expired", "인증이 만료되었습니다.")
    for check in checks:
        if check["kind"] in UPSTREAM_ADMISSION_KINDS:
            # Upstream intake authority (image/transcription admission, policy,
            # provenance, grant) expires without a version change: every commit
            # attempt rechecks its schema, exact version, status and expiry.
            from intake import records
            row = storage.get(check["owner"], check["kind"], check["id"])
            try:
                row = records.validate(check["kind"], row) if row else None
            except ValueError:
                row = None
            if not row or row["version"] != check["version"] or not records.is_current(row, now):
                fail(409, "source-upstream-revoked", "원본 반입 승인이 만료되었거나 회수되었습니다.")
            continue
        if check["kind"] != "wb_source":
            continue
        source = storage.get(check["owner"], check["kind"], check["id"])
        if (not source or source["version"] != check["version"]
                or type(source.get("accessExpiresAt")) is not int):
            fail(409, "stale-evidence", "검증한 원본 권한이 변경되었습니다.")
        deadlines.append(source["accessExpiresAt"])
    expiry = (claims or {}).get("exp")
    if expiry is None and not deadlines:
        return
    if expiry is not None and int(expiry) * 1000 <= now:
        fail(401, "authorization-expired", "인증이 만료되었습니다.")
    if deadlines and min(deadlines) <= now:
        fail(409, "stale-evidence", "검증한 원본의 유효 기간이 만료되었습니다.")


def fields(body, allowed):
    if not isinstance(body, dict) or set(body) - set(allowed):
        fail(400, "invalid-input", "허용되지 않은 입력 필드입니다.")


def text(value, name="text", maximum=2000, empty=False):
    return _text(value, name, maximum, empty)


def tools(value):
    if (not isinstance(value, list) or len(value) > len(TOOL_NAMES)
            or any(not isinstance(x, str) or x not in TOOL_NAMES for x in value)
            or len(set(value)) != len(value)):
        fail(400, "invalid-tools", "지원되는 내부 읽기 도구만 선언할 수 있습니다.")
    return value


def groups(claims):
    value = claims.get("cognito:groups", [])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            # API Gateway also renders Cognito's bracketed group list without quotes.
            value = value.strip("[]").split(",")
        if isinstance(value, str):
            value = [value]
    return {x.strip() for x in value if isinstance(x, str)} if isinstance(value, list) else set()


def public(value):
    """Private bytes are served through authenticated reads, never S3 key disclosure."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.endswith("Key")
                and k not in {"requestHash", "owner", "access", "pk", "sk", "proposal"}}
    if isinstance(value, list):
        return [public(x) for x in value]
    return value


class Service:
    def __init__(self, host, scope, claims):
        self.host, self.storage, self.claims = host, host.storage, claims
        self.collaboration = getattr(host, "collaboration", None) or Collaboration(self.storage)
        if not isinstance(claims, dict) or claims.get("sub") != scope.get("actor"):
            fail(401, "unauthorized", "검증된 사용자 정보가 필요합니다.")
        if not scope.get("project"):
            fail(400, "project-required", "프로젝트를 먼저 선택하세요.")
        self.scope = scope
        self.required_roles = set(ROLES)
        self.operator_required = False
        self.fresh()

    @property
    def owner(self):
        return self.scope["owner"]

    @property
    def actor(self):
        return self.scope["actor"]

    @property
    def project_id(self):
        return self.scope["project"]["id"]

    @property
    def operator(self):
        return bool(groups(self.claims) & {"admin", "platform-operators"})

    def fresh(self, roles=None, operator=False):
        if roles is not None:
            self.required_roles &= set(roles)
        self.operator_required = self.operator_required or operator
        expiry = self.claims.get("exp")
        if expiry is not None:
            try:
                expired = int(expiry) * 1000 <= self.storage.clock()
            except (ValueError, TypeError, OverflowError):
                expired = True
            if expired:
                fail(401, "authorization-expired", "인증이 만료되었습니다.")
            self.scope = {**self.scope, "authorizationExpiresAt": int(expiry) * 1000}
        self.scope = self.collaboration.require(self.scope, "read")
        if self.scope["role"] not in self.required_roles:
            fail(403, "forbidden", "현재 역할로 수행할 수 없는 작업입니다.")
        if self.operator_required and not self.operator:
            fail(403, "operator-required", "검증된 플랫폼 운영자 권한이 필요합니다.")
        return self.scope

    def authorization(self):
        self.fresh()
        try:
            expiry = int(self.claims["exp"]) * 1000
        except (KeyError, ValueError, TypeError, OverflowError):
            fail(401, "authorization-required", "비동기 작업에는 만료 시간이 있는 인증이 필요합니다.")
        return {"actor": self.actor, "projectId": self.project_id,
                "authorizationExpiresAt": expiry, "actorRole": self.scope["role"],
                "operator": self.operator}

    def get(self, kind, identifier):
        record = self.storage.get(self.owner, kind, _id(identifier))
        if not record or record.get("projectId") != self.project_id:
            fail(404, "not-found", "프로젝트 자료가 없습니다.")
        return record

    def write(self, kind, item, version=None):
        return self.collaboration._write(self.owner, kind, item, version)

    def commit(self, writes, checks=()):
        """The supplied scope was checked before reads; never refresh away its fence."""
        project = self.scope["project"]
        if self.claims.get("exp") is not None and int(self.claims["exp"]) * 1000 <= self.storage.clock():
            fail(401, "authorization-expired", "인증이 만료되었습니다.")
        bound = self.scope.get("authorizationExpiresAt")
        if bound is not None and (type(bound) is not int or bound <= self.storage.clock()):
            fail(401, "authorization-expired", "인증이 만료되었습니다.")
        if len(writes) + 1 > 100:
            fail(422, "atomic-scope-limit", "원자적 저장 한도를 초과했습니다. 변경 범위와 근거를 나누세요.")
        unique = {}
        for check in checks:
            key = check["owner"], check["kind"], check["id"]
            if key in unique and unique[key] != check:
                fail(409, "source-changed", "같은 원본의 서로 다른 버전이 관찰되었습니다.")
            unique[key] = check
        for write in writes:
            key = write["owner"], write["kind"], write["item"]["id"]
            if key in unique:
                if unique[key]["version"] != write.get("expected_version"):
                    fail(409, "source-changed", "검증한 원본과 저장 기준 버전이 다릅니다.")
                del unique[key]
        project_key = self.owner, "project", project["id"]
        if project_key in unique:
            if unique[project_key]["version"] != project["version"]:
                fail(409, "source-changed", "검증한 프로젝트 권한 버전이 다릅니다.")
            del unique[project_key]
        if len(writes) + len(unique) + 1 > 100:
            fail(422, "atomic-scope-limit", "원자적 저장 한도를 초과했습니다. 변경 범위와 근거를 나누세요.")
        fence = self.write("project", project, project["version"])
        try:
            # Timed authority/source checks belong to the caller. A contention
            # retry must return there for reauthorization before another send.
            observed = list(unique.values())
            return self.storage.put_many([*writes, fence], checks=observed, retry_conflicts=False,
                before_attempt=lambda: check_source_deadlines(self.storage, observed, self.claims, bound))[:-1]
        except Conflict as error:
            raise CollaborationError(409, "conflict", "프로젝트 또는 근거가 변경되었습니다.") from error

    def check(self, kind, record):
        return {"owner": self.owner, "kind": kind, "id": record["id"], "version": record["version"]}

    def identity(self, kind, request_id):
        return kind.replace("wb_", "") + "-" + _hash([self.actor, _request(request_id)])[:40]

    def existing(self, kind, identifier, request):
        previous = self.storage.get(self.owner, kind, identifier)
        if previous:
            if previous.get("requestHash") != _hash(request) or previous.get("createdBy") != self.actor:
                fail(409, "request-changed", "같은 요청 ID에 다른 내용이 지정되었습니다.")
            return previous
        return None

    def create(self, kind, body, values):
        self.fresh(WRITE_ROLES)
        identifier = self.identity(kind, body.get("requestId"))
        previous = self.existing(kind, identifier, body)
        if previous:
            return previous
        record = {**values, "id": identifier, "projectId": self.project_id,
                  "createdBy": self.actor, "requestHash": _hash(body)}
        return self.commit([self.write(kind, record)])[0]

    def page(self, kind, query=None):
        self.fresh()
        query = query or {}
        page = self.storage.list_page(self.owner, kind, limit=limit(query), cursor=query.get("cursor"))
        page["items"] = [x for x in page["items"] if x.get("projectId") == self.project_id]
        return page

    def bounded_list(self, kind, maximum=100):
        items, cursor = [], None
        while len(items) <= maximum:
            page = self.storage.list_page(self.owner, kind, min(100, maximum + 1 - len(items)), cursor)
            items.extend(x for x in page["items"] if x.get("projectId") == self.project_id)
            cursor = page.get("cursor")
            if not cursor or len(items) > maximum:
                break
        return items[:maximum], bool(cursor or len(items) > maximum)

    def put_json(self, kind, identifier, suffix, value):
        data = _json(value)
        key = self.storage.key_for(self.owner, kind, identifier, suffix)
        info = self.storage.put_blob_once(key, data, "application/json")
        return key, info["sha256"]

    def read_json(self, key, digest, maximum=4_000_000):
        if not self.storage.owns_key(self.owner, key):
            fail(403, "forbidden", "이 프로젝트의 자료가 아닙니다.")
        info = self.storage.blob_info(key)
        if info["size"] > maximum:
            fail(409, "artifact-invalid", "자료 크기가 허용 범위를 벗어났습니다.")
        data = self.storage.get_blob(key)
        if hashlib.sha256(data).hexdigest() != digest:
            fail(409, "artifact-invalid", "자료 해시가 일치하지 않습니다.")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            fail(409, "artifact-invalid", "자료 형식이 올바르지 않습니다.")

    def queue_job(self, identifier, data, fingerprint):
        self.fresh()
        job = self.host._new_job(self.owner, identifier, "workbench", data, fingerprint)
        try:
            self.host._invoke(self.owner, job)
        except Exception:
            from workbench.worker import _mark_failed
            _mark_failed(self.host, self.owner, job, "dispatch-failed")
            raise
        return job

    def gate(self, phase, payload, purpose):
        gate = getattr(self.host, "workbench_gate", None)
        if not callable(gate):
            fail(503, "model-not-configured", "경계 검사기가 구성되지 않았습니다.")
        result = gate(phase=phase, payload=payload, purpose=purpose,
                      actor=self.actor, project_id=self.project_id)
        receipt = result.get("receipt", {}) if isinstance(result, dict) else {}
        if (not isinstance(result, dict) or result.get("allowed") is not True
                or receipt.get("inspected") is not True or type(receipt.get("bytes")) is not int
                or receipt["bytes"] < 0):
            fail(422, "boundary-blocked", "모델 입출력 경계 검사가 통과되지 않았습니다.")
        # Receipt contents from adapters must never disclose prompts or originals.
        return {"inspected": True, "bytes": receipt["bytes"], "phase": phase,
                "payloadHash": _hash(payload)}


def limit(query):
    value = query.get("limit", 50)
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        fail(400, "invalid-input", "페이지 크기가 올바르지 않습니다.")
    if isinstance(value, bool) or not 1 <= parsed <= 100:
        fail(400, "invalid-input", "페이지 크기는 1~100이어야 합니다.")
    return parsed


def paginate(items, query, binding):
    size, offset = limit(query), 0
    fingerprint = _hash(binding)
    if query.get("cursor"):
        try:
            cursor = query["cursor"]
            if not isinstance(cursor, str) or len(cursor) > 1000:
                raise ValueError()
            decoded = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
            if decoded["binding"] != fingerprint or type(decoded["offset"]) is not int:
                raise ValueError()
            offset = decoded["offset"]
            if not 0 <= offset <= len(items):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            fail(400, "invalid-cursor", "조회 조건 또는 자료 버전이 변경되었습니다.")
    result = {"items": items[offset:offset + size]}
    if offset + size < len(items):
        result["cursor"] = base64.urlsafe_b64encode(_json({"binding": fingerprint, "offset": offset + size})).decode()
    return result
