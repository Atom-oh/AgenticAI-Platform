/* Agentic AI Platform SPA */
'use strict';

/* ---------------- auth (Cognito hosted UI, PKCE) ---------------- */
const AUTH = {
  domain: 'https://agentic-platform-2rhe1tko.auth.ap-northeast-2.amazoncognito.com',
  clientId: '3o8u65rhccnr1ug1f94tctmb0b',
  redirect: location.origin + '/',
};
const $ = s => document.querySelector(s);
const app = $('#app');
let ME = null;

function b64url(buf){return btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
async function login(){
  const v = b64url(crypto.getRandomValues(new Uint8Array(48)));
  sessionStorage.setItem('pkce', v);
  const ch = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(v)));
  location.href = `${AUTH.domain}/oauth2/authorize?response_type=code&client_id=${AUTH.clientId}` +
    `&redirect_uri=${encodeURIComponent(AUTH.redirect)}&scope=openid+email+profile` +
    `&code_challenge=${ch}&code_challenge_method=S256`;
}
function logout(){
  sessionStorage.clear();
  location.href = `${AUTH.domain}/logout?client_id=${AUTH.clientId}` +
    `&logout_uri=${encodeURIComponent(AUTH.redirect)}`;
}
async function exchangeCode(code){
  const body = new URLSearchParams({grant_type:'authorization_code', client_id:AUTH.clientId,
    code, redirect_uri:AUTH.redirect, code_verifier:sessionStorage.getItem('pkce')||''});
  const r = await fetch(`${AUTH.domain}/oauth2/token`,{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'}, body});
  if(!r.ok) throw new Error('토큰 교환 실패');
  const t = await r.json();
  sessionStorage.setItem('idToken', t.id_token);
  history.replaceState({},'',location.pathname + location.hash);
}
function token(){return sessionStorage.getItem('idToken')||'';}
function claims(){
  try{ return JSON.parse(atob(token().split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))); }
  catch(e){ return null; }
}

/* ---------------- api ---------------- */
async function api(method, path, body){
  const r = await fetch(path, {method,
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+token()},
    body: body ? JSON.stringify(body) : undefined});
  if(r.status===401){ sessionStorage.removeItem('idToken'); renderLogin(true); throw new Error('세션이 만료되었습니다. 다시 로그인해 주세요.'); }
  const d = await r.json().catch(()=>({error:'응답 파싱 실패'}));
  if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
  return d;
}
function toast(msg, err){const t=$('#toast');t.textContent=msg;
  t.className='toast'+(err?' err':'');t.style.display='block';
  clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',4200);}
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
function fmt(n){return (n||0).toLocaleString();}

/* ---------------- shell / router ---------------- */
const VIEWS = [
  {id:'home', ic:'◈', label:'홈 · 커버리지'},
  {id:'catalog', ic:'🗂', label:'에이전트 카탈로그'},
  {id:'builder', ic:'🛠', label:'새 에이전트'},
  {id:'workflows', ic:'⛓', label:'워크플로우'},
  {id:'skills', ic:'⚡', label:'스킬'},
  {id:'knowledge', ic:'📚', label:'지식 · 데이터'},
];
function route(){ return location.hash.replace(/^#\/?/,'') || 'home'; }
function nav(v){ location.hash = '#/'+v; }
window.addEventListener('hashchange', render);

function shell(inner){
  const r = route().split('/')[0];
  app.innerHTML = `
  <div class="shell">
    <nav class="rail">
      <div class="brand">AGENTIC <em>AI</em></div>
      <div class="sub">PLATFORM CONTROL ROOM</div>
      ${VIEWS.map(v=>`<button data-v="${v.id}" class="${r===v.id?'on':''}">
        <span class="ic">${v.ic}</span><span class="tx">${v.label}</span></button>`).join('')}
      ${ME&&ME.isAdmin?`<div class="sect">PLATFORM ENGINEER</div>
      <button data-v="admin" class="${r==='admin'?'on':''}"><span class="ic">⚙</span><span class="tx">플랫폼 운영</span></button>`:''}
      <div class="foot">
        <a href="https://www.atomai.click/AgenticAI-Platform/" target="_blank">가이드북 ↗</a><br>
        <a href="https://www.atomai.click/AgenticAI-Platform/demo-architecture.html" target="_blank">아키텍처 ↗</a>
      </div>
    </nav>
    <main>
      <div class="topbar">
        <h1 id="vtitle"></h1><div class="sp"></div>
        ${ME&&ME.profile?`<span class="chip"><span class="dot"></span>${esc(ME.profile.name)} · 잔여연차 <b>${ME.profile.leaveRemain}일</b></span>`:''}
        <span class="chip ${ME&&ME.isAdmin?'admin':''}"><span class="dot"></span>
          <b>${esc(ME?ME.email:'')}</b>&nbsp;${esc(ME?ME.team:'')}</span>
        <button class="btn ghost" id="lo">로그아웃</button>
      </div>
      <div class="content" id="view">${inner||''}</div>
    </main>
  </div>`;
  document.querySelectorAll('.rail button[data-v]').forEach(b=>b.onclick=()=>nav(b.dataset.v));
  $('#lo').onclick = logout;
}
function setTitle(t){ const e=$('#vtitle'); if(e) e.textContent=t; }

/* ---------------- login view ---------------- */
function renderLogin(expired){
  app.innerHTML = `
  <div class="login">
    <div class="mark">AGENTIC AI</div>
    <div class="tag">AgentCore 기반 사내 에이전트 플랫폼 · 관제실</div>
    <div class="panel">
      ${expired?'<p style="color:var(--amber)">세션이 만료되었습니다.</p>':''}
      <p>에이전트를 만들고, 승인하고, 지식과 연결하고, 관측하는 곳입니다.<br>
      조직 계정으로 로그인해 주세요. 가입은 플랫폼 관리자의 초대로만 가능합니다.</p>
      <button class="btn p" id="li" style="width:100%;padding:12px">로그인</button>
      <div class="hint">demo: demo@atomai.click / !234Qwer</div>
    </div>
  </div>`;
  $('#li').onclick = login;
}

/* ---------------- home: coverage constellation ---------------- */
const KIND_COLOR = {team:'#F5A524', agent:'#38E1C9', skill:'#A78BFA', datasource:'#6C9BF2',
  ontology:'#3ED598', workflow:'#F272B6', gateway:'#E8EDF9'};
async function renderHome(){
  setTitle('커버리지');
  const view = $('#view');
  view.innerHTML = `
    <div class="hero">
      <canvas id="gc" height="430"></canvas>
      <div class="hud"><div class="eyebrow">LIVE COVERAGE</div>
        <h2>에이전트 성좌</h2><p id="ghint">불러오는 중…</p></div>
      <div class="legend">${Object.entries({team:'팀',agent:'에이전트',skill:'스킬',
        datasource:'데이터소스',ontology:'온톨로지',workflow:'워크플로우'})
        .map(([k,l])=>`<span><i style="background:${KIND_COLOR[k]}"></i>${l}</span>`).join('')}</div>
    </div>
    <div class="kpis" id="kp"></div>
    <div class="notice cy">이 화면이 플랫폼의 출발점입니다 — <b>어느 팀이 어떤 에이전트를 어떤 지식(데이터소스·온톨로지·스킬) 위에서 운영하는지</b>를 한눈에 봅니다. 연결선이 없는 에이전트는 근거 없이 답하고 있다는 신호입니다.</div>`;
  let g;
  try{ g = await api('GET','/api/graph'); }
  catch(e){ $('#ghint').textContent = e.message; return; }
  $('#ghint').textContent = `${g.coverage.agents}개 에이전트 · ${g.coverage.teams}개 팀 · 고아 노드 ${g.coverage.orphans}`;
  $('#kp').innerHTML = `
    <div class="kpi cy"><span>에이전트</span><b>${g.coverage.agents}</b></div>
    <div class="kpi amber"><span>팀</span><b>${g.coverage.teams}</b></div>
    <div class="kpi"><span>노드</span><b>${g.nodes.length}</b></div>
    <div class="kpi"><span>연결</span><b>${g.edges.length}</b></div>`;
  drawGraph($('#gc'), g);
}

function drawGraph(cv, g){
  const dpr = window.devicePixelRatio||1;
  const W = cv.clientWidth, H = 430;
  cv.width = W*dpr; cv.height = H*dpr;
  const ctx = cv.getContext('2d'); ctx.scale(dpr,dpr);
  const N = g.nodes.map((n,i)=>({...n,
    x: W/2 + Math.cos(i*2.399)*Math.min(W,H)*.31,
    y: H/2 + Math.sin(i*2.399)*H*.31, vx:0, vy:0}));
  const idx = Object.fromEntries(N.map((n,i)=>[n.id,i]));
  const E = g.edges.map(e=>({a:idx[e.from], b:idx[e.to], rel:e.rel}))
    .filter(e=>e.a!=null&&e.b!=null);
  // force layout
  for(let it=0; it<220; it++){
    for(const e of E){const a=N[e.a],b=N[e.b];
      const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,f=(d-110)*.004;
      a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}
    for(let i=0;i<N.length;i++)for(let j=i+1;j<N.length;j++){
      const a=N[i],b=N[j],dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy||1;
      if(d2<28000){const f=520/d2;const d=Math.sqrt(d2);
        a.vx-=dx/d*f;a.vy-=dy/d*f;b.vx+=dx/d*f;b.vy+=dy/d*f;}}
    for(const n of N){
      n.vx+=(W/2-n.x)*.0012; n.vy+=(H/2-n.y)*.0018;
      n.x=Math.max(30,Math.min(W-30,n.x+n.vx)); n.y=Math.max(40,Math.min(H-30,n.y+n.vy));
      n.vx*=.82;n.vy*=.82;}
  }
  let hover=null, phase=0;
  function paint(){
    ctx.clearRect(0,0,W,H);
    for(const e of E){const a=N[e.a],b=N[e.b];
      ctx.strokeStyle='rgba(90,110,160,.35)';ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();
      // signal pulse along edges (subtle motion)
      const t=(phase/120+ (e.a*7%10)/10)%1;
      ctx.fillStyle='rgba(56,225,201,.55)';
      ctx.beginPath();ctx.arc(a.x+(b.x-a.x)*t, a.y+(b.y-a.y)*t, 1.6, 0, 7);ctx.fill();}
    for(const n of N){
      const c = KIND_COLOR[n.kind]||'#8B98B8';
      const r = n.kind==='agent'?9:(n.kind==='team'?11:7);
      if(n.kind==='agent'&&n.status==='PENDING'){
        ctx.strokeStyle='#F5A524';ctx.lineWidth=2;
        ctx.beginPath();ctx.arc(n.x,n.y,r+3+(Math.sin(phase/12)+1)*1.4,0,7);ctx.stroke();}
      ctx.fillStyle=c;ctx.beginPath();ctx.arc(n.x,n.y,r,0,7);ctx.fill();
      if(n.kind==='team'){ctx.fillStyle='#0A0E1A';ctx.font='700 9px "Space Grotesk"';
        ctx.textAlign='center';ctx.fillText('T',n.x,n.y+3);}
      ctx.fillStyle=(hover===n)?'#E8EDF9':'#8B98B8';
      ctx.font=(hover===n?'600 ':'')+'10.5px "IBM Plex Sans KR"';ctx.textAlign='center';
      ctx.fillText(n.label.length>14?n.label.slice(0,13)+'…':n.label, n.x, n.y+r+13);}
    phase++;
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches) requestAnimationFrame(paint);
  }
  cv.onmousemove = ev=>{const rc=cv.getBoundingClientRect();
    const mx=ev.clientX-rc.left,my=ev.clientY-rc.top;
    hover=N.find(n=>Math.hypot(n.x-mx,n.y-my)<14)||null;
    cv.style.cursor=hover?'pointer':'grab';};
  cv.onclick = ()=>{ if(hover&&hover.kind==='agent') nav('chat/'+hover.id.split(':')[1]); };
  paint();
}

/* ---------------- lifecycle rail (signature) ---------------- */
function lifecycleRail(a){
  const evalOk = a.eval && a.eval.passed;
  const approved = a.status==='APPROVED';
  const live = a.usage && a.usage.invocations>0;
  const rejected = a.status==='REJECTED';
  const stop=(lit,wait,dead,lb)=>`<div class="stop ${dead?'dead':wait?'wait':lit?'lit':''}">
    <div class="dot"></div><div class="lb">${lb}</div></div>`;
  return `<div class="lrail">
    ${stop(true,false,false,'초안')}
    ${a.status==='EVALUATING'?stop(false,true,false,'평가'):stop(evalOk,false,a.eval&&!a.eval.passed,'평가')}
    ${stop(approved,a.status==='PENDING',rejected,'승인')}
    ${stop(live&&approved,false,false,'운영')}
  </div>`;
}

/* ---------------- catalog ---------------- */
let AGENTS=[];
async function renderCatalog(){
  setTitle('에이전트 카탈로그');
  const view=$('#view');
  view.innerHTML = `<div class="sub">각 팀이 만든 에이전트 목록입니다. 카드의 레일은 수명주기(초안→평가→승인→운영)를 나타내며, 승인된 에이전트만 사용할 수 있습니다.</div>
    <div class="row" style="margin-bottom:16px" id="tf"></div><div class="grid" id="g">불러오는 중…</div>`;
  try{ AGENTS=(await api('GET','/api/agents')).agents; }
  catch(e){ $('#g').innerHTML=`<div class="empty">${esc(e.message)}</div>`; return; }
  const teams=[...new Set(AGENTS.map(a=>a.team).filter(Boolean))];
  let filter='';
  const tf=$('#tf');
  tf.innerHTML = `<button class="btn ghost on-f" data-t="">전체</button>`+
    teams.map(t=>`<button class="btn ghost" data-t="${esc(t)}">${esc(t)}</button>`).join('');
  tf.querySelectorAll('button').forEach(b=>b.onclick=()=>{filter=b.dataset.t;
    tf.querySelectorAll('button').forEach(x=>x.classList.toggle('on-f',x===b));paint();});
  function paint(){
    const list=AGENTS.filter(a=>!filter||a.team===filter);
    const g=$('#g');
    if(!list.length){g.innerHTML='<div class="empty" style="grid-column:1/-1">에이전트가 없습니다. "새 에이전트"에서 빌더와 대화해 만들어 보세요.</div>';return;}
    g.innerHTML='';
    for(const a of list){
      const c=document.createElement('div');c.className='card';
      const badge=a.status==='APPROVED'?'<span class="badge ok">승인됨</span>':
        a.status==='PENDING'?'<span class="badge pend">승인 대기</span>':
        a.status==='EVALUATING'?'<span class="badge pend">평가 중…</span>':'<span class="badge rej">거부됨</span>';
      c.innerHTML=`<div class="row" style="justify-content:space-between"><h3>${esc(a.name)}</h3>${badge}</div>
        ${lifecycleRail(a)}
        <p>${esc(a.description||'설명 없음')}</p>
        <div class="meta">${esc(a.team)} · T${a.riskTier}
          ${a.datasourceIds.length?` · 📚${a.datasourceIds.length}`:''}
          ${a.skillIds.length?` · ⚡${a.skillIds.length}`:''}
          ${a.useOntology?' · ◈온톨로지':''}
          · ${fmt(a.usage.totalTokens)}tok</div>
        <div class="row">
          <button class="btn p" data-use="${a.id}" ${a.status==='APPROVED'?'':'disabled'}>
            ${a.status==='APPROVED'?'사용하기':a.status==='EVALUATING'?'평가 중…':'승인 대기 중'}</button>
          <button class="btn d" data-del="${a.id}">삭제</button></div>`;
      g.appendChild(c);
    }
    g.querySelectorAll('[data-use]:not([disabled])').forEach(b=>
      b.onclick=()=>nav('chat/'+b.dataset.use));
    g.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
      if(!confirm('이 에이전트를 삭제할까요? Registry 레코드도 함께 삭제됩니다.'))return;
      try{await api('DELETE','/api/agents/'+b.dataset.del);toast('삭제됨');renderCatalog();}
      catch(e){toast(e.message,1);}});
  }
  paint();
}

