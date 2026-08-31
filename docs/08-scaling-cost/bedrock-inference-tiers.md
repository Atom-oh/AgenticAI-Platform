---
title: Bedrock 추론 티어
description: 온디맨드·CRIS·서비스 티어(Priority/Flex/Reserved)·Provisioned Throughput·Batch를 트래픽 패턴별로 선택하는 기준을 정리한다.
outline: [2, 3]
---

# Bedrock 추론 티어

::: tip 이 장에서 얻는 것
- 온디맨드, Cross-Region Inference(CRIS), 서비스 티어(Standard/Priority/Flex/Reserved), Provisioned Throughput, Batch inference — 6가지 용량 확보 수단의 실제 차이
- 트래픽 패턴(불규칙 소량 / 일정 대량 / 배치성 / 지연 민감)별 결정 표
- Global CRIS의 데이터 레지던시 트레이드오프와 한국 금융권에서의 제약
- `ResolvedServiceTier` 등 티어 선택을 검증하는 계측 포인트
:::

이 챕터는 [동시성 쿼터와 스로틀링](/08-scaling-cost/concurrency-quotas-throttling)과 역할을 나눈다. 그쪽이 "쿼터가 어떻게 소진되고 스로틀링이 어떻게 발생하는가"를 다룬다면, 이 챕터는 **스로틀링을 벗어나기 위해 위로 올라갈 수 있는 티어 선택지**를 다룬다. 429를 재시도 로직으로 버티는 것은 하한 대응이고, 여기서 다루는 것은 상한을 옮기는 결정이다.

## 왜 문제가 되는가

에이전트 워크로드는 전통적 API 트래픽보다 토큰 소비가 훨씬 공격적이다. 단일 사용자 요청이 수십 번의 LLM 호출(계획 → 도구 호출 → 결과 해석 → 재계획)로 증폭되고, 각 호출은 이전 컨텍스트를 누적해 입력 토큰이 선형 이상으로 늘어난다. 온디맨드 단일 리전 쿼터만으로 운영하면 다음 세 가지가 동시에 터진다.

1. **스로틀링 절벽**: TPM/RPM 쿼터 초과 시 `ThrottlingException`이 발생하고, 에이전트 루프 중간에서 발생하면 부분 완료 상태의 복구 비용이 크다.
2. **비용 불투명성**: 같은 모델이라도 어떤 티어로 호출했는지에 따라 토큰 단가가 최대 3.5배(Flex 50% 할인 ↔ Priority 75% 프리미엄) 차이 난다. 티어 전략 없이는 비용 최적화 여지를 통째로 버리는 것이다.
3. **컴플라이언스 충돌**: 처리량을 위해 Global CRIS를 켜는 순간 요청이 전 세계 상용 리전으로 라우팅될 수 있다. 데이터 레지던시 요건이 있는 조직에서는 처리량 문제와 컴플라이언스 문제가 서로 반대 방향으로 당긴다.

용량 확보 수단이 온디맨드 하나였던 시절과 달리, 2025년 말 기준 Bedrock은 서로 직교하는 두 축 — **라우팅 축**(단일 리전 / 지리 CRIS / Global CRIS)과 **우선순위 축**(Flex / Standard / Priority / Reserved) — 에 더해 별도 상품인 Provisioned Throughput과 Batch inference까지 갖추고 있다. 이 축들을 혼동하면 잘못된 상품을 사게 된다.

## 핵심 개념

### 라우팅 축: Cross-Region Inference (CRIS)

CRIS는 추론 요청을 여러 리전의 용량 풀로 분산 라우팅하는 메커니즘이다. 모델 ID 대신 **inference profile ID**를 지정하는 것만으로 활성화되며, **라우팅 자체에 추가 과금은 없다** — 과금은 요청을 발신한 source region 기준으로 계산된다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)).

- **지리(Geographic) 프로파일**: `us.`, `eu.`, `apac.` 접두사. 요청이 해당 지리 경계 안의 리전으로만 라우팅된다. 이 책의 demo 스택이 실제로 사용하는 것이 apac 지리 프로파일(`apac.anthropic.claude-sonnet-4-20250514-v1:0`, `demo/builder-harness/create-harness.json`)이다 — 서울(ap-northeast-2)에서 발신하되 APAC 리전 풀의 용량을 쓴다.
- **글로벌(Global) 프로파일**: `global.` 접두사. 지원되는 **모든 상용 리전**으로 라우팅될 수 있다. 지리 프로파일 대비 입력·출력 토큰 단가가 **약 10% 저렴**하다 — AWS가 라우팅 유연성으로 얻는 용량 효율을 가격에 반영한 것이다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)). CloudWatch/CloudTrail 로그는 계속 source region에 기록된다.

