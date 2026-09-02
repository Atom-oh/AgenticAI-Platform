"""요청 컨텍스트 — 모든 핸들러가 공유하는 push/stage/done 도우미.

핸들러 계약: def handle(ctx: Ctx, body: dict) -> None
  ctx.post(payload)            : 이벤트 1건 push (reqId·traceId 자동 부여)
  ctx.stage(kind, step, **kw)  : {"type": f"{kind}.stage", "step": step, ...}
  ctx.done(kind, **kw)         : {"type": f"{kind}.done", "elapsedMs": ..., ...}
  ctx.error(message)           : {"type": "error", ...}
프론트(lib.ts)는 reqId로 응답을 매칭하고 `.stage`/`.token`은 스트림, `.done`은 종료로 본다.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Ctx:
    apigw: object
    conn_id: str
    email: str
    rid: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started: float = field(default_factory=time.time)
    # 캐시 폴백용 이벤트 녹화 (costguard가 켠다)
    recording: list | None = None

    def post(self, payload: dict) -> None:
        payload.setdefault("reqId", self.rid)
        payload.setdefault("traceId", self.trace_id)
        if self.recording is not None and payload.get("type") != "error":
            self.recording.append(payload)
        self.apigw.post_to_connection(
            ConnectionId=self.conn_id,
            Data=json.dumps(payload, ensure_ascii=False, default=str).encode())

    def stage(self, kind: str, step: str, **kw) -> None:
        self.post({"type": f"{kind}.stage", "step": step, **kw})

    def token(self, kind: str, text: str) -> None:
        self.post({"type": f"{kind}.token", "t": text})

    def done(self, kind: str, **kw) -> None:
        kw.setdefault("elapsedMs", self.elapsed_ms())
        self.post({"type": f"{kind}.done", **kw})

    def error(self, message: str, **kw) -> None:
        self.post({"type": "error", "message": str(message)[:300], **kw})

    def elapsed_ms(self) -> int:
        return int((time.time() - self.started) * 1000)
