---
title: 동시성 쿼터와 스로틀링
description: Bedrock 온디맨드 쿼터의 토큰 burndown 메커니즘을 해부하고, max_tokens 미설정·멀티에이전트 증폭·재시도 폭풍이 만드는 ThrottlingException을 계측과 완화 경로로 통제한다.
outline: [2, 3]
---

# 동시성 쿼터와 스로틀링

::: tip 이 장에서 얻는 것
- Bedrock 온디맨드 쿼터가 **모델·리전·계정별 RPM/TPM/TPD**로 잡히는 구조와, 왜 실무에서는 대개 TPM이 먼저 걸리는지
- **토큰 burndown 메커니즘**: 요청 시작 시 `총 입력 토큰 + max_tokens`가 쿼터에서 선차감되는 예약 모델 — "요청이 많지 않은데 ThrottlingException"의 가장 흔한 원인
- 출력 토큰 burndown 배수(Claude 4.8은 15x, Sonnet 5·Opus 5는 10x, 4.7 이하는 5x)와 캐시 읽기 토큰의 쿼터 제외
- 멀티에이전트 병렬 호출이 TPM을 곱셈으로 소모하는 증폭 구조와 그 방어선
- `aws service-quotas` + CloudWatch로 사용량 vs 한도를 계측하고 증설을 요청하는 절차
:::

## 왜 문제가 되는가

에이전트 워크로드가 처음 만나는 스케일링 장벽은 컴퓨트가 아니라 **모델 쿼터**다. 전통적 API 스케일링에서는 요청 수(RPS)가 병목이지만, LLM 추론은 요청 하나가 소비하는 자원의 분산이 극단적으로 크다 — 같은 "1 요청"이 500 토큰일 수도, 15만 토큰일 수도 있다. 그래서 Bedrock은 요청 수(RPM)와 별도로 **토큰 단위 쿼터(TPM/TPD)** 를 두고, 공식 문서가 명시하듯 "Model inference in Amazon Bedrock is controlled by quotas on token usage"([Quotas for Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html)) — 추론 통제의 축은 토큰이다.

문제는 이 토큰 쿼터의 소모 방식이 직관과 다르다는 데 있다. 실제 사용한 토큰이 아니라 **사용할 수도 있는 토큰을 요청 시작 시점에 예약**하는 구조이기 때문에, 트래픽 대시보드로는 한가해 보이는 시스템이 `ThrottlingException`을 뱉는다. 여기에 에이전트 특유의 증폭 요인 — 긴 컨텍스트, 멀티턴 루프, 서브에이전트 병렬 스폰([단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent)가 정리한 챗 대비 약 15배 토큰) — 이 곱해지면, 쿼터는 아키텍처 결정과 동시에 소진 시점이 정해지는 고정 예산이 된다.

이 장은 스로틀링의 **메커니즘과 계측·즉시 완화**를 다룬다. 용량 자체를 바꾸는 구조적 선택지(CRIS, Provisioned Throughput, Reserved)는 [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers)가 정본이고, 재시도 대상 에러의 분류는 [신뢰성과 durable execution](/01-agent-design/reliability-durable-execution)의 Bedrock 에러 분류표가 정본이다.

## 핵심 개념

### 쿼터의 스코프: 모델 × 리전 × 계정 × 엔드포인트

