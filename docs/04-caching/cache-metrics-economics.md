---
title: 캐시 지표와 경제성
description: 에이전트 루프의 토큰 누적이 왜 quadratic인지, 캐시가 이를 어떻게 완화하는지, 그리고 캐시 관련 SLI를 어떻게 읽고 비용을 어떻게 계산하는지 다룬다.
outline: [2, 3]
---

# 캐시 지표와 경제성

::: tip 이 장에서 얻는 것
- 에이전트 루프에서 턴이 쌓일수록 토큰(과 비용)이 왜 quadratic(O(n²))하게 늘어나는지에 대한 개념적 유도, 그리고 캐시 읽기 0.1x 배수가 이 성장을 왜 "선형처럼 느껴지는" 구간으로 완화하는지(완전한 선형화는 아님)
- Claude Code + Bedrock 실무에서 5분 TTL vs 1시간 TTL이 개발 세션(1~4시간) 경제성에 미치는 영향과 `ENABLE_PROMPT_CACHING_1H` 설정
- 캐시 SLI(hit rate, cache read/write 토큰 비율)를 어떻게 정의하고, 캐시 히트율 < 70% 전환 임계치를 언제 발동시키는지
- Bedrock CloudWatch 런타임 지표(`InputTokenCount`, `CacheReadInputTokens`, `CacheWriteInputTokens`)로 비용과 TPM 쿼터 소모를 각각 어떻게 계산하는지 — 이 둘은 같은 공식이 아니다
- AgentCore Runtime의 기본 제공 지표에 캐시 토큰이 포함되지 않는다는 계측 공백과 그 대응
:::

## 왜 문제가 되는가

캐싱의 메커니즘(캐시 키 파생, 가격 배수, TTL, 모델별 최소 토큰)은 [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)가 정본이고, 캐시가 걸리지 않는 근본 원인은 [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes)이 다룬다. 이 장은 그 위에서 두 가지만 본다 — **왜 캐싱 없이는 에이전트 루프의 비용이 턴 수의 제곱에 가깝게 폭증하는가**, 그리고 **그 폭증을 관측하고 손익을 계산할 지표는 무엇인가**.

에이전트 루프는 상태가 없다(stateless). 모델은 요청 사이에 아무것도 기억하지 못하므로, 매 턴마다 시스템 프롬프트·도구 정의·이전 대화 전체를 다시 보내고 새 메시지를 끝에 붙인다.

> "Claude Code re-sends the full context: the system prompt, your project context, every prior message and tool result, and your new message. New content is appended at the end, which means most of each request is identical to the one before it."
> — [Claude Code 공식 문서: How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)

턴이 20턴, 50턴으로 쌓이는 긴 에이전트 세션에서 이 반복 재전송은 산술적으로 무해해 보이지만, 누적하면 전혀 그렇지 않다. 이것이 [6대 통증점](/00-intro/six-pain-points)의 3번(캐시 히트가 안 됨 → 비용 폭증)이 지연(1번)과 비용(5번)을 동시에 악화시키는 이유이기도 하다.

## 핵심 개념

### 1. 캐시 없이: 턴 수의 제곱에 가까운 토큰 누적

턴마다 새로 추가되는 토큰을 평균 `t`(시스템 프롬프트 + 도구 정의 + 해당 턴의 새 메시지·툴 결과)로 근사하면, `i`번째 턴에서 모델에 입력으로 들어가는 토큰 수는 대략 다음과 같다.

```
turn(i) 입력 토큰 ≈ t × i   (이전 i-1턴의 이력 + 이번 턴의 신규분)
```

세션 전체 `n`턴에 걸쳐 모델이 처리해야 하는 입력 토큰의 총합은

```
Total(캐시 없음) = Σ(i=1..n) t·i = t · n(n+1)/2  ≈  O(n²)
```

