"""Exact, source-authorized human decisions for persistent designs."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pytest
from test_intake_admission import env
from intake_support import api, approved
from test_design_prd_extract import GOOD, PAGES
from design_fixtures import raw_graph_with, review_all
from intake import admission, review
from workbench.service import Service
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError
from workspace.design_api import create, decide, read_object
from workspace.ontology_store import Ontology


@pytest.fixture
def design_state(env):
    env.policy();env.grant("bob")
    ref = approved(env.api, env.pid, (PAGES[0]["text"] + "\n\n" + PAGES[1]["text"]).encode(), name="design-input.txt")
    pending = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    decision = review.decide(env.api, env.scope("bob"), pending["id"], approve=True, reason="synthetic verification")
    ctx = Service(env.api, env.scope(), {"sub": "alice", "exp": env.api.storage.clock() // 1000 + 3600})
    store = Ontology(ctx)
    ids = store.publish_candidate("design-source", raw_graph_with(ref, project_id=env.pid),
                                   expected_generation=None, request_id="design-source")["identities"]
    review_all(store, ids)
    model = "global.anthropic.claude-sonnet-5"
    result = create(ctx, {"requestId": "design-request", "title": "합성 상품 설계", "procedureId": ids["savings-signup"],
                          "decisionIds": [decision["id"]], "model": model})
    prd = copy.deepcopy(GOOD)
    def rewrite(value):
        if isinstance(value, dict):
            if set(value) == {"sourceId", "page", "quote", "derivativeHash"}:
                value.update(sourceId=ref["sourceId"], page=1, derivativeHash=decision["artifact"]["sha256"])
            else:
                for child in value.values():rewrite(child)
        elif isinstance(value, list):
            for child in value:rewrite(child)
    rewrite(prd)
    return ctx, result, prd


def test_human_prd_and_flow_approval_are_exact_and_persistent(design_state):
    ctx, initial, prd = design_state
    updated = decide(ctx, initial["id"], "prd", {"version": initial["version"], "prd": prd, "hash": schema.digest(prd)})
    assert updated["status"] == "flow-review" and updated["approvals"]["prd"]["actor"] == "alice"
    stored = ctx.get("design", initial["id"])
    prepared = read_object(ctx, stored["flow"])
    assert not prepared["issues"]
    final = decide(ctx, stored["id"], "flow", {"version": stored["version"], "hash": stored["flow"]["sha256"]})
    assert final["status"] == "ready" and final["approvals"]["flow"]["hash"] == stored["flow"]["sha256"]


def test_changed_financial_value_cannot_be_confirmed(design_state):
    ctx, initial, prd = design_state
    prd["baseRate"]["value"] = "연 9.0%"
    with pytest.raises(CollaborationError, match="원문"):
        decide(ctx, initial["id"], "prd", {"version": initial["version"], "prd": prd, "hash": schema.digest(prd)})
    assert ctx.get("design", initial["id"])["status"] == "draft"


def test_changed_flow_hash_and_wrong_role_cannot_approve(design_state):
    ctx, initial, prd=design_state
    decide(ctx,initial['id'],'prd',{'version':initial['version'],'prd':prd,'hash':schema.digest(prd)})
    record=ctx.get('design',initial['id'])
    with pytest.raises(CollaborationError):
        decide(ctx,record['id'],'flow',{'version':record['version'],'hash':'0'*64})
    bob=Service(ctx.host,ctx.collaboration.resolve_scope('bob',ctx.project_id),{'sub':'bob','exp':ctx.storage.clock()//1000+3600})
    with pytest.raises(CollaborationError):
        decide(bob,record['id'],'flow',{'version':record['version'],'hash':record['flow']['sha256']})
    assert ctx.get('design',record['id'])['status']=='flow-review'


def test_design_http_response_never_exposes_private_input_keys(design_state):
    from workspace.design_api import route
    ctx, design, _ = design_state
    status, value = route(ctx.host, ctx.scope, ctx.claims, 'GET', [design['id']], {}, {})
    assert status == 200 and value['design']['id'] == design['id']
    import json
    encoded=json.dumps(value)
    assert 'workspace/' not in encoded and 'inputKey' not in encoded


def test_revoked_input_is_not_served_by_design_http(design_state):
    from workspace.design_api import route
    from intake.records import INTAKE_OWNER
    from intake import admin_handler
    ctx, design, _ = design_state
    grant=ctx.storage.get(INTAKE_OWNER,'adm_grant','grant-1')
    # Same IAM-only admin handler used by the fixture; no project-owner grant mutation.
    from types import SimpleNamespace
    result=admin_handler.handler({'op':'revoke_grant','id':grant['id'],'expectedRevision':grant['revision'],'operator':'fixture-security'},
        SimpleNamespace(invoked_function_arn='arn:aws:lambda:ap-northeast-2:000000000000:function:IntakeAdminFn'))
    assert result['ok']
    with pytest.raises(CollaborationError) as error:
        route(ctx.host,ctx.scope,ctx.claims,'GET',[design['id']],{}, {})
    assert error.value.status == 404 and error.value.code == 'not-found'


def test_design_cursor_pages_are_complete_and_actor_bound(design_state):
    from workspace.design_api import route
    ctx, first, _ = design_state
    record=ctx.get('design',first['id'])
    for i in range(6):
        create(ctx,{'requestId':f'paged-{i}','title':f'Synthetic {i}','procedureId':record['procedureId'],
                    'decisionIds':record['decisionIds'],'model':record['model']})
    status, page=route(ctx.host,ctx.scope,ctx.claims,'GET',[],{}, {})
    assert status==200 and len(page['designs'])==5 and page['cursor']
    status, last=route(ctx.host,ctx.scope,ctx.claims,'GET',[],{}, {'cursor':page['cursor']})
    assert status==200 and len(last['designs'])==2 and not last['cursor']
    assert len({d['id'] for d in page['designs']+last['designs']})==7
    bob=ctx.collaboration.resolve_scope('bob',ctx.project_id)
    with pytest.raises(CollaborationError) as error:
        route(ctx.host,bob,{'sub':'bob','exp':ctx.claims['exp']},'GET',[],{}, {'cursor':page['cursor']})
    assert error.value.code=='design-cursor-stale'
