"""Agentic AI platform workspace demo (Amazon Quick-style).

Views: agent catalog / builder (create agents) / data sources / chat.
"Agents" are configs in DynamoDB executed via InvokeHarness systemPrompt
override on one shared Harness (no per-agent AgentCore resources).
Data sources are small text corpora preloaded into context (CAG-style).

Served behind CloudFront (the only public entry point); requests lacking the
x-origin-verify secret header CloudFront injects are rejected with 403.
"""
import json
import os
import re
import time
import uuid

import boto3

HARNESS_ARN = os.environ["HARNESS_ARN"]
ORIGIN_SECRET = os.environ["ORIGIN_SECRET"]
REGION = os.environ.get("HARNESS_REGION", "ap-northeast-2")
TABLE = os.environ.get("REGISTRY_TABLE", "agentic-book-demo-registry")

MAX_AGENTS = 20
MAX_DATASOURCES = 10
MAX_DS_CHARS = 20000
MAX_PROMPT_CHARS = 4000
MAX_MSG_CHARS = 2000

agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)

SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-_]{20,80}$")
ID_RE = re.compile(r"^[a-f0-9]{8}$")

BUILDER_SPEC_PROMPT = (
    "지금까지의 대화 내용을 바탕으로, 생성할 에이전트의 스펙을 아래 JSON 형식으로만 출력하세요. "
    "다른 설명 없이 JSON 하나만 출력합니다.\n"
    '{"name": "에이전트 이름(한국어, 30자 이내)", '
    '"description": "한 문장 설명(80자 이내)", '
    '"systemPrompt": "이 에이전트의 시스템 프롬프트(한국어, 상세하게, 2000자 이내)"}'
)


# ---------------------------------------------------------------- registry
def _list(pk):
    return ddb.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq(pk)
    ).get("Items", [])


def list_agents():
    items = sorted(_list("AGENT"), key=lambda x: x.get("createdAt", 0), reverse=True)
    return [
        {
            "id": i["sk"], "name": i["name"], "description": i.get("description", ""),
            "datasourceIds": i.get("datasourceIds", []), "createdAt": int(i.get("createdAt", 0)),
            "builtin": bool(i.get("builtin")),
        }
        for i in items
    ]


def list_datasources(with_content=False):
    items = sorted(_list("DS"), key=lambda x: x.get("createdAt", 0), reverse=True)
    out = []
    for i in items:
        row = {"id": i["sk"], "name": i["name"], "chars": len(i.get("content", "")),
               "createdAt": int(i.get("createdAt", 0))}
        if with_content:
            row["content"] = i.get("content", "")
        out.append(row)
    return out


def get_item(pk, sk):
    return ddb.get_item(Key={"pk": pk, "sk": sk}).get("Item")