/* ---------------- chat ---------------- */
let SESSION = sessionStorage.getItem('nx-session');
if(!SESSION){SESSION=crypto.randomUUID().replace(/-/g,'')+'w';sessionStorage.setItem('nx-session',SESSION);}
const CHATS={};
function chatUI(title, subtitle, ph){
  $('#view').innerHTML=`<div class="sub">${subtitle}</div>
    <div class="chatbox"><div class="msgs" id="ms"></div>
    <div class="chatin"><input id="ci" placeholder="${esc(ph)}" autocomplete="off">
    <button class="btn p" id="cs">전송</button></div></div>`;
  setTitle(title);
}
function addMsg(cls,text){const d=document.createElement('div');d.className='m '+cls;
  d.textContent=text;$('#ms').appendChild(d);$('#ms').scrollTop=1e9;return d;}
function bindChat(sender){
  const ci=$('#ci'),cs=$('#cs');
  const go=async()=>{const t=ci.value.trim();if(!t)return;ci.value='';cs.disabled=true;
    addMsg('u',t);const w=addMsg('b t','생각 중…');
    try{const d=await sender(t);w.className='m b';w.textContent=d.reply||'(빈 응답)';
      if(d.usage){const m=document.createElement('div');m.className='meta';
        let s=`in ${d.usage.inputTokens} / out ${d.usage.outputTokens} · ${d.latencyMs}ms`;
        if(d.budget)s+=` · budget ${fmt(d.budget.used)}/${fmt(d.budget.total)}`;
        m.textContent=s;w.appendChild(m);}
      return d;
    }catch(e){w.className='m b';w.textContent='오류: '+e.message;}
    finally{cs.disabled=false;ci.focus();}};
  cs.onclick=go;ci.onkeydown=e=>{if(e.key==='Enter')go();};
}
async function renderChat(agentId){
  if(!AGENTS.length){try{AGENTS=(await api('GET','/api/agents')).agents;}catch(e){}}
  const a=AGENTS.find(x=>x.id===agentId);
  if(!a){nav('catalog');return;}
  chatUI('💬 '+a.name,
    `${esc(a.description||'')} <span class="pill">systemPrompt override</span><span class="pill">🔒 툴 차단</span>`+
    (a.skillIds.length?`<span class="pill">⚡스킬 ${a.skillIds.length}</span>`:'')+
    (a.useOntology?'<span class="pill">◈ 온톨로지</span>':'')+
    (a.usePersonalHr?'<span class="pill">🔐 내 인사정보</span>':''),
    '메시지를 입력하세요…');
  const hist=CHATS[agentId]||[];
  hist.forEach(h=>addMsg(h.cls,h.text));
  if(!hist.length)addMsg('b',`안녕하세요! "${a.name}" 입니다. 무엇을 도와드릴까요?`);
  bindChat(async t=>{(CHATS[agentId]=CHATS[agentId]||[]).push({cls:'u',text:t});
    const d=await api('POST','/api/chat',{agentId,message:t,sessionId:SESSION});
    CHATS[agentId].push({cls:'b',text:d.reply});return d;});
}

