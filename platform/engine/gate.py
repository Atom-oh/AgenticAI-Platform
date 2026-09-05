"""익명화 게이트 — 모델로 나가는 **유일한** 통과 지점 (SPEC v2 §3-2, §11-2, §12.1).

모든 LLM 생성·임베딩·리랭크 호출은 이 모듈을 지난다. 여기서
  ① 페이로드를 실측한다 — 문자 수, 추정 토큰(chars//3), 전달 필드 라벨(`- 적용금리: …`/`key: value` 줄), 규칙 기반 PII 스캔
     (api/common/pii.py 의 scan_rules — 플레인 마스킹과 독립된 탐지기)
  ② 식별자(주민번호·카드·휴대전화·고객/계좌 토큰·계좌번호)가 하나라도 있으면 GateRefused — 페이로드는 모델에 도달하지 않는다
  ③ 경로(LLM_ROUTE: claude | gemma | idc_vllm, 호출별 override 가능)에 따라 어댑터로 보내고,
     스트림에 .usage/.model_id/.route/.tier/.boundary 를 채운다
  ④ 'gate.crossing' 로그 1건 — 메트릭과 traceId 만, 원문 없음 (§12.5)

변환(가명처리·재식별) 로직은 이 게이트에 없다 — 합성데이터가 이미 가명 형태(§9)이고 플레인의 규칙 기반 토큰화가 앞단에 있다(§16).
화면 배지 문구는 badges() 가 사실대로 제공한다 (§11-1, §11-2).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from engine import llm

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
RERANK_REGION = "ap-northeast-1"   # rerank 모델은 서울 미제공 — 도쿄 (합성 문서만 전달)
GEN_MODEL = os.environ.get("GEN_MODEL", llm.DEFAULT_CLAUDE_MODEL)
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", llm.DEFAULT_GEMMA_MODEL)
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
RERANK_MODEL = "cohere.rerank-v3-5:0"

ROUTES = ("claude", "gemma", "idc_vllm")
ROUTE_ALIASES = {"claude": "claude", "gemma": "gemma",
                 "idc_vllm": "idc_vllm", "onprem-vllm": "idc_vllm", "hybrid_vllm": "idc_vllm", "vllm": "idc_vllm"}
# 게이트가 차단하는 식별자 유형 (common.pii.RULES 의 이름). EMAIL/KR_PASSPORT 는 집계만 한다 (GATE_REFUSE_TYPES 로 조정).
DEFAULT_REFUSE_TYPES = ("KR_RRN", "CARD", "PHONE", "CUSTOMER_TOKEN", "ACCOUNT_TOKEN", "KR_BANK_ACCOUNT")
FIELDS_CAP = 20
_FIELD_LINE = re.compile(r"^\s*[-*•·]?\s*\"?([^:：\"\n]{1,40}?)\"?\s*[:：]\s*(\S.*)$")
_API_DIR = Path(__file__).resolve().parent.parent / "api"
_ENGINE_DIR = str(Path(__file__).resolve().parent)

_lock = threading.Lock()
_adapters: dict = {}
_common_cache: dict = {}
_rt_clients: dict = {}


class GateRefused(Exception):
    """페이로드에 식별자가 있어 경계를 넘기지 않았다. .types / .count / .boundary / .purpose."""

    def __init__(self, types: List[str], count: int, boundary: dict, purpose: str) -> None:
        self.types, self.count, self.boundary, self.purpose = list(types), int(count), boundary, purpose
        super().__init__(f"익명화 게이트 차단: 식별자 {count}건 ({', '.join(types)}) — 페이로드가 모델에 전달되지 않았습니다 [{purpose}]")


class GateUnsupported(Exception):
    """요청한 경로가 이 호출 형태(예: 도구 루프)를 지원하지 않는다."""


# ---------------------------------------------------------------------------
# common.* 적재 — Lambda(api-dist 루트가 sys.path)에서는 바로, 로컬(cli/pytest)에서는 파일 경로로
# ---------------------------------------------------------------------------
def _common(name: str):
    mod = _common_cache.get(name)
    if mod is not None:
        return mod
    try:
        mod = importlib.import_module(f"common.{name}")
    except Exception:  # noqa: BLE001 — 가짜 common 모듈(테스트)·경로 부재 모두 여기로
        mod = None
    if mod is None or not hasattr(mod, "scan_rules" if name == "pii" else "log_event"):
        path = _API_DIR / "common" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"_gate_common_{name}", str(path))
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    _common_cache[name] = mod
    return mod


def _log(event: str, trace_id: str = "", **fields) -> None:
    try:
        _common("log").log_event(event, trace_id or "", **fields)
    except Exception:  # noqa: BLE001 — 로깅 실패가 호출을 막지 않는다
        sys.stdout.write(json.dumps({"event": event, "traceId": trace_id, **{k: str(v)[:80] for k, v in fields.items()}},
                                    ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# 경로 · 어댑터
# ---------------------------------------------------------------------------
def current_route(route: Optional[str] = None) -> str:
    raw = (route or os.environ.get("LLM_ROUTE", "claude") or "claude").strip().lower()
    if raw not in ROUTE_ALIASES:
        raise ValueError(f"알 수 없는 LLM_ROUTE '{raw}' — 허용: {', '.join(sorted(ROUTE_ALIASES))}")
    return ROUTE_ALIASES[raw]


def adapter(route: Optional[str] = None):
    """경로별 어댑터 (프로세스 캐시). 테스트는 set_adapter() 로 페이크를 주입한다."""
    r = current_route(route)
    with _lock:
        ad = _adapters.get(r)
        if ad is None:
            if r == "claude":
                ad = llm.ClaudeAdapter(model_id=os.environ.get("GEN_MODEL", GEN_MODEL), region=REGION)
            elif r == "gemma":
                ad = llm.GemmaAdapter(model_id=os.environ.get("GEMMA_MODEL", GEMMA_MODEL))
            else:
                ad = llm.VllmAdapter()
            _adapters[r] = ad
        return ad


def set_adapter(route: str, ad) -> None:
    """테스트·운영 전환용 주입점. None 이면 캐시를 비운다."""
    r = current_route(route)
    with _lock:
        if ad is None:
            _adapters.pop(r, None)
        else:
            _adapters[r] = ad


def reset_adapters() -> None:
    with _lock:
        _adapters.clear()


def model_id(route: Optional[str] = None) -> str:
    return adapter(route).model_id


def tier_of(route: Optional[str] = None) -> str:
    return adapter(route).tier


# ---------------------------------------------------------------------------
# 계측
# ---------------------------------------------------------------------------
def parse_fields(user: str, cap: int = FIELDS_CAP) -> List[str]:
    """`- 라벨: 값` / `key: value` / `"key": value` 줄의 라벨. 값이 없는 섹션 헤더, URL, 숫자 라벨은 제외. 순서 유지·중복 제거."""
    out: List[str] = []
    for line in (user or "").splitlines():
        m = _FIELD_LINE.match(line)
        if not m:
            continue
        label, value = m.group(1).strip().strip("\"'").strip(), m.group(2)
        if not label or label.isdigit() or "://" in label or label.lower().endswith(("http", "https")) \
                or value.startswith("//") or len(label) > 40:
            continue
        if label not in out:
            out.append(label)
        if len(out) >= cap:
            break
    return out


def refuse_types() -> Tuple[str, ...]:
    raw = os.environ.get("GATE_REFUSE_TYPES", "")
    if raw.strip():
        return tuple(t.strip().upper() for t in raw.split(",") if t.strip())
    return DEFAULT_REFUSE_TYPES


def measure(system: str, user: str) -> dict:
    """페이로드 실측. 반환 {"chars", "estTokens", "fieldsPassed", "piiRules": {"count", "hits", "byType", "refuseTypes"}}.
    hits 에는 유형만 남긴다 (식별자 조각도 기록하지 않는다)."""
    system, user = system or "", user or ""
    text = system + "\n" + user
    hits = _common("pii").scan_rules(text)
    by_type: dict = {}
    for h in hits:
        by_type[h["type"]] = by_type.get(h["type"], 0) + 1
    refuse = refuse_types()
    refused = sorted(t for t in by_type if t in refuse)
    return {"chars": len(text), "estTokens": len(text) // 3, "fieldsPassed": parse_fields(user),
            "piiRules": {"count": len(hits), "hits": [{"type": t, "detector": "rules", "count": n} for t, n in by_type.items()],
                         "byType": by_type, "refuseTypes": refused}}


def check(system: str, user: str, purpose: str, trace_id: str = "", route: Optional[str] = None,
          model: Optional[str] = None) -> dict:
    """계측 → 차단 판정 → 'gate.crossing' 로그. 차단이면 GateRefused (로그 'gate.refused')."""
    b = measure(system, user)
    r = current_route(route)
    m = model or model_id(r)
    pii = b["piiRules"]
    if pii["refuseTypes"]:
        n = sum(pii["byType"][t] for t in pii["refuseTypes"])
        _log("gate.refused", trace_id, purpose=purpose, route=r, modelId=m, chars=b["chars"], estTokens=b["estTokens"],
             piiCount=pii["count"], piiTypes=pii["refuseTypes"])
        raise GateRefused(pii["refuseTypes"], n, b, purpose)
    _log("gate.crossing", trace_id, purpose=purpose, modelId=m, route=r, tier=tier_of(r), chars=b["chars"],
         estTokens=b["estTokens"], fields=len(b["fieldsPassed"]), piiCount=pii["count"])
    return b


def design_deps(route: Optional[str] = None, trace_id: str = "", gen_max_tokens: int = 32000,
                judge_max_tokens: int = 500) -> dict:
    """디자인 스튜디오(design_loop)용 deps — 생성·판정 모두 이 게이트를 지난다(경계 계측 + PII 스캔).
    반환 {generate(system,user,on_token)->str, llm_judge(item,context)->dict, usage()->dict}.
    boundary 는 각 호출의 Stream.__init__ 에서 측정된다 (원문 로그 없음)."""
    acc = {"inputTokens": 0, "outputTokens": 0, "calls": 0}

    def _acc(u: dict) -> None:
        acc["inputTokens"] += int((u or {}).get("inputTokens", 0) or 0)
        acc["outputTokens"] += int((u or {}).get("outputTokens", 0) or 0)
        acc["calls"] += 1

    def _run(system: str, user: str, max_tokens: int, purpose: str) -> str:
        st = stream(system, user, max_tokens=max_tokens, route=route, purpose=purpose, trace_id=trace_id)
        chunks: List[str] = [ch for ch in st]
        _acc(st.usage)
        return "".join(chunks)

    def gen(system: str, user: str, on_token) -> str:
        st = stream(system, user, max_tokens=gen_max_tokens, route=route, purpose="studio.generate", trace_id=trace_id)
        chunks: List[str] = []
        for ch in st:
            chunks.append(ch)
            if on_token:
                on_token(ch)
        _acc(st.usage)
        return "".join(chunks)

    def llm_judge(item: dict, context: dict) -> dict:
        sys_p = ("당신은 은행 UX 디자인 검수자다. 체크리스트 항목 하나를 플로우 텍스트(스텝별 가시 문구)에 대해 판정한다. "
                 "verdict 는 pass|fail|incomplete 중 하나, evidence 는 한국어 한두 문장(플로우의 실제 문구 인용). "
                 "출력은 JSON 하나만: {\"verdict\": \"pass|fail|incomplete\", \"evidence\": \"...\"}")
        prd = context.get("prd") or {}
        steps = ", ".join(s.get("id", "") for s in prd.get("steps") or [])
        user_p = ("### 항목\n[" + str(item.get("id")) + "] " + str(item.get("text")) +
                  " (대상: " + str(item.get("target")) + ")\n\n### PRD 스텝 순서\n" + steps +
                  "\n\n### 플로우 텍스트\n" + str(context.get("flowText", ""))[:6000])
        text = _run(sys_p, user_p, judge_max_tokens, "studio.review")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {"verdict": "incomplete", "evidence": f"판정 JSON 없음: {text[:120]}"}
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return {"verdict": "incomplete", "evidence": f"판정 JSON 오류: {text[:120]}"}
        v = str(obj.get("verdict", "")).lower()
        return {"verdict": v if v in ("pass", "fail", "incomplete") else "incomplete",
                "evidence": str(obj.get("evidence", ""))[:600]}

    return {"generate": gen, "llm_judge": llm_judge, "usage": lambda: dict(acc)}


def infer_purpose(default: str = "generic") -> str:
    """호출자 모듈에서 목적 라벨 추정 (예: handlers/s2.py → 's2', screengen/agent.py → 'screengen.agent'). 로그 라벨용."""
    try:
        f = sys._getframe(1)
        while f is not None:
            fn = f.f_code.co_filename
            if not fn.startswith(_ENGINE_DIR) and "importlib" not in fn and "<" not in fn:
                p = Path(fn)
                parent = p.parent.name
                return f"{parent}.{p.stem}" if parent and parent not in ("api", "handlers") else p.stem
            f = f.f_back
    except Exception:  # noqa: BLE001
        pass
    return default


# ---------------------------------------------------------------------------
# 생성
# ---------------------------------------------------------------------------
class Stream:
    """게이트를 지난 토큰 스트림. 생성 시점에 계측·차단 판정(GateRefused)이 일어나고, 반복이 끝나면 .usage 가 실측값으로 채워진다.
    속성: model_id, route, tier, boundary, usage, stop_reason, purpose, trace_id."""

    def __init__(self, system: str, user: str, max_tokens: int = 800, route: Optional[str] = None,
                 purpose: Optional[str] = None, trace_id: str = "", temperature: float = 0.2) -> None:
        self.route = current_route(route)
        ad = adapter(self.route)
        self.model_id = ad.model_id
        self.tier = ad.tier
        self.endpoint = getattr(ad, "endpoint", "")
        self.purpose = purpose or infer_purpose()
        self.trace_id = trace_id
        self.boundary = check(system, user, self.purpose, trace_id, self.route, self.model_id)
        self._inner = ad.stream(system, user, max_tokens=max_tokens, temperature=temperature)
        self.usage: dict = {}
        self.stop_reason: str = ""
        self.non_stream = bool(getattr(self._inner, "non_stream", False))

    def __iter__(self):
        for chunk in self._inner:
            yield chunk
        self.usage = llm.normalize_usage(getattr(self._inner, "usage", {}))
        self.stop_reason = getattr(self._inner, "stop_reason", "") or ""

    @property
    def tokens_in(self) -> int:
        return int(self.usage.get("inputTokens", 0))

    @property
    def tokens_out(self) -> int:
        return int(self.usage.get("outputTokens", 0))

    def info(self) -> dict:
        return {"modelId": self.model_id, "route": self.route, "tier": self.tier, "endpoint": self.endpoint,
                "boundary": self.boundary, "usage": self.usage, "purpose": self.purpose, "nonStream": self.non_stream}


def stream(system: str, user: str, max_tokens: int = 800, route: Optional[str] = None, purpose: str = "s2",
           trace_id: str = "", temperature: float = 0.2) -> Stream:
    return Stream(system, user, max_tokens=max_tokens, route=route, purpose=purpose, trace_id=trace_id,
                  temperature=temperature)


def generate(system: str, user: str, max_tokens: int = 300, route: Optional[str] = None, purpose: str = "s1.decompose",
             trace_id: str = "", temperature: float = 0.2) -> Tuple[str, dict, dict]:
    """단발 생성. 반환 (text, usage, info) — info: modelId/route/tier/endpoint/boundary."""
    r = current_route(route)
    ad = adapter(r)
    boundary = check(system, user, purpose, trace_id, r, ad.model_id)
    text, usage = ad.generate(system, user, max_tokens=max_tokens, temperature=temperature)
    usage = llm.normalize_usage(usage)
    return text, usage, {"modelId": ad.model_id, "route": r, "tier": ad.tier, "endpoint": getattr(ad, "endpoint", ""),
                         "boundary": boundary, "usage": usage, "purpose": purpose}


# ---------------------------------------------------------------------------
# 도구 루프 (Reader) — Converse 호환 표면
# ---------------------------------------------------------------------------
def _messages_text(messages) -> str:
    """Converse messages 의 텍스트(본문·toolResult 텍스트·toolUse 입력)를 계측용으로 이어붙인다."""
    parts: List[str] = []
    for m in messages or []:
        for b in (m or {}).get("content", []) or []:
            if not isinstance(b, dict):
                continue
            if "text" in b:
                parts.append(str(b["text"]))
            tr = b.get("toolResult")
            if isinstance(tr, dict):
                for c in tr.get("content", []) or []:
                    if isinstance(c, dict) and "text" in c:
                        parts.append(str(c["text"]))
                    elif isinstance(c, dict) and "json" in c:
                        parts.append(json.dumps(c["json"], ensure_ascii=False))
            tu = b.get("toolUse")
            if isinstance(tu, dict) and tu.get("input") is not None:
                parts.append(json.dumps(tu["input"], ensure_ascii=False))
    return "\n".join(parts)


class ToolClient:
    """Reader 도구 루프용 게이트 클라이언트. boto3 converse(**kw) 와 같은 인자를 받아 계측·차단 후
    ClaudeAdapter.converse_with_tools 로 보내고 Converse 원형 응답을 돌려준다. 도구 루프는 Claude 경로 전용(Tier 0/1) —
    LLM_ROUTE 가 다른 값이어도 여기서는 claude 로 고정하고 routeForced 로 표기한다."""

    def __init__(self, purpose: str = "tools", trace_id: str = "") -> None:
        self.purpose = purpose
        self.trace_id = trace_id
        self.route = "claude"
        self.route_forced = current_route() != "claude"
        self.crossings: List[dict] = []

    def converse(self, **kw) -> dict:
        ad = adapter("claude")
        if not hasattr(ad, "converse_with_tools"):
            raise GateUnsupported("도구 루프는 Claude 경로(bedrock-runtime Converse)에서만 지원됩니다")
        system_text = "".join(str(b.get("text", "")) for b in (kw.get("system") or []) if isinstance(b, dict))
        messages = kw.get("messages") or []
        m = kw.get("modelId") or ad.model_id
        boundary = check(system_text, _messages_text(messages), self.purpose, self.trace_id, "claude", m)
        inf = kw.get("inferenceConfig") or {}
        r = ad.converse_with_tools(system_text, messages, kw.get("toolConfig"), model=m,
                                   max_tokens=int(inf.get("maxTokens", 1800)), temperature=float(inf.get("temperature", 0.1)))
        self.crossings.append({**boundary, "usage": llm.normalize_usage(r.get("usage", {})), "modelId": m})
        return r

    def summary(self) -> dict:
        fields: List[str] = []
        for c in self.crossings:
            for f in c.get("fieldsPassed", []):
                if f not in fields:
                    fields.append(f)
        return {"crossings": len(self.crossings), "chars": sum(c["chars"] for c in self.crossings),
                "estTokens": sum(c["estTokens"] for c in self.crossings), "fieldsPassed": fields[:FIELDS_CAP],
                "piiCount": sum(c["piiRules"]["count"] for c in self.crossings),
                "modelId": (self.crossings[-1]["modelId"] if self.crossings else adapter("claude").model_id),
                "route": self.route, "tier": adapter("claude").tier, "routeForced": self.route_forced}


# ---------------------------------------------------------------------------
# 임베딩 · 리랭크 — 같은 계측을 지난다 (§12.3: 문서·질의 텍스트만)
# ---------------------------------------------------------------------------
def _rt(region: str):
    with _lock:
        c = _rt_clients.get(region)
        if c is None:
            import boto3
            c = boto3.client("bedrock-runtime", region_name=region)
            _rt_clients[region] = c
        return c


def embed(texts: List[str], trace_id: str = "", purpose: str = "embed") -> List[List[float]]:
    """Titan v2 임베딩 (1024차원). 문서·질의 공용. 개인데이터 식별자가 있으면 GateRefused."""
    joined = "\n".join(t[:8000] for t in texts)
    b = measure("", joined)
    if b["piiRules"]["refuseTypes"]:
        _log("gate.refused", trace_id, purpose=purpose, route="embed", modelId=EMBED_MODEL, chars=b["chars"],
             piiCount=b["piiRules"]["count"], piiTypes=b["piiRules"]["refuseTypes"])
        raise GateRefused(b["piiRules"]["refuseTypes"], sum(b["piiRules"]["byType"][t] for t in b["piiRules"]["refuseTypes"]),
                          b, purpose)
    _log("gate.crossing", trace_id, purpose=purpose, modelId=EMBED_MODEL, route="embed", tier="0/1", chars=b["chars"],
         estTokens=b["estTokens"], fields=0, piiCount=b["piiRules"]["count"], items=len(texts))
    rt = _rt(REGION)
    out = []
    for t in texts:
        r = rt.invoke_model(modelId=EMBED_MODEL,
                            body=json.dumps({"inputText": t[:8000], "dimensions": 1024, "normalize": True}))
        out.append(json.loads(r["body"].read())["embedding"])
    return out


def rerank(query: str, docs: List[str], top_n: int = 5, trace_id: str = "", purpose: str = "rerank") -> List[Tuple[int, float]]:
    """Cohere Rerank v3.5 (도쿄) — (원본 인덱스, 점수) 상위 top_n."""
    b = measure("", query + "\n" + "\n".join(docs))
    if b["piiRules"]["refuseTypes"]:
        _log("gate.refused", trace_id, purpose=purpose, route="rerank", modelId=RERANK_MODEL, chars=b["chars"],
             piiCount=b["piiRules"]["count"], piiTypes=b["piiRules"]["refuseTypes"])
        raise GateRefused(b["piiRules"]["refuseTypes"], sum(b["piiRules"]["byType"][t] for t in b["piiRules"]["refuseTypes"]),
                          b, purpose)
    _log("gate.crossing", trace_id, purpose=purpose, modelId=RERANK_MODEL, route="rerank", tier="0/1", chars=b["chars"],
         estTokens=b["estTokens"], fields=0, piiCount=b["piiRules"]["count"], items=len(docs), region=RERANK_REGION)
    r = _rt(RERANK_REGION).invoke_model(modelId=RERANK_MODEL,
                                        body=json.dumps({"api_version": 2, "query": query, "documents": docs, "top_n": top_n}))
    results = json.loads(r["body"].read())["results"]
    return [(x["index"], x["relevance_score"]) for x in results]


# ---------------------------------------------------------------------------
# 표기 (§8-3 · §11) — 화면 배지 문구의 단일 출처
# ---------------------------------------------------------------------------
def route_info(route: Optional[str] = None) -> dict:
    r = current_route(route)
    ad = adapter(r)
    base = {"route": r, "tier": ad.tier, "modelId": ad.model_id, "endpoint": getattr(ad, "endpoint", ""),
            "region": getattr(ad, "region", ""), "storage": "ap-northeast-2", "storageLabel": "서울 리전"}
    if r == "claude":
        base.update(inferenceRouting="global", inferenceRoutingLabel="global (전 세계 상용 리전)",
                    badge={"title": "LLM 생성 경로 — Tier 0/1",
                           "prod": "Bedrock Claude (global 프로파일) · bedrock-runtime Converse · 소스 리전 ap-northeast-2",
                           "demo": "운영과 동일 — Bedrock Claude (global 프로파일)",
                           "region": "저장: 서울 리전 / 추론: global 라우팅",
                           "substituted": False})
    elif r == "gemma":
        base.update(inferenceRouting="us-west-2 direct", inferenceRoutingLabel="us-west-2 직접 호출 (교차 리전 추론 미지원)",
                    badge={"title": "PII 추론 경로 — Tier 2",
                           "prod": "IDC GPU + vLLM (EKS Hybrid Nodes)",
                           "demo": "Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체",
                           "region": "저장: 서울 리전 / 추론: us-west-2 직접 호출",
                           "substituted": True})
    else:
        base.update(inferenceRouting="idc", inferenceRoutingLabel="IDC 내부 (EKS Hybrid Nodes)",
                    badge={"title": "PII 추론 경로 — Tier 2",
                           "prod": "IDC GPU + vLLM (EKS Hybrid Nodes)",
                           "demo": "idc_vllm 어댑터 미구성 — 데모에서는 LLM_ROUTE=gemma 대체 경로를 사용",
                           "region": "저장: 서울 리전 / 추론: IDC",
                           "substituted": True, "implemented": False})
    return base


def gate_info() -> dict:
    """§11-2 배지 — §16 확인 결과대로: 규칙 기반 토큰화는 플레인에 있고, ML 가명처리·재식별 볼트는 미구현."""
    return {"title": "익명화 게이트",
            "prod": "가명처리 · 토큰화 · 재식별",
            "demo": "합성데이터 가명 생성 + 규칙 기반 토큰화 (ML 가명처리·재식별 볼트 미구현)",
            "refuseTypes": list(refuse_types()),
            "detector": "api/common/pii.py scan_rules (플레인 마스킹과 독립)",
            "measured": ["chars", "estTokens", "fieldsPassed", "piiRules"]}


def badges(route: Optional[str] = None) -> dict:
    return {"llm": route_info(route)["badge"], "gate": gate_info(), "route": route_info(route)}


def health(route: Optional[str] = None) -> dict:
    """어댑터 헬스 — gemma/idc_vllm 은 어댑터의 health(), claude 는 호출 없이 프로파일 정보만 (실호출로 확인)."""
    r = current_route(route)
    ad = adapter(r)
    if hasattr(ad, "health"):
        out = ad.health()
    else:
        out = {"ok": None, "model": ad.model_id, "note": "헬스체크 미구현 — 실제 생성 호출로 확인"}
    out.update(route=r, tier=ad.tier, checkedAt=int(time.time() * 1000))
    return out
