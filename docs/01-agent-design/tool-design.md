---
title: 툴 설계
description: 에이전트 툴을 컨텍스트 예산·오호출률·에러 복구 관점에서 계약(contract)으로 설계하는 방법을 다룬다.
outline: [2, 3]
---

# 툴 설계

::: tip 이 장에서 얻는 것
- 툴을 "API 래퍼"가 아니라 **에이전트와 맺는 계약(contract)**으로 설계하는 관점을 얻는다 — 파라미터 네이밍, 응답 페이지네이션, 에러 메시지까지 전부 모델에게 되돌아가는 입력이라는 원칙.
- Anthropic의 "Writing effective tools for AI agents"가 제시하는 핵심 권고(통합, 네임스페이싱, 토큰 효율, 평가 기반 개선)를 플랫폼 정책으로 옮기는 법을 익힌다.[^tool-writing]
- Claude Code가 툴 응답을 기본 25,000 토큰으로 제한하는 것처럼,[^cc-mcp] 우리 플랫폼에도 **툴 응답 상한을 정책으로 강제해야 하는 이유**와 그 계측 방법을 안다.
:::

## 왜 문제가 되는가

전통적인 API 설계는 결정론적 클라이언트를 전제한다. 클라이언트 코드는 스키마를 컴파일 타임에 알고, 같은 입력에 같은 호출을 하며, 응답이 아무리 커도 파싱 비용만 치른다. 에이전트 툴은 이 전제가 전부 무너진 환경에서 동작한다. 호출자는 비결정론적 모델이고, 툴 정의(이름·설명·파라미터 스키마)와 툴 응답은 매 턴 컨텍스트 윈도우에 주입되어 [attention budget을 소모하며](../05-context/context-engineering-discipline.md), 모호한 파라미터는 컴파일 에러가 아니라 **hallucinated argument**로 나타난다.

Anthropic은 이를 명시적으로 구분한다: 툴은 결정론적 시스템과 비결정론적 에이전트 사이의 계약이며, 좋은 툴이란 "인간 엔지니어가 봐도 언제 써야 할지 자명한" 툴이다 — 어떤 상황에서 어느 툴을 써야 하는지가 인간에게 모호하면 에이전트도 판단하지 못한다.[^tool-writing][^ctx-eng]

플랫폼 관점에서 툴 설계가 문제가 되는 지점은 세 가지다.

1. **컨텍스트 비용**: 툴 응답이 무제한이면 한 번의 호출이 세션 전체의 컨텍스트 예산을 태운다. Claude Code가 MCP 툴 응답을 기본 25,000 토큰으로 제한하고 10,000 토큰 초과 시 경고를 띄우는 것은 이 비용을 플랫폼 레벨에서 통제하는 대표 사례다.[^cc-mcp]
2. **정확도**: 툴 수가 늘고 설명이 겹치면 오호출률이 올라간다. 이 스케일 문제는 [툴 과부하](../03-accuracy-eval/tool-overload.md)에서 별도로 다루고, 이 장은 개별 툴의 품질에 집중한다.
3. **복구 가능성**: 에러 메시지는 로그가 아니라 **모델에게 되돌아가는 입력**이다. `500 Internal Error` 한 줄은 에이전트를 무의미한 재시도 루프나 포기로 몰고 가지만, "무엇이 잘못됐고 어떻게 고치면 되는지"를 담은 에러는 다음 턴에 올바른 호출을 유도한다.[^tool-writing]

## 핵심 개념

### 툴은 API 래퍼가 아니라 워크플로 단위다

기존 REST API를 엔드포인트 1:1로 툴로 노출하는 것이 가장 흔한 첫 실수다. Anthropic의 권고는 반대 방향이다: 자주 연쇄되는 멀티스텝 작업은 **하나의 상위 툴로 통합(consolidation)**하라. `list_users` → `list_events` → `create_event` 세 번의 왕복 대신 `schedule_event` 하나가 내부에서 가용 시간 조회까지 처리하는 식이다.[^tool-writing] 이는 왕복 횟수(라운드트립 지연 — [툴 라운드트립](../02-performance/tool-roundtrips.md) 참조)와 중간 응답이 컨텍스트에 쌓이는 비용을 동시에 줄인다.

반대 힘도 있다. 툴 하나가 너무 많은 것을 하면(파라미터 20개짜리 만능 툴) 모델이 어떤 조합이 유효한지 추론해야 하고 hallucinated argument가 늘어난다. 기준은 "에이전트가 실제로 수행하는 태스크의 단위"이지, 백엔드 API의 단위도, 가능한 모든 기능의 합집합도 아니다.

