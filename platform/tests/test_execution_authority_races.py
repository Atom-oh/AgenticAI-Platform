"""All existing authority predicates remain enforced when an operation is retried."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import pytest
from test_execution_integration import integrated, env, api, run
from ontology_runtime.execution_protocol import ExecutionAccess
from ontology_runtime.capability import AuthorizationDenied
from workspace.execution_ledger import LedgerError


@pytest.mark.parametrize('membership_changed',[False,True])
def test_project_race_keeps_every_authority_check(integrated,monkeypatch,membership_changed):
    s=integrated
    original=ExecutionAccess.authorize
    calls=[]
    def authorize(self,owner,identifier):
        result=original(self,owner,identifier)
        calls.append(result)
        if len(calls)==2:
            project=s.storage.get(s.owner,'project',s.env.pid)
            changed={**project,'name':'benign title change'}
            if membership_changed:
                changed['members']={k:v for k,v in project['members'].items() if k!='alice'}
            s.storage.put(s.owner,'project',changed,project['version'])
        return result
    monkeypatch.setattr(ExecutionAccess,'authorize',authorize)
    if membership_changed:
        with pytest.raises((AuthorizationDenied,LedgerError)):
            run(s)
        assert not s.invocations
        assert s.storage.get(s.owner,'ontology','project-current') is None
    else:
        result=run(s)
        assert result['status']=='succeeded' and len(s.invocations)==1
