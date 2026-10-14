const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {build}=require('esbuild');
const {chromium}=require('playwright');

test('independent criteria retain retry identity and compare exact current artifact without approval', {timeout:60000}, async()=>{
 const root=path.resolve(__dirname,'..'), posts=[], errors=[], source='a'.repeat(64), dist='b'.repeat(64);
 const bundle=await build({stdin:{contents:`import React from 'react';import{createRoot}from'react-dom/client';import DesignReferencePanel from './src/workspace/DesignReferencePanel';import{WorkspaceScope}from'./src/workspace/WorkspaceScope';import{createWorkspaceClient}from'./src/workspace/client';createRoot(document.getElementById('root')).render(<WorkspaceScope.Provider value={{client:createWorkspaceClient({projectId:'p-test'}),role:'owner'}}><DesignReferencePanel designId="design-1" variant={{artifactId:'artifact-1',sourceHash:'${source}',bundleHash:'${dist}',jobId:'job-1',variant:0,passed:true,verification:{verdict:'pass',approvable:true,findingCount:0,criticalCount:0},designRevision:1,runId:'run-1'}} flow={{screens:['intro','done'],terminals:['done']}} cases={[{eligible:true}]} canRegenerate={true} onRegenerate={()=>{window.__regenerated=(window.__regenerated||0)+1;}} onDirty={()=>{}}/></WorkspaceScope.Provider>);`,resolveDir:root,loader:'tsx'},bundle:true,write:false,format:'iife',jsx:'automatic',define:{'process.env.NODE_ENV':'"development"'},plugins:[{name:'auth',setup(b){b.onResolve({filter:/(^|\/)lib$/},()=>({path:'auth',namespace:'stub'}));b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:"export const auth={token:'synthetic-token',studioToken:null};",loader:'js'}));}}]});
 const browser=await chromium.launch({executablePath:process.env.WORKSPACE_CHROMIUM,headless:true,args:['--no-sandbox']});
 let saved=false, first=true;
 const reference={id:'reference-1',title:'금리 기준',criteriaHash:'c'.repeat(64),baselineScore:{passed:['criterion-1'],failed:[],incomplete:[],blocked:false}};
 try{
  const context=await browser.newContext({offline:true});
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url()),p=url.pathname.replace(/^\/studio-api/,'');
   const send=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(p==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>'});
   if(p==='/app.js')return route.fulfill({contentType:'application/javascript',body:bundle.outputFiles[0].text});
   assert.equal(route.request().headers()['x-workspace-project'],'p-test');
   if(route.request().method()==='GET')return send({references:saved?[reference]:[]});
   const body=route.request().postDataJSON();posts.push({p,body});
   if(p==='/designs/reference-sets'){
    assert.deepEqual(Object.keys(body).sort(),['artifactId','bundleHash','designId','golden','requestId','sourceHash','title']);
    assert.deepEqual(body.golden,[{id:'criterion-1',case:{eligible:true},mustShow:[{screen:'intro',text:'연 2.0%'}],mustNotShow:[{screen:'*',text:'가입 완료'}],endsAt:'done'}]);
    if(first){first=false;return send({error:'기준 저장 결과를 다시 확인하세요.'},503);}
    saved=true;return send({reference},201);
   }
   assert.equal(p,'/designs/reference-sets/reference-1/compare');
   assert.equal(body.criteriaHash,reference.criteriaHash);
   assert.deepEqual(Object.keys(body).sort(),['artifactId','bundleHash','criteriaHash','designId','requestId','sourceHash']);
   return send({comparison:{comparison:{improved:[],regressed:['criterion-1'],blocked:false},
    beforeScore:{rate:1},afterScore:{rate:0},approvalEffect:false}},201);
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));await page.goto('https://reference.test/');
  await page.getByText('기준 화면 세트로 온톨로지 변경 비교',{exact:true}).click();
  await page.getByText('사람이 정한 독립 기준 저장',{exact:true}).click();
  await page.getByLabel('기준 세트 이름').fill('금리 기준');
  await page.getByLabel('반드시 보여야 할 문구').fill('연 2.0%');
  await page.getByLabel('이 사례의 전체 경로에서 보이면 안 되는 문구').fill('가입 완료');
  await page.getByRole('button',{name:'이 독립 기준 추가'}).click();
  await page.getByText('필수 문구: 연 2.0%',{exact:true}).waitFor();
  await page.getByRole('button',{name:'현재 관찰과 기준 세트 고정'}).click();
  await page.getByRole('alert').filter({hasText:'기준 저장 결과를 다시 확인하세요.'}).waitFor();
  await page.getByRole('button',{name:'현재 관찰과 기준 세트 고정'}).click();
  await page.getByLabel('고정한 기준 세트').selectOption('reference-1');
  assert.equal(posts[0].body.requestId,posts[1].body.requestId);
  await page.getByRole('button',{name:'현재 설계 기준으로 시안 재생성'}).click();
  assert.equal(await page.evaluate(()=>window.__regenerated),1);
  await page.getByRole('button',{name:'현재 시안을 같은 기준으로 비교'}).click();
  await page.getByText('개선 0개 · 회귀 1개',{exact:true}).waitFor();
  assert.equal(posts[2].body.artifactId,'artifact-1');assert.equal(posts[2].body.sourceHash,source);assert.equal(posts[2].body.bundleHash,dist);
  assert(!('approve' in posts[2].body));assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
