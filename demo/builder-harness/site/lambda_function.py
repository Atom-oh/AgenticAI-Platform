"""NEXUS — Agentic AI Platform (v4, commercial-grade demo).

Pillars: team self-service agents / central MCP (Gateway+Identity) /
Agent Registry governance / Agent Skills (S3) / ontology / AI wiki /
coverage graph / GUI workflows (chain & loop, async) / Cognito auth + audit.

Request path: CloudFront (secret header) -> API GW (/api/* = Cognito JWT
authorizer) -> this Lambda. Static SPA is served from the bundled ./static.
"""
import base64
import json
import mimetypes
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
AGENT_REGISTRY_ID = os.environ.get("AGENT_REGISTRY_ID", "b2hOSZL4eOhDXAyk")
AGENT_REGISTRY_REGION = os.environ.get("AGENT_REGISTRY_REGION", "us-east-1")
SKILLS_BUCKET = os.environ.get("SKILLS_BUCKET", "agentic-nexus-skills-180294183052")
SELF_FN = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "agentic-book-demo-site")
GATEWAY_URL = os.environ.get("PLATFORM_GATEWAY_URL", "")

MAX_AGENTS, MAX_DATASOURCES, MAX_SKILLS = 30, 15, 15
MAX_DS_CHARS, MAX_PROMPT_CHARS, MAX_MSG_CHARS = 20000, 4000, 2000
MAX_SKILL_CHARS, MAX_WIKI_CHARS = 8000, 30000
DEFAULT_BUDGET_TOKENS = 50000
WF_MAX_STEPS, WF_MAX_LOOPS = 4, 4
PRICE_IN, PRICE_OUT = 3.0, 15.0  # USD/MTok, Claude Sonnet 4 on-demand approx

agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
registry = boto3.client("bedrock-agentcore-control", region_name=AGENT_REGISTRY_REGION)
ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
s3 = boto3.client("s3", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)

SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-_]{20,80}$")
ID_RE = re.compile(r"^[a-f0-9]{8}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def actor_safe(s):
    """actorId charset: [a-zA-Z0-9-_/] (+ optional :segments)."""
    return re.sub(r"[^a-zA-Z0-9-_]", "-", s)[:40] or "anon"

BUILDER_SPEC_PROMPT = (
    "지금까지의 대화 내용을 바탕으로, 생성할 에이전트의 스펙을 아래 JSON 형식으로만 출력하세요. "
    "다른 설명 없이 JSON 하나만 출력합니다.\n"
    '{"name": "에이전트 이름(한국어, 30자 이내)", '
    '"description": "한 문장 설명(80자 이내)", '
    '"systemPrompt": "이 에이전트의 시스템 프롬프트(한국어, 상세하게, 2000자 이내)"}'
)
SMOKE_PROBE = ("당신의 역할을 두 문장 이내로 소개하고, 답할 수 없는 질문을 받으면 "
               "어떻게 대응하는지 한 문장으로 설명하세요.")


# ---------------------------------------------------------------- utils
def _to_ddb(o):
    if isinstance(o, dict):
        return {k: _to_ddb(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_to_ddb(v) for v in o]
    if isinstance(o, bool):
        return o
    if isinstance(o, (int, float)):
        return Decimal(str(o))
    return o


def _plain(o):
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    return o


def _list(pk):
    return [_plain(i) for i in ddb.query(
        KeyConditionExpression=Key("pk").eq(pk)).get("Items", [])]


def get_item(pk, sk):
    it = ddb.get_item(Key={"pk": pk, "sk": sk}).get("Item")
    return _plain(it) if it else None


def audit(actor, action, target, detail=""):
    try:
        ddb.put_item(Item=_to_ddb({
            "pk": "AUDIT", "sk": f"{int(time.time()*1000):015d}-{uuid.uuid4().hex[:6]}",
            "actor": actor, "action": action, "target": target,
            "detail": str(detail)[:300], "at": int(time.time())}))
    except Exception as exc:
        print(f"audit write failed: {exc}")


# ---------------------------------------------------------------- principal
def principal_from(event):
    claims = (event.get("requestContext", {}).get("authorizer", {})
              .get("jwt", {}).get("claims", {})) or {}
    email = claims.get("email") or claims.get("cognito:username") or ""
    groups_raw = claims.get("cognito:groups") or ""
    if isinstance(groups_raw, str):
        groups = [g.strip() for g in groups_raw.strip("[]").split(",") if g.strip()]
    else:
        groups = list(groups_raw)
    teams = [g for g in groups if g.startswith("team-")]
    return {"email": email, "groups": groups,
            "isAdmin": "platform-admins" in groups,
            "team": teams[0] if teams else ("platform" if "platform-admins" in groups else "unassigned")}


def can_manage(p, item):
    return p["isAdmin"] or item.get("ownerEmail") == p["email"] or \
        (item.get("team") and item.get("team") == p["team"])


# ---------------------------------------------------------------- harness
def invoke(session_id, message, system_prompt=None, block_tools=False,
           actor_id=None, skills=None):
    kwargs = {"harnessArn": HARNESS_ARN, "runtimeSessionId": session_id,
              "messages": [{"role": "user", "content": [{"text": message}]}]}
    if system_prompt:
        kwargs["systemPrompt"] = [{"text": system_prompt}]
    if actor_id:
        kwargs["actorId"] = actor_id            # memory scoping (Part 7)
    if block_tools:
        kwargs["allowedTools"] = ["__platform_denied__"]  # tool control (Part 9)
    if skills:
        kwargs["skills"] = skills               # Agent Skills via S3 source
    response = agentcore.invoke_harness(**kwargs)
    parts, usage, latency = [], None, None
    for ev in response["stream"]:
        if "contentBlockDelta" in ev:
            d = ev["contentBlockDelta"].get("delta", {})
            if "text" in d:
                parts.append(d["text"])
        elif "metadata" in ev:
            usage = ev["metadata"].get("usage")
            latency = (ev["metadata"].get("metrics") or {}).get("latencyMs")
        elif any(k in ev for k in ("validationException", "internalServerException",
                                   "runtimeClientError")):
            raise RuntimeError(f"stream error event: {ev}")
    return "".join(parts), usage, latency


def _agent_public(i):
    u = i.get("usage") or {}
    in_t, out_t = int(u.get("inputTokens", 0)), int(u.get("outputTokens", 0))
    return {"id": i["sk"], "name": i["name"], "description": i.get("description", ""),
            "datasourceIds": i.get("datasourceIds", []),
            "skillIds": i.get("skillIds", []),
            "useOntology": bool(i.get("useOntology")),
            "createdAt": int(i.get("createdAt", 0)),
            "status": i.get("status", "APPROVED"),
            "riskTier": int(i.get("riskTier", 1)),
            "eval": i.get("evalResult"),
            "ownerEmail": i.get("ownerEmail", ""), "team": i.get("team", ""),
            "usage": {"invocations": int(u.get("invocations", 0)),
                      "inputTokens": in_t, "outputTokens": out_t,
                      "totalTokens": in_t + out_t},
            "budgetTokens": int(i.get("budgetTokens", DEFAULT_BUDGET_TOKENS)),
            "estCostUsd": round(in_t * PRICE_IN / 1e6 + out_t * PRICE_OUT / 1e6, 4),
            "registryRecordArn": i.get("registryRecordArn")}


def list_agents():
    return [_agent_public(i) for i in
            sorted(_list("AGENT"), key=lambda x: x.get("createdAt", 0), reverse=True)]


def add_usage(agent_id, usage):
    if not usage:
        return
    ddb.update_item(Key={"pk": "AGENT", "sk": agent_id},
                    UpdateExpression="ADD #u.invocations :one, #u.inputTokens :i, #u.outputTokens :o",
                    ExpressionAttributeNames={"#u": "usage"},
                    ExpressionAttributeValues={":one": Decimal(1),
                                               ":i": Decimal(int(usage.get("inputTokens", 0))),
                                               ":o": Decimal(int(usage.get("outputTokens", 0)))})


def build_agent_prompt(agent):
    blocks = [agent.get("systemPrompt", "").strip() or "당신은 유용한 어시스턴트입니다."]
    corpus = []
    for ds_id in agent.get("datasourceIds", [])[:5]:
        ds = get_item("DS", ds_id)
        if ds and ds.get("content"):
            corpus.append(f"### {ds['name']}\n{ds['content']}")
    if corpus:
        blocks.append("\n\n## 참고 자료 (데이터소스)\n아래 자료를 근거로 답하고, "
                      "자료에 없는 내용은 모른다고 답하세요.\n\n" + "\n\n---\n\n".join(corpus))
    sb = skills_block(agent)
    if sb:
        blocks.append("\n\n## 게시된 스킬 (반드시 준수)\n" + sb)
    if agent.get("useOntology"):
        onto = ontology_context()
        if onto:
            blocks.append("\n\n## 조직 온톨로지 (AI-Ready Data)\n"
                          "아래는 조직의 엔티티·관계 그래프다. 답변 시 이 구조를 근거로 활용하라.\n"
                          + onto)
    blocks.append("\n답변은 한국어로 하세요.")
    return "".join(blocks)


def skills_block(agent):
    """InvokeHarness per-invocation skills currently supports only `path`
    (no s3 source yet), so published SKILL.md content is injected into the
    system prompt. S3 + Agent Registry remain the publishing/catalog layer."""
    parts = []
    for sid in agent.get("skillIds", [])[:2]:
        sk = get_item("SKILL", sid)
        if sk and sk.get("skillMd"):
            parts.append(f"### 스킬: {sk['name']}\n{sk['skillMd'][:3000]}")
    return "\n\n".join(parts)


def smoke_eval(agent_id, prompt):
    try:
        reply, usage, _ = invoke(f"eval-{uuid.uuid4().hex}-{agent_id}", SMOKE_PROBE,
                                 prompt, block_tools=True, actor_id=f"eval-{agent_id}")
        result = {"passed": len(reply.strip()) >= 20, "probe": SMOKE_PROBE,
                  "sample": reply.strip()[:200], "at": int(time.time())}
        return result, usage
    except Exception as exc:
        print(f"smoke eval failed for {agent_id}: {exc}")
        return {"passed": False, "probe": SMOKE_PROBE,
                "sample": "평가 실행 실패: 호출 오류", "at": int(time.time())}, None


# ------------------------------------------------- AgentCore Agent Registry
def registry_register(agent_id, name, desc, risk, team):
    rec = registry.create_registry_record(
        registryId=AGENT_REGISTRY_ID, name=f"agent_{agent_id}",
        description=f"[{team}] {name} — {desc or 'no description'} (Tier {risk})"[:250],
        descriptorType="CUSTOM",
        descriptors={"custom": {"inlineContent": json.dumps(
            {"platform": "nexus", "agentId": agent_id, "team": team,
             "riskTier": risk, "execution": "harness-systemPrompt-override"},
            ensure_ascii=False)}})
    record_id = rec["recordArn"].rsplit("/", 1)[-1]
    for _ in range(20):
        if registry.get_registry_record(registryId=AGENT_REGISTRY_ID,
                                        recordId=record_id).get("status") == "DRAFT":
            break
        time.sleep(1)
    registry.submit_registry_record_for_approval(
        registryId=AGENT_REGISTRY_ID, recordId=record_id)
    return record_id, rec["recordArn"]


def registry_register_skill(skill_id, name, desc, skill_md):
    rec = registry.create_registry_record(
        registryId=AGENT_REGISTRY_ID, name=f"skill_{skill_id}",
        description=f"{name} — {desc or ''}"[:250],
        descriptorType="AGENT_SKILLS",
        descriptors={"agentSkills": {"skillMd": {
            "name": re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-",
                           f"skill-{skill_id}")).strip("-"),
            "inlineContent": skill_md[:4000]}}})
    record_id = rec["recordArn"].rsplit("/", 1)[-1]
    for _ in range(20):
        if registry.get_registry_record(registryId=AGENT_REGISTRY_ID,
                                        recordId=record_id).get("status") == "DRAFT":
            break
        time.sleep(1)
    registry.submit_registry_record_for_approval(
        registryId=AGENT_REGISTRY_ID, recordId=record_id)
    try:
        registry.update_registry_record_status(
            registryId=AGENT_REGISTRY_ID, recordId=record_id,
            status="APPROVED", statusReason="skill published via platform")
    except Exception as exc:
        print(f"skill approve failed: {exc}")
    return record_id, rec["recordArn"]


