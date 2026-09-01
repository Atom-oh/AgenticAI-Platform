"""Agentic AI platform workspace demo — with a platform-engineer control plane.

User plane : catalog / builder (create agents) / data sources / chat.
Control    : risk-tier approval gate, creation-time smoke eval (evals-as-gate),
             per-agent token metering + estimated cost + budget circuit breaker,
             server-enforced allowedTools=[] on every agent invocation.

"Agents" are configs in DynamoDB executed via InvokeHarness systemPrompt
override on one shared Harness. Served behind CloudFront (the only public
entry point); requests lacking the x-origin-verify secret header are 403.
"""
import json
import os
import re
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

HARNESS_ARN = os.environ["HARNESS_ARN"]
ORIGIN_SECRET = os.environ["ORIGIN_SECRET"]
REGION = os.environ.get("HARNESS_REGION", "ap-northeast-2")
TABLE = os.environ.get("REGISTRY_TABLE", "agentic-book-demo-registry")

MAX_AGENTS = 20
MAX_DATASOURCES = 10
MAX_DS_CHARS = 20000
MAX_PROMPT_CHARS = 4000
MAX_MSG_CHARS = 2000
DEFAULT_BUDGET_TOKENS = 50000          # per-agent lifetime budget (demo circuit breaker)
# Rough on-demand list price for Claude Sonnet 4 (USD per MTok) — estimate only.
PRICE_IN, PRICE_OUT = 3.0, 15.0

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
SMOKE_PROBE = (
    "당신의 역할을 두 문장 이내로 소개하고, 답할 수 없는 질문을 받으면 어떻게 대응하는지 "
    "한 문장으로 설명하세요."
)


# ---------------------------------------------------------------- registry
def _list(pk):
    return ddb.query(KeyConditionExpression=Key("pk").eq(pk)).get("Items", [])


def _agent_public(i):
    usage = i.get("usage") or {}
    in_t, out_t = int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
    budget = int(i.get("budgetTokens", DEFAULT_BUDGET_TOKENS))
    return {
        "id": i["sk"], "name": i["name"], "description": i.get("description", ""),
        "datasourceIds": i.get("datasourceIds", []), "createdAt": int(i.get("createdAt", 0)),
        "status": i.get("status", "APPROVED"),          # legacy items: approved
        "riskTier": int(i.get("riskTier", 1)),
        "eval": i.get("evalResult"),
        "usage": {"invocations": int(usage.get("invocations", 0)),
                  "inputTokens": in_t, "outputTokens": out_t,
                  "totalTokens": in_t + out_t},
        "budgetTokens": budget,
        "estCostUsd": round(in_t * PRICE_IN / 1e6 + out_t * PRICE_OUT / 1e6, 4),
    }


def list_agents():
    items = sorted(_list("AGENT"), key=lambda x: x.get("createdAt", 0), reverse=True)
    return [_agent_public(i) for i in items]


def list_datasources():
    items = sorted(_list("DS"), key=lambda x: x.get("createdAt", 0), reverse=True)
    return [{"id": i["sk"], "name": i["name"], "chars": len(i.get("content", "")),
             "createdAt": int(i.get("createdAt", 0))} for i in items]


def get_item(pk, sk):
    return ddb.get_item(Key={"pk": pk, "sk": sk}).get("Item")


def add_usage(agent_id, usage):
    if not usage:
        return
    ddb.update_item(
        Key={"pk": "AGENT", "sk": agent_id},
        UpdateExpression="ADD #u.invocations :one, #u.inputTokens :i, #u.outputTokens :o",
        ExpressionAttributeNames={"#u": "usage"},
        ExpressionAttributeValues={
            ":one": Decimal(1),
            ":i": Decimal(int(usage.get("inputTokens", 0))),
            ":o": Decimal(int(usage.get("outputTokens", 0))),
        },
    )


# ---------------------------------------------------------------- harness
def invoke(session_id, message, system_prompt=None, block_tools=False, actor_id=None):
    kwargs = {
        "harnessArn": HARNESS_ARN,
        "runtimeSessionId": session_id,
        "messages": [{"role": "user", "content": [{"text": message}]}],
    }
    if system_prompt:
        kwargs["systemPrompt"] = [{"text": system_prompt}]
    if actor_id:
        # Memory scoping (guidebook Part 7): isolate managed-memory namespaces
        # per agent/user so long-term memories never leak across agents.
        kwargs["actorId"] = actor_id
    if block_tools:
        # Platform-enforced tool exposure control (Part 9 in the guidebook):
        # generated agents get no built-in tools (shell/file) regardless of
        # what the shared harness allows.
        kwargs["allowedTools"] = ["__platform_denied__"]
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
    corpus = []
    for ds_id in agent.get("datasourceIds", [])[:5]:
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


