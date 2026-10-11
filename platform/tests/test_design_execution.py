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
from test_workspace_git_export import repo, git
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
                        value=copy.deepcopy(payload['target']) if 'target' in payload else {'verdicts':{item['id']:{'verdict':'pass','evidence':'synthetic review'} for item in payload['items']}}
                    return outer.refs.encode(json.dumps(value,ensure_ascii=False)),{'inputTokens':12,'outputTokens':24}
            monkeypatch.setattr(gate,'adapter',lambda *a,**kw:Adapter())
            return super().generate(system,user,on_token)
    state.workflow=DesignWorkflow(Gateway(),Interpreter(),Browser(),ReceiptSigner(kms,config['evidenceKeyArn'],'evidence'),model_factory=Model)
    return state


def execute(pipeline_state,stage,**fields):
    from workbench.service import Service
    pipeline_state.ctx=Service(pipeline_state.ctx.host,pipeline_state.ctx.scope,pipeline_state.ctx.claims)
    current=pipeline_state.ctx.get('design',pipeline_state.design['id'])
    queued=submit(pipeline_state.ctx,current['id'],stage,{'requestId':'execution-'+stage,'version':current['version'],**fields},
                  pipeline_state.ledger,pipeline_state.config,lambda *a:None)
    job=pipeline_state.ledger.dispatcher().allocate(pipeline_state.ctx.owner,queued['jobId'],operation_id='allocate-'+stage+'a'*24)
    token,claims,key=pipeline_state.caps.issue(job)
    pipeline_state.ledger.dispatcher().bind_capability(pipeline_state.ctx.owner,job['id'],job['attempt']['id'],job['fence'],
        digest=hashlib.sha256(token.encode()).hexdigest(),claims=claims,key_check=key,operation_id='bind-'+stage+'b'*24)
    event={'capability':token,'executionId':job['id'],'runtimeSessionId':job['attempt']['sessionId'],
           'workloadIdentity':'workload','operation':job['operation']}
    result=pipeline_state.workflow.run(event,job['attempt']['sessionId'])
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


def test_exact_ux_approval_rebuilds_and_produces_identical_handoff(pipeline, repo):
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
    call = authorized_call
    from workspace.worker import Worker
    from workspace.git_service import connection_hash
    host=s.ctx.host
    for path in [f"/runs/{run['id']}",f"/releases/{release['id']}"]:
        status, payload, _=call(host,'GET',path,project=s.ctx.project_id)
        assert status==200, payload
    denied=call(host,'POST',f"/releases/{release['id']}/git",{},actor='dana',project=s.ctx.project_id)
    assert denied[0]==409 and denied[1]['code']=='frontend-acceptance-required', denied
    status,payload,_=call(host,'POST',f"/designs/{record['id']}/releases/{release['id']}/accept",
        {'version':release['version'],'sourceHash':release['sourceHash'],'bundleHash':release['bundleHash'],'note':'offline verification'},
        actor='dana',project=s.ctx.project_id)
    assert status==200,payload
    bare,connection,base,_=repo
    worker=Worker(storage=s.storage)
    worker.intake_denylist_loader=host.intake_denylist_loader
    host.git_connections=worker.git_connections=lambda:{connection['id']:connection}
    body={'confirmPublic':True,'requestId':'design-git-test','connectionId':connection['id'],'connectionHash':connection_hash(connection)}
    status,payload,_=call(host,'POST',f"/releases/{release['id']}/git",body,actor='dana',project=s.ctx.project_id)
    assert status==202,payload
    event={'owner':s.ctx.owner,'jobId':payload['job']['id']}
    done=worker.handle(event)
    assert done['status']=='completed',s.storage.get(s.ctx.owner,'job',event['jobId'])
    exported=s.ctx.get('gitexport',payload['export']['id'])
    assert exported['status']=='committed' and exported['criteriaCurrent'] is True,exported
    assert git(bare,'show',f"{exported['commitSha']}:generated/studio/{run['id']}/src/App.tsx")==source['src/App.tsx']
    assert git(bare,'rev-parse','refs/heads/main').decode().strip()==base
    repeated=call(host,'POST',f"/releases/{release['id']}/git",body,actor='dana',project=s.ctx.project_id)
    assert repeated[0]==202 and repeated[1]['export']['commitSha']==exported['commitSha'],repeated
    for actor in ['bob','carol']:
        assert call(host,'POST',f"/releases/{release['id']}/git",{**body,'requestId':'denied-'+actor},actor=actor,project=s.ctx.project_id)[0]==403


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


