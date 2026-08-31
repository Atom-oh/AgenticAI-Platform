---
title: Runtime 심화
description: AgentCore Runtime이 microVM 하드웨어 격리로 세션을 나누는 이유와, 임의 프레임워크를 Runtime 계약에 맞춰 감싸는 방법을 다룬다.
outline: [2, 3]
---

# Runtime 심화

::: tip 이 장에서 얻는 것
- AgentCore **Runtime**과 **Harness**의 경계 — 이 플랫폼이 왜 Harness(config 기반 managed loop)를 기본으로 쓰면서도 Runtime을 알아야 하는지.
- 세션마다 **microVM**을 새로 띄우는 격리 모델의 정체와, `maxLifetime`(8시간) 한계에 부딪히는 시점.
- LangGraph/CrewAI/Strands/OpenAI Agents SDK 같은 임의 프레임워크를 Runtime의 HTTP 계약(`/ping`, `/invocations`)에 감싸는 어댑터 작성법.
- `/ping`을 잘못 구현해서 장시간 작업 중에 스케일다운당하는 실무 함정.
:::

## 왜 문제가 되는가

에이전트를 "그냥 컨테이너로 띄우면" 되는 것 아니냐는 질문은 세션이 하나뿐일 때는 맞다. 문제는 동시에 여러 사용자가 같은 에이전트를 호출하는 순간부터다. 한 세션의 추론 과정에서 남긴 프로세스 메모리, 파일시스템 잔여물, 환경 변수가 다음 세션으로 새어 나가면 — LLM이 비결정적으로 행동한다는 사실과 결합해 디버깅이 거의 불가능한 데이터 오염이 발생한다. 컨테이너 재사용이나 스레드 단위 격리로는 "완전히 분리됐다"는 것을 증명하기 어렵다.

AgentCore Runtime은 이 문제를 애플리케이션 계층이 아니라 **하드웨어 계층**에서 해결한다. 각 `runtimeSessionId`는 독립된 microVM을 받고, 세션이 끝나면 microVM 자체가 종료되며 메모리가 sanitize된다([Host agent or tools with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)). 이 장은 그 격리 모델이 실제로 어떻게 동작하는지, 그리고 어떤 상황에서 그 모델의 상한(8시간)에 부딪혀 다른 컴퓨트 타입(Instances)으로 넘어가야 하는지를 다룬다. 포트·엔드포인트·페이로드 형식 같은 계약의 세세한 필드는 짝을 이루는 [Runtime 배포 계약](./runtime-deploy-contract.md)에서 다룬다 — 여기서는 "왜 이 아키텍처인가"와 "내 프레임워크를 어떻게 여기에 맞출 것인가"에 집중한다.

## 핵심 개념

### Runtime vs Harness — 코드를 쓰느냐, config만 쓰느냐

AgentCore는 에이전트를 호스팅하는 두 가지 서로 다른 서비스를 제공한다.

- **Harness**: 모델·시스템 프롬프트·툴을 API 호출 하나로 지정하면 오케스트레이션(툴 실행, 메모리 관리, 응답 생성)을 AWS가 대신 처리하는 managed agent loop다. 각 세션도 격리된 microVM에서 파일시스템·셸 접근을 갖고 실행된다([What is Amazon Bedrock AgentCore? — Harness](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)). 이 책의 데모(`demo/builder-harness/`)가 실제로 배포한 것이 이 Harness다 — 오케스트레이션 코드를 직접 쓰지 않겠다는 플랫폼 결정을 그대로 반영한다.
- **Runtime**: 오케스트레이션 코드를 직접 작성하고, AWS는 그 코드를 실행할 격리된 서버리스 환경만 제공한다. 프레임워크·모델·프로토콜 선택권을 온전히 개발자가 갖는 대신, `/ping`·`/invocations` 계약을 스스로 구현해야 한다([Host agent or tools with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)).

Harness로 충분한 워크로드(범용 대화형 에이전트, 표준 툴 사용)라면 Runtime의 계약 관리 부담을 질 이유가 없다. Runtime을 선택하는 이유는 대개 다음 중 하나다: (1) LangGraph/CrewAI 같은 특정 프레임워크의 그래프·오케스트레이션 로직에 이미 투자했다, (2) A2A/MCP/AG-UI 같은 프로토콜을 세밀하게 제어해야 한다, (3) Harness가 지원하지 않는 커스텀 미들웨어(정책 검사, 커스텀 스트리밍 포맷 등)가 필요하다.

