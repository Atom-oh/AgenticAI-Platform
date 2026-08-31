---
title: Observability 심화
description: AgentCore Observability의 ADOT gen_ai OTEL 스팬, CloudWatch Transaction Search 함정, GenAI 시맨틱 컨벤션의 성숙도, 그리고 성능·비용·품질 SLI 계측을 다룬다.
outline: [2, 3]
---

# Observability 심화

::: tip 이 장에서 얻는 것
- AgentCore가 ADOT SDK로 방출하는 `gen_ai.*` OTEL 스팬의 정확한 구조와, `gen_ai.provider.name=aws.bedrock`처럼 Bedrock 전용으로 오버라이드되는 속성
- CloudWatch Transaction Search를 켜지 않으면 스팬 자체가 콘솔에 나타나지 않는다는 함정과, 이를 켜는 정확한 절차(콘솔/SDK 양쪽)
- OpenTelemetry GenAI 시맨틱 컨벤션의 실제 성숙도(Development 상태 전체) — "chat은 안정, agent는 불안정"이라는 통설과 공식 문서가 어떻게 다른지
- 성능·비용·품질을 하나의 트레이스에서 함께 읽기 위해 캡처해야 할 SLI 목록(캐시 토큰, tool-error rate, retrieval recall, 홉당 지연, trace-level cost)
- ADOT + IAM만으로 AgentCore 밖의 온프렘·멀티클라우드 에이전트도 같은 CloudWatch 파이프라인에 태울 수 있다는 사실과, 이것이 하이브리드 아키텍처에 갖는 의미
:::

## 왜 문제가 되는가

[Part 0의 로드맵](/00-intro/six-pain-points)은 관측을 "day 1부터 켜라"는 1단계 항목으로 못박는다. 이유는 단순하다 — 계측을 나중에 넣으면 초기 구간의 지연·비용·품질 데이터가 영구히 사라진다. 하지만 "관측을 켠다"는 말이 실제로 무엇을 의미하는지는 AgentCore에서 특히 헷갈리기 쉽다. AgentCore Runtime에 에이전트를 배포하고 트레이싱 토글을 켰는데도 CloudWatch GenAI Observability 대시보드가 비어 있는 경우가 흔하다. 원인은 대개 코드가 아니라, 계정 단위로 한 번 켜야 하는 CloudWatch Transaction Search를 빠뜩 잊은 것이다.

또 하나의 함정은 OTel GenAI 시맨틱 컨벤션의 성숙도를 잘못 이해하는 것이다. "`gen_ai.*` 속성이 표준이니 안정적으로 파싱해도 된다"고 가정하고 대시보드나 알럿을 짜면, 속성 이름이나 의미가 바뀔 때 조용히 깨진다. 이 장은 두 함정을 모두 다루고, 그 위에 성능·비용·품질을 하나의 트레이스에서 동시에 읽기 위한 SLI 계측 목록을 제시한다.

이 장은 Part 9의 [HITL과 감사](/09-authorization/hitl-audit)와 역할이 겹치지 않는다. 그 장은 "누가 언제 왜 승인했는가"라는 감사·컴플라이언스 질문에 CloudTrail data event와 OTel 스팬으로 답하는 것이 목적이고, 이 장은 "얼마나 빠른가, 얼마나 비용이 드는가, 응답이 얼마나 정확한가"라는 성능·비용·품질 관측이 목적이다.

## 핵심 개념

### ADOT SDK가 방출하는 `gen_ai.*` 스팬

AgentCore Runtime 위에서 도는 에이전트에 관측을 붙이는 표준 경로는 ADOT(AWS Distro for OpenTelemetry) SDK다. Python 기준으로는 `aws-opentelemetry-distro` 패키지를 의존성에 추가하고 `opentelemetry-instrument python my_agent.py`로 실행하면, Strands·LangChain·CrewAI 같은 프레임워크가 내부적으로 방출하는 OTel GenAI 시맨틱 컨벤션 스팬이 CloudWatch로 전달된다.

