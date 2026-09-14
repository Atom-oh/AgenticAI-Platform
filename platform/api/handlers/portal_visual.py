"""Bounded, read-only diagram projections of data already fetched for Portal detail.

No graph lookups, renderer DSL, package substitutions, or validation claims belong
here. ``missing`` also covers unresolved references, not just confirmed absence.
Truncation counts describe omitted items within the fetched source data.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from handlers.component_sources import binding, implementation_status

MAX_NODES = 25
MAX_EDGES = 50
MAX_LABEL = 80
MAX_TITLE = 120
MAX_NOTE = 600
MAX_ASSET_ID = 64  # portal_detail accepts at most 64 characters.


def _text(value, limit: int, fallback: str = "") -> str:
    if not isinstance(value, str) or not value.strip():
        value = fallback
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _name(node) -> str:
    for key in ("name", "title", "term", "purpose"):
        value = node.props.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return node.id


def _asset_key(asset_id: str) -> str:
    # Never truncate identifiers into collisions or expose long IDs to renderers.
    return "asset-" + sha256(asset_id.encode("utf-8")).hexdigest()[:24]


def _empty(reason: str, note: str) -> dict:
    return {"kind": "empty", "reason": _text(reason, MAX_TITLE), "note": _text(note, MAX_NOTE)}


class _Diagram:
    def __init__(self, source: str, title: str, note: str):
        self.source = source
        self.title = _text(title, MAX_TITLE)
        self.notes = [note]
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.seen_nodes: set[str] = set()
        self.seen_edges: set[tuple] = set()
        self.extra_nodes = 0
        self.extra_edges = 0
        self.shortened_text = len(title) > MAX_TITLE

    def node(self, key: str, label: str, node_type: str, asset_id: str | None = None,
             missing: bool = False) -> str:
        if key in self.seen_nodes:
            return key
        self.seen_nodes.add(key)
        if len(self.nodes) >= MAX_NODES:
            return key
        item = {"id": key, "label": _text(label, MAX_LABEL), "type": _text(node_type, 32)}
        if isinstance(asset_id, str) and asset_id:
            if len(asset_id) <= MAX_ASSET_ID and asset_id == asset_id.strip():
                item["assetId"] = asset_id
            else:
                missing = True
        if missing:
            item["missing"] = True
        self.shortened_text |= len(label) > MAX_LABEL
        self.nodes[key] = item
        return key

    def asset(self, node) -> str:
        name = _name(node)
        return self.node(_asset_key(node.id), name, node.label, node.id, missing=name == node.id)

    def edge(self, src: str, dst: str, label: str, kind: str) -> None:
        key = (src, dst, label, kind)
        if key in self.seen_edges:
            return
        self.seen_edges.add(key)
        if src in self.nodes and dst in self.nodes and len(self.edges) < MAX_EDGES:
            self.edges.append({"from": src, "to": dst, "label": label, "kind": kind})

    def finish(self, *, neighbors_complete: bool = True) -> dict:
        truncated = {
            "nodes": len(self.seen_nodes) - len(self.nodes) + self.extra_nodes,
            "edges": len(self.seen_edges) - len(self.edges) + self.extra_edges,
        }
        if any(n.get("missing") for n in self.nodes.values()):
            self.notes.append("미확인 표시는 이름·자산 연결을 확인하지 못한 항목이며, 자산이 없다는 확정이 아닙니다.")
        if any(truncated.values()):
            self.notes.append(f"조회된 데이터 중 노드 {truncated['nodes']}개·연결 {truncated['edges']}개를 생략했습니다.")
        if not neighbors_complete:
            self.notes.append("이웃 조회 한도에 도달했습니다. 생략 수는 조회 범위 기준이며 추가 관계 수는 미확인입니다.")
        if self.shortened_text:
            self.notes.append("긴 텍스트는 줄여 표시합니다.")
        return {
            "kind": "diagram", "source": self.source, "title": self.title,
            "nodes": list(self.nodes.values()), "edges": self.edges,
            "note": _text(" ".join(self.notes), MAX_NOTE), "truncated": truncated,
        }


def _procedure(node, props: dict, neighborhood: dict, neighbors_complete: bool) -> dict:
    steps = props.get("steps")
    if not isinstance(steps, list) or not steps:
        return _empty("순서가 기록된 절차 단계가 없습니다.",
                      "절차에 단계 순서를 등록해 주세요. 연결된 화면 목록만으로 순서를 추정하지 않습니다.")
    if not any(isinstance(step, str) and step.strip() for step in steps[:MAX_NODES]):
        return _empty("표시할 수 있는 절차 단계가 없습니다.",
                      "미리보기 범위의 단계 이름이 비어 있거나 지원하지 않는 형식입니다.")
    by_name: dict[str, set[str]] = defaultdict(set)
    if neighbors_complete:
        for edge, screen in neighborhood["out"]:
            name = screen.props.get("name")
            if edge.rel == "INCLUDES" and screen.label == "Screen" and isinstance(name, str):
                by_name[name].add(screen.id)
    diagram = _Diagram("procedure-steps", _name(node),
                       "등록된 단계 순서입니다. 연결된 화면 중 이름이 하나의 자산과 일치하는 단계는 해당 화면을 열 수 있습니다.")
    previous = None
    for index, step in enumerate(steps[:MAX_NODES]):
        valid = isinstance(step, str) and bool(step.strip())
        matches = by_name.get(step, set()) if valid else set()
        asset_id = next(iter(matches)) if len(matches) == 1 else None
        current = diagram.node(
            f"step-{index + 1}", step if valid else f"{index + 1}단계 내용 미확인",
            "Screen" if asset_id else "Step", asset_id, missing=not asset_id,
        )
        if previous is not None:
            diagram.edge(previous, current, "다음 단계", "sequence")
        previous = current
    diagram.extra_nodes = max(0, len(steps) - MAX_NODES)
    diagram.extra_edges = max(0, len(steps) - MAX_NODES)
    return diagram.finish(neighbors_complete=neighbors_complete)


def _screen(node, meta: dict | None, neighborhood: dict, reference_nodes: dict | None = None) -> dict:
    if meta is None:
        return _empty("화면 이동 메타데이터가 없습니다.",
                      "이전·다음 화면 연결을 등록하면 이동 관계를 확인할 수 있습니다.")
    diagram = _Diagram("screen-navigation", _name(node),
                       "등록된 이전·다음 화면입니다. 기록되지 않은 진입 조건이나 성공·실패 분기는 추정하지 않습니다.")
    current = diagram.asset(node)
    known = {**(reference_nodes or {}), node.id: node}
    for rows in neighborhood.values():
        known.update({other.id: other for _, other in rows if other.label == "Screen"})
    invalid = False
    for field, label in (("prevScreens", "이전 화면"), ("nextScreens", "다음 화면")):
        refs = meta.get(field, [])
        if not isinstance(refs, list):
            invalid = True
            continue
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                invalid = True
                continue
            target = (diagram.asset(known[ref]) if ref in known else
                      diagram.node(_asset_key(ref), ref, "Screen", ref, missing=True))
            src, dst = (target, current) if field == "prevScreens" else (current, target)
            diagram.edge(src, dst, label, "sequence")
    if invalid:
        diagram.notes.append("형식이 올바르지 않은 화면 참조는 연결로 표시하지 않았습니다.")
    if not diagram.seen_edges:
        diagram.notes.append("등록된 이전·다음 화면 연결이 없습니다.")
    return diagram.finish()


_RELATIONS = {
    "Pattern": {
        ("out", "COMPOSES", "Component"): "구성 요소",
        ("in", "FOLLOWS", "Screen"): "패턴 참조",
    },
    "PolicyRule": {
        ("out", "CONSTRAINS", "Screen"): "화면 제약",
        ("out", "DERIVED_FROM", "Regulation"): "근거 규정",
    },
}


def _relations(node, neighborhood: dict, neighbors_complete: bool) -> dict:
    source = "pattern-composition" if node.label == "Pattern" else "policy-relations"
    diagram = _Diagram(source, _name(node),
                       "저장된 구성·참조 관계입니다. 화면 이동 순서나 정책 검증·승인 결과를 뜻하지 않습니다.")
    diagram.asset(node)
    for direction, rows in neighborhood.items():
        for edge, other in rows:
            label = _RELATIONS[node.label].get((direction, edge.rel, other.label))
            if label:
                diagram.asset(other)
                diagram.edge(_asset_key(edge.src), _asset_key(edge.dst), label, "reference")
    if not diagram.seen_edges:
        return _empty("표시할 구성·참조 관계가 없습니다.",
                      "조회된 범위에 지원되는 관계가 없습니다. 저장되지 않은 흐름이나 정책 판정은 만들지 않습니다.")
    return diagram.finish(neighbors_complete=neighbors_complete)


def build_visual(node, props: dict, neighborhood: dict, screen_meta: dict | None,
                 *, neighbors_complete: bool = True, reference_nodes: dict | None = None) -> dict:
    """Project an asset without reading or mutating a backend."""
    if node.label == "Procedure":
        return _procedure(node, props, neighborhood, neighbors_complete)
    if node.label == "Screen":
        return _screen(node, screen_meta, neighborhood, reference_nodes)
    if node.label in _RELATIONS:
        return _relations(node, neighborhood, neighbors_complete)
    if node.label == "Component":
        implementation = binding(node.id, props)
        if implementation:
            return implementation
        if implementation_status(node.id, props) == "placeholder":
            return _empty("볼륨 테스트용 더미 컴포넌트입니다.",
                          "그래프 규모 검증을 위한 메타데이터입니다. React 구현이나 배포 패키지가 없습니다.")
        return _empty("실제 React 컴포넌트 코드가 연결되지 않았습니다.",
                      "기존 @atom/ui 메타데이터는 실제 패키지 구현이 아닙니다. 다른 컴포넌트 패키지로 자동 대체하지 않습니다.")
    return _empty("이 자산 유형의 시각 미리보기는 지원하지 않습니다.",
                  "저장된 메타데이터를 확인하세요. 근거 없는 다이어그램은 만들지 않습니다.")
