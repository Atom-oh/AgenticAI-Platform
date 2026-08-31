---
title: 툴 과부하
description: 툴 노출 수가 늘어날수록 툴 선택 정확도가 떨어지는 구조적 문제와, 노출 수를 줄이는 네 가지 레버(semantic search, defer_loading, consolidation, code execution)를 정확도 관점에서 다룬다.
outline: [2, 3]
---

# 툴 과부하

::: tip 이 장에서 얻는 것
- 툴 수가 늘어날 때 선택 정확도가 떨어지는 두 가지 메커니즘 — 컨텍스트 점유(prompt bloat)와 결정 복잡도(decision complexity) — 의 구분
- Anthropic이 공식 문서에서 명시한 임계 신호(툴 30~50개 초과 시 선택 정확도 저하)와, 이 책이 일관되게 쓰는 운영 임계치(50개 전에 대응)의 관계
- 노출 수를 줄이는 네 가지 레버의 정확도 관점 비교: AgentCore Gateway semantic tool search, Anthropic tool search tool(`defer_loading`), 툴 통합(consolidation), code execution 패턴
- Cedar partial evaluation 기반 툴 필터링이 인가 장치이면서 동시에 정확도 장치인 이유
- 툴 선택 정확도를 회귀 감시 가능한 SLI로 만드는 계측 방법
:::

## 왜 문제가 되는가

에이전트 플랫폼에서 툴 카탈로그는 단조 증가한다. 팀마다 MCP 서버를 하나씩 붙이고, 기존 REST API를 Gateway로 노출하고, 편의 툴을 추가하다 보면 어느 시점부터 "에이전트가 엉뚱한 툴을 고른다", "예전에는 잘 되던 태스크가 안 된다"는 리포트가 늘기 시작한다. [Part 0의 6대 통증](../00-intro/six-pain-points.md) 중 "정확도 떨어짐"의 첫 번째 근본 원인으로 지목한 것이 바로 이 **툴 과부하(tool overload)** — 선택지 과다로 인한 오선택이다.

이것이 막연한 체감이 아니라는 근거는 공식 문서에 있다. Anthropic의 tool search tool 문서는 툴 정의가 많아질 때의 문제를 두 축으로 명시한다: 멀티서버 환경에서 툴 정의만으로 약 55K 토큰을 선소비하는 **컨텍스트 점유**, 그리고 **"도구가 수십 개(문서 기준 30~50개 초과)로 늘어나면 Claude가 올바른 도구를 선택하는 정확도 자체가 떨어지기 시작한다"**는 선택 정확도 저하다.[^tool-search] 학술 쪽에서도 같은 현상이 관측된다 — MCP 툴 풀이 커질수록 툴 선택 정확도가 급락하며, 검색으로 후보를 좁히면 정확도가 크게 회복된다는 실험이 있다(아래 비공식 출처 블록 참조).[^rag-mcp]

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
>
> RAG-MCP(arXiv 2505.03275)는 피어 리뷰가 확정되지 않은 preprint다. 이 논문의 스트레스 테스트에서 전체 MCP 툴 풀을 그대로 프롬프트에 넣은 베이스라인의 툴 선택 정확도는 13.62%였고, semantic retrieval로 후보를 좁힌 RAG-MCP는 43.13%로 3배 이상 높았으며 프롬프트 토큰도 50% 이상 줄었다.[^rag-mcp] 절대 수치는 해당 벤치마크·모델 구성에 종속적이므로 "검색으로 좁히면 정확도가 유의미하게 회복된다"는 방향성만 취하고, 절대값은 자체 평가 하니스([eval-harness](./eval-harness.md))로 재측정하라.

플랫폼 관점에서 이 문제가 특히 고약한 이유는 **회귀가 조용히 일어난다**는 점이다. 툴 하나를 추가하는 배포는 어떤 기존 기능도 건드리지 않으므로 통상의 회귀 테스트를 통과한다. 그러나 LLM의 툴 선택은 카탈로그 전체를 조건으로 하는 확률적 결정이라, 새 툴의 이름·설명이 기존 툴과 의미적으로 겹치는 순간 기존 태스크의 정답률이 떨어질 수 있다. 즉 툴 추가는 격리된 변경이 아니라 **전역 프롬프트 변경**이고, 그렇게 취급해서 계측해야 한다.

## 핵심 개념

### 두 가지 저하 메커니즘: 컨텍스트 점유 vs 결정 복잡도

