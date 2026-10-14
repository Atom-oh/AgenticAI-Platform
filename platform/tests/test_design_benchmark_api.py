"""Persisted independent criteria and observed local Browser comparisons through the real API."""
import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_design_execution import pipeline, design_state, env, api, ready, execute
from workspace.design_api import route, read_object
from workspace.collaboration import CollaborationError


def call(state, method, parts, body=None):
    return route(state.ctx.host, state.ctx.scope, state.ctx.claims, method, parts, body or {}, {})[1]


def test_reference_definition_comparison_and_replay_keep_fixed_human_criteria(pipeline):
    state = pipeline
    ready(state)
    outcome, _ = execute(state, "generate")
    assert outcome["status"] == "succeeded"
    design = state.ctx.get("design", state.design["id"])
    first = design["variants"][0]
    flow = read_object(state.ctx, design["flow"])
    snapshot = read_object(state.ctx, design["input"])["snapshot"]
    from workspace.design_stages import knowledge
    k = knowledge(snapshot)
    # Independent human fixture expectations; only identifier aliases are resolved.
    golden = [{"id": "base-rate", "case": {"eligible": True, "autoTransfer": False},
               "mustShow": [{"screen": k.aliases["intro"], "text": "연 2.0%"}], "mustNotShow": [],
               "endsAt": k.aliases["done"]}]
    body = {"requestId": "independent-reference", "title": "합성 상품 기준 화면",
            "designId": design["id"], "golden": golden,
            **{key: first[key] for key in ("artifactId", "sourceHash", "bundleHash")}}
    calls = len(state.models), state.compiles, state.browsers
    created = call(state, "POST", ["reference-sets"], body)["reference"]
    assert created["golden"] == golden and created["baselineScore"]["passed"] == ["base-rate"]
    assert call(state, "POST", ["reference-sets"], body)["reference"] == created
    assert (len(state.models), state.compiles, state.browsers) == calls
    assert created["id"] in [item["id"] for item in call(state, "GET", ["reference-sets"])["references"]]
    assert execute(state, "generate", requestId="reference-regeneration", variant=1)[0]["status"] == "succeeded"
    second = state.ctx.get("design", design["id"])["variants"][-1]
    compared = call(state, "POST", ["reference-sets", created["id"], "compare"], {
        "requestId": "compare-observed", "criteriaHash": created["criteriaHash"], "designId": design["id"],
        **{key: second[key] for key in ("artifactId", "sourceHash", "bundleHash")}})["comparison"]
    assert compared["comparison"] == {"improved": [], "regressed": [], "blocked": False}
    assert compared["approvalEffect"] is False and compared["before"]["artifactId"] == first["artifactId"]
    assert compared["after"]["artifactId"] == second["artifactId"]
    assert call(state, "GET", ["comparisons", compared["id"]])["comparison"] == compared
    assert not state.ctx.get("design", design["id"])["approvals"].get("ux")
    with pytest.raises(CollaborationError) as error:
        call(state, "POST", ["reference-sets", created["id"], "compare"], {
            "requestId": "wrong-criteria", "criteriaHash": "0" * 64, "designId": design["id"],
            **{key: second[key] for key in ("artifactId", "sourceHash", "bundleHash")}})
    assert error.value.code == "benchmark-criteria-changed"


def test_revoked_input_denies_saved_reference_and_comparison(pipeline, env):
    from intake.records import INTAKE_OWNER
    state = pipeline
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    design = state.ctx.get("design", state.design["id"])
    first = design["variants"][0]
    prepared = read_object(state.ctx, design["flow"])
    case = prepared["cases"][0]
    # Expected terminal is manually selected from the fixture's ineligible case.
    from workspace.design_stages import knowledge
    k = knowledge(read_object(state.ctx, design["input"])["snapshot"])
    golden = [{"id": "ineligible", "case": copy.deepcopy(case), "mustShow": [], "mustNotShow": [],
               "endsAt": k.aliases["ineligible"]}]
    created = call(state, "POST", ["reference-sets"], {
        "requestId": "revoked-reference", "title": "철회 검증용 기준", "designId": design["id"], "golden": golden,
        **{key: first[key] for key in ("artifactId", "sourceHash", "bundleHash")}})["reference"]
    grant = state.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
    env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
    with pytest.raises(CollaborationError) as error:
        call(state, "GET", ["reference-sets", created["id"]])
    assert error.value.code == "not-found"
    assert call(state, "GET", ["reference-sets"])["references"] == []


