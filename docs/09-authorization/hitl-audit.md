---
title: HITL과 감사
description: 되돌리기 어려운 에이전트 액션에 human approval을 강제하는 HITL 패턴과, 모든 결정을 감사 가능하게 남기는 방법을 다룬다.
outline: [2, 3]
---

# HITL과 감사

::: tip 이 장에서 얻는 것
- Bedrock Agents의 user confirmation과 return of control(ROC), Step Functions `.waitForTaskToken`, AgentCore Runtime async task라는 네 가지 HITL 구현 패턴의 차이와 적용 기준
- 액션의 reversibility·blast radius·임계치로 위험 등급을 나누고, 등급별로 승인 메커니즘을 매핑하는 결정 프레임
- Cedar(Dogwood) temporal policy로 "승인 이벤트 없이는 실행 불가"를 정책 레벨에서 강제하는 방법
- 승인/거부 결정을 CloudTrail data event와 OTel 스팬으로 남겨 컴플라이언스 감사에 대응하는 방법
- AgentCore 자체에 네이티브 approval API가 있는지에 대한 정확한 확인 결과
:::

## 왜 문제가 되는가

에이전트가 스스로 판단해서 결제를 실행하거나, 데이터를 삭제하거나, 프로덕션에 배포하는 순간 실패의 성격이 바뀐다. 잘못된 API 응답 하나, 프롬프트 인젝션 하나가 되돌릴 수 없는 상태 변경으로 이어질 수 있다. AWS Well-Architected Framework의 Agentic AI Lens는 이 문제를 정확히 두 개의 극단으로 요약한다.

> Routing every agent action through human review produces rubber-stamp approvals. Routing none produces unbounded autonomy.
>
> — [AGENTSEC04-BP02 Human-in-the-loop for critical decisions](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)

모든 툴 호출에 사람 승인을 걸면 reviewer는 지쳐서 내용을 읽지 않고 승인 버튼만 누르게 된다(rubber-stamp). 반대로 아무 것도 걸지 않으면 에이전트가 무한 자율권을 갖게 된다. 이 장이 다루는 HITL(Human-in-the-loop)은 이 둘 사이에서 "사람의 판단이 실제로 결과를 바꿀 수 있는 지점"에만 정확히 멈춰 세우는 설계다.

여기에 감사(audit)가 붙는 이유도 같은 문서에 나온다. 승인/거부 결정에 reviewer identity와 timestamp가 남지 않으면, "이 결제를 누가 언제 왜 승인했는가"라는 컴플라이언스 질문에 답할 수 없다. HITL과 감사는 분리된 두 기능이 아니라, 같은 설계의 앞면과 뒷면이다.

## 핵심 개념

### HITL을 구현하는 네 가지 메커니즘

AWS 공식 문서는 에이전트의 실행 환경에 따라 서로 다른 HITL 구현 패턴을 제시한다. 하나를 골라 모든 곳에 쓰는 것이 아니라, 에이전트가 어디서 도는지에 따라 다른 메커니즘을 쓴다.

**Bedrock Agents — User confirmation**. 액션 그룹의 특정 액션에 대해 실행 전에 함수명과 파라미터 값을 애플리케이션에 반환하고, 사용자에게 yes/no 확인을 받게 한다.

> When you configure your action group, you can choose to enable user confirmation for specific actions. If user confirmation is enabled for an action, agent responds with a confirmation question asking end user to either confirm or deny the action.
>
> — [Get user confirmation before invoking action group function](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-userconfirmation.html)

**Bedrock Agents — Return of control(ROC)**. user confirmation보다 한 단계 더 넓다. 액션 그룹 단위로 설정하며, 에이전트가 호출하려는 함수/API의 `invocationInputs`와 고유한 `invocationId`를 애플리케이션에 그대로 넘긴다. 애플리케이션은 이 정보를 사용해 승인뿐 아니라 파라미터 수정, 검증, 거부까지 수행할 수 있고, 결과를 같은 `invocationId`로 다시 `InvokeAgent`의 `sessionState`에 넣어 돌려준다.

> If you configure return of control for an action group, and if the agent determines that it should call an action in this action group, the API or function details elicited from the user will be returned in the `invocationInputs` field in the InvokeAgent response, alongside a unique `invocationId`.
>
> — [Return control to the agent developer](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html)

두 패턴의 실무적 차이는 AWS 블로그가 명확히 정리한다.

