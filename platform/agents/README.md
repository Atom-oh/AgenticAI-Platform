# Scenario agent container

Current code audit: 2026-09-13. See the [platform overview](../README.md) and
[module contracts](../docs/CONTRACTS.md) for authority and shared interfaces.

`app.py` runs the five specs in `agentcore/agent_specs.py` using Strands on
AgentCore Runtime. `design_flow_agent` instead runs the shared `design_loop`.
The platform's `agentcore/invoke.py` dispatches by Registry payload; custom
Harness agents use `agentcore/harness.py`, not this container. Admin seeding
prefers this runtime when `AGENTS_RUNTIME_ARN` is configured and otherwise uses
Harness. The platform handler requires an `APPROVED` record before invocation.

This is a Tier 0/1 path. The S2 private EKS detector is a separate deployment;
AgentCore chat does not implement the authenticated S2 privacy pipeline.

## Runtime contract

`BedrockAgentCoreApp` serves port 8080: `GET /ping` and `POST /invocations`.
Normal input is `{agent, prompt, sessionId?, model?}`. Design-flow input adds
`design: {productSpec, smModel, checklists[], outputType?}`.

| File | Responsibility |
| --- | --- |
| `app.py` | Load specs/skills, construct the model and session, emit SSE events |
| `mcp_gateway.py` | Load Gateway tools over SigV4-signed MCP HTTP; filter `allowedTools` by bare or `<target>___<tool>` name |
| `boundary_gate.py` | Scan outgoing message content before model calls; refuse identifier-rule hits and measure boundary size |
| `design_deps.py` | Model and boundary adapter for the shared process-generation loop |
| `prepare_context.sh` | Copy canonical specs, `engine/model_catalog.py`, all six skills, and `design_loop/` into `_ctx/` |
| `Dockerfile` | Python 3.12, ARM64 build, UID 10001, port 8080, `python app.py`; dependencies pinned in `requirements.txt` |

SSE event types for chat are `text`, `tool_start`, `tool_input`, `tool_result`,
`boundary`, `error`, and terminal `meta`. Text uses key `t`; tool results expose
`chars` and `status`, not raw result bodies. Design execution also emits `stage`
and `design_done`; `agentcore/runtime.py` normalizes these for the caller.

`boundary.chars` measures serialized message content; `estTokens` is an estimate,
not Bedrock billing usage or the complete wire-request size. `meta.usage` reports
model token usage. `GateRefused` blocks the affected model call and removes the
session history; earlier successful calls in the same run can already have usage.
An initial gate refusal can report zero usage and `stopReason="gate_refused"`.

Environment: `AWS_REGION` (default `ap-northeast-2`), `GATEWAY_URL`, `GATEWAY_ARN`,
`GUARDRAIL_ID`, `GUARDRAIL_VERSION`, and optional `LOG_LEVEL`, `MAX_TOKENS`,
`MAX_PROMPT_CHARS`, `SKILLS_DIR`, `TEMPERATURE`. Model selection uses the exact
allowlist copied from `engine/model_catalog.py`; it is not limited to the two
original Claude specs. A default spec supplies its model; arbitrary `GEN_MODEL`
values do not extend the allowlist. `TEMPERATURE` is omitted unless explicitly set.

The process retains at most 20 session histories in memory. Payload `sessionId`
takes precedence over the runtime session ID; callers must keep session identity
consistent. This is not durable managed memory. The image does not install the
ADOT auto-instrumentation package.

## Local container commands

Run from `platform/` with Docker, temporary AWS credentials, and configured
Gateway/Guardrail values. These commands build and call real services:

```bash
bash agents/prepare_context.sh
docker build --platform linux/arm64 -t bank-agents:dev agents

docker run --rm -d --name bank-agents -p 127.0.0.1:8080:8080 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e AWS_REGION=ap-northeast-2 \
  -e GATEWAY_URL -e GATEWAY_ARN -e GUARDRAIL_ID -e GUARDRAIL_VERSION \
  bank-agents:dev

curl -s localhost:8080/ping
curl -sN -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"regulation_impact_agent","prompt":"REG-LN-001 규정 개정 영향은?"}'
docker logs bank-agents
docker stop bank-agents
```

For a role-backed shell, `aws configure export-credentials --format env` can
provide temporary credentials; do not bake them into the image or print them in
shared logs. If port 8080 is occupied, change only the host port, for example
`127.0.0.1:18080:8080`. Reuse payload `sessionId` for local multi-turn testing.

Setup/handshake failures emit an error such as `code=502`; unavailable tools are
not fabricated. Missing skills are reported. Log safety is a requirement:
normal events log sizes/names/counts, while exception paths use bounded `_err`
strings and must not be treated as a general raw-error sanitization guarantee.

## Historical smoke evidence

Inherited report, 2026-09-02 22:34 UTC, local `bank-agents:dev` image with real
Gateway/Bedrock calls; not rerun for the current commit:

- `/ping` returned `{"status":"Healthy",...}`.
- `regulation_impact_agent` called `analyze_regulation_impact`; reported usage was
  6,310 input / 2,012 output tokens, with 10 discovered and three allowed tools.
- Initial prompt `고객 CUST-0001 의 한도는?` produced `CUSTOMER_TOKEN`, error `422`,
  `stopReason="gate_refused"`, and zero usage.
- An unknown agent returned `404` with available names.
- The then-selected model rejected `temperature`; this motivated omitting it by default.

Current readiness, model availability and log contents require new evidence.