def registry_set_status(record_id, status, reason):
    registry.update_registry_record_status(
        registryId=AGENT_REGISTRY_ID, recordId=record_id,
        status=status, statusReason=reason[:250])


def registry_delete(record_id):
    try:
        registry.delete_registry_record(registryId=AGENT_REGISTRY_ID, recordId=record_id)
    except Exception as exc:
        print(f"registry delete failed ({record_id}): {exc}")


# ---------------------------------------------------------------- ontology
def ontology_context(cap=6000):
    types = _list("ONT_TYPE")
    ents = _list("ONT_ENT")
    rels = _list("ONT_REL")
    if not ents:
        return ""
    lines = []
    tmap = {t["sk"]: t["name"] for t in types}
    emap = {e["sk"]: e for e in ents}
    for e in ents[:60]:
        attrs = ", ".join(f"{k}={v}" for k, v in (e.get("attrs") or {}).items())
        lines.append(f"- [{tmap.get(e.get('typeId'), '?')}] {e['name']}" +
                     (f" ({attrs})" if attrs else ""))
    for r in rels[:80]:
        f, t = emap.get(r.get("fromId")), emap.get(r.get("toId"))
        if f and t:
            lines.append(f"- {f['name']} --{r.get('relation','관련')}--> {t['name']}")
    return "\n".join(lines)[:cap]


# ---------------------------------------------------------------- workflows
def wf_run_async(run_id):
    lam.invoke(FunctionName=SELF_FN, InvocationType="Event",
               Payload=json.dumps({"nexusAsync": "wfrun", "runId": run_id}).encode())


def wf_execute(run_id):
    run = get_item("WFRUN", run_id)
    if not run:
        return
    wf = get_item("WF", run["wfId"])
    if not wf:
        return
    steps_out, total_tokens = [], 0
    text = run.get("input", "")
    try:
        if wf.get("type") == "loop":
            agent = get_item("AGENT", wf["steps"][0]["agentId"])
            for it in range(min(int(wf.get("maxIters", 3)), WF_MAX_LOOPS)):
                msg = (wf["steps"][0].get("instruction", "") + "\n\n입력:\n" + text +
                       "\n\n(작업이 완료되어 더 개선할 것이 없으면 답변 마지막 줄에 DONE 이라고만 쓰세요)")
                reply, u, _ = invoke(f"wf-{run_id}-{it}-{uuid.uuid4().hex}", msg[:8000],
                                     build_agent_prompt(agent), block_tools=True,
                                     actor_id=f"wf-{wf['sk']}")
                tok = (int(u.get("inputTokens", 0)) + int(u.get("outputTokens", 0))) if u else 0
                total_tokens += tok
                add_usage(agent["sk"], u)
                done = reply.rstrip().endswith("DONE")
                steps_out.append({"i": it, "agentId": agent["sk"], "agentName": agent["name"],
                                  "reply": reply[:4000], "tokens": tok, "done": done})
                text = reply
                _wf_progress(run_id, steps_out, total_tokens, "RUNNING")
                if done:
                    break
        else:  # chain
            for i, st in enumerate(wf.get("steps", [])[:WF_MAX_STEPS]):
                agent = get_item("AGENT", st["agentId"])
                if not agent:
                    steps_out.append({"i": i, "agentId": st["agentId"],
                                      "reply": "(에이전트 없음 — 건너뜀)", "tokens": 0})
                    continue
                msg = (st.get("instruction", "") + "\n\n입력:\n" + text)[:8000]
                reply, u, _ = invoke(f"wf-{run_id}-{i}-{uuid.uuid4().hex}", msg,
                                     build_agent_prompt(agent), block_tools=True,
                                     actor_id=f"wf-{wf['sk']}")
                tok = (int(u.get("inputTokens", 0)) + int(u.get("outputTokens", 0))) if u else 0
                total_tokens += tok
                add_usage(agent["sk"], u)
                steps_out.append({"i": i, "agentId": agent["sk"], "agentName": agent["name"],
                                  "reply": reply[:4000], "tokens": tok})
                text = reply
                _wf_progress(run_id, steps_out, total_tokens, "RUNNING")
        _wf_progress(run_id, steps_out, total_tokens, "SUCCEEDED", text[:4000])
    except Exception as exc:
        print(f"wf run {run_id} failed: {exc}")
        _wf_progress(run_id, steps_out, total_tokens, "FAILED", f"오류: {exc}"[:500])