/* ---------------- builder ---------------- */
let SPEC=null;
function renderBuilder(){
  chatUI('🛠 새 에이전트',
    `빌더와 대화하며 요구사항을 구체화한 뒤 <b>스펙으로 변환</b> → 검토 → 생성 순서로 진행합니다. 생성 시 스모크 평가가 자동 실행되며, Tier 2이거나 평가에 실패하면 승인 대기 상태가 됩니다.`,
    '만들고 싶은 에이전트를 설명해 주세요…');
  const bar=document.createElement('div');
  bar.style.cssText='padding:10px 13px;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center';
  bar.innerHTML=`<button class="btn a" id="sp">📋 대화를 스펙으로 변환</button><span class="sub" style="margin:0" id="sph"></span>`;
  document.querySelector('.chatbox').appendChild(bar);
  addMsg('b','안녕하세요! 어떤 에이전트를 만들고 싶으신가요? 용도와 대상 사용자, 필요한 지식을 알려주시면 질문을 주고받으며 스펙을 함께 완성해 드릴게요.');
  bindChat(t=>api('POST','/api/builder',{message:t,sessionId:SESSION}));
  $('#sp').onclick=async()=>{
    $('#sp').disabled=true;$('#sph').textContent='스펙 추출 중…';
    try{const d=await api('POST','/api/spec',{sessionId:SESSION});SPEC=d.spec;renderCreate();}
    catch(e){toast(e.message,1);$('#sph').textContent='';}
    finally{$('#sp').disabled=false;}};
}
async function renderCreate(){
  setTitle('에이전트 생성 확인');
  const s=SPEC||{name:'',description:'',systemPrompt:''};
  let dss=[],sks=[];
  try{dss=(await api('GET','/api/datasources')).datasources;}catch(e){}
  try{sks=(await api('GET','/api/skills')).skills;}catch(e){}
  $('#view').innerHTML=`<div class="sub">빌더가 정리한 스펙을 검토하고, 지식·스킬을 연결한 뒤 위험 등급을 선택해 생성합니다. 생성 즉시 스모크 평가가 실행됩니다.</div>
    <label>이름</label><input id="an" maxlength="30" value="${esc(s.name)}">
    <label>설명</label><input id="ad" maxlength="120" value="${esc(s.description)}">
    <label>시스템 프롬프트</label><textarea id="ap" rows="9">${esc(s.systemPrompt)}</textarea>
    <label>위험 등급</label>
    <div class="checks">
      <label><input type="radio" name="tier" value="1" checked> Tier 1 — 읽기 전용 Q&A (평가 통과 시 자동 승인)</label>
      <label><input type="radio" name="tier" value="2"> Tier 2 — 민감/외부 데이터 (플랫폼 승인 필요)</label></div>
    <label>데이터소스 연결 (최대 5)</label>
    <div class="checks" id="ac">${dss.length?dss.map(d=>
      `<label><input type="checkbox" value="${d.id}"> 📚 ${esc(d.name)}</label>`).join(''):
      '<span class="sub" style="margin:0">지식·데이터 탭에서 먼저 등록할 수 있습니다.</span>'}</div>
    <label>스킬 연결 (최대 3)</label>
    <div class="checks" id="sc">${sks.length?sks.map(k=>
      `<label><input type="checkbox" value="${k.id}"> ⚡ ${esc(k.name)}</label>`).join(''):
      '<span class="sub" style="margin:0">스킬 탭에서 먼저 만들 수 있습니다.</span>'}</div>
    <label>개인화 · 컨텍스트</label>
    <div class="checks">
      <label><input type="checkbox" id="uo"> ◈ 조직 온톨로지를 컨텍스트로 주입</label>
      <label><input type="checkbox" id="uh"> 🔐 요청자 본인 인사정보 연결 (잔여 연차 등)</label></div>
    <div style="margin-top:20px" class="row">
      <button class="btn p" id="ok">에이전트 생성</button>
      <button class="btn ghost" id="bk">빌더로 돌아가기</button></div>`;
  $('#bk').onclick=()=>nav('builder');
  $('#ok').onclick=async()=>{
    const ds=[...document.querySelectorAll('#ac input:checked')].map(x=>x.value);
    const sk=[...document.querySelectorAll('#sc input:checked')].map(x=>x.value);
    const tier=parseInt(document.querySelector('input[name=tier]:checked').value,10);
    $('#ok').disabled=true;$('#ok').textContent='등록 중…';
    try{await api('POST','/api/agents',{name:$('#an').value,description:$('#ad').value,
      systemPrompt:$('#ap').value,datasourceIds:ds,skillIds:sk,riskTier:tier,
      useOntology:$('#uo').checked,usePersonalHr:$('#uh').checked});
      SPEC=null;
      toast('🧪 등록되었습니다 — 스모크 평가가 백그라운드에서 진행됩니다(15~30초). 카탈로그에서 상태가 갱신됩니다.');
      nav('catalog');}
    catch(e){toast(e.message,1);$('#ok').disabled=false;$('#ok').textContent='에이전트 생성';}};
}

