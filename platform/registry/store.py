"""Registry 저장소 — DynamoDB(REGISTRY_TABLE) 위의 얇은 계층. 테이블 객체 주입 가능.

키 설계 (CONTRACTS §3 가정: pk/sk 문자열, GSI byStatus = status(S) / updatedAt(N)):
  레코드  pk="rec#<name>"           sk="<recordVersion>"          + status, updatedAt (GSI 키)
  감사    pk="audit#<name>#<version>" sk="<ts13>#<uuid8>"         (status/updatedAt 속성 없음 → GSI 미포함)
name+recordVersion 유일성은 조건부 put(attribute_not_exists(pk)) 으로 강제한다.
상태 전이는 낙관적 조건(#st = :from)으로 갱신해 동시 전이를 막고, 전이마다 감사 이벤트를 쓴다.
boto3 리소스는 함수 안에서 지연 생성한다 (테스트는 페이크 주입).
"""
from __future__ import annotations

import itertools
import os
import uuid
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from registry.model import (STATUSES, ConflictError, NotFoundError, TransitionError, audit_event,
                            check_transition, now_ms)

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
EMBEDDING_ATTR = "embedding"  # JSON 문자열(소수 5자리 반올림) — 전송 시 제거된다

_audit_seq = itertools.count(1)  # 같은 ms 안의 감사 이벤트 순서를 보장하는 프로세스 내 단조 카운터


def _revision_condition(record, names, values):
    names["#revision"] = "stateRevision"
    values[":revision"] = int(record.get("stateRevision", 0)) + 1
    if "stateRevision" in record:
        values[":previousRevision"] = record["stateRevision"]
        return "#revision = :previousRevision"
    return "attribute_not_exists(#revision)"


def rec_pk(name: str) -> str:
    return f"rec#{name}"


def audit_pk(name: str, version: str) -> str:
    return f"audit#{name}#{version}"


