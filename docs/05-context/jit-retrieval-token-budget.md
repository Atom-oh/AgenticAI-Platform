---
title: JIT 검색과 토큰 예산
description: just-in-time 검색과 프리로딩의 결정 기준, 그리고 컨텍스트 구성요소별 토큰 예산 배분 원칙을 다룬다.
outline: [2, 3]
---

# JIT 검색과 토큰 예산

::: tip 이 장에서 얻는 것
- Just-in-time(JIT) 검색과 프리로딩(preloading) 중 무엇을 선택할지 판단하는 결정 기준과, 실무에서 흔한 하이브리드 구성
- 시스템 프롬프트·툴·메모리·검색 문서·대화 이력 사이에 토큰 예산을 어떻게 배분할지에 대한 예시(참고용)와, Claude Code가 실제로 공개한 스타트업 토큰 구성
- JIT 검색이 Part 2에서 다루는 지연(latency)과 근본적으로 트레이드오프 관계에 있다는 점, 그리고 이를 완화하는 하이브리드/캐싱 전략
- Part 5 전체(컨텍스트 엔지니어링 원칙 → context rot → compaction → 격리/오프로딩)가 왜 이 장에서 수렴하는지
:::

## 왜 문제가 되는가

Part 5는 지금까지 컨텍스트라는 유한 자원을 다루는 규율을 순서대로 쌓아 왔다. [컨텍스트 엔지니어링 원칙](./context-engineering-discipline.md)은 "가장 작은 고신호 토큰 집합"을 찾는 것이 목표라고 정의했고, [Context rot](./context-rot.md)는 그 집합이 커질수록 신뢰도가 비선형적으로 무너진다는 실증 근거를 제시했다. [Compaction과 요약](./compaction-summarization.md)은 컨텍스트가 이미 찬 뒤 압축하는 하류(downstream) 대책을, [컨텍스트 격리와 오프로딩](./context-isolation-offloading.md)은 애초에 컨텍스트에 들어오지 않도록 막는 상류(upstream) 대책을 다뤘다.

이 장은 그 상류 대책 중 가장 근본적인 질문으로 돌아간다: **정보를 컨텍스트에 넣을 시점을 언제로 정할 것인가.** 두 가지 극단이 있다.

- **프리로딩(preloading)**: 관련될 가능성이 있는 문서를 세션 시작 시점에 미리 컨텍스트에 넣어 둔다. 검색 지연이 없고, 프롬프트 캐싱([프롬프트 캐싱 기초](../04-caching/prompt-caching-basics.md) 참고)의 이득을 최대로 받을 수 있다.
- **JIT(just-in-time) 검색**: 에이전트가 필요하다고 판단한 시점에 도구를 호출해 데이터를 조회한다. 컨텍스트에는 필요한 만큼만 들어오지만, 매 조회마다 도구 호출 왕복(round-trip)이 발생한다.

Anthropic은 업계 전반에서 사전 처리(pre-indexing) 대신 "에이전트가 도구로 직접 데이터를 조회하는" JIT 전략으로의 이동을 관찰한다고 말하면서도, 트레이드오프를 명시한다: "Of course, there's a trade-off: runtime exploration is slower than retrieving pre-computed data."[^ctx-eng] 즉 이 선택은 컨텍스트 청정도와 지연(latency) 사이의 트레이드오프이며, 지연 자체의 구조는 Part 2([Tool round-trip](../02-performance/tool-roundtrips.md), [지연의 해부](../02-performance/latency-anatomy.md))에서 다룬다 — 이 장은 "몇 번, 언제 조회할 것인가"라는 컨텍스트 쪽 판단에 집중하고, 그 판단이 지연 예산에 어떤 부담을 얹는지를 표로 연결한다.