::: warning 데이터 레지던시 트레이드오프
Global 프로파일 사용 시 추론 처리가 **어느 상용 리전에서 일어날지 지정할 수 없다**. AWS 공식 문서도 "데이터 레지던시·컴플라이언스 요건이 있는 조직은 Global CRIS가 컴플라이언스 프레임워크에 맞는지 평가해야 한다"고 명시한다. 한국 금융권처럼 국외 처리 이슈가 있는 환경에서는 **지리 프로파일 고정 또는 리전 격리**를 우선 검토하라 — 상세한 아키텍처 옵션은 [하이브리드 아키텍처](/12-security-korea/hybrid-architecture)(작성 중)에서 다룬다.

기술적으로 Global CRIS를 차단하려면 `"aws:RequestedRegion": "unspecified"` 조건 + `inference-profile/global.*` ARN 매칭의 명시적 Deny SCP를 쓴다. 주의: Global CRIS 요청에서 `aws:RequestedRegion`은 목적지 리전이 아니라 `global`/`unspecified`로 설정되므로, 기존의 리전명 기반 Deny SCP는 기대대로 동작하지 않는다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)).
:::

### 우선순위 축: 서비스 티어 (Service Tiers)

2025년 11월 도입된 서비스 티어는 **같은 온디맨드 호출의 처리 우선순위와 단가를 요청 단위로 선택**하는 기능이다([발표](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-bedrock-priority-flex-inference-service-tiers)). Runtime API 호출 시 `service_tier` 파라미터에 `"reserved" | "priority" | "default" | "flex"`를 지정한다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)).

| 티어 | 단가 (Standard 대비) | 특성 | 용도 |
|---|---|---|---|
| **Priority** | **+75% 프리미엄** | Standard/Flex보다 우선 처리, 대부분 모델에서 최대 25% 더 나은 OTPS(output tokens per second). 사전 예약 불필요 | 고객 대면 미션 크리티컬, 지연 민감 |
| **Standard** (`default`) | 기준 | `service_tier` 미지정 시 기본값 | 일반 워크로드 |
| **Flex** | **−50% 할인** | 트래픽 피크 시 Standard 뒤로 밀려 지연 증가 허용 | 모델 평가, 요약, 라벨링, 멀티스텝 에이전트 백그라운드 작업 |
| **Reserved** | 1K TPM당 고정 단가, 월 청구 | 별도 용량 풀 예약, 99.5% 가용성 목표 | 다운타임 불허 워크로드 |

