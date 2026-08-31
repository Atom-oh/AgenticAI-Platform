---
title: 모델 라우팅
description: 작업 난이도에 맞는 모델을 선택하고, 규칙·휴리스틱·모델 기반 라우터와 Bedrock Intelligent Prompt Routing으로 지연과 비용을 동시에 최적화한다.
outline: [2, 3]
---

# 모델 라우팅

::: tip 이 장에서 얻는 것
- 작업 난이도별 모델 티어 매칭이 지연·비용에 미치는 정량적 영향과 그 근거
- 규칙 기반 / 휴리스틱 / 모델 기반 라우터의 트레이드오프와 선택 기준
- Amazon Bedrock Intelligent Prompt Routing의 실제 동작 방식과 공식 문서에 명시된 제약
- 에이전트 루프 내부에서 스텝 성격(계획/실행/요약)에 따라 모델을 바꾸는 패턴과 AgentCore Harness의 per-invocation model override 활용법
- 오라우팅(misrouting) 비용을 계측하는 SLI 설계
:::

## 왜 문제가 되는가

에이전트 플랫폼에서 모델 선택은 요청당 한 번이 아니라 **루프 반복(iteration)마다** 일어나는 결정이다. Anthropic의 multi-agent 리서치 시스템 분석에 따르면 에이전트는 일반 챗 대비 약 4배, 멀티 에이전트 시스템은 약 15배의 토큰을 소비한다[^multi-agent]. 이 배수 구조에서 "모든 호출에 가장 큰 모델"이라는 기본값은 비용과 지연 양쪽에서 곱셈으로 손해를 키운다.

티어 간 단가 차이는 작지 않다. Anthropic 공식 가격 기준으로 Claude Haiku 4.5는 입력 $1 / 출력 $5 per MTok[^haiku-pricing], Claude Sonnet 4.5는 $3 / $15[^sonnet-pricing], Claude Opus 4.5는 $5 / $25[^opus-pricing]다. 즉 최상위 티어와 최하위 티어 사이에 입력·출력 모두 **5배**의 단가 차이가 있다. Anthropic은 Haiku 4.5 발표에서 이 모델이 Sonnet 4 수준의 코딩 성능을 "1/3 비용, 2배 이상의 속도"로 제공한다고 명시했다[^haiku-announce] — 단순 작업을 상위 티어로 보내는 것은 돈뿐 아니라 시간도 낭비한다는 뜻이다.

문제의 핵심은 이것이다: 에이전트 워크로드는 난이도가 균질하지 않다. 툴 호출 인자 추출, intent 분류, 대화 요약 같은 스텝은 작은 모델로 충분하지만, 다단계 계획 수립이나 코드 생성은 상위 티어가 필요하다. 난이도 분포가 섞여 있는데 모델이 하나로 고정되어 있으면, 반드시 한쪽 방향으로 낭비가 발생한다 — 상위 모델 고정이면 비용·지연 낭비, 하위 모델 고정이면 품질 실패와 그로 인한 재시도 낭비.

## 핵심 개념

### 작업 난이도와 모델 티어의 매칭

라우팅의 첫 원칙은 **출력의 요구 수준이 모델을 결정한다**는 것이다. 플랫폼 관점에서 작업을 세 축으로 분해하면 매칭이 명확해진다.

- **변환 폭**: 입력을 거의 그대로 재구성하는가(추출·분류·포맷 변환), 새로운 구조를 생성하는가(계획·코드·분석)
- **오류 비용**: 틀렸을 때 재시도로 회복 가능한가, 다운스트림 액션(툴 실행, 사용자 노출)으로 전파되는가
- **컨텍스트 통합량**: 단일 메시지만 보면 되는가, 긴 히스토리·여러 문서를 종합해야 하는가

