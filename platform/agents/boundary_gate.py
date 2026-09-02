"""익명화 게이트 — 에이전트 런타임 내부의 모델 호출 직전 검사기 (Strands hook).

SPEC v2 §3-2: 모든 Bedrock 호출은 익명화 게이트를 통과한다. 도구 계층(agentcore/gateway_tools.py)이 개인데이터를
VPC 내부에서 마스킹한 뒤에만 반환하지만, 이 훅은 "식별자가 Bedrock에 도달하지 않았다"를 **실측**으로 증명하는
두 번째 검사기다. 모델 호출 직전(BeforeModelCallEvent)에 시스템 프롬프트 + 대화 메시지 전체(사용자 입력·도구 결과·
도구 입력)를 규칙 스캔하고,
  - 히트가 있으면 GateRefused 예외를 던져 모델 호출이 일어나지 않게 한다 (하드코딩된 0건 없음 — 실제로 막는다).
  - 히트가 없으면 {chars, estTokens=chars//3, piiRules: 0} 를 기록해 app.py 가 'boundary' 이벤트로 스트리밍한다.

RULES 는 api/common/pii.py 의 RULES 와 같은 내용이다 — 컨테이너는 api/ 를 import 하지 않으므로 복사본을 둔다.
(규칙을 바꾸면 두 곳을 함께 바꾼다. tests/test_agents_runtime.py 가 두 목록의 일치를 검사한다.)

이 모듈은 strands 없이도 import 가능해야 한다 (오프라인 테스트가 importlib 로 직접 로드한다).
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional

try:  # strands 는 컨테이너에만 설치된다 — 없으면 규칙 스캔 함수만 제공
    from strands.hooks import BeforeModelCallEvent, HookProvider, HookRegistry
    STRANDS_AVAILABLE = True
except ImportError:  # pragma: no cover - 오프라인 테스트 경로
    HookProvider = object  # type: ignore[assignment,misc]
    HookRegistry = Any  # type: ignore[assignment,misc]
    BeforeModelCallEvent = Any  # type: ignore[assignment,misc]
    STRANDS_AVAILABLE = False

# ---- api/common/pii.py RULES 와 동일 ----
RULES = [
    ("KR_RRN", re.compile(r"(?<!\d)\d{6}-?[1-8]\d{6}(?!\d)")),          # 주민/외국인등록번호
    ("KR_PASSPORT", re.compile(r"\b[MSRODG]\d{8}\b")),
    ("PHONE", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("CARD", re.compile(r"(?<!\d)(?:\d{4}[- ]?){3}\d{4}(?!\d)")),
    ("CUSTOMER_TOKEN", re.compile(r"\bCUST-\d{3,}\b")),
    ("ACCOUNT_TOKEN", re.compile(r"\bACCT-\d{3,}\b")),
    ("KR_BANK_ACCOUNT", re.compile(r"(?<!\d)\d{3}-\d{2,6}-\d{2,6}(?:-\d{1,3})?(?!\d)")),
]

TOKENS_PER_CHAR_DIVISOR = 3  # estTokens = chars // 3 (계측 표기 규칙 — 정확한 토큰 수는 meta.usage 가 실측)


def _luhn_ok(num: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", num)]
    if len(digits) < 13:
        return False
    s = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def scan_rules(text: str) -> List[Dict[str, str]]:
    """api/common/pii.scan_rules 와 같은 결과 형태: [{type, detector, sample}] (sample 은 앞 3자 + '…')."""
    hits = []
    for kind, pat in RULES:
        for m in pat.findall(text or ""):
            if kind == "CARD" and not _luhn_ok(m):
                continue
            hits.append({"type": kind, "detector": "rules", "sample": m[:3] + "…"})
    return hits


def _collect(node: Any, out: List[str]) -> None:
    """메시지 구조에서 Bedrock 으로 나가는 문자열을 모은다. json/input 블록은 실제 전송 형태(JSON 문자열)로 직렬화."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in ("json", "input") and not isinstance(v, str):
                out.append(json.dumps(v, ensure_ascii=False, default=str))
            elif k in ("bytes", "source"):
                continue  # 이미지/문서 바이트 — 텍스트 규칙 대상 아님
            else:
                _collect(v, out)
    elif isinstance(node, (list, tuple)):
        for x in node:
            _collect(x, out)


