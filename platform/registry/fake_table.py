"""인메모리 DynamoDB 테이블 페이크 — 테스트·로컬 개발용 (boto3 의존 없음).

RegistryStore 가 사용하는 boto3 Table 리소스 표면만 흉내낸다 (문자열 표현식 기반):
  put_item(Item, ConditionExpression?)         attribute_not_exists / attribute_exists / = / AND
  get_item(Key)
  query(KeyConditionExpression, ExpressionAttributeValues, ExpressionAttributeNames?, IndexName?,
        ScanIndexForward?, Limit?, ExclusiveStartKey?)   `a = :v` [AND begins_with(b, :p) | b = :v]
  update_item(Key, UpdateExpression, ExpressionAttributeNames?, ExpressionAttributeValues?,
              ConditionExpression?, ReturnValues?)        SET a = :v, #b = :w  REMOVE c
  scan(FilterExpression?, ExpressionAttributeValues?, ExpressionAttributeNames?)
조건 실패는 botocore ClientError 와 같은 .response["Error"]["Code"] 를 갖는 예외로 던진다.
운영 DynamoDB 는 이 파일을 쓰지 않는다 — 시연은 REGISTRY_TABLE 실물 테이블로 돈다.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


class ConditionalCheckFailedException(Exception):
    """boto3 ClientError 호환 형태 (.response['Error']['Code'])."""

    def __init__(self, message: str = "The conditional request failed") -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": "ConditionalCheckFailedException", "Message": message}}


_FUNC_RE = re.compile(r"^(attribute_exists|attribute_not_exists|begins_with)\((.+)\)$")


def _resolve_name(token: str, names: Dict[str, str]) -> str:
    token = token.strip()
    if token.startswith("#"):
        if token not in names:
            raise ValueError(f"ExpressionAttributeNames 에 {token} 이 없습니다")
        return names[token]
    return token


def _resolve_value(token: str, values: Dict[str, Any]) -> Any:
    token = token.strip()
    if not token.startswith(":"):
        raise ValueError(f"값 자리표시자(:v)가 필요합니다: {token}")
    if token not in values:
        raise ValueError(f"ExpressionAttributeValues 에 {token} 이 없습니다")
    return values[token]


def _split_and(expr: str) -> List[str]:
    """최상위 AND 분리 (괄호 안 AND 는 무시)."""
    parts, depth, cur = [], 0, []
    tokens = re.split(r"(\s+AND\s+|\(|\))", expr, flags=re.IGNORECASE)
    for t in tokens:
        if t is None or t == "":
            continue
        if t == "(":
            depth += 1
            cur.append(t)
        elif t == ")":
            depth -= 1
            cur.append(t)
        elif re.fullmatch(r"\s+AND\s+", t, flags=re.IGNORECASE) and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(t)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def compile_condition(expr: Optional[str], names: Dict[str, str], values: Dict[str, Any]) -> Callable[[Optional[dict]], bool]:
    """조건식 → predicate(item|None). 지원: attribute_exists/not_exists, begins_with, =, <>, AND."""
    if not expr or not expr.strip():
        return lambda item: True
    clauses = []
    for part in _split_and(expr.strip()):
        part = part.strip()
        while part.startswith("(") and part.endswith(")"):
            part = part[1:-1].strip()
        m = _FUNC_RE.match(part)
        if m:
            fn, args = m.group(1), [a.strip() for a in m.group(2).split(",")]
            attr = _resolve_name(args[0], names)
            if fn == "attribute_exists":
                clauses.append(lambda it, a=attr: it is not None and a in it)
            elif fn == "attribute_not_exists":
                clauses.append(lambda it, a=attr: it is None or a not in it)
            else:  # begins_with
                prefix = _resolve_value(args[1], values)
                clauses.append(lambda it, a=attr, p=prefix: it is not None and isinstance(it.get(a), str)
                               and it.get(a).startswith(p))
            continue
        m2 = re.match(r"^(\S+)\s*(=|<>|<=|>=|<|>)\s*(\S+)$", part)
        if not m2:
            raise ValueError(f"지원하지 않는 조건식: {part}")
        attr, op, vtok = _resolve_name(m2.group(1), names), m2.group(2), m2.group(3)
        val = _resolve_value(vtok, values)

        def _cmp(it, a=attr, o=op, v=val):
            if it is None or a not in it:
                return False
            x = it[a]
            if o == "=":
                return x == v
            if o == "<>":
                return x != v
            if o == "<":
                return x < v
            if o == "<=":
                return x <= v
            if o == ">":
                return x > v
            return x >= v
        clauses.append(_cmp)
    return lambda item: all(c(item) for c in clauses)


def _apply_update(item: dict, expr: str, names: Dict[str, str], values: Dict[str, Any]) -> None:
    """SET a = :v, #b = :w  REMOVE c, d  — 두 절만 지원."""
    sections = re.split(r"\b(SET|REMOVE)\b", expr.strip())
    i = 1
    while i < len(sections):
        kw, body = sections[i].upper(), sections[i + 1]
        i += 2
        if kw == "SET":
            for assign in _split_commas(body):
                lhs, rhs = [s.strip() for s in assign.split("=", 1)]
                item[_resolve_name(lhs, names)] = copy.deepcopy(_resolve_value(rhs, values))
        else:
            for attr in _split_commas(body):
                item.pop(_resolve_name(attr, names), None)