세 축 모두 낮으면 Haiku·Nova Micro급 — Amazon은 Nova Micro를 최저 지연·최저 비용의 text-only 모델로 포지셔닝한다[^nova] — 이 정답이고, 하나라도 높으면 Sonnet급, 변환 폭과 오류 비용이 모두 높으면(아키텍처 결정, 리드 에이전트의 작업 분해) Opus급을 검토한다. 리드(Opus) + 서브에이전트(Sonnet) 구성의 실증 근거와 15배 토큰 비용의 경제성 조건은 [단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent) 장에서 이미 다뤘으므로 여기서는 반복하지 않는다 — 이 장의 관심사는 그 매칭을 **런타임에 누가, 어떻게 결정하느냐**다.

### 라우터 설계: 세 가지 방식

라우터는 "이 요청을 어느 모델로 보낼 것인가"를 결정하는 컴포넌트다. 결정 로직의 위치와 비용에 따라 세 갈래로 나뉜다.

**1. 규칙 기반 (static mapping)** — 요청 유형·엔드포인트·스텝 종류별로 모델을 고정 매핑한다. `classify_intent → Haiku`, `generate_code → Sonnet`처럼 코드나 설정에 박는다. 라우팅 지연 0, 동작이 완전히 예측 가능하고 디버깅이 쉽다. 단점은 유형 내부의 난이도 분산을 못 잡는다는 것 — "코드 생성"에도 한 줄짜리와 리팩터링이 섞여 있다.

**2. 휴리스틱 (feature-based)** — 입력 길이, 첨부 문서 수, 키워드, 대화 턴 수 같은 피처로 분기한다. 규칙 기반보다 한 단계 동적이지만 여전히 수 ms 수준의 오버헤드다. 피처가 난이도의 좋은 프록시일 때만 유효하며, "긴 입력 = 어려운 작업"이라는 가정은 자주 깨진다(긴 로그에서 한 필드 추출은 쉬운 작업이다).

**3. 모델 기반 (learned router)** — 작은 LLM 또는 학습된 분류기가 프롬프트 난이도를 예측해 모델을 고른다. 유형 내부 분산까지 잡을 수 있는 대신, 라우터 자체가 추론 호출이므로 **모든 요청에 추가 지연과 비용**이 붙는다. RouteLLM 연구는 선호 데이터로 학습한 라우터가 MT Bench에서 GPT-4 대비 성능 95%를 유지하면서 비용을 85% 이상 절감할 수 있다고 보고했다[^routellm].

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요: RouteLLM 수치는 arXiv preprint의 자체 벤치마크 결과이며, 프로덕션 워크로드·최신 모델 조합에서의 재현성은 별도 검증이 필요하다.

트레이드오프의 본질은 이렇다. 요청당 기대 비용은 대략 다음 항의 합이다:

```
기대 비용 = 라우터 오버헤드
          + P(하향 오라우팅) × (품질 실패 비용 + 재시도/에스컬레이션 비용)
          + P(상향 오라우팅) × (상위 티어 초과 단가)
```

라우터가 정교해질수록 오라우팅 확률은 줄지만 오버헤드가 커진다. 트래픽의 난이도 분포가 유형별로 이미 잘 갈리면 규칙 기반이 최적이고, 같은 유형 안에서 난이도 분산이 크고 볼륨이 충분할 때만 모델 기반 라우터의 오버헤드가 정당화된다.

### Amazon Bedrock Intelligent Prompt Routing

Bedrock은 이 결정을 관리형으로 제공하는 **Intelligent Prompt Routing**을 갖고 있다. 공식 문서에 따르면 이 기능은 "단일 serverless endpoint로 같은 model family 내의 서로 다른 모델 간에 요청을 라우팅"하며, "각 요청에 대해 각 모델의 응답 품질을 동적으로 예측한 뒤 최고 응답 품질의 모델로 라우팅"한다[^ipr]. 동작 순서는 다음과 같다[^ipr]:

