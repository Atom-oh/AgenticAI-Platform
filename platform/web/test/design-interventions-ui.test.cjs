const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {build}=require('esbuild');
const {chromium}=require('playwright');

test('human intervention view keeps rejection identity and rejects mismatched evidence', {timeout:60000}, async()=>{
 const root=path.resolve(__dirname,'..'), submissions=[], errors=[];
 const source='a'.repeat(64), bundleHash='b'.repeat(64);
 const bundle=await build({stdin:{contents:`import React,{useState} from 'react';import {createRoot} from 'react-dom/client';import DesignInterventions from './src/workspace/DesignInterventions';import {WorkspaceScope} from './src/workspace/WorkspaceScope';import {createWorkspaceClient} from './src/workspace/client';function App(){const [other,setOther]=useState(false);return <WorkspaceScope.Provider value={{client:createWorkspaceClient({projectId:'p-test'}),project:{members:{alice:{displayName:'디자인 담당자'}}},role:'owner',actorId:'alice'}}><button onClick={()=>setOther(true)}>다른 시안 보기</button><DesignInterventions key={String(other)} designId="design-1" version={8} variant={{artifactId:other?'artifact-other':'artifact-1',sourceHash:'${source}',bundleHash:'${bundleHash}'}} onReviewed={()=>{window.__reviewCount=(window.__reviewCount||0)+1;}} onDirty={()=>{}}/></WorkspaceScope.Provider>;}createRoot(document.getElementById('root')).render(<App/>);`,resolveDir:root,loader:'tsx'},bundle:true,write:false,format:'iife',jsx:'automatic',define:{'process.env.NODE_ENV':'"development"'},plugins:[{name:'auth',setup(b){b.onResolve({filter:/(^|\/)lib$/},()=>({path:'auth',namespace:'stub'}));b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:"export const auth={token:'synthetic-token',studioToken:null};",loader:'js'}));}}]});
 const browser=await chromium.launch({executablePath:process.env.WORKSPACE_CHROMIUM,headless:true,args:['--no-sandbox']});
 let first=true, rejected=false;
 try{
  const context=await browser.newContext({offline:true});
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url()),p=url.pathname.replace(/^\/studio-api/,'');
   const send=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(p==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>'});
   if(p==='/app.js')return route.fulfill({contentType:'application/javascript',body:bundle.outputFiles[0].text});
   assert.equal(route.request().headers()['x-workspace-project'],'p-test');
   if(p.endsWith('/interventions')){
    const other=p.includes('artifact-other');
    return send({artifactId:other?'artifact-other':'artifact-1',sourceHash:other?'0'.repeat(64):source,bundleHash,
     coverage:'partial',editRequests:1,rejectionCount:rejected?1:0,screenLabels:{s1:'신청 화면'},
     events:[{id:'edit-1',kind:'edit',actor:'alice',outcome:'submitted',scopes:[{screenId:'s1',screenTitle:'신청 화면',targetTitle:'안내 문구',state:'default'}]}],
     failureChecklist:[{id:'check-1',code:'notice-missing',severity:'critical',rounds:[1],resolved:false}],
     rejectionCandidates:rejected?[{eventId:'reject-1',queueLabel:'업무 근거 연결 필요',nodes:[{title:'고지 문구를 명확하게'}]}]:[]});
   }
   assert.equal(p,'/designs/design-1/reject');
   const body=route.request().postDataJSON();submissions.push(body);
   if(first){first=false;return send({error:'반려 기록을 다시 확인하세요.'},503);}
   rejected=true;return send({design:{id:'design-1',version:9}},200);
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto('https://intervention.test/');
  await page.getByText('사람의 수정·반려와 개선 후보',{exact:true}).click();
  await page.getByText('부분 수정 요청 1회 · 반려 0회',{exact:true}).waitFor();
  await page.getByText('신청 화면 · 안내 문구 · 기본',{exact:true}).waitFor();
  await page.getByText('계측 도입 이전의 개입은 모두 확인할 수 없습니다. 기록된 이력만 표시합니다.',{exact:true}).waitFor();
  await page.getByLabel('시안 반려 사유').fill('고지 문구를 명확하게');
  await page.getByRole('button',{name:'이 시안 반려·보강 후보 기록'}).click();
  await page.getByText('반려 기록을 다시 확인하세요.').waitFor();
  await page.getByRole('button',{name:'이 시안 반려·보강 후보 기록'}).click();
  await page.getByText('부분 수정 요청 1회 · 반려 1회',{exact:true}).waitFor();
  await page.getByRole('heading',{name:'온톨로지 보강 후보 · 업무 근거 연결 필요'}).waitFor();
  assert.equal(submissions[0].requestId,submissions[1].requestId);
  assert.equal(submissions[1].version,8);assert.equal(submissions[1].artifactId,'artifact-1');
  assert.equal(submissions[1].sourceHash,source);assert.equal(submissions[1].bundleHash,bundleHash);
  assert.equal(await page.evaluate(()=>window.__reviewCount),1);
  await page.getByRole('button',{name:'다른 시안 보기'}).click();
  await page.getByText('부분 수정 요청 1회 · 반려 1회',{exact:true}).waitFor({state:'hidden'});
  const summary=page.getByText('사람의 수정·반려와 개선 후보',{exact:true});
  if(await summary.locator('..').getAttribute('open')===null)await summary.click();
  await page.getByRole('alert').filter({hasText:'선택한 시안의 검토 기준이 변경되었습니다.'}).waitFor();
  await page.getByLabel('시안 반려 사유').fill('근거가 다른 시안에는 반려를 기록하지 않습니다.');
  assert(await page.getByRole('button',{name:'이 시안 반려·보강 후보 기록'}).isDisabled());
  assert.equal(await page.getByText('부분 수정 요청 1회 · 반려 1회',{exact:true}).count(),0);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
