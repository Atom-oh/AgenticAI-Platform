"""Bedrock 클라이언트 래퍼 — 생성(Claude)·임베딩(Titan v2)·리랭크(Cohere, 도쿄).

경계 통과 지점: 이 모듈을 지나는 모든 페이로드가 클라우드로 나간다.
F6 계측: Stream.usage 가 converse_stream metadata 의 실측 토큰 수를 제공한다 (추정치 아님).
"""
from __future__ import annotations

import json
import os

import boto3

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
RERANK_REGION = "ap-northeast-1"  # rerank 모델은 서울 미제공 — 도쿄 사용(합성 문서만 전달)
GEN_MODEL = os.environ.get("GEN_MODEL", "apac.anthropic.claude-sonnet-4-20250514-v1:0")
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
RERANK_MODEL = "cohere.rerank-v3-5:0"

_rt = boto3.client("bedrock-runtime", region_name=REGION)
_rt_rerank = boto3.client("bedrock-runtime", region_name=RERANK_REGION)


def generate(system: str, user: str, max_tokens: int = 1500) -> tuple[str, dict]:
    """단발 생성. 반환: (text, usage)."""
    r = _rt.converse(
        modelId=GEN_MODEL,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
    )
    text = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
    return text, r.get("usage", {})


class Stream:
    """converse_stream 래퍼 — 토큰을 yield 하고, 끝나면 .usage(실측 토큰 수)가 채워진다."""

    def __init__(self, system: str, user: str, max_tokens: int = 1500) -> None:
        self._r = _rt.converse_stream(
            modelId=GEN_MODEL,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
        )
        self.usage: dict = {}
        self.stop_reason: str = ""

    def __iter__(self):
        for ev in self._r["stream"]:
            delta = ev.get("contentBlockDelta", {}).get("delta", {}).get("text")
            if delta:
                yield delta
            if "messageStop" in ev:
                self.stop_reason = ev["messageStop"].get("stopReason", "")
            if "metadata" in ev:
                self.usage = ev["metadata"].get("usage", {})

    @property
    def tokens_in(self) -> int:
        return int(self.usage.get("inputTokens", 0))

    @property
    def tokens_out(self) -> int:
        return int(self.usage.get("outputTokens", 0))


def generate_stream(system: str, user: str, max_tokens: int = 1500):
    """토큰 스트리밍 생성 — 청크 문자열을 yield한다 (SPEC §10). 실측 usage가 필요하면 Stream을 쓴다."""
    yield from Stream(system, user, max_tokens)


def embed(texts: list[str]) -> list[list[float]]:
    """Titan v2 임베딩 (1024차원). 문서·질의 공용."""
    out = []
    for t in texts:
        r = _rt.invoke_model(
            modelId=EMBED_MODEL,
            body=json.dumps({"inputText": t[:8000], "dimensions": 1024,
                             "normalize": True}),
        )
        out.append(json.loads(r["body"].read())["embedding"])
    return out


def rerank(query: str, docs: list[str], top_n: int = 5) -> list[tuple[int, float]]:
    """Cohere Rerank v3.5 — (원본 인덱스, 점수) 상위 top_n."""
    r = _rt_rerank.invoke_model(
        modelId=RERANK_MODEL,
        body=json.dumps({"api_version": 2, "query": query,
                         "documents": docs, "top_n": top_n}),
    )
    results = json.loads(r["body"].read())["results"]
    return [(x["index"], x["relevance_score"]) for x in results]
