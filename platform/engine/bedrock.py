"""Bedrock 래퍼 — **호환 표면**. 실제 호출은 전부 engine.gate(익명화 게이트)를 지난다 (SPEC v2 §3-2, §12.1).

기존 호출자(graphrag·vectorrag·s1·s2·screengen·registry·report)가 쓰던 시그니처를 그대로 유지한다:
  generate(system, user, max_tokens) → (text, usage)
  Stream(system, user, max_tokens)   → 반복 후 .usage(실측) + 새 속성 .model_id/.route/.tier/.boundary
  generate_stream / embed / rerank
경로(claude|gemma|idc_vllm)는 env LLM_ROUTE 또는 호출별 route= 로 정한다. 이 모듈에는 boto3 클라이언트가 없다.
"""
from __future__ import annotations

import os

from engine import gate

REGION = gate.REGION
RERANK_REGION = gate.RERANK_REGION
GEN_MODEL = os.environ.get("GEN_MODEL", gate.GEN_MODEL)
EMBED_MODEL = gate.EMBED_MODEL
RERANK_MODEL = gate.RERANK_MODEL
GateRefused = gate.GateRefused


def generate(system: str, user: str, max_tokens: int = 1500, **kw) -> tuple[str, dict]:
    """단발 생성 (게이트 경유). 반환: (text, usage). route=/purpose=/trace_id= 를 넘길 수 있다."""
    kw.setdefault("purpose", gate.infer_purpose())
    text, usage, _info = gate.generate(system, user, max_tokens=max_tokens, **kw)
    return text, usage


class Stream(gate.Stream):
    """게이트 스트림 — 토큰을 yield 하고, 끝나면 .usage(실측 토큰 수)가 채워진다. .model_id/.route/.tier/.boundary 포함."""

    def __init__(self, system: str, user: str, max_tokens: int = 1500, **kw) -> None:
        kw.setdefault("purpose", gate.infer_purpose())
        super().__init__(system, user, max_tokens=max_tokens, **kw)


def generate_stream(system: str, user: str, max_tokens: int = 1500, **kw):
    """토큰 스트리밍 생성 — 청크 문자열을 yield한다. 실측 usage 가 필요하면 Stream 을 쓴다."""
    yield from Stream(system, user, max_tokens, **kw)


def embed(texts: list[str]) -> list[list[float]]:
    """Titan v2 임베딩 (1024차원) — 게이트 계측 경유."""
    return gate.embed(texts, purpose=gate.infer_purpose("embed"))


def rerank(query: str, docs: list[str], top_n: int = 5) -> list[tuple[int, float]]:
    """Cohere Rerank v3.5 — (원본 인덱스, 점수) 상위 top_n — 게이트 계측 경유."""
    return gate.rerank(query, docs, top_n, purpose=gate.infer_purpose("rerank"))