def _split_commas(s: str) -> List[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


class InMemoryTable:
    """단일 테이블 + 임의 개수의 GSI. 키 스키마는 생성자에서 지정."""

    def __init__(self, hash_key: str = "pk", range_key: Optional[str] = "sk",
                 indexes: Optional[Dict[str, Tuple[str, Optional[str]]]] = None) -> None:
        self.hash_key, self.range_key = hash_key, range_key
        self.indexes: Dict[str, Tuple[str, Optional[str]]] = dict(indexes or {})
        self._items: Dict[Tuple[Any, Any], dict] = {}
        self.calls: List[str] = []  # 테스트에서 호출 순서 검증용

    # -- 내부 --
    def _key_of(self, item: dict) -> Tuple[Any, Any]:
        if self.hash_key not in item:
            raise ValueError(f"키 속성 {self.hash_key} 누락")
        if self.range_key and self.range_key not in item:
            raise ValueError(f"키 속성 {self.range_key} 누락")
        return (item[self.hash_key], item.get(self.range_key) if self.range_key else None)

    def _schema(self, index_name: Optional[str]) -> Tuple[str, Optional[str]]:
        if index_name is None:
            return self.hash_key, self.range_key
        if index_name not in self.indexes:
            raise ValueError(f"인덱스 없음: {index_name}")
        return self.indexes[index_name]

    # -- API --
    def put_item(self, Item: dict, ConditionExpression: Optional[str] = None,
                 ExpressionAttributeNames: Optional[dict] = None,
                 ExpressionAttributeValues: Optional[dict] = None, **_: Any) -> dict:
        self.calls.append("put_item")
        key = self._key_of(Item)
        existing = self._items.get(key)
        pred = compile_condition(ConditionExpression, ExpressionAttributeNames or {}, ExpressionAttributeValues or {})
        if not pred(existing):
            raise ConditionalCheckFailedException()
        self._items[key] = copy.deepcopy(Item)
        return {}

    def get_item(self, Key: dict, **_: Any) -> dict:
        self.calls.append("get_item")
        it = self._items.get(self._key_of(Key))
        return {"Item": copy.deepcopy(it)} if it is not None else {}

    def update_item(self, Key: dict, UpdateExpression: str, ExpressionAttributeNames: Optional[dict] = None,
                    ExpressionAttributeValues: Optional[dict] = None, ConditionExpression: Optional[str] = None,
                    ReturnValues: str = "NONE", **_: Any) -> dict:
        self.calls.append("update_item")
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        key = self._key_of(Key)
        existing = self._items.get(key)
        pred = compile_condition(ConditionExpression, names, values)
        if not pred(existing):
            raise ConditionalCheckFailedException()
        item = copy.deepcopy(existing) if existing is not None else dict(Key)
        _apply_update(item, UpdateExpression, names, values)
        self._items[key] = item
        return {"Attributes": copy.deepcopy(item)} if ReturnValues in ("ALL_NEW", "UPDATED_NEW") else {}

    def delete_item(self, Key: dict, **_: Any) -> dict:
        self.calls.append("delete_item")
        self._items.pop(self._key_of(Key), None)
        return {}

    def query(self, KeyConditionExpression: str, ExpressionAttributeValues: Optional[dict] = None,
              ExpressionAttributeNames: Optional[dict] = None, IndexName: Optional[str] = None,
              ScanIndexForward: bool = True, Limit: Optional[int] = None,
              ExclusiveStartKey: Optional[dict] = None, FilterExpression: Optional[str] = None, **_: Any) -> dict:
        self.calls.append("query")
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        hk, rk = self._schema(IndexName)
        pred = compile_condition(KeyConditionExpression, names, values)
        filt = compile_condition(FilterExpression, names, values)
        rows = [copy.deepcopy(it) for it in self._items.values()
                if hk in it and (rk is None or rk in it) and pred(it) and filt(it)]
        rows.sort(key=lambda it: (_sortable(it.get(rk)) if rk else 0, _sortable(it.get(self.hash_key)),
                                  _sortable(it.get(self.range_key)) if self.range_key else 0),
                  reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            start = self._key_of(ExclusiveStartKey)
            idx = next((i for i, it in enumerate(rows) if self._key_of(it) == start), None)
            rows = rows[idx + 1:] if idx is not None else rows
        out: dict = {}
        if Limit is not None and len(rows) > Limit:
            rows, rest = rows[:Limit], rows[Limit]
            out["LastEvaluatedKey"] = {k: rows[-1][k] for k in (self.hash_key, self.range_key) if k}
            del rest
        out["Items"], out["Count"] = rows, len(rows)
        return out

    def scan(self, FilterExpression: Optional[str] = None, ExpressionAttributeValues: Optional[dict] = None,
             ExpressionAttributeNames: Optional[dict] = None, ExclusiveStartKey: Optional[dict] = None,
             Limit: Optional[int] = None, **_: Any) -> dict:
        self.calls.append("scan")
        filt = compile_condition(FilterExpression, ExpressionAttributeNames or {}, ExpressionAttributeValues or {})
        rows = [copy.deepcopy(it) for it in self._items.values() if filt(it)]
        rows.sort(key=lambda it: (_sortable(it.get(self.hash_key)), _sortable(it.get(self.range_key)) if self.range_key else 0))
        if Limit is not None:
            rows = rows[:Limit]
        return {"Items": rows, "Count": len(rows)}

    # -- 테스트 편의 --
    def __len__(self) -> int:
        return len(self._items)

    def dump(self) -> List[dict]:
        return [copy.deepcopy(it) for it in self._items.values()]


def _sortable(v: Any) -> Any:
    """서로 다른 타입이 섞여도 정렬이 깨지지 않게 (None < number < str)."""
    if v is None:
        return (0, 0)
    if isinstance(v, (int, float)):
        return (1, v)
    return (2, str(v))


def new_registry_table() -> InMemoryTable:
    """RegistryStore 키 설계와 같은 페이크 테이블 (pk/sk + GSI byStatus(status, updatedAt))."""
    return InMemoryTable(hash_key="pk", range_key="sk", indexes={"byStatus": ("status", "updatedAt")})
