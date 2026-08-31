---
title: 툴 라운드트립
description: 툴 호출 1회가 LLM 왕복 1회라는 구조적 비용을 분해하고, 라운드트립 수를 줄이는 네 가지 레버와 운영 상한 설계를 다룬다.
outline: [2, 3]
---

# 툴 라운드트립

::: tip 이 장에서 얻는 것
- 툴 호출 1회 = **LLM 왕복(roundtrip) 1회**라는 구조적 등식을 이해한다 — 모델이 `tool_use` 블록을 생성하고, 클라이언트가 툴을 실행하고, `tool_result`를 붙여 모델을 **다시 호출**하는 루프의 각 반복이 네트워크 + prefill + 디코딩 비용을 새로 치른다.[^tool-use][^bedrock-tool]
- 라운드트립 수 자체를 줄이는 네 가지 레버 — ① 툴 통합(consolidation), ② code execution 패턴, ③ 병렬 툴 호출, ④ 후속 질의를 제거하는 응답 설계 — 를 언제 어떤 순서로 적용할지 판단하는 기준을 얻는다.
- 에이전트 루프의 라운드트립 횟수 분포를 SLI로 계측하고, AgentCore harness의 `maxIterations` 같은 **상한을 정책으로 강제**하는 운영 관점을 익힌다.[^harness-api]
:::

## 왜 문제가 되는가

에이전트 프레임워크가 툴 호출을 함수 호출처럼 보이게 감싸주기 때문에, 많은 팀이 툴 호출을 "마이크로서비스 간 RPC 한 번" 정도의 비용으로 착각한다. 실제 프로토콜은 다르다. Anthropic API 기준으로 툴 호출은 다음 시퀀스다.[^tool-use]

1. 클라이언트가 툴 정의와 대화 이력을 담아 모델을 호출한다.
2. 모델이 툴을 쓰기로 결정하면 `stop_reason: "tool_use"`와 함께 툴 이름·입력이 담긴 `tool_use` 블록을 **생성(디코딩)**한다.
3. 클라이언트가 그 입력으로 툴을 실제 실행한다.
4. 실행 결과를 `tool_result` 블록으로 대화에 붙여 **모델을 처음부터 다시 호출**한다.

Amazon Bedrock Converse API도 구조는 동일하다 — `stopReason`이 `tool_use`면 클라이언트가 `toolResult`를 담은 user 메시지를 추가해 재호출한다.[^bedrock-tool] 즉 **툴 호출 1회 = 추론 요청 1회 추가**이고, 각 요청은 다음 비용을 새로 치른다.

- **네트워크 + 큐잉**: 게이트웨이·리전 경유 포함 왕복 지연.
- **Prefill**: 시스템 프롬프트, 툴 정의, 지금까지의 전체 대화 이력(과거의 모든 `tool_use`/`tool_result` 포함)이 매 요청 **재전송·재처리**된다. prompt caching이 이 비용을 크게 완화하지만(캐시 read는 기본 입력 단가의 0.1배, 지연도 최대 85%까지 감소[^cache-pricing][^cache-news]) 없애지는 못한다 — 캐시 설계는 [프롬프트 캐싱 기초](../04-caching/prompt-caching-basics.md)에서 다룬다.
- **디코딩**: `tool_use` 블록의 JSON 입력을 토큰 단위로 생성하는 시간. 큰 payload를 파라미터로 넘기는 툴일수록 이 비용이 커진다.
- **툴 실행 시간**: 툴이 감싼 백엔드 API의 실제 지연. 에이전트 루프 관점에서는 이 시간 동안 모델이 "멈춰" 있다.