# ---------------------------------------------------------------- harness
def invoke(session_id, message, system_prompt=None):
    kwargs = {
        "harnessArn": HARNESS_ARN,
        "runtimeSessionId": session_id,
        "messages": [{"role": "user", "content": [{"text": message}]}],
    }
    if system_prompt:
        kwargs["systemPrompt"] = [{"text": system_prompt}]
    response = agentcore.invoke_harness(**kwargs)
    parts, usage, latency = [], None, None
    for ev in response["stream"]:
        if "contentBlockDelta" in ev:
            delta = ev["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                parts.append(delta["text"])
        elif "metadata" in ev:
            usage = ev["metadata"].get("usage")
            latency = (ev["metadata"].get("metrics") or {}).get("latencyMs")
        elif any(k in ev for k in ("validationException", "internalServerException", "runtimeClientError")):
            raise RuntimeError(f"stream error event: {ev}")
    return "".join(parts), usage, latency


def build_agent_prompt(agent):
    blocks = [agent.get("systemPrompt", "").strip() or "당신은 유용한 어시스턴트입니다."]
    ds_ids = agent.get("datasourceIds", [])[:5]
    corpus = []
    for ds_id in ds_ids:
        ds = get_item("DS", ds_id)
        if ds and ds.get("content"):
            corpus.append(f"### {ds['name']}\n{ds['content']}")
    if corpus:
        blocks.append(
            "\n\n## 참고 자료 (등록된 데이터소스)\n"
            "아래 자료를 근거로 답하고, 자료에 없는 내용은 모른다고 답하세요.\n\n"
            + "\n\n---\n\n".join(corpus)
        )
    blocks.append("\n답변은 한국어로 하세요.")
    return "".join(blocks)


# ---------------------------------------------------------------- handlers
def api(method, path, body):
    # --- agents ---
    if path == "/api/agents" and method == "GET":
        return 200, {"agents": list_agents()}

    if path == "/api/agents" and method == "POST":
        name = (body.get("name") or "").strip()[:30]
        desc = (body.get("description") or "").strip()[:120]
        prompt = (body.get("systemPrompt") or "").strip()
        ds_ids = [d for d in (body.get("datasourceIds") or []) if isinstance(d, str) and ID_RE.match(d)][:5]
        if not name or not prompt:
            return 400, {"error": "name과 systemPrompt는 필수입니다."}
        if len(prompt) > MAX_PROMPT_CHARS:
            return 400, {"error": f"systemPrompt는 {MAX_PROMPT_CHARS}자 이내여야 합니다."}
        if len(list_agents()) >= MAX_AGENTS:
            return 400, {"error": f"데모 한도({MAX_AGENTS}개)에 도달했습니다. 기존 에이전트를 삭제하세요."}
        agent_id = uuid.uuid4().hex[:8]
        ddb.put_item(Item={
            "pk": "AGENT", "sk": agent_id, "name": name, "description": desc,
            "systemPrompt": prompt, "datasourceIds": ds_ids, "createdAt": int(time.time()),
        })
        return 200, {"id": agent_id}

    m = re.match(r"^/api/agents/([a-f0-9]{8})$", path)
    if m and method == "DELETE":
        item = get_item("AGENT", m.group(1))
        if item and item.get("builtin"):
            return 400, {"error": "기본 제공 에이전트는 삭제할 수 없습니다."}
        ddb.delete_item(Key={"pk": "AGENT", "sk": m.group(1)})
        return 200, {"ok": True}

    # --- datasources ---
    if path == "/api/datasources" and method == "GET":
        return 200, {"datasources": list_datasources()}

    if path == "/api/datasources" and method == "POST":
        name = (body.get("name") or "").strip()[:40]
        content = (body.get("content") or "").strip()
        if not name or not content:
            return 400, {"error": "name과 content는 필수입니다."}
        if len(content) > MAX_DS_CHARS:
            return 400, {"error": f"content는 {MAX_DS_CHARS:,}자 이내여야 합니다(데모는 CAG 방식 소규모 코퍼스만)."}
        if len(list_datasources()) >= MAX_DATASOURCES:
            return 400, {"error": f"데모 한도({MAX_DATASOURCES}개)에 도달했습니다."}
        ds_id = uuid.uuid4().hex[:8]
        ddb.put_item(Item={
            "pk": "DS", "sk": ds_id, "name": name, "content": content,
            "createdAt": int(time.time()),
        })
        return 200, {"id": ds_id}

    m = re.match(r"^/api/datasources/([a-f0-9]{8})$", path)
    if m and method == "DELETE":
        ddb.delete_item(Key={"pk": "DS", "sk": m.group(1)})
        return 200, {"ok": True}

    # --- chat (use an agent) ---
    if path == "/api/chat" and method == "POST":
        message = (body.get("message") or "").strip()
        agent_id = body.get("agentId") or ""
        base = body.get("sessionId") or ""
        if not message or len(message) > MAX_MSG_CHARS:
            return 400, {"error": f"message는 1~{MAX_MSG_CHARS}자여야 합니다."}
        if not ID_RE.match(agent_id):
            return 400, {"error": "agentId가 올바르지 않습니다."}
        agent = get_item("AGENT", agent_id)
        if not agent:
            return 404, {"error": "에이전트를 찾을 수 없습니다."}
        if not SESSION_RE.match(base):
            base = uuid.uuid4().hex + "-web"
        session_id = f"{base}-{agent_id}"
        reply, usage, latency = invoke(session_id, message, build_agent_prompt(agent))
        return 200, {"reply": reply, "sessionId": base, "usage": usage, "latencyMs": latency}

    # --- builder chat + spec extraction ---
    if path == "/api/builder" and method == "POST":
        message = (body.get("message") or "").strip()
        base = body.get("sessionId") or ""
        if not message or len(message) > MAX_MSG_CHARS:
            return 400, {"error": f"message는 1~{MAX_MSG_CHARS}자여야 합니다."}
        if not SESSION_RE.match(base):
            base = uuid.uuid4().hex + "-web"
        reply, usage, latency = invoke(f"{base}-builder", message)
        return 200, {"reply": reply, "sessionId": base, "usage": usage, "latencyMs": latency}

    if path == "/api/spec" and method == "POST":
        base = body.get("sessionId") or ""
        if not SESSION_RE.match(base):
            return 400, {"error": "빌더 대화를 먼저 진행하세요."}
        reply, _, _ = invoke(f"{base}-builder", BUILDER_SPEC_PROMPT)
        jm = re.search(r"\{.*\}", reply, re.S)
        if not jm:
            return 502, {"error": "스펙 JSON을 추출하지 못했습니다. 대화를 조금 더 진행해 보세요."}
        try:
            spec = json.loads(jm.group(0))
        except json.JSONDecodeError:
            return 502, {"error": "스펙 JSON 파싱에 실패했습니다. 다시 시도해 주세요."}
        return 200, {"spec": {
            "name": str(spec.get("name", ""))[:30],
            "description": str(spec.get("description", ""))[:120],
            "systemPrompt": str(spec.get("systemPrompt", ""))[:MAX_PROMPT_CHARS],
        }}

    return 404, {"error": "not found"}


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-origin-verify") != ORIGIN_SECRET:
        return {"statusCode": 403, "body": "forbidden"}

    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = http.get("path", "/")

    if method == "GET" and not path.startswith("/api/"):
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8",
                        "Cache-Control": "public, max-age=300"},
            "body": HTML,
        }

    try:
        body = json.loads(event.get("body") or "{}") if method != "GET" else {}
    except json.JSONDecodeError:
        return _json(400, {"error": "invalid JSON body"})

    try:
        code, obj = api(method, path, body)
        return _json(code, obj)
    except Exception as exc:
        print(f"api error [{method} {path}]: {exc}")
        return _json(502, {"error": "요청 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."})