### microVM 세션 격리 — 하드웨어 경계로 결정론을 되찾는다

Runtime에서 사용자 세션 하나는 CPU·메모리·파일시스템이 독립된 전용 microVM에서 실행된다. 세션이 끝나면 microVM 전체가 종료되고 메모리가 sanitize된다 — 이것이 "세션 간 완전한 분리"를 코드가 아니라 하드웨어 경계로 보장하는 방식이다([Host agent or tools — Session isolation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)).

동일한 `runtimeSessionId`로 재호출하면 기존 microVM을 재사용하고, 새 `runtimeSessionId`는 완전히 새로운 microVM을 받는다([Configure Amazon Bedrock AgentCore lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html)). 이 재사용 여부가 다음 절의 lifecycle 설정과 직결된다.

### 컴퓨트 타입 선택 — microVM(최대 8시간) vs Instances(최대 14일)

Runtime은 두 가지 컴퓨트 타입을 제공한다.

| 컴퓨트 타입 | 최대 세션 지속 시간 | 적합한 워크로드 |
|---|---|---|
| **microVMs** (기본) | 최대 **8시간**(28,800초) | 빠르게 시작하고 온디맨드로 스케일하는 API 기반 경량 에이전트 |
| **Instances** (capacity provider, 자체 계정 EC2) | 최대 **14일**(1,209,600초) | 장기 실행 상태 유지, GPU 필요, 같은 인스턴스에서 여러 에이전트 협업(co-location) |

두 값 모두 공식 문서에서 확인된다([Host agent or tools — Extended execution time](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html), [Instances — When to use Instances](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html)). Instances는 자체 계정의 EC2 managed instance 위에서 실행되므로 Savings Plans·Reserved Instances·ODCR 같은 기존 EC2 요금 체계를 그대로 활용할 수 있고, GPU 인스턴스 패밀리(`g4dn`, `g5`, `g6`, `g6e`, `gr6`, `g6f`, `gr6f`, `g7e`, AWS 가속기 `inf2`)를 지정하면 드라이버까지 AgentCore가 프로비저닝해준다([Instances — Use GPU instance types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html)). 같은 capacity provider를 공유하는 두 런타임을 같은 `runtimeSessionId`로 호출하면 같은 인스턴스 위에서 파일시스템을 공유하며 협업하게 된다 — microVM 모델은 런타임 1개당 에이전트 1개(1:1)인 반면 Instances는 세션 1개에 여러 에이전트(1:N)를 태울 수 있다는 근본적 차이다.

컴퓨트 타입은 런타임 생성 후 변경할 수 없다. GPU나 14일 초과 상태 유지가 필요할 걸 뒤늦게 깨달으면 새 런타임을 다시 만들어야 한다.

### Lifecycle 설정 — `idleRuntimeSessionTimeout`과 `maxLifetime`

`CreateAgentRuntime`/`UpdateAgentRuntime`의 `LifecycleConfiguration`이 세션 종료 시점을 결정한다.

| 속성 | 범위(초) | 기본값 | 의미 |
|---|---|---|---|
| `idleRuntimeSessionTimeout` | microVM: 60–28,800 / Instances: 60–1,209,600 | **900**(15분) | 이 시간 동안 유휴 상태면 세션 종료 트리거. 동일 세션 재호출 시 타이머 리셋 |
| `maxLifetime` | microVM: 60–28,800 / Instances: 60–1,209,600 | **28,800**(8시간) | microVM 생성 시점부터 시작되며 리셋되지 않는 절대 상한. 도달하면 그 세션의 microVM은 종료되지만, 같은 `runtimeSessionId`로 재호출하면 새 microVM이 새로 프로비저닝되어 세션 자체는 이어질 수 있음 |

(출처: [Configure Amazon Bedrock AgentCore lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html))

제약: `idleRuntimeSessionTimeout ≤ maxLifetime`이어야 하며, Instances 컴퓨트 타입에서는 `maxLifetime`이 해당 capacity provider의 `InstanceLifecycleConfiguration.maxLifetime`도 넘을 수 없다. 위반 시 `ValidationException`.

### 배포 방식 — Container냐 Direct Code냐

AgentCore Runtime은 두 가지 아티팩트 배포 방식을 지원한다([Get started with direct code deployment — Container vs direct code deployment comparison](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy.html)):