여기서 곱셈이 발생한다. 라운드트립이 N번이면 위 비용이 N번 **직렬로** 누적되고, 대화 이력은 매 반복 커지므로 prefill 입력량은 반복이 진행될수록 단조 증가한다. 캐시가 없다면 총 입력 토큰 처리량은 라운드트립 수에 대해 대략 제곱으로 자란다(매 반복이 이전까지의 전체 이력을 다시 읽으므로). 사용자 체감으로는 "에이전트가 5초씩 열 번 생각하는" 세션이 되고, 비용으로는 같은 컨텍스트를 열 번 청구받는 세션이 된다. 지연 전체의 분해는 [지연의 해부](./latency-anatomy.md)에서, 이 장은 그중 **라운드트립 수라는 곱셈 인자**에 집중한다.

이것이 플랫폼 문제인 이유: 개별 에이전트 개발자는 자기 툴 하나의 실행 시간만 본다. 라운드트립 수의 분포, 반복당 토큰 증가율, 캐시 적중과의 상호작용은 **플랫폼이 계측하지 않으면 아무도 보지 못하는 지표**다.

## 핵심 개념

### 라운드트립의 단가 공식

한 세션의 지연을 근사하면:

```
T_session ≈ Σ_{i=1..N} [ T_net(i) + T_prefill(i) + T_decode(i) + T_tool(i) ]
```

- `N` = 라운드트립 수. 이 장의 최적화 대상.
- `T_prefill(i)` = i번째 반복의 입력 처리 시간. 이력 누적으로 i에 따라 증가하며, prompt caching이 캐시된 프리픽스 구간을 크게 줄인다.[^cache-pricing]
- `T_tool(i)` = 툴 실행 시간. 모델과 무관하게 플랫폼이 직접 최적화할 수 있는 유일한 항.

최적화 레버는 두 축이다: **N을 줄이거나**(이 장의 ①②④와 다음 장의 병렬화), **항 각각을 줄이거나**(캐싱, 툴 실행 최적화). N을 줄이는 것이 우선인 이유는 N이 나머지 모든 항의 곱셈 계수이기 때문이다.

### 레버 ①: 툴 통합 — 잘게 쪼갠 툴을 작업 단위로 합친다

가장 흔한 라운드트립 낭비는 REST 엔드포인트를 1:1로 툴로 노출한 설계에서 나온다. Anthropic의 "Writing effective tools for agents"는 자주 연쇄되는 멀티스텝 작업을 하나의 상위 툴로 통합하라고 권고한다 — `list_users` → `list_events` → `create_event` 세 번의 왕복 대신, 내부에서 가용 시간 조회까지 처리하는 `schedule_event` 하나로.[^tool-writing] 라운드트립 3회가 1회가 되고, 중간 응답(사용자 목록 전체, 이벤트 목록 전체)이 컨텍스트에 쌓이는 비용도 함께 사라진다.

통합의 기준은 "에이전트가 수행하는 태스크의 단위"다. 백엔드 API의 단위가 아니다. 트레이드오프와 통합의 한계(만능 툴의 hallucinated argument 문제)는 [툴 설계](../01-agent-design/tool-design.md)에서 계약(contract) 관점으로 상세히 다뤘다 — 이 장의 관점에서 기억할 것은, 툴 설계 리뷰에서 "이 툴 뒤에 거의 항상 따라오는 툴이 있는가?"를 물으면 통합 후보가 곧 라운드트립 절감 후보라는 점이다.

### 레버 ②: code execution — 여러 툴 호출을 한 번의 코드 실행으로

툴 통합이 서버 쪽 리팩터링이라면, code execution 패턴은 호출 방식 자체를 바꾼다. MCP 서버의 툴들을 모델에게 호출 가능한 tool 목록으로 주는 대신 실행 환경 안의 코드 API로 제시하고, 모델은 **여러 툴 호출을 오케스트레이션하는 코드 한 덩어리**를 작성해 한 번에 실행한다.[^code-exec] 루프·조건분기·중간 데이터 전달이 전부 실행 환경 안에서 일어나므로, 직접 tool call이라면 N번의 라운드트립이 필요했을 작업이 **코드 실행 1~2회의 라운드트립**으로 끝난다. 중간 결과가 모델 컨텍스트를 우회하는 효과도 크다 — Anthropic의 예시 시나리오에서 15만 토큰이 2천 토큰으로 줄었다(98.7% 절감).[^code-exec]