1. model family와 라우터 구성 선택 (default router 또는 configured router)
2. 수신 요청의 프롬프트 분석
3. family 내 각 모델의 **응답 품질 예측**
4. 품질·비용 조합이 최적인 모델 선택 및 요청 전달
5. 응답 반환 — 응답에는 실제 사용된 모델 정보가 포함된다

Configured router에서는 **fallback model**과 **response quality difference** 기준을 지정한다. 예컨대 기준이 10%면, 라우터는 다른 모델의 예측 응답 품질이 fallback 모델보다 10% 이상 좋을 때만 전환한다[^ipr]. 라우터 생성은 `CreatePromptRouter` API로 하며, family 내에서 정확히 두 모델을 짝지어 구성한다[^ipr].

공식 문서가 명시하는 제약을 그대로 옮기면[^ipr]:

- **영어 프롬프트에만 최적화**되어 있다 — 한국어 트래픽이 주력인 플랫폼에서는 라우팅 품질을 보장할 수 없다.
- 애플리케이션 고유의 성능 데이터로 **라우팅 결정을 조정할 수 없다** — 내 도메인의 품질 피드백을 학습시킬 방법이 없다.
- 라우팅 효과는 초기 학습 데이터에 의존하므로, **특수한(unique/specialized) 유스케이스에서는 최적 라우팅을 보장하지 않는다**.
- 지원 모델·리전이 문서의 표로 한정된다 — 채택 전 현재 지원 목록을 반드시 확인하라.

정리하면: 영어 중심의 범용 트래픽에서 "관리형 모델 기반 라우터"를 오케스트레이션 코드 없이 얻는 수단으로는 유효하지만, 한국어 워크로드·도메인 특화 에이전트·자체 품질 피드백 루프가 있는 플랫폼에서는 자체 라우터(규칙/휴리스틱/자체 분류기)가 여전히 필요하다.

### 에이전트 루프 내부의 라우팅: 스텝 성격별 모델 전환

요청 단위 라우팅보다 한 단계 깊은 패턴은 **같은 에이전트의 한 세션 안에서 스텝 성격에 따라 모델을 바꾸는 것**이다. 에이전트 루프의 스텝은 성격이 다르다:

- **계획(plan)**: 작업 분해, 툴 선택 전략 — 오류가 이후 전체 스텝에 전파되므로 상위 티어가 정당화된다
- **실행(act)**: 툴 인자 생성, 결과 파싱 — 대부분 좁은 변환이며 하위 티어로 충분한 경우가 많다
- **요약/압축(summarize)**: 컨텍스트 컴팩션, 대화 요약 — 전형적인 하위 티어 작업

이 패턴을 막는 전형적 장애물은 "모델이 배포 아티팩트에 고정되어 있다"는 것이었다. Amazon Bedrock AgentCore의 Harness는 이를 API 레벨에서 푼다. `InvokeHarness` 요청 본문의 `model` 필드는 공식 API 레퍼런스에 "이 invocation에 사용할 model configuration. 지정하면 harness 기본값을 override한다"로 정의되어 있다[^invoke-harness]. 즉 harness를 재배포하지 않고 **호출 단위로** 모델을 바꿀 수 있다. `systemPrompt`, `tools`, `allowedTools`, `maxIterations`도 같은 방식으로 per-invocation override가 가능하므로[^invoke-harness], 하나의 harness 배포 위에 "가벼운 프로필(Haiku + 축소 툴셋)"과 "무거운 프로필(Sonnet + 전체 툴셋)"을 얹는 구성이 성립한다. 이 책의 [demo/builder-harness](/11-builder-agent/)는 `apac.anthropic.claude-sonnet-4-20250514-v1:0`을 harness 기본 모델로 배포하는데, 호출 측에서 `model`을 넘기면 그 기본값 위에 라우팅 계층을 얹을 수 있다.

한 가지 주의: 스텝 간 모델 전환은 prompt cache를 무효화할 수 있다. 캐시는 모델 단위로 유지되므로, 긴 시스템 프롬프트·툴 정의를 캐싱해둔 세션에서 모델을 바꾸면 cache write를 다시 지불한다. 캐싱 경제성과의 상호작용은 [Part 4](/04-caching/)에서 다룬다.

