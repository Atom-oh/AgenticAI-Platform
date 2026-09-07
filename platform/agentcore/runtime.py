"""AgentCore Runtime(Strands 컨테이너, agents/) 호출 래퍼 — harness.invoke_stream 과 같은 튜플 프로토콜.

invoke_agent_runtime(agentRuntimeArn, runtimeSessionId(≥33자), payload=JSON bytes, accept='text/event-stream')
→ response['response'] 는 SSE 라인 스트림('data: {...}')이다. 컨테이너(agents/app.py)가 내는 이벤트를
  ('text', str) · ('tool_start', dict) · ('tool_input', dict) · ('tool_result', dict) · ('boundary', dict) · ('error', str)
  · 마지막 ('meta', {usage, stopReason, sessionId, modelId, runtime}) 로 정규화한다.
환경변수: AWS_REGION(기본 ap-northeast-2). boto3 클라이언트는 지연 생성한다 (테스트는 AWS 호출 없이 parse_sse 만 쓴다).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
RUNTIME_LABEL = "agentcore-runtime/strands"
MIN_SESSION_LEN = 33
_SESSION_OK = re.compile(r"[^A-Za-z0-9_-]")

_data = None


def data():
    global _data
    if _data is None:
        import boto3

        _data = boto3.client("bedrock-agentcore", region_name=REGION)
    return _data


def new_session_id() -> str:
    """AgentCore runtimeSessionId 규격(33~256자) — 41자."""
    return uuid.uuid4().hex + "-" + uuid.uuid4().hex[:8]


def normalize_session_id(session_id: Optional[str]) -> str:
    """주어진 세션 ID 를 규격에 맞춘다: 허용 문자만 남기고, 33자 미만이면 결정론적 해시 접미어로 채운다 (같은 입력 → 같은 출력)."""
    if not session_id or not str(session_id).strip():
        return new_session_id()
    sid = _SESSION_OK.sub("-", str(session_id).strip())[:200]
    if len(sid) < MIN_SESSION_LEN:
        sid = sid + "-" + hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[: MIN_SESSION_LEN - len(sid) + 8]
    return sid[:256]


def _lines(body: Any) -> Iterable[str]:
    """botocore StreamingBody(iter_lines) · bytes · str · 라인 iterable 을 모두 문자열 라인으로."""
    if body is None:
        return []
    if hasattr(body, "iter_lines"):
        it = body.iter_lines()
    elif isinstance(body, (bytes, bytearray)):
        it = bytes(body).splitlines()
    elif isinstance(body, str):
        it = body.splitlines()
    elif hasattr(body, "read"):
        raw = body.read()
        it = (raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")).splitlines()
    else:
        it = body

    def gen():
        for line in it:
            if isinstance(line, (bytes, bytearray)):
                yield bytes(line).decode("utf-8", errors="replace")
            else:
                yield str(line)

    return gen()


def parse_events(body: Any) -> Iterator[dict]:
    """SSE 라인 → 이벤트 dict. 'data: ' 접두어 유무 모두 허용, 빈 줄·':' 주석·비JSON 라인은 건너뛴다.
    여러 'data:' 라인이 빈 줄 없이 이어져도 각각 독립 JSON 으로 처리한다 (컨테이너는 이벤트마다 한 줄을 쓴다)."""
    for raw in _lines(body):
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
            if not line:
                continue
        elif line.startswith(("event:", "id:", "retry:")):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            yield ev


def to_tuples(events: Iterable[dict], session_id: str) -> Iterator[Tuple[str, Any]]:
    """컨테이너 이벤트 → harness.invoke_stream 튜플. 마지막에 반드시 ('meta', {...}) 를 낸다."""
    meta: Optional[Dict[str, Any]] = None
    for ev in events:
        t = ev.get("type")
        if t == "text":
            s = ev.get("t", "")
            if s:
                yield ("text", str(s))
        elif t in ("tool_start", "tool_input", "tool_result", "boundary", "stage", "design_done"):
            yield (t, {k: v for k, v in ev.items() if k != "type"})
        elif t == "error":
            msg = str(ev.get("message", ""))
            if ev.get("gate") == "refused":
                yield ("error", msg)
            else:
                yield ("error", msg or json.dumps(ev, ensure_ascii=False)[:400])
        elif t == "meta":
            meta = {k: v for k, v in ev.items() if k != "type"}
        elif "error" in ev and t is None:
            # bedrock_agentcore 런타임이 스트리밍 예외를 감싸 내는 형태: {"error":..., "error_type":..., "message":...}
            yield ("error", f"{ev.get('error_type', 'Error')}: {str(ev.get('error', ''))[:300]}")
    if meta is None:
        meta = {"usage": {}, "stopReason": "", "sessionId": session_id, "runtime": RUNTIME_LABEL, "incomplete": True}
    meta.setdefault("usage", {})
    meta.setdefault("stopReason", "")
    meta.setdefault("sessionId", session_id)
    meta.setdefault("runtime", RUNTIME_LABEL)
    yield ("meta", meta)


def invoke_stream(runtime_arn: str, agent_name: str, text: str, session_id: Optional[str] = None,
                  model: Optional[str] = None, qualifier: Optional[str] = None,
                  extra: Optional[Dict[str, Any]] = None) -> Iterator[Tuple[str, Any]]:
    """AgentCore Runtime 을 호출해 (event_type, payload) 튜플을 yield — harness.invoke_stream 과 같은 계약.
    extra: 페이로드에 병합할 추가 필드 (예: design_flow_agent 의 {"design": {...}})."""
    if not runtime_arn:
        raise ValueError("runtime_arn is required (AGENTS_RUNTIME_ARN)")
    sid = normalize_session_id(session_id)
    body: Dict[str, Any] = {"agent": agent_name, "prompt": text, "sessionId": sid}
    if model:
        body["model"] = model
    if extra:
        body.update(extra)
    kw: Dict[str, Any] = {"agentRuntimeArn": runtime_arn, "runtimeSessionId": sid,
                          "payload": json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          "contentType": "application/json", "accept": "text/event-stream"}
    if qualifier:
        kw["qualifier"] = qualifier
    r = data().invoke_agent_runtime(**kw)
    ctype = str(r.get("contentType") or "")
    stream = r.get("response")
    if "json" in ctype and "event-stream" not in ctype:
        # 비스트리밍 응답(JSON 본문 1개 또는 이벤트 배열)도 관용적으로 처리
        raw = stream.read() if hasattr(stream, "read") else stream
        try:
            obj = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        except Exception:  # noqa: BLE001
            obj = {"type": "error", "message": "non-JSON runtime response"}
        events = obj if isinstance(obj, list) else [obj]
        for tup in to_tuples([e for e in events if isinstance(e, dict)], sid):
            yield tup
        return
    for tup in to_tuples(parse_events(stream), sid):
        yield tup
