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
    # 토큰 배치: API Gateway @connections 는 초당 프레임 수에 상한이 있다(TooManyRequests 실측) —
    # 토큰 델타를 모아 ~60ms 또는 32자 단위로 한 프레임에 보낸다. 다른 이벤트/done 전에는 항상 비운다.
    _tok_buf: dict = field(default_factory=dict)
    _tok_at: float = 0.0
    token_batch_chars: int = 32
    token_batch_ms: int = 60

    def post(self, payload: dict) -> None:
        if self._tok_buf and not str(payload.get("type", "")).endswith(".token"):
            self.flush_tokens()
        self._post_raw(payload)

    def _post_raw(self, payload: dict) -> None:
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
        buf = self._tok_buf.setdefault(kind, [])
        if not buf:
            self._tok_at = time.time()
        buf.append(text)
        if sum(len(x) for x in buf) >= self.token_batch_chars or (time.time() - self._tok_at) * 1000 >= self.token_batch_ms:
            self.flush_tokens(kind)

    def flush_tokens(self, kind: str | None = None) -> None:
        kinds = [kind] if kind else list(self._tok_buf.keys())
        for k in kinds:
            buf = self._tok_buf.pop(k, None)
            if buf:
                self._post_raw({"type": f"{k}.token", "t": "".join(buf)})

    def done(self, kind: str, **kw) -> None:
        self.flush_tokens()
        kw.setdefault("elapsedMs", self.elapsed_ms())
        self.post({"type": f"{kind}.done", **kw})

    def error(self, message: str, **kw) -> None:
        self.post({"type": "error", "message": str(message)[:300], **kw})

    def elapsed_ms(self) -> int:
        return int((time.time() - self.started) * 1000)