- **Direct code deployment**: 코드와 의존성을 `.zip`으로 패키징해 배포. 패키지 크기 상한 **250MB**, 세션 생성률 초당 25개. 현재 Python과 Node.js 런타임을 지원.
- **컨테이너 배포**: Docker 이미지 기반, 크기 상한 **2GB**, 세션 생성률 초당 1.6개. 특화된 시스템 의존성이나 기존 컨테이너 CI/CD가 있을 때 적합.

패키지가 250MB를 넘거나 Python/Node.js가 아닌 언어(예: Java, Go)를 쓴다면 컨테이너 배포가 유일한 경로다. 반대로 가벼운 Python 프로토타입이라면 direct code deployment가 반복 배포 속도(업데이트가 컨테이너보다 훨씨 빠름)에서 이긴다. 두 방식을 혼용하는 하이브리드 전략(빠른 프로토타이핑은 direct code, 프로덕션 전환은 컨테이너)도 문서가 명시적으로 권장한다.

### 프로토콜과 계약 개요

Runtime은 HTTP, MCP, A2A, AG-UI 네 프로토콜을 지원한다([Understand the AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html)).

| 프로토콜 | 포트 | Mount Path | 인증 |
|---|---|---|---|
| HTTP | 8080 | `/invocations`(HTTP), `/ws`(WebSocket) | SigV4, OAuth 2.0 |
| MCP | 8000 | `/mcp` | SigV4, OAuth 2.0 |
| A2A | 9000 | `/`(root, JSON-RPC 2.0) | SigV4, OAuth 2.0 |
| AG-UI | 8080 | `/invocations`(SSE), `/ws` | SigV4, OAuth 2.0 |

모든 프로토콜에서 컨테이너는 ARM64여야 하고 `0.0.0.0`에 바인딩해야 한다. HTTP와 AG-UI는 같은 8080 포트에 `/invocations`와 `/ws`를 동시에 노출할 수 있어 하나의 구현으로 요청/응답과 스트리밍을 함께 지원 가능하다([HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)). 페이로드는 최대 **100MB**까지 처리 가능해 이미지·오디오·비디오를 포함한 멀티모달 요청을 지원한다([Host agent or tools — Enhanced payload handling](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)).

이 계약의 필드 하나하나(에러 코드, 요청/응답 스키마, WebSocket 핸드셰이크 등)는 [Runtime 배포 계약](./runtime-deploy-contract.md)에서 다룬다.

### `/ping`과 `HealthyBusy` — 장기 작업의 스케일다운을 막는 신호

`/ping`은 `Healthy` 또는 `HealthyBusy` 두 상태 중 하나를 반환해야 한다. `Healthy`는 새 작업을 받을 준비가 됐다는 뜻이고, `HealthyBusy`는 백그라운드 비동기 작업을 처리 중이라는 뜻이다. **`HealthyBusy`인 동안에는 런타임 세션이 활성 상태로 간주되어 idle timeout이 걸리지 않는다**([HTTP protocol contract — /ping](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)).

::: warning 실무 함정
장시간 실행되는 비동기 작업(대규모 문서 처리, 멀티스텝 에이전트 루프)을 돌리는 동안 `/ping`이 계속 `Healthy`를 반환하면, `idleRuntimeSessionTimeout`(기본 900초)이 흘러 세션이 중간에 종료될 수 있다. 백그라운드 작업이 살아있는 동안은 반드시 `HealthyBusy`를 반환하도록 구현해야 한다.

반대 방향의 함정도 있다: `time_of_last_update`를 매 ping마다 현재 시각으로 갱신하면 "상태가 계속 바뀌고 있다"는 신호가 되어 idle timeout이 영원히 발동하지 않는다. 세션이 `maxLifetime`까지 계속 살아 있게 되고, 세션 쿼터를 고갈시킬 수 있다. `time_of_last_update`는 상태가 실제로 바뀔 때만 설정하거나, 필드 자체를 생략해 플랫폼이 추적하게 두는 것이 맞다(SDK를 쓰면 이 처리를 대신 해준다). (출처: [HTTP protocol contract — /ping](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html), [A2A protocol contract — /ping](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-a2a-protocol-contract.html))
:::

### 인증 — SigV4 기본, OAuth/JWT는 옵션

