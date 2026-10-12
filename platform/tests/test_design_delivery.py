"""Real design-engine composition behind serializable application stages."""
import sys
import copy
import json
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parent))
from design_fixtures import sealed_graph
from test_design_prd_extract import GOOD, PAGES
from workspace.design_stages import prepare_flow, generate, unpacked
from design_loop.prd_extract import validate_prd
from design_loop.react_project import kit_catalog_hash


def test_prepare_flow_and_generate_serializable_candidate():
    assert validate_prd(PAGES, GOOD)["issues"] == []
    graph = sealed_graph()
    prepared = prepare_flow(graph, "savings-signup", GOOD, catalog_hash=kit_catalog_hash())
    assert not prepared["issues"]
    value = generate(graph, prepared, GOOD, {}, variant=0)
    assert "src/App.tsx" in value["files"]
    assert len(unpacked(value["screens"])) >= len(prepared["flow"]["screens"])
    assert value["registry"] == prepared["registry"]


def test_reference_aliases_do_not_mask_human_content_or_change_values():
    import json
    from design_loop.references import References
    from intake.derivative import residual
    snapshot={'generation':'a'*64,'nodes':[{'id':'node-'+'b'*40,'contentHash':'c'*64,
        'title':'문의 010-1234-5678', 'properties':{'amount':'연 2.0%'}}], 'edges':[]}
    original=json.dumps(snapshot,ensure_ascii=False)
    refs=References(snapshot)
    prepared=refs.encode(original)
    assert '010-1234-5678' in prepared and '연 2.0%' in prepared
    assert refs.decode(prepared)==original
    assert residual([{'page':1,'text':prepared}],[{'term':'SyntheticTenant','kind':'org'}])['pii']


@pytest.mark.parametrize("fault", ["missing-review", "tampered-review", "changed-copy", "changed-profile",
                                  "missing-state", "revoked-lineage", "truncated-cases"])
def test_selected_compositions_require_complete_current_review_evidence(fault):
    from workspace import design_stages as delivery
    from design_loop import verify_graph, prd_extract
    from design_loop.convention import Registry
    graph=sealed_graph()
    prepared=prepare_flow(graph,"savings-signup",GOOD,catalog_hash=kit_catalog_hash())
    generated=generate(graph,prepared,GOOD,{},variant=0)
    k=delivery.knowledge(graph)
    bundle={"prd":GOOD,"flow":prepared["flow"],"expectation":prepared["expectation"],
        "screens":unpacked(generated["screens"]),"binding_values":prd_extract.bindings(GOOD),
        "registry":Registry(k,state=generated["registry"]),"contract":prepared["contract"]}
    deps={"model":"synthetic-model","profileHash":"f"*64,"normalize":lambda value:value,
        "generate":lambda system,user,on_token=None:json.dumps({"verdicts":{item["id"]:{"verdict":"pass","evidence":"synthetic review"}
            for item in json.loads(user)["items"]}})}
    judgments={}
    verify_graph._reviewer(bundle,k,deps,generated["mode"],judgments)
    bindings=[{"decisionId":"fixture","revision":"1","artifactHash":"a"*64}]
    reviewed={"generated":generated,"judgments":{key:verify_graph.stored_judgment(key,value["verdicts"],bindings)
        for key,value in judgments.items()},"reviewProfile":{name:deps[name] for name in ("model","profileHash")}}
    rows=[{"screenId":screen,"artifactId":"selected-fixture","result":reviewed} for screen in prepared["flow"]["screens"]]
    assert delivery.compose(graph,prepared,GOOD,rows,deps,lambda stored:True)[2]
    if fault=="missing-review":
        reviewed["judgments"].clear()
    elif fault=="tampered-review":
        next(iter(reviewed["judgments"].values()))["verdicts"]["injected"]={"verdict":"pass","evidence":"changed"}
    elif fault=="changed-copy":
        generated["screens"][0]["composition"]["slots"].clear()
    elif fault=="changed-profile":
        reviewed["reviewProfile"]["profileHash"]="0"*64
    elif fault=="missing-state":
        generated["screens"].pop()
    elif fault=="truncated-cases":
        prepared=copy.deepcopy(prepared)
        prepared["expectation"]["conditions"]=[f"condition-{i}" for i in range(10)]
    expected='design-compose-states-required' if fault=='missing-state' else 'design-compose-review-required'
    with pytest.raises(ValueError,match='^'+expected+'$'):
        delivery.compose(graph,prepared,GOOD,rows,deps,lambda stored:fault!="revoked-lineage")