툴 과부하의 저하 경로는 둘로 구분해야 대응 레버가 정해진다.

1. **컨텍스트 점유(prompt bloat)** — 툴 정의(이름·설명·`inputSchema`)가 컨텍스트 윈도 앞부분을 선점해, 실제 태스크 컨텍스트(지시문, 검색 문서, 대화 이력)가 밀려나는 문제. Anthropic 문서의 예시에서 멀티서버 툴 정의 약 55K 토큰이 tool search 적용으로 85% 이상 절감됐다.[^tool-search] 이 축은 [Part 5의 JIT retrieval과 토큰 예산](../05-context/jit-retrieval-token-budget.md)이 다루는 컨텍스트 예산 문제와 동일한 현상의 다른 단면이다.
2. **결정 복잡도(decision complexity)** — 토큰 여유가 충분해도, 의미적으로 유사한 선택지가 많아지면 오선택 확률 자체가 올라가는 문제. 위 RAG-MCP 실험이 보여주듯 컨텍스트가 넘치지 않아도 후보 수 자체가 정확도를 깎는다.[^rag-mcp]

이 구분이 중요한 이유: `defer_loading`이나 프롬프트 캐싱은 ①을 완화하지만 ②는 건드리지 못한다. 반대로 유사 툴 통합과 설명 경계 재작성은 ②를 직접 공략한다. semantic tool search는 "매 턴 모델이 보는 후보 집합"을 줄이므로 둘 다에 작용한다 — 그래서 이 장의 대응 레버 중 가장 우선순위가 높다.

### 운영 임계치: 50개를 넘기기 전에

이 책 전체에서 일관되게 쓰는 운영 임계치는 **에이전트에 노출되는 툴이 50개를 넘기 전에 semantic tool search(또는 동급의 노출 축소 장치)를 도입하라**는 것이다([six-pain-points](../00-intro/six-pain-points.md), [gateway-deep-dive](../10-agentcore/gateway-deep-dive.md)와 동일 임계치). Anthropic 문서의 "30~50개 초과 시 정확도 저하"[^tool-search]를 근거로 그 구간의 상한을 대응 시한으로 잡은 것이다. 50이라는 숫자 자체는 "이 시점부터 급락한다"는 정밀 관측값이 아니라 컨텍스트 사용량·캐시 안정성([Part 4](../04-caching/cache-miss-root-causes.md))까지 함께 고려한 이 책의 운영 권고이므로, 자신의 모델·워크로드에서 임계 구간을 재측정하는 것이 안전하다.

여기서 세는 것은 카탈로그의 총 툴 수가 아니라 **한 턴에 모델이 실제로 보는 툴 수**다. 카탈로그에 500개가 있어도 매 턴 노출이 10개면 결정 복잡도 관점에서는 10개짜리 시스템이다. 이 장의 모든 레버는 결국 "카탈로그 크기와 노출 크기를 분리하는" 장치다.

### 레버 1 — AgentCore Gateway semantic tool search

AgentCore Gateway에서 semantic search를 활성화하면 `tools/list` 응답에 내장 툴 `x_amz_bedrock_agentcore_search`가 노출되고, 에이전트는 자연어 `query` 하나로 태스크에 맞는 툴 부분집합만 받아온다. AWS는 이를 "Semantic Tool Selection — 에이전트가 수천 개의 툴을 쓰면서도 프롬프트 크기를 줄이고 지연을 낮추도록 한다"고 설명한다.[^gw-search] 호출 방식·인덱스 구성 등 인프라 세부는 [gateway-deep-dive](../10-agentcore/gateway-deep-dive.md)가 정본이고, 이 장의 관심은 정확도 효과다: 모델이 매 턴 보는 후보 집합이 "전체 카탈로그"에서 "쿼리 관련 부분집합"으로 줄어들면 결정 복잡도와 컨텍스트 점유가 동시에 축소된다.

::: warning 미정착 영역
"툴 300개 이상을 semantic search로 쿼리당 10~15개로 줄인다"는 식의 구체적 축소 비율은 [gateway-deep-dive](../10-agentcore/gateway-deep-dive.md) 작성 시점에 AWS 공식 문서나 검증 가능한 출처에서 확인되지 않았다. 이 장에서도 동일하게 그 수치를 사실로 인용하지 않는다(책 전체 일관성 원칙). "쿼리당 반환 툴 개수"와 "정답 툴 포함률(recall)"을 자체 벤치마크로 측정해 이 자리를 채우라.
:::