즉 턴 수가 2배가 되면 세션 전체 비용은 대략 4배가 된다. 이것이 "긴 에이전트 세션일수록 뒤로 갈수록 체감 비용이 급격히 늘어난다"는 현상의 산술적 근거다. 캐싱이 없다면 매 턴이 이전 전체 히스토리를 **uncached input** 가격(1x)으로 재처리하기 때문에, 이 O(n²) 성장에는 아무 완화 요인이 없다.

### 2. 캐시가 있으면: 지수는 그대로, 계수가 1/10로 줄어든다

캐시가 걸리면 각 턴에서 실제로 벌어지는 일은 다음과 같다 — 이전 턴까지 쌓인 이력(`t·(i-1)` 토큰)은 캐시 읽기(0.1x)로 처리되고, 이번 턴의 신규분(`t` 토큰)만 캐시 쓰기(5분 TTL이면 1.25x, 1시간 TTL이면 2.0x — 배수 출처는 [프롬프트 캐싱 기초 §가격 배수](/04-caching/prompt-caching-basics#가격-배수-anthropic-api와-bedrock-동일))로 처리된다.

```
Total(캐시 있음) ≈ price × [ w · t · n            (매 턴의 신규분 쓰기, n번)
                            + 0.1 · t · n(n-1)/2 ] (누적 이력 읽기, quadratic이지만 계수 0.1)
```

여기서 `w`는 캐시 쓰기 배수(1.25 또는 2.0)다. 두 식을 나란히 놓으면 핵심이 드러난다 — **캐싱은 O(n²)이라는 지수를 O(n)으로 바꾸지 않는다.** quadratic 항은 여전히 남아 있다. 다만 그 계수가 1에서 0.1로, 즉 10배 작아진다. 그 결과 실무적으로 의미 있는 구간에서는 선형에 가까운 `w·t·n` 항이 지배적이고, quadratic 항이 그것을 앞지르는 시점(교차점)은 상당히 뒤로 밀린다.

교차점 `n*`(선형 쓰기 비용과 quadratic 읽기 비용이 같아지는 턴 수)을 근사하면:

```
w · n ≈ 0.1 · n(n-1)/2   →   n* ≈ 20w + 1
```

- 5분 TTL(`w=1.25`): `n* ≈ 26턴`
- 1시간 TTL(`w=2.0`): `n* ≈ 41턴`

즉 세션이 대략 25~40턴을 넘기기 전까지는 캐시가 있는 비용 곡선이 선형에 가깝게 느껴지고, 그 이후에야 0.1 계수의 quadratic 항이 서서히 우세해진다. 아래 표는 `t=1`, `price=1`로 정규화한 상대 비용 단위로 이 관계를 보여준다(실제 달러 금액이 아니라 위 공식을 그대로 계산한 개념적 모델이다 — 실제 토큰 단가는 [Amazon Bedrock 가격 페이지](https://aws.amazon.com/bedrock/pricing/)를 따로 확인해야 한다).

| 턴 수 n | 캐시 없음: n(n+1)/2 | 캐시 있음(5분 TTL, w=1.25): 1.25n + 0.05n(n-1) | 비율(있음/없음) |
|---|---|---|---|
| 10 | 55 | 17.0 | 0.31 |
| 25 | 325 | 61.25 | 0.19 |
| 50 | 1,275 | 185.0 | 0.15 |
| 100 | 5,050 | 620.0 | 0.12 |
| 200 | 20,100 | 2,240.0 | 0.11 |

n이 커질수록 비율이 0.1(캐시 읽기 배수)에 점근한다는 것을 확인할 수 있다 — 이것이 "quadratic이 사라지지는 않지만 계수가 10배 작아져 실무적으로 훨씬 완만해진다"는 이 장의 핵심 주장이다.

::: warning 미정착 영역
위 유도는 턴당 신규 토큰량 `t`가 일정하다고 가정한 단순 모델이다. 실제 에이전트 루프에서는 출력 토큰 누적, 도구 결과의 크기 변동, compaction(`/compact`, [Context rot](/05-context/context-rot))으로 인한 이력 축소가 섞여 있어 순수한 산술수열을 따르지 않는다. 또한 캐시 쓰기가 매 턴 정확히 `t`토큰만 발생한다는 가정도 캐시 breakpoint 위치와 무효화 빈도에 따라 달라진다([캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) 참고). 이 절의 표는 "캐싱이 quadratic 계수를 얼마나 줄이는가"에 대한 방향성과 크기감을 보여주는 개념적 모델이며, 특정 세션의 정확한 비용 예측 도구로 쓰기에는 부족하다.
:::

### 3. Claude Code + Bedrock 실무: TTL과 개발 세션 경제성

앞의 모델에서 `w`(캐시 쓰기 배수)는 상수로 취급했지만, 실제로는 **TTL이 만료되면 캐시 전체가 무효화되어 그 시점의 누적 이력 전체가 다시 캐시 쓰기(1.25x 또는 2.0x)로 재처리**된다. 이것이 5분 TTL이 긴 개발 세션에서 특히 불리한 이유다.

Claude Code는 인증 방식에 따라 기본 TTL을 다르게 선택한다.

> "On an API key, Amazon Bedrock, Google Cloud's Agent Platform, Microsoft Foundry, or Claude Platform on AWS, you pay the per-token rates, so the TTL stays at the cheaper five minutes by default. To opt into the one-hour TTL, set `ENABLE_PROMPT_CACHING_1H=1`."
> — [Claude Code 공식 문서: How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching)

즉 **Bedrock 위에서 Claude Code를 쓰면 기본값은 5분 TTL**이다. 개발 세션은 보통 1~4시간 이어지고, 그 사이에는 코드를 읽거나 테스트를 돌리거나 리뷰어의 답을 기다리는 등 5분을 넘는 idle 구간이 반복적으로 발생한다. 5분 TTL에서는 idle 구간을 넘길 때마다 캐시가 만료되고, **그 시점까지 쌓인 전체 이력이 다시 캐시 쓰기로 재작성**된다 — 세션이 진행될수록 재작성 대상 이력이 커지므로, 재작성이 반복될수록 재작성 1회당 비용도 커진다. 1시간 TTL은 쓰기 배수 자체는 더 높지만(2.0x vs 1.25x), 세션 내 idle 구간 대부분을 만료 없이 넘기므로 재작성 횟수 자체를 줄인다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 아래 수치는 GitHub 이슈에 실린 실사용자의 프록시 로그 예시이며, AWS/Anthropic 공식 벤치마크가 아니다. 세션 길이·호출 빈도·프롬프트 크기에 따라 실제 절감폭은 달라진다.
> 한 이용자가 Bedrock에서 Claude Code를 100회 이상 요청하는 세션에 대해 보고한 바에 따르면, 5분 TTL에서는 idle 구간마다 캐시가 재작성되어(약 20회) 세션 전체 캐싱 비용이 약 $11.25였고, 동일 세션을 1시간 TTL로 재현했을 때는 최초 1회 쓰기 이후 나머지가 대부분 캐시 히트로 처리되어 약 $5.57로 절반가량 줄었다고 보고했다.
> — [GitHub Issue #32671: Bedrock: Prompt caching TTL hardcoded to 5m, no way to configure 1h](https://github.com/anthropics/claude-code/issues/32671)

이 절감 방향은 공식 문서의 배수 구조(캐시 읽기 0.1x가 재작성 1.25x/2.0x보다 훨씬 싸다는 사실)와 논리적으로 일치하며, Claude Code 공식 문서도 동일한 결론을 뒷받침한다.

> "If you regularly pause more than five minutes between messages, the 1-hour write cost pays for itself by the third read."

Bedrock에서 1시간 TTL을 켜려면 환경 변수를 설정한다.

```bash
ENABLE_PROMPT_CACHING_1H=1
```

이 값은 API key, Bedrock, Vertex, Foundry 등 provider 전반에 적용되며, 반대로 `FORCE_PROMPT_CACHING_5M=1`로 강제 5분 TTL로 되돌릴 수도 있다(디버깅·비교 목적).
— [Claude Code 공식 문서: Cache lifetime](https://code.claude.com/docs/en/prompt-caching#cache-lifetime)

::: warning 미정착 영역
"idle 구간이 몇 분 이상이면 1시간 TTL이 유리한가"에 대한 업계 표준 임계치는 없다. [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)가 지적하듯, 이는 워크로드의 실제 요청 간격 분포, heartbeat 호출 비용, rate limit(TPM 쿼터) 소모 여부에 따라 달라지며 팀마다 다른 기준을 쓴다. 이 장에서 제시한 §2의 교차점(`n*≈26~41턴`)은 "턴 수" 기준 근사이고, "idle 시간" 기준 근사와는 별도로 계산해야 한다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 세션이 짧고(수 턴 이내) 요청 간격이 항상 5분 미만 | 기본 5분 TTL 유지, 굳이 1시간으로 올리지 않음 | 히트마다 무료로 리셋되므로 5분 TTL로도 계속 warm 상태 | 없음 |
| Claude Code를 Bedrock 위에서 장시간(1~4시간) 개발 세션에 사용 | `ENABLE_PROMPT_CACHING_1H=1`로 1시간 TTL 전환 | idle 구간마다 반복되는 전체 재작성(1.25x)을 1회 쓰기(2.0x)로 대체 | 최초 쓰기 단가가 더 높음 — idle이 거의 없는 워크로드에서는 오히려 손해 |
| 세션이 25~40턴을 넘기는 장기 에이전트 루프 | compaction/context 관리 도입을 함께 검토 | §2의 교차점을 넘으면 quadratic(계수 0.1) 항이 다시 우세해짐 — 캐싱만으로는 한계가 있음 | compaction은 캐시된 이력을 버리므로 그 직후 턴은 다시 캐시 미스([Context rot](/05-context/context-rot) 참고) |
| 비용 대시보드에서 절대 비용($)만 보고 있음 | `CacheReadInputTokens`/`CacheWriteInputTokens`/`InputTokenCount`를 별도로 시계열로 추적 | 절대 비용만으로는 "히트율이 낮아서"인지 "요청량 자체가 늘어서"인지 구분 불가 | 지표 3종을 모두 CloudWatch에서 뽑아 대시보드화하는 추가 작업 필요 |
| Bedrock TPM 쿼터(throttling)를 계산해야 함 | 캐시 읽기 토큰이 아니라 `InputTokenCount + CacheWriteInputTokens + (OutputTokenCount × burndown rate)` 공식을 사용 | `CacheReadInputTokens`는 쿼터 burndown에 포함되지 않음 — 비용 공식과 쿼터 공식이 다르다 | 비용 절감분을 쿼터 여유로 착각하면 TPM 계산이 틀어짐 |
| AgentCore Runtime에서 캐시 경제성을 계측하고 싶음 | AgentCore 기본 제공 런타임 지표 대신 Bedrock model invocation logging 또는 커스텀 span 계측을 추가 | AgentCore Runtime의 기본 제공 지표(Invocations, Latency, SessionCount 등)에는 캐시 토큰 항목이 없음 | 별도 계측 파이프라인을 세션당 원가 계산과 연결해야 함(§계측 참고) |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 캐시 히트율은 높은데(예: 90%+) 청구서는 줄지 않음 | 세션 자체가 25~40턴을 넘겨 quadratic 항(계수 0.1)이 이미 우세 구간에 진입 — 히트율이 높아도 절대 토큰량이 커서 비용이 큼 | 히트율과 절대 `CacheReadInputTokens`/`CacheWriteInputTokens` 총량을 함께 시계열로 확인 | 캐싱 튜닝이 아니라 세션 길이·이력 크기 자체를 줄이는 문제 — compaction, 요약, 세션 분할 검토 |
| Bedrock에서 Claude Code 세션마다 캐시 쓰기가 반복적으로 크게 발생 | 기본 5분 TTL에서 idle 구간(리뷰 대기, 테스트 실행 등)이 반복적으로 5분을 초과 | 요청 간 시간 간격 히스토그램에서 5분 경계를 넘는 비율 확인 | `ENABLE_PROMPT_CACHING_1H=1`로 전환하고 손익 재계산 |
| TPM 쿼터 스로틀링이 발생했는데 비용 대시보드는 정상으로 보임 | 비용 공식(캐시 읽기 0.1x 반영)과 쿼터 burndown 공식(`InputTokenCount + CacheWriteInputTokens + OutputTokenCount×burndown rate`, 캐시 읽기 제외)이 서로 다름 | 쿼터 대시보드는 `InputTokenCount`/`CacheWriteInputTokens`/`OutputTokenCount` 기준으로, 비용 대시보드는 배수 적용 후 기준으로 별도 확인 | 캐시 읽기가 늘어도 쓰기·출력 토큰이 그대로면 쿼터 압박은 해소되지 않음 — `max_tokens` 최적화와 별도로 검토 |
| AgentCore Runtime 대시보드에서 캐시 관련 수치가 아예 안 보임 | AgentCore Runtime의 서비스 제공 지표(Invocations, Latency, Session Count 등)에는 토큰/캐시 항목이 포함되지 않음 | AgentCore observability 문서의 기본 제공 지표 목록과 실제 대시보드 항목을 대조 | Bedrock 모델 호출 로깅을 별도로 켜거나, 에이전트 코드에서 `usage`/`cacheReadInputTokens` 등을 커스텀 span·메트릭으로 직접 방출 |
| 캐시 히트율이 세션 초반엔 정상이다가 후반부에 급격히 떨어짐 | 세션이 길어지며 tool 정의 변경, MCP 서버 재연결, 모델/effort 전환 등 캐시 무효화 이벤트가 쌓임([캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) 참고) | 무효화 이벤트 시점과 히트율 급락 시점을 대조 | 세션 중 모델/effort 전환, MCP 서버 재연결 최소화 — 근본 원인 조치는 해당 챕터로 |
| 손익분기 계산 없이 무조건 1시간 TTL을 전역 적용 | "TTL을 올리면 항상 유리하다"는 단순화된 가정으로 결정 | idle 없이 5분 이내로 계속 호출되는 경로에서 write 배수(2.0x)만 추가로 부담하는지 확인 | 요청 간격 분포를 워크로드별로 분리해 TTL을 선택적으로 적용 |

## 안티패턴

- ❌ 캐시 히트율만 보고 "캐싱이 잘 되고 있으니 비용도 괜찮다"고 결론 낸다 → ✅ 히트율과 절대 토큰량(`CacheReadInputTokens`/`CacheWriteInputTokens`/`InputTokenCount`)을 함께 보고, 세션이 quadratic 우세 구간(대략 25~40턴 이상)에 들어갔는지 별도로 판단한다.
- ❌ 비용 절감을 곧 TPM 쿼터 여유로 오해한다 → ✅ 비용 공식(캐시 읽기 0.1x 포함)과 쿼터 burndown 공식(캐시 읽기 제외)이 다르다는 점을 대시보드에 명시하고 분리 계측한다.
- ❌ Bedrock에서 Claude Code를 쓰면서 기본 5분 TTL을 당연하게 받아들인다 → ✅ 개발 세션이 1시간 넘게 이어지고 idle 구간이 반복된다면 `ENABLE_PROMPT_CACHING_1H=1`을 시도하고 실제 재작성 빈도 감소를 확인한다.
- ❌ AgentCore Runtime 대시보드에 캐시 지표가 없다고 캐싱이 꺼져 있다고 오판한다 → ✅ AgentCore 기본 제공 런타임 지표에는 캐시 토큰이 포함되지 않는다는 계측 공백을 알고, Bedrock 모델 호출 로깅이나 커스텀 계측으로 별도 확인한다.
- ❌ n턴짜리 세션의 비용 성장을 "캐싱했으니 이제 선형"이라고 단정한다 → ✅ 캐싱은 quadratic의 지수를 없애지 않고 계수를 10배 줄인다는 것을 이해하고, 세션이 충분히 길어지면 다시 quadratic이 우세해진다는 것을 전제로 compaction 등 별도 대책을 함께 설계한다.

## 계측 (SLI)

### 정의

- **cache hit rate**: `cache_read_input_tokens / (cache_read_input_tokens + cache_creation_input_tokens + input_tokens)` — 정의와 계산 근거는 [프롬프트 캐싱 기초 §계측](/04-caching/prompt-caching-basics#계측-sli)과 동일하게 유지한다.
- **write:read ratio**: 일정 시간창 내 캐시 쓰기 이벤트 수 대비 읽기 이벤트 수. 이 비율이 1:1에 가까우면 손익분기를 겨우 넘는 수준이고, write만 반복되면 무효화가 발생 중이라는 신호다.
- **전환 임계치 — 캐시 히트율 < 70%**: 이 임계치는 이 책의 정본 표에서 그대로 가져온다. 히트율이 70% 미만으로 떨어지면 프롬프트 구조(동적 요소 앞배치, 툴 재정렬, JSON 비결정성)를 점검하고 TTL 상향을 검토한다. 근거: [6대 통증점 — 전환 임계치 표](/00-intro/six-pain-points#전환-임계치-표).

### Bedrock에서의 계측 소스

Bedrock Converse/InvokeModel API 응답과 CloudWatch에는 다음 런타임 지표가 있다.

| 지표 | 정의 | 쿼터(TPM/TPD) 반영 | 비용 반영 |
|---|---|---|---|
| `InputTokenCount` | 캐시되지 않은 입력 토큰 수 | 포함 | 1x (표준 입력가) |
| `CacheWriteInputTokens` | 캐시에 새로 쓰인 입력 토큰 수 | 포함 | 1.25x(5분) / 2.0x(1시간) |
| `CacheReadInputTokens` | 캐시에서 읽어온 입력 토큰 수 | **미포함** | 0.1x |
| `OutputTokenCount` | 생성된 출력 토큰 수 | 포함(모델별 burndown rate 적용, 예: Claude Sonnet 5는 10x) | 표준 출력가 |

출처: [Amazon Bedrock 공식 문서: How tokens are counted in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) — "`InputTokenCount`, `OutputTokenCount`, `CacheReadInputTokens`, `CacheWriteInputTokens`" are described as "The CloudWatch Amazon Bedrock runtime metric"; "`CacheReadInputTokens` don't contribute to this calculation and are not counted toward your quota." 총 입력 토큰(비용 계산용)은 [prompt-caching.html](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)의 공식을 따른다: `total input tokens = inputTokens + cacheReadInputTokens + cacheWriteInputTokens`.

이 표에서 실무적으로 가장 자주 놓치는 부분은 **쿼터 열과 비용 열이 서로 다른 계산**이라는 점이다. 캐시 읽기는 비용은 0.1x로 싸지지만 쿼터 burndown 계산에서는 아예 빠진다 — 즉 캐시 히트가 늘어도 TPM 스로틀링 문제는 (읽기 자체로는) 해소되지 않으며, 실제 압박은 `InputTokenCount + CacheWriteInputTokens + OutputTokenCount×burndown rate`에 걸려 있다.

### AgentCore에서의 계측 공백

::: warning 미정착 영역 — 확인 필요
AgentCore Runtime의 서비스 제공(built-in) 지표(Invocations, Throttles, Latency, Session Count, ActiveSessionCount 등)는 [공식 문서](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-runtime-metrics.md)에 열거되어 있으나, 이 목록에는 토큰 수나 캐시 read/write 토큰 항목이 포함되어 있지 않다. 즉 **AgentCore Runtime 대시보드만으로는 캐시 경제성을 계측할 수 없다.** 캐시 지표를 확보하려면 (1) 기반 Bedrock 모델 호출에 대해 별도로 model invocation logging을 켜거나, (2) 에이전트 코드에서 OpenTelemetry GenAI semantic convention의 `gen_ai.usage.cache_read.input_tokens` 계열 속성을 커스텀 span으로 방출해야 한다. 다만 이 OTel 속성은 semantic-conventions 저장소에서 논의·확정 중인 항목으로, AgentCore가 이를 기본 채택했는지는 공식 문서에서 확인되지 않았다 — 프로젝트에 적용하기 전 반드시 재확인이 필요하다.
:::

## 체크리스트

- [ ] 세션당 턴 수가 §2의 교차점(대략 25~40턴)을 넘는지 확인했고, 넘는 세션에는 캐싱 외에 compaction/세션 분할 대책을 함께 두었는가
- [ ] Bedrock 위에서 Claude Code(또는 유사한 장시간 개발 세션형 워크로드)를 쓴다면 기본 5분 TTL이 적합한지, `ENABLE_PROMPT_CACHING_1H=1`이 더 경제적인지 실제 idle 간격 분포로 검증했는가
- [ ] 캐시 히트율을 [6대 통증점의 70% 임계치](/00-intro/six-pain-points#전환-임계치-표)와 비교해 지속적으로 모니터링하고 있는가
- [ ] 비용 대시보드와 TPM 쿼터 대시보드를 서로 다른 공식(캐시 읽기 포함/제외)으로 각각 구성했는가
- [ ] `InputTokenCount`, `CacheWriteInputTokens`, `CacheReadInputTokens`, `OutputTokenCount`를 CloudWatch에서 각각 시계열로 확보하고 있는가
- [ ] AgentCore Runtime을 쓴다면, 기본 제공 지표에 캐시 토큰이 없다는 계측 공백을 인지하고 별도 로깅/계측을 마련했는가
- [ ] 히트율이 높은데도 비용이 안 줄어드는 경우, 세션 길이 자체의 문제인지(quadratic 우세 구간) 프롬프트 구조 문제인지([캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) 참고) 구분했는가

## 참고

- [Claude Code 공식 문서: How Claude Code uses prompt caching](https://code.claude.com/docs/en/prompt-caching) — 매 턴 전체 히스토리 재전송 메커니즘, 인증 방식별 기본 TTL, `ENABLE_PROMPT_CACHING_1H`/`FORCE_PROMPT_CACHING_5M` 환경 변수, 캐시 성능 확인 방법
- [Anthropic 공식 문서: Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — 가격 배수(0.1x/1.25x/2.0x) 원본 정의, [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)의 정본 출처와 동일
- [Amazon Bedrock 공식 문서: Prompt caching for faster model inference](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html) — `cacheReadInputTokens`/`cacheWriteInputTokens` 응답 필드, 총 입력 토큰 계산 공식
- [Amazon Bedrock 공식 문서: How tokens are counted in Amazon Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) — `InputTokenCount`/`CacheReadInputTokens`/`CacheWriteInputTokens`/`OutputTokenCount` CloudWatch 런타임 지표 정의, 쿼터 burndown 공식, 모델별 output burndown rate
- [Amazon Bedrock AgentCore 공식 문서: AgentCore generated runtime observability data](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-runtime-metrics.md) — AgentCore Runtime 기본 제공 지표 목록(캐시 토큰 항목 없음 확인)
- [OpenTelemetry 공식 문서: Semantic conventions for AWS Bedrock operations](https://opentelemetry.io/docs/specs/semconv/gen-ai/aws-bedrock/) — GenAI 관측 표준의 현재 위치(semantic-conventions-genai 저장소로 이전)
- [6대 통증점 — 전환 임계치 표](/00-intro/six-pain-points#전환-임계치-표) — 캐시 히트율 70% 임계치의 정본
- [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics) — 캐시 키 파생, TTL 동작, 모델별 최소 토큰의 정본
- [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes) — 캐시가 걸리지 않는 근본 원인별 진단
- ⚠️ 비공식: [GitHub Issue #32671 — Bedrock: Prompt caching TTL hardcoded to 5m, no way to configure 1h](https://github.com/anthropics/claude-code/issues/32671) — 5분 vs 1시간 TTL의 실사용자 비용 비교 사례(공식 벤치마크 아님)
