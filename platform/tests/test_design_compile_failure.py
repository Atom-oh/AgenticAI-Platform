"""Failed compilation retains private diagnostics without granting artifact approval."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from test_design_api import design_state, env, api
from test_design_execution import pipeline, execute, authorized_call
from test_workspace_git_export import repo
from workspace.design_api import decide
from workspace import ontology_schema as schema


def test_compile_failure_retains_diagnostics_and_candidate_with_current_role_fences(pipeline, monkeypatch):
    s = pipeline
    decide(s.ctx, s.design['id'], 'prd', {'version': s.design['version'], 'prd': s.prd, 'hash': schema.digest(s.prd)})
    record = s.ctx.get('design', s.design['id'])
    decide(s.ctx, record['id'], 'flow', {'version': record['version'], 'hash': record['flow']['sha256']})
    def rejected(operation, payload):
        assert operation == 'compile'
        return {'build': {'ok': False, 'diagnostics': ['Synthetic type mismatch'], 'gates': {'types': {'status': 'fail'}}},
                'execution': {'sessionId': 'rejected-compile', 'inputHash': schema.digest(payload)}}
    monkeypatch.setattr(s.workflow.interpreter, 'execute', rejected)
    result, queued = execute(s, 'generate')
    assert result['status'] == 'failed'
    job = s.storage.get(s.ctx.owner, 'job', queued['jobId'])
    assert not job.get('unknownOutcome')
    assert not any(call['kind'] == 'browser' for call in job['calls'])
    compiler = next(call for call in job['calls'] if call['kind'] == 'interpreter')
    assert compiler['status'] == 'completed' and compiler['serviceSessionId'] == 'rejected-compile'
    record = s.ctx.get('design', s.design['id'])
    assert record['lastExecutionId'] == job['id'] and record['activeJobId'] is None and record['variants'] == []
    path = f"/designs/{record['id']}"
    for actor in ['alice', 'carol', 'dana']:
        status, value, _ = authorized_call(s.ctx.host, 'GET', path, actor=actor, project=s.ctx.project_id)
        assert status == 200, value
        assert value['failure']['diagnostics'] == ['Synthetic type mismatch']
        assert value['failure']['approved'] is False and 'src/App.tsx' in value['failure']['files']
    status, value, _ = authorized_call(s.ctx.host, 'GET', path, actor='bob', project=s.ctx.project_id)
    assert status == 200 and value['failure'] is None
    assert authorized_call(s.ctx.host, 'POST', path+'/approve', {'version': record['version'],
        'artifactId': queued['artifactId'], 'sourceHash': 'a'*64, 'bundleHash': 'b'*64}, project=s.ctx.project_id)[0] == 404
    # The final response check still fences the bytes: changing a stored output hash cannot return diagnostics.
    last_failed=next(row for row in reversed(job['stages']) if row['stage']=='compile' and row['status']=='failed')
    transfer = next(row for row in job['transfers'] if row['key']==last_failed['result']['build']['key'])
    original = s.storage.get_blob
    clock = s.storage.clock
    now = clock()
    def expire_after_read(key, *args, **kwargs):
        raw = original(key, *args, **kwargs)
        if key == transfer['key']:
            s.storage.clock = lambda: now + 3601000
        return raw
    monkeypatch.setattr(s.storage, 'get_blob', expire_after_read)
    status, denied, _ = authorized_call(s.ctx.host, 'GET', path, project=s.ctx.project_id)
    assert status == 401 and 'failure' not in denied
    s.storage.clock = clock
    monkeypatch.setattr(s.storage, 'get_blob', lambda key, *a, **kw: b'{}' if key == transfer['key'] else original(key, *a, **kw))
    assert authorized_call(s.ctx.host, 'GET', path, project=s.ctx.project_id)[0] == 404


def test_design_git_receipt_is_persisted_without_legacy_guard_reports(pipeline, repo, monkeypatch):
    from test_design_execution import test_exact_ux_approval_rebuilds_and_produces_identical_handoff as handoff
    refused = []
    original = pipeline.storage._report
    def reported(*args, **kwargs):
        refused.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(pipeline.storage, '_report', reported)
    handoff(pipeline, repo)
    design = pipeline.ctx.get('design', pipeline.design['id'])
    release = pipeline.ctx.get('release', design['releases'][0]['id'])
    assert release['git']['status'] == 'committed' and release['git']['commitSha']
    assert not refused
