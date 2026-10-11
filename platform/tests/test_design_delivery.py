"""Real design-engine composition behind serializable application stages."""
import sys
from pathlib import Path
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