Runtime의 기본 인증은 별도 설정 없이 자동으로 동작하는 IAM SigV4다. JWT bearer token(OAuth 2.0) 인증을 쓰려면 `authorizerConfiguration`으로 discovery URL·allowed audiences/clients/scopes를 지정해야 한다. 한 런타임은 SigV4 또는 JWT 중 하나만 지원하며 동시에 두 방식을 쓸 수 없다([Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)).

문서에 따르면 **2025년 10월 13일**부터 워크로드 아이덴티티 권한 관리 방식이 바뀌어, 신규 에이전트는 서비스 연결 역할(`AWSServiceRoleForBedrockAgentCoreRuntimeIdentity`)이 자동으로 권한을 부여하고, 그 이전에 만든 에이전트는 여전히 실행 역할에 `GetWorkloadAccessToken*` 정책을 직접 붙여야 한다(같은 출처). A2A·MCP 프로토콜 모두 SigV4와 OAuth 2.0 인증을 지원한다는 것은 서비스 계약 비교표에 명시돼 있다([Understand the AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html)).

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: "A2A/MCP에 대해 IAM 기반 인증이 특정 GA 시점에 새로 추가됐다"는 식의 타임라인 서술은 현재 공식 문서에서 날짜가 명시된 형태로 확인되지 않았다. 확인된 것은 (1) 현재 시점 A2A/MCP가 SigV4와 OAuth 2.0을 모두 지원한다는 사실, (2) 2025-10-13에 워크로드 아이덴티티 권한 모델이 서비스 연결 역할 기반으로 바뀌었다는 사실이다. "GA 시점에 A2A/MCP용 IAM auth가 추가됐다"는 세부 주장은 릴리스 노트에서 별도로 교차확인하기 전까지 확정하지 말 것.

### AgentCore CLI — zero-infra 배포 경로

Runtime에 코드를 배포하는 가장 빠른 경로는 AgentCore CLI다. 현재 공식 문서가 안내하는 설치·명령 체계는 다음과 같다([Get started with the AgentCore CLI](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html)):

```bash
npm install -g @aws/agentcore

# 프로젝트 스캐폴딩 (Strands / LangChain·LangGraph / Google ADK / OpenAI Agents 지원)
agentcore create --name MyAgent --framework Strands --protocol HTTP \
  --model-provider Bedrock --memory none --build CodeZip   # 또는 --build Container

# 로컬에서 :8080 개발 서버로 계약을 미리 검증
agentcore dev

# CDK로 IAM 롤·Runtime·엔드포인트까지 한 번에 배포
agentcore deploy

# 배포된 에이전트 호출
agentcore invoke --runtime MyAgent "Hello"
```

`--build CodeZip`이면 direct code deployment, `--build Container`면 컨테이너 배포 경로를 탄다 — 앞서의 250MB/2GB 결정과 그대로 연결된다. CLI는 ARM64 호환성을 두 빌드 타입 모두에서 자동으로 처리한다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: AWS 샘플/블로그 생태계에서는 이와 별도로 Python 패키지 `bedrock_agentcore_starter_toolkit`(마찬가지로 `agentcore configure`/`agentcore launch` 계열 명령을 제공)이 언급되는 경우가 있다. 이 세션의 실시간 조회로는 PyPI 페이지 확인이 실패해 정확한 명령 체계를 검증하지 못했다 — 실제로 이 패키지를 쓸 계획이라면 배포 전에 `pip show bedrock-agentcore-starter-toolkit` 및 공식 GitHub(`aws/agentcore-cli`) 최신 문서로 명령어를 재확인하라.

## 프레임워크 어댑터 작성법

임의 프레임워크(LangGraph, CrewAI, Strands, OpenAI Agents SDK)는 Runtime의 계약을 전혀 모른다. 따라서 얇은 어댑터가 필요하다 — 프레임워크의 실행 함수를 감싸서 `/ping`과 `/invocations`만 노출하면 된다. 핵심은 세 가지다: (1) 오래 걸리는 실행 동안 `/ping`이 `HealthyBusy`를 반환하게 만드는 것, (2) 그래프/에이전트의 최종 출력만 응답 스키마로 매핑하는 것, (3) SIGTERM을 받아 정리하는 것.