/* ---------------- workflows ---------------- */
async function renderWorkflows(){
  setTitle('워크플로우');
  const view=$('#view');
  view.innerHTML=`<div class="sub">승인된 에이전트를 이어 붙여 실행합니다 — <b>Chain</b>은 앞 단계의 출력을 다음 단계 입력으로 전달하고, <b>Loop</b>는 한 에이전트가 완료(DONE)할 때까지 반복합니다(최대 4회). 실행은 비동기로 진행되며 단계별 기록이 남습니다.</div>
    <div id="list">불러오는 중…</div>
    <h3 class="sec">새 워크플로우</h3>
    <label>이름</label><input id="wn" maxlength="40" placeholder="예: 초안 작성 → 검토 → 요약">
    <label>유형</label>
    <div class="checks">
      <label><input type="radio" name="wt" value="chain" checked> ⛓ Chain — 단계 출력을 다음 단계 입력으로</label>
      <label><input type="radio" name="wt" value="loop"> ↻ Loop — 한 에이전트가 DONE까지 반복 개선</label></div>
    <div id="steps"></div>
    <div class="row" style="margin-top:12px">
      <button class="btn ghost" id="addstep">+ 단계 추가</button>
      <button class="btn p" id="mkwf">워크플로우 생성</button></div>
    <div id="preview"></div>
    <h3 class="sec cy">실행 이력</h3><div id="runs"></div>`;
  let agents=[];
  try{agents=(await api('GET','/api/agents')).agents.filter(a=>a.status==='APPROVED');}catch(e){}
  const stepsEl=$('#steps');
  function addStep(){
    if(stepsEl.children.length>=4){toast('최대 4단계까지 가능합니다',1);return;}
    const n=stepsEl.children.length+1;
    const d=document.createElement('div');
    d.innerHTML=`<label>단계 ${n} — 에이전트 / 지시</label>
      <div class="row"><select style="max-width:260px">${agents.map(a=>
        `<option value="${a.id}">${esc(a.name)} (${esc(a.team)})</option>`).join('')}</select>
      <input placeholder="이 단계에서 할 일 (예: 아래 입력을 요약하라)" style="flex:1"></div>`;
    stepsEl.appendChild(d); paintPreview();
  }
  function readSteps(){return [...stepsEl.children].map(d=>({
    agentId:d.querySelector('select').value,
    instruction:d.querySelector('input').value.trim()}));}
  function paintPreview(){
    const wt=document.querySelector('input[name=wt]:checked').value;
    const st=readSteps();
    $('#preview').innerHTML = st.length?`<div class="wfrail">${st.map((s,i)=>{
      const a=agents.find(x=>x.id===s.agentId);
      return `<div class="wfnode ${wt==='loop'?'wfloop':''}">
        <div class="n">${wt==='loop'?'LOOP':'STEP '+String(i+1).padStart(2,'0')}</div>
        <div class="nm">${esc(a?a.name:'?')}</div>
        <div class="ins">${esc(s.instruction||'지시 없음')}</div></div>`+
        (i<st.length-1?'<div class="wfarrow">─▶</div>':'');
    }).join('')}${wt==='loop'?'<div class="wfarrow">↻ DONE까지</div>':''}</div>`:'';
  }
  stepsEl.addEventListener('input',paintPreview);
  document.querySelectorAll('input[name=wt]').forEach(r=>r.onchange=paintPreview);
  $('#addstep').onclick=addStep;
  if(agents.length)addStep(); else stepsEl.innerHTML='<div class="empty">승인된 에이전트가 없습니다. 먼저 에이전트를 만들고 승인받으세요.</div>';
  $('#mkwf').onclick=async()=>{
    const wt=document.querySelector('input[name=wt]:checked').value;
    try{await api('POST','/api/workflows',{name:$('#wn').value,type:wt,steps:readSteps(),maxIters:3});
      toast('워크플로우 생성됨');renderWorkflows();}catch(e){toast(e.message,1);}};
  // list + runs
  async function paintList(){
    let d;try{d=await api('GET','/api/workflows');}catch(e){$('#list').innerHTML=`<div class="empty">${esc(e.message)}</div>`;return;}
    $('#list').innerHTML = d.workflows.length? d.workflows.map(w=>`
      <div class="card" style="margin-bottom:10px">
        <div class="row" style="justify-content:space-between">
          <h3>${w.type==='loop'?'↻':'⛓'} ${esc(w.name)}</h3>
          <span class="pill">${esc(w.team)}</span></div>
        <div class="wfrail" style="padding:6px 0">${(w.steps||[]).map((s,i)=>{
          return `<div class="wfnode ${w.type==='loop'?'wfloop':''}">
            <div class="n">${w.type==='loop'?'LOOP':'STEP '+String(i+1).padStart(2,'0')}</div>
            <div class="nm">${esc(s.agentName||s.agentId)}</div>
            <div class="ins">${esc(s.instruction||'')}</div></div>`+
            (i<w.steps.length-1?'<div class="wfarrow">─▶</div>':'');}).join('')}</div>
        <div class="row"><input placeholder="실행 입력…" data-in="${w.sk}" style="flex:1">
          <button class="btn p" data-run="${w.sk}">실행</button>
          <button class="btn d" data-del="${w.sk}">삭제</button></div>
      </div>`).join(''):'<div class="empty">워크플로우가 없습니다.</div>';
    $('#list').querySelectorAll('[data-run]').forEach(b=>b.onclick=async()=>{
      const inp=$('#list').querySelector(`[data-in="${b.dataset.run}"]`).value.trim();
      if(!inp){toast('실행 입력을 적어주세요',1);return;}
      try{const r=await api('POST',`/api/workflows/${b.dataset.run}/run`,{input:inp});
        toast('실행 시작 — 이력에서 진행을 확인하세요');pollRun(r.runId);}
      catch(e){toast(e.message,1);}});
    $('#list').querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
      if(!confirm('삭제할까요?'))return;
      try{await api('DELETE','/api/workflows/'+b.dataset.del);paintList();}catch(e){toast(e.message,1);}});
    paintRuns(d.runs);
  }
  function paintRuns(runs){
    $('#runs').innerHTML = runs&&runs.length? runs.map(r=>runCard(r)).join(''):'<div class="empty">실행 이력이 없습니다.</div>';
  }
  function runCard(r){
    const col=r.status==='SUCCEEDED'?'ok':r.status==='FAILED'?'rej':'pend';
    return `<div class="card" style="margin-bottom:10px" id="run-${r.sk}">
      <div class="row" style="justify-content:space-between">
        <h3>${esc(r.wfName)} <span class="mono" style="color:var(--muted)">#${r.sk}</span></h3>
        <span class="badge ${col}">${r.status}</span></div>
      <div class="meta">입력: ${esc((r.input||'').slice(0,80))} · ${fmt(r.totalTokens)}tok · by ${esc(r.startedBy||'')}</div>
      ${(r.steps||[]).map(s=>`<div class="runstep"><div class="rn">${s.done?'↻ FINAL':'#'+(s.i+1)} · ${esc(s.agentName||'')} · ${fmt(s.tokens)}tok</div><pre>${esc((s.reply||'').slice(0,600))}</pre></div>`).join('')}
      ${r.status==='SUCCEEDED'&&r.output?`<div class="runstep" style="border-left-color:var(--ok)"><div class="rn">OUTPUT</div><pre>${esc(r.output.slice(0,800))}</pre></div>`:''}
    </div>`;
  }
  async function pollRun(runId){
    for(let i=0;i<40;i++){
      await new Promise(r=>setTimeout(r,3000));
      let d;try{d=(await api('GET','/api/workflows/runs/'+runId)).run;}catch(e){break;}
      const el=$('#run-'+runId);
      const html=runCard(d);
      if(el){el.outerHTML=html;}else{$('#runs').insertAdjacentHTML('afterbegin',html);}
      if(d.status!=='RUNNING')break;
    }
  }
  paintList();
}

