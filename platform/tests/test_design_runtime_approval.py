"""The compatibility design route needs the same approved source and evidence."""
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "api")]

from agentcore import runtime
from handlers import design
from registry import api

ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:123456789012:runtime/synthetic"


@pytest.fixture
def invocation(monkeypatch):
    api.reset_for_tests()
    api.create_record({"name": "design_flow_agent", "recordVersion": "v1", "recordType": "AGENT",
                       "payload": {"runtimeArn": ARN, "runtimeSourceHash": "a" * 64}},
                      "admin", status="APPROVED", embed=False, system_seed=True)
    monkeypatch.setattr(design, "RUNTIME_ARN", ARN)
    seen, published = [], []
    events = [("boundary", {"chars": 10, "estTokens": 3, "piiRules": 0, "piiCount": 0,
                             "piiDetectors": ["rules", "guardrail"], "seq": 1}),
              ("text_boundary", {"seq": 1}), ("text", "safe"), ("design_done", {"result": {"ok": True}, "modelCallSeq": 1}),
              ("meta", {"stopReason": "end_turn"})]

    def invoke(*args, **kwargs):
        seen.append(kwargs)
        return iter(events)

    monkeypatch.setattr(runtime, "invoke_stream", invoke)
    ctx = SimpleNamespace(user_sub="synthetic-actor", token=lambda *args: published.append(args),
                          stage=lambda *args, **kwargs: None)
    return ctx, seen, published, events


def test_design_forwards_approved_source_and_actor_bound_unique_session(invocation):
    ctx, seen, _, _ = invocation
    assert design._relay_runtime(ctx, {}, None)[0] == {"ok": True}
    assert seen[0]["extra"]["approvedSourceHash"] == "a" * 64
    assert len(seen[0]["session_id"]) == 64
    ctx.user_sub = "another-actor"
    design._relay_runtime(ctx, {}, None)
    assert seen[0]["session_id"] != seen[1]["session_id"]


@pytest.mark.parametrize("missing", ["actor", "approval", "hash", "runtime"])
def test_design_blocks_missing_or_mismatched_approval(invocation, missing):
    ctx, seen, _, _ = invocation
    if missing == "actor":
        ctx.user_sub = ""
    elif missing == "approval":
        api.get_store().force_status("design_flow_agent", "v1", "DEPRECATED", "admin", "Synthetic")
    else:
        payload = {"runtimeArn": ARN, "runtimeSourceHash": "a" * 64}
        payload.pop("runtimeSourceHash" if missing == "hash" else "runtimeArn")
        api.get_store().rewrite("design_flow_agent", "v1", {"payload": payload}, "admin")
    with pytest.raises(ValueError):
        design._relay_runtime(ctx, {}, None)
    assert not seen


@pytest.mark.parametrize("event", [("text", "UNVERIFIED_" * 20), ("design_done", {"result": {"ok": True}, "modelCallSeq": 1})])
def test_design_output_requires_prior_independent_evidence(invocation, event):
    ctx, _, published, events = invocation
    events.insert(0, event)
    with pytest.raises(ValueError):
        design._relay_runtime(ctx, {}, None)
    assert not published


def test_incomplete_design_response_is_not_a_publishable_result(invocation):
    ctx, _, _, events = invocation
    events[-1] = ("meta", {"incomplete": True})
    result, _, errors = design._relay_runtime(ctx, {}, None)
    assert result is None and errors


@pytest.mark.parametrize('policy', [False, True])
def test_runtime_design_refusal_preserves_classification_without_private_text(invocation, monkeypatch, policy):
    ctx, _, published, events = invocation
    raw = [{'type': 'boundary', 'seq': 1, 'chars': 10, 'estTokens': 3, 'piiRules': 0 if policy else 1,
            'piiCount': 0 if policy else 1, 'piiDetectors': ['rules','guardrail'] if policy else ['rules'],
            'blocked': True, 'policyDenied': policy, 'refusedTypes': [] if policy else ['EMAIL']},
           {'type':'error','code':422,'gate':'refused','types':[] if policy else ['EMAIL'],'message':'PRIVATE_DETAIL'},
           {'type':'meta','stopReason':'gate_refused','usage':{}}]
    events[:] = list(runtime.to_tuples(raw, 's'*64))
    result, meta, errors = design._relay_runtime(ctx, {}, None)
    assert result is None and meta['blocked'] and meta['code'] == 422 and meta['stopReason'] == 'gate_refused'
    assert not published and 'PRIVATE_DETAIL' not in str(errors)


def test_configured_runtime_is_selected_without_an_opt_in_flag(invocation, monkeypatch):
    ctx, _, _, _ = invocation
    responses = []
    ctx.post = responses.append
    monkeypatch.delenv('DESIGN_USE_RUNTIME', raising=False)
    monkeypatch.setattr(design,'load_assets',lambda:{'productSpecs':[],'smModels':[],'checklists':[],'source':'synthetic'})
    design.catalog(ctx,{})
    assert responses[-1]['runtime'] == 'agentcore-runtime/strands'
