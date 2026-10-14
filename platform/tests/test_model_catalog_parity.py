"""IaC·런타임의 Bedrock 모델 목록과 기본 모델이 카탈로그(engine/model_catalog.py)와 어긋나지 않는지 검증.

카탈로그에만 추가하면 UI에는 보이지만 워커 IAM 이 호출을 거부하고, 온톨로지 설계 배포는
해당 모델을 '지원하지 않는 프로파일'로 막는다. 기본 모델 상수가 흩어지면 표시 값과 실제 호출이 갈린다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from engine import model_catalog

INFRA = ROOT / "infra" / "lib"


def _models_ts() -> str:
    return (INFRA / "models.ts").read_text()


def test_infra_model_list_matches_catalog():
    source = _models_ts()
    block = source[source.index("CATALOG_MODEL_IDS = ["):]
    ids = re.findall(r"'([^']+)'", block[:block.index("] as const")])
    assert ids == list(model_catalog.MODEL_IDS)


def test_infra_fixed_models_match_gate_constants(monkeypatch):
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    from engine import gate, llm
    source = _models_ts()
    line = next(l for l in source.splitlines() if "FIXED_FOUNDATION_MODELS =" in l)
    assert re.findall(r"'([^']+)'", line) == [gate.EMBED_MODEL, gate.RERANK_MODEL, llm.DEFAULT_GEMMA_MODEL]


def test_stacks_use_the_shared_list_instead_of_wildcards_or_literals():
    for name in ("stack.ts", "plane-stack.ts", "workspace.ts", "ontology-stack.ts"):
        text = (INFRA / name).read_text()
        assert "from './models'" in text, name
        assert "foundation-model/*" not in text and "inference-profile/*" not in text, name
        assert not re.search(r"'global\.(anthropic|openai)\.", text), name


def test_catalog_ids_are_unique_global_profiles():
    assert len(set(model_catalog.MODEL_IDS)) == len(model_catalog.MODEL_IDS)
    assert all(re.fullmatch(r"global\.(anthropic|openai)\.[a-z0-9.-]+", m) for m in model_catalog.MODEL_IDS)


def test_runtime_default_constants_follow_the_catalog(monkeypatch):
    monkeypatch.delenv("GEN_MODEL", raising=False)
    from agentcore import agent_specs
    from engine import llm
    assert llm.DEFAULT_CLAUDE_MODEL == model_catalog.DEFAULT_MODEL
    assert agent_specs.DEFAULT_MODEL == model_catalog.DEFAULT_MODEL
    assert agent_specs.QUALITY_MODEL in model_catalog.MODEL_IDS
    for rel in ("api/handlers/design.py", "studio/loop.py", "screengen/agent.py", "registry/seed.py",
                "agentcore/harness.py", "web/src/S2.tsx"):
        text = (ROOT / rel).read_text()
        assert not re.search(r"(global|apac)\.anthropic\.claude-[a-z0-9.:-]+", text), rel
