"""UX Asset Portal 액션 (SPEC v2 §8-2 · §7 · §12.8).

요청/응답형(액션명 = 응답 type):
  portal_list         {category}  category ∈ Foundation|Components|Patterns|Screens|Procedures|Policies|UXWriting
                      → {cards[{id,label,name,status,rawStatus,version,owner,related{Label:n},computedBy,...}], count, backend, note}
  portal_detail       {id}        → {node, props, related, versionChain, neighbors, screenMeta?, registry?, mapping}
  portal_impact       {id}        → {counts, screens[], patterns[], policyRules[], departments[], products[], procedures[],
                                     graph{nodes,edges}}   (Component · Pattern · PolicyRule 만)
  portal_publish      {id}        → Registry 레코드 생성/조회 (DRAFT) — Component(기존 CUSTOM/COMPONENT 레코드) ·
                                     Pattern(CUSTOM/PATTERN) · Screen(CUSTOM/SCREEN_SPEC). 그 외는 ok:false '미구현'
  portal_sync         {id}        → related 재계산 ('그래프 재순회') + Registry 레코드 재조회
  portal_registry_map {}          → §7 등록 대상 매핑표 + 실제 Registry 레코드 상태 (MCP 서버 3건 멱등 시드)

원칙
- Related 카운트·영향 범위는 전부 GraphStore 순회 결과다 (§12.8 하드코딩 금지). 응답마다 computedBy='graph-traversal' 과
  backend(local|neptune) 를 함께 보낸다.
- 카테고리 ↔ 라벨: Foundation → UXTerm(category='공통' · 도메인 무관 공통 용어). 온톨로지에 디자인 토큰(색·타이포·간격) 노드가
  없으므로 Foundation 은 "미구현 — 대체 표시" 로 표기한다. Components→Component, Patterns→Pattern, Screens→Screen(+ScreenMeta),
  Procedures→Procedure, Policies→PolicyRule, UXWriting→UXTerm(전체).
- Status 는 노드의 approvalStatus/status 를 APPROVED|DRAFT|DEPRECATED 로 정규화하고 rawStatus 를 함께 준다.
- Neptune 백엔드에서는 카드 수만큼 질의하지 않도록 라벨 지정 배치 질의(IN $ids, LIMIT)로 이웃/카운트를 한 번에 가져온다.
- 로그에는 자산 설명·payload 원문을 남기지 않는다 (id · 건수 · 소요시간만).
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from common.ctx import Ctx
from common.log import log_event
from handlers.core import GRAPH_BACKEND, lazy_store

COMPUTED_BY = "graph-traversal"
TIER_BADGE = "Tier 0/1 전용"

# 카테고리 → (라벨, 속성 필터, 설명)
CATEGORIES: Dict[str, dict] = {
    "Foundation": {"label": "UXTerm", "filter": {"category": "공통"}, "library": "UX Dictionary (공통)",
                   "note": "미구현 — 온톨로지에 Foundation(디자인 토큰: 색·타이포·간격) 노드가 없다. 대체 표시: UX Dictionary 의 "
                           "도메인 무관 '공통' 용어. 이 용어들은 UX Writing 카테고리에도 포함된다."},
    "Components": {"label": "Component", "filter": {}, "library": "Component Library", "note": None},
    "Patterns": {"label": "Pattern", "filter": {}, "library": "Pattern Library", "note": None},
    "Screens": {"label": "Screen", "filter": {}, "library": "Screen (+ Screen Metadata)",
                "note": "Screen 노드에 ScreenMeta(DESCRIBES) 를 조인 — 목적·진입조건·이전/다음 화면."},
    "Procedures": {"label": "Procedure", "filter": {}, "library": "Procedure Library", "note": None},
    "Policies": {"label": "PolicyRule", "filter": {}, "library": "Policy Rule", "note": None},
    "UXWriting": {"label": "UXTerm", "filter": {}, "library": "UX Dictionary", "note": None},
}
LABEL_TO_CATEGORY = {"Component": "Components", "Pattern": "Patterns", "Screen": "Screens",
                     "Procedure": "Procedures", "PolicyRule": "Policies", "UXTerm": "UXWriting"}
IMPACT_LABELS = ("Component", "Pattern", "PolicyRule")
PUBLISHABLE = {"Component": ("CUSTOM", "COMPONENT"), "Pattern": ("CUSTOM", "PATTERN"), "Screen": ("CUSTOM", "SCREEN_SPEC")}

_APPROVED_RAW = {"APPROVED", "ACTIVE", "LIVE", "PUBLISHED"}
_DEPRECATED_RAW = {"DEPRECATED", "RETIRED", "INACTIVE", "ARCHIVED"}
# 문자열로 직렬화된 JSON 속성 (seed/generate.py 가 Neptune 호환을 위해 문자열로 저장)
_JSON_PROPS = ("propsSchema", "steps", "prevScreens", "nextScreens", "sections")
_NAME_KEYS = ("name", "title", "term", "purpose")


# ---------- 공통 유틸 ----------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _elapsed(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def map_status(raw: Any) -> str:
    """노드 상태 → APPROVED | DRAFT | DEPRECATED (§8-2 카드 Status). 알 수 없거나 대기 상태는 DRAFT."""
    s = str(raw or "").strip().upper()
    if s in _APPROVED_RAW:
        return "APPROVED"
    if s in _DEPRECATED_RAW:
        return "DEPRECATED"
    return "DRAFT"


def node_name(n) -> str:
    for k in _NAME_KEYS:
        v = n.props.get(k)
        if v:
            return str(v)[:80]
    return n.id


def parsed_props(props: dict) -> dict:
    """JSON 문자열 속성을 객체로 되돌린다 (표시용). 파싱 실패 시 원문 유지."""
    out = dict(props)
    for k in _JSON_PROPS:
        v = out.get(k)
        if isinstance(v, str) and v[:1] in ("[", "{"):
            try:
                out[k] = json.loads(v)
            except Exception:  # noqa: BLE001
                pass
    return out


def _g(n) -> dict:
    return {"id": n.id, "label": n.label, "name": node_name(n)}


def _major(semver: Any) -> int:
    m = re.match(r"^(\d+)", str(semver or "1"))
    return int(m.group(1)) if m else 1


def _store():
    return lazy_store()


def _category_of(n) -> Optional[str]:
    return LABEL_TO_CATEGORY.get(n.label)


# ---------- 배치 순회 (Neptune 은 라벨 지정 IN 질의 1건, Local 은 neighbors 반복) ----------
def _is_neptune(store) -> bool:
    return getattr(store, "name", "") == "neptune" and callable(getattr(store, "_q", None))


def bulk_related_counts(store, label: str, ids: List[str]) -> Dict[str, Dict[str, int]]:
    """{id: {Label: n}} — 양방향 이웃을 라벨별 distinct 카운트. 그래프 순회 결과만 쓴다 (§12.8)."""
    if not ids:
        return {}
    if _is_neptune(store):
        out: Dict[str, Dict[str, int]] = {i: {} for i in ids}
        for i in range(0, len(ids), 200):
            rows = store._q(f"MATCH (a:{label})-[]-(m) WHERE a.id IN $ids "
                            "RETURN a.id AS id, head(labels(m)) AS label, count(DISTINCT m) AS c LIMIT 20000",
                            {"ids": ids[i:i + 200]})
            for r in rows:
                out.setdefault(str(r["id"]), {})[str(r["label"])] = int(r["c"])
        return {k: dict(sorted(v.items())) for k, v in out.items()}
    return {i: store.related_counts(i) for i in ids}


def bulk_neighbors(store, src_label: str, ids: List[str], rel: str, direction: str, dst_label: str) -> Dict[str, list]:
    """{id: [Node]} — ids 각각의 rel 이웃(dst_label 만). Neptune 은 배치 질의."""
    if not ids:
        return {}
    if _is_neptune(store):
        pat = (f"(a:{src_label})-[:{rel}]->(m:{dst_label})" if direction == "out"
               else f"(a:{src_label})<-[:{rel}]-(m:{dst_label})")
        out: Dict[str, list] = defaultdict(list)
        for i in range(0, len(ids), 200):
            rows = store._q(f"MATCH {pat} WHERE a.id IN $ids RETURN a.id AS id, m LIMIT 20000", {"ids": ids[i:i + 200]})
            for r in rows:
                out[str(r["id"])].append(store._node(r["m"]))
        return dict(out)
    return {i: [n for _, n in store.neighbors(i, rel, direction=direction) if n.label == dst_label] for i in ids}


# ---------- 카드 ----------
def _brief(n) -> str:
    p = n.props
    if n.label == "Component":
        return f"v{p.get('version', '?')} · {p.get('approvalStatus', '?')}"
    if n.label == "Pattern":
        return f"카테고리 {p.get('category', '—')}"
    if n.label == "Procedure":
        try:
            steps = json.loads(p.get("steps") or "[]")
        except Exception:  # noqa: BLE001
            steps = []
        return f"{len(steps)} 단계"
    if n.label == "PolicyRule":
        return f"{p.get('ruleType', '—')} · {p.get('severity', '—')}"
    if n.label == "Screen":
        return f"{p.get('channel', '—')} · {p.get('route', '')}"
    if n.label == "UXTerm":
        return str(p.get("definition") or "")[:90]
    return ""


def _card(n, related: Dict[str, int], owner: Optional[str], meta: Optional[dict] = None) -> dict:
    p = n.props
    raw = p.get("approvalStatus", p.get("status"))
    card = {"id": n.id, "label": n.label, "category": _category_of(n), "name": node_name(n),
            "status": map_status(raw), "rawStatus": raw, "version": p.get("version"),
            "owner": owner, "brief": _brief(n), "related": related, "computedBy": COMPUTED_BY}
    if n.label == "UXTerm":
        card["termCategory"] = p.get("category")
    if meta is not None:
        card["meta"] = meta
    return card


def _screen_meta_map(store, screen_ids: List[str]) -> Dict[str, dict]:
    """ScreenMeta 조인 — 목록은 find_by_label 1회 + screenNo 조인(seed 계약: DESCRIBES 대상 == screenNo, 화면당 1건)."""
    wanted = set(screen_ids)
    out: Dict[str, dict] = {}
    for sm in store.find_by_label("ScreenMeta"):
        sid = str(sm.props.get("screenNo") or "")
        if sid in wanted:
            pp = parsed_props(sm.props)
            out[sid] = {"metaId": sm.id, "purpose": pp.get("purpose"), "entryCondition": pp.get("entryCondition"),
                        "prevScreens": pp.get("prevScreens") or [], "nextScreens": pp.get("nextScreens") or []}
    return out


def list_cards(store, category: str) -> Tuple[List[dict], dict]:
    spec = CATEGORIES[category]
    nodes = store.find_by_label(spec["label"], **spec["filter"])
    ids = [n.id for n in nodes]
    related = bulk_related_counts(store, spec["label"], ids)
    owners: Dict[str, Optional[str]] = {}
    metas: Dict[str, dict] = {}
    if spec["label"] == "Component":
        owners = {n.id: n.props.get("owner") for n in nodes}
    elif spec["label"] == "Screen":
        depts = bulk_neighbors(store, "Screen", ids, "OWNED_BY", "out", "Department")
        owners = {i: (node_name(depts[i][0]) if depts.get(i) else None) for i in ids}
        metas = _screen_meta_map(store, ids)
    cards = [_card(n, related.get(n.id, {}), owners.get(n.id), metas.get(n.id) if metas else None) for n in nodes]
    cards.sort(key=lambda c: c["id"])
    return cards, spec


# ---------- Registry 연동 (지연 import · 부재 허용) ----------
def _registry():
    try:
        from registry import api as registry_api  # noqa: WPS433
        return registry_api
    except Exception:  # noqa: BLE001 — 배포 패키지에 registry 모듈이 없을 수 있다
        return None


def _component_record_key(n) -> Tuple[str, str]:
    return str(n.props.get("name") or n.id), f"v{_major(n.props.get('version'))}"


def _registry_view(n) -> dict:
    """Component 노드 ↔ Registry 레코드(name, v<major>). 레코드가 없으면 record=None 을 그대로 보낸다."""
    reg = _registry()
    if reg is None:
        return {"available": False, "record": None, "tier": TIER_BADGE, "error": "Registry 모듈 없음"}
    if n.label != "Component":
        return {"available": True, "record": None, "tier": TIER_BADGE, "note": "Registry 레코드는 Component 만 기준선에 존재"}
    name, ver = _component_record_key(n)
    try:
        rec = reg.get_record(name, ver)
    except Exception as e:  # noqa: BLE001
        return {"available": True, "record": None, "tier": TIER_BADGE, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    return {"available": True, "record": rec, "name": name, "recordVersion": ver, "tier": TIER_BADGE,
            "backend": reg.backend()}


def _mapping_for(label: str) -> Optional[dict]:
    try:
        from registry.seed import ASSET_RECORD_TYPES
    except Exception:  # noqa: BLE001
        return None
    key = {"Component": "component_contract", "Screen": "screen_spec", "Pattern": "pattern"}.get(label)
    for row in ASSET_RECORD_TYPES:
        if row["key"] == key:
            return row
    return None


# ---------- 액션 ----------
def portal_list(ctx: Ctx, body: dict) -> None:
    t0 = time.time()
    category = str(body.get("category") or "Components").strip()
    if category not in CATEGORIES:
        ctx.post({"type": "portal_list", "ok": False, "code": 400,
                  "error": f"알 수 없는 카테고리: {category} (가능: {', '.join(CATEGORIES)})"})
        return
    store = _store()
    cards, spec = list_cards(store, category)
    # 카테고리별 건수 — 레일 배지용. Neptune 에서는 라벨당 질의 1건이므로 클라이언트가 첫 로드 뒤 withCounts=false 로 생략할 수 있다.
    counts = None
    if body.get("withCounts", True) not in (False, 0, "0", "false"):
        counts = {c: len(store.find_by_label(CATEGORIES[c]["label"], **CATEGORIES[c]["filter"])) for c in CATEGORIES}
    log_event("portal.list", ctx.trace_id, category=category, count=len(cards), backend=store.name, ms=_elapsed(t0))
    ctx.post({"type": "portal_list", "ok": True, "category": category, "label": spec["label"], "library": spec["library"],
              "note": spec["note"], "cards": cards, "count": len(cards), "categoryCounts": counts,
              "computedBy": COMPUTED_BY, "backend": store.name, "graphBackend": GRAPH_BACKEND, "elapsedMs": _elapsed(t0)})


def _neighbors_sample(store, node_id: str, per_rel: int = 8, total: int = 80) -> List[dict]:
    """방향·관계별 이웃 표본 [{rel, direction, count, nodes[{id,label,name}]}]."""
    out = []
    n_total = 0
    for direction in ("out", "in"):
        by_rel: Dict[str, list] = defaultdict(list)
        for e, other in store.neighbors(node_id, direction=direction):
            by_rel[e.rel].append(other)
        for rel in sorted(by_rel):
            nodes = by_rel[rel]
            take = nodes[:per_rel]
            n_total += len(take)
            out.append({"rel": rel, "direction": direction, "count": len(nodes), "nodes": [_g(x) for x in take]})
            if n_total >= total:
                return out
    return out


def portal_detail(ctx: Ctx, body: dict) -> None:
    t0 = time.time()
    node_id = str(body.get("id") or "")[:64].strip()
    store = _store()
    n = store.get_node(node_id) if node_id else None
    if n is None:
        ctx.post({"type": "portal_detail", "ok": False, "code": 404, "error": f"자산 없음: {node_id}"})
        return
    related = store.related_counts(n.id)
    chain = [{**_g(c), "version": c.props.get("version"), "status": map_status(c.props.get("approvalStatus", c.props.get("status"))),
              "rawStatus": c.props.get("approvalStatus", c.props.get("status")), "current": c.id == n.id}
             for c in store.version_chain(n.id)]
    owner = n.props.get("owner")
    screen_meta = None
    if n.label == "Screen":
        depts = [d for _, d in store.neighbors(n.id, "OWNED_BY") if d.label == "Department"]
        owner = node_name(depts[0]) if depts else None
        metas = [m for _, m in store.neighbors(n.id, "DESCRIBES", direction="in") if m.label == "ScreenMeta"]
        if metas:
            pp = parsed_props(metas[0].props)
            screen_meta = {"metaId": metas[0].id, "purpose": pp.get("purpose"), "entryCondition": pp.get("entryCondition"),
                           "prevScreens": pp.get("prevScreens") or [], "nextScreens": pp.get("nextScreens") or []}
    card = _card(n, related, owner, screen_meta)
    payload = {"type": "portal_detail", "ok": True, **card, "props": parsed_props(n.props),
               "versionChain": chain, "neighbors": _neighbors_sample(store, n.id),
               "impactSupported": n.label in IMPACT_LABELS, "publishable": n.label in PUBLISHABLE,
               "publishTarget": ({"recordType": PUBLISHABLE[n.label][0], "subtype": PUBLISHABLE[n.label][1]}
                                 if n.label in PUBLISHABLE else None),
               "mapping": _mapping_for(n.label), "registry": _registry_view(n) if n.label == "Component" else None,
               "backend": store.name, "graphBackend": GRAPH_BACKEND, "elapsedMs": _elapsed(t0)}
    if n.label == "UXTerm" and n.props.get("category") == "공통":
        payload["alsoIn"] = ["Foundation", "UXWriting"]
    log_event("portal.detail", ctx.trace_id, id=n.id, label=n.label, backend=store.name, ms=_elapsed(t0))
    ctx.post(payload)


# ---------- 영향 분석 ----------
def _impact_component(store, n) -> dict:
    r = store.impact_of_component(n.id)
    return {"center": n, "screens": r.screens, "patterns": r.patterns, "policyRules": r.policy_rules,
            "products": r.products, "departments": r.departments, "procedures": r.procedures, "components": [],
            "regulations": [], "path": list(r.path_edges), "traversal": "GraphStore.impact_of_component"}


def _expand_screens(store, screens: list, path: list, skip_rel: Optional[str] = None) -> dict:
    """화면 집합에서 컴포넌트·패턴·정책규칙·상품·부서·절차로 1홉 확장 (배치)."""
    from graph.store import Edge
    ids = [s.id for s in screens]
    res: Dict[str, Dict[str, Any]] = defaultdict(dict)

    def collect(rel: str, direction: str, dst_label: str, bucket: str) -> None:
        if rel == skip_rel:
            return
        for sid, others in bulk_neighbors(store, "Screen", ids, rel, direction, dst_label).items():
            for o in others:
                res[bucket].setdefault(o.id, o)
                path.append(Edge(sid, rel, o.id) if direction == "out" else Edge(o.id, rel, sid))
    collect("USES", "out", "Component", "components")
    collect("FOLLOWS", "out", "Pattern", "patterns")
    collect("CONSTRAINS", "in", "PolicyRule", "policyRules")
    collect("SOLD_VIA", "in", "Product", "products")
    collect("OWNED_BY", "out", "Department", "departments")
    collect("INCLUDES", "in", "Procedure", "procedures")
    return {k: list(v.values()) for k, v in res.items()}


def _impact_pattern(store, n) -> dict:
    """Pattern: (Screen)-[:FOLLOWS]->(pat) 화면 + (pat)-[:COMPOSES]->(Component)."""
    from graph.store import Edge
    path: list = []
    screens = [s for e, s in store.neighbors(n.id, "FOLLOWS", direction="in") if s.label == "Screen"]
    path += [Edge(s.id, "FOLLOWS", n.id) for s in screens]
    composed = [c for _, c in store.neighbors(n.id, "COMPOSES") if c.label == "Component"]
    path += [Edge(n.id, "COMPOSES", c.id) for c in composed]
    ex = _expand_screens(store, screens, path, skip_rel="FOLLOWS")
    comps = {c.id: c for c in composed}
    for c in ex.get("components", []):
        comps.setdefault(c.id, c)
    return {"center": n, "screens": screens, "patterns": [], "policyRules": ex.get("policyRules", []),
            "products": ex.get("products", []), "departments": ex.get("departments", []),
            "procedures": ex.get("procedures", []), "components": list(comps.values()), "regulations": [],
            "path": path, "traversal": "Portal: Screen-FOLLOWS->Pattern ∪ Pattern-COMPOSES->Component → 화면 1홉 확장"}


def _impact_policy(store, n) -> dict:
    """PolicyRule: (pol)-[:CONSTRAINS]->(Screen) 화면 + (pol)-[:DERIVED_FROM]->(Regulation)."""
    from graph.store import Edge
    path: list = []
    screens = [s for _, s in store.neighbors(n.id, "CONSTRAINS") if s.label == "Screen"]
    path += [Edge(n.id, "CONSTRAINS", s.id) for s in screens]
    regs = [r for _, r in store.neighbors(n.id, "DERIVED_FROM") if r.label == "Regulation"]
    path += [Edge(n.id, "DERIVED_FROM", r.id) for r in regs]
    ex = _expand_screens(store, screens, path, skip_rel="CONSTRAINS")
    return {"center": n, "screens": screens, "patterns": ex.get("patterns", []), "policyRules": [],
            "products": ex.get("products", []), "departments": ex.get("departments", []),
            "procedures": ex.get("procedures", []), "components": ex.get("components", []), "regulations": regs,
            "path": path, "traversal": "Portal: PolicyRule-CONSTRAINS->Screen → 화면 1홉 확장 (+DERIVED_FROM Regulation)"}


_REL_PRIORITY = {"USES": 0, "FOLLOWS": 1, "COMPOSES": 1, "CONSTRAINS": 1, "DERIVED_FROM": 1,
                 "SOLD_VIA": 2, "OWNED_BY": 3, "INCLUDES": 3}


def impact_graph(res: dict, max_nodes: int = 110, max_edges: int = 400) -> dict:
    """S1 과 같은 형태 {nodes[{id,label,name}], edges[{src,rel,dst}]} — 중심 → 화면 → 패턴/정책 → 부서/상품/절차 순, 상한 명시."""
    ordered = [res["center"]] + res["screens"] + res["components"] + res["patterns"] + res["policyRules"] \
        + res["regulations"] + res["departments"] + res["products"] + res["procedures"]
    nodes: Dict[str, dict] = {}
    for n in ordered:
        if n.id not in nodes:
            nodes[n.id] = _g(n)
    total_nodes = len(nodes)
    keep = dict(list(nodes.items())[:max_nodes])
    seen = set()
    edges = []
    for e in sorted(res["path"], key=lambda e: _REL_PRIORITY.get(e.rel, 9)):
        k = (e.src, e.rel, e.dst)
        if k in seen or e.src not in keep or e.dst not in keep:
            continue
        seen.add(k)
        edges.append({"src": e.src, "rel": e.rel, "dst": e.dst})
    total_edges = len({(e.src, e.rel, e.dst) for e in res["path"]})
    return {"nodes": list(keep.values()), "edges": edges[:max_edges],
            "truncated": {"nodes": max(total_nodes - len(keep), 0), "edges": max(total_edges - min(len(edges), max_edges), 0)}}


def compute_impact(store, n) -> dict:
    if n.label == "Component":
        return _impact_component(store, n)
    if n.label == "Pattern":
        return _impact_pattern(store, n)
    return _impact_policy(store, n)


def portal_impact(ctx: Ctx, body: dict) -> None:
    t0 = time.time()
    node_id = str(body.get("id") or "")[:64].strip()
    store = _store()
    n = store.get_node(node_id) if node_id else None
    if n is None:
        ctx.post({"type": "portal_impact", "ok": False, "code": 404, "error": f"자산 없음: {node_id}"})
        return
    if n.label not in IMPACT_LABELS:
        ctx.post({"type": "portal_impact", "ok": False, "code": 400, "id": n.id, "label": n.label,
                  "error": f"영향 분석은 Component · Pattern · PolicyRule 에만 제공한다 (요청: {n.label})"})
        return
    res = compute_impact(store, n)
    counts = {k: len(res[k]) for k in ("screens", "patterns", "policyRules", "products", "departments", "procedures",
                                        "components", "regulations")}
    lists = {k: [_g(x) for x in res[k]] for k in ("patterns", "policyRules", "products", "departments",
                                                   "procedures", "components", "regulations")}
    # 화면은 순회가 돌려준 Node 의 props 를 그대로 쓴다 (채널 표시) — 화면 수만큼 재조회하지 않는다
    lists["screens"] = [{**_g(x), "channel": x.props.get("channel")} for x in res["screens"]]
    graph = impact_graph(res)
    log_event("portal.impact", ctx.trace_id, id=n.id, label=n.label, screens=counts["screens"], backend=store.name,
              ms=_elapsed(t0))
    ctx.post({"type": "portal_impact", "ok": True, "id": n.id, "label": n.label, "name": node_name(n), "counts": counts,
              **lists, "graph": graph, "pathEdges": len(res["path"]), "traversal": res["traversal"],
              "computedBy": COMPUTED_BY, "backend": store.name, "graphBackend": GRAPH_BACKEND, "elapsedMs": _elapsed(t0)})


# ---------- Publish / Sync ----------
def _publish_payload(store, n) -> dict:
    pp = parsed_props(n.props)
    related = store.related_counts(n.id)
    base = {"assetId": n.id, "label": n.label, "sourceStatus": n.props.get("approvalStatus", n.props.get("status")),
            "related": related, "relatedComputedBy": COMPUTED_BY, "graphBackend": store.name, "publishedAt": _now_ms()}
    if n.label == "Pattern":
        comps = [c.id for _, c in store.neighbors(n.id, "COMPOSES") if c.label == "Component"]
        screens = [s.id for _, s in store.neighbors(n.id, "FOLLOWS", direction="in") if s.label == "Screen"]
        return {**base, "patternId": pp.get("patternId"), "name": pp.get("name"), "category": pp.get("category"),
                "composes": comps, "followedBy": screens[:50]}
    if n.label == "Screen":
        metas = [m for _, m in store.neighbors(n.id, "DESCRIBES", direction="in") if m.label == "ScreenMeta"]
        meta = parsed_props(metas[0].props) if metas else {}
        depts = [d for _, d in store.neighbors(n.id, "OWNED_BY") if d.label == "Department"]
        return {**base, "screenId": pp.get("screenId"), "name": pp.get("name"), "channel": pp.get("channel"),
                "route": pp.get("route"), "ownerDept": node_name(depts[0]) if depts else None,
                "spec": {"purpose": meta.get("purpose"), "entryCondition": meta.get("entryCondition"),
                         "prevScreens": meta.get("prevScreens") or [], "nextScreens": meta.get("nextScreens") or []},
                "uses": [c.id for _, c in store.neighbors(n.id, "USES") if c.label == "Component"],
                "follows": [p.id for _, p in store.neighbors(n.id, "FOLLOWS") if p.label == "Pattern"],
                "constrainedBy": [p.id for _, p in store.neighbors(n.id, "CONSTRAINS", direction="in") if p.label == "PolicyRule"]}
    # Component — 기존 시드와 같은 payload 형태 (module/exportName/propsSchema/componentId/semver/origin)
    schema = pp.get("propsSchema") if isinstance(pp.get("propsSchema"), dict) else {}
    try:
        from registry.seed import _thin_to_schema, kebab  # 시드와 같은 스키마 합성 규칙
        props_schema = _thin_to_schema(json.dumps(schema, ensure_ascii=False)) if schema else {}
        module = f"@atom/ui/{kebab(str(pp.get('name') or n.id))}"
    except Exception:  # noqa: BLE001
        props_schema, module = {"type": "object", "properties": schema}, f"@atom/ui/{str(pp.get('name') or n.id).lower()}"
    return {**base, "module": module, "exportName": pp.get("name"), "propsSchema": props_schema, "supersededBy": None,
            "componentId": n.id, "semver": pp.get("version"), "origin": "portal-publish"}


def _publish_record(store, n) -> dict:
    rtype, subtype = PUBLISHABLE[n.label]
    if n.label == "Component":
        name, ver = _component_record_key(n)
        desc = f"{pp_name(n)} — UX Asset Portal 에서 발행한 컴포넌트 계약 (온톨로지 {n.id})"
        tags = ["ui", "component", "portal", str(n.props.get("name") or "").lower()]
        owner = n.props.get("owner") or ""
    else:
        name, ver = n.id, "v1"
        owner = ""
        if n.label == "Pattern":
            desc = f"패턴 스펙 — {pp_name(n)} (카테고리 {n.props.get('category', '—')}) · UX Asset Portal 발행"
            tags = ["pattern", "ux", "portal", str(n.props.get("category") or "")]
        else:
            depts = [d for _, d in store.neighbors(n.id, "OWNED_BY") if d.label == "Department"]
            owner = node_name(depts[0]) if depts else ""
            desc = f"화면 스펙 — {pp_name(n)} ({n.props.get('channel', '—')}) · UX Asset Portal 발행 (Screen + ScreenMeta)"
            tags = ["screen", "spec", "portal", str(n.props.get("channel") or "")]
    return {"name": name, "recordVersion": ver, "recordType": rtype, "subtype": subtype, "description": desc[:2000],
            "owner": owner, "tags": [t for t in tags if t], "payload": _publish_payload(store, n)}


def pp_name(n) -> str:
    return node_name(n)


def portal_publish(ctx: Ctx, body: dict) -> None:
    t0 = time.time()
    node_id = str(body.get("id") or "")[:64].strip()
    store = _store()
    n = store.get_node(node_id) if node_id else None
    if n is None:
        ctx.post({"type": "portal_publish", "ok": False, "code": 404, "error": f"자산 없음: {node_id}"})
        return
    if n.label not in PUBLISHABLE:
        ctx.post({"type": "portal_publish", "ok": False, "code": 400, "id": n.id, "label": n.label,
                  "error": f"Publish 미구현 — §7 등록 대상 매핑에 없는 자산 유형 ({n.label}). "
                           "Component · Pattern · Screen 만 Registry 로 발행한다."})
        return
    reg = _registry()
    if reg is None:
        ctx.post({"type": "portal_publish", "ok": False, "code": 503, "id": n.id, "error": "Registry 모듈 없음 (배포 패키지 확인)"})
        return
    from registry.model import RegistryError
    rec_in = _publish_record(store, n)
    existing = reg.get_record(rec_in["name"], rec_in["recordVersion"])
    action = "existing"
    record = existing
    if existing is None:
        try:
            record = reg.create_record(rec_in, actor=ctx.email)  # DRAFT 로 시작 — 승인은 Registry 화면에서
            action = "created"
        except RegistryError as e:
            if getattr(e, "code", 400) == 409:  # 동시 발행 경합 — 기존 레코드를 돌려준다
                record, action = reg.get_record(rec_in["name"], rec_in["recordVersion"]), "existing"
            else:
                ctx.post({"type": "portal_publish", "ok": False, "code": e.code, "id": n.id, "error": str(e)[:300],
                          "errorType": type(e).__name__})
                return
    log_event("portal.publish", ctx.trace_id, id=n.id, label=n.label, action=action, email=ctx.email,
              recordType=rec_in["recordType"], subtype=rec_in["subtype"], status=(record or {}).get("status"),
              ms=_elapsed(t0))
    ctx.post({"type": "portal_publish", "ok": True, "id": n.id, "label": n.label, "action": action, "record": record,
              "target": {"recordType": rec_in["recordType"], "subtype": rec_in["subtype"], "name": rec_in["name"],
                         "recordVersion": rec_in["recordVersion"]},
              "mapping": _mapping_for(n.label), "tier": TIER_BADGE, "registryBackend": reg.backend(),
              "note": ("기존 Registry 레코드 — Portal 은 덮어쓰지 않는다 (상태 전이는 Agent Registry 화면에서)" if action == "existing"
                       else "DRAFT 로 생성 — 승인 요청 → 승인은 Agent Registry 화면의 상태 전이로 진행"),
              "elapsedMs": _elapsed(t0)})


def portal_sync(ctx: Ctx, body: dict) -> None:
    """Sync = 그래프 재순회 (related 재계산) + Registry 레코드 재조회. 외부 원본(Git/Figma) 동기화는 미구현."""
    t0 = time.time()
    node_id = str(body.get("id") or "")[:64].strip()
    store = _store()
    n = store.get_node(node_id) if node_id else None
    if n is None:
        ctx.post({"type": "portal_sync", "ok": False, "code": 404, "error": f"자산 없음: {node_id}"})
        return
    related = store.related_counts(n.id)
    log_event("portal.sync", ctx.trace_id, id=n.id, label=n.label, backend=store.name, ms=_elapsed(t0))
    ctx.post({"type": "portal_sync", "ok": True, "id": n.id, "label": n.label, "related": related,
              "computedBy": COMPUTED_BY, "syncLabel": "그래프 재순회", "backend": store.name, "graphBackend": GRAPH_BACKEND,
              "registry": _registry_view(n) if n.label == "Component" else None,
              "note": "외부 원본(Git · Figma) 동기화는 미구현 — 이 Sync 는 온톨로지 재순회와 Registry 재조회만 수행한다",
              "syncedAt": _now_ms(), "elapsedMs": _elapsed(t0)})


def portal_registry_map(ctx: Ctx, body: dict) -> None:
    """§7 등록 대상 매핑표 + 실제 Registry 상태. MCP 서버 레코드 3건은 여기서 멱등 시드한다 (기준선과 분리)."""
    t0 = time.time()
    reg = _registry()
    if reg is None:
        ctx.post({"type": "portal_registry_map", "ok": False, "code": 503, "error": "Registry 모듈 없음", "rows": []})
        return
    from registry import seed as seedmod
    bootstrapped = None
    if reg.counts()["total"] == 0 and body.get("bootstrap", True):
        bootstrapped = seedmod.seed(actor=ctx.email)
    mcp_seed = seedmod.seed_mcp_servers(actor=ctx.email)
    lookup = {
        "component_contract": [("Button", "v2"), ("Button", "v3")],
        "screen_spec": [], "pattern": [],
        "registry_gitlab_mcp": [("registry_gitlab_mcp", "v1")],
        "skills": [("bank-publishing-conventions", "v1"), ("kwcag-accessibility", "v1")],
        "screen_agent": [("screen_generation_agent", "v1")],
        "design_mcp": [("figma_mcp", "v1"), ("drawio_mcp", "v1")],
    }
    rows = []
    for row in seedmod.ASSET_RECORD_TYPES:
        recs = []
        for name, ver in lookup.get(row["key"], []):
            r = reg.get_record(name, ver)
            recs.append({"name": name, "recordVersion": ver, "status": r.get("status") if r else None,
                         "recordType": r.get("recordType") if r else None, "subtype": r.get("subtype") if r else None,
                         "found": r is not None,
                         "payloadFlags": ({k: r["payload"].get(k) for k in ("deployed", "connected", "tier", "origin")
                                           if k in (r.get("payload") or {})} if r else {})})
        if row["key"] in ("screen_spec", "pattern"):
            sub = row["subtype"]
            recs = [{"name": x["name"], "recordVersion": x["recordVersion"], "status": x["status"], "recordType": x["recordType"],
                     "subtype": x["subtype"], "found": True, "payloadFlags": {}}
                    for x in reg.list_records({"type": "CUSTOM", "subtype": sub})][:20]
        rows.append({**row, "records": recs})
    log_event("portal.registry_map", ctx.trace_id, mcpCreated=mcp_seed["created"], bootstrapped=bool(bootstrapped),
              ms=_elapsed(t0))
    ctx.post({"type": "portal_registry_map", "ok": True, "rows": rows, "mcpSeed": mcp_seed, "bootstrapped": bootstrapped,
              "tier": TIER_BADGE, "registryBackend": reg.backend(), "counts": reg.counts(), "elapsedMs": _elapsed(t0)})


ROUTES = {
    "portal_list": portal_list, "portal_detail": portal_detail, "portal_impact": portal_impact,
    "portal_publish": portal_publish, "portal_sync": portal_sync, "portal_registry_map": portal_registry_map,
}