### 에스컬레이션: 라우팅의 안전망

정적이든 동적이든, 라우터는 **하향 오라우팅의 회복 경로**를 가져야 한다. 실무 패턴은 "작은 모델 먼저, 실패 신호 시 상위 티어 재시도"다. 실패 신호는 구조화 출력의 스키마 검증 실패, 툴 인자 validation 오류, 자체 평가(confidence) 저하 등으로 정의한다. 에스컬레이션이 있으면 라우터의 하향 오류가 "품질 사고"가 아니라 "지연 +1회 왕복"으로 완화된다 — 대신 에스컬레이션율이 높아지면 두 모델 비용을 이중으로 내는 셈이므로, 에스컬레이션율 자체를 SLI로 감시해야 한다(아래 계측 절).

### 크로스리전 추론 프로파일(CRIS)과의 구분

모델 ID에 `us.`/`apac.` 프리픽스가 붙는 cross-region inference profile은 이 장의 라우팅과 목적이 다르다 — CRIS는 **같은 모델**의 요청을 여러 리전으로 분산해 가용성·스로틀링을 완화하는 장치이고, 이 장의 라우팅은 **다른 모델** 간 품질·비용 선택이다. 두 축은 직교하며 조합 가능하다(라우터가 모델을 고르고, 그 모델의 CRIS 프로파일로 호출). CRIS의 상세(리전 세트, 스로틀링 동작, quota와의 관계)는 [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers) 장이 정본이다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 스텝 유형별 난이도가 명확히 갈림 (분류/추출 vs 코드 생성) | 규칙 기반 매핑 | 오버헤드 0, 예측 가능, 디버깅 용이 | 유형 내부 난이도 분산 미포착 |
| 같은 유형 안에서 난이도 분산이 크고 볼륨이 큼 | 모델 기반 라우터 + 에스컬레이션 | 유형 내부 분산까지 라우팅 | 전 요청에 라우터 지연·비용 추가 |
| 영어 범용 트래픽, 오케스트레이션 코드 최소화 원함 | Bedrock Intelligent Prompt Routing | 관리형, 응답 품질 예측 기반 자동 선택[^ipr] | 영어 최적화, 자체 피드백 반영 불가, 지원 모델 제한[^ipr] |
| 한국어 주력 트래픽 | 자체 라우터 (규칙/휴리스틱/자체 분류기) | IPR은 영어 프롬프트에만 최적화[^ipr] | 라우터 구축·평가·운영 비용 자담 |
| 한 에이전트 세션 내 스텝 성격이 이질적 | AgentCore Harness per-invocation `model` override[^invoke-harness] | 재배포 없이 호출별 모델 전환 | 모델 전환 시 prompt cache 무효화 가능 |
| 오류 비용이 높은 결정 스텝 (계획, 작업 분해) | 상위 티어 고정 (라우팅 제외) | 하향 오라우팅의 전파 비용이 절감액을 초과 | 해당 스텝 비용 상한 고정 |
| 품질 검증이 기계적으로 가능한 출력 (스키마, 테스트) | 하위 티어 우선 + 실패 시 에스컬레이션 | 오라우팅이 사고가 아닌 재시도로 완화 | 에스컬레이션율만큼 이중 과금·지연 |
| 스로틀링·리전 가용성이 병목 | 모델 라우팅이 아닌 CRIS | 문제의 축이 다름 (같은 모델의 분산) | [Part 8](/08-scaling-cost/bedrock-inference-tiers) 참고 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| p50 지연은 좋은데 비용이 예산 초과 | 상향 오라우팅 — 단순 스텝이 상위 티어로 감 | 스텝 유형별 modelId 분포·토큰 비용 분해 | 추출/분류/요약 스텝을 하위 티어로 강등, 강등 후 품질 회귀 테스트 |
| 특정 유형 요청의 품질 저하·재시도 급증 | 하향 오라우팅 — 라우터가 난이도를 과소평가 | 에스컬레이션율·스키마 검증 실패율을 라우팅 결정별로 집계 | 해당 유형 규칙 상향 조정, 또는 에스컬레이션 트리거 강화 |
| 전 요청 TTFT가 일괄 +수백 ms | 모델 기반 라우터의 추론 오버헤드 | 라우터 구간 span을 분리 계측 ([지연 해부](/02-performance/latency-anatomy) 참고) | 고빈도 유형은 규칙으로 단락(short-circuit), 라우터는 애매한 구간에만 |
| 한국어 트래픽에서 IPR 라우팅이 체감상 무작위 | IPR은 영어 프롬프트에만 최적화[^ipr] | 언어별 라우팅 분포·품질 지표 분리 집계 | 한국어 트래픽은 자체 라우터로 우회 |
| 모델 전환 직후 입력 토큰 비용 급증 | 모델 변경으로 prompt cache miss (cache write 재지불) | usage의 `cacheReadInputTokens`/`cacheWriteInputTokens` 추이[^invoke-harness] | 전환 빈도 축소, 세션 내 모델 고정 구간 설계 |
| 에스컬레이션율이 계속 상승하며 비용 역전 | 하위 티어 기본값이 워크로드 난이도와 불일치 | 에스컬레이션율 SLI 추세 + 유형별 분해 | 해당 유형의 1차 모델을 한 티어 상향 — 이중 과금보다 싸다 |
| 라우팅 로직 변경 후 원인 불명 품질 회귀 | 라우팅 결정이 로그에 없어 재현 불가 | 요청별 라우팅 결정·근거·최종 modelId 로깅 여부 점검 | 라우팅 결정을 구조화 로그로 남기고 배포 태그와 조인 |
| IPR configured router가 기대와 달리 거의 한 모델만 사용 | response quality difference 기준이 임계 부근에서 한쪽으로 쏠림[^ipr] | 응답에 포함된 사용 모델 정보 집계[^ipr] | 기준값 조정 후 playground/오프라인 평가로 분포 재확인 |