정확도 관점의 새 주의점 하나: 검색 계층을 넣는 순간 오류 모드가 "오선택"에서 **"검색 miss"**로 이동한다. 정답 툴이 검색 결과에 아예 안 들어오면 모델이 아무리 잘 골라도 실패한다. 그래서 semantic search 도입 후에는 툴 선택 정확도와 별개로 **검색 recall**을 독립 SLI로 계측해야 한다(아래 계측 절).

### 레버 2 — Anthropic tool search tool (`defer_loading`)

모델 API 레벨의 동일 개념이 Anthropic의 tool search tool이다. 툴 정의에 `defer_loading: true`를 지정하면 정의가 컨텍스트에 선적재되지 않고, 모델이 tool search tool로 필요한 정의만 on-demand 로드한다. 공식 가이드라인의 사용 기준: 툴이 10개 미만이고 전체 정의가 소규모면 표준 방식(전부 선적재)이 낫고, 툴 10개 이상·정의 10K 토큰 초과·MCP 툴 200개 이상 규모면 tool search를 권장한다.[^tool-search] 컨텍스트 점유 관점의 전환 기준은 [jit-retrieval-token-budget](../05-context/jit-retrieval-token-budget.md)에서 토큰 예산 배분표와 함께 다뤘다.

Gateway semantic search와의 관계: 같은 문제를 다른 계층에서 푼다. Gateway 방식은 MCP 프로토콜 계층(어느 모델이든 적용, AWS 인프라 종속), tool search tool은 Claude API 계층(모델 종속, 인프라 불요)이다. 둘을 중첩하면 검색 계층이 이중이 되어 recall 실패 지점도 이중이 되므로, 한 계층에서 노출 축소를 담당하게 하고 다른 계층은 passthrough로 두는 것이 진단 가능성 면에서 낫다.

### 레버 3 — 툴 통합(consolidation)

검색은 후보를 줄이지만, 애초에 후보가 될 툴 자체를 줄이는 것이 더 근본적이다. Anthropic의 "Writing effective tools for AI agents"는 자주 연쇄되는 멀티스텝 작업을 하나의 상위 툴로 통합하라고 권고한다 — `list_users` → `list_events` → `create_event` 세 툴 대신 `schedule_event` 하나가 내부에서 가용 시간 조회까지 처리하는 식이다.[^tool-writing] 정확도 관점의 효과는 이중이다: 카탈로그 크기 자체가 줄고(결정 복잡도 감소), 오호출이 가능한 결정 지점이 3회에서 1회로 줄어든다(오류 복합 감소). 설계 세부 — 네임스페이싱, 설명 경계, 응답 포맷 — 는 [tool-design](../01-agent-design/tool-design.md)에서 다뤘다.

통합은 유사 툴 정리와 함께 간다. 툴 선택 정확도가 떨어지는 태스크의 트레이스를 보면, 모델이 헷갈린 두 툴의 description이 사람 눈에도 구분되지 않는 경우가 대부분이다. 검색 레버를 켜기 전에 "이 두 툴을 사람이 설명만 보고 구분할 수 있는가"부터 정리하라 — semantic search도 결국 그 설명을 임베딩하므로, 설명이 겹치면 검색 정확도까지 같이 나빠진다.

### 레버 4 — code execution 패턴

Anthropic의 code execution with MCP 패턴은 툴들을 "호출 가능한 tool 목록"이 아니라 파일시스템 위의 코드 API로 제시하고, 모델이 필요한 함수 정의만 읽어 코드로 조합 호출하게 한다. Anthropic의 예시 시나리오에서 툴 정의 선적재 150K 토큰이 2K 토큰으로 줄었다(98.7% 절감).[^code-exec] 정확도 관점에서는 "N개 중 하나를 고르는" 문제를 "필요한 API를 탐색해 코드를 작성하는" 문제로 바꾸는 것인데, 이는 대규모 카탈로그에서 유리한 대신 샌드박스 구축·운영이라는 전제 조건이 붙는다. 트레이드오프와 도입 판단은 [mcp-server-design](../01-agent-design/mcp-server-design.md)의 결정 표를 따르라.

### 레버 5 — 인가 계층의 부수 효과: Cedar 툴 필터링