단, 샌드박스 구축·운영 부담이라는 대가가 있다. 언제 직접 호출로 충분하고 언제 code execution으로 넘어가야 하는지, 그리고 AgentCore Gateway/Code Interpreter와의 조합은 [MCP 서버 설계](../01-agent-design/mcp-server-design.md)에서 정본으로 다뤘다.

### 레버 ③: 병렬 툴 호출 — N을 줄이는 게 아니라 직렬 구간을 접는다

모델은 한 응답 안에 **여러 개의 `tool_use` 블록**을 낼 수 있고, 클라이언트가 이를 동시에 실행해 결과들을 한 번의 재호출에 모아 붙이면 독립적인 툴 호출 k개가 라운드트립 1회로 접힌다.[^parallel] 이는 툴 간 의존성이 없을 때만 성립하며, 클라이언트 하니스가 병렬 실행을 실제로 지원해야 한다(순차 실행하면 지연 이득이 사라진다). 스트리밍과 병렬 툴 호출의 구현 디테일 — 병렬 유도 프롬프팅, 부분 실패 처리, 스트리밍과의 결합 — 은 다음 장 [스트리밍과 병렬 툴 호출](./streaming-parallel-tools.md)의 정본 주제이므로 여기서는 레버의 존재만 짚는다.

### 레버 ④: 후속 질의를 제거하는 응답 설계

라운드트립 로그를 보면 "툴 응답이 부족해서 발생한 추가 호출"이 반복적으로 나타난다. 전형적인 패턴:

- 검색 툴이 ID 목록만 반환 → 모델이 각 ID마다 상세 조회 툴을 다시 호출.
- 생성 툴이 성공 여부만 반환 → 모델이 방금 만든 리소스의 ID나 URL을 얻으려고 조회 툴을 호출.
- 페이지네이션 기본 page size가 작음 → 모델이 다음 페이지를 계속 요청.

해법은 **다음 턴에 필요할 정보를 응답에 미리 포함**하는 것이다. 생성 응답에 리소스 ID·정규 URL·핵심 필드를 담고, 검색 응답에 후속 판단에 충분한 요약 필드를 담는다. 이는 [툴 설계](../01-agent-design/tool-design.md)에서 다룬 "응답은 모델에게 되돌아가는 입력" 원칙의 라운드트립 버전이다. 단, 반대 방향의 압력도 있다 — 응답을 무한정 살찌우면 컨텍스트 비용이 커진다. Anthropic이 제시하는 `response_format: "concise" | "detailed"` 같은 상세도 파라미터가 이 긴장의 실용적 절충이다(concise는 detailed 대비 약 1/3 토큰).[^tool-writing] 판단 기준: **"이 필드가 없으면 모델이 추가 라운드트립을 하는가?"** — 그렇다면 포함하고, 아니면 뺀다.

### 툴 실행 시간: N이 아니라 T_tool을 줄이는 축

라운드트립 수를 다 줄여도 `T_tool`이 크면 소용없다. 툴이 느린 외부 API를 감싸고 있다면 플랫폼 레벨에서:

- **툴 결과 캐싱**: 같은 입력에 같은 결과를 주는 조회형 툴(환율, 문서 검색, 스키마 조회)은 툴 서버 앞단에 TTL 캐시를 둔다. 이는 프롬프트 캐시와 별개의, 평범한 애플리케이션 캐시다.
- **타임아웃 + 유용한 에러**: 툴에 명시적 타임아웃을 걸고, 타임아웃 시 모델에게 "재시도할 가치가 있는지"를 알려주는 에러 메시지를 반환한다. 타임아웃 없는 툴 하나가 세션 전체를 하니스의 `timeoutSeconds`까지 매달아 둔다(이 책 데모 harness는 120초로 설정 — `demo/builder-harness/create-harness.json`).
- **응답 크기 상한**: 거대한 툴 응답은 그 자체로 다음 반복의 prefill을 부풀린다. Claude Code가 MCP 툴 응답을 기본 25,000 토큰으로 제한하는 것이 참고할 만한 정책 사례다.[^cc-mcp]

