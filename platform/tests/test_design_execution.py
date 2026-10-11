"""Production ledger/API flow with real crypto, compiler and Browser; AWS transports substituted."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent))
import pytest
from test_design_api import design_state, env, api
from test_execution_integration import RSAKms
from workspace import ontology_schema as schema
from workspace.design_api import decide
from workspace.design_execution import submit
from workspace.execution_ledger import CostGuardGate, Ledger
from workspace.execution_sources import ExecutionSources
from workspace.execution_verifier import KmsVerifier, KEY_OWNER
from ontology_runtime.execution_protocol import ExecutionCapabilities, ReceiptSigner
from ontology_runtime.execution_tools import ExecutionTools
from ontology_runtime.design_workflow import DesignWorkflow
from ontology_runtime.design_model import DesignModel
from design_loop.references import References
from design_loop.react_project import compile_local
from workspace.browser import evaluate_bundle
from engine import gate


@pytest.fixture
def pipeline(design_state, monkeypatch):
    ctx, design, prd = design_state
    storage = ctx.storage;storage.single_attempt = True
    charges = {}
    monkeypatch.setattr(CostGuardGate, 'check', lambda *a, **kw: None)
    monkeypatch.setattr(CostGuardGate, 'record', lambda self, tokens, charge_id: charges.setdefault(charge_id, tokens))
    monkeypatch.setenv('BEDROCK_READ_TIMEOUT','180')
    kms = RSAKms()
    for identifier,purpose in [('cap','execution-capability'),('evidence','runtime-evidence')]:
        arn='arn:aws:kms:ap-northeast-2:000000000000:key/'+identifier
        storage.put(KEY_OWNER,'ac_key',{'id':identifier,'purpose':purpose,'keyArn':arn,'algorithm':'RS256','state':'active',
            'notBefore':storage.clock()//1000-60,'notAfter':storage.clock()//1000+86400,
            'publicKeyHash':hashlib.sha256(kms.get_public_key(KeyId=arn)['PublicKey']).hexdigest()})
    storage.put(KEY_OWNER,'ac_key',{'id':'gateway-binding','gatewayId':'gateway','targetId':'target','workloadIdentityArn':'workload'})
    config={'protocol':'platform-execution/1','revision':'design-test','capabilityKeyId':'cap','evidenceKeyId':'evidence',
        'evidenceKeyArn':'arn:aws:kms:ap-northeast-2:000000000000:key/evidence','workloadIdentityArn':'workload',
        'interpreter':{'archiveHash':'a'*64,'analyzerCodeHash':'b'*64,'dependencyLockHash':'c'*64},
        'design':{'projects':[ctx.project_id],'models':[design['model']]}}
    verifier=KmsVerifier(storage,kms,['evidence']);caps=ExecutionCapabilities(storage,kms,config)
    ledger=Ledger.production(storage,verifier=verifier,sources=ExecutionSources(ctx.host))
    tools=ExecutionTools(ctx.host,verifier,caps,config)
    metadata=SimpleNamespace(client_context=SimpleNamespace(custom={'bedrockAgentCoreGatewayId':'gateway',
        'bedrockAgentCoreTargetId':'target','bedrockAgentCoreToolName':'ontology___execution'}))
    state=SimpleNamespace(ctx=ctx,design=design,prd=prd,storage=storage,ledger=ledger,caps=caps,config=config,
                          models=[],charges=charges,compiles=0,browsers=0)
    class Gateway:
        def call(self,name,arguments,*,capability,operation_id):
            assert name=='ontology___execution'
            return tools.invoke({**arguments,'_executionAuthorization':{'capability':capability,'operationId':operation_id}},metadata)
    class Interpreter:
        def __init__(self):self.config=config['interpreter']
        def execute(self,operation,payload):
            assert operation=='compile';state.compiles+=1
            build=compile_local(payload)
            return {'build':build,'execution':{'backend':'agentcore-code-interpreter','sourceExecuted':False,
                'sessionId':'compiler-'+str(state.compiles),'toolArchiveHash':'a'*64,'analyzerCodeHash':'b'*64,
                'dependencyLockHash':'c'*64,'inputHash':schema.digest(payload)}}
    class Browser:
        def evaluate(self,build,contract,reference=None,visual_tolerance=0.15,text_scale=1.0):
            import base64
            state.browsers+=1
            result=evaluate_bundle({n:base64.b64decode(v) for n,v in build['files'].items()},contract,reference,
                visual_tolerance,expected_hash=build['bundleHash'],text_scale=text_scale)
            result['browserExecution']={'sessionId':'browser-'+str(state.browsers),'backend':'agentcore-browser'}
            return result
    class Model(DesignModel):
        def generate(self,system,user,on_token=None):
            outer=self
            class Adapter:
                model_id=design['model'];tier='0/1';endpoint='offline-converse'
                def generate(self,system,user,**kwargs):
                    state.models.append(system)
                    if 'product requirements document' in system:
                        value=copy.deepcopy(prd)
                    else:
                        payload=json.loads(outer.refs.decode(user))
                        value={'verdicts':{item['id']:{'verdict':'pass','evidence':'synthetic review'} for item in payload['items']}}
                    return outer.refs.encode(json.dumps(value,ensure_ascii=False)),{'inputTokens':12,'outputTokens':24}
            monkeypatch.setattr(gate,'adapter',lambda *a,**kw:Adapter())
            return super().generate(system,user,on_token)
    state.workflow=DesignWorkflow(Gateway(),Interpreter(),Browser(),ReceiptSigner(kms,config['evidenceKeyArn'],'evidence'),model_factory=Model)
    return state


def execute(state,stage,**fields):
    from workbench.service import Service
    state.ctx=Service(state.ctx.host,state.ctx.scope,state.ctx.claims)
    current=state.ctx.get('design',state.design['id'])
    queued=submit(state.ctx,current['id'],stage,{'requestId':'execution-'+stage,'version':current['version'],**fields},
                  state.ledger,state.config,lambda *a:None)
    job=state.ledger.dispatcher().allocate(state.ctx.owner,queued['jobId'],operation_id='allocate-'+stage+'a'*24)
    token,claims,key=state.caps.issue(job)
    state.ledger.dispatcher().bind_capability(state.ctx.owner,job['id'],job['attempt']['id'],job['fence'],
        digest=hashlib.sha256(token.encode()).hexdigest(),claims=claims,key_check=key,operation_id='bind-'+stage+'b'*24)
    event={'capability':token,'executionId':job['id'],'runtimeSessionId':job['attempt']['sessionId'],
           'workloadIdentity':'workload','operation':job['operation']}
    result=state.workflow.run(event,job['attempt']['sessionId'])
    return result,queued


def test_cited_extraction_uses_real_boundary_ledger_and_atomic_publication(pipeline):
    s=pipeline
    result,queued=execute(s,'extract')
    assert result['status']=='succeeded'
    design=s.ctx.get('design',s.design['id'])
    assert design['status']=='prd-review' and not design['activeJobId']
    assert len(s.models)==1 and sum(s.charges.values())==36
    assert s.ctx.get('wb_artifact',queued['artifactId'])['status']=='completed'


def test_approved_flow_generates_actual_verified_react_artifact(pipeline):
    s=pipeline
    current=decide(s.ctx,s.design['id'],'prd',{'version':s.design['version'],'prd':s.prd,'hash':schema.digest(s.prd)})
    record=s.ctx.get('design',s.design['id'])
    decide(s.ctx,record['id'],'flow',{'version':record['version'],'hash':record['flow']['sha256']})
    result,queued=execute(s,'generate')
    design=s.ctx.get('design',s.design['id'])
    summary=design['variants'][0]['verification'] if design.get('variants') else {}
    assert result['status']=='succeeded', {'unavailable':summary.get('unavailable'), 'roles':summary.get('roles'),
        'findings':[{k:f.get(k) for k in ['code','role','severity']} for f in summary.get('findings',[])]}
    assert design['status']=='review' and design['variants'][0]['passed']
    assert s.compiles==1 and s.browsers==2 and s.models
    run=s.ctx.get('run',design['variants'][0]['runId'])
    assert run['rounds'][0]['passed'] and run['executionId']==queued['jobId']


def test_exact_ux_approval_rebuilds_and_produces_identical_handoff(pipeline):
    from workspace.design_api import approve_variant
    from design_loop.handoff import read_zip
    from workspace.releases import approved_artifacts
    s=pipeline
    decide(s.ctx,s.design['id'],'prd',{'version':s.design['version'],'prd':s.prd,'hash':schema.digest(s.prd)})
    record=s.ctx.get('design',s.design['id'])
    decide(s.ctx,record['id'],'flow',{'version':record['version'],'hash':record['flow']['sha256']})
    result,_=execute(s,'generate')
    assert result['status']=='succeeded'
    record=s.ctx.get('design',s.design['id']);variant=record['variants'][0]
    approve_variant(s.ctx,record['id'],{'version':record['version'],'artifactId':variant['artifactId'],
        'sourceHash':variant['sourceHash'],'bundleHash':variant['bundleHash']})
    calls=len(s.models)
    result,_=execute(s,'release',baseArtifactId=variant['artifactId'])
    assert result['status']=='succeeded' and len(s.models)==calls
    record=s.ctx.get('design',s.design['id']);release=s.ctx.get('release',record['releases'][0]['id'])
    assert release['status']=='ready' and s.compiles==2 and s.browsers==4
    package=read_zip(s.storage.get_blob(release['handoffKey']))
    source=read_zip(s.storage.get_blob(release['sourceKey']))
    assert {key.removeprefix('project/'):value for key,value in package.items() if key.startswith('project/')}==source
    assert 'convention/apply.cjs' in package and 'verification-report.json' in package
    run=s.ctx.get('run',variant['runId'])
    approved_artifacts(s.storage,s.ctx.owner,run,1,release['approvalHash'])


def test_duplicate_design_submission_returns_same_job_and_legacy_worker_refuses(pipeline):
    from workspace.storage import ReservedRecord
    from workbench.service import Service
    s=pipeline;record=s.ctx.get('design',s.design['id'])
    body={'requestId':'once-only','version':record['version']}
    first=submit(s.ctx,record['id'],'extract',body,s.ledger,s.config,lambda *a:None)
    ctx=Service(s.ctx.host,s.ctx.scope,s.ctx.claims)
    repeated=submit(ctx,record['id'],'extract',body,s.ledger,s.config,lambda *a:None)
    assert first['jobId']==repeated['jobId'] and first['artifactId']==repeated['artifactId']
    job=s.storage.get(s.ctx.owner,'job',first['jobId'])
    with pytest.raises(ReservedRecord):
        s.storage.put(s.ctx.owner,'job',{**job,'status':'completed'},job['version'])
