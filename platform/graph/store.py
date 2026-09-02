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


class NeptuneGraphStore(GraphStore):
    """Amazon Neptune Serverless (openCypher HTTPS) 구현 — 고객 시연용 (SPEC §11).

    노드 식별은 우리 도메인 `id` 프로퍼티, 라벨은 Neptune 라벨을 그대로 쓴다.
    Lambda가 같은 VPC에 있어 SG로만 접근 가능하다.
    """

    name = "neptune"

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = endpoint or os.environ.get("NEPTUNE_ENDPOINT", "")
        self.url = f"https://{self.endpoint}:8182/openCypher"

    def _q(self, cypher: str, params: dict | None = None) -> list[dict]:
        """openCypher 질의. BRIDGE_FN이 있으면 VPC 안의 브리지 Lambda를 경유한다 (Lambda 본체는 VPC 밖)."""
        bridge_fn = os.environ.get("BRIDGE_FN", "")
        if bridge_fn:
            import boto3
            lam = boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
            r = lam.invoke(FunctionName=bridge_fn, Payload=json.dumps(
                {"op": "neptune", "cypher": cypher, "params": params or {}}).encode())
            body = json.loads(r["Payload"].read().decode() or "{}")
            if r.get("FunctionError") or "errorMessage" in body:
                raise RuntimeError(f"neptune via bridge failed: {str(body)[:300]}")
            return body["results"]
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode(
            {"query": cypher, "parameters": json.dumps(params or {})}).encode()
        req = urllib.request.Request(self.url, data=data, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["results"]

    @staticmethod
    def _node(row: dict) -> Node:
        # openCypher HTTPS는 노드를 {~id, ~labels, ~properties}로 반환한다
        props = dict(row.get("~properties", {}))
        return Node(id=props.get("id", row.get("~id", "")),
                    label=(row.get("~labels") or ["?"])[0], props=props)

    def upsert_nodes(self, nodes: list[Node]) -> None:
        by_label: dict[str, list[dict]] = defaultdict(list)
        for n in nodes:
            by_label[n.label].append({"id": n.id, **{k: (json.dumps(v, ensure_ascii=False)
                                      if isinstance(v, (dict, list)) else v)
                                      for k, v in n.props.items()}})
        for label, rows in by_label.items():
            for i in range(0, len(rows), 300):
                self._q(f"UNWIND $rows AS row CREATE (n:{label}) SET n = row",
                        {"rows": rows[i:i + 300]})

    def upsert_edges(self, edges: list[Edge]) -> None:
        by_rel: dict[str, list[dict]] = defaultdict(list)
        for e in edges:
            by_rel[e.rel].append({"src": e.src, "dst": e.dst})
        for rel, rows in by_rel.items():
            for i in range(0, len(rows), 300):
                self._q(f"UNWIND $rows AS row MATCH (a {{id: row.src}}), (b {{id: row.dst}}) "
                        f"CREATE (a)-[:{rel}]->(b)", {"rows": rows[i:i + 300]})

    def wipe(self) -> None:
        self._q("MATCH (n) DETACH DELETE n")

    def count(self) -> tuple[int, int]:
        n = self._q("MATCH (n) RETURN count(n) AS c")[0]["c"]
        e = self._q("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
        return n, e

    def get_node(self, node_id: str) -> Node | None:
        rows = self._q("MATCH (n {id: $id}) RETURN n LIMIT 1", {"id": node_id})
        return self._node(rows[0]["n"]) if rows else None

    def neighbors(self, node_id: str, rel: str | None = None,
                  direction: str = "out") -> list[tuple[Edge, Node]]:
        rel_part = f":{rel}" if rel else ""
        pattern = (f"(a {{id: $id}})-[r{rel_part}]->(b)" if direction == "out"
                   else f"(a {{id: $id}})<-[r{rel_part}]-(b)")
        rows = self._q(f"MATCH {pattern} RETURN type(r) AS rel, b LIMIT 200", {"id": node_id})
        out = []
        for row in rows:
            other = self._node(row["b"])
            e = (Edge(node_id, row["rel"], other.id) if direction == "out"
                 else Edge(other.id, row["rel"], node_id))
            out.append((e, other))
        return out

    def find_by_label(self, label: str, **prop_filters) -> list[Node]:
        where = " AND ".join(f"n.{k} = ${k}" for k in prop_filters) or "true"
        rows = self._q(f"MATCH (n:{label}) WHERE {where} RETURN n LIMIT 1000", prop_filters)
        return [self._node(r["n"]) for r in rows]

    def impact_of_regulation(self, reg_code: str) -> ImpactResult:
        regs = self.find_by_label("Regulation", code=reg_code)
        if not regs:
            return ImpactResult(None, [], [], [], [], [], [], [])
        reg = regs[0]
        p = {"c": reg_code}
        base = "MATCH (r:Regulation {code: $c})"
        conditions = [self._node(x["n"]) for x in self._q(
            base + "<-[:DERIVED_FROM]-(n:Condition) RETURN DISTINCT n", p)]
        products = [self._node(x["n"]) for x in self._q(
            base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(n:Product) RETURN DISTINCT n", p)]
        screens = [self._node(x["n"]) for x in self._q(
            base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(:Product)"
                   "-[:SOLD_VIA]->(n:Screen) RETURN DISTINCT n", p)]
        components = [self._node(x["n"]) for x in self._q(
            base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(:Product)"
                   "-[:SOLD_VIA]->(:Screen)-[:USES]->(n:Component) RETURN DISTINCT n", p)]
        departments = [self._node(x["n"]) for x in self._q(
            base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(p:Product) "
                   "OPTIONAL MATCH (p)-[:SOLD_VIA]->(s:Screen) "
                   "MATCH (x)-[:OWNED_BY]->(n:Department) WHERE x = p OR x = s "
                   "RETURN DISTINCT n", p)]
        documents = [self._node(x["n"]) for x in self._q(
            base + "<-[:REFERENCES]-(n:Document) RETURN DISTINCT n", p)]
        # 시각화용 경로 엣지 (홉별 쌍 조회)
        path: list[Edge] = []
        for cy, rel in [
            (base + "<-[:DERIVED_FROM]-(c:Condition) RETURN c.id AS s, $c2 AS d", "DERIVED_FROM"),
            (base + "<-[:DERIVED_FROM]-(c:Condition)<-[:HAS_CONDITION]-(pp:Product) "
                    "RETURN DISTINCT pp.id AS s, c.id AS d", "HAS_CONDITION"),
            (base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(pp:Product)"
                    "-[:SOLD_VIA]->(sc:Screen) RETURN DISTINCT pp.id AS s, sc.id AS d", "SOLD_VIA"),
            (base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(:Product)"
                    "-[:SOLD_VIA]->(sc:Screen)-[:USES]->(cm:Component) "
                    "RETURN DISTINCT sc.id AS s, cm.id AS d", "USES"),
            (base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(pp:Product)"
                    "-[:OWNED_BY]->(dp:Department) RETURN DISTINCT pp.id AS s, dp.id AS d", "OWNED_BY"),
            (base + "<-[:REFERENCES]-(dc:Document) RETURN dc.id AS s, $c2 AS d", "REFERENCES"),
        ]:
            for row in self._q(cy, {"c": reg_code, "c2": reg.id}):
                path.append(Edge(row["s"], rel, row["d"]))
        return ImpactResult(reg, conditions, products, screens,
                            components, departments, documents, path)

    def stats(self) -> dict:
        n, e = self.count()
        return {"backend": self.name, "nodes": n, "edges": e, "by_label": {}}


def get_store(seed_dir: str | Path | None = None) -> GraphStore:
    """GRAPH_BACKEND 환경변수에 따라 백엔드를 선택한다 (기본 local)."""
    backend = os.environ.get("GRAPH_BACKEND", "local")
    if backend == "neptune":
        return NeptuneGraphStore()
    default_seed = Path(__file__).resolve().parent.parent / "seed" / "out"
    return LocalGraphStore.from_seed_dir(seed_dir or default_seed)