## 안티패턴

- ❌ 모든 스텝에 최상위 모델을 기본값으로 배포하고 "나중에 최적화" → ✅ 배포 첫날부터 스텝 유형별 규칙 매핑을 넣는다. 나중의 강등은 품질 회귀 검증 비용이 들지만, 처음의 매핑은 거의 공짜다.
- ❌ 라우터 정확도만 높이려고 라우터 자체를 상위 티어 모델로 교체 → ✅ 라우터 오버헤드가 절감액을 잠식한다. 라우터는 항상 라우팅 대상 최하위 모델보다 싸고 빨라야 한다.
- ❌ 하향 오라우팅의 회복 경로 없이 작은 모델로 강등 → ✅ 스키마 검증·툴 인자 validation 실패를 트리거로 한 에스컬레이션을 함께 배포한다.
- ❌ 한국어 트래픽을 Bedrock IPR에 그대로 태움 → ✅ 공식 제약(영어 최적화)을 존중하고[^ipr], 한국어는 자체 라우터로 처리한다.
- ❌ 모델별 품질 차이를 감으로 판단해 라우팅 규칙 작성 → ✅ 스텝 유형별 오프라인 평가 세트로 티어 간 품질 격차를 측정한 뒤 규칙을 만든다 ([Part 3](/03-accuracy-eval/) 참고).
- ❌ 스로틀링 회피 목적으로 모델 라우팅 도입 → ✅ 그 문제의 도구는 CRIS·추론 티어다 ([Part 8](/08-scaling-cost/bedrock-inference-tiers)). 품질·비용 축과 가용성 축을 섞지 않는다.
- ❌ 라우팅 결정을 로깅하지 않음 → ✅ 요청별 (라우팅 근거, 선택 모델, 에스컬레이션 여부)를 구조화 로그로 남긴다. 이것이 없으면 아래 SLI 전부가 불가능하다.