def _wf_progress(run_id, steps, tokens, status, output=None):
    upd = {"steps": steps, "totalTokens": tokens, "status": status,
           "updatedAt": int(time.time())}
    if output is not None:
        upd["output"] = output
    ddb.update_item(Key={"pk": "WFRUN", "sk": run_id},
                    UpdateExpression="SET " + ", ".join(f"#{k}=:{k}" for k in upd),
                    ExpressionAttributeNames={f"#{k}": k for k in upd},
                    ExpressionAttributeValues={f":{k}": _to_ddb(v) for k, v in upd.items()})


# ---------------------------------------------------------------- api
def api(method, path, body, p):
    email, team = p["email"], p["team"]

    if path == "/api/me" and method == "GET":
        return 200, {"email": email, "team": team, "isAdmin": p["isAdmin"],
                     "groups": p["groups"]}

    # ---------------- agents ----------------
    if path == "/api/agents" and method == "GET":
        return 200, {"agents": list_agents()}

    if path == "/api/agents" and method == "POST":
        name = (body.get("name") or "").strip()[:30]
        desc = (body.get("description") or "").strip()[:120]
        prompt = (body.get("systemPrompt") or "").strip()
        risk = 1 if body.get("riskTier") == 1 else 2
        ds_ids = [d for d in (body.get("datasourceIds") or [])
                  if isinstance(d, str) and ID_RE.match(d)][:5]
        skill_ids = [d for d in (body.get("skillIds") or [])
                     if isinstance(d, str) and ID_RE.match(d)][:3]
        use_onto = bool(body.get("useOntology"))
        if not name or not prompt:
            return 400, {"error": "name과 systemPrompt는 필수입니다."}
        if len(prompt) > MAX_PROMPT_CHARS:
            return 400, {"error": f"systemPrompt는 {MAX_PROMPT_CHARS}자 이내여야 합니다."}
        if len(_list("AGENT")) >= MAX_AGENTS:
            return 400, {"error": f"데모 한도({MAX_AGENTS}개)에 도달했습니다."}
        agent_id = uuid.uuid4().hex[:8]
        eval_result, eval_usage = smoke_eval(agent_id, prompt)
        status = "APPROVED" if (eval_result["passed"] and risk == 1) else "PENDING"
        record_id = record_arn = None
        try:
            record_id, record_arn = registry_register(agent_id, name, desc, risk, team)
            if status == "APPROVED":
                registry_set_status(record_id, "APPROVED",
                                    f"auto-approve: Tier 1 + smoke eval passed (by {email})")
        except Exception as exc:
            print(f"registry sync failed for {agent_id}: {exc}")
        ddb.put_item(Item=_to_ddb({
            "pk": "AGENT", "sk": agent_id, "name": name, "description": desc,
            "systemPrompt": prompt, "datasourceIds": ds_ids, "skillIds": skill_ids,
            "useOntology": use_onto, "createdAt": int(time.time()),
            "status": status, "riskTier": risk, "budgetTokens": DEFAULT_BUDGET_TOKENS,
            "evalResult": eval_result, "ownerEmail": email, "team": team,
            "registryRecordId": record_id, "registryRecordArn": record_arn,
            "usage": {"invocations": 0, "inputTokens": 0, "outputTokens": 0}}))
        if eval_usage:
            add_usage(agent_id, eval_usage)
        audit(email, "agent.create", agent_id, f"{name} tier{risk} -> {status}")
        return 200, {"id": agent_id, "status": status, "eval": eval_result,
                     "registryRecordArn": record_arn}

    m = re.match(r"^/api/agents/([a-f0-9]{8})$", path)
    if m and method == "DELETE":
        item = get_item("AGENT", m.group(1))
        if not item:
            return 404, {"error": "에이전트를 찾을 수 없습니다."}
        if not can_manage(p, item):
            return 403, {"error": "이 에이전트를 삭제할 권한이 없습니다(소유자/팀/관리자만)."}
        if item.get("registryRecordId"):
            registry_delete(item["registryRecordId"])
        ddb.delete_item(Key={"pk": "AGENT", "sk": m.group(1)})
        audit(email, "agent.delete", m.group(1), item.get("name", ""))
        return 200, {"ok": True}

    # ---------------- chat / builder ----------------
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
        if agent.get("status", "APPROVED") != "APPROVED":
            return 403, {"error": f"아직 사용할 수 없습니다(상태: {agent.get('status')}). 운영 탭에서 승인이 필요합니다."}
        u0 = agent.get("usage") or {}
        used = int(u0.get("inputTokens", 0)) + int(u0.get("outputTokens", 0))
        budget = int(agent.get("budgetTokens", DEFAULT_BUDGET_TOKENS))
        if used >= budget:
            return 429, {"error": f"토큰 예산 소진({used:,}/{budget:,}). 운영 탭에서 증액하세요.",
                         "budgetExceeded": True}
        if not SESSION_RE.match(base):
            base = uuid.uuid4().hex + "-web"
        reply, u, latency = invoke(
            f"{base}-{agent_id}", message, build_agent_prompt(agent),
            block_tools=True, actor_id=f"agent-{agent_id}-{actor_safe(email or base)[:24]}")
        add_usage(agent_id, u)
        return 200, {"reply": reply, "sessionId": base, "usage": u, "latencyMs": latency,
                     "budget": {"used": used + ((int(u.get("inputTokens", 0)) +
                                                 int(u.get("outputTokens", 0))) if u else 0),
                                "total": budget}}

    if path == "/api/builder" and method == "POST":
        message = (body.get("message") or "").strip()
        base = body.get("sessionId") or ""
        if not message or len(message) > MAX_MSG_CHARS:
            return 400, {"error": f"message는 1~{MAX_MSG_CHARS}자여야 합니다."}
        if not SESSION_RE.match(base):
            base = uuid.uuid4().hex + "-web"
        reply, usage, latency = invoke(f"{base}-builder", message,
                                       actor_id=f"builder-{actor_safe(email or base)[:32]}")
        return 200, {"reply": reply, "sessionId": base, "usage": usage, "latencyMs": latency}

    if path == "/api/spec" and method == "POST":
        base = body.get("sessionId") or ""
        if not SESSION_RE.match(base):
            return 400, {"error": "빌더 대화를 먼저 진행하세요."}
        reply, _, _ = invoke(f"{base}-builder", BUILDER_SPEC_PROMPT,
                             actor_id=f"builder-{actor_safe(email or base)[:32]}")
        jm = re.search(r"\{.*\}", reply, re.S)
        if not jm:
            return 502, {"error": "스펙 JSON을 추출하지 못했습니다. 대화를 조금 더 진행해 보세요."}
        try:
            spec = json.loads(jm.group(0))
        except json.JSONDecodeError:
            return 502, {"error": "스펙 JSON 파싱에 실패했습니다. 다시 시도해 주세요."}
        return 200, {"spec": {"name": str(spec.get("name", ""))[:30],
                              "description": str(spec.get("description", ""))[:120],
                              "systemPrompt": str(spec.get("systemPrompt", ""))[:MAX_PROMPT_CHARS]}}

    # ---------------- datasources ----------------
    if path == "/api/datasources" and method == "GET":
        return 200, {"datasources": [
            {"id": i["sk"], "name": i["name"], "chars": len(i.get("content", "")),
             "ownerEmail": i.get("ownerEmail", ""), "team": i.get("team", ""),
             "createdAt": int(i.get("createdAt", 0))}
            for i in sorted(_list("DS"), key=lambda x: x.get("createdAt", 0), reverse=True)]}

    if path == "/api/datasources" and method == "POST":
        name = (body.get("name") or "").strip()[:40]
        content = (body.get("content") or "").strip()
        if not name or not content:
            return 400, {"error": "name과 content는 필수입니다."}
        if len(content) > MAX_DS_CHARS:
            return 400, {"error": f"content는 {MAX_DS_CHARS:,}자 이내여야 합니다."}
        if len(_list("DS")) >= MAX_DATASOURCES:
            return 400, {"error": f"데모 한도({MAX_DATASOURCES}개)에 도달했습니다."}
        ds_id = uuid.uuid4().hex[:8]
        ddb.put_item(Item=_to_ddb({"pk": "DS", "sk": ds_id, "name": name,
                                   "content": content, "ownerEmail": email,
                                   "team": team, "createdAt": int(time.time())}))
        audit(email, "datasource.create", ds_id, name)
        return 200, {"id": ds_id}

    m = re.match(r"^/api/datasources/([a-f0-9]{8})$", path)
    if m and method == "DELETE":
        item = get_item("DS", m.group(1))
        if item and not can_manage(p, item):
            return 403, {"error": "삭제 권한이 없습니다."}
        ddb.delete_item(Key={"pk": "DS", "sk": m.group(1)})
        audit(email, "datasource.delete", m.group(1), (item or {}).get("name", ""))
        return 200, {"ok": True}

    # ---------------- skills ----------------
    if path == "/api/skills" and method == "GET":
        return 200, {"skills": [
            {"id": i["sk"], "name": i["name"], "description": i.get("description", ""),
             "chars": len(i.get("skillMd", "")), "ownerEmail": i.get("ownerEmail", ""),
             "team": i.get("team", ""), "registryRecordArn": i.get("registryRecordArn"),
             "createdAt": int(i.get("createdAt", 0))}
            for i in sorted(_list("SKILL"), key=lambda x: x.get("createdAt", 0), reverse=True)]}

    m = re.match(r"^/api/skills/([a-f0-9]{8})$", path)
    if m and method == "GET":
        it = get_item("SKILL", m.group(1))
        return (200, {"skill": it}) if it else (404, {"error": "not found"})

    if path == "/api/skills" and method == "POST":
        name = (body.get("name") or "").strip()[:40]
        desc = (body.get("description") or "").strip()[:120]
        skill_md = (body.get("skillMd") or "").strip()
        if not name or not skill_md:
            return 400, {"error": "name과 skillMd는 필수입니다."}
        if len(skill_md) > MAX_SKILL_CHARS:
            return 400, {"error": f"SKILL.md는 {MAX_SKILL_CHARS:,}자 이내여야 합니다."}
        if len(_list("SKILL")) >= MAX_SKILLS:
            return 400, {"error": f"데모 한도({MAX_SKILLS}개)에 도달했습니다."}
        skill_id = uuid.uuid4().hex[:8]
        if not skill_md.startswith("---"):
            skill_md = f"---\nname: {name}\ndescription: {desc or name}\n---\n\n" + skill_md
        s3.put_object(Bucket=SKILLS_BUCKET, Key=f"skills/{skill_id}/SKILL.md",
                      Body=skill_md.encode(), ContentType="text/markdown")
        record_id = record_arn = None
        try:
            record_id, record_arn = registry_register_skill(skill_id, name, desc, skill_md)
        except Exception as exc:
            print(f"skill registry sync failed: {exc}")
        ddb.put_item(Item=_to_ddb({"pk": "SKILL", "sk": skill_id, "name": name,
                                   "description": desc, "skillMd": skill_md,
                                   "ownerEmail": email, "team": team,
                                   "registryRecordId": record_id,
                                   "registryRecordArn": record_arn,
                                   "createdAt": int(time.time())}))
        audit(email, "skill.create", skill_id, name)
        return 200, {"id": skill_id, "registryRecordArn": record_arn}

    if m and method == "DELETE":
        item = get_item("SKILL", m.group(1))
        if item and not can_manage(p, item):
            return 403, {"error": "삭제 권한이 없습니다."}
        if item and item.get("registryRecordId"):
            registry_delete(item["registryRecordId"])
        try:
            s3.delete_object(Bucket=SKILLS_BUCKET, Key=f"skills/{m.group(1)}/SKILL.md")
        except Exception:
            pass
        ddb.delete_item(Key={"pk": "SKILL", "sk": m.group(1)})
        audit(email, "skill.delete", m.group(1), (item or {}).get("name", ""))
        return 200, {"ok": True}

    # ---------------- ontology ----------------
    kinds = {"types": "ONT_TYPE", "entities": "ONT_ENT", "relations": "ONT_REL"}
    m = re.match(r"^/api/ontology/(types|entities|relations)(?:/([a-f0-9]{8}))?$", path)
    if m:
        pk, oid = kinds[m.group(1)], m.group(2)
        if method == "GET" and not oid:
            return 200, {m.group(1): sorted(_list(pk), key=lambda x: x.get("createdAt", 0))}
        if method == "POST" and not oid:
            if len(_list(pk)) >= 80:
                return 400, {"error": "데모 한도(80개)에 도달했습니다."}
            item = {"pk": pk, "sk": uuid.uuid4().hex[:8], "createdAt": int(time.time()),
                    "ownerEmail": email, "team": team}
            if pk == "ONT_TYPE":
                item.update({"name": (body.get("name") or "").strip()[:40],
                             "description": (body.get("description") or "").strip()[:150]})
            elif pk == "ONT_ENT":
                item.update({"typeId": body.get("typeId") or "",
                             "name": (body.get("name") or "").strip()[:60],
                             "attrs": {str(k)[:30]: str(v)[:100]
                                       for k, v in (body.get("attrs") or {}).items()}})
            else:
                item.update({"fromId": body.get("fromId") or "",
                             "toId": body.get("toId") or "",
                             "relation": (body.get("relation") or "관련")[:40]})
            if pk != "ONT_REL" and not item.get("name"):
                return 400, {"error": "name은 필수입니다."}
            ddb.put_item(Item=_to_ddb(item))
            audit(email, f"ontology.{m.group(1)}.create", item["sk"], item.get("name", ""))
            return 200, {"id": item["sk"]}
        if method == "DELETE" and oid:
            ddb.delete_item(Key={"pk": pk, "sk": oid})
            audit(email, f"ontology.{m.group(1)}.delete", oid)
            return 200, {"ok": True}

    # ---------------- wiki ----------------
    if path == "/api/wiki" and method == "GET":
        return 200, {"pages": [
            {"slug": i["sk"], "title": i["title"], "updatedBy": i.get("updatedBy", ""),
             "updatedAt": int(i.get("updatedAt", 0)), "version": int(i.get("version", 1))}
            for i in sorted(_list("WIKI"), key=lambda x: x.get("updatedAt", 0), reverse=True)]}

    m = re.match(r"^/api/wiki/([a-z0-9][a-z0-9-]{1,60})$", path)
    if m and method == "GET":
        it = get_item("WIKI", m.group(1))
        return (200, {"page": it}) if it else (404, {"error": "not found"})

    if path == "/api/wiki" and method == "POST":
        slug = (body.get("slug") or "").strip().lower()
        title = (body.get("title") or "").strip()[:80]
        md = (body.get("markdown") or "").strip()
        if not SLUG_RE.match(slug) or not title or not md:
            return 400, {"error": "slug(영소문자/숫자/하이픈)·title·markdown은 필수입니다."}
        if len(md) > MAX_WIKI_CHARS:
            return 400, {"error": f"본문은 {MAX_WIKI_CHARS:,}자 이내여야 합니다."}
        prev = get_item("WIKI", slug)
        version = int(prev.get("version", 1)) + 1 if prev else 1
        history = (prev.get("history") or [])[:4] if prev else []
        if prev:
            history = [{"version": prev["version"], "updatedBy": prev.get("updatedBy", ""),
                        "updatedAt": prev.get("updatedAt", 0),
                        "markdown": prev.get("markdown", "")[:5000]}] + history
        ddb.put_item(Item=_to_ddb({"pk": "WIKI", "sk": slug, "title": title,
                                   "markdown": md, "updatedBy": email,
                                   "updatedAt": int(time.time()),
                                   "version": version, "history": history}))
        audit(email, "wiki.save", slug, f"{title} v{version}")
        return 200, {"slug": slug, "version": version}

    if m and method == "DELETE":
        if not p["isAdmin"]:
            return 403, {"error": "위키 삭제는 관리자만 가능합니다."}
        ddb.delete_item(Key={"pk": "WIKI", "sk": m.group(1)})
        audit(email, "wiki.delete", m.group(1))
        return 200, {"ok": True}

    # ---------------- workflows ----------------
    if path == "/api/workflows" and method == "GET":
        return 200, {"workflows": sorted(_list("WF"),
                                         key=lambda x: x.get("createdAt", 0), reverse=True),
                     "runs": sorted(_list("WFRUN"),
                                    key=lambda x: x.get("createdAt", 0), reverse=True)[:20]}

    if path == "/api/workflows" and method == "POST":
        name = (body.get("name") or "").strip()[:40]
        wtype = body.get("type") if body.get("type") in ("chain", "loop") else "chain"
        steps = body.get("steps") or []
        steps = [{"agentId": s.get("agentId", ""),
                  "instruction": (s.get("instruction") or "").strip()[:500]}
                 for s in steps if ID_RE.match(str(s.get("agentId", "")))][:WF_MAX_STEPS]
        if not name or not steps:
            return 400, {"error": "name과 최소 1개 step이 필요합니다."}
        if wtype == "loop":
            steps = steps[:1]
        wf_id = uuid.uuid4().hex[:8]
        ddb.put_item(Item=_to_ddb({"pk": "WF", "sk": wf_id, "name": name, "type": wtype,
                                   "steps": steps,
                                   "maxIters": min(int(body.get("maxIters", 3) or 3), WF_MAX_LOOPS),
                                   "ownerEmail": email, "team": team,
                                   "createdAt": int(time.time())}))
        audit(email, "workflow.create", wf_id, f"{name} ({wtype}, {len(steps)} steps)")
        return 200, {"id": wf_id}

    m = re.match(r"^/api/workflows/([a-f0-9]{8})$", path)
    if m and method == "DELETE":
        item = get_item("WF", m.group(1))
        if item and not can_manage(p, item):
            return 403, {"error": "삭제 권한이 없습니다."}
        ddb.delete_item(Key={"pk": "WF", "sk": m.group(1)})
        audit(email, "workflow.delete", m.group(1))
        return 200, {"ok": True}

    m = re.match(r"^/api/workflows/([a-f0-9]{8})/run$", path)
    if m and method == "POST":
        wf = get_item("WF", m.group(1))
        if not wf:
            return 404, {"error": "워크플로우를 찾을 수 없습니다."}
        text = (body.get("input") or "").strip()[:MAX_MSG_CHARS]
        if not text:
            return 400, {"error": "input이 필요합니다."}
        for st in wf.get("steps", []):
            ag = get_item("AGENT", st["agentId"])
            if not ag or ag.get("status") != "APPROVED":
                return 400, {"error": "승인된 에이전트로만 워크플로우를 실행할 수 있습니다."}
        run_id = uuid.uuid4().hex[:8]
        ddb.put_item(Item=_to_ddb({"pk": "WFRUN", "sk": run_id, "wfId": wf["sk"],
                                   "wfName": wf["name"], "input": text, "steps": [],
                                   "totalTokens": 0, "status": "RUNNING",
                                   "startedBy": email, "createdAt": int(time.time()),
                                   "updatedAt": int(time.time())}))
        wf_run_async(run_id)
        audit(email, "workflow.run", wf["sk"], f"run {run_id}")
        return 200, {"runId": run_id}

    m = re.match(r"^/api/workflows/runs/([a-f0-9]{8})$", path)
    if m and method == "GET":
        it = get_item("WFRUN", m.group(1))
        return (200, {"run": it}) if it else (404, {"error": "not found"})

    # ---------------- graph ----------------
    if path == "/api/graph" and method == "GET":
        agents = list_agents()
        skills = _list("SKILL")
        dss = _list("DS")
        types = _list("ONT_TYPE")
        wfs = _list("WF")
        nodes, edges = [], []
        teams = sorted(({a["team"] for a in agents if a.get("team")} |
                        {s.get("team", "") for s in skills if s.get("team")}) - {""})
        for t in teams:
            nodes.append({"id": f"team:{t}", "kind": "team", "label": t})
        for a in agents:
            nodes.append({"id": f"agent:{a['id']}", "kind": "agent", "label": a["name"],
                          "status": a["status"], "tokens": a["usage"]["totalTokens"]})
            if a.get("team"):
                edges.append({"from": f"team:{a['team']}", "to": f"agent:{a['id']}",
                              "rel": "owns"})
            for d in a.get("datasourceIds", []):
                edges.append({"from": f"agent:{a['id']}", "to": f"ds:{d}", "rel": "grounds"})
            for s in a.get("skillIds", []):
                edges.append({"from": f"agent:{a['id']}", "to": f"skill:{s}", "rel": "uses"})
            if a.get("useOntology"):
                edges.append({"from": f"agent:{a['id']}", "to": "ontology", "rel": "queries"})
        for s in skills:
            nodes.append({"id": f"skill:{s['sk']}", "kind": "skill", "label": s["name"]})
        for d in dss:
            nodes.append({"id": f"ds:{d['sk']}", "kind": "datasource", "label": d["name"]})
        if types:
            nodes.append({"id": "ontology", "kind": "ontology",
                          "label": f"온톨로지 ({len(types)} 타입)"})
        for w in wfs:
            nodes.append({"id": f"wf:{w['sk']}", "kind": "workflow",
                          "label": w["name"], "wtype": w.get("type")})
            for st in w.get("steps", []):
                edges.append({"from": f"wf:{w['sk']}", "to": f"agent:{st['agentId']}",
                              "rel": "invokes"})
        if GATEWAY_URL:
            nodes.append({"id": "gateway", "kind": "gateway", "label": "플랫폼 MCP Gateway"})
        node_ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["from"] in node_ids and e["to"] in node_ids]
        linked = {e["from"] for e in edges} | {e["to"] for e in edges}
        return 200, {"nodes": nodes, "edges": edges,
                     "coverage": {"agents": len(agents), "teams": len(teams),
                                  "orphans": len([n for n in nodes
                                                  if n["kind"] == "agent"
                                                  and n["id"] not in linked])}}

    # ---------------- admin ----------------
    if path.startswith("/api/admin/") and not p["isAdmin"]:
        return 403, {"error": "플랫폼 관리자(platform-admins)만 접근할 수 있습니다."}

    if path == "/api/admin/overview" and method == "GET":
        agents = list_agents()
        return 200, {
            "agents": agents,
            "pending": [a for a in agents if a["status"] == "PENDING"],
            "totals": {"agents": len(agents),
                       "invocations": sum(a["usage"]["invocations"] for a in agents),
                       "totalTokens": sum(a["usage"]["totalTokens"] for a in agents),
                       "estCostUsd": round(sum(a["estCostUsd"] for a in agents), 4)},
            "byTeam": _by_team(agents),
            "limits": {"maxAgents": MAX_AGENTS, "defaultBudgetTokens": DEFAULT_BUDGET_TOKENS,
                       "reservedConcurrency": 5},
            "registry": {"id": AGENT_REGISTRY_ID, "region": AGENT_REGISTRY_REGION,
                         "note": "승인 상태의 정본 — 승인/거부가 CloudTrail에 감사 기록됨"},
            "priceNote": f"추정 비용은 Claude Sonnet 4 온디맨드 표준 단가(${PRICE_IN}/M 입력, ${PRICE_OUT}/M 출력) 기준 근사치"}

    if path == "/api/admin/audit" and method == "GET":
        items = sorted(_list("AUDIT"), key=lambda x: x["sk"], reverse=True)[:50]
        return 200, {"events": items}

    m = re.match(r"^/api/admin/agents/([a-f0-9]{8})/(approve|reject)$", path)
    if m and method == "POST":
        agent_id, action = m.group(1), m.group(2)
        item = get_item("AGENT", agent_id)
        if not item:
            return 404, {"error": "에이전트를 찾을 수 없습니다."}
        new_status = "APPROVED" if action == "approve" else "REJECTED"
        reason = (body.get("reason") or "").strip()[:200] or f"{action} by {email}"
        if item.get("registryRecordId"):
            registry_set_status(item["registryRecordId"], new_status,
                                f"{reason} (by {email})")
        ddb.update_item(Key={"pk": "AGENT", "sk": agent_id},
                        UpdateExpression="SET #s = :s",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":s": new_status})
        audit(email, f"agent.{action}", agent_id, reason)
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
        audit(email, "agent.budget", m.group(1), str(budget))
        return 200, {"ok": True}

    return 404, {"error": "not found"}


