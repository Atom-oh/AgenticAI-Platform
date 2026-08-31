---
title: 신뢰성과 durable execution
description: 장시간 실행 에이전트가 프로세스 중단·부수효과 중복·재시도 폭주에서 살아남도록 멱등성, 체크포인팅, durable execution을 설계한다.
outline: [2, 3]
---

# 신뢰성과 durable execution

::: tip 이 장에서 얻는 것
- 프로세스가 죽어도 "처음부터 다시"가 아니라 실패 지점부터 재개하는 durable execution 모델의 동작 원리(checkpoint + replay)
- LLM 호출의 비결정성이 replay와 충돌하는 이유와, LLM/툴 호출을 step(activity) 경계로 격리하는 설계 원칙
- 재시도해야 할 에러와 즉시 실패해야 할 에러의 분류 기준(Bedrock 예시), 그리고 Temporal · Step Functions · Lambda durable functions 간 선택 기준
:::

## 왜 문제가 되는가

동기식 챗봇은 요청-응답이 수 초 안에 끝나므로 실패하면 사용자가 다시 물어보면 된다. 하지만 에이전트가 "월말 정산 리포트를 만들어 관련 팀에 메일로 보내라" 같은 작업을 받으면 이야기가 달라진다. LLM 호출 수십 번, 툴 호출 수십 번이 수 분~수 시간에 걸쳐 이어지고, 그 사이 어디서든 프로세스가 죽을 수 있다 — 배포로 인한 컨테이너 재시작, OOM, spot 회수, 다운스트림 타임아웃.

이때 두 가지 질문이 동시에 터진다.

1. **재개(resume)**: 30단계 중 27단계에서 죽었는데 처음부터 다시 도는가? LLM 토큰 비용과 시간이 그대로 이중 과금된다.
2. **부수효과(side effect) 중복**: 이미 발송된 이메일, 이미 실행된 결제가 재실행 시 한 번 더 나가는가? 이것은 비용 문제가 아니라 사고다.