두 극단 사이에는 실무에서 훨씬 흔한 회색 지대가 있다: "In certain settings, the most effective agents might employ a hybrid strategy, retrieving some data up front for speed, and pursuing further autonomous exploration at its discretion."[^ctx-eng] "결정 표"에서 이 경계를 판단 기준으로 정리한다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> JIT 검색이 실제로 얹는 지연을 밀리초 단위로 공식 발표한 Anthropic·OpenAI 자료는 확인되지 않았다. 업계 블로그 수준의 참고치로는, 수십만 문서 규모의 잘 색인된 벡터 검색은 P95 기준 수십 밀리초(예: 45ms) 수준이라는 추정이 있고,[^latency-rag] 순차적 도구 호출 여러 개를 거치는 RAG 에이전트는 P50 기준 약 2,850~2,900ms까지 누적되며 병렬화로 약 150ms를 절감했다는 사례가 있다.[^latency-agent] 두 수치 모두 특정 구현·인프라에 종속적이므로 자신의 파이프라인에서 직접 계측해야 한다.

## 핵심 개념

### 1. JIT 검색이 성립하는 조건

Anthropic이 정리한 JIT 검색의 이점은, 인간이 파일시스템 전체를 기억하지 않고 필요할 때 폴더를 열어보는 것과 같은 방식으로 에이전트가 동작하게 만든다는 것이다.[^ctx-eng] 코드베이스가 수십만~수백만 라인이거나, 문서 코퍼스가 계속 갱신되어 사전 인덱스가 금방 낡는 경우, 매 요청이 코퍼스의 서로 다른 작은 부분만 필요로 하는 경우가 JIT가 유리한 전형적 상황이다. 이런 경우 프리로딩은 (a) 컨텍스트에 대부분 쓰이지 않을 데이터를 채워 context rot 위험을 높이고, (b) 캐시 적중률을 오히려 떨어뜨릴 수 있다 — 매 요청마다 필요한 부분이 달라지면 "미리 넣어둔 것"의 재사용률 자체가 낮다.

컨텍스트 윈도우 자체가 커진다고 이 판단이 무의미해지는 것도 아니다. Claude Opus 5·Sonnet 5 등 일부 모델은 API·Bedrock·Google Cloud·Microsoft Foundry에서 기본으로 100만(1M) 토큰 윈도우를 제공하지만, Sonnet 4.5·Haiku 4.5 같은 모델은 여전히 20만(200K) 토큰이다.[^ctx-windows-api] 윈도우가 1M으로 늘어도 공식 문서는 "더 많은 컨텍스트가 자동으로 더 나은 것은 아니다 — 토큰 수가 늘어날수록 정확도와 recall이 저하되는 현상(context rot)이 있다"고 명시한다.[^ctx-windows-api] 즉 "윈도우가 크니 다 넣어도 된다"는 결론은 윈도우 크기와 무관하게 성립하지 않으며, [Context rot](./context-rot.md)에서 다룬 근거가 여기서도 그대로 적용된다.

### 2. 프리로딩(+캐싱)이 유리한 조건

반대로 코퍼스가 작고 안정적이며, 여러 요청이 같은 문서 집합을 반복 참조한다면 프리로딩이 유리하다. 이 조건은 [프롬프트 캐싱 기초](../04-caching/prompt-caching-basics.md)와 [캐시 비용·경제성](../04-caching/cache-metrics-economics.md)에서 다루는 프롬프트 캐싱의 전제 조건과 정확히 겹친다 — 캐시가 효과를 내려면 프리픽스가 안정적이고 반복 재사용돼야 하는데, 이는 곧 "미리 넣어도 낭비가 적은 데이터"의 정의와 같다. Anthropic의 계측에 따르면 프롬프트 캐싱이 적용된 반복 요청은 새 입력 토큰 비용의 최대 90%를, 지연은 최대 85%를 줄인다.[^prompt-cache] 코퍼스 전체를 캐싱 가능한 접두사로 프리로딩해두고, 그 위에 짧은 질의만 얹는 구성은 "매 요청마다 검색 도구를 호출"하는 JIT 구성보다 지연과 비용 모두에서 유리할 수 있다.

