const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {build}=require('esbuild');
const {chromium}=require('playwright');

test('procedure canvas retains request identity, exact approvals, sandboxed selection and verified handoff', {timeout:120000}, async()=>{
 const root=path.resolve(__dirname,'..');
 const model='global.anthropic.claude-sonnet-5';
 const data=Buffer.from('synthetic handoff bytes');const sha=createHash('sha256').update(data).digest('hex');
 let design=null, firstCreate=true, invalidChunk=true, downloaded=0;const calls=[],errors=[];
 const variant={artifactId:'artifact-1',jobId:'exec-1',variant:0,passed:true,sourceHash:'a'.repeat(64),bundleHash:'b'.repeat(64),verification:{verdict:'pass',findings:[]}};
 let release={id:'release-1',version:1,status:'ready',sourceHash:variant.sourceHash,bundleHash:variant.bundleHash,approvalHash:'c'.repeat(64)};
 const bundle=await build({stdin:{contents:`import React from 'react';import {createRoot} from 'react-dom/client';import DesignPanel from './src/workspace/DesignPanel';import {WorkspaceScope} from './src/workspace/WorkspaceScope';import {createWorkspaceClient} from './src/workspace/client';const client=createWorkspaceClient({projectId:'p-test'});createRoot(document.getElementById('root')).render(<WorkspaceScope.Provider value={{client,project:{id:'p-test',name:'합성',version:1,members:{alice:{role:'owner'}}},role:'owner',actorId:'alice'}}><DesignPanel config={{models:[{id:'${model}',label:'Claude Sonnet 5'}],defaultModel:'${model}'}} onDirty={()=>{}}/></WorkspaceScope.Provider>);`,resolveDir:root,loader:'tsx'},bundle:true,write:false,format:'iife',jsx:'automatic',loader:{'.css':'empty'},define:{'process.env.NODE_ENV':'"development"'},plugins:[{name:'auth',setup(b){b.onResolve({filter:/(^|\/)lib$/},()=>({path:'auth',namespace:'stub'}));b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:`export const auth={token:'synthetic-token',studioToken:null};`,loader:'js'}));}}]});
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
   if(p==='/designs/design-1/confirm-prd'){assert.match(body.hash,/^[a-f0-9]{64}$/);design={...design,version:3,status:'flow-review',approvals:{prd:{}}};return send({design});}
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
  const frame=page.frameLocator('iframe[title="선택한 시안 미리보기"]');await frame.getByRole('button',{name:'신청'}).waitFor();
  assert.equal(await page.locator('iframe').getAttribute('sandbox'),'allow-scripts');
  await page.getByRole('button',{name:'화면에서 수정 요소 선택'}).click();await frame.getByRole('button',{name:'신청'}).click();
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