def test_actual_ontology_change_regenerates_new_design_against_unmodified_reference(pipeline):
    from design_fixtures import raw_graph_with
    from workspace.ontology_store import Ontology
    from workspace.design_api import create
    from workbench.service import Service
    state = pipeline
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    old = state.ctx.get("design", state.design["id"])
    variant = old["variants"][0]
    from workspace.design_stages import knowledge
    k = knowledge(read_object(state.ctx, old["input"])["snapshot"])
    reference = call(state, "POST", ["reference-sets"], {
        "requestId": "title-reference", "title": "입구 문구 기준", "designId": old["id"],
        **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")},
        "golden": [{"id": "intro-title", "case": {"eligible": True, "autoTransfer": False},
                    "mustShow": [{"screen": k.aliases["intro"], "text": "상품 소개"}],
                    "mustNotShow": [], "endsAt": k.aliases["done"]}]})["reference"]
    # Change the actual canonical ontology through its reviewed publication API.
    state.ctx = Service(state.ctx.host, state.ctx.scope, state.ctx.claims)
    store = Ontology(state.ctx)
    graph = raw_graph_with(old["sourceRefs"][0], project_id=state.ctx.project_id)
    node = next(item for item in graph["nodes"] if item["id"] == "intro")
    node["title"] = "변경된 상품 안내"
    from workspace import ontology_schema as schema
    graph["nodes"] = [schema.seal({key: value for key, value in item.items() if key != "contentHash"})
                      for item in graph["nodes"]]
    published = store.publish_candidate("design-source", graph,
        expected_generation=store.current()["generation"], request_id="changed-intro-title")
    changed_id = published["identities"]["intro"]
    changed = store.read([changed_id])["nodes"][0]
    for decision in ("reviewed", "approved"):
        store.review_node(changed_id, expected_generation=store.current()["generation"],
            revision=changed["revision"], decision=decision, reason="human synthetic title change",
            request_id=f"title-{decision}")
    # Historical comparison cannot authorize the obsolete design for new work.
    with pytest.raises(CollaborationError) as error:
        call(state, "GET", [old["id"]])
    assert error.value.code == "not-found"
    assert call(state, "GET", ["reference-sets", reference["id"]])["reference"]["criteriaHash"] == reference["criteriaHash"]
    state.ctx = Service(state.ctx.host, state.ctx.scope, state.ctx.claims)
    fresh = create(state.ctx, {"requestId": "after-ontology-change", "title": "변경 후 재생성",
        "procedureId": old["procedureId"], "decisionIds": old["decisionIds"], "model": old["model"]})
    state.design = fresh
    ready(state)
    assert execute(state, "generate", requestId="regenerate-after-change")[0]["status"] == "succeeded"
    current = state.ctx.get("design", fresh["id"])["variants"][0]
    result = call(state, "POST", ["reference-sets", reference["id"], "compare"], {
        "requestId": "changed-title-comparison", "criteriaHash": reference["criteriaHash"],
        "designId": fresh["id"], **{key: current[key] for key in ("artifactId", "sourceHash", "bundleHash")}})["comparison"]
    assert result["comparison"]["regressed"] == ["intro-title"] and not result["comparison"]["blocked"]
    assert result["before"]["generation"] != result["after"]["generation"]
    assert result["criteriaHash"] == reference["criteriaHash"]
    assert not state.ctx.get("design", fresh["id"])["approvals"].get("ux")


@pytest.mark.parametrize("missing_field", ["observations", "checkpoints"])
def test_ordinary_verifier_pass_cannot_create_reference_without_complete_observations(
        pipeline, monkeypatch, missing_field):
    state = pipeline
    actual = state.workflow.browser.evaluate
    def missing(*args, **kwargs):
        report = actual(*args, **kwargs)
        if missing_field == "observations":
            report.pop("observations", None)
        else:
            report["observations"].pop("checkpoints")
        return report
    monkeypatch.setattr(state.workflow.browser, "evaluate", missing)
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    design = state.ctx.get("design", state.design["id"])
    variant = design["variants"][0]
    from workspace.design_stages import knowledge
    k = knowledge(read_object(state.ctx, design["input"])["snapshot"])
    before = len(state.models), state.compiles, state.browsers
    with pytest.raises(CollaborationError) as error:
        call(state, "POST", ["reference-sets"], {
            "requestId": "no-actual-observations", "title": "관찰 없는 기준", "designId": design["id"],
            **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")},
            "golden": [{"id": "no-observation", "case": {"eligible": True, "autoTransfer": False},
                        "mustShow": [], "mustNotShow": [], "endsAt": k.aliases["done"]}]})
    assert error.value.code == "benchmark-observations-required"
    assert state.storage.list(state.ctx.owner, "design_reference") == []
    assert (len(state.models), state.compiles, state.browsers) == before