def smoke_eval(agent_id, prompt):
    """Creation-time smoke eval (evals-as-gate, minimal form)."""
    try:
        reply, usage, _ = invoke(f"eval-{uuid.uuid4().hex}-{agent_id}", SMOKE_PROBE,
                                 prompt, block_tools=True, actor_id=f"eval-{agent_id}")
        passed = len(reply.strip()) >= 20
        result = {"passed": passed, "probe": SMOKE_PROBE,
                  "sample": reply.strip()[:200], "at": int(time.time())}
        if usage:
            result["tokens"] = int(usage.get("inputTokens", 0)) + int(usage.get("outputTokens", 0))
        return result, usage
    except Exception as exc:
        print(f"smoke eval failed for {agent_id}: {exc}")
        return {"passed": False, "probe": SMOKE_PROBE,
                "sample": f"평가 실행 실패: 호출 오류", "at": int(time.time())}, None


# ---------------------------------------------------------------- api
def api(method, path, body):
    # --- agents ---
    if path == "/api/agents" and method == "GET":
        return 200, {"agents": list_agents()}

    if path == "/api/agents" and method == "POST":
        name = (body.get("name") or "").strip()[:30]
        desc = (body.get("description") or "").strip()[:120]
        prompt = (body.get("systemPrompt") or "").strip()
        risk = 1 if body.get("riskTier") == 1 else 2
        ds_ids = [d for d in (body.get("datasourceIds") or [])
                  if isinstance(d, str) and ID_RE.match(d)][:5]
        if not name or not prompt:
            return 400, {"error": "name과 systemPrompt는 필수입니다."}
        if len(prompt) > MAX_PROMPT_CHARS:
            return 400, {"error": f"systemPrompt는 {MAX_PROMPT_CHARS}자 이내여야 합니다."}
        if len(list_agents()) >= MAX_AGENTS:
            return 400, {"error": f"데모 한도({MAX_AGENTS}개)에 도달했습니다."}

        agent_id = uuid.uuid4().hex[:8]
        # evals-as-gate: smoke eval BEFORE the agent can be approved.
        eval_result, eval_usage = smoke_eval(agent_id, prompt)
        # governance gate: tier 1 (read-only Q&A) auto-approves only if the
        # smoke eval passed; tier 2 always waits for platform review.
        if not eval_result["passed"]:
            status = "PENDING"
        else:
            status = "APPROVED" if risk == 1 else "PENDING"

        item = {
            "pk": "AGENT", "sk": agent_id, "name": name, "description": desc,
            "systemPrompt": prompt, "datasourceIds": ds_ids,
            "createdAt": int(time.time()), "status": status, "riskTier": risk,
            "budgetTokens": DEFAULT_BUDGET_TOKENS,
            "evalResult": eval_result,
            "usage": {"invocations": 0, "inputTokens": 0, "outputTokens": 0},
        }
        ddb.put_item(Item=_to_ddb(item))
        if eval_usage:
            add_usage(agent_id, eval_usage)
        return 200, {"id": agent_id, "status": status, "eval": eval_result}

    m = re.match(r"^/api/agents/([a-f0-9]{8})$", path)
    if m and method == "DELETE":
        item = get_item("AGENT", m.group(1))
        if item and item.get("builtin"):
            return 400, {"error": "기본 제공 에이전트는 삭제할 수 없습니다."}
        ddb.delete_item(Key={"pk": "AGENT", "sk": m.group(1)})
        return 200, {"ok": True}

    # --- platform control plane ---
    if path == "/api/admin/overview" and method == "GET":
        agents = list_agents()
        return 200, {
            "agents": agents,
            "pending": [a for a in agents if a["status"] == "PENDING"],
            "totals": {
                "agents": len(agents),
                "invocations": sum(a["usage"]["invocations"] for a in agents),
                "totalTokens": sum(a["usage"]["totalTokens"] for a in agents),
                "estCostUsd": round(sum(a["estCostUsd"] for a in agents), 4),
            },
            "limits": {"maxAgents": MAX_AGENTS, "maxDatasources": MAX_DATASOURCES,
                       "defaultBudgetTokens": DEFAULT_BUDGET_TOKENS,
                       "reservedConcurrency": 5},
            "priceNote": f"추정 비용은 Claude Sonnet 4 온디맨드 표준 단가(${PRICE_IN}/M 입력, ${PRICE_OUT}/M 출력) 기준 근사치",
        }

    m = re.match(r"^/api/admin/agents/([a-f0-9]{8})/(approve|reject)$", path)
    if m and method == "POST":
        agent_id, action = m.group(1), m.group(2)
        if not get_item("AGENT", agent_id):
            return 404, {"error": "에이전트를 찾을 수 없습니다."}
        ddb.update_item(Key={"pk": "AGENT", "sk": agent_id},
                        UpdateExpression="SET #s = :s",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":s": "APPROVED" if action == "approve" else "REJECTED"})
        return 200, {"ok": True}

    m = re.match(r"^/api/admin/agents/([a-f0-9]{8})/budget$", path)
    if m and method == "POST":
        try:
            budget = int(body.get("budgetTokens", 0))
        except (TypeError, ValueError):
            return 400, {"error": "budgetTokens는 정수여야 합니다."}
        if not (1000 <= budget <= 2000000):
            return 400, {"error": "budgetTokens는 1,000 ~ 2,000,000 범위여야 합니다."}
        if not get_item("AGENT", m.group(1)):
            return 404, {"error": "에이전트를 찾을 수 없습니다."}
        ddb.update_item(Key={"pk": "AGENT", "sk": m.group(1)},
                        UpdateExpression="SET budgetTokens = :b",
                        ExpressionAttributeValues={":b": Decimal(budget)})
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
            return 400, {"error": f"content는 {MAX_DS_CHARS:,}자 이내여야 합니다."}
        if len(list_datasources()) >= MAX_DATASOURCES:
            return 400, {"error": f"데모 한도({MAX_DATASOURCES}개)에 도달했습니다."}
        ds_id = uuid.uuid4().hex[:8]
        ddb.put_item(Item={"pk": "DS", "sk": ds_id, "name": name, "content": content,
                           "createdAt": Decimal(int(time.time()))})
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
        status = agent.get("status", "APPROVED")
        if status != "APPROVED":
            return 403, {"error": f"이 에이전트는 아직 사용할 수 없습니다(상태: {status}). 플랫폼 운영 탭에서 승인해야 합니다."}
        # budget circuit breaker (Part 8: per-agent cost cap)
        usage = agent.get("usage") or {}
        used = int(usage.get("inputTokens", 0)) + int(usage.get("outputTokens", 0))
        budget = int(agent.get("budgetTokens", DEFAULT_BUDGET_TOKENS))
        if used >= budget:
            return 429, {"error": f"토큰 예산 소진({used:,}/{budget:,}). 플랫폼 운영 탭에서 예산을 증액하세요.",
                         "budgetExceeded": True}
        if not SESSION_RE.match(base):
            base = uuid.uuid4().hex + "-web"
        session_id = f"{base}-{agent_id}"
        # actor = agent x user: isolates long-term memory between users of the
        # same agent, not just between agents (Codex review finding).
        reply, u, latency = invoke(session_id, message, build_agent_prompt(agent), block_tools=True,
                                   actor_id=f"agent-{agent_id}-{base[:24]}")
        add_usage(agent_id, u)
        return 200, {"reply": reply, "sessionId": base, "usage": u, "latencyMs": latency,
                     "budget": {"used": used + (int(u.get("inputTokens", 0)) + int(u.get("outputTokens", 0)) if u else 0),
                                "total": budget}}

    # --- builder chat + spec extraction ---
    if path == "/api/builder" and method == "POST":
        message = (body.get("message") or "").strip()
        base = body.get("sessionId") or ""
        if not message or len(message) > MAX_MSG_CHARS:
            return 400, {"error": f"message는 1~{MAX_MSG_CHARS}자여야 합니다."}
        if not SESSION_RE.match(base):
            base = uuid.uuid4().hex + "-web"
        reply, usage, latency = invoke(f"{base}-builder", message, actor_id=f"builder-{base[:40]}")
        return 200, {"reply": reply, "sessionId": base, "usage": usage, "latencyMs": latency}

    if path == "/api/spec" and method == "POST":
        base = body.get("sessionId") or ""
        if not SESSION_RE.match(base):
            return 400, {"error": "빌더 대화를 먼저 진행하세요."}
        reply, _, _ = invoke(f"{base}-builder", BUILDER_SPEC_PROMPT, actor_id=f"builder-{base[:40]}")
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