이 판단은 [GraphRAG·agentic RAG, 그리고 쓰지 말아야 할 때](../06-vector-search/graphrag-agentic-when-not.md)에서 다루는 "검색 자체를 에이전틱하게 만들 것인가"라는 상위 질문과 맞닿아 있다. 코퍼스가 프리로딩 가능한 크기라면, 애초에 검색 인프라(벡터 스토어, agentic RAG 루프)를 두는 비용 자체를 피할 수 있다는 것이 그 챕터의 핵심 논지 중 하나다.

### 3. 하이브리드: "핵심은 미리, 나머지는 검색"

실무에서 순수 JIT나 순수 프리로딩보다 흔한 구성은, 항상 필요한 소량의 "핵심 컨텍스트"(예: 스키마 요약, 코딩 컨벤션, 자주 참조되는 API 목록)는 시스템 프롬프트나 세션 시작 시점에 프리로딩하고, 나머지 대용량·저빈도 데이터는 도구 호출로 JIT 조회하는 방식이다.

Anthropic API의 tool search tool은 이 하이브리드를 도구 정의(스키마)라는 좁은 영역에 그대로 구현한 공식 기능이며, 문서 자체가 "tool search's on-demand loading is also an instance of the broader just-in-time retrieval principle"이라고 명시한다.[^tool-search] 문서가 제시하는 수치는 이 장의 핵심 주장 — JIT가 컨텍스트 사용량을 줄이지만 그 이득은 코퍼스(여기서는 도구 카탈로그) 크기에 비례한다 — 을 정확히 뒷받침한다.

- GitHub, Slack, Sentry, Grafana, Splunk를 묶은 일반적인 멀티서버 구성은 Claude가 아무 작업도 하기 전에 도구 정의만으로 약 55,000 토큰을 소비한다.[^tool-search]
- 도구 검색(tool search)을 켜면 이 소비를 일반적으로 85% 이상 줄이고, 요청당 필요한 3~5개 도구만 로드한다.[^tool-search]
- 도구가 30~50개를 넘어서면 Claude의 도구 선택 정확도 자체가 떨어지기 시작한다 — 이는 [Part 3의 툴 과부하](../03-accuracy-eval/tool-overload.md)와 정확히 같은 현상을 도구 스키마의 컨텍스트 점유라는 관점에서 재확인하는 수치다.[^tool-search]
- 공식 가이드라인: 도구가 10개 미만이고 모든 도구가 매 요청에 실제로 쓰이며 전체 정의가 100토큰 미만이면 표준 방식(전체 프리로딩)이 더 적합하고, 그 반대(10개 이상, 10K 토큰 초과, 200개 이상의 MCP 서버 도구 등)면 tool search를 쓰라고 명시한다.[^tool-search]

이는 "코퍼스가 작으면 프리로딩, 크면 JIT"라는 이 장의 결정 기준을 도구 스키마라는 좁은 영역에 정량적으로 적용한 사례다. Claude Code 역시 같은 원리를 적용해 MCP 도구를 기본적으로 이름만 나열한 지연 상태로 두고, 실제로 필요할 때만 전체 스키마를 컨텍스트로 불러온다.[^ctx-window]

### 4. 토큰 예산 배분: 구성요소별 경쟁

컨텍스트 윈도우는 최소한 다음 구성요소가 경쟁하는 공유 자원이다: 시스템 프롬프트, 툴 정의(스키마), 메모리(CLAUDE.md 등 영속 지시문), 검색된 문서(retrieved docs), 대화 이력(conversation). [컨텍스트 엔지니어링 원칙](./context-engineering-discipline.md)이 각 구성요소에 대한 질적 원칙(시스템 프롬프트는 명확하고 간결하게, 툴은 중복 없이 등)을 제시했다면, 이 장은 그 원칙을 "그래서 실제로 몇 토큰씩 배분할 것인가"라는 정량적 질문으로 이어간다.

정답이 정해진 배분표는 없다 — 워크로드마다 시스템 프롬프트의 복잡도, 필요한 툴의 개수, 검색 코퍼스의 크기가 전혀 다르기 때문이다. 다만 Claude Code는 자신의 기본 세션이 사용자가 아무것도 입력하기 전에 이미 무엇을 얼마나 소비하는지를 공개 문서로 예시화해 두었다.[^ctx-window] 아래 표는 그 예시(200K 토큰 윈도우 기준, "illustrative"라고 문서가 직접 명시한 값)를 그대로 인용한 것이다.