> With ROC, the agent provides the developer with the information about the task that it wants to execute and completely relies on the developer to execute the task. In this approach, the developer has the possibility to not only validate the agent's decision, but also contribute with additional context and modify parameters during the agent's execution process.
>
> — [Implement human-in-the-loop confirmation with Amazon Bedrock Agents](https://aws.amazon.com/blogs/machine-learning/implement-human-in-the-loop-confirmation-with-amazon-bedrock-agents/)

두 패턴 모두 "애플리케이션이 승인 UX를 구현한다"는 전제를 깐다. 즉 승인 화면, 알림, 거부 처리 로직은 여전히 여러분이 만들어야 한다.

**Step Functions `.waitForTaskToken` 콜백**. 워크플로가 태스크 토큰을 발급하고 일시 정지한다. 이 토큰은 SNS·SES·webhook 등 reviewer 집단에 맞는 채널로 승인 애플리케이션에 전달되고, 애플리케이션이 reviewer를 대신해 `SendTaskSuccess`/`SendTaskFailure`를 호출한다. reviewer가 Step Functions API를 직접 호출하는 경우는 거의 없다 — 승인 앱이 자격 증명을 쥐고, reviewer는 앱과 상호작용한다. (출처: [AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html))

**AgentCore Runtime — async task**. 승인이 몇 분~몇 시간씩 걸리는 경우(다중 reviewer 합의 등)를 위한 메커니즘이다. `add_async_task`/`complete_async_task`로 태스크 수명을 관리하고, `/ping` 엔드포인트가 `Healthy`/`HealthyBusy` 상태를 보고한다.

> Amazon Bedrock AgentCore Runtime supports both synchronous and asynchronous processing through a unified API, enabling an agent to start a task that may take minutes or hours, immediately acknowledge the request, continue approval workflows in the background, and let the user check back later for results.
>
> — [AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)

블로킹 대기(reviewer 응답 대기 등)는 별도 스레드나 async 메서드로 돌려야 한다. 그렇지 않으면 `/ping` 헬스체크가 막히고, 15분간 응답이 없으면 런타임 세션이 종료된다([Handle asynchronous and long running agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)).

::: warning 미정착 영역
**AgentCore 자체의 네이티브 approval 기능은 확인 필요.** 공식 문서를 확인한 결과, AgentCore Runtime에는 Bedrock Agents의 user confirmation/ROC 같은 "승인 API"가 별도로 존재하지 않는다. AgentCore가 제공하는 것은 범용 async task 수명 관리(`add_async_task`, `/ping`)뿐이며, 실제 "누구에게 승인을 물을지·거부 시 무엇을 할지"는 프레임워크 훅으로 직접 구현해야 한다. 예를 들어 Strands Agents는 `BeforeToolCallEvent`에 콜백을 등록해 툴 실행 직전에 `event.interrupt(...)`로 에이전트 루프를 멈추고 사람 응답을 기다리는 패턴을 지원하며, 이 패턴이 AgentCore Runtime 위에서 동작하는 예제가 헬스케어 워크로드용으로 공개되어 있다.

```python
class ApprovalHook(HookProvider):
    SENSITIVE_TOOLS = ["get_patient_condition", "get_patient_vitals"]

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.approve)

    def approve(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] not in self.SENSITIVE_TOOLS:
            return
        approval = event.interrupt(
            f"{event.tool_use['name']}-approval",
            reason={"reason": f"Authorize {event.tool_use['name']} with args: {event.tool_use.get('input', {})}"},
        )
        if approval.lower() not in ["y", "yes", "t"]:
            event.cancel_tool = f"User denied permission to run {event.tool_use['name']}"
```
(출처: [Human-in-the-loop constructs for agentic workflows in healthcare and life sciences](https://aws.amazon.com/blogs/machine-learning/human-in-the-loop-constructs-for-agentic-workflows-in-healthcare-and-life-sciences/))

즉 "AgentCore = HITL 내장"이 아니라 "AgentCore Runtime의 async task + 프레임워크 레벨 hook interrupt"의 조합이 현재의 구현 방식이다. 이 조합형 구현이 대규모 프로덕션에서 얼마나 검증되었는지에 대한 커뮤니티 레퍼런스는 아직 많지 않으므로, 자체 벤치마크 없이 그대로 신뢰하지 말 것.
:::

### Cedar(Dogwood) temporal policy로 승인을 정책 레벨에 강제하기

`/09-authorization/cedar-verified-permissions.md`에서 다루는 AgentCore Policy(Cedar/Dogwood)는 승인 UX 자체를 만들지 않지만, "승인 이벤트가 세션 안에 기록되어 있어야만 민감한 액션을 permit한다"는 규칙을 정책 엔진 레벨에서 강제할 수 있다. 즉 애플리케이션이 승인 로직을 빠뜨리거나 우회해도, gateway 앞단의 정책이 막아준다.

공식 문서의 one-time-use approval 예제가 정확히 이 패턴이다. `get_account_balance` 호출을 "승인" 행위로 취급하고, 그 응답이 최근 1시간 내에 기록되어 있어야 `transfer_funds`를 permit한다.

```
permit (
  principal,
  action == AgentCore::Action::"FundsTarget___transfer_funds",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/my-gateway"
)
when temporal {
  !AgentCore::Action::"FundsTarget___transfer_funds"::response{ eventResource: resource }
  since within 1h AgentCore::Action::"FundsTarget___get_account_balance"::response{ eventResource: resource }
};
```

승인이 성립하면 그 승인은 1회성으로 소비된다 — 이체가 완료되는 순간 다음 이체는 새 승인이 있어야 한다. (출처: [Authoring temporal policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal-authoring.html))

같은 문서는 다자 승인에 가까운 패턴도 제공한다. `count` 표현식으로 특정 시간창 안에 일치하는 이벤트가 임계치 이상 쌓였는지 확인하는 "approval threshold" 패턴이다.

```
when temporal {
    exists (n: Long).
        (count for (t: Timepoint).
            where (formerly within 5m (AgentCore::Action::"FundsTarget___transfer_funds"::response{ eventResource: resource, input.toAccount: context.input.customerId } && tp(t)))) == n
        && n >= 2
};
```

단, 문서는 `count`가 "일치하는 이벤트 수"만 세고 "서로 다른 principal인지"는 구분하지 않는다고 명시한다 — 즉 이건 "N건의 이벤트" 임계치이고, "N명의 서로 다른 승인자"를 강제하는 멀티파티 승인은 아니다. 진짜 다자 승인이 필요하면 이벤트에 승인자 ID를 실어 correlate하는 별도 설계가 필요하다.

거부 이력을 반영하는 패턴도 있다. 세션 내에서 `get_account_balance`가 거부(`::error`)된 적이 있으면, 이후 `transfer_funds`를 forbid하는 것이다 — 한 번의 이상 신호가 세션 전체의 신뢰 수준을 낮추는 설계다. (같은 출처)

이 세 패턴을 조합하면 "고액 이상은 반드시 사람 승인을 거쳐야 한다"는 요구를 애플리케이션 로직이 아니라 정책으로 표현할 수 있다: 승인 앱이 사람의 승인을 받은 뒤에만 "승인 완료" 툴을 호출하고, temporal policy는 그 호출의 `response` 이벤트가 세션에 기록되어 있어야 실제 고액 액션을 permit한다. 애플리케이션 코드에 있는 `if approved:` 분기 하나에만 의존하지 않게 된다.

### 위험 등급 분류: 정적 속성 + 동적 신호

AGENTSEC04-BP02는 위험 분류기를 LLM에 맡기지 말라고 명시한다.

> Risk classification itself can't rely on an LLM exposed to the same untrusted content as the request being evaluated, because adversarial content could influence the classifier into marking the request as low-risk. Use deterministic logic (policy engines, rule-based classifiers) as the authoritative signal, with LLM-assisted classification as an optional input that a deterministic layer re-checks.
>
> — [AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)

기준선은 다음과 같다: 읽기 전용 오퍼레이션은 자율 실행, 저위험 쓰기는 단일 reviewer 승인, 금융 트랜잭션·데이터 삭제·외부 커뮤니케이션 같은 고위험 오퍼레이션은 단일/다중/out-of-band 중 하나의 강한 승인 경로를 거친다.

### 영구 트러스트 그랜트(persistent trust grant)

같은 오퍼레이션 패턴을 반복적으로 승인받는 게 비효율적일 때, "한 번 승인하면 이후 자동 통과"시키는 그랜트가 유용할 수 있다. 그러나 이는 사람의 판단 시점을 "매 순간"에서 "그랜트 발급 시점"으로 옮기는 것이므로, 문서는 명확한 스코프 제약을 요구한다.

> If you implement persistent trust, bound each grant to a specific command, parameter shape, or resource. Tier grants by risk so higher-risk operations are ineligible for persistent trust or require re-confirmation at a defined cadence, and make grants themselves auditable and revocable. Wildcard trust grants (approving all future operations of a given type with no parameter scoping) effectively remove human oversight from an entire class of operations and should not be issued.
>
> — [AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)

### 감사: CloudTrail data event와 OTel 스팬

Bedrock Agents의 `InvokeAgent`/`InvokeInlineAgent` 호출은 CloudTrail에서 관리 이벤트가 아니라 **데이터 이벤트**로 기록되며, 기본적으로 켜져 있지 않다.

> Amazon Bedrock logs all Agents for Amazon Bedrock Runtime API operations (such as InvokeAgent and InvokeInlineAgent) actions to CloudTrail as data events. To log InvokeAgent calls, configure advanced event selectors to record data events for the AWS::Bedrock::AgentAlias resource type.
>
> — [Monitor Amazon Bedrock API calls using CloudTrail](https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html)

즉 advanced event selector로 `AWS::Bedrock::AgentAlias` 리소스 타입의 데이터 이벤트를 명시적으로 활성화하지 않으면, 에이전트가 어떤 툴을 호출했는지 CloudTrail에 전혀 남지 않는다. AgentCore Identity 쪽도 마찬가지로 `GetWorkloadAccessToken`, `GetResourceOauth2Token` 같은 이벤트가 `bedrock-agentcore.amazonaws.com` 소스로 CloudTrail에 기록되며, 토큰 값 자체는 redact된다([Authenticate with Private Key JWT using Amazon Bedrock AgentCore Identity](https://aws.amazon.com/blogs/machine-learning/authenticate-with-private-key-jwt-using-amazon-bedrock-agentcore-identity/)).

승인/거부 결정 자체도 별도로 로그에 남겨야 한다. AGENTSEC04-BP02는 "Log every approval: Capture reviewer identity, timestamps, operation under review, decision, and escalation events"를 구현 단계로 명시한다. CloudTrail은 API 호출을 기록하지만, "reviewer B가 오전 9시에 이 이체를 승인했다"는 비즈니스 결정 자체는 승인 애플리케이션이 별도로 구조화된 로그(예: S3 + Athena, 또는 OTel 스팬 속성)로 남겨야 한다.

관측 축은 AgentCore Observability다. AgentCore는 OpenTelemetry 호환 계측을 기본 제공하며, `bedrock-agentcore` CloudWatch 네임스페이스로 분산 트레이스·스팬 로그·메트릭을 내보낸다.

> Amazon Bedrock AgentCore emits distributed traces, structured span-level logs, and metrics under the bedrock-agentcore CloudWatch namespace. This telemetry follows the OpenTelemetry (OTEL) protocol and routes to Amazon CloudWatch by default.
>
> — [Debugging production agents with Amazon Bedrock AgentCore Observability](https://aws.amazon.com/blogs/machine-learning/debugging-production-agents-with-amazon-bedrock-agentcore-observability/)

Strands·LangChain·CrewAI 같은 프레임워크는 OpenInference/Openllmetry/OpenLit/Traceloop 계측 라이브러리를 통해 OTel semantic convention을 이미 지원하며, ADOT SDK(`aws-opentelemetry-distro`)를 추가하면 CloudWatch 콘솔의 GenAI observability 페이지에서 바로 확인할 수 있다([Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)). 툴 호출 스팬에 승인 여부·reviewer ID를 커스텀 속성으로 실으면, 트레이스 하나로 "이 요청이 어떤 승인을 거쳐 실행됐는가"를 추적할 수 있다. 스팬 스키마와 W3C Trace Context 전파의 구체적인 설계는 `/10-agentcore/observability-deep-dive.md`에서 이어서 다룬다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 읽기 전용 조회 (조회, 검색) | 자율 실행, HITL 없음 | 되돌릴 수 있고 blast radius가 0에 가까움 | 과도한 승인은 reviewer fatigue를 유발한다 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |
| 저위험 쓰기 (내부 티켓 생성, 비고 업데이트) | 단일 reviewer 승인 | 가역적이지만 감사 추적은 필요 | 승인 지연이 워크플로 속도를 낮춤 |
| Bedrock Agents 기반 에이전트, 승인자가 최종 사용자 | User confirmation | 함수/파라미터를 그대로 사용자에게 yes/no로 노출, 구현이 가장 단순 | 파라미터 수정·검증 로직을 넣을 수 없음 ([Get user confirmation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-userconfirmation.html)) |
| Bedrock Agents 기반 에이전트, 애플리케이션이 파라미터를 검증/수정해야 함 | Return of control(ROC) | `invocationInputs`를 그대로 받아 애플리케이션이 실행 여부·내용을 완전히 통제 | 액션 그룹 단위로만 설정 가능해 세분화가 액션그룹 설계에 종속됨 ([Return control](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html)) |
| Step Functions 기반 워크플로, reviewer가 최종 사용자와 분리된 별도 역할 | `.waitForTaskToken` 콜백 | SNS/SES/webhook으로 reviewer 채널을 자유롭게 구성, 비동기 워크플로에 적합 | 승인 앱을 별도로 구축·운영해야 함 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |
| 승인 처리가 분~시간 단위로 오래 걸림 (다자 합의 등) | AgentCore Runtime async task (`add_async_task`/`complete_async_task`) | 즉시 응답 후 백그라운드로 승인 대기, 8시간까지 실행 지원 | 블로킹 대기를 별도 스레드로 분리하지 않으면 `/ping`이 막혀 세션이 15분 후 종료됨 ([Handle asynchronous and long running agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)) |
| 고액 결제·데이터 삭제 등 임계치 기반 필수 승인을 정책으로 강제 | Cedar/Dogwood temporal policy (승인 이벤트를 `since`/`count`로 요구) | 애플리케이션 코드 우회로부터 독립적인 강제(policy engine이 gateway 앞단에서 차단) | 정책 작성이 이벤트 스키마·시간창 설계에 익숙해야 하고, `response` 이벤트가 기록되기까지의 레이스 컨디션을 고려해야 함 ([Authoring temporal policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal-authoring.html)) |
| 반복적인 저위험 패턴에 매번 승인받기가 비효율적 | 영구 트러스트 그랜트 (스코프 제한) | 승인 시점을 "매 요청"에서 "그랜트 발급 시점"으로 이동해 운영 효율 확보 | 고위험 오퍼레이션은 그랜트 대상에서 제외해야 하며, wildcard 그랜트는 절대 금지 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 승인 요청이 거의 100% 승인되고 거부율이 0에 가까움 | 모든 액션을 위험도 구분 없이 승인 대상으로 만들어 rubber-stamp화 | 승인/거부/타임아웃 비율과 reviewer당 평균 결정 시간을 계측 | 정적 속성+동적 신호 기반 deterministic risk classifier로 read-only는 자율화, 고위험만 승인 경로에 남김 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |
| reviewer가 액션명과 파라미터만 보고 몇 초 안에 승인 | 추론 체인·데이터 소스·잠재적 결과가 승인 화면에 없어 판단이 아니라 형식 절차가 됨 | 평균 승인 소요 시간이 컨텍스트를 읽기엔 지나치게 짧은지 확인 | 전체 decision context를 S3 등 durable storage에 먼저 저장하고, 인증된 승인 UI 또는 presigned URL로 노출 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |
| reviewer가 응답하지 않으면 워크플로가 무한 대기 상태로 멈춤 | 타임아웃 정책과 에스컬레이션 경로가 정의되지 않음 | 승인 대기 상태로 멈춰 있는 세션/실행 개수를 모니터링 | Step Functions `TimeoutSeconds`/`HeartbeatSeconds` + `Catch`로 2차 reviewer 에스컬레이션, 기본 fallback은 차단 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |
| CloudTrail에서 에이전트의 툴 호출/승인 기록이 전혀 안 보임 | `InvokeAgent`는 데이터 이벤트이며 기본적으로 로깅되지 않음 | CloudTrail Event History에서 `AWS::Bedrock::AgentAlias` 데이터 이벤트가 활성화되어 있는지 확인 | Advanced event selector로 `AWS::Bedrock::AgentAlias` 데이터 이벤트를 명시적으로 켠다 ([Monitor Amazon Bedrock API calls using CloudTrail](https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html)) |
| Cedar temporal policy가 방금 받은 승인을 인식하지 못하고 DENY | 승인 호출과 의존 액션을 백투백으로 붙여 보내, `response` 이벤트가 아직 기록되기 전에 다음 요청이 도착 | 승인 직후 바로 실행한 케이스에서만 DENY가 재현되는지 확인 | 승인 호출이 완료되고 그 `response`가 기록됐음을 확인한 뒤 의존 액션을 보낸다 ([Authoring temporal policies의 Note](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal-authoring.html)) |
| 영구 트러스트 그랜트가 예상보다 훨씬 넓은 범위의 액션을 자동 승인 | 그랜트를 command/parameter/resource로 스코프하지 않고 액션 타입 전체에 wildcard로 발급 | 그랜트 정의를 감사해 파라미터 제약이 있는지 확인 | 그랜트를 구체적 command·parameter shape·resource로 한정하고, 고위험 오퍼레이션은 그랜트 대상에서 제외 ([AGENTSEC04-BP02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)) |
| AgentCore Runtime 기반 승인 대기 에이전트 세션이 15분 뒤 강제 종료됨 | reviewer 응답 대기 같은 블로킹 작업이 `/ping` 헬스체크 스레드를 막음 | 세션 종료 로그와 `/ping` 응답 지연 상관관계 확인 | 블로킹 대기를 별도 스레드/async로 분리하고 `add_async_task`/`complete_async_task`로 상태를 명시적으로 관리 ([Handle asynchronous and long running agents](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)) |

## 안티패턴

- ❌ 모든 툴 호출을 예외 없이 사람 승인 대기로 만든다 → ✅ 정적 속성(액션 종류, 리소스)과 동적 신호(빈도, 시간대, 이상 징후)로 위험 등급을 나누고, 읽기 전용은 자율 실행시킨다.
- ❌ 위험 여부를 요청을 처리하는 LLM 자신에게 판단시킨다 → ✅ deterministic risk classifier(정책 엔진, 규칙 기반)를 신뢰 소스로 삼고, LLM 판단은 재검증 대상인 보조 신호로만 쓴다.
- ❌ 승인 화면에 함수명과 파라미터만 노출한다 → ✅ 추론 체인, 참조한 데이터 소스, 예상 결과를 포함한 전체 decision context를 durable storage에 저장하고 인증된 뷰로 제공한다.
- ❌ 타임아웃/에스컬레이션 없이 승인 워크플로를 배포한다 → ✅ 타임아웃 시 안전한 fallback(차단)과 2차 reviewer 에스컬레이션 경로를 명시한다.
- ❌ 한 번의 승인으로 이후 모든 유사 액션을 wildcard로 자동 허용한다 → ✅ 영구 트러스트 그랜트는 command·parameter·resource로 스코프하고, 감사 가능·철회 가능하게 만든다.
- ❌ 승인 로직을 애플리케이션 코드의 `if approved:` 한 줄에만 의존한다 → ✅ Cedar/Dogwood temporal policy로 "승인 이벤트가 세션에 기록되어 있어야 permit"을 정책 엔진 레벨에서 강제해, 코드 우회로부터 독립적인 방어선을 둔다.
- ❌ CloudTrail이 기본으로 모든 걸 기록한다고 가정하고 별도 설정 없이 배포한다 → ✅ `InvokeAgent`/`InvokeInlineAgent`는 데이터 이벤트이므로 advanced event selector로 명시적으로 활성화한다.

## 계측 (SLI)

- **승인 대기열 길이 / 평균 대기 시간**: reviewer 병목이 실제 운영을 막고 있는지 확인. 급증하면 risk tier 재조정 또는 reviewer 인원 확충 신호.
- **승인/거부/타임아웃 비율**: 거부율이 0에 가깝고 승인률이 100%에 근접하면 rubber-stamp 징후 — AGENTSEC04-BP02가 명시한 안티패턴과 동일.
- **reviewer별 decision latency 분포**: 특정 reviewer가 지나치게 빨리(맥락을 읽지 않고) 혹은 지나치게 늦게 결정하는지 확인.
- **temporal policy DENY율**: Cedar temporal policy가 "승인 없이 시도된 민감 액션"을 얼마나 막고 있는지 — 이 값이 0이면 정책이 실효성이 없거나(모든 시도가 이미 사전 승인됨) 아니면 정책이 실제로 평가되지 않고 있는 것.
- **CloudTrail 데이터 이벤트 커버리지**: `AWS::Bedrock::AgentAlias` 등 필요한 리소스 타입의 데이터 이벤트가 실제로 활성화되어 기록되고 있는지 — Event History 쿼리로 주기 점검.
- **AgentCore Observability 스팬 완전성**: 승인 여부·reviewer ID가 툴 호출 스팬의 커스텀 속성으로 실제 기록되는지, W3C Trace Context가 승인 앱까지 끊기지 않고 전파되는지. 구체 스키마는 `/10-agentcore/observability-deep-dive.md` 참고.
- **영구 트러스트 그랜트 audit**: 발급된 그랜트 수, 스코프(command/parameter/resource), 마지막 재확인(re-confirmation) 시점 — wildcard 그랜트가 존재하는지 주기적으로 스캔.

## 체크리스트

- [ ] 모든 에이전트 액션을 reversibility·blast radius·금액/위험 임계치 기준으로 위험 등급(자율/단일 승인/다중 승인/out-of-band)으로 분류했다
- [ ] 위험 분류기는 LLM이 아니라 deterministic 로직이며, LLM 판단은 재검증되는 보조 신호로만 사용한다
- [ ] Bedrock Agents 기반 에이전트는 user confirmation 또는 ROC 중 액션 특성에 맞는 패턴을 선택했다
- [ ] Step Functions 기반 워크플로는 `.waitForTaskToken` 콜백으로 승인 앱과 연동했다
- [ ] 장시간 승인이 필요한 AgentCore Runtime 에이전트는 async task(`add_async_task`/`complete_async_task`)로 구현했고, 블로킹 대기를 별도 스레드로 분리했다
- [ ] 고액/고위험 액션은 Cedar/Dogwood temporal policy로 "승인 이벤트가 세션에 기록되어야 permit"을 정책 레벨에서 강제한다
- [ ] 영구 트러스트 그랜트는 command·parameter·resource로 스코프되고, 감사 가능·철회 가능하며, wildcard 그랜트는 존재하지 않는다
- [ ] 타임아웃과 에스컬레이션 경로가 모든 승인 메커니즘에 정의되어 있고, 기본 fallback은 차단이다
- [ ] 승인/거부 결정에 reviewer identity와 timestamp가 남고, 전체 decision context가 durable storage에 저장되어 있다
- [ ] `AWS::Bedrock::AgentAlias` 등 필요한 CloudTrail 데이터 이벤트가 advanced event selector로 명시적으로 활성화되어 있다
- [ ] AgentCore Observability/OTel 스팬에 승인 여부와 reviewer 정보가 속성으로 기록되고, Part 10 observability 파이프라인과 연결되어 있다

## 참고

- [AGENTSEC04-BP02 Human-in-the-loop for critical decisions — AWS Well-Architected Agentic AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)
- [Get user confirmation before invoking action group function — Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-userconfirmation.html)
- [Return control to the agent developer by sending elicited information in an InvokeAgent response — Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html)
- [Implement human-in-the-loop confirmation with Amazon Bedrock Agents — AWS Machine Learning Blog](https://aws.amazon.com/blogs/machine-learning/implement-human-in-the-loop-confirmation-with-amazon-bedrock-agents/)
- [Human-in-the-loop constructs for agentic workflows in healthcare and life sciences — AWS Machine Learning Blog](https://aws.amazon.com/blogs/machine-learning/human-in-the-loop-constructs-for-agentic-workflows-in-healthcare-and-life-sciences/)
- [Handle asynchronous and long running agents with Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)
- [Authoring temporal policies — Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal-authoring.html)
- [Monitor Amazon Bedrock API calls using CloudTrail](https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html)
- [Auditing generative AI workloads with AWS CloudTrail — AWS Cloud Operations Blog](https://aws.amazon.com/blogs/mt/auditing-generative-ai-workloads-with-aws-cloudtrail/)
- [Authenticate with Private Key JWT using Amazon Bedrock AgentCore Identity — AWS Machine Learning Blog](https://aws.amazon.com/blogs/machine-learning/authenticate-with-private-key-jwt-using-amazon-bedrock-agentcore-identity/)
- [AGENTOPS05-BP01 Establish end-to-end tracing and telemetry for agent operations — AWS Well-Architected Agentic AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops05-bp01.html)
- [Debugging production agents with Amazon Bedrock AgentCore Observability — AWS Machine Learning Blog](https://aws.amazon.com/blogs/machine-learning/debugging-production-agents-with-amazon-bedrock-agentcore-observability/)
- [Add observability to your Amazon Bedrock AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- 관련 챕터: `/09-authorization/cedar-verified-permissions.md`(Cedar/Dogwood 정책 기초), `/10-agentcore/observability-deep-dive.md`(OTel 스팬 스키마 심화)
