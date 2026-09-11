# platform/studio/store.py
"""스튜디오 저장소 — 잡·라운드·시안(승인 상태). DynamoDB(STUDIO_TABLE) 또는 인메모리 페이크(테스트)."""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any, Optional

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
STATUSES = ("검토중", "승인됨", "반려")


def now_ms() -> int:
    return int(time.time() * 1000)


class StudioStore:
    def __init__(self, table: Any = None, table_name: Optional[str] = None) -> None:
        self._table = table
        self._table_name = table_name if table_name is not None else os.environ.get("STUDIO_TABLE", "")

    @property
    def backend(self) -> str:
        if self._table is not None and type(self._table).__name__ == "InMemoryTable":
            return "memory"
        return "dynamodb" if (self._table is not None or self._table_name) else "memory"

    def table(self) -> Any:
        if self._table is None:
            if self._table_name:
                import boto3  # 지연 import — 테스트 오프라인
                self._table = boto3.resource("dynamodb", region_name=REGION).Table(self._table_name)
            else:
                from registry.fake_table import InMemoryTable
                self._table = InMemoryTable(hash_key="pk", range_key="sk")
        return self._table

    # ---------- 공통 ----------
    def _get(self, pk: str, sk: str) -> Optional[dict]:
        it = self.table().get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return _clean(it) if it else None

    def _list(self, pk: str, limit: int, prefix: str = "") -> list:
        kwargs: dict = dict(KeyConditionExpression="pk = :pk" + (" AND begins_with(sk, :p)" if prefix else ""),
                            ExpressionAttributeValues={":pk": pk, **({":p": prefix} if prefix else {})},
                            ScanIndexForward=False, Limit=limit)
        return [_clean(i) for i in self.table().query(**kwargs).get("Items", [])]

    # ---------- 잡 ----------
    def put_job(self, job: dict, actor: str) -> dict:
        ts = now_ms()
        item = {**job, "pk": f"job#{job['jobId']}", "sk": "meta", "status": "running", "createdAt": ts, "createdBy": actor, "updatedAt": ts}
        self.table().put_item(Item=item)
        self.table().put_item(Item={"pk": "jobs", "sk": f"{ts:013d}#{job['jobId']}", "jobId": job["jobId"], "brief": job.get("brief", "")[:200],
                                    "productCode": job.get("productCode", ""), "status": "running", "createdAt": ts, "createdBy": actor})
        return _clean(item)

    def update_job(self, job_id: str, **fields) -> None:
        """읽기-수정-쓰기, 조건식 없음 — 잡마다 쓰기자가 하나(해당 워커)라서 안전하다."""
        meta = self._get(f"job#{job_id}", "meta")
        if not meta:
            return
        meta.update(fields)
        meta["updatedAt"] = now_ms()
        self.table().put_item(Item={**meta, "pk": f"job#{job_id}", "sk": "meta"})
        row = {"pk": "jobs", "sk": f"{int(meta['createdAt']):013d}#{job_id}", "jobId": job_id, "brief": str(meta.get("brief", ""))[:200],
               "productCode": meta.get("productCode", ""), "status": meta.get("status", "running"), "createdAt": meta["createdAt"],
               "createdBy": meta.get("createdBy", ""), "score": meta.get("score"), "stopReason": meta.get("stopReason"), "draftId": meta.get("draftId")}
        self.table().put_item(Item={k: v for k, v in row.items() if v is not None})

    def get_job(self, job_id: str) -> Optional[dict]:
        meta = self._get(f"job#{job_id}", "meta")
        if not meta:
            return None
        rounds = sorted(self._list(f"job#{job_id}", 50, prefix="round#"), key=lambda r: r.get("round", 0))
        return {**meta, "rounds": rounds}

    def list_jobs(self, limit: int = 20) -> list:
        return self._list("jobs", limit)

    def put_round(self, job_id: str, rec: dict) -> None:
        item = {k: v for k, v in rec.items() if k not in ("html", "items")}
        self.table().put_item(Item={**item, "pk": f"job#{job_id}", "sk": f"round#{int(rec['round']):02d}", "createdAt": now_ms()})
        if "items" in rec:
            self.table().put_item(Item={"pk": f"report#{job_id}", "sk": f"round#{int(rec['round']):02d}",
                                       "items": rec["items"]})

    def get_round(self, job_id: str, number: int) -> Optional[dict]:
        key = f"round#{number:02d}"
        meta = self._get(f"job#{job_id}", key)
        if meta is None:
            return None
        report = self._get(f"report#{job_id}", key)
        return {**meta, "items": (report or {}).get("items", []), "itemsComplete": report is not None}

    # ---------- 시안 ----------
    def put_draft(self, draft: dict) -> dict:
        existing = self.get_draft(draft.get("draftId", ""))
        created_at = existing["createdAt"] if existing else (draft.get("createdAt") or now_ms())
        d = {**draft, "status": draft.get("status") or "검토중", "comment": draft.get("comment", ""), "createdAt": created_at}
        self.table().put_item(Item={**d, "pk": f"draft#{d['draftId']}", "sk": "meta"})
        self._put_draft_row(d)
        return d

    def _put_draft_row(self, d: dict) -> None:
        self.table().put_item(Item={**d, "pk": "drafts", "sk": f"{int(d['createdAt']):013d}#{d['draftId']}"})

    def get_draft(self, draft_id: str) -> Optional[dict]:
        return self._get(f"draft#{draft_id}", "meta")

    def list_drafts(self, limit: int = 60) -> list:
        return self._list("drafts", limit)

    def set_draft_status(self, draft_id: str, status: str, comment: str, actor: str) -> Optional[dict]:
        d = self.get_draft(draft_id)
        if not d or status not in STATUSES:
            return None
        d.update({"status": status, "comment": (comment or "")[:500], "reviewedBy": actor, "reviewedAt": now_ms()})
        if status == "승인됨":
            d["approvalScope"] = "static-design"
        self.table().put_item(Item={**d, "pk": f"draft#{draft_id}", "sk": "meta"})
        self._put_draft_row(d)
        return d

    def approved_drafts(self, limit: int = 2) -> list:
        return [d for d in self.list_drafts(200) if d.get("status") == "승인됨"][:limit]


def _plain(obj: Any) -> Any:
    """DynamoDB 리소스가 돌려주는 Decimal 을 int/float 로 되돌린다 (재귀)."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _clean(item: dict) -> dict:
    return {k: v for k, v in _plain(item).items() if k not in ("pk", "sk")}
