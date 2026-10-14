"""플랫폼에서 허용한 Bedrock 추론 프로파일. 호출 가능 여부는 실제 오류로 보고한다."""
from __future__ import annotations

import os

# 서울 리전의 활성 foundation model / inference profile 목록과 대조(2026-09-10, 5.5·GPT-6.1/Luna 2026-10-14 Converse 실호출 확인).
# 새 공급자나 임의 엔드포인트를 클라이언트 입력으로 허용하지 않는다. 첫 항목이 기본 모델이다.
# infra/lib/workspace.ts · ontology-stack.ts 의 IAM/배포 허용 목록도 이 목록과 일치해야 한다(test_model_catalog_parity).
_MODELS = (
    ("global.anthropic.claude-sonnet-5", "Claude Sonnet 5", "Anthropic"),
    ("global.anthropic.claude-sonnet-5-5", "Claude Sonnet 5.5", "Anthropic"),
    ("global.anthropic.claude-opus-5-5", "Claude Opus 5.5", "Anthropic"),
    ("global.anthropic.claude-opus-5", "Claude Opus 5", "Anthropic"),
    ("global.anthropic.claude-haiku-5-5", "Claude Haiku 5.5", "Anthropic"),
    ("global.openai.gpt-6.1-sol", "GPT-6.1 Sol", "OpenAI"),
    ("global.openai.gpt-6-luna", "GPT-6 Luna", "OpenAI"),
    ("global.openai.gpt-6-astra", "GPT-6 Astra", "OpenAI"),
    ("global.anthropic.claude-fable-5-1", "Claude Fable 5.1", "Anthropic"),
    ("global.anthropic.claude-fable-5", "Claude Fable 5", "Anthropic"),
)
MODEL_IDS = tuple(row[0] for row in _MODELS)
DEFAULT_MODEL = MODEL_IDS[0]


def resolve(value: str | None = None) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError("AI 모델은 허용 목록에서 선택하세요.")
    model = (value or "").strip() or os.environ.get("GEN_MODEL", DEFAULT_MODEL)
    if model not in MODEL_IDS:
        raise ValueError("지원하지 않는 AI 모델입니다. 허용된 Bedrock 모델을 다시 선택하세요.")
    return model


def options() -> list[dict]:
    return [{"id": model, "label": label, "provider": provider} for model, label, provider in _MODELS]