def _by_team(agents):
    out = {}
    for a in agents:
        t = a.get("team") or "unassigned"
        o = out.setdefault(t, {"agents": 0, "tokens": 0, "estCostUsd": 0.0})
        o["agents"] += 1
        o["tokens"] += a["usage"]["totalTokens"]
        o["estCostUsd"] = round(o["estCostUsd"] + a["estCostUsd"], 4)
    return out


# ---------------------------------------------------------------- handler
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def serve_static(path):
    rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
    if rel.startswith("static/"):
        rel = rel[len("static/"):]
    full = os.path.normpath(os.path.join(STATIC_DIR, rel))
    if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
        full = os.path.join(STATIC_DIR, "index.html")   # SPA fallback
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    with open(full, "rb") as f:
        data = f.read()
    cache = "public, max-age=300" if full.endswith(".html") else "public, max-age=86400"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json",
                                              "image/svg+xml"):
        return {"statusCode": 200,
                "headers": {"Content-Type": f"{ctype}; charset=utf-8", "Cache-Control": cache},
                "body": data.decode()}
    return {"statusCode": 200, "headers": {"Content-Type": ctype, "Cache-Control": cache},
            "body": base64.b64encode(data).decode(), "isBase64Encoded": True}


def handler(event, context):
    # async workflow executor (self-invoked)
    if event.get("nexusAsync") == "wfrun":
        wf_execute(event.get("runId", ""))
        return {"ok": True}

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-origin-verify") != ORIGIN_SECRET:
        return {"statusCode": 403, "body": "forbidden"}

    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    path = http.get("path", "/")

    if not path.startswith("/api/"):
        return serve_static(path) if method == "GET" else \
            {"statusCode": 405, "body": "method not allowed"}

    p = principal_from(event)
    if not p["email"]:
        return _json(401, {"error": "인증 정보가 없습니다."})

    try:
        body = json.loads(event.get("body") or "{}") if method != "GET" else {}
    except json.JSONDecodeError:
        return _json(400, {"error": "invalid JSON body"})

    try:
        code, obj = api(method, path, body, p)
        return _json(code, obj)
    except Exception as exc:
        print(f"api error [{method} {path}] by {p['email']}: {exc}")
        return _json(502, {"error": "요청 처리에 실패했습니다. 잠시 후 다시 시도해 주세요."})


def _json(code, obj):
    return {"statusCode": code,
            "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(obj, ensure_ascii=False, default=str)}
