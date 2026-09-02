# agents/ — 시나리오 에이전트 컨테이너 (Strands Agents on AgentCore Runtime)

`agentcore/agent_specs.py` 의 4종 시나리오 에이전트를 **Strands Agents** 로 실행하는 컨테이너다.
AgentCore Runtime 이 이 이미지를 `runtimeSessionId` 별 microVM 으로 띄우고, WsFn 은 `agentcore/invoke.py` →
`agentcore/runtime.py` (`invoke_agent_runtime`, SSE) 로 호출한다. Tier 0/1 전용 경로다 — Tier 2(PII 추론)는 AgentCore 를 타지 않는다.

```
WsFn ─ invoke_agent_runtime(SSE) ─▶ AgentCore Runtime ─ app.py (BedrockAgentCoreApp :8080)
                                        ├─ Strands Agent(BedrockModel: Claude · Guardrail) ── Bedrock
                                        │     └─ BoundaryGateHook: 모델 호출 직전 식별자 규칙 스캔 (히트 → 호출 없음)
                                        └─ MCP(streamable HTTP, SigV4) ─▶ AgentCore Gateway ─▶ PlatformToolsFn (마스킹된 결과만)
```

| 파일 | 역할 |
|---|---|
| `app.py` | 엔트리포인트. payload `{agent, prompt, sessionId?, model?}` → SSE 이벤트(`text`·`tool_start`·`tool_input`·`tool_result`·`boundary`·`error`·`meta`) |
| `mcp_gateway.py` | Gateway(MCP, IAM 인바운드) 도구 로더 — 요청마다 SigV4 재서명하는 `httpx.Auth`, `allowedTools` 필터(`<target>___<tool>`·bare 둘 다 허용) |
| `boundary_gate.py` | 익명화 게이트 훅 — `api/common/pii.py` 와 같은 RULES 로 나가는 메시지 전체를 스캔, `GateRefused` 로 모델 호출 차단, 통과 시 `{chars, estTokens, piiRules:0}` 계측 |
| `prepare_context.sh` | `../agentcore/agent_specs.py` 와 `../skills/*.md` 를 `_ctx/` 로 복사 (Dockerfile 이 `_ctx/` 에서 COPY) |
| `Dockerfile` | `python:3.12-slim`(arm64) · 비루트 사용자 · `EXPOSE 8080` · `CMD ["python","app.py"]` |

환경변수: `AWS_REGION`(ap-northeast-2) · `GATEWAY_URL`(필수) · `GATEWAY_ARN` · `GEN_MODEL` · `GUARDRAIL_ID` · `GUARDRAIL_VERSION` · 선택 `LOG_LEVEL`, `MAX_TOKENS`.
모델은 `agent_specs` 의 두 모델(`global.anthropic.claude-sonnet-5`, `global.anthropic.claude-opus-5`)과 `GEN_MODEL` 만 허용한다.

## 로컬 실행 (Docker, linux/arm64)

```bash
cd platform
bash agents/prepare_context.sh
docker build --platform linux/arm64 -t bank-agents:dev agents

# 자격증명은 환경에서 (인스턴스 역할이면 export-credentials 로 임시 키를 꺼낸다). 이미지에 키를 굽지 않는다.
eval "$(aws configure export-credentials --format env)"
docker run --rm -d --name bank-agents -p 8080:8080 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e AWS_REGION=ap-northeast-2 \
  -e GATEWAY_URL="https://<gateway-id>.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp" \
  -e GATEWAY_ARN="arn:aws:bedrock-agentcore:ap-northeast-2:<account>:gateway/<gateway-id>" \
  -e GEN_MODEL=global.anthropic.claude-sonnet-5 \
  -e GUARDRAIL_ID=<guardrail-id> -e GUARDRAIL_VERSION=1 \
  bank-agents:dev

curl -s localhost:8080/ping
# → {"status":"Healthy","time_of_last_update":...}

curl -sN -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"agent":"regulation_impact_agent","prompt":"REG-LN-001 규정 개정 영향은?"}'
# → data: {"type":"boundary","chars":...,"estTokens":...,"piiRules":0,"seq":1}
#   data: {"type":"tool_start","name":"analyze_regulation_impact","toolUseId":"..."}
#   data: {"type":"tool_input", ...}   data: {"type":"tool_result","chars":...,"status":"success"}
#   data: {"type":"text","t":"..."} ...
#   data: {"type":"meta","usage":{"inputTokens":..,"outputTokens":..},"modelId":"...","stopReason":"end_turn","sessionId":"...","runtime":"agentcore-runtime/strands", ...}

docker logs bank-agents   # 프롬프트·응답 원문은 기록하지 않는다 (길이·도구 이름·토큰 수만)
docker stop bank-agents
```

호출자(WsFn)가 쓰는 `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` 헤더가 로컬에는 없으므로 payload `sessionId` 로 멀티턴을 이어간다
(같은 `sessionId` 로 두 번 호출하면 이전 대화가 이어진다 — 프로세스 내 최대 20세션).

## 정직성 규칙

- Gateway 연결·서명·MCP 핸드셰이크가 실패하면 `{"type":"error","code":502,...}` 를 내고 끝낸다. 도구 결과를 흉내내지 않는다.
- 게이트가 거부하면(`GateRefused: <규칙>`) 모델 호출은 일어나지 않았다 — `boundary` 이벤트의 `piiRules>0` 과 `meta.stopReason="gate_refused"` 로 확인한다.
- `boundary.chars/estTokens` 는 실제로 Bedrock 에 보낸 메시지 직렬화 길이의 실측이고, 정확한 토큰 수는 `meta.usage` 다.

## 검증 기록 (로컬 스모크, 2026-09-02 22:34 UTC — 이미지 `bank-agents:dev`, 실제 Gateway·Bedrock 호출)

- `GET /ping` → `{"status":"Healthy",...}`.
- S1 `{"agent":"regulation_impact_agent","prompt":"REG-LN-001 규정 개정 영향은?"}` → 이벤트 순서
  `boundary(chars 421, piiRules 0)` → `tool_start analyze_regulation_impact` → `tool_input {"reg_code":"REG-LN-001"}` →
  `tool_result(chars 7753, success)` → `boundary(chars 7491, piiRules 0)` → `text` ×… (2,080자) →
  `meta{usage 6310/2012, stopReason end_turn, gatewayTools 10, toolNames 3(allowedTools 필터), elapsed 25.2s}`.
- 게이트 거부 `"고객 CUST-0001 의 한도는?"` → `boundary(piiRules 1)` → `error 422 GateRefused: CUSTOMER_TOKEN` →
  `meta{usage 0/0, stopReason gate_refused}` — 모델 호출이 실제로 일어나지 않았다(토큰 0).
- 모르는 에이전트 → `error 404` + `available[]`.
- 컨테이너 로그에 프롬프트 원문 없음(길이·도구 이름·토큰 수만).
- `temperature` 는 넘기지 않는다: `global.anthropic.claude-sonnet-5` Converse 에 `temperature` 를 주면
  `ValidationException: temperature is deprecated for this model` (같은 날 직접 확인). 구형 모델에만 `TEMPERATURE` env 로 켠다.
- 호스트 8080 이 이미 사용 중이면 `-p 18080:8080` 처럼 호스트 포트만 바꾼다 (컨테이너 포트 8080 은 AgentCore 계약).
