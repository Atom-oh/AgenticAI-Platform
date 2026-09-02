"""Semantic Layer 로더 (SPEC §4.4).

metrics.yaml을 파싱해 한국어 별칭 → 지표 정의를 해석한다.
Text-to-SQL 파이프라인(F3)은 반드시 이 계층을 통해서만 SQL 템플릿을 얻는다 —
LLM이 지표 계산식을 임의로 만들지 못하게 하는 것이 목적이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:  # Lambda 런타임 — metrics.json 변환본을 사용한다
    yaml = None

_YAML = Path(__file__).resolve().parent / "metrics.yaml"


@dataclass
class Metric:
    name: str
    korean_aliases: list[str]
    description: str
    sql_template: str
    unit: str
    owner_dept: str


@dataclass
class Dimension:
    name: str
    korean_aliases: list[str]
    values: list[str]


class SemanticLayer:
    def __init__(self, path: Path = _YAML) -> None:
        json_path = path.with_suffix(".json")  # Lambda에는 PyYAML이 없어 배포 시 변환본 사용
        if json_path.exists():
            import json
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.metrics = [Metric(**m) for m in raw["metrics"]]
        self.dimensions = [Dimension(**d) for d in raw["dimensions"]]
        self._alias_index: dict[str, Metric] = {}
        for m in self.metrics:
            self._alias_index[m.name] = m
            for a in m.korean_aliases:
                self._alias_index[a] = m

    def resolve(self, term: str) -> Metric | None:
        """한국어 용어를 지표 정의로 해석한다. 부분 일치까지 허용."""
        t = term.strip()
        if t in self._alias_index:
            return self._alias_index[t]
        for alias, m in self._alias_index.items():
            if alias.replace(" ", "") in t.replace(" ", ""):
                return m
        return None