```python
"""AgentCore Runtime HTTP 어댑터 — 임의 프레임워크를 /ping, /invocations 계약에 감싼다.
포트 8080, host 0.0.0.0 (컨테이너 계약 요구사항).
"""
import asyncio
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 프레임워크별 실행 함수는 여기서 한 곳에만 바꿔 끼우면 된다.
# 예: LangGraph → graph.ainvoke(...) / CrewAI → crew.kickoff_async(...)
# Strands → agent.invoke_async(...) / OpenAI Agents SDK → Runner.run(...)
from my_framework_adapter import run_agent_step, run_agent_stream

# 동시에 처리 중인 요청 수 — /ping이 HealthyBusy를 결정하는 근거
_in_flight = 0
_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    def _graceful_shutdown(sig, frame):
        # 진행 중인 요청이 응답을 마칠 시간을 주고 종료 — AgentCore가 SIGTERM을 보낸다.
        sys.exit(0)

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/ping")
async def ping():
    async with _lock:
        busy = _in_flight > 0
    # HealthyBusy: 백그라운드 작업이 살아있는 동안 idle timeout을 막는다.
    # time_of_last_update는 일부러 생략한다 — 매 호출마다 갱신하면
    # "계속 상태가 바뀐다"는 신호가 되어 idle timeout이 영영 발동하지 않는다.
    status = "HealthyBusy" if busy else "Healthy"
    return JSONResponse({"status": status})


@app.post("/invocations")
async def invocations(request: Request):
    global _in_flight
    body = await request.json()
    prompt = body.get("prompt", "")
    accept = request.headers.get("accept", "")

    async with _lock:
        _in_flight += 1
    try:
        if "text/event-stream" in accept:
            return StreamingResponse(
                _sse_wrapper(prompt), media_type="text/event-stream"
            )
        result = await run_agent_step(prompt)
        return JSONResponse({"response": result, "status": "success"})
    finally:
        async with _lock:
            _in_flight -= 1


async def _sse_wrapper(prompt: str):
    # run_agent_stream은 프레임워크의 네이티브 스트리밍(예: LangGraph astream_events,
    # Strands stream_async)을 감싸 텍스트 조각을 순서대로 내보내는 제너레이터라고 가정.
    async for chunk in run_agent_stream(prompt):
        yield f"data: {chunk}\n\n"
```

이 패턴을 프레임워크별로 어떻게 채우는지는 다음과 같다.

- **LangGraph**: `run_agent_step`은 `graph.ainvoke({"messages": [...]})` 호출 뒤 마지막 메시지를 추출한다. 스트리밍은 `graph.astream_events(...)`를 SSE로 그대로 릴레이한다.
- **CrewAI**: `crew.kickoff_async(inputs={"prompt": prompt})`의 반환값(`CrewOutput`)에서 `.raw`를 추출해 `response` 필드에 담는다. CrewAI는 네이티브 비동기 토큰 스트리밍이 약하므로, 중간 태스크 완료 이벤트를 자체적으로 SSE `data:` 프레임으로 변환해야 하는 경우가 많다.
- **Strands Agents**: `agent.invoke_async(prompt)` 또는 `agent.stream_async(prompt)`를 그대로 쓸 수 있고, Strands SDK 자체가 AgentCore Runtime 통합을 염두에 두고 만들어져 있어 어댑터가 가장 얇다.
- **OpenAI Agents SDK**: `Runner.run(agent, prompt)`(비스트리밍) 또는 `Runner.run_streamed(agent, prompt)`(스트리밍, `result.stream_events()`)를 감싼다. OpenAI 모델을 Bedrock 밖에서 직접 호출하는 구성이므로, API 키를 환경 변수가 아니라 Secrets Manager에서 런타임에 조회하도록 만드는 것을 잊지 않는다.