| 구성요소 | 토큰(예시) | 비중(전체 200K 대비) |
|---|---|---|
| 시스템 프롬프트 | 4,200 | 2.1% |
| 프로젝트 `CLAUDE.md` | 1,800 | 0.9% |
| 사용자 전역 `~/.claude/CLAUDE.md` | 320 | 0.16% |
| 자동 메모리(`MEMORY.md`) | 680 | 0.34% |
| 환경 정보(작업 디렉터리, OS 등) | 280 | 0.14% |
| MCP 툴 이름 목록(스키마는 지연 로드) | 120 | 0.06% |
| 스킬 설명 목록 | 450 | 0.23% |
| **시작 시점 합계** | **~7,850** | **~3.9%** |

이 표는 특정 프로젝트의 예시일 뿐 일반 규범이 아니다. 문서 자체가 "실제 값은 CLAUDE.md 크기, MCP 서버 구성, 파일 길이에 따라 달라진다"고 명시한다.[^ctx-window] 여기서 얻어야 할 실무적 시사점은 절대 숫자가 아니라 구조다: 세션이 시작되기도 전에 전체 윈도우의 몇 %가 이미 소비되고, 그중 대부분(시스템 프롬프트, 스킬 목록, 메모리)이 "매 세션 자동으로 재발생하는 고정비"라는 것이다. 검색된 문서와 대화 이력을 위한 예산은 이 고정비를 뺀 나머지에서 나온다.

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> 아래 "예시 배분(참고용)" 표는 위 Claude Code 실측치를 참고해, 시스템 프롬프트/툴/메모리/검색 문서/대화 이력이라는 5개 범주로 재구성한 저자의 예시적 재배분이다. Anthropic이나 다른 프레임워크가 이 5범주 자체를 공식적으로 규정하거나 퍼센트 기준을 발표한 바는 확인되지 않았다. 자신의 워크로드에 맞게 반드시 재측정하라.

