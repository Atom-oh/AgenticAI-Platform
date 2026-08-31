---
title: 프레임워크 지형
description: 에이전트 오케스트레이션 프레임워크(LangGraph, CrewAI, Strands, OpenAI Agents SDK)를 특성 기준으로 비교하고, 플랫폼이 프레임워크 락인을 피하는 얇은 어댑터 전략을 정리한다.
outline: [2, 3]
---

# 프레임워크 지형

::: tip 이 장에서 얻는 것
- 주요 오케스트레이션 프레임워크(LangGraph, CrewAI, Strands Agents, OpenAI Agents SDK, AutoGen/Agent Framework)의 설계 축 — 그래프 기반 vs 역할 기반 vs model-driven loop, 상태 관리 방식, MCP 지원 — 을 공식 문서 기준으로 비교한 표
- Anthropic의 "프레임워크는 프롬프트/응답을 추상화 뒤에 숨긴다"는 경고를 플랫폼 관점에서 어떻게 반영할 것인가
- **프레임워크 중립 하니스 논지**: AgentCore Runtime의 배포 계약이 프레임워크에 무관하다는 사실이 왜 "플랫폼은 프레임워크를 표준화하지 않고 계약을 표준화한다"는 결론으로 이어지는지
- 프레임워크 없이 직접 구현 / 그래프 프레임워크 / managed Harness 중 무엇을 언제 고를지에 대한 결정 표
- 프레임워크 선택·교체 국면에서 봐야 할 실패 모드와 SLI
:::

## 왜 문제가 되는가

에이전트 플랫폼을 설계할 때 가장 먼저 받는 질문이 "우리 표준 프레임워크는 뭐로 하나요?"다. 그리고 이 질문은 대개 잘못된 질문이다. 오케스트레이션 프레임워크는 지금 이 지형에서 가장 빠르게 변하는 계층이다 — 이 책을 쓰는 동안에도 Microsoft는 AutoGen과 Semantic Kernel을 [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)로 통합했고, AWS는 Strands Agents 위에 config 기반 managed loop인 [AgentCore Harness](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)를 얹었다. 플랫폼이 특정 프레임워크의 추상화(LangGraph의 `StateGraph`, CrewAI의 `Crew`)를 API 표면·CI 파이프라인·관측 스키마에 새겨 넣으면, 프레임워크 교체 비용이 곧 플랫폼 재작성 비용이 된다.

두 번째 문제는 디버깅 가시성이다. Anthropic은 [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)에서 이렇게 경고한다:

> "These frameworks make it easy to get started... However, they often create extra layers of abstraction that can obscure the underlying prompts and responses, making them harder to debug. ... We suggest that developers start by using LLM APIs directly."

프레임워크가 프롬프트 조립·툴 결과 주입·재시도를 대신해 줄수록, 프로덕션 장애 시점에 "모델에 실제로 어떤 토큰이 들어갔는가"를 재구성하기 어려워진다. 플랫폼 엔지니어에게 프레임워크 선택은 개발 생산성 문제이기 이전에 **관측 가능성과 교체 가능성의 문제**다.

세 번째 문제는 이 결정이 사실 회피 가능하다는 점이다. [Runtime 심화](/10-agentcore/runtime-deep-dive)에서 본 것처럼 AgentCore Runtime의 계약은 `/ping`과 `/invocations`를 노출하는 ARM64 컨테이너일 뿐이며([Host agent or tools with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)), 그 안에서 무슨 프레임워크가 돌든 배포 계약([Runtime 배포 계약](/10-agentcore/runtime-deploy-contract))은 동일하다. 배포 계약이 프레임워크 중립이라면, 플랫폼이 표준화할 것은 프레임워크가 아니라 계약이다.

## 핵심 개념

### 설계 축 1 — 오케스트레이션 모델: 그래프 vs 역할 vs model-driven loop

프레임워크들은 "누가 제어 흐름을 결정하는가"에서 갈린다.