def _json(code, obj):
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(obj, ensure_ascii=False, default=str)}


# ---------------------------------------------------------------- SPA
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agentic AI 플랫폼 워크스페이스</title>
<style>
:root{--bg:#0b1220;--panel:#141e33;--line:#26334f;--accent:#38bdf8;--accent2:#a78bfa;
      --text:#e2e8f0;--muted:#8fa3c0;--danger:#f87171;--ok:#34d399;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',Apple SD Gothic Neo,sans-serif;
     display:flex;height:100vh;overflow:hidden}
nav{width:220px;background:var(--panel);border-right:1px solid var(--line);padding:18px 12px;
    display:flex;flex-direction:column;gap:4px;flex-shrink:0}
nav .brand{font-weight:700;font-size:15px;padding:6px 10px 16px}
nav .brand small{display:block;color:var(--muted);font-weight:400;font-size:11px;margin-top:3px}
nav button{all:unset;cursor:pointer;padding:10px 12px;border-radius:9px;font-size:13.5px;color:var(--muted)}
nav button:hover{background:#1b2740;color:var(--text)}
nav button.on{background:#1b2740;color:var(--accent);font-weight:600}
nav .foot{margin-top:auto;font-size:11px;color:var(--muted);padding:10px}
nav .foot a{color:var(--accent);text-decoration:none}
main{flex:1;overflow-y:auto;padding:26px 30px}
h2{font-size:18px;margin-bottom:4px} .sub{color:var(--muted);font-size:12.5px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:16px;
      display:flex;flex-direction:column;gap:8px}
.card h3{font-size:14.5px} .card p{font-size:12.5px;color:var(--muted);flex:1;line-height:1.5}
.card .tags{font-size:11px;color:var(--accent2)}
.row{display:flex;gap:8px;align-items:center}
.btn{border:none;border-radius:8px;padding:8px 14px;font-size:12.5px;font-weight:600;cursor:pointer}
.btn.p{background:var(--accent);color:#082f49}
.btn.g{background:#233152;color:var(--text)}
.btn.d{background:transparent;color:var(--danger);border:1px solid #3f2b3a}
.btn:disabled{opacity:.45;cursor:default}
input,textarea,select{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--text);
      border-radius:9px;padding:10px 12px;font-size:13px;outline:none;font-family:inherit}
input:focus,textarea:focus{border-color:var(--accent)}
label{display:block;font-size:12px;color:var(--muted);margin:14px 0 6px}
.chatbox{display:flex;flex-direction:column;height:calc(100vh - 150px);background:var(--panel);
         border:1px solid var(--line);border-radius:13px;overflow:hidden}
.msgs{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:11px}
.m{max-width:80%;padding:11px 13px;border-radius:11px;font-size:13.5px;line-height:1.55;white-space:pre-wrap}
.m.u{align-self:flex-end;background:var(--accent);color:#082f49;border-bottom-right-radius:2px}
.m.b{align-self:flex-start;background:#1b2740;border:1px solid var(--line);border-bottom-left-radius:2px}
.m.b.t{color:var(--muted);font-style:italic}
.m .meta{font-size:10.5px;color:var(--muted);margin-top:5px}
.chatin{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line)}
.pill{font-size:11px;background:#233152;border-radius:20px;padding:3px 10px;color:var(--muted)}
.notice{background:#1b2740;border:1px solid var(--line);border-left:3px solid var(--accent2);
        border-radius:8px;padding:12px 14px;font-size:12.5px;color:var(--muted);margin-bottom:18px;line-height:1.6}
.checks{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.checks label{display:flex;align-items:center;gap:6px;margin:0;background:#1b2740;border:1px solid var(--line);
        border-radius:8px;padding:7px 11px;cursor:pointer;font-size:12px;color:var(--text)}
.checks input{width:auto}
.empty{color:var(--muted);font-size:13px;padding:30px;text-align:center;border:1px dashed var(--line);border-radius:12px}
.toast{position:fixed;bottom:22px;right:22px;background:#1b2740;border:1px solid var(--line);
       border-left:3px solid var(--ok);padding:12px 16px;border-radius:10px;font-size:13px;display:none;z-index:9}
.toast.err{border-left-color:var(--danger)}
</style>
</head>
<body>
<nav>
  <div class="brand">Agentic AI 플랫폼<small>워크스페이스 데모 · AgentCore Harness</small></div>
  <button data-v="catalog" class="on">🗂️ 에이전트 카탈로그</button>
  <button data-v="builder">🛠️ 새 에이전트 만들기</button>
  <button data-v="datasources">📚 데이터소스</button>
  <button data-v="chat" id="navChat" style="display:none">💬 채팅</button>
  <div class="foot"><a href="https://www.atomai.click/AgenticAI-Platform/" target="_blank">가이드북 보기 ↗</a></div>
</nav>
<main id="main"></main>
<div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s); const main=$('#main');
let view='catalog', agents=[], dss=[], chatAgent=null, pendingSpec=null;
let base=null; try{base=localStorage.getItem('wsSession')}catch(e){}
if(!base){base=crypto.randomUUID().replace(/-/g,'')+'w'; try{localStorage.setItem('wsSession',base)}catch(e){}}
const chats={}; // per-agent message history (this browser session only)

function toast(msg,err){const t=$('#toast');t.textContent=msg;t.className='toast'+(err?' err':'');
  t.style.display='block';setTimeout(()=>t.style.display='none',3200);}
async function api(method,path,body){
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},
    body:body?JSON.stringify(body):undefined});
  const d=await r.json().catch(()=>({error:'응답 파싱 실패'}));
  if(!r.ok) throw new Error(d.error||('HTTP '+r.status));
  return d;
}
function nav(v){view=v;document.querySelectorAll('nav button[data-v]').forEach(b=>
  b.classList.toggle('on',b.dataset.v===v));render();}
document.querySelectorAll('nav button[data-v]').forEach(b=>b.onclick=()=>nav(b.dataset.v));
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

// ---------- catalog ----------
async function renderCatalog(){
  main.innerHTML='<h2>에이전트 카탈로그</h2><div class="sub">등록된 에이전트를 선택해 바로 사용합니다. 새 에이전트는 빌더와 대화해 만듭니다.</div><div class="grid" id="g">불러오는 중…</div>';
  try{agents=(await api('GET','api/agents')).agents;}catch(e){$('#g').innerHTML='<div class="empty">'+esc(e.message)+'</div>';return;}
  const g=$('#g');
  if(!agents.length){g.innerHTML='<div class="empty" style="grid-column:1/-1">아직 에이전트가 없습니다.<br>"새 에이전트 만들기"에서 빌더와 대화해 첫 에이전트를 만들어 보세요.</div>';return;}
  g.innerHTML='';
  for(const a of agents){
    const c=document.createElement('div');c.className='card';
    c.innerHTML='<h3>'+esc(a.name)+'</h3><p>'+esc(a.description||'설명 없음')+'</p>'+
      '<div class="tags">'+(a.datasourceIds.length?('📚 데이터소스 '+a.datasourceIds.length+'개 연결'):'데이터소스 없음')+'</div>'+
      '<div class="row"><button class="btn p" data-use="'+a.id+'">사용하기</button>'+
      (a.builtin?'':'<button class="btn d" data-del="'+a.id+'">삭제</button>')+'</div>';
    g.appendChild(c);
  }
  g.querySelectorAll('[data-use]').forEach(b=>b.onclick=()=>{chatAgent=agents.find(x=>x.id===b.dataset.use);
    $('#navChat').style.display='block';nav('chat');});
  g.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
    if(!confirm('이 에이전트를 삭제할까요?'))return;
    try{await api('DELETE','api/agents/'+b.dataset.del);toast('삭제됨');renderCatalog();}
    catch(e){toast(e.message,1);}});
}

// ---------- datasources ----------
async function renderDS(){
  main.innerHTML='<h2>데이터소스</h2>'+
   '<div class="sub">에이전트에 연결할 소규모 지식 코퍼스를 등록합니다.</div>'+
   '<div class="notice">이 데모는 소규모 코퍼스를 컨텍스트에 프리로딩하는 <b>CAG(Cache-Augmented Generation)</b> 방식을 씁니다 — 코퍼스가 작으면 벡터 검색보다 전체 프리로딩이 낫다는 가이드북 Part 6의 결정 표 그대로입니다. 대규모 코퍼스는 Bedrock Knowledge Bases로 확장합니다.</div>'+
   '<div class="grid" id="g">불러오는 중…</div>'+
   '<h2 style="margin-top:26px">새 데이터소스 등록</h2>'+
   '<label>이름</label><input id="dn" maxlength="40" placeholder="예: 사내 환불 정책 v2">'+
   '<label>내용 (텍스트, 최대 20,000자)</label><textarea id="dc" rows="8" placeholder="문서 본문을 붙여넣으세요"></textarea>'+
   '<div style="margin-top:14px"><button class="btn p" id="da">등록</button></div>';
  $('#da').onclick=async()=>{
    try{await api('POST','api/datasources',{name:$('#dn').value,content:$('#dc').value});
      toast('데이터소스 등록됨');renderDS();}catch(e){toast(e.message,1);}};
  try{dss=(await api('GET','api/datasources')).datasources;}catch(e){$('#g').innerHTML='<div class="empty">'+esc(e.message)+'</div>';return;}
  const g=$('#g');
  if(!dss.length){g.innerHTML='<div class="empty" style="grid-column:1/-1">등록된 데이터소스가 없습니다.</div>';return;}
  g.innerHTML='';
  for(const d of dss){
    const c=document.createElement('div');c.className='card';
    c.innerHTML='<h3>📄 '+esc(d.name)+'</h3><p>'+d.chars.toLocaleString()+'자</p>'+
      '<div class="row"><button class="btn d" data-del="'+d.id+'">삭제</button></div>';
    g.appendChild(c);
  }
  g.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
    if(!confirm('삭제할까요? 이 데이터소스를 쓰는 에이전트는 해당 자료 없이 동작합니다.'))return;
    try{await api('DELETE','api/datasources/'+b.dataset.del);toast('삭제됨');renderDS();}
    catch(e){toast(e.message,1);}});
}