def test_large_failed_browser_evidence_remains_inspectable_only_by_round_editors(pipeline, monkeypatch):
    call = authorized_call
    s=pipeline
    decide(s.ctx,s.design['id'],'prd',{'version':s.design['version'],'prd':s.prd,'hash':schema.digest(s.prd)})
    record=s.ctx.get('design',s.design['id'])
    decide(s.ctx,record['id'],'flow',{'version':record['version'],'hash':record['flow']['sha256']})
    evaluate=s.workflow.browser.evaluate
    def failing(*args,**kwargs):
        report=evaluate(*args,**kwargs)
        report['passed']=False
        report['accessibility'].update(status='fail',violations=[{'id':'contrast','impact':'serious','description':'x'*1200} for _ in range(30)])
        return report
    monkeypatch.setattr(s.workflow.browser,'evaluate',failing)
    result,_=execute(s,'generate')
    assert result['status']=='needs_changes'
    record=s.ctx.get('design',s.design['id']);variant=record['variants'][0]
    path=f"/designs/{record['id']}/artifacts/{variant['artifactId']}"
    for actor in ['alice','carol','dana']:
        for query in [{},{'kind':'source'},{'kind':'bundle'},{'kind':'screenshot'}]:
            status, payload, _=call(s.ctx.host,'GET',path,actor=actor,project=s.ctx.project_id,query=query)
            assert status==200,payload
    for query in [{},{'kind':'source'},{'kind':'bundle'},{'kind':'screenshot'}]:
        actual=call(s.ctx.host,'GET',path,actor='bob',project=s.ctx.project_id,query=query)
        missing=call(s.ctx.host,'GET',f"/designs/{record['id']}/artifacts/missing",actor='bob',project=s.ctx.project_id,query=query)
        assert actual[:2]==missing[:2] and actual[0]==404,(actual,missing)
    job=s.storage.get(s.ctx.owner,'job',variant['jobId'])
    assert all(len(json.dumps(stage['result']).encode())<=8192 for stage in job['stages'])


