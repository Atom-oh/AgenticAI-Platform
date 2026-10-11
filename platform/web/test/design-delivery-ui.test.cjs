const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {build}=require('esbuild');
const {chromium}=require('playwright');

async function panelBundle(root,model,handoff=false){return build({stdin:{contents:`import React from 'react';import {createRoot} from 'react-dom/client';import DesignPanel from './src/workspace/DesignPanel';import DesignHandoff from './src/workspace/DesignHandoff';import {WorkspaceScope} from './src/workspace/WorkspaceScope';import {createWorkspaceClient} from './src/workspace/client';const client=createWorkspaceClient({projectId:'p-test'});createRoot(document.getElementById('root')).render(<WorkspaceScope.Provider value={{client,project:{id:'p-test',name:'합성',version:1,members:{alice:{role:'owner'}}},role:'owner',actorId:'alice'}}>${handoff ? '<DesignHandoff designId="design-1" releaseIds={["release-1"]}/>' : `<DesignPanel config={{models:[{id:'${model}',label:'Claude Sonnet 5'}],defaultModel:'${model}'}} onDirty={()=>{}}/>`}</WorkspaceScope.Provider>);`,resolveDir:root,loader:'tsx'},bundle:true,write:false,format:'iife',jsx:'automatic',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"development"'},plugins:[{name:'auth',setup(b){b.onResolve({filter:/(^|\/)lib$/},()=>({path:'auth',namespace:'stub'}));b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:`export const auth={token:'synthetic-token',studioToken:null};`,loader:'js'}));}}]});}

test('procedure canvas retains request identity, exact approvals, sandboxed selection and verified handoff', {timeout:120000}, async()=>{
 const root=path.resolve(__dirname,'..');
 const model='global.anthropic.claude-sonnet-5';
 const data=Buffer.from('synthetic handoff bytes');const sha=createHash('sha256').update(data).digest('hex');
 let design=null, firstCreate=true, invalidChunk=true, downloaded=0;const calls=[],errors=[];
 const variant={artifactId:'artifact-1',jobId:'exec-1',variant:0,passed:true,sourceHash:'a'.repeat(64),bundleHash:'b'.repeat(64),verification:{verdict:'pass',findings:[]}};
 let release={id:'release-1',version:1,status:'ready',sourceHash:variant.sourceHash,bundleHash:variant.bundleHash,approvalHash:'c'.repeat(64)};
 const bundle=await panelBundle(root,model);
 const browser=await chromium.launch({executablePath:process.env.WORKSPACE_CHROMIUM,headless:true,args:['--no-sandbox','--disable-background-networking']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},offline:true,acceptDownloads:true});
  await context.route('**/*',async r=>{
   const url=new URL(r.request().url()),method=r.request().method();
   if(url.pathname==='/')return r.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>'});
   if(url.pathname==='/app.js')return r.fulfill({contentType:'application/javascript; charset=utf-8',body:bundle.outputFiles[0].text});
   const p=url.pathname.replace(/^\/studio-api/,'');const body=method==='POST'?r.request().postDataJSON():null;
   const send=(value,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
   calls.push({p,method,body,project:r.request().headers()['x-workspace-project']});
   if(p==='/designs/config')return send({enabled:true,models:[model]});
   if(p==='/designs/inputs')return send({inputs:[{id:'adm-1',sourceId:'doc-1',kind:'document-pages',pageCount:1}]});
   if(p==='/documents')return send({documents:[{id:'doc-1',title:'합성 상품 설명',approvedRevisionId:'r1'}]});
   if(p==='/ontology')return send({nodes:[{id:'procedure-1',title:'신청 절차',type:'Procedure',reviewState:'approved'}]});
   if(p==='/git-connections')return send({connections:[]});
   if(p==='/designs'&&method==='GET')return send({designs:design?[design]:[]});
   if(p==='/designs'&&method==='POST'){
    if(firstCreate){firstCreate=false;return send({error:'일시적 오류'},503);}
    assert.equal(body.model,model);assert.deepEqual(body.decisionIds,['adm-1']);
    design={id:'design-1',title:body.title,version:1,revision:1,status:'draft',model,variants:[],approvals:{}};return send({design},201);
   }
   const detail=()=>({design,prd:design.status==='draft'?null:{prd:{productName:{value:'합성 적금'}},issues:[]},flow:design.approvals.prd?{flow:{screens:['s1'],transitions:[]},issues:[],contract:{rules:[{id:'rule-1',title:'필수 동작 확인'}]}}:null,flowHash:'d'.repeat(64)});
   if(p==='/designs/design-1'&&method==='GET')return send(detail());
   if(p==='/designs/design-1/extract'){design={...design,version:2,status:'prd-review'};return send({design});}
   if(p==='/designs/design-1/confirm-prd'){assert.equal(body.hash,'d524bd8abdb2f9512e6772787ca4e71488dd5eb1aa443f51131609606723d17d');design={...design,version:3,status:'flow-review',approvals:{prd:{}}};return send({design});}
   if(p==='/designs/design-1/approve-flow'){assert.equal(body.hash,'d'.repeat(64));design={...design,version:4,status:'ready',approvals:{prd:{},flow:{}}};return send({design});}
   if(p==='/designs/design-1/generate'){design={...design,version:5,status:'review',variants:[variant]};return send({design});}
   if(p==='/designs/design-1/artifacts/artifact-1')return send({previewHtml:'<html><body><button data-testid="cta">신청</button></body></html>',verification:variant.verification,targets:[{screenId:'s1',state:'default',targetId:'node-1',testId:'cta',label:'신청 버튼'}],sourceHash:variant.sourceHash,bundleHash:variant.bundleHash});
   if(p==='/designs/design-1/approve'){assert.equal(body.sourceHash,variant.sourceHash);assert.equal(body.bundleHash,variant.bundleHash);design={...design,version:6,status:'approved',approvals:{...design.approvals,ux:{artifactId:'artifact-1'}}};return send({design});}
   if(p==='/designs/design-1/release'){design={...design,version:7,status:'released',releases:[{id:'release-1',sourceHash:variant.sourceHash,bundleHash:variant.bundleHash}]};return send({design});}
   if(p==='/designs/design-1/releases/release-1'){
    if(!url.searchParams.has('kind'))return send({release});
    return send({data:data.toString('base64'),offset:0,total:data.length,sha256:sha,chunkHash:invalidChunk?'0'.repeat(64):sha,complete:true});
   }
   if(p==='/designs/design-1/releases/release-1/accept'){assert.equal(body.sourceHash,release.sourceHash);release={...release,version:2,frontendAcceptance:{actor:'alice',sourceHash:release.sourceHash,bundleHash:release.bundleHash}};return send({design});}
   errors.push(p);return send({error:'unexpected route'},404);
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));page.on('download',()=>downloaded++);await page.goto('https://design.test/');
  await page.getByLabel('설계 이름').fill('완성 흐름');await page.getByLabel('검토된 업무 절차').selectOption('procedure-1');
  await page.getByText('합성 상품 설명 · 1쪽').check();
  await page.getByRole('button',{name:'설계 시작',exact:true}).click();await page.getByText('일시적 오류').waitFor();
  await page.getByRole('button',{name:'설계 시작',exact:true}).click();await page.getByRole('button',{name:'문서에서 PRD 정리'}).click();
  await page.getByRole('button',{name:'이 PRD 확정'}).click();await page.getByRole('button',{name:'이 흐름과 확인 기준 승인'}).click();
  await page.getByRole('button',{name:'시안 만들기',exact:true}).click();await page.getByRole('button',{name:'시안 1 · 검증 통과'}).click();
  const outer=page.frameLocator('iframe[title="선택한 시안 미리보기"]');const frame=outer.frameLocator('iframe[data-workspace-preview-content]');await frame.getByRole('button',{name:'신청'}).waitFor();
  assert.equal(await page.locator('iframe').getAttribute('sandbox'),'allow-scripts');
  await page.getByRole('button',{name:'화면에서 수정 요소 선택'}).click();
  assert.equal(await outer.locator('iframe').getAttribute('sandbox'),'allow-scripts');
  assert.equal(await page.locator('iframe').getAttribute('referrerpolicy'),'no-referrer');
  assert.match(await frame.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content'),/connect-src 'none'/);
  await frame.getByRole('button',{name:'신청'}).click();
  await page.waitForFunction(()=>document.querySelector('select') && [...document.querySelectorAll('label')].some(label=>label.textContent.includes('수정 대상') && label.querySelector('select')?.value==='0'));
  assert.equal(await page.getByLabel('수정 대상').inputValue(),'0');
  await page.getByRole('button',{name:'이 시안 승인',exact:true}).click();await page.getByRole('button',{name:'승인 소스 재빌드·전달 준비'}).click();
  await page.getByRole('button',{name:'전달 ZIP 다운로드'}).click();await page.getByText('전달 조각의 무결성을 확인하지 못했습니다.').waitFor();assert.equal(downloaded,0);
  invalidChunk=false;const event=page.waitForEvent('download');await page.getByRole('button',{name:'전달 ZIP 다운로드'}).click();await event;
  await page.getByRole('button',{name:'이 전달 버전 개발자 확인'}).click();await page.getByRole('button',{name:'개발자 확인 완료'}).waitFor();
  assert(await page.getByRole('button',{name:'등록된 저장소로 전달'}).isDisabled());
  const created=calls.filter(c=>c.p==='/designs'&&c.method==='POST');assert.equal(created.length,2);assert.equal(created[0].body.requestId,created[1].body.requestId);
  assert(calls.every(c=>c.project==='p-test'));assert(!calls.some(c=>c.p==='/runs'));assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});


test('paginated designs keep preview and instruction approval bound to the viewed variant', {timeout:60000}, async()=>{
 const model='global.anthropic.claude-sonnet-5', bundle=await panelBundle(path.resolve(__dirname,'..'),model);
 const a={artifactId:'a',jobId:'ja',variant:0,passed:true,sourceHash:'a'.repeat(64),bundleHash:'b'.repeat(64),verification:{verdict:'pass'}};
 const b={...a,artifactId:'b',variant:1,sourceHash:'c'.repeat(64),bundleHash:'d'.repeat(64)};
 const design={id:'design-6',title:'여섯째 설계',version:1,revision:1,status:'review',model,variants:[a,b],approvals:{flow:{}}};
 let finishA,startedA,wrong=false,admitted=false;const waitingA=new Promise(resolve=>startedA=resolve),held=new Promise(resolve=>finishA=resolve);
 const browser=await chromium.launch({executablePath:process.env.WORKSPACE_CHROMIUM,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({offline:true});
  await context.route('**/*',async r=>{
   const u=new URL(r.request().url()),p=u.pathname.replace(/^\/studio-api/,'');
   const send=value=>r.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(p==='/')return r.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>'});
   if(p==='/app.js')return r.fulfill({contentType:'application/javascript; charset=utf-8',body:bundle.outputFiles[0].text});
   if(p==='/designs/config')return send({enabled:true,models:[model]});
   if(p==='/documents')return send({documents:[]});if(p==='/ontology')return send({nodes:[]});if(p==='/git-connections')return send({connections:[]});
   if(p==='/designs/inputs')return send({inputs:admitted?[{id:'instruction-1',kind:'prompt-text'}]:[]});
   if(p==='/designs')return send(u.searchParams.has('cursor')?{designs:[design]}:{designs:Array.from({length:5},(_,i)=>({...design,id:'design-'+i,title:'앞선 설계 '+i})),cursor:'opaque-page'});
   if(p==='/designs/design-6')return send({design});
   if(p==='/designs/instructions'){admitted=true;return send({decision:{id:'instruction-1',status:'admitted'}});}
   if(p.includes('/artifacts/')){
    const v=p.endsWith('/a')?a:b;
    if(v===a){startedA();await held;}
    return send({previewHtml:'<button data-testid="cta">'+v.artifactId+'</button>',verification:v.verification,
     sourceHash:wrong?'0'.repeat(64):v.sourceHash,bundleHash:v.bundleHash,targets:[{screenId:'s',state:'default',targetId:'t',testId:'cta',label:'대상'}]}).catch(()=>{});
   }
   throw new Error('Unexpected route '+p);
  });
  const page=await context.newPage();await page.goto('https://regression.test/');
  await page.getByLabel('진행 중인 설계').selectOption('design-6');
  await page.getByRole('button',{name:'시안 1 · 검증 통과',exact:true}).click();await waitingA;
  assert(await page.getByRole('button',{name:'이 시안 승인',exact:true}).isDisabled());
  await page.getByRole('button',{name:'시안 2 · 검증 통과',exact:true}).click();
  const preview=page.frameLocator('iframe[title="선택한 시안 미리보기"]').frameLocator('iframe[data-workspace-preview-content]');
  await preview.getByRole('button',{name:'b',exact:true}).waitFor();finishA();
  await page.getByText('선택한 부분 수정',{exact:true}).click();
  await page.getByLabel('수정 대상').selectOption('0');await page.getByLabel('수정 지시문',{exact:true}).fill('첫 번째 지시');
  await page.getByRole('button',{name:'수정 지시문 사용 검토 요청'}).click();await page.getByText('수정 지시문 승인됨',{exact:true}).waitFor();
  assert(!(await page.getByRole('button',{name:'선택 영역 수정·재검증'}).isDisabled()));
  await page.getByLabel('수정 지시문',{exact:true}).fill('다른 지시');
  assert(await page.getByRole('button',{name:'선택 영역 수정·재검증'}).isDisabled());
  assert.equal(await page.getByText('수정 지시문 승인됨',{exact:true}).count(),0);
  assert.equal(await preview.getByRole('button',{name:'b',exact:true}).count(),1);
  wrong=true;await page.getByRole('button',{name:'시안 2 · 검증 통과',exact:true}).click();
  await page.getByText('선택한 시안과 표시할 검증 근거가 다릅니다.',{exact:true}).waitFor();
  assert(await page.getByRole('button',{name:'이 시안 승인',exact:true}).isDisabled());
  assert.equal(await page.locator('iframe[title="선택한 시안 미리보기"]').count(),0);
 }finally{finishA?.();await browser.close();}
});


test('design Git delivery displays queued, verified and delivered-unverified receipts in place', {timeout:60000}, async()=>{
 const bundle=await panelBundle(path.resolve(__dirname,'..'),'global.anthropic.claude-sonnet-5',true);
 const sourceHash='a'.repeat(64),commitSha='f'.repeat(40),branch='feature/release-1';
 const release={id:'release-1',version:1,status:'ready',sourceHash,bundleHash:'b'.repeat(64),approvalHash:'c'.repeat(64),frontendAcceptance:{actor:'alice',sourceHash,bundleHash:'b'.repeat(64)}};
 let exported=false,finished=false,unverified=false,posts=0;
 const receipt=()=>({status:unverified?'delivered-unverified':finished?'committed':'queued',sourceHash,
   ...(finished?{commitSha,branch,baseSha:'e'.repeat(40),criteriaCurrent:true}:{})});
 const browser=await chromium.launch({executablePath:process.env.WORKSPACE_CHROMIUM,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({offline:true});
  await context.route('**/*',r=>{
   const p=new URL(r.request().url()).pathname.replace(/^\/studio-api/,'');
   const send=value=>r.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(p==='/')return r.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>'});
   if(p==='/app.js')return r.fulfill({contentType:'application/javascript; charset=utf-8',body:bundle.outputFiles[0].text});
   if(p==='/git-connections')return send({connections:[{id:'registered',label:'합성 저장소',repository:'synthetic/delivery',visibility:'private',connectionHash:'d'.repeat(64)}]});
   if(p==='/designs/design-1/releases/release-1')return send({release:{...release,...(exported?{git:receipt()}:{})}});
   if(p==='/releases/release-1/git'){
    const body=r.request().postDataJSON();assert.equal(body.connectionHash,'d'.repeat(64));assert.equal(body.confirmPublic,false);
    posts++;exported=true;return send({job:{id:'git-job',status:'queued'},release:{...release,git:receipt()},export:{id:'export-1'}});
   }
   if(p==='/jobs/git-job')return send({job:{id:'git-job',status:finished?'completed':'queued'}});
   throw new Error('Unexpected route '+p);
  });
  const page=await context.newPage();await page.goto('https://git-delivery.test/');
  await page.getByLabel('등록된 Git 전달 대상').selectOption('registered');
  await page.getByRole('button',{name:'등록된 저장소로 전달'}).click();await page.getByText('Git 전달 대기',{exact:true}).waitFor();
  finished=true;await page.getByText('Git 커밋 확인됨',{exact:true}).waitFor();
  assert.equal(await page.getByText(commitSha,{exact:true}).count(),1);assert.equal(await page.getByText(branch,{exact:true}).count(),1);
  unverified=true;await page.getByRole('button',{name:'Git 결과 새로 조회'}).click();await page.getByText('Git 전달 결과 확인 필요',{exact:true}).waitFor();
  assert.equal(await page.getByText('Git 커밋 확인됨',{exact:true}).count(),0);
  assert.equal(await page.getByText(commitSha,{exact:true}).count(),1);assert.equal(posts,1);
 }finally{await browser.close();}
});