/* ---------------- skills ---------------- */
async function renderSkills(){
  setTitle('스킬');
  $('#view').innerHTML=`<div class="sub">반복해서 쓰는 방법 지식을 <b>SKILL.md</b>(agentskills.io 표준)로 패키징합니다. 게시하면 S3에 저장되고 Agent Registry에 AGENT_SKILLS로 등록되며, 이 스킬을 연결한 에이전트는 답변 시 해당 규약을 따릅니다.</div>
    <div class="grid" id="g">불러오는 중…</div>
    <h3 class="sec">새 스킬 게시</h3>
    <label>이름</label><input id="sn" maxlength="40" placeholder="예: 보고서 형식 규약">
    <label>설명</label><input id="sd" maxlength="120">
    <label>SKILL.md (frontmatter 없이 본문만 적어도 됩니다)</label>
    <textarea id="sm" rows="9" placeholder="# 보고서 형식&#10;- 항상 결론부터&#10;- 표는 3열 이내…"></textarea>
    <div style="margin-top:14px"><button class="btn p" id="mk">스킬 게시</button></div>`;
  $('#mk').onclick=async()=>{
    try{await api('POST','/api/skills',{name:$('#sn').value,description:$('#sd').value,skillMd:$('#sm').value});
      toast('스킬 게시됨 (S3 + Agent Registry)');renderSkills();}catch(e){toast(e.message,1);}};
  let sks;try{sks=(await api('GET','/api/skills')).skills;}catch(e){$('#g').innerHTML=`<div class="empty">${esc(e.message)}</div>`;return;}
  const g=$('#g');
  g.innerHTML = sks.length? '':'<div class="empty" style="grid-column:1/-1">스킬이 없습니다.</div>';
  for(const s of sks){
    const c=document.createElement('div');c.className='card';
    c.innerHTML=`<h3>⚡ ${esc(s.name)}</h3><p>${esc(s.description||'')}</p>
      <div class="meta">${esc(s.team)} · ${fmt(s.chars)}자${s.registryRecordArn?' · registry ✓':''}</div>
      <div class="row"><button class="btn ghost" data-v="${s.id}">보기</button>
      <button class="btn d" data-del="${s.id}">삭제</button></div>`;
    g.appendChild(c);
  }
  g.querySelectorAll('[data-v]').forEach(b=>b.onclick=async()=>{
    try{const d=await api('GET','/api/skills/'+b.dataset.v);
      alert(d.skill.skillMd.slice(0,2000));}catch(e){toast(e.message,1);}});
  g.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
    if(!confirm('삭제할까요?'))return;
    try{await api('DELETE','/api/skills/'+b.dataset.del);toast('삭제됨');renderSkills();}
    catch(e){toast(e.message,1);}});
}