// ---------- chat helpers ----------
function chatUI(title,subtitle,placeholder){
  main.innerHTML='<h2>'+esc(title)+'</h2><div class="sub">'+subtitle+'</div>'+
   '<div class="chatbox"><div class="msgs" id="ms"></div>'+
   '<div class="chatin"><input id="ci" placeholder="'+esc(placeholder)+'" autocomplete="off">'+
   '<button class="btn p" id="cs">전송</button></div></div>';
}
function addMsg(cls,text){const d=document.createElement('div');d.className='m '+cls;
  d.textContent=text;$('#ms').appendChild(d);$('#ms').scrollTop=1e9;return d;}
function bindChat(sender){
  const ci=$('#ci'),cs=$('#cs');
  const go=async()=>{const t=ci.value.trim();if(!t)return;ci.value='';cs.disabled=true;
    addMsg('u',t);const w=addMsg('b t','생각 중…');
    try{const d=await sender(t);w.className='m b';w.textContent=d.reply||'(빈 응답)';
      if(d.usage){const m=document.createElement('div');m.className='meta';
        m.textContent='입력 '+d.usage.inputTokens+' / 출력 '+d.usage.outputTokens+' 토큰 · '+d.latencyMs+'ms';
        w.appendChild(m);}
      return d;
    }catch(e){w.className='m b';w.textContent='오류: '+e.message;}
    finally{cs.disabled=false;ci.focus();}};
  cs.onclick=go; ci.onkeydown=e=>{if(e.key==='Enter')go();};
}