AgentCore Policy(Cedar)는 partial evaluation으로 "어떤 조건에서도 항상 거부되는" 액션을 식별해 `list_tools` 응답에서 아예 제외한다.[^cedar] 이는 일차적으로 인가·프롬프트 인젝션 방어 장치지만([cedar-verified-permissions](../09-authorization/cedar-verified-permissions.md)), 정확도 관점에서도 공짜 노출 축소다: 호출해봤자 거부될 툴이 후보에서 사라지면 그만큼 결정 복잡도가 줄고, "모델이 금지된 툴을 골라 DENY로 실패하는" 오류 모드 자체가 소거된다. 항상 거부되어야 하는 액션을 조건 없는 `forbid`로 표현해야 partial evaluation이 필터링할 수 있다는 운영 규칙도 함께 지켜야 한다.[^cedar]

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 툴 10개 미만, 정의 소규모 | 전부 선적재(표준 방식) 유지 | 검색 계층의 recall 실패·지연을 감수할 이유 없음[^tool-search] | 증가 추세면 개수 SLI에 50개 경보를 미리 걸어야 함 |
| 툴 30~50개 구간 진입, 유사 툴 다수 | 먼저 consolidation + 설명 경계 재작성 | 카탈로그 자체 축소가 검색보다 근본적, 검색 품질의 전제이기도 함[^tool-writing] | 통합 툴은 유연성 감소 — 비정형 조합 태스크는 저수준 툴 병행 |
| 툴 50개 근접~초과, Gateway 사용 | Gateway semantic tool search 활성화 | 프로토콜 계층에서 노출 축소, 모델 불문 적용[^gw-search] | 검색 recall 계측 의무 발생, 인덱스 최신화 지연 |
| 툴 50개 근접~초과, Claude API 직결 | tool search tool + `defer_loading` | 인프라 추가 없이 API 레벨에서 동일 효과[^tool-search] | Claude 종속, Gateway 검색과 중첩 금지(이중 recall 실패 지점) |
| 카탈로그 수백 개 이상 + 대용량 중간 결과 전달 | code execution 패턴 | 정의 on-demand 로드 + 중간 결과 컨텍스트 우회[^code-exec] | 샌드박스 구축·운영 부담 — [mcp-server-design](../01-agent-design/mcp-server-design.md) |
| 호출자별로 사용 가능 툴이 다름 | Cedar 정책 + partial evaluation 필터링 | 인가와 노출 축소를 같은 메커니즘으로 해결[^cedar] | 조건부 forbid는 필터링되지 않음 — 정책 표현 규율 필요 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 툴 추가 배포 후 무관한 기존 태스크의 정답률 하락 | 툴 추가는 전역 프롬프트 변경 — 새 툴 설명이 기존 툴과 의미적으로 겹침 | 배포 전후 골든 태스크 셋의 툴 선택 정확도 비교([eval-harness](./eval-harness.md)) | 설명 경계 재작성 또는 통합, 툴 추가를 평가 게이트 통과 대상으로 승격 |
| 유사 툴 사이 오선택 반복 | description 중복, 네임스페이스 부재 | 오답 트레이스에서 혼동된 툴 쌍의 설명을 나란히 검토 | 네임스페이싱·설명 재작성·통합 — [tool-design](../01-agent-design/tool-design.md) |
| semantic search 도입 후에도 실패율 개선 없음 | 정답 툴이 검색 결과에 미포함(recall 실패)으로 오류 모드만 이동 | 실패 태스크에서 검색 반환 목록에 정답 툴 포함 여부 로깅 | 툴 설명 임베딩 품질 개선, 반환 개수(top-k) 조정, recall SLI 상시 계측 |
| 요청 시작 전에 컨텍스트 수만 토큰 선소비 | 전체 툴 정의 선적재(컨텍스트 점유) | 첫 요청 input tokens에서 사용자 프롬프트 분리 계측 | `defer_loading`/semantic search 전환 — 기준은 10개·10K 토큰[^tool-search] |
| 모델이 금지된 툴을 계속 시도하다 DENY로 실패 | 인가상 호출 불가한 툴이 `list_tools`에 노출됨 | Gateway 로그에서 DENY 비율과 해당 액션의 정책 형태(조건부 forbid 여부) 확인 | 무조건 거부 액션은 조건 없는 forbid로 표현해 partial evaluation 필터링 유도[^cedar] |
| 툴 목록 변경 때마다 지연·비용 동반 악화 | 툴 정의가 캐시 프리픽스 최상위라 목록 변경이 캐시 전체를 무효화 | `cache_read_input_tokens / input_tokens` 추이와 툴 목록 변경 이벤트 대조 | 툴 스키마 안정화, 변경을 배포 단위로 묶기 — [Part 4](../04-caching/cache-miss-root-causes.md) |