- **그래프 기반 (LangGraph)**: 개발자가 노드(단계)와 엣지(전이)를 명시적으로 선언하고, 상태가 그래프를 따라 흐른다. LangGraph는 에이전트를 상태 그래프로 모델링하며, 조건부 엣지와 순환(cycle)을 명시적으로 표현한다([LangGraph docs](https://langchain-ai.github.io/langgraph/)). 제어 흐름의 결정권이 대부분 개발자에게 있다 — 모델은 노드 안에서만 자유롭다.
- **역할 기반 (CrewAI)**: 개발자가 role/goal/backstory를 가진 에이전트들과 task를 선언하면, 프레임워크가 협업 프로세스(sequential, hierarchical)를 실행한다. CrewAI는 자율 협업 단위인 **Crews**와 이벤트 기반 결정적 제어 흐름인 **Flows** 두 추상화를 제공하고, LangChain에 의존하지 않는 독립 프레임워크임을 명시한다([CrewAI docs](https://docs.crewai.com/), [GitHub](https://github.com/crewAIInc/crewAI)).
- **Model-driven loop (Strands Agents, OpenAI Agents SDK)**: 개발자는 모델·시스템 프롬프트·툴만 제공하고, "다음에 무엇을 할지"는 모델이 루프 안에서 스스로 결정한다. Strands는 이를 model-driven approach라고 명시적으로 이름 붙였고, agentic loop 자체가 SDK의 핵심이다([Strands Agents docs](https://strandsagents.com/), [GitHub](https://github.com/strands-agents/sdk-python)). OpenAI Agents SDK는 instructions + tools + **handoffs**(에이전트 간 명시적 제어 이양)라는 최소 프리미티브로 같은 루프를 구성한다([OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/)).

이 축은 [에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow)의 구분과 정확히 겹친다. 그래프 기반 프레임워크는 사실상 "LLM 노드를 가진 워크플로우 엔진"이고, model-driven loop는 진짜 에이전트 루프다. 워크플로우가 필요한 자리에 model-driven 프레임워크를, 에이전트가 필요한 자리에 그래프 프레임워크를 넣으면 어느 쪽이든 프레임워크와 싸우게 된다.

### 설계 축 2 — 상태 관리: 어디까지 프레임워크가 책임지는가

- **LangGraph**: checkpointer가 매 super-step마다 그래프 상태를 저장하고 thread 단위로 조직한다. in-memory/SQLite/Postgres 백엔드를 지원하며, 이 위에 time-travel(과거 상태에서 재실행)과 human-in-the-loop 중단/재개가 얹힌다([LangGraph persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/)). 상태 영속화가 프레임워크의 1급 기능이다 — [내구성 실행](/01-agent-design/reliability-durable-execution) 관점에서 가장 정교하다.
- **CrewAI**: memory(단기/장기/엔티티)를 통한 세션·지식 영속화를 제공하지만([CrewAI Memory](https://docs.crewai.com/concepts/memory)), LangGraph처럼 매 스텝의 실행 상태를 체크포인트하는 모델은 아니다. Flows에는 상태 관리와 재개 기능이 있다([CrewAI Flows](https://docs.crewai.com/concepts/flows)).
- **OpenAI Agents SDK**: Sessions로 대화 이력을 관리하지만, 실행 중간 상태는 기본적으로 run 동안 메모리에 있다([OpenAI Agents SDK — Sessions](https://openai.github.io/openai-agents-python/sessions/)).
- **Strands Agents**: session management와 conversation manager를 제공하고, 상태 영속화는 세션 저장소로 위임한다([Strands — Sessions & State](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/agents/sessions-state/)).

플랫폼 관점의 함의: 상태 관리를 프레임워크에 맡길수록 그 프레임워크의 상태 스키마가 데이터 계층(DB 테이블, 직렬화 포맷)에 스며든다. 프레임워크 교체 시 코드보다 **저장된 상태의 마이그레이션**이 더 아프다. AgentCore를 쓰는 플랫폼이라면 세션 상태는 프레임워크가 아니라 [AgentCore Memory](/07-memory/)와 microVM 세션 격리(per-session lifecycle)에 두는 편이 교체 가능성을 지킨다.

### 설계 축 3 — MCP 지원

툴 생태계 접근성은 이제 MCP([Model Context Protocol](https://modelcontextprotocol.io/)) 지원 여부로 수렴하고 있고, 주요 프레임워크는 전부 지원한다 — 다만 통합 깊이가 다르다.

- **Strands**: MCP 지원이 기본 의존성으로 내장되어 있고, `MCPClient`가 MCP 서버의 툴 집합을 그대로 에이전트 툴로 노출한다([Strands — MCP tools](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/tools/mcp-tools/)).
- **OpenAI Agents SDK**: hosted MCP tools, Streamable HTTP, stdio 서버를 공식 지원한다([OpenAI Agents SDK — MCP](https://openai.github.io/openai-agents-python/mcp/)).
- **LangGraph/LangChain**: `langchain-mcp-adapters`로 MCP 서버의 툴을 LangChain 툴로 변환해 사용한다([langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)).
- **CrewAI**: MCP 서버 연동을 공식 문서에서 지원한다([CrewAI — MCP](https://docs.crewai.com/mcp/overview)).

즉 MCP는 더 이상 프레임워크 차별점이 아니라 **프레임워크 중립적인 툴 계약**이다. 이것이 이 장의 논지에 중요한 이유: 툴을 MCP 서버로 만들어 두면([MCP 서버 설계](/01-agent-design/mcp-server-design)) 툴 투자가 프레임워크 선택과 분리된다. 프레임워크를 갈아타도 툴은 그대로다.

### 특성 비교 표

작성 시점(2026-08) 공식 문서 기준의 특성 비교다. 별점이나 순위가 아니라 "무엇이 다른가"만 적는다.

| 축 | LangGraph | CrewAI | Strands Agents | OpenAI Agents SDK | Agent Framework (구 AutoGen/SK) |
|---|---|---|---|---|---|
| 오케스트레이션 모델 | 명시적 상태 그래프(노드/엣지/순환) | 역할 기반 Crews + 이벤트 기반 Flows | model-driven agent loop | 최소 프리미티브(instructions/tools/**handoffs**/guardrails) | 그래프 워크플로우 + 멀티에이전트 대화 패턴 |
| 제어 흐름 결정권 | 주로 개발자(그래프 정의) | 선언된 프로세스 + 모델 | 주로 모델(루프) | 주로 모델 + 명시적 handoff | 혼합(워크플로우/대화 패턴별) |
| 상태 관리 | checkpointer(매 super-step, thread 단위, Postgres 등) | Memory(단기/장기/엔티티), Flows 상태 | 세션 관리, 저장소 위임 | Sessions(대화 이력), run 중간 상태는 인메모리 | thread/checkpoint 지원 |
| MCP 지원 | 어댑터 패키지(`langchain-mcp-adapters`) | 공식 지원 | 기본 내장(`MCPClient`) | 공식 지원(hosted/HTTP/stdio) | 공식 지원 |
| 모델 종속성 | 모델 중립 | 모델 중립 | 모델 중립(기본 프로바이더는 Bedrock) | OpenAI 우선, 타 모델은 호환 API 경유 | 모델 중립 |
| 언어 | Python, JS/TS | Python | Python, TypeScript | Python, JS/TS 등 | Python, .NET |
| 운영 주체 | LangChain | CrewAI Inc. | AWS 주도 오픈소스 | OpenAI | Microsoft |
| 근거 | [docs](https://langchain-ai.github.io/langgraph/) | [docs](https://docs.crewai.com/) | [docs](https://strandsagents.com/) | [docs](https://openai.github.io/openai-agents-python/) | [GitHub](https://github.com/microsoft/agent-framework) |

::: warning 미정착 영역
이 표는 **2026-08 작성 시점** 기준이다. 프레임워크 지형은 분기 단위로 바뀐다 — AutoGen과 Semantic Kernel이 Agent Framework로 통합된 것처럼([microsoft/agent-framework](https://github.com/microsoft/agent-framework)), 표의 어떤 행도 1년 뒤 유효하다고 가정하지 말라. 채택 결정 전에 반드시 각 공식 문서에서 현재 상태를 재확인해야 한다. 이 변동성 자체가 이 장의 결론 — 프레임워크가 아니라 계약을 표준화하라 — 의 근거다.
:::

### 프레임워크 중립 하니스 논지

이 책의 플랫폼 전략은 세 개의 사실 위에 서 있다.

1. **배포 계약은 프레임워크를 모른다.** AgentCore Runtime이 요구하는 것은 `/ping`·`/invocations`를 노출하는 ARM64 컨테이너뿐이다([Runtime 배포 계약](/10-agentcore/runtime-deploy-contract)). LangGraph든 CrewAI든 직접 작성한 루프든, [Runtime 심화](/10-agentcore/runtime-deep-dive)의 얇은 어댑터 패턴(프레임워크 실행 함수를 `/invocations` 핸들러로 감싸고, 장기 실행 중 `/ping`이 `HealthyBusy`를 반환하게 하는 것)으로 동일하게 배포된다. 스캐폴딩 툴킷도 Strands / LangGraph / Google ADK / OpenAI Agents를 같은 방식으로 감싼다.
2. **툴 계약도 프레임워크를 모른다.** 위에서 본 대로 MCP는 모든 주요 프레임워크가 지원하는 공통 분모다.
3. **프레임워크 선택 자체를 회피할 수 있다.** AgentCore **Harness**는 모델·시스템 프롬프트·툴을 config로 지정하면 오케스트레이션 루프(툴 실행, 메모리 관리, 응답 생성)를 AWS가 대신 실행하는 managed agent loop이며, Strands 기반이다([What is Amazon Bedrock AgentCore?](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)). 이 책의 실증 사례가 `demo/builder-harness/`다 — 빌더 에이전트 데모는 오케스트레이션 코드를 한 줄도 쓰지 않고 `create-harness` API 호출 하나로 배포됐고, 그 결과 "어떤 프레임워크를 쓸 것인가"라는 질문이 그 워크로드에서는 아예 소멸했다.

따라서 플랫폼의 표준은 이렇게 선다: **기본은 Harness(managed, 프레임워크 질문 소멸), 예외는 Runtime + 얇은 어댑터(팀이 가져온 어떤 프레임워크든 동일 계약으로 수용), 금지는 프레임워크 추상화가 플랫폼 API·데이터 스키마에 스며드는 것.** 프레임워크는 컨테이너 안의 구현 디테일이어야 한다.

Anthropic의 권고("단순 LLM API 직접 사용부터 시작하라. 많은 패턴은 몇 줄이면 구현된다"; [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))도 이 구도에서 자연스럽게 수용된다 — 얇은 어댑터 전략에서는 "프레임워크 없이 직접 구현"도 그냥 또 하나의 유효한 컨테이너 내용물이다. 프레임워크를 쓰는 팀에게 Anthropic이 붙인 단서 — "그 아래의 코드를 이해하고 있으라(understand the underlying code). 밑단에 대한 잘못된 가정이 흔한 오류의 원천이다" — 는 플랫폼 요구사항으로 번역하면 "프레임워크가 조립한 최종 프롬프트와 원시 응답을 관측 파이프라인에서 볼 수 있어야 한다"가 된다(아래 [계측](#계측-sli)).

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 범용 대화형 에이전트, 표준 툴/MCP 사용으로 충분 | **AgentCore Harness** (managed) | 오케스트레이션·메모리를 AWS가 처리, 프레임워크 선택 자체를 회피 | 루프 커스터마이징 제어권 포기 — 커스텀 미들웨어 불가 |
| 단순 패턴(prompt chaining, routing, 단일 툴 루프), 디버깅 가시성이 최우선 | 프레임워크 없이 **LLM API 직접 호출** | 프롬프트/응답이 전부 내 코드에 보임 — Anthropic 권고의 기본형 | 재시도·상태 저장·스트리밍을 직접 구현 |
| 단계·분기가 명시적으로 정의되는 복잡 워크플로우, 스텝 단위 재개/휴먼 승인 필요 | **LangGraph류** (그래프) + Runtime 어댑터 | checkpointer 기반 상태 영속화·time-travel이 1급 기능 | 그래프/상태 스키마에 대한 프레임워크 종속, 추상화 계층만큼 디버깅 간접화 |
| 역할 분담이 자연스러운 멀티에이전트 협업 시나리오를 빠르게 프로토타이핑 | **CrewAI류** (역할 기반) + Runtime 어댑터 | 높은 추상화로 협업 구조를 빠르게 선언 | 세밀한 제어 흐름·상태 체크포인트는 상대적으로 약함 |
| 모델이 흐름을 결정하는 개방형 태스크, Bedrock 중심 스택 | **Strands Agents** + Runtime 어댑터 | model-driven loop, MCP 내장, AgentCore 통합이 가장 얇음 | 명시적 워크플로우 표현은 그래프 프레임워크보다 약함 |
| 팀이 이미 특정 프레임워크에 투자(코드·역량) | 해당 프레임워크 유지 + **Runtime 얇은 어댑터** | 배포 계약이 프레임워크 중립이므로 재작성 불필요 | `/ping`·`/invocations`·SIGTERM 계약을 어댑터에서 직접 지켜야 함 |

핵심 규칙: 첫 번째 행(Harness)에서 시작해 아래로 내려갈 이유를 **증명**해야 한다. "언젠가 커스텀 루프가 필요할 것 같아서"는 이유가 아니다 — [에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow)에서 본 것과 같은 종류의, 단순한 쪽이 기본값인 결정이다.

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 프로덕션 오답을 디버깅하는데 "모델에 실제로 들어간 프롬프트"를 재구성할 수 없음 | 프레임워크 추상화가 최종 프롬프트 조립을 숨김 — Anthropic이 경고한 바로 그 지점 | 트레이스에서 model-invocation span에 원시 요청/응답 페이로드가 있는지 확인 | 프레임워크의 콜백/트레이싱 훅으로 원시 프롬프트·응답을 관측 파이프라인에 기록([Part 3 — 평가 하네스](/03-accuracy-eval/eval-harness)) |
| 프레임워크 메이저 업그레이드 후 에이전트 동작이 조용히 변함 | 프레임워크 내부 기본 프롬프트/루프 정책 변경이 앱 코드 변경 없이 유입 | lockfile diff + 골든 트레이스 회귀 평가를 업그레이드 전후로 실행 | 프레임워크 버전을 컨테이너에 고정하고, 업그레이드를 평가 게이트 뒤에 배치 |
| 프레임워크를 바꾸려니 플랫폼 API·DB 스키마까지 바꿔야 함 | 프레임워크의 상태/메시지 스키마가 플랫폼 계약에 누출됨(락인) | 플랫폼 API 스펙과 저장 스키마에서 프레임워크 고유 타입(`StateGraph` 직렬화 등)을 grep | 경계에서 플랫폼 소유의 중립 스키마로 변환, 프레임워크 타입은 컨테이너 내부로 격리 |
| 장기 실행 중 Runtime이 트래픽을 라우팅하지 않음 | 어댑터가 실행 중 `/ping`에 `HealthyBusy`를 반환하지 않음 | 실행 중 `/ping` 응답을 직접 호출해 확인 | [Runtime 심화](/10-agentcore/runtime-deep-dive)의 어댑터 패턴대로 헬스체크를 실행 상태와 연동 |
| 같은 세션의 재호출에서 이전 컨텍스트 소실 | 프레임워크 인메모리 상태에 의존 — 인스턴스/microVM 재활용 가정이 깨짐 | 서로 다른 인스턴스에 라우팅되는 연속 호출로 재현 | 세션 상태를 외부화([Part 7 — 메모리](/07-memory/)) 하거나 per-session lifecycle 사용 |
| 멀티에이전트 협업이 무한 위임/순환에 빠져 비용 폭증 | 역할 기반/handoff 구조에 종료 조건·turn 상한 부재 | 트레이스에서 handoff 횟수와 반복 패턴 확인 | turn/토큰 상한과 종료 조건을 명시([단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent)) |

## 안티패턴

- ❌ 전사 표준 프레임워크를 하나 지정하고 플랫폼 API를 그 추상화에 맞춰 설계한다 → ✅ 계약(배포 계약, MCP 툴 계약, 관측 스키마)을 표준화하고 프레임워크는 컨테이너 내부 구현으로 격리한다.
- ❌ "에이전트니까 일단 프레임워크부터 고른다" → ✅ LLM API 직접 호출로 시작하고, 프레임워크가 해결해 줄 구체적 문제(체크포인트, 그래프 분기)를 확인한 뒤에 도입한다 ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
- ❌ Harness로 충분한 표준 워크로드에 Runtime + LangGraph 커스텀 스택을 세운다 → ✅ managed 기본값(Harness)에서 시작하고, 커스텀 루프가 필요하다는 증거가 생길 때만 내려간다.
- ❌ 프레임워크의 checkpointer/메모리 저장 포맷을 플랫폼의 세션 저장 표준으로 삼는다 → ✅ 세션·메모리는 플랫폼 소유 서비스(AgentCore Memory 등)에 두고 프레임워크는 그것을 읽는 쪽으로 배치한다.
- ❌ 프레임워크 버전을 느슨하게 두고(`>=`) 자동 업그레이드한다 → ✅ 컨테이너에 버전을 고정하고 골든 트레이스 평가를 통과해야 업그레이드한다.
- ❌ 블로그의 프레임워크 별점 비교를 근거로 채택을 결정한다 → ✅ 자사 워크로드의 대표 태스크로 각 후보를 같은 평가 하네스에 태워 비교한다([Part 3](/03-accuracy-eval/)).

## 계측 (SLI)

프레임워크 계층에서 플랫폼이 봐야 할 지표는 "프레임워크가 무엇을 숨기고 있는가"와 "교체 가능성이 유지되고 있는가"를 겨냥한다.

- **원시 프롬프트 관측 커버리지**: model-invocation span 중 최종 조립 프롬프트·원시 응답이 기록된 비율. 100% 미만이면 Anthropic이 경고한 디버깅 불능 상태가 잠재해 있는 것이다. 프레임워크별 콜백 훅으로 수집한다.
- **루프 형상 지표**: 호출당 model call 수, tool call 수, handoff/위임 횟수의 분포(p50/p95). 프레임워크 업그레이드나 프롬프트 변경 후 이 분포의 이동은 루프 정책이 바뀌었다는 신호다.
- **오케스트레이션 오버헤드**: 전체 지연 중 model+tool 실행 시간을 뺀 나머지(프레임워크의 상태 직렬화, 그래프 전이, 체크포인트 쓰기). [지연 해부](/02-performance/latency-anatomy)의 분해 위에 한 층을 추가하는 것이다.
- **체크포인트/세션 저장 실패율**: checkpointer 또는 세션 저장소 쓰기 실패·재개 실패 비율. 상태 관리를 프레임워크에 맡겼다면 이것이 내구성 SLI다.
- **계약 준수 헬스**: 실행 중 `/ping` 응답 정합성(HealthyBusy 반환율), SIGTERM 후 정상 종료율 — 어댑터가 배포 계약을 지키고 있는지의 직접 지표.
- **교체 가능성 드리프트**(주기적 감사 지표): 플랫폼 공개 API·저장 스키마에서 프레임워크 고유 타입이 검출되는 건수. 0이 목표이고, 증가 추세는 락인이 진행 중이라는 뜻이다.

## 체크리스트

- [ ] 이 워크로드는 Harness(managed)로 충분한가? Runtime + 프레임워크로 내려가야 할 구체적 이유(커스텀 미들웨어, 기존 그래프 투자, 프로토콜 제어)를 문서화했는가?
- [ ] 프레임워크 없이 LLM API 직접 호출로 시작하는 선택지를 검토했는가? 프레임워크가 해결하는 문제를 한 문장으로 말할 수 있는가?
- [ ] 프레임워크 고유 타입/스키마가 플랫폼 공개 API·DB 스키마·관측 스키마에 노출되지 않는가?
- [ ] 툴은 MCP 서버로 만들어 프레임워크 선택과 분리했는가? ([MCP 서버 설계](/01-agent-design/mcp-server-design))
- [ ] 최종 조립 프롬프트와 원시 모델 응답이 트레이스에 기록되는가?
- [ ] 프레임워크 버전이 컨테이너 이미지에 고정되어 있고, 업그레이드가 골든 트레이스 평가 게이트 뒤에 있는가?
- [ ] Runtime 배포 시 어댑터가 `/ping`(HealthyBusy 포함)·`/invocations`·SIGTERM 계약을 지키는가? ([Runtime 심화](/10-agentcore/runtime-deep-dive))
- [ ] 세션/메모리 상태가 프레임워크 인메모리가 아니라 플랫폼 소유 저장소에 있는가? ([Part 7](/07-memory/))
- [ ] 멀티에이전트 구성이라면 handoff/turn 상한과 종료 조건이 명시되어 있는가?
- [ ] 채택 비교를 별점·블로그가 아니라 자사 평가 하네스의 대표 태스크로 수행했는가?
- [ ] 비교 표(이 장 포함)의 작성 시점을 확인하고, 결정 전 각 공식 문서에서 현재 상태를 재검증했는가?

## 참고

- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — 프레임워크 추상화 경고, "LLM API 직접 사용부터 시작하라" 권고
- [LangGraph](https://langchain-ai.github.io/langgraph/) / [Persistence(checkpointer)](https://langchain-ai.github.io/langgraph/concepts/persistence/) / [GitHub](https://github.com/langchain-ai/langgraph)
- [CrewAI docs](https://docs.crewai.com/) / [Flows](https://docs.crewai.com/concepts/flows) / [GitHub](https://github.com/crewAIInc/crewAI)
- [Strands Agents](https://strandsagents.com/) / [GitHub](https://github.com/strands-agents/sdk-python) / [AWS Prescriptive Guidance — Strands Agents](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-frameworks/strands-agents.html)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) / [MCP 지원](https://openai.github.io/openai-agents-python/mcp/)
- [Microsoft Agent Framework(구 AutoGen + Semantic Kernel)](https://github.com/microsoft/agent-framework)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [What is Amazon Bedrock AgentCore?](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) — Harness(managed loop, Strands 기반) 정의
- [Host agent or tools with AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html) — 프레임워크 중립 컨테이너 계약
- 이 책의 관련 장: [Runtime 심화](/10-agentcore/runtime-deep-dive), [Runtime 배포 계약](/10-agentcore/runtime-deploy-contract), [에이전트 vs 워크플로우](/01-agent-design/agent-vs-workflow), [내구성 실행](/01-agent-design/reliability-durable-execution)
- 이 책의 Harness 실증 사례: `demo/builder-harness/README.md`