| 구성요소 | 예시 배분(200K 윈도우 기준) | 근거/원칙 |
|---|---|---|
| 시스템 프롬프트 | 2,000~5,000 토큰 (~1~2.5%) | 짧고 명확하게 유지. Claude Code 실측 4,200 토큰이 상한선에 가까운 참고치.[^ctx-window] |
| 툴 정의(스키마) | 필요한 것만: 도구 10개 미만·전체 정의 100토큰 미만이면 상시 로드, 10개 이상이거나 10K 토큰을 넘으면 지연 로드 | Anthropic API 공식 가이드라인(tool search tool 사용 기준)을 프리로딩 vs JIT 전환 기준으로 재사용.[^tool-search] |
| 메모리(영속 지시문) | 1,000~3,000 토큰 (~0.5~1.5%) | 프로젝트 `CLAUDE.md`는 200줄 이내로 유지하고 참조성 내용은 스킬/경로 스코프 규칙으로 이전.[^ctx-window] |
| 검색된 문서(retrieved) | 남는 예산의 절반 이상, 세션 성격에 따라 가변 | JIT/프리로딩 선택 자체가 이 범주의 크기를 결정 — 이 장의 "결정 표" 참고 |
| 대화 이력 | 나머지, compaction 임계치에 도달하면 하류 대책으로 처리 | [Compaction과 요약](./compaction-summarization.md) 참고 |

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 코퍼스가 작고 안정적이며 반복 재사용됨(예: 고정된 API 레퍼런스, 소규모 사내 위키) | 프리로딩 + 프롬프트 캐싱 | 검색 왕복 지연 제거, 캐시 적중 시 입력 비용 최대 90%·지연 최대 85% 절감[^prompt-cache] | 코퍼스가 갱신되면 캐시 무효화, 관련 없는 부분도 컨텍스트에 상시 존재 |
| 코퍼스가 크고 매 요청마다 필요한 부분이 다름(대형 코드베이스, 방대한 문서 저장소) | JIT 검색(에이전틱 도구 호출) | 필요한 만큼만 컨텍스트에 적재, context rot 위험 최소화 | 매 조회마다 도구 호출 왕복 지연 추가[^ctx-eng] |
| 소량의 핵심 컨텍스트는 항상 필요하고, 나머지는 저빈도·대용량 | 하이브리드(핵심 프리로딩 + 나머지 JIT) | 고정비를 캐싱하면서 나머지는 필요할 때만 부담 | 경계 설정(무엇을 "핵심"으로 볼지) 자체가 설계 판단이 되어 재조정 필요 |
| 도구가 10개 미만이고 전부 매 요청에 쓰이며 전체 정의가 100토큰 미만 | 스키마 상시 로드(표준 tool calling) | 소규모라면 지연 도구 검색의 왕복 비용이 적재 비용보다 큼[^tool-search] | 도구 수가 늘면 이 임계값을 넘어 재검토 필요 |
| 도구가 10개 이상, 정의가 10K 토큰 초과, 또는 여러 MCP 서버를 묶어 200개 이상의 도구를 노출 | tool search tool로 지연 로드(`defer_loading: true`) + 검색 시 확장 | 미사용 스키마가 컨텍스트를 차지하지 않음, 55K→85%+ 절감 사례처럼 대규모일수록 효과가 큼[^tool-search] | 도구를 처음 발견하는 시점에 검색 왕복 지연 발생, 캐시 프리픽스는 보존됨[^tool-search] |
| 멀티에이전트로 탐색을 병렬화해 지연을 상쇄하려 함 | 서브에이전트 병렬 JIT 검색 | 개별 조회 지연을 병렬화로 흡수 | 토큰 사용량 최대 15배 증가[^multi-agent], 오케스트레이션 복잡도 |
| 이미 컨텍스트가 임계치에 근접, 배분 조정으로는 부족 | [Compaction](./compaction-summarization.md) | 마지막 방어선 | 정보 손실 위험 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 매 턴마다 같은 문서를 다시 검색해 지연과 비용이 누적됨 | JIT 검색만 쓰고, 반복 조회되는 안정적 데이터를 캐싱/프리로딩으로 전환하지 않음 | 도구 호출 로그에서 동일 쿼리·동일 문서에 대한 반복 조회 빈도 확인 | 반복률이 높은 데이터를 식별해 세션 시작 시 프리로딩하거나 캐시 레이어 추가 |
| 프리로딩한 문서 대부분이 실제 응답에 쓰이지 않음 | 코퍼스 크기·질문 다양성을 재는 대신 "안전하게 다 넣자"는 관성으로 프리로딩 결정 | 프리로딩된 토큰 중 응답 생성에 실제로 인용/참조된 비율 측정 | 사용률이 낮은 부분을 JIT 검색으로 전환하거나, 검색 인덱스를 새로 구성 |
| JIT 검색 도입 후 응답 지연이 SLO를 초과 | 검색 도구 왕복이 매 턴 순차적으로 발생, 병렬화되지 않음 | 트레이스에서 도구 호출 체인의 순차 vs 병렬 비율 확인 | 독립적인 조회는 병렬 도구 호출로 묶기([Tool round-trip](../02-performance/tool-roundtrips.md) 참고), 필요하면 서브에이전트로 분산 |
| 툴 스키마를 전부 상시 로드해서 시스템/툴 예산이 대화 이력 예산을 잠식 | 도구 수가 늘어나는데도 지연 로드(deferred)로 전환하지 않음 | 시작 시점 컨텍스트에서 툴 정의가 차지하는 토큰 비율 측정 | 도구가 10개 이상이거나 정의가 10K 토큰을 넘으면 지연 로드/도구 검색으로 전환[^tool-search] |
| 하이브리드 구성에서 "핵심 컨텍스트"에 실제로는 저빈도 데이터가 섞여 들어가 항상 로드됨 | 프리로딩 대상 선정 기준이 없고 최초 설계 이후 재검토가 없음 | 프리로딩 항목별 참조 빈도를 주기적으로 재측정 | 참조 빈도가 낮아진 항목을 JIT 대상으로 이전, 주기적 재분류 프로세스 도입 |
| 캐시 프리픽스에 검색 결과를 포함시켰다가 검색 결과가 매번 달라져 캐시가 계속 무효화됨 | 프롬프트 캐싱과 JIT 검색을 구조적으로 분리하지 않음(검색 결과가 캐시 가능한 접두사 뒤가 아니라 앞이나 중간에 삽입) | 캐시 적중률 지표가 검색 결과 포함 턴에서 급락하는지 확인([캐시 비용·경제성](../04-caching/cache-metrics-economics.md) 참고) | 안정적인 접두사(시스템 프롬프트, 정적 메모리)와 가변적인 검색 결과의 배치 순서를 분리 — 캐시 가능한 부분을 항상 앞에 고정 |