## 계측 (SLI)

라우팅은 "배포하고 끝"이 아니라 분포를 감시해야 하는 시스템이다. 최소 SLI 세트:

- **라우팅 분포**: 스텝 유형 × 선택 모델의 요청 수·토큰 비중. 분포가 한쪽으로 쏠리면 라우터가 사실상 죽은 것이다.
- **에스컬레이션율**: 하위 티어 시도 후 상위 티어 재시도된 비율. 상승 추세는 1차 모델 티어가 워크로드와 불일치한다는 신호. 이중 과금 손익분기(대략 하위 티어 비용/상·하위 티어 비용 차)를 넘으면 기본 티어를 올린다.
- **하향 오라우팅 프록시**: 스키마 검증 실패율, 툴 인자 validation 오류율, 사용자 재질문율을 라우팅 결정별로 분해.
- **라우터 오버헤드**: 라우팅 결정 구간의 지연을 별도 span으로. 모델 기반 라우터라면 라우터 자체의 토큰 비용도 집계.
- **모델별 단가 가중 비용**: 티어별 단가[^haiku-pricing][^sonnet-pricing][^opus-pricing]를 곱한 스텝 유형별 비용. AgentCore Harness는 스트림의 `metadata` 이벤트로 invocation별 `inputTokens`/`outputTokens`/`cacheReadInputTokens`/`latencyMs`를 반환하므로[^invoke-harness] 이를 라우팅 로그와 조인한다. 차지백 설계는 [Part 8](/08-scaling-cost/token-accounting-chargeback)에서.
- **IPR 사용 시 — 실제 선택 모델 분포**: 응답에 포함되는 사용 모델 정보[^ipr]를 집계해 response quality difference 기준이 의도한 분포를 만드는지 확인.

::: warning 미정착 영역
"오라우팅률"을 직접 측정하는 표준 방법은 아직 없다. 실무에서는 (a) 샘플링된 요청을 두 티어에 모두 보내 품질을 오프라인 비교하는 shadow routing, (b) 에스컬레이션율·검증 실패율 같은 프록시 지표 중 하나를 쓰는데, shadow routing은 정확하지만 비용이 이중이고 프록시는 싸지만 하향 오류만 잡는다. 조직의 평가 인프라 성숙도에 따라 선택이 갈리는 영역이다.
:::

## 체크리스트

- [ ] 스텝 유형별(분류/추출/계획/실행/요약) 오프라인 평가 세트로 티어 간 품질 격차를 측정했다
- [ ] 스텝 유형 → 모델 규칙 매핑이 코드/설정으로 존재하고, 최상위 모델이 무조건 기본값이 아니다
- [ ] 하향 오라우팅 회복 경로(검증 실패 트리거 에스컬레이션)가 있다
- [ ] 라우팅 결정(근거, 선택 모델, 에스컬레이션 여부)이 요청별 구조화 로그로 남는다
- [ ] 라우터 오버헤드가 별도 span으로 계측되고, 모델 기반 라우터라면 라우팅 대상 최하위 모델보다 싸고 빠르다
- [ ] Bedrock IPR 채택 검토 시: 트래픽 언어(영어 최적화 제약), 지원 모델·리전 표, 자체 품질 피드백 반영 불가를 확인했다[^ipr]
- [ ] AgentCore Harness 사용 시: per-invocation `model` override로 재배포 없는 스텝별 모델 전환이 가능함을 안다[^invoke-harness]
- [ ] 모델 전환이 prompt cache에 미치는 영향(cache write 재지불)을 usage 지표로 감시한다
- [ ] 에스컬레이션율의 손익분기를 계산해두고, 초과 시 기본 티어 상향 절차가 있다
- [ ] 스로틀링·가용성 문제를 모델 라우팅으로 풀려 하지 않는다 — CRIS/추론 티어는 [Part 8](/08-scaling-cost/bedrock-inference-tiers)