### 네임스페이싱

툴이 여러 서비스에 걸쳐 있으면 이름 자체가 분류 신호가 된다. Anthropic은 서비스별(`asana_search`, `jira_search`), 리소스별(`asana_projects_search`, `asana_users_search`) 네임스페이싱을 권고하며, prefix 기반이냐 suffix 기반이냐의 선택조차 자사 툴 사용 평가에서 무시할 수 없는(non-trivial) 성능 차이를 만들었다고 보고한다.[^tool-writing] 즉 네이밍 컨벤션은 취향 문제가 아니라 **평가로 검증해야 할 설계 변수**다. 플랫폼은 툴 등록 시점에 네임스페이스 규칙을 린트로 강제하는 것이 좋다.

### 파라미터: 모호성은 곧 hallucination이다

파라미터 이름과 스키마는 모델이 값을 채우는 유일한 근거다. `user`라는 이름은 이메일인지, 표시 이름인지, UUID인지 모델이 추측하게 만든다. `user_id`로 바꾸는 것만으로 추측의 여지가 사라진다.[^tool-writing]

```json
// ❌ 모델이 무엇을 넣을지 추측해야 하는 스키마
{
  "name": "get_orders",
  "parameters": {
    "user":  { "type": "string" },
    "date":  { "type": "string" },
    "limit": { "type": "integer" }
  }
}
```

```json
// ✅ 이름·포맷·기본값이 계약을 명시하는 스키마
{
  "name": "orders_search",
  "parameters": {
    "user_id": {
      "type": "string",
      "description": "주문자의 내부 사용자 ID (예: 'usr_01H...'). 이메일이 아님."
    },
    "created_after": {
      "type": "string",
      "format": "date-time",
      "description": "이 시각(ISO 8601, UTC) 이후 생성된 주문만 반환."
    },
    "limit": {
      "type": "integer",
      "default": 20,
      "maximum": 100,
      "description": "최대 반환 건수. 기본 20."
    }
  }
}
```

같은 원칙이 응답 필드에도 적용된다. 응답은 유연성보다 문맥적 관련성을 우선해야 하며, `uuid`, `256px_image_url`, `mime_type` 같은 저수준 기술 식별자는 후속 호출에 꼭 필요한 경우가 아니면 빼라는 것이 원문의 권고다.[^tool-writing] 이를 위해 Anthropic은 `response_format: "concise" | "detailed"` 같은 enum 파라미터로 에이전트가 응답 상세도를 스스로 선택하게 하는 패턴을 제시하는데, concise 응답은 detailed 대비 약 1/3 토큰만 소비했다.[^tool-writing]

### 응답은 컨텍스트 예산 안에서 설계한다

툴 응답의 토큰 효율을 위한 네 가지 표준 장치는 **pagination, range selection, filtering, truncation**이며, 각각에 합리적인 기본 파라미터 값을 줘야 한다.[^tool-writing]

```python
# ❌ 테이블 전체를 그대로 반환 — 한 번의 호출이 컨텍스트를 태운다
def list_logs(service: str) -> str:
    rows = db.query("SELECT * FROM logs WHERE service = %s", service)
    return json.dumps(rows)  # 수만 행이면 그대로 수십만 토큰
```

```python
# ✅ 기본값이 있는 페이지네이션 + 필터 + 잘림 안내
def logs_search(service: str, level: str = "ERROR",
                since: str | None = None,
                limit: int = 50, cursor: str | None = None) -> str:
    rows, next_cursor, total = db.page(service, level, since, limit, cursor)
    return json.dumps({
        "logs": rows,                      # limit 건까지만
        "total_matched": total,
        "next_cursor": next_cursor,        # 더 필요하면 이 값으로 재호출
        "truncated": next_cursor is not None,
        "hint": "결과가 많습니다. level/since 필터를 좁히면 더 정확합니다."
                if total > limit * 10 else None,
    })
```

핵심은 잘랐다는 사실과 **더 가져오는 방법을 응답 안에 함께 알려주는 것**이다. 조용히 자르면 모델은 "결과가 그게 전부"라고 믿고, 안내 없이 자르면 어떻게 이어서 조회할지 모른다.

