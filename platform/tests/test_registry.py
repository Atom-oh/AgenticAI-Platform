"""F4 Agent Registry 테스트 — 상태 기계 · 유일성 · Consumer API(APPROVED 만) · 감사 · 시드 멱등/리셋 · 하이브리드 검색 · 핸들러.

오프라인: 인메모리 페이크 테이블, REGISTRY_EMBED=0 (Bedrock 호출 없음).
실행: cd platform && python3 -m pytest tests/test_registry.py -q
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
os.environ.pop("REGISTRY_TABLE", None)
os.environ["REGISTRY_EMBED"] = "0"

from registry import api  # noqa: E402
from registry import seed as seedmod  # noqa: E402
from registry.fake_table import ConditionalCheckFailedException, new_registry_table  # noqa: E402
from registry.model import (ConflictError, NotFoundError, TransitionError, ValidationError,  # noqa: E402
                            allowed_targets, check_transition, validate_record)
from registry.store import EMBEDDING_ATTR, RegistryStore  # noqa: E402

ACTOR = "demo@atomai.click"


@pytest.fixture(autouse=True)
def fresh_store():
    yield api.reset_for_tests()


def _rec(name="widget_x", version="v1", rtype="AGENT", **kw):
    base = {"name": name, "recordVersion": version, "recordType": rtype, "description": "테스트 레코드",
            "owner": "테스트팀", "tags": ["test"], "payload": {"k": 1}}
    base.update(kw)
    return base


# ---------------- 상태 기계 ----------------
def test_state_machine_valid_paths():
    assert check_transition("DRAFT", "PENDING_APPROVAL") == "submit"
    assert check_transition("PENDING_APPROVAL", "APPROVED") == "approve"
    assert check_transition("PENDING_APPROVAL", "REJECTED", "근거 부족") == "reject"
    assert check_transition("APPROVED", "DEPRECATED", "v3 승인") == "deprecate"
    assert check_transition("REJECTED", "DRAFT") == "revise"
    assert allowed_targets("PENDING_APPROVAL") == ["APPROVED", "REJECTED"]
    assert allowed_targets("DEPRECATED") == []


@pytest.mark.parametrize("frm,to", [
    ("DRAFT", "APPROVED"), ("DRAFT", "DEPRECATED"), ("DRAFT", "REJECTED"),
    ("APPROVED", "DRAFT"), ("APPROVED", "PENDING_APPROVAL"), ("APPROVED", "REJECTED"),
    ("DEPRECATED", "APPROVED"), ("DEPRECATED", "DRAFT"),
    ("REJECTED", "APPROVED"), ("REJECTED", "PENDING_APPROVAL"),
    ("PENDING_APPROVAL", "DRAFT"), ("PENDING_APPROVAL", "DEPRECATED"),
    ("APPROVED", "APPROVED"), ("BOGUS", "APPROVED"), ("DRAFT", "BOGUS"),
])
def test_state_machine_invalid(frm, to):
    with pytest.raises(TransitionError):
        check_transition(frm, to, "사유")


def test_reason_required_for_reject_and_deprecate():
    with pytest.raises(ValidationError):
        check_transition("PENDING_APPROVAL", "REJECTED", "")
    with pytest.raises(ValidationError):
        check_transition("APPROVED", "DEPRECATED", "   ")


def test_transition_via_api_writes_audit():
    api.create_record(_rec(), ACTOR)
    rec, ev = api.transition("widget_x", "v1", "PENDING_APPROVAL", ACTOR, "검토 요청")
    assert rec["status"] == "PENDING_APPROVAL" and ev["transition"] == "submit"
    rec, ev = api.transition("widget_x", "v1", "APPROVED", "approver@atomai.click", "")
    assert rec["status"] == "APPROVED" and rec["updatedBy"] == "approver@atomai.click"
    trail = api.audit_trail("widget_x", "v1")
    assert [a["to"] for a in trail] == ["APPROVED", "PENDING_APPROVAL", "DRAFT"]  # 최신순
    assert trail[0] == {**trail[0], "actor": "approver@atomai.click", "from": "PENDING_APPROVAL", "to": "APPROVED"}
    assert set(trail[0]) >= {"actor", "from", "to", "reason", "ts"}
    assert trail[1]["reason"] == "검토 요청"
    assert trail[2]["from"] == "" and trail[2]["transition"] == "create"
    # 허용되지 않은 전이는 400 계열이며 감사 이벤트가 늘지 않는다
    with pytest.raises(TransitionError) as ei:
        api.transition("widget_x", "v1", "DRAFT", ACTOR, "")
    assert ei.value.code == 400
    assert len(api.audit_trail("widget_x", "v1")) == 3
    with pytest.raises(NotFoundError):
        api.transition("nope", "v1", "PENDING_APPROVAL", ACTOR)


def test_optimistic_concurrency_on_transition(fresh_store):
    api.create_record(_rec(), ACTOR)
    tbl = fresh_store.table()
    # 다른 세션이 먼저 전이했다고 가정 — 저장소를 우회해 상태를 바꾼다
    tbl.update_item(Key={"pk": "rec#widget_x", "sk": "v1"}, UpdateExpression="SET #s = :s",
                    ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "PENDING_APPROVAL"})
    store = RegistryStore(table=tbl)
    cur = {"status": "DRAFT"}
    # 낮은 수준에서 조건식이 걸리는지: from=DRAFT 인 갱신은 실패해야 한다
    with pytest.raises(ConditionalCheckFailedException):
        tbl.update_item(Key={"pk": "rec#widget_x", "sk": "v1"}, UpdateExpression="SET #s = :to",
                        ConditionExpression="attribute_exists(pk) AND #s = :from",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":to": "APPROVED", ":from": cur["status"]})
    # 저장소 수준: 최신 상태를 읽어 정상 전이한다 (PENDING_APPROVAL → APPROVED)
    rec, _ = store.transition("widget_x", "v1", "APPROVED", ACTOR)
    assert rec["status"] == "APPROVED"


# ---------------- 유일성 / 검증 ----------------
def test_uniqueness_name_plus_version():
    api.create_record(_rec(), ACTOR)
    with pytest.raises(ConflictError) as ei:
        api.create_record(_rec(description="다른 설명"), ACTOR)
    assert ei.value.code == 409
    api.create_record(_rec(version="v2"), ACTOR)  # 다른 버전은 허용
    assert {r["recordVersion"] for r in api.list_records({"q": "widget_x"})} == {"v1", "v2"}
    assert api.get_record("widget_x", "v1")["status"] == "DRAFT"


@pytest.mark.parametrize("bad", [
    {"name": "", "recordVersion": "v1", "recordType": "AGENT"},
    {"name": "한글이름", "recordVersion": "v1", "recordType": "AGENT"},
    {"name": "ok", "recordVersion": "1.0", "recordType": "AGENT"},
    {"name": "ok", "recordVersion": "v1", "recordType": "TOOL"},
    {"name": "ok", "recordVersion": "v1", "recordType": "CUSTOM"},  # subtype 누락
    {"name": "ok", "recordVersion": "v1", "recordType": "AGENT", "tags": "x"},
    {"name": "ok", "recordVersion": "v1", "recordType": "AGENT", "payload": []},
])
def test_validate_record_rejects(bad):
    with pytest.raises(ValidationError):
        validate_record(bad)


def test_fake_table_conditional_put_and_gsi():
    t = new_registry_table()
    t.put_item(Item={"pk": "rec#a", "sk": "v1", "status": "APPROVED", "updatedAt": 2})
    t.put_item(Item={"pk": "rec#a", "sk": "v2", "status": "APPROVED", "updatedAt": 5})
    t.put_item(Item={"pk": "audit#a#v1", "sk": "0000000000001#ab", "to": "APPROVED"})  # status 없음 → GSI 미포함
    with pytest.raises(ConditionalCheckFailedException):
        t.put_item(Item={"pk": "rec#a", "sk": "v1"}, ConditionExpression="attribute_not_exists(pk)")
    r = t.query(IndexName="byStatus", KeyConditionExpression="#st = :st", ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":st": "APPROVED"}, ScanIndexForward=False)
    assert [i["sk"] for i in r["Items"]] == ["v2", "v1"]
    r = t.query(KeyConditionExpression="pk = :pk AND begins_with(sk, :p)",
                ExpressionAttributeValues={":pk": "rec#a", ":p": "v"})
    assert r["Count"] == 2
    t.update_item(Key={"pk": "rec#a", "sk": "v1"}, UpdateExpression="SET #s = :s REMOVE updatedAt",
                  ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "DEPRECATED"})
    it = t.get_item(Key={"pk": "rec#a", "sk": "v1"})["Item"]
    assert it["status"] == "DEPRECATED" and "updatedAt" not in it


# ---------------- 시드 · Consumer API · 리셋 ----------------
def test_seed_baseline_content():
    res = seedmod.seed(ACTOR)
    assert res["created"] == res["total"] > 80 and res["skipped"] == 0
    assert res["embedded"] == 0 and res["embeddingsEnabled"] is False  # 오프라인
    v2, v3 = api.get_record("Button", "v2"), api.get_record("Button", "v3")
    assert v2["status"] == "APPROVED" and v3["status"] == "PENDING_APPROVAL"
    assert v2["payload"]["supersededBy"] == "v3" and v2["payload"]["module"] == "@atom/ui/button"
    assert v2["payload"]["propsSchema"]["properties"]["kind"]["enum"] == ["primary", "secondary", "danger"]
    assert set(v2["payload"]["propsSchema"]["required"]) == {"label", "kind"}
    p3 = v3["payload"]["propsSchema"]["properties"]
    assert p3["variant"]["enum"] == ["solid", "outline", "ghost"] and p3["tone"]["enum"] == ["brand", "neutral", "critical"]
    assert p3["size"]["enum"] == ["sm", "md", "lg"] and p3["onClick"]["type"] == "function"
    assert api.get_record("Button", "v1")["status"] == "DEPRECATED"
    # 여신 심사 화면 필수 컴포넌트 — 전부 승인 + 풍부한 스키마
    for name in ("DataTable", "Badge", "Card", "FormField", "Select", "PageHeader", "Alert"):
        r = api.get_record(name, "v1")
        assert r and r["status"] == "APPROVED" and r["subtype"] == "COMPONENT", name
        assert r["payload"]["module"] == f"@atom/ui/{seedmod.kebab(name)}"
        assert r["payload"]["propsSchema"]["properties"], name
    dt = api.get_record("DataTable", "v1")["payload"]["propsSchema"]
    assert set(dt["required"]) == {"columns", "rows", "caption"}
    assert dt["properties"]["columns"]["items"]["properties"].keys() == {"key", "header"}
    # 온톨로지 유래 컴포넌트 80건 + 사슬
    comps = api.list_records({"type": "CUSTOM", "subtype": "COMPONENT"})
    assert sum(1 for c in comps if c["payload"].get("origin") == "ontology") == 80
    assert api.get_record("Card", "v1")["payload"]["supersededBy"] == "v2"
    # MCP / AGENT / SKILL
    mcp = api.get_record("bank_platform_mcp_tools", "v1")
    assert mcp["recordType"] == "MCP" and mcp["status"] == "APPROVED" and mcp["payload"]["gateway"] == "nexus-platform-tools"
    assert {t["name"] for t in mcp["payload"]["tools"]} == {"analyze_regulation_impact", "resolve_metric", "list_regulations"}
    assert "nexus-platform-tools" in mcp["description"]
    for a in ("s1_regulation_impact", "s2_mydata_advisor", "screen_generation_agent", "report_reader", "report_writer"):
        assert api.get_record(a, "v1")["status"] == "APPROVED", a
    assert api.get_record("card_benefit_advisor", "v1")["status"] == "PENDING_APPROVAL"
    assert "미구현" in api.get_record("card_benefit_advisor", "v1")["description"]
    for s in ("bank-publishing-conventions", "kwcag-accessibility"):
        r = api.get_record(s, "v1")
        assert r["recordType"] == "SKILL" and r["status"] == "APPROVED" and r["payload"]["path"] == f"skills/{s}.md"
    c = api.counts()
    assert c["total"] == res["total"] and c["byType"]["MCP"] == 1 and c["byType"]["SKILL"] == 2 and c["byType"]["AGENT"] == 6


def test_consumer_api_returns_only_approved():
    seedmod.seed(ACTOR)
    comps = api.list_approved(subtype="COMPONENT")
    assert comps and all(r["status"] == "APPROVED" for r in comps)
    keys = {(r["name"], r["recordVersion"]) for r in comps}
    assert ("Button", "v2") in keys and ("Button", "v3") not in keys and ("Button", "v1") not in keys
    assert all(r["recordType"] == "CUSTOM" and r["subtype"] == "COMPONENT" for r in comps)
    assert all(EMBEDDING_ATTR not in r for r in comps)
    agents = api.list_approved("AGENT")
    assert {a["name"] for a in agents} == {"s1_regulation_impact", "s2_mydata_advisor", "screen_generation_agent",
                                          "report_reader", "report_writer"}
    # S3 반전: v2 폐기 + v3 승인 → Consumer 결과가 즉시 바뀐다
    api.transition("Button", "v2", "DEPRECATED", ACTOR, "v3 승인")
    api.transition("Button", "v3", "APPROVED", ACTOR, "")
    keys = {(r["name"], r["recordVersion"]) for r in api.list_approved(subtype="COMPONENT")}
    assert ("Button", "v3") in keys and ("Button", "v2") not in keys
    # 전체 목록에는 여전히 v2(DEPRECATED)가 보인다 — 관리 화면과 Consumer 의 차이
    assert api.get_record("Button", "v2")["status"] == "DEPRECATED"
    assert all(r["status"] == "APPROVED" for r in api.list_approved())


def test_seed_idempotent_and_reset_baseline():
    first = seedmod.seed(ACTOR)
    second = seedmod.seed(ACTOR)
    assert second["created"] == 0 and second["skipped"] == first["total"] and second["updated"] == 0
    assert api.counts()["total"] == first["total"]
    # 시연 반전 후 리셋
    api.transition("Button", "v2", "DEPRECATED", ACTOR, "v3 승인")
    api.transition("Button", "v3", "APPROVED", ACTOR, "")
    r = seedmod.reset_demo_state(ACTOR)
    assert {(c["name"], c["recordVersion"], c["to"]) for c in r["changed"]} == {("Button", "v2", "APPROVED"), ("Button", "v3", "PENDING_APPROVAL")}
    assert api.get_record("Button", "v2")["status"] == "APPROVED"
    assert api.get_record("Button", "v3")["status"] == "PENDING_APPROVAL"
    top = api.audit_trail("Button", "v2")[0]
    assert top["reason"] == "시연 리셋" and top["forced"] is True and top["from"] == "DEPRECATED" and top["to"] == "APPROVED"
    # 이미 기준선이면 변경 0건 (감사 이벤트도 추가되지 않는다)
    n = len(api.audit_trail("Button", "v2"))
    assert seedmod.reset_demo_state(ACTOR)["changed"] == [] and len(api.audit_trail("Button", "v2")) == n
    # 다른 레코드는 리셋이 건드리지 않는다
    api.transition("card_benefit_advisor", "v1", "APPROVED", ACTOR)
    seedmod.reset_demo_state(ACTOR)
    assert api.get_record("card_benefit_advisor", "v1")["status"] == "APPROVED"
    # seed(reset=True) 는 기준선 상태·내용으로 재기록
    res = seedmod.seed(ACTOR, reset=True)
    assert res["updated"] == first["total"] and res["statusReset"] >= 1
    assert api.get_record("card_benefit_advisor", "v1")["status"] == "PENDING_APPROVAL"


def test_reset_on_empty_registry_seeds_first():
    r = seedmod.reset_demo_state(ACTOR)
    assert r["seeded"] and r["seeded"]["created"] > 0 and r["changed"] == []
    assert r["consumerComponents"] == len(api.list_approved(subtype="COMPONENT")) > 0


def test_version_chain_walks_both_directions():
    seedmod.seed(ACTOR)
    chain = api.version_chain("Button", "v2")
    assert [c["recordVersion"] for c in chain] == ["v1", "v2", "v3"]
    assert [c["status"] for c in chain] == ["DEPRECATED", "APPROVED", "PENDING_APPROVAL"]
    assert [c["current"] for c in chain] == [False, True, False] and all(c["inChain"] for c in chain)
    assert [c["recordVersion"] for c in api.version_chain("Button", "v3")] == ["v1", "v2", "v3"]
    assert api.version_chain("Button", "v9") == []
    assert [c["recordVersion"] for c in api.version_chain("DataTable", "v1")] == ["v1"]


# ---------------- 검색 ----------------
def test_search_keyword_korean_without_embeddings():
    seedmod.seed(ACTOR)
    res = api.search_detailed("규정 개정 영향 분석")
    assert res["dense"] is False and "임베딩 미사용" in res["note"]
    assert res["hits"][0]["record"]["name"] == "s1_regulation_impact" and res["hits"][0]["match"] == "keyword"
    assert all(EMBEDDING_ATTR not in h["record"] for h in res["hits"])
    assert [h["record"]["name"] for h in api.search("여신 심사 결과 표")][0] == "DataTable"
    names = [h["record"]["name"] for h in api.search("Button")]
    assert names[:3] == ["Button", "Button", "Button"]
    # IDF: 60건에 나오는 "컴포넌트"가 3건에만 나오는 "버튼"을 덮지 못한다 (더미 Widget 이 Button 을 앞서면 안 된다)
    names = [h["record"]["name"] for h in api.search("버튼 컴포넌트")]
    assert names[:3] == ["Button", "Button", "Button"], names[:5]
    assert api.search("상태 배지")[0]["record"]["name"] == "Badge"
    assert api.search("우대금리", "AGENT")[0]["record"]["name"] == "s2_mydata_advisor"
    assert all(h["record"]["recordType"] == "SKILL" for h in api.search("접근성", "SKILL"))
    assert api.search("") == [] and api.search("zzzz없는단어qqq") == []


def test_search_hybrid_rrf_with_injected_embeddings(monkeypatch, fresh_store):
    seedmod.seed(ACTOR)
    # 두 레코드에만 임베딩을 심고, 질의 임베딩은 Badge 와 같게 → Badge 가 dense 1위
    badge = [1.0] + [0.0] * 3
    alert = [0.0, 1.0, 0.0, 0.0]
    fresh_store.set_embedding("Badge", "v1", json.dumps(badge))
    fresh_store.set_embedding("Alert", "v1", json.dumps(alert))
    monkeypatch.setattr(api, "_embed", lambda texts: [badge] if texts else None)
    res = api.search_detailed("상태 배지")
    assert res["dense"] is True
    top = res["hits"][0]
    assert top["record"]["name"] == "Badge" and top["match"] == "hybrid" and top["denseRank"] == 1 and top["keywordRank"] == 1
    assert top["denseScore"] == 1.0
    # Alert 는 코사인 0 → dense 미포함(최소 유사도 미달) → 키워드로만 잡히면 'keyword'
    for h in res["hits"]:
        if h["record"]["name"] == "Alert":
            assert h["match"] == "keyword"
    # 키워드에 전혀 없는 질의라도 dense 만으로 잡힌다
    res2 = api.search_detailed("qqqq")
    assert res2["hits"] and res2["hits"][0]["record"]["name"] == "Badge" and res2["hits"][0]["match"] == "dense"


def test_embedding_failure_falls_back_to_keyword(monkeypatch, capsys):
    """REGISTRY_EMBED=1 인데 Bedrock 호출이 실패하면 — 예외를 삼키고 키워드만으로 답하며 로그를 남긴다."""
    import types
    seedmod.seed(ACTOR)
    monkeypatch.setenv("REGISTRY_EMBED", "1")
    stub = types.ModuleType("engine.bedrock")

    def boom(texts):
        raise RuntimeError("bedrock down")
    stub.embed = boom
    monkeypatch.setitem(sys.modules, "engine.bedrock", stub)
    res = api.search_detailed("배지")
    assert res["dense"] is False and "임베딩 호출 실패" in res["note"]
    assert res["hits"][0]["record"]["name"] == "Badge" and res["hits"][0]["match"] == "keyword"
    assert "registry.embed_failed" in capsys.readouterr().out
    # 시드도 같은 경로 — 실패는 카운트만 올리고 시드는 성공한다
    api.reset_for_tests()
    r = seedmod.seed(ACTOR)
    assert r["embedFailed"] == r["total"] and r["embedded"] == 0 and r["created"] == r["total"]


# ---------------- 핸들러 ----------------
class _FakeApigw:
    def __init__(self):
        self.posted = []

    def post_to_connection(self, ConnectionId, Data):
        self.posted.append(json.loads(Data.decode()))


def _handler_module():
    spec = importlib.util.spec_from_file_location("handlers_registry_under_test", ROOT / "api" / "handlers" / "registry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ctx():
    from common.ctx import Ctx
    gw = _FakeApigw()
    return Ctx(apigw=gw, conn_id="c1", email=ACTOR, rid="r1"), gw


def test_handler_routes_end_to_end():
    h = _handler_module()
    assert set(h.ROUTES) == {"registry_list", "registry_get", "registry_transition", "registry_search",
                             "registry_consumer", "registry_create", "registry_seed"}
    ctx, gw = _ctx()
    # 빈 레지스트리 → 목록 요청이 기준선을 부트스트랩한다
    h.registry_list(ctx, {})
    ev = gw.posted[-1]
    assert ev["type"] == "registry_list" and ev["reqId"] == "r1" and ev["bootstrapped"]["created"] > 0
    assert ev["counts"]["total"] == len(ev["records"]) and all(EMBEDDING_ATTR not in r for r in ev["records"])
    assert ev["backend"] == "memory" and ev["embeddingsEnabled"] is False
    h.registry_list(ctx, {"type": "CUSTOM", "status": "PENDING_APPROVAL"})
    assert all(r["recordType"] == "CUSTOM" and r["status"] == "PENDING_APPROVAL" for r in gw.posted[-1]["records"])
    assert gw.posted[-1]["bootstrapped"] is None
    # get: record + audit + versionChain
    h.registry_get(ctx, {"name": "Button", "version": "v2"})
    ev = gw.posted[-1]
    assert ev["ok"] and ev["record"]["status"] == "APPROVED" and ev["record"]["allowedTargets"] == ["DEPRECATED"]
    assert [c["recordVersion"] for c in ev["versionChain"]] == ["v1", "v2", "v3"] and ev["audit"]
    h.registry_get(ctx, {"name": "Nope", "version": "v1"})
    assert gw.posted[-1]["ok"] is False and gw.posted[-1]["code"] == 404
    # consumer: APPROVED 만
    h.registry_consumer(ctx, {"subtype": "COMPONENT"})
    ev = gw.posted[-1]
    assert ev["count"] == len(ev["records"]) > 0 and all(r["status"] == "APPROVED" for r in ev["records"])
    assert ("Button", "v3") not in {(r["name"], r["recordVersion"]) for r in ev["records"]}
    # transition: 잘못된 전이는 400 계열 이벤트(예외 아님)
    h.registry_transition(ctx, {"name": "Button", "version": "v3", "to": "DEPRECATED", "reason": "x"})
    ev = gw.posted[-1]
    assert ev["type"] == "registry_transition" and ev["ok"] is False and ev["code"] == 400 and "허용되지 않은 전이" in ev["error"]
    h.registry_transition(ctx, {"name": "Button", "version": "v2", "to": "DEPRECATED", "reason": ""})
    assert gw.posted[-1]["ok"] is False and gw.posted[-1]["errorType"] == "ValidationError"
    h.registry_transition(ctx, {"name": "Button", "version": "v2", "to": "DEPRECATED", "reason": "v3 승인"})
    ev = gw.posted[-1]
    assert ev["ok"] and ev["record"]["status"] == "DEPRECATED" and ev["audit"]["actor"] == ACTOR and ev["transition"] == "deprecate"
    assert ev["auditTrail"][0]["reason"] == "v3 승인"
    h.registry_transition(ctx, {"name": "Button", "version": "v3", "to": "APPROVED"})
    assert gw.posted[-1]["ok"] and gw.posted[-1]["record"]["status"] == "APPROVED"
    h.registry_consumer(ctx, {"subtype": "COMPONENT"})
    keys = {(r["name"], r["recordVersion"]) for r in gw.posted[-1]["records"]}
    assert ("Button", "v3") in keys and ("Button", "v2") not in keys
    # search
    h.registry_search(ctx, {"q": "버튼 컴포넌트"})
    ev = gw.posted[-1]
    assert ev["type"] == "registry_search" and ev["hits"] and ev["dense"] is False
    # create: 검증 실패 → 400, 성공 → DRAFT
    h.registry_create(ctx, {"record": {"name": "bad name!", "recordVersion": "v1", "recordType": "AGENT"}})
    assert gw.posted[-1]["ok"] is False and gw.posted[-1]["code"] == 400
    h.registry_create(ctx, {"record": _rec("new_agent")})
    ev = gw.posted[-1]
    assert ev["ok"] and ev["record"]["status"] == "DRAFT" and ev["record"]["updatedBy"] == ACTOR
    h.registry_create(ctx, {"record": _rec("new_agent")})
    assert gw.posted[-1]["ok"] is False and gw.posted[-1]["code"] == 409
    # seed 액션 (멱등)
    h.registry_seed(ctx, {})
    assert gw.posted[-1]["ok"] and gw.posted[-1]["result"]["created"] == 0


def test_handler_does_not_log_reason_or_description_text(capsys):
    """로그에는 사유·설명 원문이 남지 않는다 (§12.3) — 길이/해시만."""
    h = _handler_module()
    ctx, gw = _ctx()
    seedmod.seed(ACTOR)
    secret_reason = "고객 홍길동 관련 사유 XYZ123"
    h.registry_transition(ctx, {"name": "Button", "version": "v2", "to": "DEPRECATED", "reason": secret_reason})
    out = capsys.readouterr().out
    assert "registry.transition" in out and secret_reason not in out and "XYZ123" not in out
    assert ACTOR not in out  # 이메일은 해시로만
    assert gw.posted[-1]["ok"]
