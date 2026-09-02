"""GraphStore 인터페이스와 LocalGraphStore · NeptuneGraphStore 구현 (SPEC v2 §5 · §10-1).

두 구현을 강제한다:
- LocalGraphStore: 인메모리, 개발·테스트·리허설용 (비용 0)
- NeptuneGraphStore: 고객 시연용 (openCypher HTTPS, VPC 내부 브리지 Lambda 경유)

환경변수 GRAPH_BACKEND=neptune|local 로 전환하며, UI는 항상 현재 백엔드를 표시한다.
로컬 구현으로 시연하면서 Neptune이라고 말하는 것은 허용되지 않는다.

v2 (§5-2/§5-3 UX 자산 도메인) 추가 계약:
- ImpactResult.policy_rules — S1 순회가 Regulation ← PolicyRule → Screen 경로를 포함한다 (§5-5)
- impact_of_component()  — 컴포넌트 변경 영향 (화면·패턴·정책규칙·상품·부서·절차)
- related_counts()       — 이웃 라벨별 건수 (UX Asset Portal 의 Related 카운트 — 하드코딩 금지, §12.8)
- version_chain()        — SUPERSEDED_BY 사슬을 과거→최신 순으로
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
    """S1 규정 영향 분석(§5-5) 결과 — 순회 경로를 함께 반환한다.

    screens = 상품 판매 화면(SOLD_VIA) ∪ 정책규칙이 제약하는 화면(PolicyRule-CONSTRAINS).
    components/departments 는 그 화면 전체에서 USES/OWNED_BY 로 이어진다.
    """
    regulation: Node | None
    conditions: list[Node]
    products: list[Node]
    screens: list[Node]
    components: list[Node]
    departments: list[Node]
    documents: list[Node]
    # 시각화용 순회 경로: 실제로 밟은 엣지 목록
    path_edges: list[Edge]
    # v2: (r)<-[:DERIVED_FROM]-(pol:PolicyRule)
    policy_rules: list[Node] = field(default_factory=list)

    def counts(self) -> dict:
        return {
            "conditions": len(self.conditions),
            "products": len(self.products),
            "screens": len(self.screens),
            "components": len(self.components),
            "departments": len(self.departments),
            "documents": len(self.documents),
            "policyRules": len(self.policy_rules),
        }


@dataclass
class ComponentImpact:
    """컴포넌트 변경 영향 분석 결과 (§5-4 두 번째 제약, §8-2 "이 컴포넌트를 변경하면 영향받는 화면").

    screens      : (Screen)-[:USES]->(comp) 직접 사용 화면
    patterns     : screens -[:FOLLOWS]-> Pattern  ∪  (Pattern)-[:COMPOSES]->(comp)
    policy_rules : (PolicyRule)-[:CONSTRAINS]-> screens
    products     : (Product)-[:SOLD_VIA]-> screens
    departments  : screens / products -[:OWNED_BY]-> Department
    procedures   : (Procedure)-[:INCLUDES]-> screens
    """
    component: Node | None
    screens: list[Node]
    patterns: list[Node]
    policy_rules: list[Node]
    products: list[Node]
    departments: list[Node]
    procedures: list[Node]
    path_edges: list[Edge]

    def counts(self) -> dict:
        return {
            "screens": len(self.screens),
            "patterns": len(self.patterns),
            "policyRules": len(self.policy_rules),
            "products": len(self.products),
            "departments": len(self.departments),
            "procedures": len(self.procedures),
        }


def _empty_component_impact() -> ComponentImpact:
    return ComponentImpact(None, [], [], [], [], [], [], [])


class GraphStore(ABC):
    """그래프 백엔드 추상화. Neptune/Local이 같은 계약을 따른다."""

    name: str = "abstract"

    @abstractmethod
    def upsert_nodes(self, nodes: list[Node]) -> None: ...

    @abstractmethod
    def upsert_edges(self, edges: list[Edge], labels: dict | None = None) -> None: ...

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
    def impact_of_component(self, component_id: str) -> ComponentImpact: ...

    @abstractmethod
    def related_counts(self, node_id: str) -> dict[str, int]: ...

    @abstractmethod
    def stats(self) -> dict: ...

    # ---------- 공통 구현: 버전 사슬 ----------
    def version_chain(self, node_id: str, max_hops: int = 50) -> list[Node]:
        """SUPERSEDED_BY 를 과거(in)·최신(out) 양방향으로 따라가 과거→최신 순으로 돌려준다.

        두 백엔드가 같은 neighbors() 계약을 쓰므로 순회 로직을 공유한다 (사슬은 짧고 홉당 1질의).
        순환·중복은 visited 로 끊고 max_hops 로 상한을 둔다 (§12.11 무한 루프 금지).
        """
        start = self.get_node(node_id)
        if start is None:
            return []
        visited = {start.id}
        older: list[Node] = []
        cur = start
        for _ in range(max_hops):
            prev = [n for _, n in self.neighbors(cur.id, "SUPERSEDED_BY", direction="in")
                    if n.label == cur.label and n.id not in visited]
            if not prev:
                break
            cur = prev[0]
            visited.add(cur.id)
            older.append(cur)
        newer: list[Node] = []
        cur = start
        for _ in range(max_hops):
            nxt = [n for _, n in self.neighbors(cur.id, "SUPERSEDED_BY", direction="out")
                   if n.label == cur.label and n.id not in visited]
            if not nxt:
                break
            cur = nxt[0]
            visited.add(cur.id)
            newer.append(cur)
        return list(reversed(older)) + [start] + newer


class LocalGraphStore(GraphStore):
    """인메모리 구현. seed JSONL을 로드해 §5-5 순회를 수행한다."""

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

    def upsert_edges(self, edges: list[Edge], labels: dict | None = None) -> None:
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
        if node_id not in table:  # defaultdict 오염 방지
            return []
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

    # ---------- S1: §5-5 순회 (v2 — PolicyRule 경로 포함) ----------
    def impact_of_regulation(self, reg_code: str) -> ImpactResult:
        regs = self.find_by_label("Regulation", code=reg_code)
        if not regs:
            return ImpactResult(None, [], [], [], [], [], [], [])
        reg = regs[0]
        path: list[Edge] = []
        seen: dict[str, set[str]] = defaultdict(set)

        conditions: list[Node] = []
        products: list[Node] = []
        screens: list[Node] = []
        components: list[Node] = []
        departments: list[Node] = []
        documents: list[Node] = []
        policy_rules: list[Node] = []

        def add(bucket: str, lst: list[Node], n: Node) -> bool:
            if n.id in seen[bucket]:
                return False
            seen[bucket].add(n.id)
            lst.append(n)
            return True

        def expand_screen(scr: Node) -> None:
            """(s)-[:USES]->(comp), (s)-[:OWNED_BY]->(dept) — 화면을 처음 만났을 때 1회."""
            for e4, comp in self.neighbors(scr.id, "USES"):
                if comp.label != "Component":
                    continue
                path.append(e4)
                add("comp", components, comp)
            for e5, dept in self.neighbors(scr.id, "OWNED_BY"):
                path.append(e5)
                add("d", departments, dept)

        # (r)<-[:DERIVED_FROM]-(c:Condition)<-[:HAS_CONDITION]-(p:Product)
        for e, src in self.neighbors(reg.id, "DERIVED_FROM", direction="in"):
            if src.label == "PolicyRule":
                # (r)<-[:DERIVED_FROM]-(pol:PolicyRule)-[:CONSTRAINS]->(s2:Screen)
                path.append(e)
                add("pol", policy_rules, src)
                for e8, scr in self.neighbors(src.id, "CONSTRAINS"):
                    if scr.label != "Screen":
                        continue
                    path.append(e8)
                    if add("s", screens, scr):
                        expand_screen(scr)
                continue
            if src.label != "Condition":
                continue
            cond = src
            add("c", conditions, cond)
            path.append(e)
            for e2, prod in self.neighbors(cond.id, "HAS_CONDITION", direction="in"):
                path.append(e2)
                if not add("p", products, prod):
                    continue
                # (p)-[:SOLD_VIA]->(s:Screen)
                for e3, scr in self.neighbors(prod.id, "SOLD_VIA"):
                    path.append(e3)
                    if add("s", screens, scr):
                        expand_screen(scr)
                # (p)-[:OWNED_BY]->(pd:Department)
                for e6, dept in self.neighbors(prod.id, "OWNED_BY"):
                    path.append(e6)
                    add("d", departments, dept)
        # (doc)-[:REFERENCES]->(r)
        for e7, doc in self.neighbors(reg.id, "REFERENCES", direction="in"):
            path.append(e7)
            add("doc", documents, doc)

        return ImpactResult(reg, conditions, products, screens,
                            components, departments, documents, path, policy_rules)

    # ---------- 컴포넌트 변경 영향 ----------
    def impact_of_component(self, component_id: str) -> ComponentImpact:
        comps = self.find_by_label("Component", componentId=component_id)
        comp = comps[0] if comps else self.get_node(component_id)
        if comp is None or comp.label != "Component":
            return _empty_component_impact()
        path: list[Edge] = []
        seen: dict[str, set[str]] = defaultdict(set)
        screens: list[Node] = []
        patterns: list[Node] = []
        policy_rules: list[Node] = []
        products: list[Node] = []
        departments: list[Node] = []
        procedures: list[Node] = []

        def add(bucket: str, lst: list[Node], n: Node) -> bool:
            if n.id in seen[bucket]:
                return False
            seen[bucket].add(n.id)
            lst.append(n)
            return True

        # (s:Screen)-[:USES]->(comp)
        for e, scr in self.neighbors(comp.id, "USES", direction="in"):
            if scr.label != "Screen":
                continue
            path.append(e)
            if not add("s", screens, scr):
                continue
            for e2, pat in self.neighbors(scr.id, "FOLLOWS"):             # (s)-[:FOLLOWS]->(Pattern)
                if pat.label == "Pattern":
                    path.append(e2); add("pat", patterns, pat)
            for e3, pol in self.neighbors(scr.id, "CONSTRAINS", direction="in"):  # (PolicyRule)-[:CONSTRAINS]->(s)
                if pol.label == "PolicyRule":
                    path.append(e3); add("pol", policy_rules, pol)
            for e4, prod in self.neighbors(scr.id, "SOLD_VIA", direction="in"):   # (Product)-[:SOLD_VIA]->(s)
                path.append(e4)
                if add("p", products, prod):
                    for e5, dept in self.neighbors(prod.id, "OWNED_BY"):
                        path.append(e5); add("d", departments, dept)
            for e6, dept in self.neighbors(scr.id, "OWNED_BY"):           # (s)-[:OWNED_BY]->(Department)
                path.append(e6); add("d", departments, dept)
            for e7, prc in self.neighbors(scr.id, "INCLUDES", direction="in"):    # (Procedure)-[:INCLUDES]->(s)
                if prc.label == "Procedure":
                    path.append(e7); add("prc", procedures, prc)
        # (Pattern)-[:COMPOSES]->(comp) — 패턴 라이브러리 차원의 포함 관계도 패턴 건수에 포함
        for e8, pat in self.neighbors(comp.id, "COMPOSES", direction="in"):
            if pat.label == "Pattern":
                path.append(e8); add("pat", patterns, pat)
        return ComponentImpact(comp, screens, patterns, policy_rules, products, departments, procedures, path)

    # ---------- Related 카운트 (그래프 순회 결과, 하드코딩 금지) ----------
    def related_counts(self, node_id: str) -> dict[str, int]:
        if node_id not in self._nodes:
            return {}
        by_label: dict[str, set[str]] = defaultdict(set)
        for direction in ("out", "in"):
            for _, other in self.neighbors(node_id, direction=direction):
                by_label[other.label].add(other.id)
        return {k: len(v) for k, v in sorted(by_label.items())}

    def stats(self) -> dict:
        return {
            "backend": self.name,
            "nodes": len(self._nodes),
            "edges": sum(len(v) for rels in self._out.values() for v in rels.values()),
            "by_label": {k: len(v) for k, v in sorted(self._by_label.items())},
        }


class NeptuneGraphStore(GraphStore):
    """Amazon Neptune Serverless (openCypher HTTPS) 구현 — 고객 시연용 (SPEC §10-1).

    노드 식별은 우리 도메인 `id` 프로퍼티, 라벨은 Neptune 라벨을 그대로 쓴다.
    Lambda 본체는 VPC 밖에 있고 BRIDGE_FN(VPC 프라이빗 서브넷의 브리지 Lambda)을 경유한다.
    모든 MATCH 는 라벨을 지정한다 (무라벨 MATCH 는 전수 스캔으로 타임아웃).
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

    def _nodes(self, cypher: str, params: dict) -> list[Node]:
        return [self._node(x["n"]) for x in self._q(cypher, params)]

    def _pairs(self, cypher: str, params: dict, rel: str) -> list[Edge]:
        return [Edge(row["s"], rel, row["d"]) for row in self._q(cypher, params)]

    def upsert_nodes(self, nodes: list[Node]) -> None:
        by_label: dict[str, list[dict]] = defaultdict(list)
        for n in nodes:
            by_label[n.label].append({"id": n.id, **{k: (json.dumps(v, ensure_ascii=False)
                                      if isinstance(v, (dict, list)) else v)
                                      for k, v in n.props.items()}})
        for label, rows in by_label.items():
            for i in range(0, len(rows), 200):
                self._q(f"UNWIND $rows AS row CREATE (n:{label}) SET n = row",
                        {"rows": rows[i:i + 200]})

    def upsert_edges(self, edges: list[Edge], labels: dict | None = None) -> None:
        """엣지 적재. labels(id→라벨)를 주면 라벨 지정 MATCH로 인덱스 탐색을 유도한다 (무라벨 MATCH는 전수 스캔으로 타임아웃)."""
        by_key: dict[tuple, list[dict]] = defaultdict(list)
        for e in edges:
            sl = (labels or {}).get(e.src, "")
            dl = (labels or {}).get(e.dst, "")
            by_key[(e.rel, sl, dl)].append({"src": e.src, "dst": e.dst})
        for (rel, sl, dl), rows in by_key.items():
            a = f"a:{sl}" if sl else "a"
            b = f"b:{dl}" if dl else "b"
            for i in range(0, len(rows), 100):
                self._q(f"UNWIND $rows AS row MATCH ({a} {{id: row.src}}) MATCH ({b} {{id: row.dst}}) "
                        f"CREATE (a)-[:{rel}]->(b)", {"rows": rows[i:i + 100]})

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

    # ---------- S1: §5-5 순회 (v2) ----------
    def impact_of_regulation(self, reg_code: str) -> ImpactResult:
        regs = self.find_by_label("Regulation", code=reg_code)
        if not regs:
            return ImpactResult(None, [], [], [], [], [], [], [])
        reg = regs[0]
        p = {"c": reg_code, "c2": reg.id}
        base = "MATCH (r:Regulation {code: $c})"
        via_prod = (base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(pp:Product)-[:SOLD_VIA]->(s:Screen)")
        via_pol = (base + "<-[:DERIVED_FROM]-(pol:PolicyRule)-[:CONSTRAINS]->(s:Screen)")

        conditions = self._nodes(base + "<-[:DERIVED_FROM]-(n:Condition) RETURN DISTINCT n LIMIT 2000", p)
        products = self._nodes(
            base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(n:Product) RETURN DISTINCT n LIMIT 1000", p)
        policy_rules = self._nodes(base + "<-[:DERIVED_FROM]-(n:PolicyRule) RETURN DISTINCT n LIMIT 500", p)
        # screens = SOLD_VIA 화면 ∪ PolicyRule 제약 화면 (UNION 은 라벨 MATCH 2회로 대신하고 파이썬에서 합친다)
        screens_by_id: dict[str, Node] = {}
        for cy in (via_prod + " RETURN DISTINCT s AS n LIMIT 1000",
                   via_pol + " RETURN DISTINCT s AS n LIMIT 1000"):
            for n in self._nodes(cy, p):
                screens_by_id.setdefault(n.id, n)
        screens = list(screens_by_id.values())
        comps_by_id: dict[str, Node] = {}
        depts_by_id: dict[str, Node] = {}
        for cy in (via_prod + "-[:USES]->(n:Component) RETURN DISTINCT n LIMIT 1000",
                   via_pol + "-[:USES]->(n:Component) RETURN DISTINCT n LIMIT 1000"):
            for n in self._nodes(cy, p):
                comps_by_id.setdefault(n.id, n)
        for cy in (base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(pp:Product)-[:OWNED_BY]->(n:Department) "
                   "RETURN DISTINCT n LIMIT 200",
                   via_prod + "-[:OWNED_BY]->(n:Department) RETURN DISTINCT n LIMIT 200",
                   via_pol + "-[:OWNED_BY]->(n:Department) RETURN DISTINCT n LIMIT 200"):
            for n in self._nodes(cy, p):
                depts_by_id.setdefault(n.id, n)
        documents = self._nodes(base + "<-[:REFERENCES]-(n:Document) RETURN DISTINCT n LIMIT 1000", p)
        # 시각화용 경로 엣지 (홉별 쌍 조회)
        path: list[Edge] = []
        for cy, rel in [
            (base + "<-[:DERIVED_FROM]-(c:Condition) RETURN DISTINCT c.id AS s, $c2 AS d", "DERIVED_FROM"),
            (base + "<-[:DERIVED_FROM]-(c:Condition)<-[:HAS_CONDITION]-(pp:Product) "
                    "RETURN DISTINCT pp.id AS s, c.id AS d", "HAS_CONDITION"),
            (via_prod + " RETURN DISTINCT pp.id AS s, s.id AS d", "SOLD_VIA"),
            (via_prod + "-[:USES]->(cm:Component) RETURN DISTINCT s.id AS s, cm.id AS d", "USES"),
            (via_prod + "-[:OWNED_BY]->(dp:Department) RETURN DISTINCT s.id AS s, dp.id AS d", "OWNED_BY"),
            (base + "<-[:DERIVED_FROM]-(:Condition)<-[:HAS_CONDITION]-(pp:Product)"
                    "-[:OWNED_BY]->(dp:Department) RETURN DISTINCT pp.id AS s, dp.id AS d", "OWNED_BY"),
            (base + "<-[:DERIVED_FROM]-(pol:PolicyRule) RETURN DISTINCT pol.id AS s, $c2 AS d", "DERIVED_FROM"),
            (via_pol + " RETURN DISTINCT pol.id AS s, s.id AS d", "CONSTRAINS"),
            (via_pol + "-[:USES]->(cm:Component) RETURN DISTINCT s.id AS s, cm.id AS d", "USES"),
            (via_pol + "-[:OWNED_BY]->(dp:Department) RETURN DISTINCT s.id AS s, dp.id AS d", "OWNED_BY"),
            (base + "<-[:REFERENCES]-(dc:Document) RETURN DISTINCT dc.id AS s, $c2 AS d", "REFERENCES"),
        ]:
            path.extend(self._pairs(cy + " LIMIT 3000", p, rel))
        return ImpactResult(reg, conditions, products, screens, list(comps_by_id.values()),
                            list(depts_by_id.values()), documents, path, policy_rules)

    # ---------- 컴포넌트 변경 영향 ----------
    def impact_of_component(self, component_id: str) -> ComponentImpact:
        comps = self.find_by_label("Component", componentId=component_id)
        if not comps:
            return _empty_component_impact()
        comp = comps[0]
        p = {"cid": component_id, "nid": comp.id}
        base = "MATCH (s:Screen)-[:USES]->(c:Component {componentId: $cid})"
        screens = self._nodes(base + " RETURN DISTINCT s AS n LIMIT 1000", p)
        pats_by_id: dict[str, Node] = {}
        for cy in (base + " MATCH (s)-[:FOLLOWS]->(n:Pattern) RETURN DISTINCT n LIMIT 500",
                   "MATCH (n:Pattern)-[:COMPOSES]->(c:Component {componentId: $cid}) RETURN DISTINCT n LIMIT 500"):
            for n in self._nodes(cy, p):
                pats_by_id.setdefault(n.id, n)
        policy_rules = self._nodes(base + " MATCH (n:PolicyRule)-[:CONSTRAINS]->(s) RETURN DISTINCT n LIMIT 500", p)
        products = self._nodes(base + " MATCH (n:Product)-[:SOLD_VIA]->(s) RETURN DISTINCT n LIMIT 1000", p)
        depts_by_id: dict[str, Node] = {}
        for cy in (base + " MATCH (s)-[:OWNED_BY]->(n:Department) RETURN DISTINCT n LIMIT 200",
                   base + " MATCH (pp:Product)-[:SOLD_VIA]->(s) MATCH (pp)-[:OWNED_BY]->(n:Department) "
                          "RETURN DISTINCT n LIMIT 200"):
            for n in self._nodes(cy, p):
                depts_by_id.setdefault(n.id, n)
        procedures = self._nodes(base + " MATCH (n:Procedure)-[:INCLUDES]->(s) RETURN DISTINCT n LIMIT 500", p)
        path: list[Edge] = []
        for cy, rel in [
            (base + " RETURN DISTINCT s.id AS s, $nid AS d", "USES"),
            (base + " MATCH (s)-[:FOLLOWS]->(pt:Pattern) RETURN DISTINCT s.id AS s, pt.id AS d", "FOLLOWS"),
            ("MATCH (pt:Pattern)-[:COMPOSES]->(c:Component {componentId: $cid}) RETURN DISTINCT pt.id AS s, $nid AS d",
             "COMPOSES"),
            (base + " MATCH (pol:PolicyRule)-[:CONSTRAINS]->(s) RETURN DISTINCT pol.id AS s, s.id AS d", "CONSTRAINS"),
            (base + " MATCH (pp:Product)-[:SOLD_VIA]->(s) RETURN DISTINCT pp.id AS s, s.id AS d", "SOLD_VIA"),
            (base + " MATCH (pp:Product)-[:SOLD_VIA]->(s) MATCH (pp)-[:OWNED_BY]->(dp:Department) "
                    "RETURN DISTINCT pp.id AS s, dp.id AS d", "OWNED_BY"),
            (base + " MATCH (s)-[:OWNED_BY]->(dp:Department) RETURN DISTINCT s.id AS s, dp.id AS d", "OWNED_BY"),
            (base + " MATCH (pr:Procedure)-[:INCLUDES]->(s) RETURN DISTINCT pr.id AS s, s.id AS d", "INCLUDES"),
        ]:
            path.extend(self._pairs(cy + " LIMIT 3000", p, rel))
        return ComponentImpact(comp, screens, list(pats_by_id.values()), policy_rules, products,
                               list(depts_by_id.values()), procedures, path)

    # ---------- Related 카운트 ----------
    def related_counts(self, node_id: str) -> dict[str, int]:
        n = self.get_node(node_id)
        if n is None:
            return {}
        rows = self._q(f"MATCH (a:{n.label} {{id: $id}})-[]-(m) "
                       "RETURN head(labels(m)) AS label, count(DISTINCT m) AS c", {"id": node_id})
        return {str(r["label"]): int(r["c"]) for r in sorted(rows, key=lambda r: str(r["label"]))}

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