### 운영 상한: maxIterations

라운드트립 수는 최적화 대상이기 이전에 **폭주 방지 대상**이다. 에이전트 루프는 환경 피드백을 보고 스스로 다음 행동을 정하는 구조라서,[^building-agents] 잘못된 에러 메시지나 순환하는 툴 결과를 만나면 같은 호출을 무한히 반복할 수 있다. 상한이 없으면 한 세션이 토큰 예산과 지연 SLO를 동시에 태운다.

Amazon Bedrock AgentCore harness는 이를 1급 설정으로 노출한다: `CreateHarness`의 `maxIterations`는 "invocation당 에이전트 루프가 실행할 수 있는 최대 반복 횟수"이며, `UpdateHarness`로 변경하거나 invocation 단위로 오버라이드할 수 있다.[^harness-api][^harness-ops] 이 책의 데모 harness는 `maxIterations: 10`으로 설정했다(`demo/builder-harness/create-harness.json`) — 명확화 질문을 주고받는 빌더 에이전트 특성상 10회면 충분하고, 그 이상은 루프 이상 신호로 간주한다는 판단이다.

상한 값을 정하는 방법은 추측이 아니라 분포다: 성공 세션의 라운드트립 p99를 계측하고, 그보다 여유 있게 높되 폭주를 조기에 끊는 값으로 잡는다. 그리고 **상한 도달을 성공으로 위장하지 않는 것**이 중요하다 — `maxIterations` 소진으로 끝난 세션은 별도 종료 사유로 태깅해 알람 대상으로 삼아야 한다.