// ---------- use-agent chat ----------
function renderChat(){
  if(!chatAgent){nav('catalog');return;}
  chatUI('💬 '+chatAgent.name, esc(chatAgent.description||'')+
    ' <span class="pill">공유 Harness + systemPrompt override</span>'+
    (chatAgent.datasourceIds.length?' <span class="pill">📚 데이터소스 '+chatAgent.datasourceIds.length+'개</span>':''),
    '메시지를 입력하세요…');
  const hist=chats[chatAgent.id]||[];
  for(const h of hist)addMsg(h.cls,h.text);
  if(!hist.length)addMsg('b','안녕하세요! "'+chatAgent.name+'" 에이전트입니다. 무엇을 도와드릴까요?');
  bindChat(async t=>{
    (chats[chatAgent.id]=chats[chatAgent.id]||[]).push({cls:'u',text:t});
    const d=await api('POST','api/chat',{agentId:chatAgent.id,message:t,sessionId:base});
    chats[chatAgent.id].push({cls:'b',text:d.reply});return d;});
}

// ---------- builder ----------
function renderBuilder(){
  chatUI('🛠️ 새 에이전트 만들기',
   '빌더 에이전트와 대화하며 요구사항을 좁힌 뒤, <b>스펙으로 변환</b> 버튼으로 에이전트를 생성합니다. <span class="pill">요구사항 대화 → spec → 카탈로그 (가이드북 Part 11)</span>',
   '만들고 싶은 에이전트를 설명해 주세요…');
  const bar=document.createElement('div');
  bar.style.cssText='padding:10px 12px;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center';
  bar.innerHTML='<button class="btn g" id="sp">📋 지금까지 대화를 스펙으로 변환</button><span class="sub" style="margin:0" id="sphint"></span>';
  $('.chatbox').appendChild(bar);
  addMsg('b','안녕하세요! 어떤 에이전트를 만들고 싶으신가요? 용도·사용자·필요한 지식을 알려주시면 명확화 질문을 드리며 스펙을 함께 만들어 갑니다.');
  bindChat(t=>api('POST','api/builder',{message:t,sessionId:base}));
  $('#sp').onclick=async()=>{
    $('#sp').disabled=true;$('#sphint').textContent='스펙 추출 중…';
    try{const d=await api('POST','api/spec',{sessionId:base});pendingSpec=d.spec;renderCreateForm();}
    catch(e){toast(e.message,1);$('#sphint').textContent='';}
    finally{$('#sp').disabled=false;}};
}

