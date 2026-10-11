"""Only completed, current same-actor execution sessions supply Memory continuity."""
import hashlib
import hmac
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_execution_integration import integrated, env, api, run
from test_agentcore_workflow import Events
from ontology_runtime.memory import Memory
from workspace.execution_source import submit


class NamespaceKey:
    def generate_mac(self,**kwargs):
        assert kwargs['MacAlgorithm']=='HMAC_SHA_256'
        return {'Mac':hmac.digest(b'synthetic-namespace',kwargs['Message'],'sha256')}


def test_later_session_reads_only_completed_same_source_context(integrated):
    s=integrated
    events=Events()
    s.workflow.memory=Memory(events,'memory',NamespaceKey(),'namespace','organization')
    assert run(s)['status']=='succeeded'
    first=s.storage.get(s.owner,'job',s.job_id)
    response=submit(s.ctx,{'requestId':'memory-second','name':'source-analysis','collectionDecisionId':s.decision['id']},
                    s.ledger,s.config,lambda *a:None)
    s.job_id=response['job']['id'];s.artifact_id=response['artifact']['id']
    assert run(s)['status']=='succeeded'
    second=s.storage.get(s.owner,'job',s.job_id)
    context=next(row for row in second['stages'] if row['stage']=='context')['outputs'][0]
    value=json.loads(s.storage.get_blob(context['key']))
    assert value['continuity']['events'] and value['continuity']['authority'] is False
    assert first['memoryContextHash']==second['memoryContextHash']
    index=s.storage.list_page(s.owner,'exec_memory')['items']
    assert {row['executionId'] for row in index}=={first['id'],second['id']}
    assert len(events.events)==2