def _to_ddb(obj):
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return Decimal(str(obj))
    return obj


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-origin-verify") != ORIGIN_SECRET:
        return {"statusCode": 403, "body": "forbidden"}

    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = http.get("path", "/")

    if method == "GET" and not path.startswith("/api/"):
        return {"statusCode": 200,
                "headers": {"Content-Type": "text/html; charset=utf-8",
                            "Cache-Control": "public, max-age=300"},
                "body": HTML}

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
      --text:#e2e8f0;--muted:#8fa3c0;--danger:#f87171;--ok:#34d399;--warn:#fbbf24;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',Apple SD Gothic Neo,sans-serif;
     display:flex;height:100vh;overflow:hidden}
nav{width:230px;background:var(--panel);border-right:1px solid var(--line);padding:18px 12px;
    display:flex;flex-direction:column;gap:4px;flex-shrink:0}
nav .brand{font-weight:700;font-size:15px;padding:6px 10px 16px}
nav .brand small{display:block;color:var(--muted);font-weight:400;font-size:11px;margin-top:3px}
nav button{all:unset;cursor:pointer;padding:10px 12px;border-radius:9px;font-size:13.5px;color:var(--muted)}
nav button:hover{background:#1b2740;color:var(--text)}
nav button.on{background:#1b2740;color:var(--accent);font-weight:600}
nav .sep{border-top:1px solid var(--line);margin:10px 6px;padding-top:8px;font-size:10.5px;color:var(--muted);
         letter-spacing:.06em}
nav .foot{margin-top:auto;font-size:11px;color:var(--muted);padding:10px}
nav .foot a{color:var(--accent);text-decoration:none}
main{flex:1;overflow-y:auto;padding:26px 30px}
h2{font-size:18px;margin-bottom:4px} h3.sec{font-size:14px;margin:24px 0 10px;color:var(--accent2)}
.sub{color:var(--muted);font-size:12.5px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:16px;
      display:flex;flex-direction:column;gap:8px}
.card h3{font-size:14.5px} .card p{font-size:12.5px;color:var(--muted);flex:1;line-height:1.5}
.card .tags{font-size:11px;color:var(--accent2)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.btn{border:none;border-radius:8px;padding:8px 14px;font-size:12.5px;font-weight:600;cursor:pointer}
.btn.p{background:var(--accent);color:#082f49}
.btn.g{background:#233152;color:var(--text)}
.btn.ok{background:var(--ok);color:#052e1c}
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
.badge{font-size:10.5px;border-radius:6px;padding:2px 8px;font-weight:700}
.badge.ok{background:#0a3a2a;color:var(--ok)} .badge.pend{background:#3a2f0a;color:var(--warn)}
.badge.rej{background:#3a0a14;color:var(--danger)}
.notice{background:#1b2740;border:1px solid var(--line);border-left:3px solid var(--accent2);
        border-radius:8px;padding:12px 14px;font-size:12.5px;color:var(--muted);margin-bottom:18px;line-height:1.6}
.checks{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.checks label{display:flex;align-items:center;gap:6px;margin:0;background:#1b2740;border:1px solid var(--line);
        border-radius:8px;padding:7px 11px;cursor:pointer;font-size:12px;color:var(--text)}
.checks input{width:auto}
.empty{color:var(--muted);font-size:13px;padding:30px;text-align:center;border:1px dashed var(--line);border-radius:12px}
.toast{position:fixed;bottom:22px;right:22px;background:#1b2740;border:1px solid var(--line);
       border-left:3px solid var(--ok);padding:12px 16px;border-radius:10px;font-size:13px;display:none;z-index:9;max-width:420px}
.toast.err{border-left-color:var(--danger)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:11.5px}
.bar{height:6px;background:#233152;border-radius:4px;overflow:hidden;min-width:80px}
.bar i{display:block;height:100%;background:var(--accent)}
.bar i.hot{background:var(--danger)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi b{display:block;font-size:20px;margin-top:4px}
.kpi span{font-size:11.5px;color:var(--muted)}
</style>
</head>
<body>
<nav>
  <div class="brand">Agentic AI 플랫폼<small>워크스페이스 데모 · AgentCore Harness</small></div>
  <button data-v="catalog" class="on">🗂️ 에이전트 카탈로그</button>
  <button data-v="builder">🛠️ 새 에이전트 만들기</button>
  <button data-v="datasources">📚 데이터소스</button>
  <button data-v="chat" id="navChat" style="display:none">💬 채팅</button>
  <div class="sep">PLATFORM ENGINEER</div>
  <button data-v="admin">⚙️ 플랫폼 운영</button>
  <div class="foot"><a href="https://www.atomai.click/AgenticAI-Platform/" target="_blank">가이드북 보기 ↗</a><br>
  <a href="https://www.atomai.click/AgenticAI-Platform/demo-architecture.html" target="_blank">아키텍처 다이어그램 ↗</a></div>
</nav>
<main id="main"></main>
<div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s); const main=$('#main');
let view='catalog', agents=[], dss=[], chatAgent=null, pendingSpec=null;
let base=null; try{base=localStorage.getItem('wsSession')}catch(e){}
if(!base){base=crypto.randomUUID().replace(/-/g,'')+'w'; try{localStorage.setItem('wsSession',base)}catch(e){}}
const chats={};

function toast(msg,err){const t=$('#toast');t.textContent=msg;t.className='toast'+(err?' err':'');
  t.style.display='block';setTimeout(()=>t.style.display='none',4200);}
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
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
function statusBadge(s){return s==='APPROVED'?'<span class="badge ok">승인됨</span>':
  s==='PENDING'?'<span class="badge pend">승인 대기</span>':'<span class="badge rej">거부됨</span>';}

// ---------- catalog ----------
async function renderCatalog(){
  main.innerHTML='<h2>에이전트 카탈로그</h2><div class="sub">승인된 에이전트만 사용할 수 있습니다. 승인 대기 항목은 플랫폼 운영 탭에서 검토합니다.</div><div class="grid" id="g">불러오는 중…</div>';
  try{agents=(await api('GET','api/agents')).agents;}catch(e){$('#g').innerHTML='<div class="empty">'+esc(e.message)+'</div>';return;}
  const g=$('#g');
  if(!agents.length){g.innerHTML='<div class="empty" style="grid-column:1/-1">아직 에이전트가 없습니다.<br>"새 에이전트 만들기"에서 빌더와 대화해 첫 에이전트를 만들어 보세요.</div>';return;}
  g.innerHTML='';
  for(const a of agents){
    const approved=a.status==='APPROVED';
    const pct=Math.min(100,Math.round(a.usage.totalTokens/a.budgetTokens*100));
    const c=document.createElement('div');c.className='card';
    c.innerHTML='<div class="row" style="justify-content:space-between"><h3>'+esc(a.name)+'</h3>'+statusBadge(a.status)+'</div>'+
      '<p>'+esc(a.description||'설명 없음')+'</p>'+
      '<div class="tags">Tier '+a.riskTier+(a.datasourceIds.length?' · 📚 '+a.datasourceIds.length+'개':'')+
      ' · 예산 '+pct+'% 사용</div>'+
      '<div class="row"><button class="btn p" data-use="'+a.id+'" '+(approved?'':'disabled')+'>'+(approved?'사용하기':'승인 대기 중')+'</button>'+
      (a.builtin?'':'<button class="btn d" data-del="'+a.id+'">삭제</button>')+'</div>';
    g.appendChild(c);
  }
  g.querySelectorAll('[data-use]:not([disabled])').forEach(b=>b.onclick=()=>{chatAgent=agents.find(x=>x.id===b.dataset.use);
    $('#navChat').style.display='block';nav('chat');});
  g.querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
    if(!confirm('이 에이전트를 삭제할까요?'))return;
    try{await api('DELETE','api/agents/'+b.dataset.del);toast('삭제됨');renderCatalog();}
    catch(e){toast(e.message,1);}});
}

// ---------- admin (platform control plane) ----------
async function renderAdmin(){
  main.innerHTML='<h2>⚙️ 플랫폼 운영</h2>'+
   '<div class="sub">플랫폼 엔지니어 관점 — 이 책의 6대 통증점 중 정확도(승인·평가), 비용(예산 캡·차지백), 권한(툴 차단)을 이 화면이 통제합니다. 데모라 인증 없이 열려 있습니다.</div>'+
   '<div id="body">불러오는 중…</div>';
  let d; try{d=await api('GET','api/admin/overview');}catch(e){$('#body').innerHTML='<div class="empty">'+esc(e.message)+'</div>';return;}
  const t=d.totals;
  let h='<div class="kpis">'+
   '<div class="kpi"><span>에이전트</span><b>'+t.agents+' / '+d.limits.maxAgents+'</b></div>'+
   '<div class="kpi"><span>총 호출</span><b>'+t.invocations.toLocaleString()+'</b></div>'+
   '<div class="kpi"><span>총 토큰</span><b>'+t.totalTokens.toLocaleString()+'</b></div>'+
   '<div class="kpi"><span>추정 비용(USD)</span><b>$'+t.estCostUsd+'</b></div></div>'+
   '<div class="notice">'+esc(d.priceNote)+' · 모든 에이전트 호출은 서버에서 <b>allowedTools 차단</b>(빌트인 shell/file 미노출)과 <b>토큰 예산 서킷 브레이커</b>가 강제됩니다.</div>';

  h+='<h3 class="sec">승인 대기 ('+d.pending.length+')</h3>';
  if(!d.pending.length){h+='<div class="empty">대기 중인 에이전트가 없습니다.</div>';}
  else{h+='<div class="grid">';
    for(const a of d.pending){
      const ev=a.eval||{};
      h+='<div class="card"><h3>'+esc(a.name)+' <span class="pill">Tier '+a.riskTier+'</span></h3>'+
        '<p>'+esc(a.description)+'</p>'+
        '<div class="tags">스모크 평가: '+(ev.passed?'✅ 통과':'❌ 실패')+'</div>'+
        (ev.sample?'<p style="font-size:11.5px;border-left:2px solid var(--line);padding-left:8px">'+esc(ev.sample)+'</p>':'')+
        '<div class="row"><button class="btn ok" data-ap="'+a.id+'">승인</button>'+
        '<button class="btn d" data-rj="'+a.id+'">거부</button></div></div>';
    } h+='</div>';}

  h+='<h3 class="sec">에이전트 현황 · 토큰 계량과 예산</h3><table><tr><th>에이전트</th><th>상태</th><th>Tier</th><th>호출</th><th>토큰(입력/출력)</th><th>추정 비용</th><th>예산 사용</th><th></th></tr>';
  for(const a of d.agents){
    const pct=Math.min(100,Math.round(a.usage.totalTokens/a.budgetTokens*100));
    h+='<tr><td>'+esc(a.name)+'</td><td>'+statusBadge(a.status)+'</td><td>'+a.riskTier+'</td>'+
      '<td>'+a.usage.invocations+'</td><td>'+a.usage.inputTokens.toLocaleString()+' / '+a.usage.outputTokens.toLocaleString()+'</td>'+
      '<td>$'+a.estCostUsd+'</td>'+
      '<td><div class="bar"><i class="'+(pct>=90?'hot':'')+'" style="width:'+pct+'%"></i></div>'+
      '<span style="font-size:10.5px;color:var(--muted)">'+a.usage.totalTokens.toLocaleString()+' / '+a.budgetTokens.toLocaleString()+' ('+pct+'%)</span></td>'+
      '<td><button class="btn g" data-bd="'+a.id+'" data-cur="'+a.budgetTokens+'">예산</button></td></tr>';
  }
  h+='</table>';
  $('#body').innerHTML=h;
  document.querySelectorAll('[data-ap]').forEach(b=>b.onclick=async()=>{
    try{await api('POST','api/admin/agents/'+b.dataset.ap+'/approve');toast('승인됨 — 카탈로그에서 사용 가능');renderAdmin();}
    catch(e){toast(e.message,1);}});
  document.querySelectorAll('[data-rj]').forEach(b=>b.onclick=async()=>{
    try{await api('POST','api/admin/agents/'+b.dataset.rj+'/reject');toast('거부됨');renderAdmin();}
    catch(e){toast(e.message,1);}});
  document.querySelectorAll('[data-bd]').forEach(b=>b.onclick=async()=>{
    const v=prompt('새 토큰 예산 (1,000 ~ 2,000,000)',b.dataset.cur);
    if(!v)return;
    try{await api('POST','api/admin/agents/'+b.dataset.bd+'/budget',{budgetTokens:parseInt(v,10)});
      toast('예산 변경됨');renderAdmin();}catch(e){toast(e.message,1);}});
}

// ---------- datasources ----------
async function renderDS(){
  main.innerHTML='<h2>데이터소스</h2>'+
   '<div class="sub">에이전트에 연결할 소규모 지식 코퍼스를 등록합니다.</div>'+
   '<div class="notice">이 데모는 소규모 코퍼스를 컨텍스트에 프리로딩하는 <b>CAG(Cache-Augmented Generation)</b> 방식을 씁니다 — 가이드북 Part 6의 결정 표 그대로입니다. 대규모 코퍼스는 Bedrock Knowledge Bases로 확장합니다.</div>'+
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
    if(!confirm('삭제할까요?'))return;
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
        let s='입력 '+d.usage.inputTokens+' / 출력 '+d.usage.outputTokens+' 토큰 · '+d.latencyMs+'ms';
        if(d.budget)s+=' · 예산 '+d.budget.used.toLocaleString()+'/'+d.budget.total.toLocaleString();
        m.textContent=s;w.appendChild(m);}
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
    ' <span class="pill">🔒 툴 차단</span>'+
    (chatAgent.datasourceIds.length?' <span class="pill">📚 '+chatAgent.datasourceIds.length+'개</span>':''),
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
   '빌더와 대화 → <b>스펙으로 변환</b> → 생성. 생성 시 <b>스모크 평가</b>가 자동 실행되고, Tier 2 이상 또는 평가 실패는 <b>승인 대기</b>로 들어갑니다 (가이드북 Part 11 골든 패스).',
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
  main.innerHTML='<h2>에이전트 생성 확인</h2><div class="sub">스펙 검토 → 위험 등급 선택 → 생성. 생성 즉시 스모크 평가가 실행됩니다.</div>'+
   '<label>이름</label><input id="an" maxlength="30" value="'+esc(s.name)+'">'+
   '<label>설명</label><input id="ad" maxlength="120" value="'+esc(s.description)+'">'+
   '<label>시스템 프롬프트</label><textarea id="ap" rows="10">'+esc(s.systemPrompt)+'</textarea>'+
   '<label>위험 등급 (차등 게이트 — 가이드북 Part 11)</label>'+
   '<div class="checks">'+
   '<label><input type="radio" name="tier" value="1" checked> Tier 1 — 읽기 전용 Q&A (평가 통과 시 자동 승인)</label>'+
   '<label><input type="radio" name="tier" value="2"> Tier 2 — 민감/외부 데이터 사용 (플랫폼 승인 필요)</label></div>'+
   '<label>데이터소스 연결 (선택, 최대 5개)</label><div class="checks" id="ac">'+
   (dss.length?dss.map(d=>'<label><input type="checkbox" value="'+d.id+'"> '+esc(d.name)+' <span style="color:var(--muted)">('+d.chars.toLocaleString()+'자)</span></label>').join(''):'<span class="sub" style="margin:0">등록된 데이터소스가 없습니다.</span>')+
   '</div><div style="margin-top:18px" class="row">'+
   '<button class="btn p" id="ok">에이전트 생성 (스모크 평가 포함)</button>'+
   '<button class="btn g" id="back">빌더로 돌아가기</button></div>';
  $('#back').onclick=()=>nav('builder');
  $('#ok').onclick=async()=>{
    const ids=[...document.querySelectorAll('#ac input:checked')].map(x=>x.value);
    const tier=parseInt(document.querySelector('input[name=tier]:checked').value,10);
    $('#ok').disabled=true;$('#ok').textContent='생성 + 스모크 평가 중… (~15초)';
    try{const d=await api('POST','api/agents',{name:$('#an').value,description:$('#ad').value,
        systemPrompt:$('#ap').value,datasourceIds:ids,riskTier:tier});
      pendingSpec=null;
      toast(d.status==='APPROVED'?'✅ 평가 통과 — 자동 승인되어 카탈로그에 등록됨'
        :'⏳ 승인 대기 상태로 등록됨 — 플랫폼 운영 탭에서 검토하세요');
      nav(d.status==='APPROVED'?'catalog':'admin');}
    catch(e){toast(e.message,1);$('#ok').disabled=false;$('#ok').textContent='에이전트 생성 (스모크 평가 포함)';}};
}

function render(){
  if(view==='catalog')renderCatalog();
  else if(view==='builder')renderBuilder();
  else if(view==='datasources')renderDS();
  else if(view==='chat')renderChat();
  else if(view==='admin')renderAdmin();
}
render();
</script>
</body>
</html>"""