이 어댑터를 ARM64 컨테이너로 감싸는 Dockerfile/빌드 절차, SIGTERM 처리의 세부, ECR 푸시 흐름은 [Runtime 배포 계약](./runtime-deploy-contract.md)에서 다룬다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 표준 대화형 에이전트, 표준 툴 사용으로 충분 | Harness | 오케스트레이션·메모리 관리를 AWS가 대신 처리 | 커스텀 프로토콜/미들웨어 제어권을 포기 |
| LangGraph/CrewAI 등 특정 프레임워크의 그래프 로직에 이미 투자 | Runtime + 얇은 어댑터 | 프레임워크·모델 선택권 완전 보유 | `/ping`·`/invocations` 계약을 스스로 지켜야 함 |
| 요청-응답이 수 초~수 분 내 끝나는 API형 에이전트 | Runtime, microVM 컴퓨트 타입 | 서버리스, 온디맨드 스케일, 8시간이면 충분 | 8시간(28,800초) `maxLifetime` 상한 |
| GPU 추론, 며칠씩 이어지는 상태 유지, 여러 에이전트 co-location | Runtime, Instances(capacity provider) | 최대 14일, GPU 드라이버 자동 프로비저닝, 파일시스템 공유 | 자체 계정 EC2 관리 책임 일부, microVM보다 구성 복잡 |
| Python/Node.js, 코드 250MB 이하, 빠른 반복 배포가 중요 | Direct code deployment(CodeZip) | 배포 갱신 속도, 초당 세션 생성률(25/s)이 컨테이너(1.6/s)보다 높음 | 언어·시스템 의존성 제약, 250MB 상한 |
| 250MB 초과, 비Python/Node.js, 기존 컨테이너 CI/CD 보유 | 컨테이너 배포 | 최대 2GB, 임의 의존성·베이스 이미지 자유 | 배포 갱신이 느리고 세션 생성률이 낮음 |
| 장시간 비동기 백그라운드 작업(문서 처리, 멀티스텝 루프) | `/ping`이 작업 중 `HealthyBusy` 반환 | idle timeout(기본 900초) 도달 전 스케일다운 방지 | 상태 추적 로직을 어댑터에 직접 구현해야 함 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 장시간 작업 도중 세션이 예고 없이 끊긴다 | `/ping`이 백그라운드 작업 중에도 `Healthy`를 반환해 idle timeout(기본 900초)이 발동 | CloudWatch에서 세션 종료 시점과 idle timeout 값을 대조 | 작업이 살아있는 동안 `/ping`이 `HealthyBusy`를 반환하도록 수정 |
| 8시간짜리 배치가 항상 중간에서 끊긴다 | microVM 컴퓨트 타입의 `maxLifetime` 상한(28,800초)에 걸림 | `GetAgentRuntime`으로 컴퓨트 타입과 `lifecycleConfiguration.maxLifetime` 확인 | Instances(capacity provider)로 전환해 최대 14일까지 확보, 또는 작업을 세션 재개 가능한 단위로 분할 |
| idle timeout이 전혀 발동하지 않고 세션이 `maxLifetime`까지 계속 살아있음, 세션 쿼터 고갈 | `/ping` 응답의 `time_of_last_update`를 매 호출마다 현재 시각으로 덮어써 "지속적인 상태 변화"로 오인됨 | `/ping` 구현에서 `time_of_last_update` 설정 로직 검토 | 상태가 실제로 바뀔 때만 설정하거나 필드를 생략(SDK 사용 시 자동 처리) |
| ARM64 배포인데 컨테이너가 시작하지 않는다 | x86/amd64로 빌드된 이미지를 그대로 푸시 | `docker inspect <image>` 로 Architecture 필드 확인 | `docker buildx build --platform linux/arm64`로 재빌드 |
| 컨테이너 배포로 전환했는데 갑자기 배포 갱신이 느려짐 | Direct code deployment(CodeZip)에서 컨테이너로 전환하며 250MB 제약을 벗어났지만 세션 생성률(1.6/s)·빌드 시간 트레이드오프를 인지하지 못함 | 배포 파이프라인의 이미지 빌드/푸시 시간과 세션 생성률 모니터링 | 프로토타입은 CodeZip, 프로덕션 전환 시점에만 컨테이너로 이동하는 하이브리드 전략 채택 |
| 동일 요청인데 매번 콜드스타트처럼 느리다 | 매 요청마다 새 `runtimeSessionId`를 생성해 매번 새 microVM을 프로비저닝 | 클라이언트 코드에서 `runtimeSessionId` 생성 로직 확인 | 동일 대화/작업 단위로 `runtimeSessionId`를 재사용해 기존 microVM을 재활용 |
| SigV4 런타임에 JWT 토큰을 보냈는데 이상한 403이 뜬다 | 하나의 런타임은 SigV4 또는 JWT 중 하나만 지원 — 혼용 불가 | 런타임의 `authorizerConfiguration` 확인 | 인증 방식이 다른 클라이언트를 위해 별도 런타임 버전을 생성 |

## 안티패턴