## 안티패턴

- ❌ "컨텍스트 윈도우가 넉넉하니 관련 있을 만한 문서를 다 프리로딩한다" → ✅ 코퍼스 크기와 요청당 재사용률을 먼저 측정하고, 재사용률이 낮으면 JIT로 전환한다.
- ❌ 모든 검색을 JIT 도구 호출로만 처리하고 반복 조회 패턴을 방치한다 → ✅ 반복 조회되는 안정적 데이터는 프리로딩·캐싱으로 옮겨 지연과 비용을 동시에 줄인다.
- ❌ 툴 스키마를 습관적으로 전부 상시 로드한다 → ✅ 도구 수(10개)·정의 크기(10K 토큰) 임계값을 넘으면 지연 로드로 전환한다.[^tool-search]
- ❌ 토큰 예산 배분표를 한 번 정하고 재검토하지 않는다 → ✅ 워크로드가 바뀌면(툴 수 증가, 코퍼스 확장) 배분을 다시 측정한다 — 이 장의 표는 특정 시점의 예시일 뿐 고정값이 아니다.
- ❌ JIT 검색을 순차적으로 여러 번 호출하면서 지연 누적을 인지하지 못한다 → ✅ 독립적인 조회는 병렬화하고, 지연 예산을 [Part 2](../02-performance/tool-roundtrips.md)의 SLO와 함께 계측한다.

## 계측 (SLI)

- **검색 재사용률**: 동일 쿼리/문서에 대한 반복 조회 비율. 높을수록 프리로딩·캐싱으로 전환할 여지가 크다는 신호.
- **프리로딩 활용률**: 프리로딩된 토큰 중 실제 응답에 인용·참조된 비율. 낮으면 프리로딩 범위가 과도하다는 신호.
- **JIT 조회당 추가 지연**: 검색 도구 호출 1회가 전체 턴 지연에 추가하는 시간(P50/P95). [Tool round-trip](../02-performance/tool-roundtrips.md)의 지연 계측과 같은 방식으로 측정.
- **시작 시점 고정비 비율**: 사용자가 첫 입력을 하기 전에 이미 소비된 토큰이 전체 윈도우에서 차지하는 비율(Claude Code는 `/context`로 실시간 확인 가능[^ctx-window]).
- **캐시 적중률과 JIT 검색 도입의 상호작용**: JIT 검색 결과가 캐시 가능한 접두사를 얼마나 자주 무효화시키는지([캐시 비용·경제성](../04-caching/cache-metrics-economics.md) 참고).

## 체크리스트

- [ ] 프리로딩 대상 코퍼스의 크기와 요청당 재사용률을 측정했는가?
- [ ] 반복 조회되는 안정적 데이터를 식별해 캐싱/프리로딩으로 전환했는가?
- [ ] 툴 스키마 전체 크기가 윈도우의 몇 %를 차지하는지 확인하고, 지연 로드 전환 여부를 판단했는가?
- [ ] JIT 검색 도입으로 늘어난 지연이 SLO 내에 있는지 계측했는가?
- [ ] 독립적인 검색 호출을 병렬화했는가?
- [ ] 프리로딩과 JIT 검색을 병행할 때, 캐시 가능한 접두사와 가변적 검색 결과의 배치 순서를 분리했는가?
- [ ] 토큰 예산 배분(시스템/툴/메모리/검색 문서/대화 이력)을 워크로드 변화에 맞춰 주기적으로 재측정하는가?
- [ ] 시작 시점 고정비(시스템 프롬프트, 메모리, 스킬 목록 등)를 파악하고, 그것이 전체 예산에서 과도한 비중을 차지하지 않는지 확인했는가?