단계 수가 늘수록 문제는 기하급수적으로 악화된다. 단계당 성공률이 p일 때 n단계 파이프라인의 완주 확률은 p^n으로 떨어진다 — 이 수치 모델과 상세 표는 [Part 3 평가 하니스](/03-accuracy-eval/eval-harness)가 정본이다. 여기서 중요한 것은 체크포인트가 이 식을 바꾼다는 점이다: 실패 시 처음부터가 아니라 마지막 체크포인트부터 재시도하면, 전체 완주 확률이 "n단계를 한 번에 통과할 확률"이 아니라 "각 단계를 (재시도 포함해) 결국 통과할 확률"의 곱으로 바뀐다. [Part 0의 전환 임계치 표](/00-intro/six-pain-points#전환-임계치-표)가 "파이프라인 10단계 이상 → durable execution 검토"를 제시하는 근거가 이것이다.

## 핵심 개념

### 멱등성 (idempotency)

분산 시스템에서 재시도는 필연이고, 재시도가 있는 곳에 중복 실행이 있다. 멱등성은 "같은 요청이 두 번 도착해도 효과는 한 번"을 보장하는 성질이다. 실무 패턴은 idempotency key다: 호출자가 요청마다 고유 키를 부여하고, 수신 측이 키를 저장해 재도착 시 이전 결과를 그대로 반환한다. Stripe API가 `Idempotency-Key` 헤더로 이 패턴을 표준화했고([Stripe — Idempotent requests](https://docs.stripe.com/api/idempotent_requests)), AWS에서는 Powertools for AWS Lambda의 idempotency utility가 DynamoDB를 저장소로 같은 패턴을 제공한다([Powertools — Idempotency](https://docs.powertools.aws.dev/lambda/python/latest/utilities/idempotency/)).

에이전트 설계에서의 함의: **부수효과를 내는 모든 툴은 멱등하거나, 멱등성 키를 받게 만들어라.** `send_email(to, body)`가 아니라 `send_email(to, body, request_id)`다. 키는 에이전트 run ID + step 번호처럼 재실행 시에도 동일하게 재생성되는 값이어야 한다. LLM이 키를 생성하게 두면 안 된다 — 재시도 시 LLM이 다른 키를 만들면 멱등성이 깨진다. 키 부여는 하니스(코드)의 책임이다.

### 체크포인팅

체크포인팅은 각 단계의 완료 여부와 결과를 내구성 있는 저장소에 기록하는 것이다. 순진한 구현은 "대화 히스토리를 DB에 저장"이지만, 이것만으로는 부족하다. 히스토리에는 "이메일을 보냈다"는 assistant 메시지가 있어도, 그 메시지가 기록되기 직전에 죽었다면 이메일은 나갔는데 기록이 없다(또는 그 반대). 체크포인트는 **부수효과 실행과 결과 기록이 하나의 단위로 묶인 경계**여야 하고, 그 경계를 넘는 재실행은 멱등성 키가 막아야 한다. "저장된 상태" ≠ "재개 가능한 상태"다.

### Durable execution 실행 모델

durable execution은 체크포인팅을 애플리케이션 코드가 아니라 실행 엔진이 담당하는 모델이다. 공통 메커니즘은 **checkpoint + replay**: 각 단계(step/activity)의 결과를 엔진이 자동으로 영속화하고, 실패 후 재개 시 코드를 처음부터 다시 실행하되 이미 완료된 단계는 실제 실행 대신 저장된 결과를 반환하며 건너뛴다. 개발자는 일반 언어로 순차 코드를 쓰고, 엔진이 그것을 재개 가능하게 만든다.

대표 구현 세 가지:

- **Temporal** — 오픈소스 durable execution 엔진. Workflow 코드의 모든 이벤트를 Event History에 기록하고 replay로 상태를 복원한다. Workflow 코드에는 결정성(determinism) 제약이 걸리고, 비결정적 작업은 Activity로 분리한다([Temporal — Workflow definition, deterministic constraints](https://docs.temporal.io/workflow-definition#deterministic-constraints)).
- **AWS Step Functions** — 상태 머신을 ASL(JSON)로 선언하는 관리형 오케스트레이터. Standard workflow는 최대 1년까지 실행할 수 있다([Choosing workflow type](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html)). `.waitForTaskToken` 통합 패턴은 task token을 외부로 넘기고 `SendTaskSuccess`/`SendTaskFailure` 호출이 올 때까지 워크플로우를 일시정지한다 — 사람 승인, 서드파티 연동, 레거시 시스템 대기에 쓰인다([Service integration patterns](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html)). 이 패턴을 HITL 승인 게이트로 쓰는 구체적 설계는 [Part 9 — HITL과 감사](/09-authorization/hitl-audit)에서 다뤘다.
- **AWS Lambda durable functions** — Lambda의 실행 모델을 확장해 단일 함수 안에서 durable execution을 제공하는 기능. durable execution SDK로 핸들러를 감싸면 `context.step()`(비즈니스 로직 + 자동 재시도 + 결과 체크포인트), `context.wait()`(컴퓨트 과금 없는 일시정지), `context.waitForCallback()`(외부 시스템·사람의 응답 대기, `SendDurableExecutionCallbackSuccess`/`Failure`로 재개) 같은 durable operation을 쓸 수 있고, 하나의 durable execution이 중단에도 불구하고 최대 1년까지 진행을 유지한다. 실패 시 핸들러를 처음부터 replay하되 완료된 durable operation은 저장된 결과로 건너뛴다([AWS Lambda durable functions](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html), [AWS Compute Blog — Building fault-tolerant applications with AWS Lambda durable functions](https://aws.amazon.com/blogs/compute/building-fault-tolerant-long-running-application-with-aws-lambda-durable-functions/)).

세 구현 모두 wait/callback 상태에서는 컴퓨트가 돌지 않는다. "사람의 승인을 3일 기다리는 에이전트"를 유휴 컨테이너 없이 구현할 수 있는 이유다.

### 에이전트 루프와 replay: LLM 호출은 반드시 step 안으로

durable execution의 replay는 "코드를 다시 실행하면 같은 결정 경로를 밟는다"는 전제 위에 서 있다. 그런데 LLM 호출은 본질적으로 비결정적이다 — 같은 프롬프트에 다른 응답이 올 수 있고, temperature 0이어도 재현이 보장되지 않는다. replay 중에 LLM을 실제로 다시 호출하면 지난 실행과 다른 툴을 고를 수 있고, 그 순간 저장된 히스토리와 코드 실행 경로가 어긋나 replay가 깨진다.

따라서 설계 원칙은 하나다: **LLM 호출과 툴 호출은 전부 step(Temporal에서는 Activity) 경계 안에 넣고, 그 결과를 체크포인트로 저장해 replay 시 재사용한다.** Temporal은 이것을 결정성 제약으로 강제한다 — Workflow 코드에서 네트워크 호출·난수·시스템 시계 같은 비결정적 연산을 금지하고, 그런 작업은 Activity로 분리해 결과를 Event History에 기록하고 replay 시 저장된 값을 반환한다([Temporal — deterministic constraints](https://docs.temporal.io/workflow-definition#deterministic-constraints), [Activity definition](https://docs.temporal.io/activity-definition)). Lambda durable functions의 replay 모델도 동일한 요구를 한다: 핸들러가 처음부터 재실행되므로, 비결정적 연산이 durable operation 밖에 있으면 replay마다 결과가 달라진다.

이 원칙의 부수 이득이 크다. replay는 저장된 LLM 응답을 재사용하므로 **재개 시 토큰 재과금이 없고**, run의 전체 궤적(어떤 프롬프트에 어떤 응답, 어떤 툴 호출)이 체크포인트 저장소에 자동으로 남아 감사·디버깅·평가 데이터셋 추출의 원천이 된다.

::: warning 미정착 영역
에이전트 프레임워크 자체의 체크포인터(예: LangGraph의 persistence/checkpointer — [LangGraph — Persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/))와 durable execution 엔진의 체크포인트를 어떻게 역할 분담할지는 아직 업계 합의가 없다. 프레임워크 체크포인터만으로 부수효과 멱등성까지 커버하려는 시도, 반대로 에이전트 루프 전체를 Temporal workflow로 옮기는 시도가 공존한다. 이중 체크포인팅(프레임워크 + 엔진)은 상태 불일치의 새 원인이 되므로, 한쪽을 source of truth로 정하고 다른 쪽은 캐시로 취급하는 것이 현재로서는 안전한 기본값이다.
:::

### 재시도 정책: 에러를 분류하라

모든 에러를 재시도하는 것은 재시도하지 않는 것만큼 나쁘다. 분류 기준은 "다시 시도하면 결과가 달라질 수 있는가"다. Bedrock Runtime의 Converse API 에러가 좋은 교보재다([Converse API — Errors](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)):

| 에러 | HTTP | 분류 | 이유 |
|---|---|---|---|
| `ThrottlingException` | 429 | 재시도 (backoff + jitter) | 쿼터 초과는 일시적 — 시간이 지나면 성공한다 |
| `ServiceUnavailableException` | 503 | 재시도 | 서비스 측 일시 장애 |
| `InternalServerException` | 500 | 재시도 | 서버 오류, 일시적일 수 있음 |
| `ModelTimeoutException` | 408 | 재시도 (횟수 제한) | 타임아웃 — 단, 반복되면 입력 크기 문제 의심 |
| `ValidationException` | 400 | **즉시 실패** | 요청 자체가 잘못됨 — 같은 요청은 백만 번 보내도 400 |
| `AccessDeniedException` | 403 | **즉시 실패** | 권한 문제는 재시도가 아니라 IAM 수정으로 푼다 |
| `ResourceNotFoundException` | 404 | **즉시 실패** | 존재하지 않는 리소스는 기다려도 생기지 않는다 |

AWS 공식 가이드는 `ThrottlingException` 대응으로 exponential backoff + jitter를 권고하고([AWS re:Post Knowledge Center — Bedrock retry/backoff](https://repost.aws/knowledge-center/bedrock-retry-exponential-backoff-api), [Bedrock ThrottlingException 트러블슈팅](https://repost.aws/knowledge-center/bedrock-throttling-error)), AWS SDK는 standard(기본, 최대 3회 시도) / adaptive 재시도 모드를 내장한다([AWS SDKs — Retry behavior](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)). durable execution 엔진의 step 레벨 재시도 정책에 이 분류를 반영하라 — Temporal의 Activity retry policy나 Lambda durable functions의 step 재시도 설정 모두 non-retryable 에러 타입 지정을 지원하므로, `ValidationException`류를 명시적으로 non-retryable로 등록해야 한다. 스로틀링 자체의 근본 대응(쿼터·CRIS·프로비저닝)은 [Part 8 — 동시성 쿼터와 스로틀링](/08-scaling-cost/concurrency-quotas-throttling)에서 다룬다.

한 가지 뉘앙스: **LLM 출력이 스키마 검증에 실패한 경우**는 위 표의 `ValidationException`과 다르다. 요청이 아니라 응답이 잘못된 것이고, LLM은 비결정적이므로 재시도(가능하면 검증 오류를 피드백에 포함해서)가 정당하다. "결정적 실패는 즉시 중단, 비결정적 실패는 제한된 재시도"로 기억하라.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 파이프라인 10단계 미만, 부수효과 없음(읽기 전용) | 단순 재시도 + 상위 레벨 재실행 | durable execution의 운영 비용이 이득을 초과 | 실패 시 토큰 비용 이중 지출 감수 |
| 10단계 미만이지만 결제·발송 등 부수효과 있음 | idempotency key(Powertools 등) + 재시도 | 중복 실행만 막으면 재실행이 안전해짐 | 키 저장소(DynamoDB 등) 운영 필요 |
| 10단계 이상 또는 수 시간 이상 실행 | durable execution 도입 | p^n 붕괴를 체크포인트 단위 재시도로 완화 | 코드에 step 경계·결정성 규율 필요 |
| AWS serverless 스택, 오케스트레이션 로직이 코드 중심 | Lambda durable functions | 기존 Lambda 프로그래밍 모델 안에서 checkpoint/replay·wait·callback 제공 | Lambda 실행 환경·언어 SDK 지원 범위에 종속 |
| 워크플로우를 선언적으로 관리, 서비스 통합 다수 | Step Functions (Standard) | ASL 선언 + 최대 1년 실행 + 220+ 서비스 통합 | 복잡한 분기 로직이 ASL로 장황해짐 |
| 멀티클라우드/온프렘, 복잡한 코드 중심 워크플로우 | Temporal | 언어 네이티브 코드 + 자가 호스팅 가능 + 성숙한 replay 모델 | 클러스터(또는 Temporal Cloud) 운영 비용, 결정성 제약 학습 |
| 사람 승인·외부 시스템 응답 대기 | `.waitForTaskToken` 또는 `waitForCallback` | 대기 중 컴퓨트 과금 없음, 최대 1년 대기 | 콜백 유실 대비 heartbeat/timeout 설계 필수 |
| 짧고 빈번한 이벤트 처리(초 단위) | durable execution 불필요 — 멱등 소비자 패턴 | 체크포인트 오버헤드가 처리 시간을 압도 | at-least-once 전달의 중복은 여전히 멱등성으로 방어 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 재시작 후 같은 이메일이 두 번 발송됨 | 부수효과가 체크포인트 경계 밖에 있거나 idempotency key 부재 | 발송 로그에서 동일 run ID의 중복 요청 확인 | 부수효과를 step으로 감싸고, run ID + step 번호 기반 idempotency key 부여 |
| replay 시 non-determinism 에러로 워크플로우 실패 | workflow(결정적 코드) 안에서 LLM 호출·난수·현재 시각 사용 | replay 테스트(Temporal replayer, LocalDurableTestRunner)로 재현 | 비결정적 연산 전부를 Activity/step으로 이동 |
| 재시도가 스로틀링을 오히려 악화(재시도 폭풍) | 429에 backoff/jitter 없는 즉시 재시도 | 429 비율과 클라이언트 재시도 횟수의 동반 상승 확인 | exponential backoff + jitter, SDK adaptive 모드, 동시 실행 상한 |
| 같은 에러로 재시도 한도까지 소진 후 실패 | `ValidationException`류 결정적 에러를 재시도 대상에 포함 | step 재시도 이력에서 동일 에러 코드 반복 확인 | 에러 분류표 기반 non-retryable 타입 명시 |
| 재개 후 에이전트가 이전과 다른 경로로 폭주 | LLM 응답을 체크포인트에 저장하지 않고 재개 시 재호출 | 재개 전후 trajectory diff — 재개 지점 이후 툴 선택이 달라짐 | LLM 호출을 step화하여 응답 자체를 영속화 |
| 승인 대기 워크플로우가 영원히 안 끝남 | 콜백(task token) 유실 + heartbeat/timeout 미설정 | 실행 이력에서 wait 상태로 수일간 정지 확인 | heartbeat timeout 설정, 토큰-비즈니스 ID 매핑 저장, 만료 시 escalation 분기 |
| 체크포인트 저장소 비용·지연 급증 | 매 step마다 전체 대화 히스토리를 통째로 저장 | 체크포인트 크기가 step 수에 따라 선형 이상으로 증가 | step 결과(델타)만 저장, 대용량 페이로드는 S3 오프로드 + 참조 저장 |

## 안티패턴

- ❌ 에이전트 루프 전체(LLM 호출 포함)를 workflow의 결정적 코드 안에 작성한다 → ✅ LLM·툴 호출은 전부 step/Activity 경계 안에 넣고 결과를 영속화한다. replay는 저장된 결과를 재사용해야 한다.
- ❌ 모든 예외를 잡아 일괄 재시도한다 → ✅ 재시도 가능(429·5xx·타임아웃)과 즉시 실패(400·403·404)를 분류하고, non-retryable 타입을 재시도 정책에 명시한다.
- ❌ "대화 히스토리를 DB에 저장했으니 재개 가능하다"고 간주한다 → ✅ 부수효과 완료 여부가 히스토리와 원자적으로 묶여 있는지, 재실행 시 멱등성이 지켜지는지까지 확인한다.
- ❌ 2~3단계짜리 파이프라인에 Temporal 클러스터부터 도입한다 → ✅ [전환 임계치](/00-intro/six-pain-points#전환-임계치-표)(10단계 이상)를 넘기 전에는 idempotency key + 단순 재시도로 충분하다.
- ❌ LLM에게 idempotency key나 승인 토큰을 생성·전달시킨다 → ✅ 키와 토큰은 하니스(코드)가 결정적으로 생성·관리한다. LLM 재호출 시 값이 바뀌면 안 되는 것은 LLM 손에 쥐여주지 않는다.

## 계측 (SLI)

- **run 완주율**: 시작된 run 대비 최종 성공 비율. 단계 수별로 분해하면 p^n 붕괴가 시작되는 지점이 보인다.
- **재개 성공률**: 중단 후 재개된 run 중 처음부터 재실행 없이 완주한 비율. durable execution 도입 효과를 직접 측정하는 지표다.
- **중복 부수효과 발생률**: idempotency key 충돌(= 중복 요청이 차단된 횟수) 카운트. 0이 아니라는 것 자체가 정상이다 — 재시도가 실제로 일어나고 방어가 작동한다는 뜻. 갑자기 치솟으면 재시도 폭풍을 의심하라.
- **재시도 분포**: step별 재시도 횟수 히스토그램과 에러 코드별 분해. non-retryable 에러가 재시도 이력에 나타나면 분류 누락이다.
- **replay 실패율**: non-determinism 에러로 실패한 replay 비율. 0이어야 정상 — 0이 아니면 workflow 코드에 비결정적 연산이 새어 들어간 것이다.
- **wait 체류 시간**: callback 대기 상태의 p50/p99. p99가 timeout에 근접하면 escalation 경로가 실제로 작동하는지 점검하라.

## 체크리스트

- [ ] 부수효과를 내는 모든 툴이 멱등하거나 idempotency key를 받는다
- [ ] idempotency key는 LLM이 아니라 하니스가 run ID + step 번호로 결정적으로 생성한다
- [ ] LLM 호출과 툴 호출이 전부 step/Activity 경계 안에 있고, 결과가 체크포인트로 영속화된다
- [ ] 재시도 정책에 non-retryable 에러 타입(`ValidationException`, `AccessDeniedException` 등)이 명시되어 있다
- [ ] 재시도에 exponential backoff + jitter가 적용되어 있다
- [ ] replay 테스트가 CI에 있다(Temporal replayer, Lambda durable functions의 로컬 테스트 러너 등)
- [ ] callback 대기에 heartbeat/timeout과 만료 시 escalation 분기가 설계되어 있다
- [ ] 파이프라인 단계 수를 계측하고 있고, 10단계 임계치 초과 시 durable execution 검토가 프로세스화되어 있다
- [ ] 위 SLI 중 최소한 run 완주율·재개 성공률·중복 부수효과 발생률을 대시보드에서 보고 있다

## 참고

- [AWS Lambda durable functions — 개발자 가이드](https://docs.aws.amazon.com/lambda/latest/dg/durable-functions.html)
- [Building fault-tolerant applications with AWS Lambda durable functions — AWS Compute Blog](https://aws.amazon.com/blogs/compute/building-fault-tolerant-long-running-application-with-aws-lambda-durable-functions/)
- [AWS Step Functions — Service integration patterns (`.waitForTaskToken`)](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html)
- [AWS Step Functions — Choosing workflow type (Standard 최대 1년)](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html)
- [Temporal — Workflow definition과 deterministic constraints](https://docs.temporal.io/workflow-definition#deterministic-constraints)
- [Temporal — Activity definition](https://docs.temporal.io/activity-definition)
- [Amazon Bedrock Runtime — Converse API Errors](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- [AWS re:Post Knowledge Center — Bedrock retry logic과 exponential backoff](https://repost.aws/knowledge-center/bedrock-retry-exponential-backoff-api)
- [AWS SDKs and Tools — Retry behavior](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)
- [Powertools for AWS Lambda — Idempotency utility](https://docs.powertools.aws.dev/lambda/python/latest/utilities/idempotency/)
- [Stripe API — Idempotent requests](https://docs.stripe.com/api/idempotent_requests)
- [LangGraph — Persistence (checkpointer)](https://langchain-ai.github.io/langgraph/concepts/persistence/) — 비공식(벤더 문서), durable execution 엔진과의 역할 분담은 미정착
- 관련 장: [Part 9 — HITL과 감사](/09-authorization/hitl-audit) · [Part 3 — 평가 하니스](/03-accuracy-eval/eval-harness) · [Part 0 — 6대 통증점(전환 임계치 표)](/00-intro/six-pain-points) · [Part 8 — 동시성 쿼터와 스로틀링](/08-scaling-cost/concurrency-quotas-throttling)