def _plain(obj: Any) -> Any:
    """DynamoDB 리소스가 돌려주는 Decimal 을 int/float 로 되돌린다 (재귀)."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _to_ddb(obj: Any) -> Any:
    """DynamoDB 쓰기용 정규화 — float → Decimal (DynamoDB 리소스는 float 를 거부한다). NaN/Inf 는 문자열로.
    payload 에 금리·우대율 같은 실수가 있어도 저장되게 한다 (재귀)."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return str(obj)
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [_to_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    return obj


def _is_conditional_failure(e: Exception) -> bool:
    resp = getattr(e, "response", None) or {}
    code = (resp.get("Error") or {}).get("Code")
    return code == "ConditionalCheckFailedException" or (
        code == "TransactionCanceledException" and any(
            reason.get("Code") == "ConditionalCheckFailed" for reason in resp.get("CancellationReasons", [])))


def _record_from_item(item: dict) -> dict:
    out = {k: v for k, v in _plain(item).items() if k not in ("pk", "sk")}
    return out


class RegistryStore:
    def __init__(self, table: Any = None, table_name: Optional[str] = None) -> None:
        self._table = table
        self._transaction_client = None
        self._table_name = table_name if table_name is not None else os.environ.get("REGISTRY_TABLE", "")

    @property
    def backend(self) -> str:
        """'dynamodb' | 'memory' — UI 에 저장소 실체를 표기하기 위해 노출."""
        if self._table is not None and type(self._table).__name__ == "InMemoryTable":
            return "memory"
        return "dynamodb" if (self._table is not None or self._table_name) else "memory"

    def table(self) -> Any:
        if self._table is None:
            if self._table_name:
                import boto3  # 지연 import — 테스트 경로에서 AWS 를 건드리지 않는다
                self._table = boto3.resource("dynamodb", region_name=REGION).Table(self._table_name)
            else:
                from registry.fake_table import new_registry_table
                self._table = new_registry_table()
        return self._table

    @contextmanager
    def administration_lease(self, name: str, version: str):
        """Serialize a mirror decision beyond Lambda's maximum execution time."""
        table = self.table()
        key = {"pk": f"admin-sync#{name}", "sk": version}
        owner, now = uuid.uuid4().hex, int(time.time())
        previous = table.get_item(Key=key, ConsistentRead=True).get("Item")
        try:
            if previous and previous["expires"] <= now:
                table.delete_item(Key=key, ConditionExpression="#owner = :owner AND #expires = :expires",
                    ExpressionAttributeNames={"#owner": "owner", "#expires": "expires"},
                    ExpressionAttributeValues={":owner": previous["owner"], ":expires": previous["expires"]})
            table.put_item(Item={**key, "owner": owner, "expires": now + 960},
                           ConditionExpression="attribute_not_exists(pk)")
        except Exception as error:
            if _is_conditional_failure(error):
                raise ConflictError("Registry administration is already in progress") from None
            raise
        try:
            yield
        finally:
            table.delete_item(Key=key, ConditionExpression="#owner = :owner",
                              ExpressionAttributeNames={"#owner": "owner"},
                              ExpressionAttributeValues={":owner": owner})

    # ---------- 레코드 ----------
    def _write_with_audit(self, operation: str, mutation: dict, name: str, version: str, event: dict) -> None:
        """Commit the conditional record mutation and its audit event atomically."""
        table = self.table()
        audit = {"Put": {"Item": self._audit_item(name, version, event),
                         "ConditionExpression": "attribute_not_exists(pk)"}}
        writes = [{operation: mutation}, audit]
        if self.backend == "memory":
            table.transact_write_items(TransactItems=writes)
            return
        import boto3
        from boto3.dynamodb.types import TypeSerializer
        if self._transaction_client is None:
            self._transaction_client = boto3.client("dynamodb", region_name=REGION)
        serializer = TypeSerializer()
        encoded = []
        for write in writes:
            kind, parameters = next(iter(write.items()))
            parameters = {**parameters, "TableName": table.name}
            for field in ("Item", "Key", "ExpressionAttributeValues"):
                if field in parameters:
                    parameters[field] = {key: serializer.serialize(_to_ddb(value))
                                         for key, value in parameters[field].items()}
            encoded.append({kind: parameters})
        self._transaction_client.transact_write_items(TransactItems=encoded, ClientRequestToken=str(uuid.uuid4()))

    def put_new(self, rec: dict, actor: str, reason: str = "", transition: str = "create") -> dict:
        """신규 레코드 저장 (name+recordVersion 유일). 생성 감사 이벤트(from=None) 기록."""
        ts = now_ms()
        item = dict(rec)
        item.update({"pk": rec_pk(rec["name"]), "sk": rec["recordVersion"],
                     "createdAt": ts, "updatedAt": ts, "updatedBy": actor, "stateRevision": 1})
        item = _to_ddb(item)
        try:
            self._write_with_audit("Put", {"Item": item, "ConditionExpression": "attribute_not_exists(pk)"},
                                   rec["name"], rec["recordVersion"],
                                   audit_event(actor, "", rec["status"], reason, ts, transition=transition))
        except Exception as e:  # noqa: BLE001 — 조건 실패만 변환, 나머지는 그대로
            if _is_conditional_failure(e):
                raise ConflictError(f"이미 존재하는 레코드: {rec['name']} {rec['recordVersion']}")
            raise
        return _record_from_item(item)

    def get(self, name: str, version: str) -> Optional[dict]:
        r = self.table().get_item(Key={"pk": rec_pk(name), "sk": version}, ConsistentRead=True)
        it = r.get("Item")
        return _record_from_item(it) if it else None

    def versions(self, name: str) -> List[dict]:
        r = self.table().query(KeyConditionExpression="pk = :pk",
                               ExpressionAttributeValues={":pk": rec_pk(name)}, ConsistentRead=True)
        return [_record_from_item(it) for it in r.get("Items", [])]

    def by_status(self, status: str) -> List[dict]:
        out: List[dict] = []
        kwargs: Dict[str, Any] = dict(IndexName="byStatus", KeyConditionExpression="#st = :st",
                                      ExpressionAttributeNames={"#st": "status"},
                                      ExpressionAttributeValues={":st": status}, ScanIndexForward=False)
        while True:
            r = self.table().query(**kwargs)
            out += [_record_from_item(it) for it in r.get("Items", [])]
            lek = r.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return out

    def all_records(self) -> List[dict]:
        out: List[dict] = []
        for st in STATUSES:
            out += self.by_status(st)
        return out

    def transition(self, name: str, version: str, to_status: str, actor: str, reason: str = "",
                   *, expected_record: Optional[dict] = None) -> Tuple[dict, dict]:
        """상태 기계 검증 → 낙관적 조건 갱신 → 감사 이벤트. 반환 (record, audit_event)."""
        cur = self.get(name, version)
        if cur is None:
            raise NotFoundError(f"레코드 없음: {name} {version}")
        tname = check_transition(cur["status"], to_status, reason)
        ts = now_ms()
        condition = "attribute_exists(pk) AND #st = :from"
        names = {"#st": "status", "#ua": "updatedAt", "#ub": "updatedBy"}
        values = {":to": to_status, ":from": cur["status"], ":ts": ts, ":by": actor}
        condition += " AND " + _revision_condition(cur, names, values)
        if expected_record is not None:
            for index, field in enumerate(("status", "stateRevision", "payload", "description", "recordType", "subtype",
                                          "owner", "tags")):
                if cur.get(field) != expected_record.get(field):
                    raise ConflictError("The requested record changed before administrative approval")
                key = f"#expected{index}"
                names[key] = field
                if field in expected_record:
                    value = f":expected{index}"
                    values[value] = _to_ddb(expected_record[field])
                    condition += f" AND {key} = {value}"
                else:
                    condition += f" AND attribute_not_exists({key})"
        ev = audit_event(actor, cur["status"], to_status, reason, ts, transition=tname)
        try:
            self._write_with_audit("Update", {
                "Key": {"pk": rec_pk(name), "sk": version},
                "UpdateExpression": "SET #st = :to, #ua = :ts, #ub = :by, #revision = :revision",
                "ConditionExpression": condition, "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }, name, version, ev)
        except Exception as e:  # noqa: BLE001
            if _is_conditional_failure(e):
                raise ConflictError("상태가 이미 변경되었습니다. 새로고침 후 다시 시도하세요.") from e
            raise
        return {**cur, "status": to_status, "updatedAt": ts, "updatedBy": actor, "stateRevision": values[":revision"]}, ev

    def force_status(self, name: str, version: str, to_status: str, actor: str, reason: str) -> Tuple[dict, dict]:
        """상태 기계를 우회하는 직접 기록 (시연 리셋·기준선 재설정 전용). 감사 이벤트에 forced=True."""
        cur = self.get(name, version)
        if cur is None:
            raise NotFoundError(f"레코드 없음: {name} {version}")
        if to_status not in STATUSES:
            raise TransitionError(f"알 수 없는 목표 상태: {to_status}")
        ts = now_ms()
        names = {"#st": "status", "#ua": "updatedAt", "#ub": "updatedBy"}
        values = {":to": to_status, ":ts": ts, ":by": actor}
        condition = "attribute_exists(pk) AND " + _revision_condition(cur, names, values)
        ev = audit_event(actor, cur["status"], to_status, reason, ts, forced=True)
        try:
            self._write_with_audit("Update", {
                "Key": {"pk": rec_pk(name), "sk": version},
                "UpdateExpression": "SET #st = :to, #ua = :ts, #ub = :by, #revision = :revision",
                "ConditionExpression": condition, "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }, name, version, ev)
        except Exception as e:
            if _is_conditional_failure(e):
                raise ConflictError("The record changed during administrative status update") from e
            raise
        return {**cur, "status": to_status, "updatedAt": ts, "updatedBy": actor, "stateRevision": values[":revision"]}, ev

    def rewrite(self, name: str, version: str, fields: dict, actor: str) -> dict:
        """상태 외 필드(description/payload/tags/owner/subtype/recordType) 덮어쓰기 — 시드 reset 용."""
        allowed = {"description", "payload", "tags", "owner", "subtype", "recordType"}
        sets, names, values = [], {"#ua": "updatedAt", "#ub": "updatedBy"}, {":ts": now_ms(), ":by": actor}
        cur = self.get(name, version)
        if cur is None:
            raise NotFoundError(f"레코드 없음: {name} {version}")
        condition = "attribute_exists(pk) AND " + _revision_condition(cur, names, values)
        for i, (k, v) in enumerate(sorted(fields.items())):
            if k not in allowed:
                continue
            names[f"#f{i}"], values[f":f{i}"] = k, _to_ddb(v)
            sets.append(f"#f{i} = :f{i}")
        sets += ["#ua = :ts", "#ub = :by", "#revision = :revision"]
        try:
            r = self.table().update_item(Key={"pk": rec_pk(name), "sk": version},
                                         UpdateExpression="SET " + ", ".join(sets),
                                         ConditionExpression=condition,
                                         ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                                         ReturnValues="ALL_NEW")
        except Exception as e:  # noqa: BLE001
            if _is_conditional_failure(e):
                raise ConflictError("The record changed during administrative rewrite") from e
            raise
        return _record_from_item(r.get("Attributes") or {})

    def set_embedding(self, name: str, version: str, embedding_json: str) -> None:
        self.table().update_item(Key={"pk": rec_pk(name), "sk": version},
                                 UpdateExpression="SET #e = :e", ConditionExpression="attribute_exists(pk)",
                                 ExpressionAttributeNames={"#e": EMBEDDING_ATTR},
                                 ExpressionAttributeValues={":e": embedding_json})

    # ---------- 감사 ----------
    @staticmethod
    def _audit_item(name: str, version: str, ev: dict) -> dict:
        # sk = ts(13자리) # 프로세스 내 순번(6자리) # 난수 — 같은 ms 에 두 이벤트가 나도 최신순 정렬이 깨지지 않는다
        return {"pk": audit_pk(name, version),
                "sk": f"{int(ev['ts']):013d}#{next(_audit_seq) % 1000000:06d}#{uuid.uuid4().hex[:6]}",
                "name": name, "recordVersion": version, **ev}

    def put_audit(self, name: str, version: str, ev: dict) -> None:
        self.table().put_item(Item=self._audit_item(name, version, ev))

    def audit(self, name: str, version: str, limit: int = 50) -> List[dict]:
        r = self.table().query(KeyConditionExpression="pk = :pk",
                               ExpressionAttributeValues={":pk": audit_pk(name, version)},
                               ScanIndexForward=False, Limit=limit)
        return [{k: v for k, v in _plain(it).items() if k not in ("pk", "sk")} for it in r.get("Items", [])]
