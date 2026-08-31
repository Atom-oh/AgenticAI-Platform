---
title: 토큰 어카운팅과 차지백
description: 멀티테넌트 에이전트 플랫폼에서 트레이스 기반 토큰 합산, application inference profile과 Cost Allocation Tags를 이용한 비용 귀속, showback/chargeback 조직 설계, per-run 예산 캡과 비용 서킷 브레이커의 구현 지점을 다룬다.
outline: [2, 3]
---

# 토큰 어카운팅과 차지백

::: tip 이 장에서 얻는 것
- "누가 얼마를 썼는가"에 답하는 3개 계층 — **트레이스 계층**(OTel `gen_ai.usage.*` 합산), **청구 계층**(application inference profile + Cost Allocation Tags + CUR), **실시간 강제 계층**(게이트웨이/하니스 토큰 카운터) — 의 역할 분담
- 멀티에이전트 run에서 서브에이전트들의 토큰을 trace id로 묶어 **요청당 진짜 비용**을 계산하는 방법과, 그 합계가 청구서와 어긋나는 전형적 원인들
- Bedrock의 공식 비용 귀속 수단 — application inference profile, Cost Allocation Tags, Cost Explorer/CUR 분해, model invocation logging의 `identity.arn` — 각각의 적용 범위와 한계
- showback과 chargeback의 구분, 그리고 어느 쪽을 먼저 도입해야 하는가에 대한 조직 설계 관점
- per-run 예산 캡과 비용 서킷 브레이커를 **어느 컴포넌트에** 구현해야 하는가 — 청구 데이터는 강제 수단이 될 수 없는 이유
:::

## 왜 문제가 되는가

플랫폼이 단일 팀의 에이전트 하나만 돌릴 때 비용 질문은 간단하다 — 월말 청구서의 Amazon Bedrock 항목이 곧 그 에이전트의 비용이다. 그러나 플랫폼이 여러 팀/테넌트의 에이전트를 호스팅하는 순간 이 등식은 무너진다. 모든 팀이 같은 AWS 계정, 같은 모델, 심지어 같은 cross-region inference profile을 통해 호출하면, 청구서에는 모델별·리전별 총액만 남고 **팀별·테넌트별·요청별 분해는 불가능**해진다. Bedrock 사용량이 커질수록 재무 조직은 "이 비용을 어느 코스트 센터에 배부하느냐"를 묻기 시작하는데, 계측 없이 사후에 답할 방법은 없다.

