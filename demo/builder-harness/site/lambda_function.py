"""Demo chat site for the AgenticBookBuilderDemo Harness.

GET  /      -> chat UI (HTML)
POST /chat  -> invoke the AgentCore Harness and return the full reply as JSON

Served behind CloudFront (the only public entry point); the Lambda Function URL
rejects any request that lacks the x-origin-verify secret header CloudFront
injects, so direct calls to the URL get 403.
"""
import json
import os
import re
import uuid

import boto3

HARNESS_ARN = os.environ["HARNESS_ARN"]
ORIGIN_SECRET = os.environ["ORIGIN_SECRET"]
REGION = os.environ.get("HARNESS_REGION", "ap-northeast-2")

client = boto3.client("bedrock-agentcore", region_name=REGION)

HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>빌더 에이전트 데모 — Agentic AI 플랫폼 엔지니어링</title>
<style>
  :root { --bg:#0f172a; --panel:#1e293b; --accent:#38bdf8; --text:#e2e8f0; --muted:#94a3b8; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'Segoe UI',Apple SD Gothic Neo,sans-serif;
         display:flex; flex-direction:column; height:100vh; }
  header { padding:14px 20px; background:var(--panel); border-bottom:1px solid #334155; }
  header h1 { font-size:16px; font-weight:600; }
  header p { font-size:12px; color:var(--muted); margin-top:2px; }
  header a { color:var(--accent); text-decoration:none; }
  #chat { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:12px; }
  .msg { max-width:78%; padding:12px 14px; border-radius:12px; line-height:1.55; font-size:14px; white-space:pre-wrap; }
  .user { align-self:flex-end; background:var(--accent); color:#082f49; border-bottom-right-radius:2px; }
  .bot  { align-self:flex-start; background:var(--panel); border:1px solid #334155; border-bottom-left-radius:2px; }
  .bot.thinking { color:var(--muted); font-style:italic; }
  form { display:flex; gap:8px; padding:14px 20px; background:var(--panel); border-top:1px solid #334155; }
  input { flex:1; padding:12px 14px; border-radius:10px; border:1px solid #475569; background:var(--bg);
          color:var(--text); font-size:14px; outline:none; }
  input:focus { border-color:var(--accent); }
  button { padding:12px 22px; border-radius:10px; border:none; background:var(--accent); color:#082f49;
           font-weight:700; font-size:14px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .meta { font-size:11px; color:var(--muted); margin-top:4px; }
</style>
</head>
<body>
<header>
  <h1>빌더 에이전트 데모 (AgentCore Harness)</h1>
  <p>요구사항을 이야기하면 명확화 질문을 거쳐 에이전트 배포 계획을 제안합니다 ·
     <a href="https://www.atomai.click/AgenticAI-Platform/" target="_blank">가이드북 보기</a></p>
</header>
<div id="chat">
  <div class="msg bot">안녕하세요! 어떤 에이전트를 만들고 싶은지 말씀해 주세요. 예: "사내 계약서 검토를 도와주는 에이전트를 만들고 싶어요"</div>
</div>
<form id="f">
  <input id="q" placeholder="만들고 싶은 에이전트를 설명해 주세요…" autocomplete="off" required>
  <button id="send">전송</button>
</form>
<script>
const chat = document.getElementById('chat');
const form = document.getElementById('f');
const q = document.getElementById('q');
const send = document.getElementById('send');
let sessionId = null;
try { sessionId = localStorage.getItem('builderDemoSession'); } catch(e) {}
if (!sessionId) {
  sessionId = crypto.randomUUID() + '-web';
  try { localStorage.setItem('builderDemoSession', sessionId); } catch(e) {}
}
function add(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = q.value.trim();
  if (!text) return;
  add('user', text);
  q.value = ''; send.disabled = true;
  const wait = add('bot thinking', '생각 중…');
  try {
    const r = await fetch('chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, sessionId})
    });
    const data = await r.json();
    wait.classList.remove('thinking');
    if (data.reply) {
      wait.textContent = data.reply;
      if (data.usage) {
        const m = document.createElement('div');
        m.className = 'meta';
        m.textContent = `입력 ${data.usage.inputTokens} / 출력 ${data.usage.outputTokens} 토큰 · ${data.latencyMs}ms`;
        wait.appendChild(m);
      }
    } else {
      wait.textContent = '오류: ' + (data.error || '알 수 없는 오류');
    }
  } catch (err) {
    wait.classList.remove('thinking');
    wait.textContent = '요청 실패: ' + err.message;
  } finally {
    send.disabled = false; q.focus();
  }
});
</script>
</body>
</html>"""


SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-_]{32,99}$")


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-origin-verify") != ORIGIN_SECRET:
        return {"statusCode": 403, "body": "forbidden"}
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if method == "GET":
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "public, max-age=300",
            },
            "body": HTML,
        }

    if method != "POST":
        return {"statusCode": 405, "body": "method not allowed"}

    try:
        body = json.loads(event.get("body") or "{}")
        message = (body.get("message") or "").strip()
        session_id = body.get("sessionId") or (str(uuid.uuid4()) + "-web")
        if not message:
            return _json(400, {"error": "message is required"})
        if len(message) > 2000:
            return _json(400, {"error": "message too long (max 2000 chars)"})
        if not SESSION_RE.match(session_id):
            session_id = str(uuid.uuid4()) + "-web"

        response = client.invoke_harness(
            harnessArn=HARNESS_ARN,
            runtimeSessionId=session_id,
            messages=[{"role": "user", "content": [{"text": message}]}],
        )

        reply_parts = []
        usage = None
        latency = None
        for ev in response["stream"]:
            if "contentBlockDelta" in ev:
                delta = ev["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    reply_parts.append(delta["text"])
            elif "metadata" in ev:
                usage = ev["metadata"].get("usage")
                metrics = ev["metadata"].get("metrics") or {}
                latency = metrics.get("latencyMs")
            elif any(k in ev for k in ("validationException", "internalServerException", "runtimeClientError")):
                print(f"stream error event: {ev}")
                return _json(502, {"error": "에이전트 호출 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})

        return _json(200, {
            "reply": "".join(reply_parts),
            "sessionId": session_id,
            "usage": usage,
            "latencyMs": latency,
        })
    except Exception as exc:  # surface a safe message only
        print(f"invoke error: {exc}")
        return _json(502, {"error": "에이전트 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."})


def _json(code, obj):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(obj, ensure_ascii=False),
    }
