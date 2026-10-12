const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {build}=require('esbuild');
const {chromium}=require('playwright');

test('exact design discussion survives retry and reopening without mixing anchors', {timeout:60000}, async()=>{
 const root=path.resolve(__dirname,'..'), submissions=[], errors=[];
 const flow={flow:{screens:['screen-1']},contract:{rules:[{id:'rule-1',title:'필수 고지'}]}};
 const bundle=await build({stdin:{contents:`import React from 'react';import {createRoot} from 'react-dom/client';import DesignDiscussion from './src/workspace/DesignDiscussion';import {WorkspaceScope} from './src/workspace/WorkspaceScope';import {createWorkspaceClient} from './src/workspace/client';createRoot(document.getElementById('root')).render(<WorkspaceScope.Provider value={{client:createWorkspaceClient({projectId:'p-test'}),project:{id:'p-test',members:{alice:{role:'owner',displayName:'기획 담당자'}}},role:'owner',actorId:'alice'}}><DesignDiscussion designId="design-1" prdHash="${'a'.repeat(64)}" flowHash="${'b'.repeat(64)}" flow={${JSON.stringify(flow)}} variant={{artifactId:'artifact-1',sourceHash:'${'c'.repeat(64)}',bundleHash:'${'d'.repeat(64)}'}} screenIds={['screen-1']} onDirty={()=>{}}/></WorkspaceScope.Provider>);`,resolveDir:root,loader:'tsx'},bundle:true,write:false,format:'iife',jsx:'automatic',define:{'process.env.NODE_ENV':'"development"'},plugins:[{name:'auth',setup(b){b.onResolve({filter:/(^|\/)lib$/},()=>({path:'auth',namespace:'stub'}));b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:"export const auth={token:'synthetic-token',studioToken:null};",loader:'js'}));}}]});
 const browser=await chromium.launch({executablePath:process.env.WORKSPACE_CHROMIUM,headless:true,args:['--no-sandbox']});
 let first=true, comments=[];
 try{
  const context=await browser.newContext({offline:true});
  await context.route('**/*',async route=>{
   const url=new URL(route.request().url());
   const send=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(url.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div id="root"></div><script src="/app.js"></script>'});
   if(url.pathname==='/app.js')return route.fulfill({contentType:'application/javascript',body:bundle.outputFiles[0].text});
   assert.equal(url.pathname,'/studio-api/designs/design-1/comments');
   assert.equal(route.request().headers()['x-workspace-project'],'p-test');
   if(route.request().method()==='GET'){
    const anchor=JSON.parse(url.searchParams.get('anchor'));
    return send({comments:comments.filter(item=>JSON.stringify(item.anchor)===JSON.stringify(anchor))});
   }
   const body=route.request().postDataJSON();submissions.push(body);
   if(first){first=false;return send({error:'저장 결과를 다시 확인하세요.'},503);}
   const comment={id:'comment-1',author:'alice',text:body.text,anchor:body.anchor,createdAt:1791804000000};
   comments=[comment];return send({comment},201);
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.goto('https://discussion.test/');
  await page.getByText('PRD·흐름·화면에 의견 남기기',{exact:true}).click();
  const choice=page.getByLabel('의견을 연결할 근거');
  assert.equal(await choice.locator('option').count(),4);
  await page.getByLabel('설계 의견 내용').fill('원문 자격 조건을 함께 확인해주세요.');
  assert(await choice.isDisabled());
  await page.getByRole('button',{name:'선택한 근거에 의견 남기기'}).click();
  await page.getByText('저장 결과를 다시 확인하세요.').waitFor();
  await page.getByRole('button',{name:'선택한 근거에 의견 남기기'}).click();
  await page.getByText('기획 담당자',{exact:true}).waitFor();
  assert.equal(submissions[0].requestId,submissions[1].requestId);
  assert.deepEqual(submissions[1].anchor,{kind:'prd',id:'prd',sha256:'a'.repeat(64)});
  assert.equal(await page.getByLabel('설계 의견 내용').inputValue(),'');
  const response=page.waitForResponse(value=>value.url().includes('/comments?'));
  const screenOption=await choice.locator('option').filter({hasText:'선택한 시안'}).getAttribute('value');
  await choice.selectOption(screenOption);await response;
  await page.getByText('원문 자격 조건을 함께 확인해주세요.',{exact:true}).waitFor({state:'hidden'});
  assert.equal(await page.locator('.ws-comments li').count(),0);
  await page.getByLabel('설계 의견 내용').fill('화면 고지 문구 검토');
  await page.getByRole('button',{name:'선택한 근거에 의견 남기기'}).click();
  await page.getByText('화면 고지 문구 검토',{exact:true}).waitFor();
  assert.deepEqual(submissions[2].anchor,{kind:'screen',id:'screen-1',artifactId:'artifact-1',
    sourceHash:'c'.repeat(64),bundleHash:'d'.repeat(64)});
  await page.getByText('PRD·흐름·화면에 의견 남기기',{exact:true}).click();
  const reopened=page.waitForResponse(value=>value.url().includes('/comments?'));
  await page.getByText('PRD·흐름·화면에 의견 남기기',{exact:true}).click();await reopened;
  await page.getByText('화면 고지 문구 검토',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