그리고 이 절제를 개별 툴 작성자의 선의에 맡기면 안 된다. Claude Code는 MCP 툴 응답을 `MAX_MCP_OUTPUT_TOKENS` 환경변수로 제어하되 **기본 25,000 토큰으로 제한**하고, 10,000 토큰 초과 시 경고를 표시하며, 개별 툴이 `anthropic/maxResultSizeChars` 어노테이션으로 자체 상한을 선언할 수 있게 한다(텍스트 기준 최대 500,000자 하드 실링).[^cc-mcp] 즉 성숙한 에이전트 런타임은 "잘 만든 툴은 알아서 절제하겠지"가 아니라 **런타임 레벨의 상한 + 경고 임계값 + 툴별 오버라이드**라는 3단 정책으로 강제한다. 자체 플랫폼을 만든다면 같은 구조를 복제해야 한다: 전역 기본 상한, 초과 접근 시 관측 가능한 경고, 정당한 사유가 있는 툴만 선언적으로 상한을 올리는 예외 경로.

### 에러 메시지는 모델에게 되돌아가는 입력이다

툴 실행이 실패했을 때 에이전트가 받는 것은 스택 트레이스를 읽을 개발자가 아니라 **다음 행동을 골라야 하는 모델**이다. Anthropic의 권고는 불투명한 에러 코드 대신 "구체적이고 실행 가능한 개선안(specific and actionable improvements)"을 담아, 올바른 사용 패턴과 더 토큰 효율적인 전략 쪽으로 에이전트를 유도하라는 것이다.[^tool-writing]

```json
// ❌ 모델이 할 수 있는 게 없는 에러 — 무의미한 동일 재시도 또는 포기 유발
{ "error": "Request failed", "code": 400 }
```

```json
// ✅ 무엇이 잘못됐고, 어떻게 고치면 되는지를 담은 에러
{
  "error": "invalid_parameter",
  "message": "created_after='2026-8-1'는 ISO 8601 형식이 아닙니다. '2026-08-01T00:00:00Z' 형식으로 다시 호출하세요.",
  "retryable": true
}
```

```json
// ✅ 재시도하면 안 되는 경우는 그것도 명시한다
{
  "error": "permission_denied",
  "message": "user_id='usr_01H...'의 주문은 현재 권한(read:own_orders)으로 조회할 수 없습니다. 재시도하지 말고 사용자에게 권한 요청이 필요하다고 보고하세요.",
  "retryable": false
}
```

에러 설계의 판별 기준은 하나다: **이 에러 문자열만 보고 모델이 다음 턴에 더 나은 호출을 만들 수 있는가.** 검증 실패라면 어떤 필드가 어떤 형식이어야 하는지, rate limit이라면 언제 재시도해야 하는지, 권한 문제라면 재시도가 무의미하다는 사실 자체를 알려야 한다. 단, 에러 메시지도 컨텍스트에 쌓이는 토큰이므로 스택 트레이스 전체를 그대로 반환하는 것은 반대편 극단의 실수다.

### 평가 기반 개선: 툴은 프롬프트처럼 튜닝한다