// ---------- create form ----------
async function renderCreateForm(){
  const s=pendingSpec||{name:'',description:'',systemPrompt:''};
  try{dss=(await api('GET','api/datasources')).datasources;}catch(e){dss=[];}
  main.innerHTML='<h2>에이전트 생성 확인</h2><div class="sub">빌더가 만든 스펙을 검토·수정하고, 연결할 데이터소스를 고른 뒤 생성합니다.</div>'+
   '<label>이름</label><input id="an" maxlength="30" value="'+esc(s.name)+'">'+
   '<label>설명</label><input id="ad" maxlength="120" value="'+esc(s.description)+'">'+
   '<label>시스템 프롬프트</label><textarea id="ap" rows="10">'+esc(s.systemPrompt)+'</textarea>'+
   '<label>데이터소스 연결 (선택, 최대 5개)</label><div class="checks" id="ac">'+
   (dss.length?dss.map(d=>'<label><input type="checkbox" value="'+d.id+'"> '+esc(d.name)+' <span style="color:var(--muted)">('+d.chars.toLocaleString()+'자)</span></label>').join(''):'<span class="sub" style="margin:0">등록된 데이터소스가 없습니다 — 데이터소스 탭에서 먼저 등록할 수 있습니다.</span>')+
   '</div><div style="margin-top:18px" class="row">'+
   '<button class="btn p" id="ok">에이전트 생성</button>'+
   '<button class="btn g" id="back">빌더로 돌아가기</button></div>';
  $('#back').onclick=()=>nav('builder');
  $('#ok').onclick=async()=>{
    const ids=[...document.querySelectorAll('#ac input:checked')].map(x=>x.value);
    try{await api('POST','api/agents',{name:$('#an').value,description:$('#ad').value,
        systemPrompt:$('#ap').value,datasourceIds:ids});
      pendingSpec=null;toast('에이전트가 카탈로그에 등록되었습니다');nav('catalog');}
    catch(e){toast(e.message,1);}};
}

function render(){
  if(view==='catalog')renderCatalog();
  else if(view==='builder')renderBuilder();
  else if(view==='datasources')renderDS();
  else if(view==='chat')renderChat();
}
render();
</script>
</body>
</html>"""