def authorized_call(host, method, path, body=None, *, actor="alice", project=None, query=None):
    import base64
    event={"rawPath":"/studio-api"+path,"headers":{"X-Workspace-Project":project},
        "requestContext":{"http":{"method":method},"authorizer":{"jwt":{"claims":{
            "sub":actor,"token_use":"access","exp":host.storage.clock()//1000+3600}}}},
        "queryStringParameters":query or {}}
    if body is not None:event["body"]=json.dumps(body)
    response=host.handle(event)
    payload=base64.b64decode(response["body"]) if response.get("isBase64Encoded") else json.loads(response["body"])
    return response["statusCode"],payload,response.get("headers",{})


@pytest.mark.parametrize("move_neighbour", [False, True])
def test_edit_rechecks_layout_outside_the_exact_selected_subtree(pipeline, monkeypatch, move_neighbour):
    from intake import prompts, review
    from workspace.design_api import read_object
    from workspace.design_stages import unpacked, knowledge
    from design_loop.composition import walk
    from design_loop.convention import Registry
    from design_loop.edit import subtree_testids
    s=pipeline
    decide(s.ctx,s.design['id'],'prd',{'version':s.design['version'],'prd':s.prd,'hash':schema.digest(s.prd)})
    record=s.ctx.get('design',s.design['id'])
    decide(s.ctx,record['id'],'flow',{'version':record['version'],'hash':record['flow']['sha256']})
    assert execute(s,'generate')[0]['status']=='succeeded'
    record=s.ctx.get('design',s.design['id']);variant=record['variants'][0]
    artifact=s.ctx.get('wb_artifact',variant['artifactId'])
    generated=read_object(s.ctx,artifact['result'])['generated']
    key,composition=next(iter(unpacked(generated['screens']).items()))
    target=next(node['id'] for _,node,_ in walk(composition))
    k=knowledge(read_object(s.ctx,record['input'])['snapshot'])
    excluded=subtree_testids(composition,target,Registry(k,state=generated['registry']))
    if move_neighbour:
        evaluate=s.workflow.browser.evaluate
        def shifted(*args,**kwargs):
            report=evaluate(*args,**kwargs)
            for boxes in report['layoutBoxes'].values():
                other=next((identifier for identifier in boxes if identifier not in excluded),None)
                if other is not None:
                    boxes[other][0]+=2
                    break
            return report
        monkeypatch.setattr(s.workflow.browser,'evaluate',shifted)
    decision=prompts.admit_prompt_text(s.ctx.host,s.ctx.scope,'선택한 요소를 유지하세요.',purpose='edit-instruction',claims=s.ctx.claims)
    review.decide(s.ctx.host,s.ctx.collaboration.resolve_scope('bob',s.ctx.project_id),decision['id'],approve=True,reason='synthetic instruction')
    result,_=execute(s,'edit',baseArtifactId=variant['artifactId'],instructionDecisionId=decision['id'],
        screenId=key[0],state=key[1],targetId=target)
    assert result['status']==('needs_changes' if move_neighbour else 'succeeded')
    edited=s.ctx.get('design',s.design['id'])['variants'][-1]
    assert edited['verification']['layout']['stable'] is not move_neighbour


def test_design_cancel_rejects_other_requester_and_clears_active_pointer(pipeline, monkeypatch):
    from ontology_runtime import execution_entrypoints
    s=pipeline;record=s.ctx.get('design',s.design['id'])
    queued=submit(s.ctx,record['id'],'extract',{'requestId':'cancel-test','version':record['version']},
                  s.ledger,s.config,lambda *a:None)
    monkeypatch.setattr(execution_entrypoints,'dependencies',lambda:(s.ctx.host,s.config,s.ledger,None,None,None,None))
    path=f"/designs/{record['id']}/cancel"
    denied=authorized_call(s.ctx.host,'POST',path,{},actor='bob',project=s.ctx.project_id)
    assert denied[0]==403 and denied[1]['code']=='forbidden',denied
    done=authorized_call(s.ctx.host,'POST',path,{},project=s.ctx.project_id)
    assert done[0]==200,done
    assert done[1]['design']['activeJobId'] is None
    assert s.storage.get(s.ctx.owner,'job',queued['jobId'])['status']=='cancelled'


def test_pre_model_boundary_refusal_records_known_zero_usage(monkeypatch):
    from workspace.execution_source import encode
    class Session:
        job={'executionId':'test'}
        outcomes=[]
        def call(self,name,payload):return {'clean':True,'sha256':hashlib.sha256(encode(payload)).hexdigest()}
        def intent(self,*args,**kwargs):return 'model-call'
        def outcome(self,call_id,**kwargs):self.outcomes.append((call_id,kwargs))
    monkeypatch.setenv('BEDROCK_READ_TIMEOUT','180')
    def refuse(*args,**kwargs):raise gate.GateRefused(['PHONE'],1,{},'design.execute')
    monkeypatch.setattr(gate,'generate',refuse)
    session=Session();model=DesignModel(session,{'model':'global.anthropic.claude-sonnet-5'})
    with pytest.raises(gate.GateRefused):model.generate('system','synthetic content')
    assert session.outcomes==[('model-call',{'status':'failed','usage':{'inputTokens':0,'outputTokens':0}})]