툴 정의는 한 번 쓰고 끝나는 인터페이스가 아니라 평가로 반복 개선하는 대상이다. Anthropic의 워크플로는 (1) 실제와 유사한 멀티스텝 태스크로 평가 세트를 만들고, (2) 에이전트가 툴을 사용하는 transcript를 분석해 혼동 지점을 찾고, (3) 툴 이름·설명·스키마·응답 포맷을 수정한 뒤 재평가하는 루프다 — 원문 권고의 대부분이 "Claude Code로 자사 내부 툴 구현을 반복 최적화하며" 얻어졌고, 과적합 방지를 위해 held-out 테스트 세트를 유지했다고 밝힌다.[^tool-writing] 프로토타이핑 단계에서는 SDK 문서(`llms.txt` 등)를 모델에게 주고 로컬 MCP 서버로 빠르게 시제품을 만들어 직접 써보게 하는 접근을 권한다.[^tool-writing] 이 평가 하네스 구축은 [평가 하네스](../03-accuracy-eval/eval-harness.md)에서 다룬다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 백엔드 API 3~4개가 항상 연쇄 호출되는 태스크 | 상위 워크플로 툴 하나로 통합 | 왕복·중간 응답 토큰 절감, 오호출 지점 축소[^tool-writing] | 유연성 감소 — 비정형 조합이 필요한 태스크는 저수준 툴 병행 필요 |
| 여러 서비스의 유사 기능 툴이 공존 | 서비스·리소스 기반 네임스페이싱 강제 | 이름만으로 선택 신호 제공, 평가에서 성능 차이 확인됨[^tool-writing] | 네이밍 규칙(prefix vs suffix)은 자체 평가로 검증 필요 |
| 툴 응답이 수천~수만 행이 될 수 있음 | pagination + filtering + 기본 limit, 잘림 시 next_cursor 안내 | 한 호출이 컨텍스트 예산을 태우는 것 방지[^tool-writing] | 대량 집계가 목적인 태스크는 왕복 증가 — 집계 전용 툴 별도 제공 |
| 에이전트마다 필요한 상세도가 다름 | `response_format: concise/detailed` enum 제공 | concise가 약 1/3 토큰으로 대부분 태스크 커버[^tool-writing] | 후속 호출에 식별자가 필요한 체인은 detailed 강제 필요 |
| 툴 작성자들이 응답 크기를 자율 관리 | 런타임 전역 상한 + 경고 임계값 + 툴별 선언적 오버라이드 | Claude Code의 25,000 토큰 기본 상한과 동일한 구조[^cc-mcp] | 상한에 걸린 정당한 대용량 응답은 예외 절차 필요 |
| 에러 발생 시 무엇을 반환할지 | 원인 + 수정 방법 + retryable 여부를 담은 구조화 에러 | 에러는 모델의 다음 턴 입력 — 복구를 유도해야 함[^tool-writing] | 에러도 토큰 — 스택 트레이스 전문은 넣지 않는다 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 한두 번의 툴 호출 후 컨텍스트가 가득 차 compaction이 조기 발동 | 툴 응답에 상한·페이지네이션 없음 | 호출당 응답 토큰 분포 측정 — p99가 수만 토큰인 툴 식별 | 기본 limit + pagination 도입, 런타임 전역 상한 정책 적용[^cc-mcp] |
| 존재하지 않는 값이나 잘못된 형식의 인자로 호출 (hallucinated args) | 파라미터 이름·설명이 모호(`user`, `date`, `id`) | 호출 로그에서 스키마 검증 실패율, 인자별 실패 분포 확인 | `user_id`처럼 의미·형식이 이름에 드러나게 리네이밍, description에 예시 포맷 명시[^tool-writing] |
| 같은 실패 호출을 그대로 반복하는 재시도 루프 | 에러 메시지가 원인·수정 방법을 담지 않음 | transcript에서 동일 인자 연속 호출 패턴 검출 | 에러에 "무엇이 잘못됐고 어떻게 고치는지" + `retryable` 플래그 포함 |
| 유사 툴 사이에서 오선택이 잦음 | 툴 설명 중복, 네임스페이스 부재 | 태스크별 정답 툴 대비 실제 선택 툴 비율(툴 선택 정확도) 측정 | 네임스페이싱·설명 경계 재작성, 겹치는 툴 통합 — [툴 과부하](../03-accuracy-eval/tool-overload.md) 참조 |
| 결과가 잘렸는데 에이전트가 전부라고 믿고 잘못된 결론 도출 | 조용한 truncation — 잘림 사실·이어받기 방법 미표기 | 응답에 truncated 플래그가 있는지, 잘림 후 후속 페이지 호출이 일어나는지 확인 | `truncated: true` + `next_cursor` + 필터 좁히기 힌트를 응답에 포함 |
| 툴을 개선했는데 좋아졌는지 나빠졌는지 알 수 없음 | 평가 세트 부재, transcript 미분석 | 툴 변경 PR에 평가 결과가 첨부되는지 확인 | 멀티스텝 태스크 기반 평가 + held-out 세트로 회귀 검증[^tool-writing] |

## 안티패턴

- ❌ REST 엔드포인트를 1:1로 툴로 노출한다 → ✅ 에이전트 태스크 단위로 통합된 워크플로 툴을 설계한다.[^tool-writing]
- ❌ 파라미터를 `user`, `date`, `q`처럼 짧게 짓는다 → ✅ `user_id`, `created_after`처럼 의미와 형식이 이름에 드러나게 짓고, description에 예시를 넣는다.[^tool-writing]
- ❌ 응답 크기 관리를 각 툴 작성자에게 맡긴다 → ✅ 런타임 전역 상한(예: Claude Code의 기본 25,000 토큰)과 경고 임계값을 플랫폼 정책으로 강제한다.[^cc-mcp]
- ❌ `{"error": "failed"}` 같은 불투명 에러를 반환한다 → ✅ 원인, 수정 방법, 재시도 가능 여부를 담아 다음 턴의 올바른 호출을 유도한다.[^tool-writing]
- ❌ 응답에 uuid, mime_type 등 모든 필드를 습관적으로 포함한다 → ✅ 문맥적으로 필요한 필드만 기본 반환하고, 상세는 `response_format`으로 옵트인시킨다.[^tool-writing]
- ❌ 툴 설명을 감으로 쓰고 배포 후 방치한다 → ✅ 멀티스텝 평가와 transcript 분석으로 프롬프트처럼 반복 튜닝한다.[^tool-writing]