단가 관계(Priority +75%, Flex −50%)는 [Bedrock 가격 페이지](https://aws.amazon.com/bedrock/pricing/)에 명시돼 있다. 중요한 운영 특성: **on-demand 쿼터는 priority/default/flex 세 티어가 공유**하고, reserved 용량만 별도 풀이다. 즉 Priority는 "쿼터를 늘려주는" 것이 아니라 "같은 쿼터 안에서 내 요청을 앞줄에 세우는" 것이다.

::: warning 미정착 영역
서비스 티어의 모델 커버리지는 빠르게 변하는 중이다. 2025년 11월 발표 시점의 Priority/Flex 지원 모델은 OpenAI(gpt-oss), DeepSeek V3.1, Qwen3, Nova Pro/Premier 중심이었고, Anthropic Claude 계열의 티어별 지원 여부는 모델별로 다르다 — 도입 전 반드시 [Models at a glance](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html)에서 대상 모델의 지원 티어를 확인하라.
:::

### Reserved Tier

2025년 11월 발표된 예약 용량 상품([발표](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-bedrock-reserved-service-tier))으로, [공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html) 기준 특성은 다음과 같다.

- **최소 예약 단위**: 입력 100,000 TPM / 출력 10,000 TPM. 입력·출력 용량을 워크로드에 맞게 독립적으로 배분할 수 있다.
- **가용성 목표**: 모델 응답 기준 99.5% uptime 타깃.
- **약정**: 1개월 또는 3개월. 1K TPM당 고정 단가로 월 청구. 삭제 전까지 과금이 계속되며, 삭제는 AWS 계정 매니저를 통한다.
- **오버플로**: 예약 용량 초과분은 자동으로 Standard 티어로 흘러넘친다 — 예약량이 하드 캡이 아니다.
- **접근**: AWS 계정 팀 컨택 필요(셀프서비스 아님).
- **사이징 주의**: TPM 소비량에 `InputTokenCount`뿐 아니라 **`CacheWriteInputTokens`도 포함**된다. Prompt caching을 쓴다면 CloudWatch에서 두 지표를 합산해 예약량을 산정해야 한다.

2026년 1월부터 Claude Opus 4.5, Haiku 4.5에서도 사용 가능하다([발표](https://aws.amazon.com/about-aws/whats-new/2026/01/amazon-bedrock-reserved-tier-claude-opus-haiku)).

### Provisioned Throughput

서비스 티어·추론 프로파일과 **무관한 별도 상품**이다. **모델 유닛(MU)** 단위로 고정 처리량을 구매하고 **시간당** 과금되며, 약정은 무약정 / 1개월 / 6개월 — 길수록 시간당 단가가 할인된다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/prov-throughput.html)). 커스텀 모델(fine-tuned)을 서빙하려면 Provisioned Throughput 구매가 필수라는 점이 실무에서 가장 흔한 진입 경로다. MU당 처리량 스펙과 단가는 공개 문서에 없고 계정 매니저 문의 사항이다.

Reserved Tier와의 관계: 둘 다 "예약 용량"이지만 Provisioned Throughput은 MU 단위·시간당 과금·커스텀 모델 지원의 구세대 상품, Reserved Tier는 TPM 단위·월 청구·Standard 오버플로가 있는 신상품이다. 신규 설계에서 베이스 모델만 쓴다면 Reserved Tier가 사이징 단위(TPM)가 실제 쿼터 체계와 일치해 다루기 쉽다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: "신규 워크로드에는 Provisioned Throughput보다 Reserved Tier가 권장된다"는 식의 공식 포지셔닝 문서는 아직 없다. 위 비교는 두 상품의 공식 스펙에서 도출한 이 책의 해석이다.

### Batch Inference

지연이 전혀 중요하지 않은 대량 작업(평가 데이터셋 생성, 백필, 대량 분류)을 위한 비동기 상품. 온디맨드 대비 **50% 할인**이며([가격 페이지](https://aws.amazon.com/bedrock/pricing/)), S3 입출력 기반으로 동작하고 별도 쿼터를 쓴다. Global CRIS도 batch inference를 지원한다.

## 결정 표

| 트래픽 패턴 | 1순위 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 불규칙 소량 (PoC, 내부 도구) | 온디맨드 + 지리 CRIS | 약정 없음, 라우팅 무과금으로 스로틀링 헤드룸 확보 | 피크 시 429 가능 — 재시도 필수 |
| 일정 대량 + 레지던시 무관 | Global CRIS (+필요 시 Priority) | 지리 대비 ~10% 저렴 + 최대 용량 풀 | 처리 리전 지정 불가 |
| 일정 대량 + 레지던시 요건 (한국 금융권 등) | 지리 CRIS 고정 + Reserved Tier | 지리 경계 유지하며 99.5% 목표의 전용 풀 | 최소 100K/10K TPM, 1~3개월 약정, 계정 팀 컨택 |
| 지연 민감 스파이크 (고객 대면 에이전트) | Priority 티어 | 예약 없이 요청 단위로 앞줄 — 스파이크에만 프리미엄 지불 | +75% 단가, on-demand 쿼터는 공유 |
| 지연 둔감 백그라운드 (평가, 요약, 에이전트 서브태스크) | Flex 티어 | −50% 즉시 절감, 코드 변경은 파라미터 하나 | 피크 시 지연 증가 |
| 완전 배치성 (백필, 데이터셋 생성) | Batch inference | −50% + 별도 쿼터로 온라인 트래픽과 격리 | 비동기, S3 파이프라인 필요 |
| 커스텀(fine-tuned) 모델 서빙 | Provisioned Throughput | 유일한 선택지 (필수) | MU 스펙 비공개, 시간당 과금 |

실무 패턴은 조합이다: 같은 에이전트 플랫폼 안에서 사용자 대면 경로는 `priority`, 도구 결과 요약 같은 내부 경로는 `flex`, 야간 평가는 batch — 라우팅 레이어에서 요청 성격에 따라 `service_tier`를 분기하는 것이 티어 도입의 실질적 형태다.

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| Priority로 바꿨는데 여전히 429 | Priority는 우선순위만 바꿀 뿐 on-demand 쿼터를 priority/default/flex가 공유 | Service Quotas에서 해당 모델 TPM/RPM 소진율 확인 | 쿼터 증설 요청, CRIS 전환, 또는 Reserved Tier |
| Global CRIS 전환 후 `AccessDeniedException` | 3단계 IAM 정책 누락(특히 리전·계정 없는 글로벌 FM ARN `arn:aws:bedrock:::foundation-model/...`) 또는 리전 제한 SCP | CloudTrail에서 거부된 문장 확인; SCP의 `aws:RequestedRegion` 조건에 `"unspecified"` 허용 여부 확인 | [공식 3-파트 정책](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html) 적용, SCP에 `"unspecified"` 추가 |
| 리전 Deny SCP를 뒀는데 Global CRIS가 뚫림 | Global CRIS는 `aws:RequestedRegion`을 목적지 리전이 아닌 `global`로 설정 — 리전명 매칭 Deny가 무효 | 정책 시뮬레이터 또는 실호출로 검증 | `"aws:RequestedRegion": "unspecified"` + `inference-profile/global.*` 조건의 명시적 Deny |
| Flex 티어에서 에이전트 타임아웃 급증 | 피크 시간대에 Flex 요청이 Standard 뒤로 밀림 | CloudWatch에서 `ServiceTier` 차원별 latency 비교 | 지연 상한 있는 경로는 `default`/`priority`로 분기, Flex는 진짜 비동기 작업에만 |
| Reserved 예약량이 예상보다 빨리 소진 | `CacheWriteInputTokens`가 TPM 소비에 포함되는 것을 사이징에서 누락 | CloudWatch `InputTokenCount` + `CacheWriteInputTokens` 합산 | 두 지표 합산 기준으로 재사이징 |
| 쿼터 계산이 실측과 안 맞음 | Claude Sonnet 4/4.5, Opus 4 등은 출력 토큰 burndown이 5:1 (출력 1토큰 = 쿼터 5토큰) | [쿼터 burndown 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)의 모델별 배율 확인 | `입력 + 캐시쓰기 + (출력 × 5)` 공식으로 재계산 — 상세는 [동시성 쿼터 챕터](/08-scaling-cost/concurrency-quotas-throttling) |
| Provisioned Throughput 삭제했다고 생각했는데 과금 지속 | 약정 기간 내 삭제 불가, 약정 후에도 명시적 삭제 전까지 과금 | 콘솔/`ListProvisionedModelThroughputs`로 상태 확인 | 약정 만료 확인 후 명시적 삭제, auto-renew 해제 |

## 안티패턴

- ❌ 429 대응을 지수 백오프 재시도로만 해결 → ✅ 재시도는 마지막 방어선. 먼저 CRIS(무과금)로 용량 풀을 넓히고, 그다음 티어를 조정한다.
- ❌ 전 트래픽을 Priority로 일괄 전환 ("비싸도 빠르니까") → ✅ +75%는 지연 민감 경로에만. 나머지는 Standard/Flex로 분기하면 총비용이 오히려 내려간다.
- ❌ 컴플라이언스 검토 없이 10% 절감 때문에 Global 프로파일 채택 → ✅ 데이터 레지던시 요건을 먼저 확정하고, 요건이 있으면 지리 프로파일 + SCP로 Global을 명시적으로 차단한다.
- ❌ "예약 용량 = Provisioned Throughput"이라는 낡은 등식으로 설계 → ✅ 베이스 모델이면 Reserved Tier(TPM 단위, Standard 오버플로)를 먼저 비교 검토한다.
- ❌ 티어 선택을 모델 호출 코드 곳곳에 하드코딩 → ✅ 라우팅/게이트웨이 레이어 한 곳에서 요청 메타데이터(경로 성격, 지연 예산)로 `service_tier`를 결정한다.
- ❌ Flex로 전환하고 지연 SLO는 그대로 유지 → ✅ Flex 채택은 해당 경로의 지연 SLO 완화와 한 세트다.

## 계측 (SLI)

티어 전략은 계측 없이는 검증 불가다. 최소한 다음을 본다.

- **`ResolvedServiceTier`**: CloudWatch Metrics에서 `ModelId`, `ServiceTier`(요청한 티어), `ResolvedServiceTier`(실제 서빙한 티어) 차원을 제공한다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)). **요청 티어 ≠ 서빙 티어 비율**이 핵심 SLI다 — 예컨대 Reserved 요청이 Standard로 오버플로되는 비율이 지속적으로 높다면 예약량 재사이징 신호다. API 응답과 CloudTrail 이벤트에서도 서빙 티어를 확인할 수 있다.
- **티어별 latency 분해**: 같은 모델의 TTFT/OTPS를 `ServiceTier` 차원으로 분리해 Priority의 +75%가 실제 지연 개선으로 환산되는지 검증한다 (공식 주장: 대부분 모델에서 최대 25% OTPS 개선).
- **스로틀링율**: 모델·티어별 `ThrottlingException` 비율. CRIS/티어 전환의 효과를 직접 측정하는 지표다.
- **토큰 소비 분해**: `InputTokenCount`, `OutputTokenCount`, `CacheWriteInputTokens`, `CacheReadInputTokens` — Reserved 사이징과 [비용 배분](/08-scaling-cost/token-accounting-chargeback)의 원천 데이터.
- **Global CRIS 관측성**: 로그는 source region에 남으므로 대시보드는 기존 리전 기준을 유지하면 된다. 단, 처리 리전 자체는 지표로 노출되지 않는다는 점을 감사 대응 문서에 명시해 둔다.

## 체크리스트

- [ ] 데이터 레지던시 요건을 확정했다 — 요건이 있으면 지리 프로파일 고정 + Global CRIS 차단 SCP(`"aws:RequestedRegion": "unspecified"` + `global.*` 프로파일 ARN)를 배포했다.
- [ ] 온디맨드 단일 리전 대신 CRIS inference profile을 기본값으로 쓰고 있다 (라우팅 무과금).
- [ ] 요청 경로를 지연 민감도로 분류하고, 라우팅 레이어 한 곳에서 `service_tier`를 분기한다.
- [ ] 대상 모델이 각 티어를 지원하는지 [Models at a glance](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html)에서 확인했다.
- [ ] Priority 채택 경로의 비용 증가(+75%)를 트래픽 비중으로 환산해 승인받았다.
- [ ] Flex 채택 경로의 지연 SLO를 함께 완화했다.
- [ ] 배치성 작업은 Batch inference(−50%, 별도 쿼터)로 온라인 트래픽에서 격리했다.
- [ ] Reserved Tier 사이징에 `CacheWriteInputTokens`와 출력 burndown 배율(Claude 4계열 5:1)을 반영했다.
- [ ] `ServiceTier`/`ResolvedServiceTier` 차원의 CloudWatch 대시보드와 오버플로 비율 알람을 구성했다.
- [ ] Provisioned Throughput은 커스텀 모델 서빙 등 필수 케이스로 한정하고, 약정·auto-renew 상태를 정기 점검한다.

## 참고

- [Increase throughput with cross-Region inference — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)
- [Global cross-Region inference — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)
- [Service tiers for optimizing performance and cost — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/service-tiers-inference.html)
- [Amazon Bedrock introduces Priority and Flex inference service tiers (2025-11)](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-bedrock-priority-flex-inference-service-tiers)
- [Amazon Bedrock introduces Reserved Service tier (2025-11)](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-bedrock-reserved-service-tier)
- [Amazon Bedrock Reserved Tier — Claude Opus 4.5 / Haiku 4.5 (2026-01)](https://aws.amazon.com/about-aws/whats-new/2026/01/amazon-bedrock-reserved-tier-claude-opus-haiku)
- [Increase model invocation capacity with Provisioned Throughput — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/prov-throughput.html)
- [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)
- [Amazon Bedrock service tiers (제품 페이지)](https://aws.amazon.com/bedrock/service-tiers/)
- [Securing Amazon Bedrock cross-Region inference: geographic and global — AWS ML Blog](https://aws.amazon.com/blogs/machine-learning/securing-amazon-bedrock-cross-region-inference-geographic-and-global/)