- ❌ 모든 세션이 하나의 오래 실행되는 프로세스/컨테이너를 공유하게 만들고 세션 ID로 로직만 분기한다 → ✅ 세션마다 별도 `runtimeSessionId`로 호출해 Runtime이 부여하는 microVM 격리를 그대로 활용한다.
- ❌ `/ping`을 항상 `{"status": "Healthy"}`만 반환하도록 하드코딩한다 → ✅ 진행 중인 비동기 작업 수를 추적해 `HealthyBusy`/`Healthy`를 정확히 구분한다.
- ❌ 8시간 넘는 배치 작업을 microVM 컴퓨트 타입에 그대로 태우고 재시도로 버틴다 → ✅ Instances(capacity provider)로 전환하거나 작업을 재개 가능한 체크포인트 단위로 쪼갠다.
- ❌ 프레임워크의 네이티브 실행 함수를 그대로 엔드포인트에 노출해 SIGTERM·에러 스키마를 처리하지 않는다 → ✅ 얇은 어댑터 계층에서 계약(에러 코드, 헬스체크, graceful shutdown)을 명시적으로 구현한다.
- ❌ 컨테이너와 direct code deployment 중 하나를 "항상 옳은 기본값"으로 고정한다 → ✅ 패키지 크기·언어·배포 반복 속도 요구에 따라 매 프로젝트마다 재평가한다.

## 계측 (SLI)

- **세션 provisioning 지연**: 새 `runtimeSessionId`의 첫 호출부터 첫 응답까지의 레이턴시(콜드스타트) — microVM vs Instances(첫 인스턴스 프로비저닝 포함) 비교.
- **idle timeout으로 인한 비자발적 세션 종료율**: `HealthyBusy`를 반환해야 했는데 반환하지 못해 끊긴 세션의 비율. CloudWatch 로그에서 세션 종료 원인과 마지막 `/ping` 상태를 상관분석.
- **`maxLifetime` 도달로 인한 세션 재프로비저닝 횟수**: 8시간(microVM) 또는 14일(Instances) 상한에 부딪혀 새 인스턴스가 생성된 빈도 — 워크로드가 애초에 잘못된 컴퓨트 타입에 배치됐는지의 신호.
- **RuntimeClientError(424) 비율**: 컨테이너가 4xx/5xx를 반환한 비율 — 어댑터 계층의 버그 신호.
- **direct code deployment의 배포 리드타임 vs 컨테이너 배포**: 코드 변경부터 신규 세션에 반영되기까지의 시간 — 250MB 근처를 오갈 때 이 지표로 전환 시점을 판단.

## 체크리스트

- [ ] `/ping`이 백그라운드 작업 유무에 따라 `Healthy`/`HealthyBusy`를 정확히 반환하는가.
- [ ] `/ping`의 `time_of_last_update`를 상태 변화 시점에만 설정하거나 생략했는가(매 호출마다 갱신하지 않는가).
- [ ] 워크로드의 예상 최대 실행 시간이 컴퓨트 타입의 `maxLifetime` 상한(microVM 8시간 / Instances 14일)을 넘지 않는가.
- [ ] 동일 대화/작업 단위에는 동일 `runtimeSessionId`를 재사용해 불필요한 콜드스타트를 피하는가.
- [ ] 컨테이너는 ARM64로 빌드했고 `0.0.0.0:8080`(또는 프로토콜별 포트)에 바인딩하는가.
- [ ] SIGTERM을 받아 진행 중인 요청을 정리하고 graceful shutdown하는가.
- [ ] 배포 방식(direct code vs 컨테이너)을 패키지 크기(250MB 기준)와 언어(Python/Node.js 여부)에 따라 의도적으로 선택했는가.
- [ ] 인증 방식(SigV4 vs JWT)을 클라이언트별로 하나만 요구하도록 런타임을 구성했는가.
- [ ] 프레임워크 어댑터가 프레임워크 고유 예외를 Runtime의 HTTP 에러 코드(400/401/403/404/409/424/429/500)로 매핑하는가.

## 참고

- [Host agent or tools with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
- [Understand the AgentCore Runtime service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html)
- [HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
- [A2A protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-a2a-protocol-contract.html)
- [Configure Amazon Bedrock AgentCore lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html)
- [Instances and capacity providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html)
- [Get started with Amazon Bedrock AgentCore Runtime direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy.html)
- [Get started with the AgentCore CLI](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html)
- [Authenticate and authorize with Inbound Auth and Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html)
- [What is Amazon Bedrock AgentCore?](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
- 짝 챕터: [Runtime 배포 계약](./runtime-deploy-contract.md)
- 이 책의 Harness 실증 사례: `demo/builder-harness/README.md`