## 참고

[^ctx-eng]: Anthropic, ["Effective context engineering for AI agents"](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), Anthropic Engineering Blog — JIT 검색 vs 사전 계산의 트레이드오프, 하이브리드 전략, "가장 작은 고신호 토큰 집합" 원칙의 출처.
[^multi-agent]: Anthropic, ["How we built our multi-agent research system"](https://www.anthropic.com/engineering/built-multi-agent-research-system), Anthropic Engineering Blog (2025년 6월 13일 게시) — "멀티에이전트 시스템은 채팅 대비 약 15배 더 많은 토큰을 쓴다"는 수치의 출처.
[^prompt-cache]: Anthropic, ["Prompt caching with Claude"](https://www.anthropic.com/news/prompt-caching), Anthropic News.
[^ctx-window]: Claude Code Docs, ["Explore the context window"](https://code.claude.com/docs/en/context-window) — 세션 시작 시점의 예시 토큰 구성(시스템 프롬프트 4,200, 프로젝트 CLAUDE.md 1,800 등), MCP 툴 지연 로드, `/compact` 이후 재주입/소실 항목, `/context` 실시간 확인.
[^tool-search]: Anthropic, ["Tool search tool"](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), Claude Docs — 멀티서버 도구 정의 약 55K 토큰과 tool search 적용 시 85%+ 절감 사례, 도구 30~50개 초과 시 선택 정확도 저하, `defer_loading` 사용 기준(10개 미만·100토큰 미만은 표준 방식, 10개 이상·10K 토큰 초과·200개 이상 MCP 도구는 tool search).
[^ctx-windows-api]: Anthropic, ["Context windows"](https://platform.claude.com/docs/en/build-with-claude/context-windows), Claude Docs — 모델별 컨텍스트 윈도우 크기(1M vs 200K), `<budget:token_budget>`/`<system_warning>` 컨텍스트 인지(context awareness) 주입 메커니즘, context rot 인용.
[^latency-rag]: ⚠️ 비공식. Bound Dev, ["Latency budget for a RAG pipeline"](https://www.boundev.ai/blog/latency-budget-rag-pipeline).
[^latency-agent]: ⚠️ 비공식. Kunal Ganglani, ["AI agent latency optimization budget"](https://www.kunalganglani.com/blog/ai-agent-latency-optimization-budget).

- [컨텍스트 엔지니어링 원칙](./context-engineering-discipline.md) — "가장 작은 고신호 토큰 집합"이라는 이 장의 상위 원칙
- [Context rot](./context-rot.md) — 프리로딩이 과도할 때 감당해야 하는 근본 위험
- [Compaction과 요약](./compaction-summarization.md) — 배분 조정으로도 부족할 때의 하류 대책
- [컨텍스트 격리와 오프로딩](./context-isolation-offloading.md) — JIT 검색의 파일시스템 오프로딩 버전
- [Tool round-trip](../02-performance/tool-roundtrips.md), [지연의 해부](../02-performance/latency-anatomy.md) — JIT 검색이 얹는 지연의 구조
- [프롬프트 캐싱 기초](../04-caching/prompt-caching-basics.md), [캐시 비용·경제성](../04-caching/cache-metrics-economics.md) — 프리로딩이 유리해지는 조건과 경제성
- [GraphRAG·agentic RAG, 그리고 쓰지 말아야 할 때](../06-vector-search/graphrag-agentic-when-not.md) — 검색 자체를 에이전틱하게 만들지에 대한 상위 판단
- [Manage tool context](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context), [Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching) — 툴 정의 자체의 컨텍스트 점유를 줄이는 보완 기법