멀티에이전트 아키텍처는 이 문제를 한 단계 더 어렵게 만든다. Anthropic의 실측에 따르면 멀티에이전트 시스템은 챗 대비 **약 15배의 토큰**을 소비하며, 토큰 사용량이 성능 분산의 80%를 설명한다[^multi-agent]. 하나의 사용자 요청이 오케스트레이터와 서브에이전트 N개를 발동시키면, 각 서브에이전트의 LLM 호출은 개별 스팬으로 흩어진다. 오케스트레이터의 호출만 세면 요청당 비용을 수 배에서 십수 배 과소계상하게 되고, 이 상태에서 단가를 산정해 chargeback을 시작하면 플랫폼 조직이 차액을 떠안는다. [Part 1의 단일 vs 멀티 판정](/01-agent-design/single-vs-multi-agent)이 "per-run 예산 캡 + 비용 서킷 브레이커 없이 멀티를 배포하지 말라"고 못박은 것과, [실행 로드맵 3단계](/00-intro/six-pain-points#실행-로드맵)가 이를 멀티에이전트 도입의 전제 조건으로 둔 것은 모두 이 장의 어카운팅이 실제로 동작한다는 가정 위에 서 있다.

마지막으로, 에이전트 플랫폼의 비용은 모델 토큰만이 아니다. AgentCore Runtime의 microVM CPU/메모리 과금([Part 10 쿼터와 가격](/10-agentcore/quotas-pricing)), Gateway 호출 건수, Memory 이벤트 건수도 테넌트별로 귀속되어야 완전한 그림이 나온다. 토큰 어카운팅과 인프라 어카운팅은 메커니즘이 다르므로 별도로 설계해야 한다.

## 핵심 개념

### 3개 계층: 트레이스, 청구, 실시간 강제

토큰 어카운팅은 하나의 시스템이 아니라 목적이 다른 세 계층이다. 이 구분을 놓치면 "Cost Explorer가 있으니 예산 통제도 된다"는 식의 범주 오류를 저지르게 된다.

| 계층 | 데이터 | 답하는 질문 | 지연 |
|---|---|---|---|
| **트레이스 계층** | OTel 스팬의 `gen_ai.usage.*` 속성 | 이 요청/세션/에이전트가 토큰을 얼마나 썼는가 | 준실시간 |
| **청구 계층** | Cost Allocation Tags, Cost Explorer, CUR | 이 팀/테넌트에게 실제로 얼마를 배부하는가 | 시간~일 단위 |
| **실시간 강제 계층** | 게이트웨이/하니스의 인메모리 토큰 카운터 | 이 run을 지금 중단해야 하는가 | 요청 경로 내 동기 |

트레이스 계층은 엔지니어링 진단용이고, 청구 계층은 재무 정산용이며, 실시간 강제 계층만이 폭주하는 run을 실제로 멈출 수 있다. 청구 데이터는 본질적으로 지연 데이터다 — 예컨대 AWS Budgets의 예산 데이터는 실시간이 아니라 주기 갱신(하루 최대 3회)이므로[^budgets], 알림이 도착했을 때는 이미 임계를 한참 지난 뒤일 수 있다. Budgets는 안전망이지 브레이크가 아니다.

### 멀티에이전트 run의 토큰 합산: trace id가 정산 단위다

요청당 진짜 비용의 정의는 다음과 같다: **하나의 사용자 요청에서 파생된 모든 LLM 호출 스팬의 토큰을 trace id로 묶어 합산하고, 모델별 단가를 곱한 값**. OTel GenAI 시맨틱 컨벤션에서 각 inference 스팬은 `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`를, Bedrock 호출은 추가로 `gen_ai.usage.cache_read.input_tokens` / `gen_ai.usage.cache_write.input_tokens`를 담는다[^genai-spans][^genai-bedrock]. 속성별 정확한 의미와 AgentCore ADOT SDK가 방출하는 스팬 구조는 [Part 10 Observability 딥다이브](/10-agentcore/observability-deep-dive)가 정본이다.

합산 로직은 개념적으로 단순하다:

```
run_cost(trace_id) =
  Σ (스팬 s ∈ trace_id, s.kind = inference):
      s.input_tokens        × price_in(s.model)
    + s.output_tokens       × price_out(s.model)
    + s.cache_read_tokens   × price_cache_read(s.model)
    + s.cache_write_tokens  × price_cache_write(s.model)
```

단가는 모델·리전별로 다르고 캐시 read는 할인, 캐시 write는 모델에 따라 표준 입력 단가보다 비쌀 수 있으므로 [Bedrock 가격 페이지](https://aws.amazon.com/bedrock/pricing/)의 모델별 수치를 조회 테이블로 유지해야 한다. 세션 단위·에이전트 단위·테넌트 단위 롤업은 이 trace-level cost를 상위 키(`session.id`, 테넌트 식별 속성)로 다시 집계하는 것에 불과하다.

이 합산이 성립하려면 두 가지 전제가 필요하다. 첫째, **trace context 전파** — 오케스트레이터가 서브에이전트를 스폰할 때 W3C trace context(또는 AgentCore의 세션 헤더/baggage)를 반드시 전달해야 한다. 전파가 끊기면 서브에이전트의 스팬은 별도 트레이스가 되어 합산에서 누락된다. 둘째, **캐시 토큰 계측** — `gen_ai.usage.cache_read/write.input_tokens`는 Recommended 속성이라 계측 라이브러리가 기본으로 방출하지 않을 수 있다[^genai-bedrock]. 캐시 토큰을 빼고 합산하면 프롬프트 캐싱을 많이 쓰는 워크로드일수록 청구서와의 괴리가 커진다.

::: warning 미정착 영역
OTel GenAI 시맨틱 컨벤션은 2026년 8월 현재 **전체가 `Status: Development`**다 — `gen_ai.usage.*`를 포함한 거의 모든 속성이 안정(Stable) 상태가 아니다([Part 10 Observability 딥다이브](/10-agentcore/observability-deep-dive)에서 상세 검증). 어카운팅 파이프라인이 속성 이름을 하드코딩하면 컨벤션 개정 시 조용히 과소계상이 시작된다. 파싱 계층을 분리하고 계측 라이브러리 버전을 고정하라. 또한 "트레이스 합산 비용"을 그대로 정산 금액으로 쓸지, AWS 청구 데이터로 보정한 값을 쓸지는 업계 표준이 없는 조직별 결정 사항이다.
:::

### 트레이스 합계 ≠ 청구서: 어긋나는 이유들

트레이스 기반 합산과 실제 청구액은 원리적으로 일치해야 하지만 실무에서는 어긋난다. 대표적 원인:

- **CloudWatch 메트릭의 정의**: Bedrock의 `InputTokenCount` 메트릭은 **캐시된 토큰을 제외한** 입력 토큰이다. 총 입력 소비는 `InputTokenCount + CacheWriteInputTokens`로 합산해야 하며, `CacheReadInputTokens`는 별도 (할인) 단가로 과금된다[^token-counting]. 트레이스와 메트릭을 교차검증할 때 이 정의 차이를 모르면 "메트릭이 맞고 트레이스가 틀렸다"는 오진에 빠진다.
- **실패·재시도 호출**: SDK 레벨 재시도나 스로틀 후 재호출도 성공 시 과금되지만, 계측이 최종 성공 응답의 usage만 기록하면 누락된다.
- **비-트레이스 경로**: 배치 평가, 오프라인 잡, 콘솔 playground 사용처럼 트레이싱 밖에서 발생한 호출은 청구서에만 나타난다.
- **quota 소진과 과금의 구분**: Anthropic 모델의 출력 토큰 burndown rate(예: 일부 모델에서 출력 1토큰이 쿼터 10~15토큰을 소진)는 **스로틀링 쿼터 계산용**이지 과금 배수가 아니다[^token-counting]. 쿼터 대시보드 수치를 비용으로 오독하지 말 것 — 쿼터 설계는 [동시성·쿼터·스로틀링](/08-scaling-cost/concurrency-quotas-throttling) 참고.

그래서 운영 원칙은 이렇다: **트레이스 합계는 귀속과 진단의 1차 데이터, 청구 데이터는 월 단위 ground truth**. 둘의 비율(어카운팅 커버리지)을 SLI로 추적하고, 괴리가 커지면 계측 누수를 찾는다.

### Bedrock의 공식 비용 귀속 수단

**Application inference profile.** 계정 안에 사용자가 직접 생성하는 추론 프로파일로, 태그를 붙일 수 있어서 Bedrock 비용 귀속의 1차 수단이다. 테넌트/제품/코스트 센터별로 프로파일을 만들고, 애플리케이션은 base model ID 대신 프로파일 ARN으로 호출한다[^aip-docs][^aip-blog]:

```bash
aws bedrock create-inference-profile \
  --inference-profile-name "tenant-a-prod" \
  --model-source '{"copyFrom": "arn:aws:bedrock:<REGION>::foundation-model/<MODEL_ID>"}' \
  --tags '[{"key": "TenantID", "value": "tenant-a"},
           {"key": "CostCenter", "value": "cc-1234"}]'
```

이후 Billing and Cost Management 콘솔에서 해당 태그를 **cost allocation tag로 활성화**해야 Cost Explorer/CUR에 나타난다. 공식 문서가 명시하는 두 가지 함정: 활성화 후 태그가 반영되기까지 **최대 24시간**이 걸리고, cost allocation tag는 **소급 적용되지 않는다** — 활성화 이전에 발생한 비용은 영원히 미태깅 상태로 남는다[^aip-docs]. 활성화 후에는 Cost Explorer에서 태그로 필터/그룹핑하고, CUR(classic과 CUR 2.0 모두)에서 라인 아이템 단위로 분해할 수 있다[^aip-docs].

**Cost Explorer / CUR 분해.** 태그 없이도 Cost Explorer는 Bedrock 비용을 usage type(모델 + 입출력 방향 + 리전) 단위로 그룹핑해 보여주므로, "어느 모델이 비용을 지배하는가"는 태깅 없이 답할 수 있다. 그러나 "어느 팀인가"는 태그 또는 프로파일 없이는 불가능하다. Cost allocation tag의 일반 메커니즘은 AWS Billing 문서가 정본이다[^cat-docs].

**Model invocation logging의 `identity.arn`.** 프로파일을 만들 수 없거나 호출 주체별(개발자/역할별) 분해가 필요하면, model invocation logging을 켜고 로그의 `identity.arn` 필드로 호출자를 식별할 수 있다. CloudWatch Logs Insights로 ARN별 토큰 합계를 집계하는 패턴이 AWS 공식 블로그에 문서화되어 있다[^cw-blog]:

```
fields @timestamp, identity.arn, input.inputTokenCount, output.outputTokenCount
| stats sum(input.inputTokenCount) as totalInputTokens,
        sum(output.outputTokenCount) as totalOutputTokens,
        count(*) as invocationCount by identity.arn
```

이 방식은 "누가 호출했는가"까지만 답한다. 하나의 서비스 role이 여러 테넌트를 대신 호출하는 멀티테넌트 게이트웨이 구조에서는 identity가 곧 테넌트가 아니므로, 테넌트 분해는 프로파일 또는 트레이스 계층으로 해야 한다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 내부 운영 노트에는 "CUR 2.0이 Bedrock API 호출의 IAM caller identity를 자동 기록하므로 Bedrock 측 설정 없이 per-developer 귀속이 가능하다"는 정리가 있으나, 이 장 집필 시점에 해당 동작을 명시한 AWS 공식 문서를 직접 확인하지 못했다. CUR 2.0 기반 identity 귀속을 설계에 넣기 전에 [Data Exports(CUR 2.0) 문서](https://docs.aws.amazon.com/cur/latest/userguide/what-is-data-exports.html)의 컬럼 스키마에서 실제 지원 여부를 검증하라. 공식 블로그가 확인해주는 경로는 invocation logging의 `identity.arn`이다[^cw-blog].

### AgentCore 자체 비용의 테넌트 귀속

모델 토큰 밖의 비용 — Runtime microVM의 vCPU-hour/GB-hour, Gateway 호출, Memory 이벤트([Part 10 쿼터와 가격](/10-agentcore/quotas-pricing)) — 은 **리소스 태그 기반**으로 귀속한다. AgentCore는 Runtime, Browser, Code Interpreter, Gateway 등 리소스에 `TagResource`/`tag-resource`로 태그를 부착할 수 있고[^agentcore-tagging], harness에 부여한 태그는 harness가 생성하는 managed Runtime·Runtime endpoint·managed Memory로 전파된다[^harness-ops]. 따라서 테넌트별로 Runtime(또는 harness)을 분리 배포하는 구조라면 태그 → cost allocation tag 활성화 → Cost Explorer 분해의 동일한 파이프라인을 재사용할 수 있다. 반대로 하나의 Runtime을 여러 테넌트가 공유하면 microVM 비용의 테넌트 분해는 태그로 불가능하고, 세션(`runtimeSessionId`)-테넌트 매핑을 트레이스 계층에서 직접 유지해야 한다.

### Showback vs chargeback: 조직 설계

- **Showback**: 팀별 비용을 계측해 보여주기만 하고 실제 예산 이전은 없다. 데이터 신뢰도가 낮아도 시작할 수 있고, 계측 오류의 정치적 비용이 없다.
- **Chargeback**: 팀 예산에서 실제로 차감한다. 배부 금액에 이의가 제기되므로 어카운팅 커버리지와 미귀속 비용 처리 규칙이 먼저 합의되어 있어야 한다.

권장 순서는 showback → chargeback이다. showback을 1~2 청구 주기 돌리면서 (1) 트레이스 합계와 청구서의 괴리율, (2) 미태깅 비용의 비율을 안정화한 뒤에 chargeback으로 전환한다. chargeback 전환 시에는 미귀속 비용(공용 인프라, 미태깅 잔여분)을 누가 부담하는가 — 플랫폼 조직 흡수, 사용량 비례 배부, 균등 배부 — 를 재무와 사전에 문서화해야 한다. 이 배부 규칙 자체는 기술 문제가 아니라 조직 합의의 문제이며, 어떤 규칙도 계측이 부실하면 분쟁을 만든다.

### per-run 예산 캡과 비용 서킷 브레이커의 구현 지점

강제는 실시간 계층의 일이다. 구현 지점은 모든 LLM 호출이 지나가는 곳 — **게이트웨이 또는 에이전트 하니스** — 이어야 한다.

- **per-run 예산 캡**: run 시작 시 trace id에 카운터를 열고, 매 모델 응답의 usage(input/output/cache 토큰)를 누적한다. 임계 초과 시 하드 킬이 아니라 **graceful termination** — 지금까지의 부분 결과를 요약해 반환하고 종료한다. 서브에이전트 스폰 시점에 남은 예산을 분할 할당하면 서브에이전트 루프에 의한 발산도 함께 막힌다. 스폰 깊이/폭 하드 리밋과 병행한다([Part 1](/01-agent-design/single-vs-multi-agent)).
- **비용 서킷 브레이커**: 테넌트/에이전트 단위로 시간창(시간당·일당) 누적 비용을 추적하고, 임계 초과 시 신규 멀티에이전트 run의 스폰을 차단하거나 단일 에이전트/저비용 모델로 강등한다. 임계치는 per-run 비용 분포의 p99/p50 배율 데이터를 근거로 설정한다([Part 1의 SLI](/01-agent-design/single-vs-multi-agent#계측-sli)).

두 장치 모두 어카운팅 파이프라인(트레이스 계층)과 같은 usage 데이터를 소비하지만, 집계 저장소를 기다리면 안 된다 — 요청 경로 내의 동기 카운터여야 폭주 run을 그 run 안에서 멈출 수 있다. AWS Budgets 알림은 이 두 장치가 모두 뚫렸을 때의 마지막 안전망으로만 배치한다[^budgets].

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 테넌트/제품 단위로 Bedrock 비용을 재무 시스템에 배부해야 함 | Application inference profile + cost allocation tags[^aip-docs] | Cost Explorer/CUR에서 태그로 직접 분해, 커스텀 분석 불필요 | 프로파일 생성·관리 오버헤드, 테넌트×모델 조합만큼 프로파일 증가, 태그 비소급 |
| 호출 주체(role/개발자)별 사용량만 알면 됨 | Model invocation logging + `identity.arn` 집계[^cw-blog] | Bedrock 설정만으로 가능, Logs Insights 쿼리로 즉시 분해 | 게이트웨이 뒤의 테넌트는 식별 불가, 로그 저장 비용 |
| 요청당·세션당 비용, 멀티에이전트 run 합산이 필요함 | 트레이스 계층 — `gen_ai.usage.*` × 단가를 trace id로 합산[^genai-spans] | 청구 데이터로는 요청 단위 분해가 원리적으로 불가능 | 계측 커버리지 관리 필요, 컨벤션 Development 상태 리스크 |
| 폭주 run을 실시간으로 중단해야 함 | 게이트웨이/하니스의 동기 토큰 카운터 + per-run 캡 | 청구/집계 계층은 지연 데이터라 강제 수단이 될 수 없음[^budgets] | 요청 경로에 상태 관리 추가, 임계치 튜닝 필요 |
| 비용 가시화를 이제 막 시작하는 조직 | Showback부터, 1~2 청구 주기 후 chargeback 전환 | 커버리지가 검증되기 전의 chargeback은 배부 분쟁을 유발 | 그 기간 동안 비용 책임의 실질적 이전은 없음 |
| AgentCore Runtime/Gateway/Memory 비용의 테넌트 귀속 | 테넌트별 리소스 분리 + 리소스 태그[^agentcore-tagging] | harness 태그가 managed 리소스로 전파되어 파이프라인 재사용 가능[^harness-ops] | 공유 Runtime 구조에서는 태그 귀속 불가 — 세션-테넌트 매핑을 직접 유지 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 요청당 비용이 청구서 대비 계속 과소계상됨 | 서브에이전트 스폰 시 trace context 미전파 — 서브에이전트 토큰이 별도 트레이스로 고아화 | 멀티에이전트 run의 트레이스에서 inference 스팬 수와 실제 모델 호출 수 비교 | 스폰 경로 전체에 W3C trace context/baggage 전파를 강제, 하니스 레벨에서 계측 |
| 캐시를 많이 쓰는 워크로드에서 트레이스 합계와 청구서 괴리가 큼 | `cache_read/write.input_tokens` 미계측(Recommended 속성이라 기본 방출 아님)[^genai-bedrock], 또는 캐시 단가 미반영 | 스팬 raw JSON에서 캐시 토큰 속성 존재 확인 | 캐시 토큰을 커스텀 속성으로 계측하고 단가 테이블에 cache read/write 단가 추가 |
| 태그를 붙였는데 Cost Explorer에 안 나타남 | Cost allocation tag 미활성화, 활성화 후 24시간 미경과, 또는 활성화 이전 비용(비소급)[^aip-docs] | Billing 콘솔 Cost allocation tags에서 활성화 상태 확인 | 태그 활성화를 프로파일 생성 파이프라인의 필수 단계로 자동화 |
| CloudWatch 토큰 메트릭 합계가 트레이스 합계와 다름 | `InputTokenCount`는 캐시 토큰 제외 — 총 입력은 `+ CacheWriteInputTokens`[^token-counting] | 메트릭 정의를 공식 표와 대조[^cw-metrics] | 교차검증 수식을 메트릭 정의에 맞게 수정 |
| 예산 알림이 왔을 땐 이미 대규모 초과 | AWS Budgets는 주기 갱신(하루 최대 3회)의 지연 데이터[^budgets], 실시간 강제 계층 부재 | 초과 발생 시각과 알림 시각의 차이 확인 | 게이트웨이/하니스에 동기 per-run 캡 + 서킷 브레이커 구현, Budgets는 안전망으로 격하 |
| chargeback 도입 후 배부 금액에 대한 팀 간 분쟁 | 미귀속(미태깅) 비용의 배부 규칙 미합의, 어카운팅 커버리지 검증 없이 정산 시작 | untagged 비용 비율과 트레이스-청구 괴리율 측정 | showback으로 롤백해 커버리지 안정화, 미귀속 비용 부담 규칙을 재무와 문서화 |
| 특정 테넌트의 AgentCore Runtime 비용을 분리할 수 없음 | 여러 테넌트가 하나의 Runtime/harness를 공유 — 리소스 태그는 리소스 단위 귀속만 가능[^agentcore-tagging] | CUR에서 해당 Runtime의 비용이 단일 태그로만 잡히는지 확인 | 테넌트별 Runtime 분리 배포, 불가하면 세션-테넌트 매핑 기반 내부 배부 |

## 안티패턴

- ❌ 오케스트레이터의 LLM 호출만 계측하고 요청당 비용이라 부른다 → ✅ trace id로 서브에이전트 스팬까지 합산한다. 멀티에이전트는 챗 대비 약 15배 토큰이다[^multi-agent] — 루트 스팬만 세면 그 배수만큼 과소계상한다.
- ❌ 모든 팀이 base model ID(또는 시스템 cross-region profile)로 직접 호출하게 두고 나중에 비용을 나누려 한다 → ✅ 테넌트별 application inference profile ARN을 게이트웨이가 주입한다. 태그는 비소급이므로[^aip-docs] "나중에"는 없다.
- ❌ Cost Explorer/Budgets를 비용 통제 장치로 배치한다 → ✅ 그것은 가시화·안전망이다. 강제는 요청 경로 내 동기 카운터(per-run 캡, 서킷 브레이커)만 할 수 있다[^budgets].
- ❌ 트레이스 합산 파이프라인에 `gen_ai.usage.*` 속성명을 하드코딩하고 표준이라 가정한다 → ✅ 전체 컨벤션이 Development 상태다 — 파싱 계층 분리, 라이브러리 버전 고정([Part 10](/10-agentcore/observability-deep-dive)).
- ❌ 커버리지 검증 없이 곧바로 chargeback으로 시작한다 → ✅ showback으로 1~2 청구 주기 동안 트레이스-청구 괴리율과 미태깅 비율을 안정화한 뒤 전환한다.
- ❌ 토큰 비용만 배부하고 AgentCore Runtime/Gateway/Memory 비용은 플랫폼 공용비로 묻어둔다 → ✅ 리소스 태그로 함께 귀속한다[^agentcore-tagging] — 유휴 메모리 과금처럼 테넌트 행동이 유발하는 인프라 비용이 있다([Part 10](/10-agentcore/quotas-pricing)).

## 계측 (SLI)

- **trace-level cost 분포 (p50/p99, 테넌트별)** — 요청당 진짜 비용. p99/p50 배율은 꼬리 발산의 조기 신호이자 서킷 브레이커 임계치의 근거 데이터다([Part 1](/01-agent-design/single-vs-multi-agent#계측-sli)과 공유).
- **어카운팅 커버리지** — (트레이스 합산 토큰 × 단가) ÷ (청구 계층의 Bedrock 실비용). 1.0에서 멀어질수록 계측 누수 또는 단가 테이블 부패. 월 단위로 청구서와 대사한다.
- **미귀속 비용 비율** — 전체 Bedrock + AgentCore 비용 중 활성 cost allocation tag가 없는 라인 아이템의 비율. chargeback 전환의 게이트 지표.
- **per-run 캡 발동률과 절약 추정치** — 캡이 중단시킨 run 수와, 중단 시점 소비량 기반 절약 추정. 발동률 0은 임계치가 무의미하게 높다는 신호일 수 있다.
- **서킷 브레이커 발동 횟수(테넌트별)** — 반복 발동하는 테넌트는 요금제/쿼터 협상 또는 에이전트 설계 리뷰 대상이다.
- **캐시 토큰 비중** — trace-level cost 중 cache read/write가 차지하는 비율. 캐시 경제성 판단은 [Part 4 캐시 지표](/04-caching/cache-metrics-economics)와 연결된다.

## 체크리스트

- [ ] 하나의 멀티에이전트 run에 대해 트레이스의 inference 스팬 수 = 실제 모델 호출 수임을 실측으로 확인했는가 (trace context 전파 검증)
- [ ] `gen_ai.usage.cache_read/write.input_tokens`가 실제 스팬에 방출되는지 raw JSON으로 확인했는가[^genai-bedrock]
- [ ] 단가 테이블이 모델·리전·캐시 read/write 단가를 모두 포함하고, 가격 페이지 변경 시 갱신되는 프로세스가 있는가
- [ ] 테넌트별 application inference profile이 생성되고, 게이트웨이가 프로파일 ARN을 주입하며, base model ID 직접 호출이 차단되어 있는가[^aip-blog]
- [ ] Cost allocation tag가 **활성화**되어 있고, 프로파일 생성 파이프라인에 활성화 단계가 포함되어 있는가 (비소급 주의)[^aip-docs]
- [ ] AgentCore Runtime/harness 리소스에 테넌트 태그가 부착되어 있는가 — 공유 Runtime이라면 세션-테넌트 매핑 대안이 있는가[^agentcore-tagging]
- [ ] per-run 예산 캡이 게이트웨이/하니스에 동기 카운터로 구현되어 있고, 초과 시 graceful termination이 동작하는가
- [ ] 비용 서킷 브레이커의 임계치가 per-run 비용 분포 데이터(p99/p50)에 근거해 설정되어 있는가
- [ ] 월 단위로 트레이스 합산과 청구서를 대사하고 어카운팅 커버리지를 기록하는가
- [ ] chargeback 전환 전에 미귀속 비용의 배부 규칙이 재무와 합의·문서화되어 있는가

## 참고

- [Application inference profiles — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-application-inference-profiles.html) — 프로파일 태깅, cost allocation tag 활성화 절차, 24시간 반영 지연·비소급 제약, Cost Explorer/CUR(classic·2.0) 분해. 확인 시점: 2026-08-31.
- [Manage multi-tenant Amazon Bedrock costs using application inference profiles — AWS ML Blog](https://aws.amazon.com/blogs/machine-learning/manage-multi-tenant-amazon-bedrock-costs-using-application-inference-profiles/) — 멀티테넌트 chargeback 설계 관점의 프로파일 활용.
- [Build a proactive AI cost management system for Amazon Bedrock, Part 2 — AWS ML Blog](https://aws.amazon.com/blogs/machine-learning/build-a-proactive-ai-cost-management-system-for-amazon-bedrock-part-2/) — `create-inference-profile` CLI 예시와 코스트 센터별 프로파일 매핑 패턴.
- [Monitor bedrock-runtime inference using CloudWatch metrics](https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-runtime-metrics.html) — `InputTokenCount`/`OutputTokenCount`/`CacheReadInputTokens`/`CacheWriteInputTokens` 등 런타임 메트릭 정의.
- [How tokens are counted in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) — `InputTokenCount`의 캐시 토큰 제외 규칙, burndown rate(쿼터 소진 배수 ≠ 과금 배수).
- [Improve visibility into Amazon Bedrock usage and performance with Amazon CloudWatch — AWS ML Blog](https://aws.amazon.com/blogs/machine-learning/improve-visibility-into-amazon-bedrock-usage-and-performance-with-amazon-cloudwatch/) — invocation log의 `identity.arn` 기반 Logs Insights 집계 쿼리.
- [Using AWS cost allocation tags](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/cost-alloc-tags.html) — cost allocation tag 일반 메커니즘.
- [Tagging AgentCore resources](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/tagging.html), [AgentCore observability and cost controls](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html) — AgentCore 리소스 태깅과 harness 태그의 managed 리소스 전파.
- [OTel GenAI semantic conventions — gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md), [aws-bedrock.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md) — `gen_ai.usage.*` 속성 정의. 컨벤션 성숙도 검증은 [Part 10 Observability 딥다이브](/10-agentcore/observability-deep-dive)가 정본.
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) — 모델·리전별 토큰 단가, 캐시 read/write 단가.
- [Best practices for controlling access and managing your budgets — AWS Cost Management](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-best-practices.html) — Budgets 데이터 갱신 주기(하루 최대 3회)와 알림 지연 특성.
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system) — 멀티에이전트의 약 15배 토큰 비용, 토큰 사용량의 성능 분산 80% 설명.

[^multi-agent]: Anthropic, ["How we built our multi-agent research system"](https://www.anthropic.com/engineering/built-multi-agent-research-system) (2025). 챗 대비 멀티에이전트 약 15배 토큰, 토큰 사용량이 성능 분산의 80%를 설명한다는 실측의 출처. [Part 1](/01-agent-design/single-vs-multi-agent)에서 상세 인용.
[^budgets]: AWS Cost Management User Guide, ["Best practices for controlling access and managing your budgets"](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-best-practices.html) — 예산 정보는 실시간이 아니라 하루 최대 3회 갱신된다고 명시.
[^genai-spans]: OpenTelemetry GenAI semantic conventions, [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) — `gen_ai.usage.input_tokens`/`output_tokens` 정의.
[^genai-bedrock]: OpenTelemetry GenAI semantic conventions, [aws-bedrock.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/aws-bedrock.md) — `gen_ai.usage.cache_read.input_tokens`/`cache_write.input_tokens` 정의(Recommended 속성).
[^token-counting]: Amazon Bedrock User Guide, ["How tokens are counted in Amazon Bedrock"](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) — `InputTokenCount`는 캐시 토큰 제외, 총 입력 = `InputTokenCount + CacheWriteInputTokens`, 출력 토큰 burndown rate는 쿼터 소진 계산용이라는 정의의 출처.
[^aip-docs]: Amazon Bedrock User Guide, ["Application inference profiles"](https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-application-inference-profiles.html) — 태그 활성화 절차, 최대 24시간 반영 지연, 비소급 제약, Cost Explorer/CUR 분해의 출처.
[^aip-blog]: AWS ML Blog, ["Manage multi-tenant Amazon Bedrock costs using application inference profiles"](https://aws.amazon.com/blogs/machine-learning/manage-multi-tenant-amazon-bedrock-costs-using-application-inference-profiles/) — `TenantID` 등 태그 기반 멀티테넌트 chargeback 패턴.
[^cat-docs]: AWS Billing User Guide, ["Using AWS cost allocation tags"](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/cost-alloc-tags.html).
[^cw-blog]: AWS ML Blog, ["Improve visibility into Amazon Bedrock usage and performance with Amazon CloudWatch"](https://aws.amazon.com/blogs/machine-learning/improve-visibility-into-amazon-bedrock-usage-and-performance-with-amazon-cloudwatch/) — invocation log `identity.arn` 기반 Logs Insights 집계 쿼리의 출처.
[^cw-metrics]: Amazon Bedrock User Guide, ["Monitor bedrock-runtime inference using CloudWatch metrics"](https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-runtime-metrics.html).
[^agentcore-tagging]: Amazon Bedrock AgentCore Developer Guide, ["Tagging AgentCore resources"](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/tagging.html) — Runtime/Browser/Code Interpreter/Gateway 등에 대한 `TagResource` 지원.
[^harness-ops]: Amazon Bedrock AgentCore Developer Guide, ["Observability and cost controls"](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html) — harness 태그가 managed Runtime·endpoint·Memory로 전파된다는 서술의 출처.