> To view this data in the CloudWatch console generative AI observability page and in Amazon CloudWatch, you need to add the AWS Distro for Open Telemetry (ADOT) SDK to your agent code.
>
> — [Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

이 스팬은 OTel GenAI 시맨틱 컨벤션(아래에서 자세히 다룸)을 따르며, AWS Bedrock을 호출하는 경우 `gen_ai.provider.name`이 `"aws.bedrock"`으로 오버라이드된다.

> `gen_ai.provider.name` MUST be set to `"aws.bedrock"` and SHOULD be provided **at span creation time**.
>
> — [Semantic conventions for AWS Bedrock operations](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md)

실무에서 확인해야 할 핵심 속성은 다음과 같다(모두 OTel GenAI 시맨틱 컨벤션의 `gen-ai-spans.md`, `gen-ai-agent-spans.md`, `aws-bedrock.md`에서 정의).

| 속성 | 의미 | 출처 |
|---|---|---|
| `gen_ai.provider.name` | 호출된 GenAI 제공자(Bedrock 경우 `aws.bedrock`) | [aws-bedrock.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md) |
| `gen_ai.request.model` | 요청된 모델명(예: Claude 모델 ID) | [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |
| `gen_ai.response.model` | 실제로 응답을 생성한 모델명 | [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |
| `gen_ai.usage.input_tokens` / `output_tokens` | 입력/출력 토큰 수(캐시된 토큰 포함) | [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |
| `gen_ai.usage.cache_read.input_tokens` / `cache_write.input_tokens` | 프롬프트 캐시 read/write 토큰(Bedrock prompt caching과 직결) | [aws-bedrock.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md) |
| `gen_ai.response.finish_reasons` | 생성 종료 이유(`stop`, `length`, `content_filter`, `error` 등) | [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |
| `gen_ai.tool.name` | `execute_tool` 스팬의 대상 툴 이름 | [gen-ai-agent-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) |
| `error.type` | 스팬 실패 시 오류 클래스(OTel 일반 컨벤션, 이 항목만 Stable) | [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |

세션 ID 전파는 두 경로로 나뉜다. AgentCore Runtime에 호스팅된 에이전트는 `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` 헤더를 요청에 넣으면 ADOT가 하위 헤더에 `session_id`를 자동으로 세팅한다. AgentCore 밖에서 도는 에이전트는 OTel baggage에 `session.id`를 직접 넣어야 한다.

> When using ADOT, in order to propagate session id correctly, define the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` in the request header. ADOT then sets the session_id correctly in the downstream headers.
>
> — [Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

### CloudWatch Transaction Search — 켜지 않으면 안 보인다

AgentCore가 스팬을 방출해도, 계정 차원에서 **CloudWatch Transaction Search를 먼저 활성화**하지 않으면 CloudWatch GenAI Observability 대시보드에 아무것도 나타나지 않는다. 이건 AgentCore 특유의 필수 선행 조건이며, 공식 문서가 명시적으로 "one-time setup"이라고 부른다.

> To view metrics, spans, and traces generated by the AgentCore service, you first need to complete a one-time setup to turn on Amazon CloudWatch Transaction Search. To view service-provided spans for memory resources, you also need to enable tracing when you create a memory.
>
> — [Enabling AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

활성화는 두 경로다.

1. **콘솔**: CloudWatch → Application Signals (APM) → Transaction search → Enable Transaction Search → "ingest spans as structured logs" 체크 → Save.
2. **CLI/SDK**: X-Ray에 CloudWatch Logs로 `PutLogEvents`를 허용하는 리소스 정책을 `logs put-resource-policy`로 걸고, `aws xray update-trace-segment-destination --destination CloudWatchLogs`로 트레이스 목적지를 CloudWatch Logs로 지정한 뒤, 필요하면 `aws xray update-indexing-rule`로 샘플링 비율을 조정한다.

게이트웨이·런타임·빌트인 툴·메모리 각 리소스에서 개별적으로 **Tracing** 토글을 켜야 하는데, 이 토글 자체도 Transaction Search가 먼저 켜져 있지 않으면 동작하지 않는다.

> You must have CloudWatch Transaction Search enabled before you can enable tracing.
>
> — [Configure tracing delivery to CloudWatch using the console](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

### 스팬 목적지 — 공유 로그 그룹 vs 에이전트별 로그 그룹

기본적으로 AgentCore 스팬은 계정 전체가 공유하는 `aws/spans` 로그 그룹에 쌓인다. 리전에 따라 최근 생성된 에이전트는 기본값이 에이전트별 로그 그룹(`/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint_name>`)으로 바뀌었고, `UNIFIED_TRACES_DESTINATION_ENABLED` 환경 변수로 명시적으로 전환할 수도 있다. 단 이 기능은 **ADOT `aws-opentelemetry-distro>=0.18.0`** 이상에서만 동작하며, 구버전은 이 설정을 무시하고 공유 로그 그룹으로 보낸다. 멀티테넌트 환경에서 에이전트별로 접근 제어·암호화를 분리하고 싶다면 이 버전 요구사항을 반드시 확인해야 한다.

### GenAI 시맨틱 컨벤션의 실제 성숙도

::: warning 미정착 영역
"chat/embeddings 스팬은 안정(Stable)이고 agent·tool orchestration·평가·안전 스코어만 provisional"이라는 식으로 정리된 자료를 종종 보게 되지만, 2026년 8월 현재 OpenTelemetry의 공식 GenAI 시맨틱 컨벤션 저장소([open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai))를 직접 확인하면 그렇지 않다. `gen-ai/README.md`, `gen-ai-spans.md`(추론·임베딩·retrieval 포함), `gen-ai-agent-spans.md`, `gen-ai-metrics.md`, `aws-bedrock.md`, `anthropic.md` **전체 문서가 예외 없이 `Status: Development`**로 표기되어 있다. 개별 속성 단위로도 `gen_ai.request.model`, `gen_ai.response.finish_reasons`, `gen_ai.usage.cache_read.input_tokens` 등 거의 모든 `gen_ai.*` 속성이 `Development` 배지를 달고 있다. 유일하게 `Stable` 배지가 붙은 속성은 GenAI 고유가 아닌 OTel 일반 컨벤션의 `error.type`이다.

즉 "chat만 안정적"이라는 안정성 구분은 현재 공식 스펙 문서에는 존재하지 않는다 — chat/embeddings와 agent/tool 스팬 모두 아직 development 단계이며, 언제든 속성명이나 시맨틱이 바뀔 수 있다. 실제 위험은 chat/embeddings이 "이미 안정적이니 안심하고" 파서를 하드코딩하는 것이다. 대시보드·알럿을 만들 때는 속성 이름 변경에 대비해 파싱 레이어를 한 곳에 모으고, ADOT/시맨틱 컨벤션 버전을 픽스해서 업그레이드를 의도적으로 관리해야 한다.
:::

> **Status**: [Development][DocumentStatus]
>
> — [Semantic conventions for generative AI systems](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md), [Semantic conventions for generative client AI spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md), [Semantic Conventions for GenAI agent and framework spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)

### AgentCore Observability를 AgentCore 밖으로 확장하기 — 하이브리드로 가는 다리

AgentCore Observability는 AgentCore Runtime에 호스팅된 에이전트만을 위한 것이 아니다. ADOT SDK와 IAM 자격 증명만 갖추면, **온프렘이나 다른 클라우드에서 도는 에이전트도 같은 CloudWatch 파이프라인으로 텔레메트리를 보낼 수 있다.**

> With AgentCore, you can also view metrics for agents that aren't running in the AgentCore runtime. Additional setup steps are required to configure telemetry outputs for non-AgentCore agents.
>
> — [Enabling observability for agents hosted outside of AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

절차는 AWS 자격 증명 환경 변수(`AWS_ACCESS_KEY_ID` 등)와 OTel 환경 변수(`AGENT_OBSERVABILITY_ENABLED=true`, `OTEL_PYTHON_DISTRO=aws_distro`, `OTEL_RESOURCE_ATTRIBUTES`에 로그 그룹·엔드포인트 지정 등)를 설정하는 것으로 끝난다. 단, **ADOT Collector 자체는 AgentCore 관측에서 지원되지 않는다** — SDK 직접 계측이나 Lambda용 OTel Layer만 지원된다.

> **ADOT Collector not supported for agent observability.** The ADOT Collector is not supported for agent observability. To send telemetry from an agent hosted outside of AgentCore runtime, you must use either the ADOT SDK or the AWS Lambda Layer for OpenTelemetry.
>
> — [Enabling observability for agents hosted outside of AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)

이는 [Part 12 하이브리드 아키텍처](/12-security-korea/hybrid-architecture)에서 다루는 EKS Hybrid Nodes + PrivateLink 구성에 직결된다. 온프렘에서 도는 에이전트도 IAM 자격 증명(장기 키가 아니라 역할 기반이 바람직)만 확보하면 같은 CloudWatch GenAI Observability 대시보드에서 프로덕션 에이전트와 동일하게 트레이스·비용·SLI를 볼 수 있다는 뜻이다. 다만 ADOT Collector가 지원되지 않으므로, 온프렘 네트워크에서 CloudWatch/X-Ray 엔드포인트로의 아웃바운드 경로(PrivateLink 또는 인터넷 게이트웨이)를 SDK가 직접 열 수 있어야 한다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| AgentCore Runtime에 배포된 에이전트 관측 | ADOT SDK + `opentelemetry-instrument` 자동계측 | 프레임워크(Strands/LangChain/CrewAI)가 이미 OTel GenAI 스팬을 방출 | 프레임워크가 지원하지 않는 커스텀 로직은 수동 계측 필요 |
| AgentCore 밖(Lambda)에서 도는 에이전트 관측 | AWS Lambda Layer for OpenTelemetry | `aws-opentelemetry-distro` 패키지·구동 명령 불필요, 레이어만 추가 | Lambda 콜드스타트에 레이어 로딩 시간 소폭 추가 |
| 온프렘/멀티클라우드 에이전트 관측 | ADOT SDK + IAM 자격 증명 직접 설정 | ADOT Collector 미지원이므로 SDK가 CloudWatch/X-Ray로 직접 전송 | 온프렘 네트워크에서 AWS 엔드포인트로의 아웃바운드 경로 필요(PrivateLink 권장) |
| 프레임워크가 Strands/LangChain/CrewAI가 아닐 때 | OpenInference/Openllmetry/OpenLit/Traceloop 중 하나 선택 | AgentCore가 명시적으로 지원을 확인한 계측 라이브러리 | 라이브러리마다 속성 커버리지가 다름 — 직접 검증 필요 |
| 멀티테넌트에서 에이전트별 로그 격리 필요 | `UNIFIED_TRACES_DESTINATION_ENABLED=true` + 에이전트별 로그 그룹 | 접근 제어·암호화를 에이전트 단위로 분리 가능 | ADOT `aws-opentelemetry-distro>=0.18.0` 필수, 기존 스팬은 이동 안 됨 |
| 다른 관측 플랫폼(Datadog 등)과 통합 | `DISABLE_ADOT_OBSERVABILITY=true` | AgentCore 기본 ADOT 환경변수를 해제하고 자체 파이프라인 사용 | CloudWatch GenAI Observability 대시보드는 못 씀 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 트레이싱을 켰는데 CloudWatch GenAI Observability 대시보드가 비어 있음 | CloudWatch Transaction Search를 계정 단위로 활성화하지 않음 | CloudWatch 콘솔 → Application Signals (APM) → Transaction search 상태 확인 | Transaction Search를 먼저 활성화한 뒤 리소스별 Tracing 토글을 켠다 |
| 특정 에이전트의 스팬만 안 보임(다른 에이전트는 정상) | 그 에이전트의 실행 역할에 `logs:PutResourcePolicy` 권한이 없어 에이전트별 로그 그룹으로 전달 실패 | 실행 역할 정책과 X-Ray 리소스 정책의 `Resource` ARN 확인 | 실행 역할에 해당 로그 그룹에 대한 권한 부여, 또는 공유 `aws/spans` 그룹으로 되돌림 |
| `UNIFIED_TRACES_DESTINATION_ENABLED=true`로 설정했는데 스팬이 계속 공유 로그 그룹에 쌓임 | ADOT `aws-opentelemetry-distro`가 0.18.0 미만 — 구버전은 이 설정을 무시 | `pip show aws-opentelemetry-distro` 버전 확인 | 0.18.0 이상으로 업그레이드 |
| 캐시 적중률을 추적하고 싶은데 트레이스에서 캐시 토큰이 안 보임 | `gen_ai.usage.cache_read.input_tokens` / `cache_write.input_tokens`는 `Recommended` 속성이라 계측 라이브러리가 기본으로 방출하지 않을 수 있음 | 사용 중인 계측 라이브러리(OpenInference 등)의 속성 커버리지를 스팬 raw JSON에서 직접 확인 | 필요하면 커스텀 스팬 속성으로 직접 추가 계측 |
| 대시보드가 어느 날 갑자기 값이 비거나 이름이 바뀜 | GenAI 시맨틱 컨벤션 전체가 `Development` 상태 — 속성명/의미가 예고 없이 바뀔 수 있음 | ADOT/계측 라이브러리 버전 업그레이드 로그와 변경 시점을 대조 | 파서를 한 곳에 모으고, 업그레이드를 의도적 릴리스로 관리(자동 최신화 금지) |
| 온프렘 에이전트의 스팬이 전혀 CloudWatch에 도달하지 않음 | ADOT Collector로 배치했는데, AgentCore 관측은 Collector를 지원하지 않음 | 온프렘 배포 구성이 Collector 기반인지 SDK 직접계측인지 확인 | ADOT SDK 직접 계측 또는 Lambda 레이어 방식으로 전환 |
| `sessionId`가 트레이스에서 끊겨 홉 간 연결이 안 됨 | AgentCore Runtime 호출 시 `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` 헤더 누락, 또는 외부 에이전트에서 OTel baggage에 `session.id` 미설정 | 트레이스의 `session.id`/`gen_ai.conversation.id` 속성이 홉마다 동일한지 확인 | 헤더 또는 baggage 설정을 호출 경로 전체에 일관되게 적용 |

## 안티패턴

- ❌ 트레이싱 토글만 켜고 CloudWatch Transaction Search 활성화를 잊는다 → ✅ 계정을 새로 쓸 때 Transaction Search 활성화를 온보딩 체크리스트의 0번 항목으로 둔다.
- ❌ `gen_ai.*` 속성명을 대시보드/알럿 쿼리에 하드코딩하고 "표준이니 안 바뀐다"고 가정한다 → ✅ 전체 컨벤션이 `Development` 상태임을 전제로 파싱 레이어를 분리하고 버전을 고정한다.
- ❌ 캐시 read/write 토큰을 계측하지 않은 채 "캐시가 잘 히트되고 있다"고 가정한다 → ✅ `gen_ai.usage.cache_read.input_tokens` / `cache_write.input_tokens`를 매 트레이스에서 확인하고 [Part 4 캐시 지표](/04-caching/cache-metrics-economics)와 연결한다.
- ❌ 온프렘 에이전트에 ADOT Collector를 배치하고 관측이 되길 기대한다 → ✅ ADOT SDK 직접 계측 또는 Lambda OTel Layer만 지원됨을 배포 전에 확인한다.
- ❌ trace-level cost 없이 모델 호출 건수만 세서 비용을 추정한다 → ✅ 스팬의 `gen_ai.usage.input_tokens`/`output_tokens`(캐시 토큰 포함)와 모델별 단가를 곱해 트레이스 단위 비용을 계산한다.

## 계측 (SLI)

에이전트 워크로드는 단일 지표로 건강 상태를 판단할 수 없다. 아래 SLI를 같은 트레이스 컨텍스트(같은 `trace_id`/`session_id`) 안에서 함께 캡처해야 성능·비용·품질을 하나의 그림으로 읽을 수 있다.

- **캐시 read/write 토큰** — `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens`. 캐시 미스율이 올라가면 지연과 비용이 동시에 악화되므로([Part 4](/04-caching/cache-miss-root-causes)) 트레이스 레벨에서 추적해야 근본 원인(프리픽스 변경, 툴 목록 재정렬 등)에 빠르게 도달한다.
- **tool-error rate** — `execute_tool` 스팬의 `error.type` 발생 비율을 툴별로 집계. 특정 툴만 오류율이 튀면 그 툴의 스키마·인자 검증을 먼저 본다.
- **retrieval recall** — RAG를 쓰는 에이전트라면 retrieval 스팬(`gen-ai-spans.md`의 Retrievals 섹션)에서 반환된 문서와 실제 정답 문서 집합을 비교해 recall을 오프라인/온라인으로 계측. Part 3의 검색 실패율과 직결된다.
- **홉당 지연(per-hop latency)** — `gen_ai.client.operation.duration`, `gen_ai.server.time_to_first_token`, `execute_tool.duration`을 각 홉별로 분해해서 어느 단계(추론 vs 툴 실행 vs 검색)가 전체 지연을 지배하는지 특정한다.
- **trace-level cost** — 트레이스 안의 모든 `gen_ai.usage.*` 토큰 합계에 모델별 단가를 곱해 요청 1건의 실제 비용을 계산한다. 세션 단위·에이전트 단위로 롤업하면 "어느 사용자/어느 에이전트가 비용을 태우는가"에 직접 답할 수 있다.

이 SLI들은 개별 대시보드 패널이 아니라 **하나의 트레이스에서 함께 조회 가능해야** 의미가 있다. 지연이 튀었을 때 그것이 캐시 미스 때문인지, 툴 오류 재시도 때문인지, 검색 실패로 컨텍스트가 커진 것 때문인지 트레이스 하나로 구분할 수 있어야 근본 원인 분석 시간이 줄어든다.

## 체크리스트

- [ ] CloudWatch Transaction Search를 계정 단위로 활성화했는가(콘솔 또는 `update-trace-segment-destination`)
- [ ] AgentCore Runtime/Memory/Gateway/Built-in tools 각 리소스에서 Tracing 토글을 개별적으로 켰는가
- [ ] ADOT SDK(`aws-opentelemetry-distro`) 버전이 필요한 기능(예: 에이전트별 로그 그룹)의 최소 요구 버전(0.18.0+)을 만족하는가
- [ ] `gen_ai.provider.name`이 Bedrock 호출에서 `aws.bedrock`으로 정확히 세팅되는지 실제 스팬 raw 데이터로 확인했는가
- [ ] 캐시 read/write 토큰, tool-error rate, retrieval recall, 홉당 지연, trace-level cost를 모두 같은 트레이스 컨텍스트에서 조회 가능한가
- [ ] 세션 ID가 AgentCore Runtime 헤더 또는 OTel baggage로 호출 경로 전체에 일관되게 전파되는가
- [ ] GenAI 시맨틱 컨벤션이 `Development` 상태임을 감안해 속성 파싱 레이어를 분리하고 계측 라이브러리 버전을 의도적으로 고정했는가
- [ ] 온프렘/멀티클라우드 에이전트가 있다면 ADOT SDK 직접 계측(Collector 미지원)으로 CloudWatch까지 IAM 경로가 열려 있는가
- [ ] 관측을 day 1부터 켜서 [로드맵 1단계](/00-intro/six-pain-points)의 요구를 충족했는가

## 참고

- [Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [Semantic conventions for generative AI systems (README)](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md)
- [Semantic conventions for generative client AI spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
- [Semantic Conventions for GenAI agent and framework spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)
- [Semantic conventions for AWS Bedrock operations](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md)
- [Semantic conventions for generative AI metrics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)
- [Part 0 — 6대 통증점과 실행 로드맵](/00-intro/six-pain-points)
- [Part 9 — HITL과 감사](/09-authorization/hitl-audit)
- [Part 12 — 하이브리드 아키텍처](/12-security-korea/hybrid-architecture)