## 안티패턴

- ❌ 팀 요청마다 툴을 추가하고 총량을 아무도 세지 않는다 → ✅ 노출 툴 개수를 SLI로 만들고 50개 임계치에 경보를 건다(카탈로그 수가 아니라 **턴당 노출 수** 기준).
- ❌ 툴 추가를 "기능 무관 변경"으로 보고 회귀 테스트 없이 배포한다 → ✅ 툴 카탈로그 변경을 골든 태스크 셋 평가 게이트의 트리거로 삼는다.
- ❌ 정확도가 떨어지자마자 semantic search부터 켠다 → ✅ 유사 툴 통합·설명 정리를 먼저 한다 — 겹치는 설명은 검색 정확도도 같이 깎는다.[^tool-writing]
- ❌ Gateway semantic search와 tool search tool을 중첩해서 켠다 → ✅ 노출 축소는 한 계층이 담당하고 다른 계층은 passthrough — recall 실패 지점을 하나로 유지한다.
- ❌ 검색 도입 후 recall을 계측하지 않는다 → ✅ "정답 툴이 검색 결과에 포함됐는가"를 실패 분석의 1차 분기점으로 로깅한다.
- ❌ 커뮤니티 발표의 축소 비율("300개 → 10~15개" 등)을 근거로 도입을 정당화한다 → ✅ 자체 벤치마크로 쿼리당 반환 개수·recall을 측정한다(위 미정착 영역 참조).

## 계측 (SLI)

툴 과부하는 조용한 회귀이므로 계측 없이는 인지 자체가 안 된다. 최소 세트:

- **턴당 노출 툴 개수**: 매 턴 모델에게 실제 전달된 툴 정의 수의 분포(P50/P95). 50개 임계치 경보의 대상. 카탈로그 총수는 별도 보조 지표로.
- **툴 선택 정확도**: 골든 태스크 셋에서 "정답 툴(시퀀스)을 선택한 비율". 툴 카탈로그 변경마다 재실행 — 평가 하니스 구성은 [eval-harness](./eval-harness.md), 궤적 단위 채점은 [llm-judge-trajectory](./llm-judge-trajectory.md) 참조.
- **툴 정의 컨텍스트 점유율**: 첫 요청 input tokens 중 툴 정의가 차지하는 비율. `defer_loading` 전환 판단 입력([jit-retrieval-token-budget](../05-context/jit-retrieval-token-budget.md)과 공유).
- **검색 recall(semantic search 사용 시)**: 골든 태스크의 정답 툴이 `x_amz_bedrock_agentcore_search`(또는 tool search tool) 반환 목록에 포함된 비율. 쿼리당 반환 개수 분포도 함께.
- **오선택 후 정정 비율**: 잘못된 툴 호출 후 에러를 보고 올바른 툴로 재시도한 턴의 비율. 정확도 하락이 지연·비용으로 전이되는 크기를 보여준다 — [Part 2 tool round-trip](../02-performance/tool-roundtrips.md)의 왕복 계측과 결합.
- **인가 DENY 비율**: Gateway 툴 호출 중 정책 DENY 비율. 높으면 "호출 불가 툴이 노출되고 있다"는 신호 — partial evaluation 필터링 누수 점검([cedar-verified-permissions](../09-authorization/cedar-verified-permissions.md)의 `list_tools` 전/후 노출 수 지표와 짝).

## 체크리스트

- [ ] 턴당 노출 툴 개수를 계측하고 있고, 50개 임계치에 경보가 걸려 있는가?
- [ ] 툴 카탈로그 변경(추가·설명 수정 포함)이 골든 태스크 평가 게이트를 트리거하는가?
- [ ] 유사 툴 쌍의 description을 사람이 설명만 보고 구분할 수 있는가? 항상 연쇄되는 툴 시퀀스는 통합을 검토했는가?
- [ ] 툴 10개 이상 또는 정의 10K 토큰 초과 시점에 `defer_loading`/semantic search 전환을 판단했는가?[^tool-search]
- [ ] semantic search 도입 시 검색 recall을 독립 SLI로 계측하는가? 노출 축소 계층이 하나로 유지되는가?
- [ ] 항상 거부되어야 하는 액션이 조건 없는 `forbid`로 표현되어 `list_tools`에서 실제로 필터링되는가?
- [ ] 축소 비율·정확도 개선 수치를 외부 발표가 아니라 자체 벤치마크로 확보했는가?
- [ ] 툴 목록 변경이 프롬프트 캐시에 미치는 영향을 캐시 히트율과 함께 추적하는가?