## 계측 (SLI)

- **호출당 응답 토큰 분포 (p50/p99)**: 툴별로 추적한다. p99가 전역 상한에 상시 근접하는 툴은 페이지네이션/필터 설계 결함 신호다. Claude Code의 10,000 토큰 경고 임계값처럼, 상한보다 낮은 관측용 경고선을 따로 둔다.[^cc-mcp]
- **스키마 검증 실패율 / hallucinated argument 비율**: 파라미터 모호성의 직접 지표. 특정 파라미터에 실패가 집중되면 그 파라미터의 이름·설명부터 고친다.
- **에러 후 복구율**: 툴 에러 발생 후 N턴 이내에 성공 호출로 이어진 비율. 이 비율이 낮은 에러 타입은 메시지가 actionable하지 않다는 뜻이다.
- **동일 인자 재시도율**: 같은 인자로 같은 툴을 연속 호출하는 비율 — 에러 메시지가 복구 정보를 못 주고 있다는 신호.
- **툴 선택 정확도**: 평가 세트에서 태스크별 기대 툴 대비 실제 선택. 카탈로그가 커질 때의 추세는 [툴 과부하](../03-accuracy-eval/tool-overload.md)의 지표와 함께 본다.
- **태스크당 툴 왕복 횟수**: 통합 후보 발굴 지표. 항상 같은 순서로 연쇄되는 툴 시퀀스는 상위 툴로 합칠 후보다.

::: warning 미정착 영역
"툴 응답 상한을 몇 토큰으로 잡을 것인가"의 업계 표준 수치는 없다. Claude Code의 기본 25,000 토큰은 해당 제품의 컨텍스트 운용 방식에 맞춘 값이며,[^cc-mcp] 자체 플랫폼의 적정값은 모델의 컨텍스트 윈도우, 세션당 평균 호출 수, compaction 정책에 따라 달라진다 — 구조(전역 기본값 + 경고선 + 툴별 오버라이드)를 복제하되 수치는 자체 평가로 정해야 한다. 또한 "툴 몇 개부터 오호출이 급증하는가"도 모델·도메인 의존적이어서 정착된 임계값이 없다([툴 과부하](../03-accuracy-eval/tool-overload.md) 참조).
:::

## 체크리스트

- [ ] 각 툴이 백엔드 API 단위가 아니라 에이전트 태스크 단위로 정의되어 있는가? 항상 연쇄되는 툴 시퀀스는 통합을 검토했는가?
- [ ] 툴 이름에 서비스·리소스 네임스페이스가 적용되어 있고, 규칙이 등록 시점 린트로 강제되는가?
- [ ] 모든 파라미터 이름이 값의 의미와 형식을 드러내는가 (`user` ❌ → `user_id` ✅)? description에 예시 포맷이 있는가?
- [ ] 대량 결과 가능성이 있는 모든 툴에 pagination/filtering/truncation과 합리적 기본값이 있는가?
- [ ] 잘린 응답이 잘림 사실(`truncated`)과 이어받는 방법(`next_cursor`)을 함께 반환하는가?
- [ ] 런타임 레벨의 전역 응답 상한 + 경고 임계값 + 툴별 오버라이드 정책이 있는가?
- [ ] 모든 에러 응답이 원인, 수정 방법, `retryable` 여부를 담고 있는가? 이 에러만 보고 모델이 다음 호출을 고칠 수 있는가?
- [ ] 응답 기본 포맷이 저수준 식별자를 배제한 concise인가? 상세가 필요한 체인을 위한 `response_format` 옵션이 있는가?
- [ ] 멀티스텝 태스크 기반 평가 세트와 held-out 세트가 있고, 툴 변경 시 회귀 평가가 도는가?
- [ ] 응답 토큰 분포, hallucinated arg 비율, 에러 후 복구율, 툴 선택 정확도를 대시보드에서 관찰하고 있는가?

## 참고

- [^tool-writing]: Anthropic, "Writing effective tools for AI agents—using AI agents", <https://www.anthropic.com/engineering/writing-tools-for-agents>
- [^cc-mcp]: Claude Code Docs, "Connect Claude Code to tools via MCP" — MCP output limits: 기본 상한 25,000 토큰(`MAX_MCP_OUTPUT_TOKENS`), 경고 임계값 10,000 토큰, 툴별 `anthropic/maxResultSizeChars` 오버라이드(최대 500,000자), <https://code.claude.com/docs/en/mcp>
- [^ctx-eng]: Anthropic, "Effective context engineering for AI agents" (2025-09-29), <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