::: warning 미정착 영역
라운드트립 자체를 프로토콜 레벨에서 줄이려는 시도들 — 툴 결과를 모델이 코드로 직접 소비하는 programmatic tool calling, 서버 측에서 에이전트 루프를 대신 돌려주는 managed loop API 등 — 은 벤더별로 이름·범위·과금 방식이 아직 수렴하지 않았다. 이 장의 네 가지 레버는 클라이언트가 루프를 소유하는 현재의 지배적 구조를 전제하며, 루프 소유권이 서버로 넘어가는 제품을 채택할 때는 계측 포인트(반복 수·반복당 토큰)를 벤더가 노출하는지부터 확인하라.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 특정 툴 A 뒤에 거의 항상 툴 B·C가 따라옴 | 툴 통합 (레버 ①) | 고정 시퀀스는 모델 판단이 필요 없음 — 왕복 3회를 1회로[^tool-writing] | 툴 서버 리팩터링 필요, 만능 툴화 시 오호출 증가 |
| 툴 카탈로그가 크고, 호출 횟수·순서가 데이터에 따라 가변 | code execution 패턴 (레버 ②) | 루프·분기를 실행 환경 안으로 이동, 라운드트립 1~2회로 압축[^code-exec] | 샌드박스 구축·운영 부담 — [MCP 서버 설계](../01-agent-design/mcp-server-design.md) 참조 |
| 서로 독립인 조회 여러 건 (예: 지표 3종 동시 조회) | 병렬 툴 호출 (레버 ③) | 한 응답의 다중 `tool_use`로 직렬 왕복을 1회로 접음[^parallel] | 의존성 있으면 불가, 하니스의 병렬 실행 지원 필요 — [다음 장](./streaming-parallel-tools.md) |
| 로그에 "생성 직후 조회", "ID로 재조회" 패턴 반복 | 응답 필드 보강 (레버 ④) | 후속 질의의 원인이 응답 결핍 | 응답 비대화 → 컨텍스트 비용, 상세도 파라미터로 절충[^tool-writing] |
| 라운드트립은 적은데 세션이 느림 | 툴 실행 최적화 (캐시·타임아웃) + [캐싱](../04-caching/prompt-caching-basics.md) | 병목이 N이 아니라 `T_tool`·`T_prefill` | 캐시 신선도 관리, 무효화 설계 |
| 일부 세션이 비정상적으로 긴 루프 | `maxIterations` 상한 + 종료 사유 태깅 | 폭주 세션이 예산·SLO를 태우기 전에 차단[^harness-api] | 정상적으로 긴 태스크가 잘릴 수 있음 — p99 기반으로 값 설정 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 단순 요청인데 세션 지연이 수십 초 | REST 엔드포인트 1:1 노출로 라운드트립 과다 | 세션당 라운드트립 수 분포(p50/p95)와 트레이스의 툴 시퀀스 확인 | 고정 시퀀스를 통합 툴로(레버 ①), 가변 시퀀스는 code execution 검토(레버 ②) |
| 라운드트립마다 비용·지연이 선형 이상으로 증가 | 이력 누적 + 캐시 미적중으로 prefill 전량 재처리 | 반복별 `cache_read_input_tokens` 비율 확인 — 반복 진행에도 낮으면 캐시 문제 | 캐시 브레이크포인트 재배치 — [캐시 미스 근본 원인](../04-caching/cache-miss-root-causes.md) |
| 같은 툴을 같은 인자로 반복 호출하다 상한 도달 | 에러 메시지가 복구 정보를 안 줘서 모델이 동일 재시도 | 트레이스에서 연속 동일 `tool_use` 블록 검출 | 에러 응답에 원인·대안 명시 — [툴 설계](../01-agent-design/tool-design.md)의 에러 계약 |
| 생성/변경 요청마다 조회 라운드트립이 따라붙음 | 툴 응답에 후속 턴에 필요한 ID·상태 누락 | 로그에서 "쓰기 직후 읽기" 시퀀스 빈도 집계 | 응답에 리소스 ID·핵심 필드 포함(레버 ④) |
| 특정 툴 호출 시 세션이 하니스 타임아웃까지 정지 | 느린 백엔드 API + 툴 타임아웃 부재 | 툴별 실행 시간 p95/p99, 타임아웃 발생률 | 툴 타임아웃 + 조회형 툴 결과 캐싱 + 재시도 가치 담은 에러 |
| `maxIterations` 도달 세션이 "완료"로 집계됨 | 종료 사유를 구분하지 않는 하니스/집계 로직 | 종료 사유별(정상 종료 vs 상한 소진 vs 타임아웃) 세션 분류 | 상한 소진을 별도 사유로 태깅·알람, 상한 도달률 SLI화 |
| 병렬 가능해 보이는 조회들이 항상 직렬 실행 | 하니스가 다중 `tool_use`를 순차 처리, 또는 모델이 병렬을 안 씀 | 응답당 `tool_use` 블록 수 분포 확인 | [스트리밍과 병렬 툴 호출](./streaming-parallel-tools.md)의 병렬 실행·유도 기법 |

## 안티패턴