def outgoing_text(messages: Optional[List[dict]], system_prompt: Any = None) -> str:
    parts: List[str] = []
    if system_prompt:
        _collect(system_prompt, parts)
    _collect(messages or [], parts)
    return "\n".join(parts)


def measure(messages: Optional[List[dict]], system_prompt: Any = None) -> Dict[str, Any]:
    """반환: {chars, estTokens, piiRules, hits:[type,...], messages}. hits 에는 값이 아니라 규칙 이름만 담는다."""
    text = outgoing_text(messages, system_prompt)
    hits = scan_rules(text)
    return {
        "chars": len(text),
        "estTokens": len(text) // TOKENS_PER_CHAR_DIVISOR,
        "piiRules": len(hits),
        "hits": sorted({h["type"] for h in hits}),
        "messages": len(messages or []),
    }


class GateRefused(RuntimeError):
    """익명화 게이트 거부 — 메시지에 식별자 규칙 히트가 있어 모델 호출을 수행하지 않았다."""

    def __init__(self, types: List[str], measurement: Optional[dict] = None):
        self.types = sorted(set(types))
        self.measurement = measurement or {}
        super().__init__("GateRefused: " + ",".join(self.types))


class BoundaryGateHook(HookProvider):  # type: ignore[misc]
    """Strands HookProvider — BeforeModelCallEvent 마다 나가는 메시지를 스캔한다.

    - 히트 → GateRefused 예외 (모델 호출 없음). 이벤트 루프가 EventLoopException 으로 감싸 올릴 수 있으므로
      app.py 는 예외 체인(__cause__)에서 GateRefused 를 찾는다.
    - 통과 → measurements 에 {chars, estTokens, piiRules: 0, ...} 추가. app.py 가 drain() 으로 꺼내 'boundary' 이벤트로 낸다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.measurements: List[Dict[str, Any]] = []
        self._emitted = 0
        self.refused: Optional[Dict[str, Any]] = None
        self.calls = 0

    # HookProvider 인터페이스
    def register_hooks(self, registry: "HookRegistry", **kwargs: Any) -> None:  # noqa: D401
        if not STRANDS_AVAILABLE:  # pragma: no cover
            raise RuntimeError("strands is not installed — BoundaryGateHook cannot register")
        registry.add_callback(BeforeModelCallEvent, self.before_model_call)

    def before_model_call(self, event: "BeforeModelCallEvent") -> None:
        agent = getattr(event, "agent", None)
        messages = list(getattr(agent, "messages", []) or [])
        system_prompt = getattr(agent, "system_prompt", None)
        self.check(messages, system_prompt)

    def check(self, messages: List[dict], system_prompt: Any = None) -> Dict[str, Any]:
        """훅 본체 (strands 없이도 테스트 가능). 히트 시 GateRefused."""
        m = measure(messages, system_prompt)
        with self._lock:
            self.calls += 1
            m["seq"] = self.calls
            self.measurements.append(m)
            if m["piiRules"]:
                self.refused = m
        if m["piiRules"]:
            raise GateRefused(m["hits"], m)
        return m

    def drain(self) -> List[Dict[str, Any]]:
        """아직 스트리밍하지 않은 계측값을 순서대로 반환한다."""
        with self._lock:
            new = self.measurements[self._emitted:]
            self._emitted = len(self.measurements)
        return [dict(x) for x in new]

    @property
    def last(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self.measurements[-1]) if self.measurements else None

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "modelCalls": self.calls,
                "chars": sum(int(m.get("chars", 0)) for m in self.measurements),
                "estTokens": sum(int(m.get("estTokens", 0)) for m in self.measurements),
                "piiRules": sum(int(m.get("piiRules", 0)) for m in self.measurements),
                "refused": bool(self.refused),
                "refusedTypes": list(self.refused["hits"]) if self.refused else [],
            }


def find_gate_refusal(exc: BaseException) -> Optional[GateRefused]:
    """예외 체인(__cause__/__context__/original_exception)에서 GateRefused 를 찾는다."""
    seen = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, GateRefused):
            return cur
        nxt = getattr(cur, "original_exception", None)
        cur = nxt if isinstance(nxt, BaseException) else (cur.__cause__ or cur.__context__)
    return None