/* ---------------- knowledge: wiki / datasources / ontology ---------------- */
function mdRender(md){
  const codes=[];
  let h=esc(md).replace(/```(\w*)\n?([\s\S]*?)```/g,(_,l,c)=>{codes.push(c);return '\u0000C'+(codes.length-1)+'\u0000';});
  h=h.replace(/^#### (.*)$/gm,'<h4>$1</h4>').replace(/^### (.*)$/gm,'<h3>$1</h3>')
     .replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h1>$1</h1>')
     .replace(/^---+$/gm,'<hr>')
     .replace(/^&gt; ?(.*)$/gm,'<blockquote>$1</blockquote>')
     .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
     .replace(/`([^`]+)`/g,'<code>$1</code>')
     .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  h=h.replace(/((?:^\|.*\|[ \t]*$\n?)+)/gm, block=>{
    const rows=block.trim().split('\n').map(r=>r.trim()).filter(r=>r.startsWith('|'));
    if(rows.length<2) return block;
    const cells=r=>r.replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
    const head=cells(rows[0]);
    const sep=rows[1]&&/^[\s|:\-]+$/.test(rows[1]);
    const body=(sep?rows.slice(2):rows.slice(1)).map(r=>'<tr>'+cells(r).map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('');
    return '<div class="tbl"><table><tr>'+head.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+body+'</table></div>\n';
  });
  h=h.replace(/^[-*] (.*)$/gm,'<li>$1</li>')
     .replace(/^\d+[.)] (.*)$/gm,'<oli>$1</oli>');
  h=h.replace(/(?:<li>[^\u0000]*?<\/li>\n?)+/g,m=>'<ul>'+m.replace(/\n/g,'')+'</ul>\n');
  h=h.replace(/(?:<oli>[^\u0000]*?<\/oli>\n?)+/g,m=>'<ol>'+m.replace(/<oli>/g,'<li>').replace(/<\/oli>/g,'</li>').replace(/\n/g,'')+'</ol>\n');
  h=h.replace(/<\/blockquote>\n<blockquote>/g,'<br>');
  h=h.split(/\n{2,}/).map(b=>{
    const t=b.trim();
    return /^<(h\d|ul|ol|pre|blockquote|hr|div)/.test(t)?t:(t?'<p>'+t.replace(/\n/g,'<br>')+'</p>':'');
  }).join('');
  return h.replace(/\u0000C(\d+)\u0000/g,(_,i)=>'<pre><code>'+codes[+i]+'</code></pre>');
}
async function renderKnowledge(sub){
  setTitle('지식 · 데이터');
  const tab=sub||'wiki';
  $('#view').innerHTML=`<div class="tabs">
    <button data-t="wiki" class="${tab==='wiki'?'on':''}">AI Wiki</button>
    <button data-t="ds" class="${tab==='ds'?'on':''}">데이터소스</button>
    <button data-t="onto" class="${tab==='onto'?'on':''}">온톨로지</button></div>
    <div id="kbody">불러오는 중…</div>`;
  document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>nav('knowledge/'+b.dataset.t));
  const body=$('#kbody');
  if(tab==='wiki') return paintWiki(body);
  if(tab==='ds') return paintDS(body);
  return paintOnto(body);
}
async function paintWiki(body, slug){
  if(slug){
    let d;try{d=(await api('GET','/api/wiki/'+slug)).page;}catch(e){body.innerHTML=`<div class="empty">${esc(e.message)}</div>`;return;}
    body.innerHTML=`<div class="row" style="justify-content:space-between;margin-bottom:8px">
      <h2 class="vh">${esc(d.title)}</h2>
      <span class="pill">v${d.version} · ${esc(d.updatedBy)}</span></div>
      <div class="md card" style="display:block">${mdRender(d.markdown)}</div>
      <div class="row" style="margin-top:14px">
        <button class="btn ghost" id="back">← 목록</button>
        <button class="btn a" id="edit">편집</button></div>
      <div id="ed"></div>`;
    $('#back').onclick=()=>paintWiki(body);
    $('#edit').onclick=()=>{
      $('#ed').innerHTML=`<label>제목</label><input id="wt2" value="${esc(d.title)}">
        <label>본문 (Markdown)</label><textarea id="wm2" rows="14">${esc(d.markdown)}</textarea>
        <div style="margin-top:12px"><button class="btn p" id="sv">저장 (v${d.version+1})</button></div>`;
      $('#sv').onclick=async()=>{
        try{await api('POST','/api/wiki',{slug,title:$('#wt2').value,markdown:$('#wm2').value});
          toast('저장됨');paintWiki(body,slug);}catch(e){toast(e.message,1);}};};
    return;
  }
  let pages;try{pages=(await api('GET','/api/wiki')).pages;}catch(e){body.innerHTML=`<div class="empty">${esc(e.message)}</div>`;return;}
  body.innerHTML=`<div class="sub">조직의 AI 지식 베이스입니다. 문서를 에이전트의 근거 자료로 연결하거나, 운영 규약의 정본으로 사용합니다.</div>
    <div class="grid" id="wg">${pages.length?'':'<div class="empty" style="grid-column:1/-1">문서가 없습니다.</div>'}</div>
    <h3 class="sec">새 문서</h3>
    <div class="row"><input id="ws" placeholder="slug (예: agent-naming-rules)" style="max-width:280px">
    <input id="wt" placeholder="제목" style="flex:1"></div>
    <label>본문 (Markdown)</label><textarea id="wm" rows="8"></textarea>
    <div style="margin-top:12px"><button class="btn p" id="mkw">문서 만들기</button></div>`;
  const wg=$('#wg');
  for(const p of pages){
    const c=document.createElement('div');c.className='card';
    c.innerHTML=`<h3>📄 ${esc(p.title)}</h3>
      <div class="meta">/${esc(p.slug)} · v${p.version} · ${esc(p.updatedBy)}</div>
      <div class="row"><button class="btn ghost" data-s="${esc(p.slug)}">열기</button></div>`;
    wg.appendChild(c);
  }
  wg.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>paintWiki(body,b.dataset.s));
  $('#mkw').onclick=async()=>{
    try{await api('POST','/api/wiki',{slug:$('#ws').value,title:$('#wt').value,markdown:$('#wm').value});
      toast('문서 생성됨');paintWiki(body);}catch(e){toast(e.message,1);}};
}
async function paintDS(body){
  let dss;try{dss=(await api('GET','/api/datasources')).datasources;}catch(e){body.innerHTML=`<div class="empty">${esc(e.message)}</div>`;return;}
  body.innerHTML=`<div class="notice">소규모 문서는 컨텍스트 프리로딩(<b>CAG</b>) 방식으로 에이전트에 연결합니다(가이드북 Part 6의 결정 기준). 대규모 코퍼스는 Bedrock Knowledge Bases로 확장합니다. 🛰 자동 수집 데이터소스는 크롤러가 6시간마다 갱신합니다.</div>
    <div class="grid" id="dg">${dss.length?'':'<div class="empty" style="grid-column:1/-1">데이터소스가 없습니다.</div>'}</div>
    <h3 class="sec">새 데이터소스</h3>
    <label>이름</label><input id="dn" maxlength="40">
    <label>내용 (텍스트, 최대 20,000자)</label><textarea id="dc" rows="7"></textarea>
    <div style="margin-top:12px"><button class="btn p" id="mkd">등록</button></div>`;
  const dg=$('#dg');
  for(const d of dss){
    const c=document.createElement('div');c.className='card';
    const auto=d.source==='crawler';
    c.innerHTML=`<div class="row" style="justify-content:space-between"><h3>${auto?'🛰':'📚'} ${esc(d.name)}</h3>${auto?'<span class="badge pend">자동 수집</span>':''}</div>
      <div class="meta">${fmt(d.chars)}자 · ${esc(d.team)}${auto&&d.crawledAt?' · 갱신 '+new Date(d.crawledAt*1000).toLocaleString('ko-KR'):''}</div>
      <div class="row">${auto&&ME.isAdmin?'<button class="btn a" data-crawl="1">지금 수집</button>':''}
      <button class="btn d" data-del="${d.id}">삭제</button></div>`;
    dg.appendChild(c);
  }
  dg.querySelectorAll('[data-crawl]').forEach(b=>b.onclick=async()=>{
    b.disabled=true;
    try{const r=await api('POST','/api/admin/crawl');toast(r.note||'수집을 시작했습니다.');}
    catch(e){toast(e.message,1);}finally{b.disabled=false;}});
  dg.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
    if(!confirm('삭제할까요?'))return;
    try{await api('DELETE','/api/datasources/'+b.dataset.del);toast('삭제됨');paintDS(body);}
    catch(e){toast(e.message,1);}});
  $('#mkd').onclick=async()=>{
    try{await api('POST','/api/datasources',{name:$('#dn').value,content:$('#dc').value});
      toast('등록됨');paintDS(body);}catch(e){toast(e.message,1);}};
}
async function paintOnto(body){
  let T=[],E=[],R=[];
  try{T=(await api('GET','/api/ontology/types')).types;
      E=(await api('GET','/api/ontology/entities')).entities;
      R=(await api('GET','/api/ontology/relations')).relations;}catch(e){}
  const tmap=Object.fromEntries(T.map(t=>[t.sk,t.name]));
  const emap=Object.fromEntries(E.map(x=>[x.sk,x.name]));
  body.innerHTML=`<div class="notice cy"><b>AI-Ready Data</b> — 조직의 엔티티·관계를 구조화해 두면, "온톨로지 주입"을 켠 에이전트가 이 그래프를 근거로 답합니다.</div>
    <h3 class="sec">엔티티 타입 (${T.length})</h3>
    <div class="row" id="tl">${T.map(t=>`<span class="pill">${esc(t.name)} <a data-dt="${t.sk}" style="cursor:pointer;color:var(--danger)">×</a></span>`).join('')||'<span class="sub" style="margin:0">없음</span>'}</div>
    <div class="row" style="margin-top:10px"><input id="tn" placeholder="타입 이름 (예: 서비스, 팀, 시스템)" style="max-width:300px">
    <button class="btn ghost" id="mkt">+ 타입</button></div>
    <h3 class="sec">엔티티 (${E.length})</h3>
    <table><tr><th>타입</th><th>이름</th><th>속성</th><th></th></tr>
    ${E.map(x=>`<tr><td>${esc(tmap[x.typeId]||'?')}</td><td>${esc(x.name)}</td>
      <td class="mono">${esc(Object.entries(x.attrs||{}).map(([k,v])=>k+'='+v).join(', '))}</td>
      <td><a data-de="${x.sk}" style="cursor:pointer;color:var(--danger)">삭제</a></td></tr>`).join('')}</table>
    <div class="row" style="margin-top:10px">
      <select id="et" style="max-width:170px">${T.map(t=>`<option value="${t.sk}">${esc(t.name)}</option>`).join('')}</select>
      <input id="en" placeholder="엔티티 이름" style="max-width:220px">
      <input id="ea" placeholder="속성 (k=v, 쉼표 구분)" style="flex:1">
      <button class="btn ghost" id="mke">+ 엔티티</button></div>
    <h3 class="sec">관계 (${R.length})</h3>
    <table>${R.map(r=>`<tr><td>${esc(emap[r.fromId]||'?')}</td>
      <td class="mono" style="color:var(--signal)">—${esc(r.relation)}→</td>
      <td>${esc(emap[r.toId]||'?')}</td>
      <td><a data-dr="${r.sk}" style="cursor:pointer;color:var(--danger)">삭제</a></td></tr>`).join('')}</table>
    <div class="row" style="margin-top:10px">
      <select id="rf" style="max-width:190px">${E.map(x=>`<option value="${x.sk}">${esc(x.name)}</option>`).join('')}</select>
      <input id="rr" placeholder="관계 (예: 운영한다)" style="max-width:170px">
      <select id="rt" style="max-width:190px">${E.map(x=>`<option value="${x.sk}">${esc(x.name)}</option>`).join('')}</select>
      <button class="btn ghost" id="mkr">+ 관계</button></div>`;
  $('#mkt').onclick=async()=>{try{await api('POST','/api/ontology/types',{name:$('#tn').value});paintOnto(body);}catch(e){toast(e.message,1);}};
  $('#mke').onclick=async()=>{
    const attrs={};$('#ea').value.split(',').forEach(kv=>{const [k,...v]=kv.split('=');if(k&&v.length)attrs[k.trim()]=v.join('=').trim();});
    try{await api('POST','/api/ontology/entities',{typeId:$('#et').value,name:$('#en').value,attrs});paintOnto(body);}catch(e){toast(e.message,1);}};
  $('#mkr').onclick=async()=>{try{await api('POST','/api/ontology/relations',{fromId:$('#rf').value,toId:$('#rt').value,relation:$('#rr').value});paintOnto(body);}catch(e){toast(e.message,1);}};
  body.querySelectorAll('[data-dt]').forEach(a=>a.onclick=async()=>{await api('DELETE','/api/ontology/types/'+a.dataset.dt);paintOnto(body);});
  body.querySelectorAll('[data-de]').forEach(a=>a.onclick=async()=>{await api('DELETE','/api/ontology/entities/'+a.dataset.de);paintOnto(body);});
  body.querySelectorAll('[data-dr]').forEach(a=>a.onclick=async()=>{await api('DELETE','/api/ontology/relations/'+a.dataset.dr);paintOnto(body);});
}

/* ---------------- admin ---------------- */
async function renderAdmin(){
  setTitle('플랫폼 운영');
  const view=$('#view');
  view.innerHTML=`<div class="sub">플랫폼 엔지니어의 통제 화면입니다 — 승인·예산·감사를 여기서 처리합니다. 거버넌스 정본은 AgentCore Agent Registry이며, 모든 결정이 CloudTrail과 플랫폼 감사 로그에 남습니다.</div><div id="ab">불러오는 중…</div>`;
  let d;try{d=await api('GET','/api/admin/overview');}catch(e){view.innerHTML=`<div class="empty">${esc(e.message)}</div>`;return;}
  const t=d.totals;
  let h=`<div class="kpis">
    <div class="kpi cy"><span>에이전트</span><b>${t.agents} / ${d.limits.maxAgents}</b></div>
    <div class="kpi"><span>총 호출</span><b>${fmt(t.invocations)}</b></div>
    <div class="kpi"><span>총 토큰</span><b>${fmt(t.totalTokens)}</b></div>
    <div class="kpi amber"><span>추정 비용</span><b>$${t.estCostUsd}</b></div></div>
    <div class="notice">🗂 거버넌스 정본: <b>Agent Registry</b> ${esc(d.registry.id)} (${esc(d.registry.region)}) — ${esc(d.registry.note)} · ${esc(d.priceNote)}</div>`;
  h+=`<h3 class="sec">승인 대기 (${d.pending.length})</h3>`;
  h+= d.pending.length? `<div class="grid">${d.pending.map(a=>{
    const ev=a.eval||{};
    return `<div class="card"><h3>${esc(a.name)} <span class="pill">T${a.riskTier} · ${esc(a.team)}</span></h3>
      ${lifecycleRail(a)}<p>${esc(a.description)}</p>
      <div class="meta">스모크 평가: ${ev.passed?'✅ 통과':'❌ 실패'}</div>
      ${ev.sample?`<p style="font-size:11.5px;border-left:2px solid var(--line);padding-left:9px">${esc(ev.sample)}</p>`:''}
      <div class="row"><button class="btn ok" data-ap="${a.id}">승인</button>
      <button class="btn d" data-rj="${a.id}">거부</button></div></div>`;}).join('')}</div>`
    :'<div class="empty">대기 중인 에이전트가 없습니다.</div>';
  h+=`<h3 class="sec">팀별 사용량</h3><table><tr><th>팀</th><th>에이전트</th><th>토큰</th><th>추정 비용</th></tr>
    ${Object.entries(d.byTeam).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.agents}</td>
    <td>${fmt(v.tokens)}</td><td>$${v.estCostUsd}</td></tr>`).join('')}</table>`;
  h+=`<h3 class="sec">에이전트 현황</h3><table><tr><th>에이전트</th><th>팀</th><th>상태</th><th>호출</th><th>토큰</th><th>예산</th><th></th></tr>
    ${d.agents.map(a=>{const pct=Math.min(100,Math.round(a.usage.totalTokens/a.budgetTokens*100));
    return `<tr><td>${esc(a.name)}${a.registryRecordArn?' <span class="pill" title="'+esc(a.registryRecordArn)+'">reg</span>':''}</td>
      <td>${esc(a.team)}</td>
      <td>${a.status==='APPROVED'?'<span class="badge ok">승인</span>':a.status==='PENDING'?'<span class="badge pend">대기</span>':a.status==='EVALUATING'?'<span class="badge pend">평가 중</span>':'<span class="badge rej">거부</span>'}</td>
      <td>${a.usage.invocations}</td><td class="mono">${fmt(a.usage.totalTokens)}</td>
      <td><div class="bar"><i class="${pct>=90?'hot':''}" style="width:${pct}%"></i></div>
      <span class="mono" style="color:var(--muted)">${pct}% / ${fmt(a.budgetTokens)}</span></td>
      <td><button class="btn ghost" data-bd="${a.id}" data-cur="${a.budgetTokens}">예산</button></td></tr>`;}).join('')}</table>`;
  h+=`<h3 class="sec">감사 로그</h3><div id="audit">불러오는 중…</div>`;
  $('#ab').innerHTML=h;
  document.querySelectorAll('[data-ap]').forEach(b=>b.onclick=async()=>{
    const reason=prompt('승인 사유 (Registry + CloudTrail에 기록됩니다)','읽기 전용 확인 후 승인');
    if(reason==null)return;
    try{await api('POST',`/api/admin/agents/${b.dataset.ap}/approve`,{reason});toast('승인됨');renderAdmin();}
    catch(e){toast(e.message,1);}});
  document.querySelectorAll('[data-rj]').forEach(b=>b.onclick=async()=>{
    const reason=prompt('거부 사유','정책 검토 필요');if(reason==null)return;
    try{await api('POST',`/api/admin/agents/${b.dataset.rj}/reject`,{reason});toast('거부됨');renderAdmin();}
    catch(e){toast(e.message,1);}});
  document.querySelectorAll('[data-bd]').forEach(b=>b.onclick=async()=>{
    const v=prompt('새 토큰 예산 (1,000~2,000,000)',b.dataset.cur);if(!v)return;
    try{await api('POST',`/api/admin/agents/${b.dataset.bd}/budget`,{budgetTokens:parseInt(v,10)});
      toast('예산 변경됨');renderAdmin();}catch(e){toast(e.message,1);}});
  try{const au=(await api('GET','/api/admin/audit')).events;
    $('#audit').innerHTML=`<table><tr><th>시각</th><th>주체</th><th>행위</th><th>대상</th><th>상세</th></tr>
      ${au.map(e=>`<tr><td class="mono">${new Date(e.at*1000).toLocaleString('ko-KR')}</td>
      <td>${esc(e.actor)}</td><td class="mono" style="color:var(--signal)">${esc(e.action)}</td>
      <td class="mono">${esc(e.target)}</td><td style="color:var(--muted)">${esc(e.detail||'')}</td></tr>`).join('')}</table>`;
  }catch(e){$('#audit').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}
}

/* ---------------- boot ---------------- */
async function render(){
  const r = route().split('/');
  if(!token()){renderLogin();return;}
  if(!ME){
    try{ ME = await api('GET','/api/me'); }
    catch(e){ return; }
  }
  shell();
  if(r[0]==='home') return renderHome();
  if(r[0]==='catalog') return renderCatalog();
  if(r[0]==='builder') return renderBuilder();
  if(r[0]==='chat'&&r[1]) return renderChat(r[1]);
  if(r[0]==='workflows') return renderWorkflows();
  if(r[0]==='skills') return renderSkills();
  if(r[0]==='knowledge') return renderKnowledge(r[1]);
  if(r[0]==='admin') return renderAdmin();
  return renderHome();
}
(async function(){
  const code = new URLSearchParams(location.search).get('code');
  if(code){ try{ await exchangeCode(code); }catch(e){ toast(e.message,1); } }
  render();
})();
