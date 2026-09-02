"""GraphStore 인터페이스와 LocalGraphStore 구현 (SPEC §11).

두 구현을 강제한다:
- LocalGraphStore: 인메모리, 개발·테스트·리허설용 (비용 0)
- NeptuneGraphStore: 고객 시연용 (Phase 2에서 구현, openCypher HTTPS 엔드포인트)

환경변수 GRAPH_BACKEND=neptune|local 로 전환하며, UI는 항상 현재 백엔드를 표시한다.
로컬 구현으로 시연하면서 Neptune이라고 말하는 것은 허용되지 않는다.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Node:
    id: str
    label: str
    props: dict = field(default_factory=dict)


@dataclass
class Edge:
    src: str
    rel: str
    dst: str
    props: dict = field(default_factory=dict)


@dataclass
class ImpactResult:
    """S1 규정 영향 분석(§4.3) 결과 — 순회 경로를 함께 반환한다."""
    regulation: Node | None
    conditions: list[Node]
    products: list[Node]
    screens: list[Node]
    components: list[Node]
    departments: list[Node]
    documents: list[Node]
    # 시각화용 순회 경로: 실제로 밟은 엣지 목록
    path_edges: list[Edge]

    def counts(self) -> dict:
        return {
            "conditions": len(self.conditions),
            "products": len(self.products),
            "screens": len(self.screens),
            "components": len(self.components),
            "departments": len(self.departments),
            "documents": len(self.documents),
        }


class GraphStore(ABC):
    """그래프 백엔드 추상화. Neptune/Local이 같은 계약을 따른다."""

    name: str = "abstract"

    @abstractmethod
    def upsert_nodes(self, nodes: list[Node]) -> None: ...

    @abstractmethod
    def upsert_edges(self, edges: list[Edge]) -> None: ...

    @abstractmethod
    def get_node(self, node_id: str) -> Node | None: ...

    @abstractmethod
    def neighbors(self, node_id: str, rel: str | None = None,
                  direction: str = "out") -> list[tuple[Edge, Node]]: ...

    @abstractmethod
    def find_by_label(self, label: str, **prop_filters) -> list[Node]: ...

    @abstractmethod
    def impact_of_regulation(self, reg_code: str) -> ImpactResult: ...

    @abstractmethod
    def stats(self) -> dict: ...


class LocalGraphStore(GraphStore):
    """인메모리 구현. seed JSONL을 로드해 §4.3 순회를 수행한다."""

    name = "local"

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._out: dict[str, dict[str, list[Edge]]] = defaultdict(lambda: defaultdict(list))
        self._in: dict[str, dict[str, list[Edge]]] = defaultdict(lambda: defaultdict(list))
        self._by_label: dict[str, list[str]] = defaultdict(list)

    # ---------- 적재 ----------
    def upsert_nodes(self, nodes: list[Node]) -> None:
        for n in nodes:
            if n.id not in self._nodes:
                self._by_label[n.label].append(n.id)
            self._nodes[n.id] = n

    def upsert_edges(self, edges: list[Edge]) -> None:
        for e in edges:
            if e.src not in self._nodes or e.dst not in self._nodes:
                raise ValueError(f"dangling edge: {e.src} -[{e.rel}]-> {e.dst}")
            self._out[e.src][e.rel].append(e)
            self._in[e.dst][e.rel].append(e)

    @classmethod
    def from_seed_dir(cls, seed_dir: str | Path) -> "LocalGraphStore":
        seed_dir = Path(seed_dir)
        store = cls()
        with open(seed_dir / "nodes.jsonl", encoding="utf-8") as f:
            store.upsert_nodes([Node(**json.loads(line)) for line in f])
        with open(seed_dir / "edges.jsonl", encoding="utf-8") as f:
            store.upsert_edges([Edge(**json.loads(line)) for line in f])
        return store

    # ---------- 조회 ----------
    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def neighbors(self, node_id: str, rel: str | None = None,
                  direction: str = "out") -> list[tuple[Edge, Node]]:
        table = self._out if direction == "out" else self._in
        rels = [rel] if rel else list(table[node_id].keys())
        result = []
        for r in rels:
            for e in table[node_id].get(r, []):
                other = e.dst if direction == "out" else e.src
                result.append((e, self._nodes[other]))
        return result

    def find_by_label(self, label: str, **prop_filters) -> list[Node]:
        out = []
        for nid in self._by_label.get(label, []):
            n = self._nodes[nid]
            if all(n.props.get(k) == v for k, v in prop_filters.items()):
                out.append(n)
        return out

    # ---------- S1: §4.3 4-hop 순회 ----------
    def impact_of_regulation(self, reg_code: str) -> ImpactResult:
        regs = self.find_by_label("Regulation", code=reg_code)
        if not regs:
            return ImpactResult(None, [], [], [], [], [], [], [])
        reg = regs[0]
        path: list[Edge] = []
        seen: dict[str, set[str]] = defaultdict(set)

        def collect(bucket: str, node: Node, edge: Edge) -> None:
            if node.id not in seen[bucket]:
                seen[bucket].add(node.id)
            path.append(edge)

        conditions: list[Node] = []
        products: list[Node] = []
        screens: list[Node] = []
        components: list[Node] = []
        departments: list[Node] = []
        documents: list[Node] = []

        # (r)<-[:DERIVED_FROM]-(c:Condition)
        for e, cond in self.neighbors(reg.id, "DERIVED_FROM", direction="in"):
            if cond.id not in seen["c"]:
                seen["c"].add(cond.id); conditions.append(cond)
            path.append(e)
            # (c)<-[:HAS_CONDITION]-(p:Product)
            for e2, prod in self.neighbors(cond.id, "HAS_CONDITION", direction="in"):
                path.append(e2)
                if prod.id in seen["p"]:
                    continue
                seen["p"].add(prod.id); products.append(prod)
                # (p)-[:SOLD_VIA]->(s:Screen)-[:USES]->(comp)
                for e3, scr in self.neighbors(prod.id, "SOLD_VIA"):
                    path.append(e3)
                    if scr.id not in seen["s"]:
                        seen["s"].add(scr.id); screens.append(scr)
                        for e4, comp in self.neighbors(scr.id, "USES"):
                            path.append(e4)
                            if comp.id not in seen["comp"]:
                                seen["comp"].add(comp.id); components.append(comp)
                        for e5, dept in self.neighbors(scr.id, "OWNED_BY"):
                            path.append(e5)
                            if dept.id not in seen["d"]:
                                seen["d"].add(dept.id); departments.append(dept)
                for e6, dept in self.neighbors(prod.id, "OWNED_BY"):
                    path.append(e6)
                    if dept.id not in seen["d"]:
                        seen["d"].add(dept.id); departments.append(dept)
        # (doc)-[:REFERENCES]->(r)
        for e7, doc in self.neighbors(reg.id, "REFERENCES", direction="in"):
            path.append(e7)
            if doc.id not in seen["doc"]:
                seen["doc"].add(doc.id); documents.append(doc)

        return ImpactResult(reg, conditions, products, screens,
                            components, departments, documents, path)

    def stats(self) -> dict:
        return {
            "backend": self.name,
            "nodes": len(self._nodes),
            "edges": sum(len(v) for rels in self._out.values() for v in rels.values()),
            "by_label": {k: len(v) for k, v in sorted(self._by_label.items())},
        }


def get_store(seed_dir: str | Path | None = None) -> GraphStore:
    """GRAPH_BACKEND 환경변수에 따라 백엔드를 선택한다 (기본 local)."""
    backend = os.environ.get("GRAPH_BACKEND", "local")
    if backend == "neptune":
        # Phase 2에서 NeptuneGraphStore 구현으로 교체된다.
        raise NotImplementedError("NeptuneGraphStore는 Phase 2에서 구현 (SPEC §13)")
    default_seed = Path(__file__).resolve().parent.parent / "seed" / "out"
    return LocalGraphStore.from_seed_dir(seed_dir or default_seed)