def reference_input(state, request_id="authority-reference"):
    ready(state)
    assert execute(state, "generate")[0]["status"] == "succeeded"
    design = state.ctx.get("design", state.design["id"])
    variant = design["variants"][0]
    from workspace.design_stages import knowledge
    k = knowledge(read_object(state.ctx, design["input"])["snapshot"])
    return {"requestId": request_id, "title": "권한 검증용 독립 기준", "designId": design["id"],
            **{key: variant[key] for key in ("artifactId", "sourceHash", "bundleHash")},
            "golden": [{"id": "base-rate", "case": {"eligible": True, "autoTransfer": False},
                        "mustShow": [{"screen": k.aliases["intro"], "text": "연 2.0%"}],
                        "mustNotShow": [], "endsAt": k.aliases["done"]}]}


def test_planner_developer_cannot_freeze_or_compare_and_retries_cannot_replace_criteria(pipeline):
    state = pipeline
    body = reference_input(state)
    created = call(state, "POST", ["reference-sets"], body)["reference"]
    compared_body = {"requestId": "same-result", "criteriaHash": created["criteriaHash"],
                     **{key: body[key] for key in ("designId", "artifactId", "sourceHash", "bundleHash")}}
    compared = call(state, "POST", ["reference-sets", created["id"], "compare"], compared_body)["comparison"]
    for actor in ("bob", "dana"):
        scope = state.ctx.collaboration.resolve_scope(actor, state.ctx.project_id)
        claims = {"sub": actor, "exp": state.ctx.claims["exp"]}
        for parts, request in ((["reference-sets"], body),
                               (["reference-sets", created["id"], "compare"], compared_body)):
            with pytest.raises(CollaborationError) as error:
                route(state.ctx.host, scope, claims, "POST", parts, request, {})
            assert (error.value.status, error.value.code) == (403, "forbidden")
    changed = copy.deepcopy(body)
    changed["golden"][0]["mustShow"][0]["text"] = "연 9.0%"
    with pytest.raises(CollaborationError) as error:
        call(state, "POST", ["reference-sets"], changed)
    assert error.value.code == "request-changed"
    assert call(state, "GET", ["reference-sets", created["id"]])["reference"] == created
    assert call(state, "POST", ["reference-sets", created["id"], "compare"], compared_body)["comparison"] == compared
    with pytest.raises(CollaborationError) as error:
        call(state, "POST", ["reference-sets", created["id"], "compare"],
             {**compared_body, "sourceHash": "0" * 64})
    assert error.value.code == "benchmark-source-changed"
    assert call(state, "GET", ["comparisons", compared["id"]])["comparison"] == compared


def test_reference_cursor_cannot_switch_actor_and_retains_every_authorized_page(pipeline):
    state = pipeline
    body = reference_input(state)
    expected = set()
    for index in range(7):
        reference = call(state, "POST", ["reference-sets"],
                         {**body, "requestId": f"paged-reference-{index}", "title": f"합성 기준 {index}"})["reference"]
        expected.add(reference["id"])
    _, first = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", ["reference-sets"], {}, {})
    assert len(first["references"]) == 5 and first["cursor"]
    _, last = route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", ["reference-sets"], {},
                    {"cursor": first["cursor"]})
    assert not last["cursor"]
    assert {item["id"] for item in [*first["references"], *last["references"]]} == expected
    other = state.ctx.collaboration.resolve_scope("bob", state.ctx.project_id)
    with pytest.raises(CollaborationError) as error:
        route(state.ctx.host, other, {"sub": "bob", "exp": state.ctx.claims["exp"]}, "GET",
              ["reference-sets"], {}, {"cursor": first["cursor"]})
    assert error.value.code == "design-cursor-stale"
    for query in ({"cursor": first["cursor"], "limit": "1"}, {"cursor": "missing-cursor"}):
        with pytest.raises(CollaborationError) as error:
            route(state.ctx.host, state.ctx.scope, state.ctx.claims, "GET", ["reference-sets"], {}, query)
        assert (error.value.status, error.value.code) == (409, "design-cursor-stale")