## 참고

- AWS, [Understanding intelligent prompt routing in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html) — 동작 방식, 지원 모델, 제약, routing criteria
- AWS, [InvokeHarness API Reference (Amazon Bedrock AgentCore)](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html) — per-invocation `model`/`systemPrompt`/`tools` override, usage metadata
- Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system) — 에이전트 토큰 배수, Opus 리드 + Sonnet 서브 구성
- Anthropic, [Claude Haiku 4.5](https://www.anthropic.com/claude/haiku) / [Introducing Claude Haiku 4.5](https://www.anthropic.com/news/claude-haiku-4-5) — Haiku 가격·성능 포지셔닝
- Anthropic, [Introducing Claude Sonnet 4.5](https://www.anthropic.com/news/claude-sonnet-4-5), [Introducing Claude Opus 4.5](https://www.anthropic.com/news/claude-opus-4-5) — 티어별 가격
- AWS, [Amazon Nova foundation models](https://aws.amazon.com/ai/generative-ai/nova/) — Nova Micro 포지셔닝
- Ong et al., [RouteLLM: Learning to Route LLMs with Preference Data](https://arxiv.org/abs/2406.18665) — 학습형 라우터 연구 (preprint)
- 이 책: [단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent), [지연 해부](/02-performance/latency-anatomy), [Bedrock 추론 티어](/08-scaling-cost/bedrock-inference-tiers)

[^multi-agent]: Anthropic, ["How we built our multi-agent research system"](https://www.anthropic.com/engineering/built-multi-agent-research-system) (2025). 챗 대비 에이전트 약 4배, 멀티 에이전트 약 15배 토큰 사용의 출처. 상세 논의는 [단일 vs 멀티 에이전트](/01-agent-design/single-vs-multi-agent) 장 참고.
[^haiku-pricing]: Anthropic, [Claude Haiku 4.5](https://www.anthropic.com/claude/haiku) — 입력 $1 / 출력 $5 per MTok.
[^sonnet-pricing]: Anthropic, [Introducing Claude Sonnet 4.5](https://www.anthropic.com/news/claude-sonnet-4-5) — 입력 $3 / 출력 $15 per MTok.
[^opus-pricing]: Anthropic, [Introducing Claude Opus 4.5](https://www.anthropic.com/news/claude-opus-4-5) — 입력 $5 / 출력 $25 per MTok.
[^haiku-announce]: Anthropic, [Introducing Claude Haiku 4.5](https://www.anthropic.com/news/claude-haiku-4-5) — Sonnet 4 대비 유사한 코딩 성능을 1/3 비용·2배 이상 속도로 제공한다는 발표 문구의 출처.
[^nova]: AWS, [Amazon Nova foundation models](https://aws.amazon.com/ai/generative-ai/nova/) — Nova Micro의 lowest-latency text-only 포지셔닝.
[^ipr]: AWS, [Understanding intelligent prompt routing in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html) — 응답 품질 예측 기반 라우팅 동작, default/configured router, fallback model과 response quality difference, 영어 최적화·앱 피드백 미반영·학습 데이터 의존 제약, 지원 모델·리전 표, `CreatePromptRouter` API의 출처.
[^invoke-harness]: AWS, [InvokeHarness — Amazon Bedrock AgentCore API Reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeHarness.html) — 요청 본문 `model` 필드("If specified, overrides the harness default"), `systemPrompt`/`tools`/`allowedTools`/`maxIterations` override, 응답 `metadata`의 usage(`inputTokens`, `outputTokens`, `cacheReadInputTokens`, `cacheWriteInputTokens`)와 `latencyMs`의 출처.
[^routellm]: Ong et al., ["RouteLLM: Learning to Route LLMs with Preference Data"](https://arxiv.org/abs/2406.18665) (arXiv:2406.18665) — MT Bench에서 GPT-4 성능 95% 유지, 비용 85%+ 절감 보고의 출처.