- ❌ 백엔드 REST API를 엔드포인트 1:1로 툴 등록 → ✅ 에이전트 태스크 단위로 통합한 툴 설계, 툴 등록 리뷰에서 "뒤따르는 툴이 있는가"를 심사 항목으로.[^tool-writing]
- ❌ "캐시가 있으니 라운드트립은 공짜" → ✅ 캐시는 prefill **비용**을 0.1배로 줄일 뿐,[^cache-pricing] 네트워크·디코딩·툴 실행의 직렬 지연과 캐시 write/miss 비용은 라운드트립마다 남는다. N 감소가 우선.
- ❌ 페이지네이션 기본값을 백엔드 관행(page size 10 등)대로 노출 → ✅ 에이전트 소비 기준으로 기본 page size와 요약 수준을 설계해 "다음 페이지" 라운드트립을 구조적으로 제거.
- ❌ 상한 없는 에이전트 루프(`while (stopReason == "tool_use")`) → ✅ `maxIterations` 상한 + 상한 도달을 실패 사유로 태깅.[^harness-api]
- ❌ 느린 외부 API를 그대로 감싼 툴 → ✅ 툴 레벨 타임아웃 + 조회형 결과 캐시 + 복구 가능한 에러 메시지.
- ❌ 라운드트립 절감을 이유로 모든 것을 하는 파라미터 20개짜리 만능 툴 → ✅ 통합의 단위는 태스크 — 오호출·hallucinated argument가 늘면 재시도 라운드트립으로 되돌아온다([툴 설계](../01-agent-design/tool-design.md)).

## 계측 (SLI)

라운드트립은 트레이스 없이는 보이지 않는다. 최소 계측 세트:

- **세션당 라운드트립 수 분포** (p50/p95/p99): 이 장의 핵심 지표. 에이전트 버전·툴 카탈로그 변경 전후로 비교한다. 툴 통합이나 응답 보강의 효과는 이 분포의 좌측 이동으로 검증한다.
- **`maxIterations` 도달률**: 상한 소진으로 끝난 세션 비율. 0이 아니면 루프 이상(에러 계약 문제, 순환) 또는 상한이 너무 낮다는 신호. AgentCore harness는 CloudWatch 기반 관측과 비용 통제를 함께 제공한다.[^harness-ops]
- **반복 인덱스별 입력 토큰과 캐시 적중률**: i번째 반복의 `input_tokens` 대비 `cache_read_input_tokens` 비율. 반복이 진행될수록 적중률이 올라가야 정상이고, 그렇지 않으면 [캐시 미스 근본 원인](../04-caching/cache-miss-root-causes.md)을 의심한다.
- **툴별 실행 시간 p95/p99와 타임아웃률**: `T_tool` 축의 지표. 느린 툴 상위 목록은 캐싱·통합 후보 목록이기도 하다.
- **응답당 `tool_use` 블록 수**: 병렬화 활용도의 프록시. 항상 1이면 병렬 레버가 놀고 있는 것이다 — [다음 장](./streaming-parallel-tools.md) 참조.
- **"쓰기 직후 읽기" 시퀀스 빈도**: 레버 ④의 백로그를 자동 발굴하는 로그 쿼리. 특정 툴 쌍의 연속 출현이 잦으면 응답 보강 또는 통합 후보다.

## 체크리스트

- [ ] 세션당 라운드트립 수 분포(p50/p95/p99)를 대시보드로 보고 있는가?
- [ ] 에이전트 루프에 `maxIterations` 상한이 있고, 값은 성공 세션 p99 기반으로 산정했는가?
- [ ] 상한 도달·타임아웃이 정상 종료와 구분되는 종료 사유로 태깅·알람되는가?
- [ ] 툴 카탈로그에서 "거의 항상 연쇄되는 툴 시퀀스"를 주기적으로 발굴해 통합 후보로 리뷰하는가?
- [ ] 쓰기 툴의 응답이 후속 턴에 필요한 리소스 ID·핵심 필드를 포함하는가?
- [ ] 모든 툴에 타임아웃이 있고, 타임아웃 에러가 재시도 가치를 모델에게 알려주는가?
- [ ] 조회형(멱등) 툴에 결과 캐시를 검토했는가?
- [ ] 반복 인덱스별 캐시 적중률이 반복 진행에 따라 상승하는가?
- [ ] 툴 카탈로그가 크거나 중간 데이터가 큰 워크로드에 대해 code execution 패턴 전환을 평가했는가?
- [ ] 독립 조회들이 병렬 `tool_use`로 접히는지(응답당 블록 수) 확인했는가?