## 참고

- Anthropic, [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) — 툴 30~50개 초과 시 선택 정확도 저하, 멀티서버 툴 정의 약 55K 토큰과 85%+ 절감 사례, `defer_loading` 사용 기준
- Anthropic, [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) — 툴 통합(consolidation), 네임스페이싱, 설명 경계
- Anthropic, [Code execution with MCP: Building more efficient agents](https://www.anthropic.com/engineering/code-execution-with-mcp) — 툴 정의 선적재 150K → 2K 토큰(98.7%) 예시
- Anthropic, [Manage tool context](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context) — 툴 정의의 컨텍스트 점유를 줄이는 보완 기법
- AWS, [Amazon Bedrock AgentCore Gateway — 개요](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) — Semantic Tool Selection
- AWS, [Search for tools in your AgentCore gateway with a natural language query](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-semantic-search.md) — `x_amz_bedrock_agentcore_search`
- AWS Security Blog, [Why Policy in Amazon Bedrock AgentCore chose Cedar for securing agentic workflows](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/) — partial evaluation 기반 `list_tools` 필터링
- Gan & Sun, [RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation (arXiv:2505.03275)](https://arxiv.org/abs/2505.03275) — ⚠️ preprint, 비공식 출처
- 이 책의 관련 장: [six-pain-points](../00-intro/six-pain-points.md) · [tool-design](../01-agent-design/tool-design.md) · [mcp-server-design](../01-agent-design/mcp-server-design.md) · [jit-retrieval-token-budget](../05-context/jit-retrieval-token-budget.md) · [cedar-verified-permissions](../09-authorization/cedar-verified-permissions.md) · [gateway-deep-dive](../10-agentcore/gateway-deep-dive.md)

[^tool-search]: Anthropic, ["Tool search tool"](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), Claude Docs — 툴 30~50개 초과 시 선택 정확도 저하, 멀티서버 툴 정의 약 55K 토큰과 tool search 적용 시 85%+ 절감, `defer_loading` 사용 기준(10개 미만·소규모 정의는 표준 방식, 10개 이상·10K 토큰 초과·200개 이상 MCP 툴은 tool search 권장). [jit-retrieval-token-budget](../05-context/jit-retrieval-token-budget.md)과 동일 출처.
[^rag-mcp]: ⚠️ 비공식(preprint). Gan, T. & Sun, Q., ["RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection via Retrieval-Augmented Generation"](https://arxiv.org/abs/2505.03275), arXiv:2505.03275 (2025-05) — 스트레스 테스트에서 툴 선택 정확도 43.13%(RAG-MCP) vs 13.62%(전체 노출 베이스라인), 프롬프트 토큰 50% 이상 절감.
[^gw-search]: AWS, ["Amazon Bedrock AgentCore Gateway — 개요"](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) 및 ["Search for tools with natural language query"](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-semantic-search.md) — Semantic Tool Selection과 `x_amz_bedrock_agentcore_search` 호출 방식. 인프라 세부는 [gateway-deep-dive](../10-agentcore/gateway-deep-dive.md)가 정본.
[^tool-writing]: Anthropic, ["Writing effective tools for AI agents"](https://www.anthropic.com/engineering/writing-tools-for-agents), Anthropic Engineering Blog — 멀티스텝 연쇄 작업의 상위 툴 통합, 네임스페이싱, 설명 경계 권고.
[^code-exec]: Anthropic, ["Code execution with MCP: Building more efficient agents"](https://www.anthropic.com/engineering/code-execution-with-mcp), Anthropic Engineering Blog (2025-11) — 툴 정의 선적재 150,000 → 2,000 토큰(98.7% 절감) 예시 시나리오. 단일 예시 수치라는 한계는 [mcp-server-design](../01-agent-design/mcp-server-design.md)의 비공식 출처 블록 참조.
[^cedar]: AWS Security Blog, ["Why Policy in Amazon Bedrock AgentCore chose Cedar for securing agentic workflows"](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/) — Cedar partial evaluation으로 항상 거부되는 액션을 `list_tools` 응답에서 제외하는 툴 필터링. 정책 표현 규율(조건 없는 forbid)은 [cedar-verified-permissions](../09-authorization/cedar-verified-permissions.md) 참조.