@pytest.mark.parametrize("change", ["grant", "version"])
def test_source_withdrawal_at_final_response_fence_denies_saved_criteria(pipeline, env, monkeypatch, change):
    from intake.records import INTAKE_OWNER
    state = pipeline
    created = call(state, "POST", ["reference-sets"], reference_input(state))["reference"]
    original = state.storage.put_many
    revoked = False
    def revoke_before_response(writes, checks=(), **kwargs):
        nonlocal revoked
        if not revoked and not writes and any(check["kind"] == "design_reference" for check in checks):
            revoked = True
            if change == "grant":
                grant = state.storage.get(INTAKE_OWNER, "adm_grant", "grant-1")
                env.admin({"op": "revoke_grant", "id": grant["id"], "expectedRevision": grant["revision"]})
            else:
                row = state.storage.get(state.ctx.owner, "design_reference", created["id"])
                state.storage.put(state.ctx.owner, "design_reference", row, expected_version=row["version"])
        return original(writes, checks, **kwargs)
    monkeypatch.setattr(state.storage, "put_many", revoke_before_response)
    with pytest.raises(CollaborationError) as error:
        call(state, "GET", ["reference-sets", created["id"]])
    assert revoked and error.value.code == "not-found"
    assert len(state.storage.list(state.ctx.owner, "design_reference")) == 1


@pytest.mark.parametrize("mutation", [
    "array-boolean", "array-object", "array-string", "unknown-field",
    "unknown-assertion-field", "missing-field", "duplicate-id", "text-object", "title-object",
])
def test_malformed_reference_criteria_are_client_errors_without_source_reads_or_writes(
        design_state, monkeypatch, mutation):
    from workspace import design_benchmarks
    ctx, design, _ = design_state
    golden = [{"id": "criterion-1", "case": {"eligible": True}, "mustShow": [],
               "mustNotShow": [], "endsAt": "done"}]
    if mutation.startswith("array-"):
        golden[0]["mustShow"] = {"array-boolean": True, "array-object": {}, "array-string": "bad"}[mutation]
    elif mutation == "unknown-field":
        golden[0]["modelPass"] = True
    elif mutation == "unknown-assertion-field":
        golden[0]["mustShow"] = [{"screen": "intro", "text": "안내", "observed": True}]
    elif mutation == "missing-field":
        golden[0].pop("mustNotShow")
    elif mutation == "duplicate-id":
        golden.append(copy.deepcopy(golden[0]))
    elif mutation == "text-object":
        golden[0]["mustShow"] = [{"screen": "intro", "text": {"untrusted": True}}]
    title = {"untrusted": True} if mutation == "title-object" else "입력 형식 검사"
    def unexpected_read(*args, **kwargs):
        pytest.fail("Malformed criteria must be refused before reading an artifact")
    monkeypatch.setattr(design_benchmarks, "artifact", unexpected_read)
    with pytest.raises(CollaborationError) as error:
        route(ctx.host, ctx.scope, ctx.claims, "POST", ["reference-sets"], {
            "requestId": "invalid-reference", "title": title,
            "designId": design["id"], "artifactId": "not-read",
            "sourceHash": "a" * 64, "bundleHash": "b" * 64, "golden": golden,
        }, {})
    assert (error.value.status, error.value.code) == (422, "benchmark-reference-invalid")
    assert ctx.storage.list(ctx.owner, "design_reference") == []


@pytest.mark.parametrize("bound", ["cases", "bytes"])
def test_reference_limits_remain_client_errors_before_artifact_reads(design_state, monkeypatch, bound):
    from workspace import design_benchmarks
    ctx, design, _ = design_state
    golden = [{"id": f"criterion-{index}", "case": {"eligible": True},
               "mustShow": [{"screen": "intro", "text": "안" * 12000}] if bound == "bytes" else [],
               "mustNotShow": [], "endsAt": "done"}
              for index in range(2 if bound == "bytes" else 65)]
    def unexpected_read(*args, **kwargs):
        pytest.fail("Over-limit criteria must be refused before reading an artifact")
    monkeypatch.setattr(design_benchmarks, "artifact", unexpected_read)
    with pytest.raises(CollaborationError) as error:
        route(ctx.host, ctx.scope, ctx.claims, "POST", ["reference-sets"], {
            "requestId": "over-limit-reference", "title": "기준 한도 검사",
            "designId": design["id"], "artifactId": "not-read",
            "sourceHash": "a" * 64, "bundleHash": "b" * 64, "golden": golden,
        }, {})
    assert (error.value.status, error.value.code) == (422, "benchmark-reference-limit")
    assert ctx.storage.list(ctx.owner, "design_reference") == []