## 참고

- [Anthropic 공식 문서: Tool use with Claude](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) — `tool_use`/`tool_result` 루프, `stop_reason: "tool_use"`
- [Amazon Bedrock 공식 문서: Use a tool with the Converse API](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html) — Converse API의 `stopReason`/`toolResult` 루프
- [Anthropic Engineering: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) — 툴 통합, 응답 상세도 파라미터(concise ≈ 1/3 토큰)
- [Anthropic Engineering: Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) — 여러 툴 호출을 코드 실행으로 묶기, 15만→2천 토큰(98.7% 절감) 예시
- [Anthropic 공식 문서: Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — 캐시 read 0.1배 / write 1.25배 단가
- [Anthropic News: Prompt caching (2024-08-14)](https://www.anthropic.com/news/prompt-caching) — 긴 프롬프트 기준 비용 최대 90%·지연 최대 85% 절감
- [Anthropic Engineering: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — 환경 피드백 루프로서의 에이전트 정의
- [AWS 공식 문서: CreateHarness — Amazon Bedrock AgentCore Control Plane API](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html) — `maxIterations` 파라미터
- [AWS 공식 문서: AgentCore harness — Observability and cost controls](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html)
- [Claude Code Docs: Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp) — MCP 툴 응답 기본 상한 25,000 토큰
- 이 책 데모: `demo/builder-harness/create-harness.json` — `maxIterations: 10`, `timeoutSeconds: 120`

[^tool-use]: Anthropic, "Tool use with Claude" — 모델이 `stop_reason: "tool_use"`로 툴 사용을 요청하고, 클라이언트가 실행 결과를 `tool_result`로 붙여 대화를 이어가는 루프, <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>
[^bedrock-tool]: Amazon Bedrock, "Use a tool with the Converse API" — `stopReason`이 `tool_use`일 때 `toolResult`를 담은 메시지로 재호출, <https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html>
[^tool-writing]: Anthropic, "Writing effective tools for agents" — 툴 통합 권고(`schedule_event` 예시), `response_format` 상세도 파라미터, <https://www.anthropic.com/engineering/writing-tools-for-agents>
[^code-exec]: Anthropic, "Code execution with MCP: Building more efficient agents" — 툴을 코드 API로 제시해 오케스트레이션을 실행 환경으로 이동, 예시 시나리오에서 150,000 → 2,000 토큰(98.7% 절감), <https://www.anthropic.com/engineering/code-execution-with-mcp>
[^parallel]: Anthropic, "Tool use with Claude" — 한 응답에 여러 `tool_use` 블록을 반환하는 parallel tool use, <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>
[^cache-pricing]: Anthropic, "Prompt caching" — cache read는 기본 입력 단가의 0.1배, 5분 cache write는 1.25배, <https://platform.claude.com/docs/en/build-with-claude/prompt-caching>
[^cache-news]: Anthropic, "Prompt caching with Claude" (2024-08-14) — 긴 프롬프트 기준 비용 최대 90%, 지연 최대 85% 절감, <https://www.anthropic.com/news/prompt-caching>
[^cc-mcp]: Claude Code Docs, "Connect Claude Code to tools via MCP" — MCP 툴 응답 기본 상한 25,000 토큰(`MAX_MCP_OUTPUT_TOKENS`), <https://code.claude.com/docs/en/mcp>
[^building-agents]: Anthropic, "Building effective agents" — 에이전트는 환경 피드백을 근거로 스스로 다음 행동을 결정하는 루프, <https://www.anthropic.com/engineering/building-effective-agents>
[^harness-api]: AWS, "CreateHarness — Amazon Bedrock AgentCore Control Plane API Reference" — `maxIterations`: invocation당 에이전트 루프 최대 반복 횟수, <https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateHarness.html>
[^harness-ops]: AWS, "AgentCore harness — Observability and cost controls", <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-operations.html>