Bedrock 온디맨드 쿼터는 계정 단위로 부여되며, **모델별·리전별**로 별도의 RPM/TPM 값이 잡힌다([AWS General Reference — Amazon Bedrock service quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html#limits_bedrock)). 즉 같은 계정이라도 us-east-1의 Sonnet 쿼터와 us-west-2의 Sonnet 쿼터는 독립이고, Sonnet 쿼터가 남아 있어도 Opus 쿼터는 소진될 수 있다. 추가로 두 가지 스코프 변수가 있다:

- **엔드포인트 분리**: `bedrock-runtime`과 `bedrock-mantle` 엔드포인트는 같은 모델이라도 **별도 쿼터로 추적**된다([Quotas for Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html)). mantle 엔드포인트 전용 모델은 입력/출력 토큰 쿼터가 분리되어 있고 burndown이 적용되지 않는다([How tokens are counted](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)).
- **계정별 차등**: 기본 쿼터는 고정값이 아니다. 리전 요인, 결제 이력, 사기성 사용 여부, 증설 승인 이력에 따라 계정마다 다르게 조정될 수 있고([Quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html)), **신규 계정은 감액된 쿼터로 시작**한다([How tokens are counted](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) — TPD 항목). "문서의 기본값"이 아니라 **내 계정의 실제 값**을 `aws service-quotas`로 확인하는 것이 출발점이다(§계측 참고).

TPD(tokens per day)는 기본적으로 `TPM × 24 × 60`으로 설정되지만 신규 계정은 이보다 낮다([같은 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)). TPM만 보고 있으면 하루 후반에 TPD가 먼저 바닥나는 경우를 놓친다.

에이전트 워크로드에서 대개 **TPM이 RPM보다 먼저 걸린다.** 에이전트 요청은 시스템 프롬프트 + 툴 정의 + 누적 히스토리로 요청당 수천~수만 토큰을 실어 나르므로, RPM 한도에 닿기 훨씬 전에 토큰 한도가 포화된다. 뒤에서 볼 예약 메커니즘은 이 격차를 더 벌린다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
>
> 일부 최신 모델은 RPM 쿼터 없이 토큰 쿼터(TPM/TPD)만으로 통제된다는 정리가 있다(내부 운영 노트 기준, 예: Claude Opus 4.7/4.8). 방향성은 공식 문서의 "토큰 사용량으로 추론을 통제한다"는 서술과 일치하지만, 특정 모델에 RPM 쿼터가 있는지 여부는 리전·시점에 따라 다르므로 반드시 해당 리전의 [Service Quotas 콘솔 실측값](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html)으로 확인하라.

### 토큰 burndown: 예약 → 조정 → 정산

`ThrottlingException`을 이해하는 열쇠는 쿼터 차감이 3단계로 일어난다는 것이다([How tokens are counted in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)):

1. **요청 시작 시(예약)**: `총 입력 토큰 + max_tokens`가 TPM/TPD에서 선차감된다. 이 시점에 쿼터가 부족하면 요청은 스로틀된다.
2. **처리 중(조정)**: 실제 생성된 출력 토큰 수에 맞춰 소모량이 주기적으로 조정된다.
3. **요청 종료 시(정산)**: 최종 소모량은 `InputTokenCount + CacheWriteInputTokens + (OutputTokenCount × burndown rate)`로 확정되고, 예약분 중 미사용 토큰은 쿼터로 반환된다.

여기서 두 가지 실무 함의가 나온다.

**첫째, `max_tokens` 미설정(또는 과대 설정)은 쿼터를 태우는 가장 흔한 실수다.** 예약 단계의 차감량은 실제 출력이 아니라 **선언한 상한**을 쓴다. 공식 문서의 시나리오를 그대로 옮기면: 입력 3,000 + 캐시 쓰기 1,000 + 출력 1,000 토큰짜리 요청이 `max_tokens=32,000`이면 시작 시 **36,000 토큰을 예약**하지만, `max_tokens=1,250`으로 조이면 **5,250 토큰**만 예약한다 — 최종 정산은 둘 다 9,000으로 같다([같은 문서, Scenario 1·2](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)). 예약이 크면 같은 TPM 안에서 동시에 띄울 수 있는 요청 수가 그만큼 줄어든다. `max_tokens`를 아예 설정하지 않으면 모델 최대값 수준으로 예약되므로, "트래픽은 적은데 스로틀링이 난다"의 1순위 용의자다. 공식 문서도 "TPM 쿼터에 예상보다 일찍 닿는다면 `max_tokens`를 줄여라"라고 직접 권고한다([같은 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)).

**둘째, 출력 토큰에는 모델별 burndown 배수가 붙는다.** 출력 토큰 1개가 쿼터에서 1개로 차감되는 게 아니다. 공식 수치는([같은 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)):

| 모델 | 출력 토큰 burndown 배수 |
|---|---|
| Anthropic Claude 4.8 | **15x** |
| Anthropic Claude Sonnet 5 / Opus 5 | **10x** |
| Anthropic Claude 4.7 이하 | **5x** |
| OpenAI GPT-5.6 (Sol/Terra/Luna, `bedrock-runtime`) | **10x** |
| 그 외 모델 | 1:1 |

즉 고성능 추론(reasoning) 계열일수록 출력 1토큰이 쿼터 10~15토큰을 태운다. 과금은 실사용 토큰 기준이므로 비용 대시보드에는 이 배수가 보이지 않는다 — 공식 예시로, 5x 모델에서 입력 1,000 + 출력 100 토큰 요청은 **청구는 1,100 토큰, 쿼터 소모는 1,500 토큰**이다([같은 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)). 출력이 긴 워크로드(코드 생성, 장문 리포트)는 명목 TPM의 1/10~1/15만 쓸 수 있다고 봐야 한다.

### 캐시 읽기는 쿼터에서 빠진다

정산 공식에 `CacheReadInputTokens`가 없다는 점에 주목하라. 공식 문서가 명시한다: "`CacheReadInputTokens` don't contribute to this calculation and are not counted toward your quota"([같은 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)). **캐시 읽기 토큰은 TPM burndown에서 제외되고, 캐시 쓰기 토큰은 포함된다.** 따라서 프롬프트 캐싱은 비용 절감 수단인 동시에 **실효 TPM을 늘리는 수단**이다 — 시스템 프롬프트·툴 정의처럼 매 요청 반복되는 수만 토큰이 캐시 히트로 전환되면 그만큼 쿼터 예산이 풀린다. 단, 비용 공식(캐시 읽기 0.1x 과금)과 쿼터 공식(캐시 읽기 완전 제외)은 서로 다른 계산이며, 이 구분과 대시보드 분리는 [캐시 지표와 경제성](/04-caching/cache-metrics-economics)이 정본이다.

### 멀티에이전트는 쿼터를 곱셈으로 소모한다

쿼터는 계정×리전×모델 단위 공유 자원이므로, orchestrator-worker 패턴에서 리드가 서브에이전트 N개를 병렬 스폰하면 **동일 분(minute) 안에 N+1개의 예약이 동시에 걸린다.** 각 서브에이전트가 시스템 프롬프트·툴 정의를 독립 컨텍스트로 복제해 실어 나르므로 예약량도 병렬로 곱해진다 — [단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent)가 인용한 Anthropic 실증(멀티에이전트는 챗 대비 약 15배 토큰)이 쿼터 관점에서는 "같은 TPM으로 감당 가능한 사용자 수가 1/15로 줄어든다"로 번역된다. 여기에 burndown 예약 메커니즘이 겹치면 더 나쁘다: 서브에이전트 각각이 `max_tokens`만큼 선예약하므로, 실제 토큰 소비 이전에 **스폰 순간의 예약 스파이크**만으로 스로틀링이 발생할 수 있다. 멀티에이전트 도입 시 per-run 예산 캡과 스폰 폭 제한이 비용 가드레일일 뿐 아니라 **쿼터 가드레일**이기도 한 이유다.

### 재시도: 분류 먼저, 백오프는 기본

`ThrottlingException`(HTTP 429)은 재시도 가능한 에러다 — 어떤 에러를 재시도하고 어떤 에러를 즉시 실패시킬지의 분류표는 [신뢰성과 durable execution](/01-agent-design/reliability-durable-execution)의 Bedrock 에러 분류표를 그대로 따른다. 이 장의 관심사는 재시도가 쿼터에 미치는 2차 효과다:

- AWS SDK의 **standard 모드**(기본, 최대 3회 시도)는 exponential backoff를 내장하고, **adaptive 모드**는 여기에 클라이언트 측 rate limiting을 더해 스로틀 응답을 관찰하며 송신 속도 자체를 낮춘다([AWS SDKs — Retry behavior](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)). AWS는 adaptive 모드를 실험적 기능으로 표기하며, 여러 클라이언트가 같은 쿼터를 공유할 때는 모든 클라이언트에서 함께 쓰라고 안내한다.
- AWS 공식 트러블슈팅 가이드는 `ThrottlingException` 대응으로 exponential backoff + jitter를 권고한다([re:Post Knowledge Center — Bedrock retry/backoff](https://repost.aws/knowledge-center/bedrock-retry-exponential-backoff-api), [Bedrock throttling 트러블슈팅](https://repost.aws/knowledge-center/bedrock-throttling-error)).
- 백오프 없는 즉시 재시도는 **재시도 폭풍**을 만든다: 429를 받은 클라이언트들이 동시에 재전송하면 예약 스파이크가 반복되어 스로틀링이 자기강화된다. 429 비율과 재시도 횟수가 동반 상승하면 이 패턴이다.

::: warning 미정착 영역
공유 쿼터 앞단의 클라이언트 측 admission control — SDK adaptive 모드에 맡길지, 애플리케이션 레벨 토큰 버킷/세마포어(동시 예약량 기준)를 직접 둘지, 게이트웨이 계층에서 테넌트별로 분배할지 — 는 업계 표준이 없는 영역이다. adaptive 모드는 프로세스 단위로 동작하므로 수평 확장된 워커 플릿 전체의 협조적 제어는 보장하지 못하고, 중앙 게이트웨이는 그 자체가 단일 장애점·지연 요인이 된다. 팀의 배포 토폴로지에 따라 판단이 갈리며, 이 책은 "우선 SDK adaptive + `max_tokens` 최적화로 시작하고, 테넌트 간 공정성 문제가 실측되면 게이트웨이 분배를 검토"하는 순서를 권하지만 이는 실무 관행이지 검증된 표준이 아니다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 트래픽은 적은데 `ThrottlingException` 발생 | `max_tokens`를 실제 출력 분포(p95)에 맞게 명시 설정 | 예약량이 `총 입력 + max_tokens`이므로 가장 큰 단일 개선([burndown 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)) | 너무 조이면 응답 잘림 — p95 + 여유분으로 설정하고 잘림률 계측 |
| 반복되는 대형 프리픽스(시스템 프롬프트·툴 정의) | 프롬프트 캐싱 적용 | 캐시 읽기는 TPM burndown 제외 — 실효 TPM 증가([캐시 지표와 경제성](/04-caching/cache-metrics-economics)) | 캐시 쓰기는 쿼터에 포함; 히트율 관리 필요 |
| 버스트성 트래픽, 단일 리전 쿼터 초과 | Cross-Region Inference(CRIS) 프로파일 | 여러 리전 용량으로 분산([CRIS 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)) — 상세는 [추론 티어](/08-scaling-cost/bedrock-inference-tiers) | 데이터 리전 제약·지연 변동 검토 필요 |
| 정상 상태(steady-state) 사용량이 한도에 근접 | Service Quotas 증설 요청 | 구조적 부족은 재시도로 못 푼다([증설 절차](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-increase.html)) | 승인까지 리드타임; 계정 이력에 따라 반려 가능 |
| 예측 가능한 대규모 고정 부하 | Provisioned/Reserved 티어 검토 | 온디맨드 쿼터 경쟁에서 이탈 — [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers)가 정본 | 약정 비용; 유휴 시간 낭비 |
| 멀티에이전트 도입으로 쿼터 압박 | 스폰 폭·깊이 제한 + per-run 예산 캡 + 서브에이전트 `max_tokens` 개별 설정 | 병렬 예약 스파이크 억제([단일 vs 멀티](/01-agent-design/single-vs-multi-agent)) | 병렬성 축소 = 지연 증가 |
| 여러 워커가 같은 쿼터를 공유 | SDK adaptive retry 모드(전 클라이언트 일괄) | 클라이언트 측 rate limiting으로 협조적 감속([SDK retry](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html)) | 실험적 기능; 일부만 켜면 형평성 왜곡 |
| 지연 무관한 배치 작업이 대화형 트래픽과 쿼터 경쟁 | 배치를 별도 리전/계정/시간대로 격리 | 쿼터는 모델×리전×계정 단위 공유 자원 | 운영 복잡도 증가 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| RPM 여유가 큰데 `ThrottlingException` | `max_tokens` 미설정/과대 → 예약이 TPM 선점 | 코드에서 `max_tokens` 명시 여부 확인; `OutputTokenCount` 실측 분포 vs 설정값 비교 | `max_tokens`를 출력 p95 기준으로 설정 |
| 출력이 긴 워크로드에서 명목 TPM의 소수만 쓰고 스로틀 | 출력 토큰 burndown 배수(5x~15x) 미반영 | 정산 공식으로 재계산: `Input + CacheWrite + Output×배수` | 용량 계획을 배수 반영 실효 TPM으로 재산정; 출력 축소(구조화 출력, 요약 계약) |
| 캐시 히트율을 올렸는데 스로틀링 그대로 | 병목이 캐시 쓰기·출력 토큰 쪽 — 캐시 읽기만 쿼터 제외 | `CacheWriteInputTokens`·`OutputTokenCount` 추이 확인 | TTL/프리픽스 설계로 쓰기 반복 억제; `max_tokens` 재점검 ([캐시 지표](/04-caching/cache-metrics-economics)) |
| 멀티에이전트 런 시작 순간에만 스로틀 스파이크 | 서브에이전트 N개 동시 스폰 = N개 예약 동시 발생 | 스로틀 타임스탬프와 스폰 이벤트 상관 분석 | 스폰 폭 제한, 스태거드(staggered) 스폰, 서브에이전트별 `max_tokens` 축소 |
| 429 이후 오히려 에러율 급등 (재시도 폭풍) | 백오프/jitter 없는 즉시 재시도 | 429 비율과 클라이언트 재시도 횟수 동반 상승 확인 ([신뢰성 장](/01-agent-design/reliability-durable-execution)) | exponential backoff + jitter, adaptive 모드, 동시 실행 상한 |
| 오후·저녁에만 스로틀링 | TPM은 여유인데 TPD 소진 (특히 신규 계정 감액) | Service Quotas에서 TPD 값과 일 누적 토큰 비교 | TPD 증설 요청; 배치 작업 시간 분산 |
| 스테이징에서 되던 부하가 프로덕션 계정에서 스로틀 | 쿼터가 계정별 차등(신규 계정 감액, 이력 기반 조정) | 두 계정의 `aws service-quotas` 실측값 비교 | 프로덕션 계정 기준 사전 증설; 부하 테스트를 프로덕션 쿼터로 수행 |
| 같은 모델인데 엔드포인트 바꾸니 쿼터 거동이 다름 | `bedrock-runtime` vs `bedrock-mantle`은 별도 쿼터·별도 정산(mantle은 burndown 미적용) | 호출 엔드포인트 확인([Quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html)) | 엔드포인트별로 용량 계획 분리 |

## 안티패턴

- ❌ `max_tokens`를 "안전하게" 모델 최대값으로 두거나 아예 생략한다 → ✅ 워크로드별 출력 토큰 p95를 계측해 그 수준 + 여유분으로 명시하고, 잘림률(stop reason)을 함께 모니터링한다.
- ❌ 문서의 기본 쿼터 값으로 용량 계획을 세운다 → ✅ 계정·리전별 실측값(`aws service-quotas list-service-quotas`)을 기준으로 하고, 신규 계정 감액 가능성을 전제한다.
- ❌ 비용 대시보드의 토큰 수로 쿼터 여유를 추정한다 → ✅ 과금(실사용)과 burndown(배수 적용 + 예약)은 다른 공식이다 — 쿼터 대시보드를 별도로 만든다.
- ❌ 스로틀링을 재시도 튜닝만으로 해결하려 한다 → ✅ 재시도는 일시적 스파이크용이다. 정상 상태 부족이면 쿼터 증설 또는 [티어 전환](/08-scaling-cost/bedrock-inference-tiers)이 답이다.
- ❌ 서브에이전트 수를 늘리며 성능 개선만 계측한다 → ✅ 스폰 폭 × 요청당 예약량 = 분당 쿼터 점유를 함께 계측하고, 스폰 폭에 하드 리밋을 둔다.
- ❌ 워커 플릿 일부에만 adaptive retry를 켠다 → ✅ 같은 쿼터를 공유하는 모든 클라이언트에 일괄 적용한다 — 일부만 감속하면 나머지가 쿼터를 독식한다.

## 계측 (SLI)

**핵심 SLI 4종:**

1. **스로틀률**: `InvocationThrottles / Invocations` (`AWS/Bedrock` 네임스페이스, `ModelId` 차원, [runtime metrics](https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-cloudwatch.html)). 0이 아니면 원인 분석 대상, 지속 상승이면 용량 조치 대상.
2. **TPM 사용률**: 분당 `InputTokenCount + CacheWriteInputTokens + OutputTokenCount × burndown 배수` 합계를 쿼터 값으로 나눈 비율. 예약 스파이크는 이 정산 기준 지표에 안 잡히므로, 스로틀률과 함께 봐야 한다.
3. **`max_tokens` 효율**: 설정한 `max_tokens` 대비 실제 `OutputTokenCount`의 비율 분포. 중앙값이 낮을수록 예약 낭비가 크다는 뜻 — 공식 문서가 권하는 CloudWatch 자동 대시보드(Token Counts by Model)로 우선 확인 가능([burndown 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html)).
4. **재시도 건강도**: 429 비율과 클라이언트 재시도 횟수의 상관 — 정의와 해석은 [신뢰성 장의 SLI](/01-agent-design/reliability-durable-execution)를 따른다.

**쿼터 실측값 조회:**

```bash
# 리전의 Bedrock 토큰 쿼터 나열
aws service-quotas list-service-quotas \
  --service-code bedrock --region us-east-1 \
  --query "Quotas[?contains(QuotaName, 'tokens per minute')].{Name:QuotaName, Value:Value, Code:QuotaCode}" \
  --output table

# 특정 쿼터 증설 요청
aws service-quotas request-service-quota-increase \
  --service-code bedrock --quota-code <QUOTA_CODE> \
  --desired-value <VALUE> --region us-east-1
```

([service-quotas CLI 레퍼런스](https://docs.aws.amazon.com/cli/latest/reference/service-quotas/index.html), [Bedrock 쿼터 증설 절차](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-increase.html)). 증설은 AWS 심사를 거치며 계정 이력에 따라 승인 여부·리드타임이 달라진다 — 긴급하면 Support 케이스를 병행하라. CloudWatch의 분당 피크 사용량(정산 기준)과 이 실측 한도를 같은 대시보드에 겹쳐 그려서, "한도의 70% 도달" 같은 선행 알람을 스로틀 발생 전에 울리게 한다.

## 체크리스트

- [ ] 모든 Bedrock 호출 경로에 `max_tokens`가 명시되어 있고, 값이 출력 p95 실측 기반이다
- [ ] 사용 모델의 출력 burndown 배수(15x/10x/5x/1x)를 확인해 실효 TPM으로 용량 계획을 세웠다
- [ ] 계정·리전별 쿼터 실측값을 `aws service-quotas`로 확인했다 (문서 기본값 아님)
- [ ] TPM 대시보드가 쿼터 공식(캐시 읽기 제외, 배수 포함)으로 계산되며, 비용 대시보드와 분리되어 있다
- [ ] TPD를 별도로 모니터링한다 (특히 신규 계정)
- [ ] 스로틀률·TPM 사용률에 선행 알람(예: 한도 70%)이 걸려 있다
- [ ] 재시도에 exponential backoff + jitter가 적용되고, 쿼터 공유 클라이언트 전체에 동일한 retry 모드가 설정되어 있다
- [ ] 멀티에이전트 스폰 폭·깊이에 하드 리밋과 per-run 예산 캡이 있다
- [ ] 반복 프리픽스에 프롬프트 캐싱을 적용해 쿼터 예산을 회수했다 ([캐시 지표](/04-caching/cache-metrics-economics))
- [ ] 정상 상태 부족 시의 에스컬레이션 경로(증설 → CRIS → [티어 전환](/08-scaling-cost/bedrock-inference-tiers))가 문서화되어 있다

## 참고

- [Amazon Bedrock 공식 문서: Quotas for Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html) — 쿼터 스코프, 엔드포인트별 분리, 계정별 차등
- [Amazon Bedrock 공식 문서: How tokens are counted in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) — 예약/조정/정산 3단계, `max_tokens` 시나리오, 모델별 burndown 배수, 캐시 읽기 제외, TPD 기본값
- [Amazon Bedrock 공식 문서: Request an increase for Amazon Bedrock quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-increase.html) — 증설 절차
- [AWS General Reference: Amazon Bedrock service quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html#limits_bedrock) — 모델·리전별 기본 쿼터 표
- [AWS SDKs and Tools: Retry behavior](https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html) — standard/adaptive 모드
- [AWS re:Post Knowledge Center: Bedrock retry/backoff](https://repost.aws/knowledge-center/bedrock-retry-exponential-backoff-api), [Bedrock throttling 트러블슈팅](https://repost.aws/knowledge-center/bedrock-throttling-error)
- [Amazon Bedrock 공식 문서: Monitor Amazon Bedrock with CloudWatch](https://docs.aws.amazon.com/bedrock/latest/userguide/monitoring-cloudwatch.html) — `Invocations`, `InvocationThrottles`, 토큰 카운트 지표
- [Amazon Bedrock 공식 문서: Cross-Region Inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- 책 내부: [신뢰성과 durable execution](/01-agent-design/reliability-durable-execution) · [단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent) · [캐시 지표와 경제성](/04-caching/cache-metrics-economics) · [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers)
