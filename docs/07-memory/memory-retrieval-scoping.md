---
title: 메모리 검색과 스코핑
description: 메모리를 언제·무엇을·누구 범위에서 가져올지 결정하는 검색 정책과 네임스페이스 기반 테넌트 격리를 다룬다.
outline: [2, 3]
---

# 메모리 검색과 스코핑

::: tip 이 장에서 얻는 것
- 메모리 검색 타이밍(매 턴 / 세션 시작 시 / agentic 명시 요청)과 검색 대상(top-k 유사도 / 열거 / 계층 검색)의 결정 기준
- AgentCore Memory 네임스페이스 구조(`/strategy/{memoryStrategyId}/actor/{actorId}/...`)와 `namespace`(정확 일치) vs `namespacePath`(계층 검색)의 차이
- `actor_id: "tenant-a:user-123"` 합성 패턴과 커스텀 네임스페이스 변수를 이용한 테넌트+사용자 이중 스코핑, 그리고 IAM condition key로 그것을 강제하는 방법
- 멀티 에이전트가 하나의 메모리 스토어를 공유할 때 공유/격리 경계를 긋는 설계
- 메모리 주입 위치가 프롬프트 캐시 히트율에 미치는 영향과 스코핑 사고를 탐지하는 계측
:::

## 왜 문제가 되는가

메모리를 "저장"하는 문제는 [메모리 쓰기 정책](/07-memory/memory-write-policies)의 영역이다. 이 장은 그 반대편, 즉 저장된 메모리를 **언제, 무엇을, 누구의 범위에서** 꺼내오는가를 다룬다. 이 세 결정이 잘못되면 두 종류의 사고가 동시에 난다.

첫째는 **정확도 사고**다. 매 턴 무조건 top-k를 주입하면 무관한 메모리가 컨텍스트를 오염시키고([컨텍스트 부패](/05-context/context-rot) 참고), 반대로 검색을 안 하면 "지난주에 말씀드렸는데요"라는 사용자를 처음 보는 것처럼 대한다. 검색된 메모리는 토큰 예산을 소비하므로, 이 결정은 [JIT 검색과 토큰 예산](/05-context/jit-retrieval-token-budget)에서 다룬 구성요소별 예산 경쟁의 한 축이기도 하다.

둘째는 **프라이버시 사고**다. 스코핑이 잘못되면 사용자 A의 메모리("A의 연봉 협상 내역")가 사용자 B의 대화에 주입된다. 이것은 데이터 유출이자, 동시에 B에게 완전히 틀린 답을 만들어내는 정확도 사고다. AWS는 AgentCore Memory 네임스페이스 설계 가이드에서 이를 명시적으로 경고한다 — 계층 검색(tree traversal)을 쓸 때 "isolation and retrieval patterns을 충분히 검토해 의도치 않은 데이터가 노출되지 않도록 하라"([AWS ML Blog, Namespace design patterns in AgentCore Memory](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)). 더 나아가 Bedrock의 agentic retrieval 문서는 "you are responsible for supplying the correct `memoryId`, `sessionBinding`, and `retrievalConfigs` values. Agentic retrieval does not verify that the session or the namespaces [belong to the caller]"라고 못박는다([AWS, Use agentic retrieval to query a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)) — 즉 스코핑은 플랫폼이 아니라 **호출자의 책임**이며, 애플리케이션 버그 하나가 곧 크로스 유저 유출이다.

## 핵심 개념

### 1. 언제 검색할 것인가: 세 가지 트리거

메모리 검색 트리거는 세 가지로 나뉜다.

- **세션 시작 시 1회 프리로드** — 사용자 프로필·선호(user preference) 같은 저변동 메모리를 세션 첫 턴에 한 번 가져와 컨텍스트에 고정한다. 이후 턴에서는 검색 비용이 없고 프롬프트 프리픽스가 안정적이라 캐시 친화적이다. 단, 세션 중 새로 추출된 메모리는 반영되지 않는다.
- **매 턴 검색** — 현재 사용자 쿼리를 검색어로 삼아 턴마다 top-k를 가져온다. 최신성과 관련성이 가장 좋지만, 턴마다 검색 레이턴시가 붙고 주입 내용이 매번 달라져 캐시 프리픽스를 흔든다(후술).
- **agentic(명시적 요청 시) 검색** — 메모리 검색을 툴로 노출하고, 에이전트가 필요하다고 판단할 때만 호출하게 한다. AgentCore의 agentic retrieval 통합에서도 "the agent decides whether to retrieve and composes its own query"가 기본 모델이다([AWS, Use agentic retrieval](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)). 불필요한 주입이 없는 대신, 에이전트가 검색해야 할 때 안 하는 실패가 생긴다.

이것은 [JIT 검색과 토큰 예산](/05-context/jit-retrieval-token-budget)에서 다룬 "프리로드 vs JIT" 결정의 메모리 버전이다. 같은 원칙이 적용된다: **거의 모든 턴에 쓰이는 것은 프리로드하고, 가끔 쓰이는 것은 JIT로 가져온다.** 실무 기본값은 하이브리드다 — 사용자 선호/프로필은 세션 시작 시 프리로드, 사실(fact)·과거 세션 요약은 agentic 또는 매 턴 top-k.

검색어 자체도 설계 대상이다. 사용자 발화를 그대로 검색어로 쓰는 방식과, LLM이 타깃 검색어를 생성하는 방식("Help me plan my next trip" → "travel preferences, destination history, budget constraints")이 있으며 후자는 관련성이 높은 대신 레이턴시가 추가된다([AWS ML Blog, Namespace design patterns](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)).

### 2. 무엇을 가져올 것인가: top-k 유사도 vs 열거 vs 계층 검색

AgentCore Memory 기준으로 검색 API는 용도가 갈린다([AWS ML Blog, Namespace design patterns](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)).

- **`RetrieveMemoryRecords`** — 의미 기반(semantic) top-k 검색. `searchCriteria.searchQuery`(최대 10,000자)와 `topK`(1~100)를 받는다([AWS CLI, retrieve-memory-records](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/retrieve-memory-records.html)). 에이전트 턴 중의 기본 검색 수단이다.
- **`ListMemoryRecords`** — 특정 네임스페이스의 레코드 열거. 사용자에게 "당신에 대해 기억하는 것" UI를 보여주거나, 감사(audit)·일괄 삭제 같은 관리 작업에 쓴다.
- **`GetMemoryRecord` / `DeleteMemoryRecord`** — 개별 레코드 조회·삭제. 사용자가 특정 기억을 수정/삭제하게 하는 메모리 관리 플로우용이다.

검색 범위는 두 필드로 제어한다. **`namespace`는 정확 일치(exact match)** — 그 경로에 저장된 레코드만 반환한다. **`namespacePath`는 계층 검색(hierarchical)** — 그 경로 아래 서브트리 전체를 검색한다. 두 필드 중 하나는 필수다([AWS CLI, retrieve-memory-records](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/retrieve-memory-records.html)). 예를 들어 `namespace="/actor/customer-123/preferences/"`는 선호만, `namespacePath="/actor/customer-123/"`은 그 사용자의 facts·preferences·세션 요약 전부를 검색한다. `namespacePath`는 "이 고객에 대해 아는 것 전부"류 기능에 유용하지만, 서브트리가 넓을수록 의도치 않은 레코드가 딸려올 확률이 커진다 — **기본값은 좁은 `namespace`, 계층 검색은 명시적 필요가 있을 때만.**

"최근성 가중(recency-weighted)" 검색은 AgentCore가 API 파라미터로 제공하는 기능이 아니다. 필요하면 애플리케이션에서 topK를 여유 있게 가져온 뒤 레코드 타임스탬프로 재랭킹하는 후처리로 구현한다.

::: warning 미정착 영역
top-k의 적정값, 유사도 점수 하한(threshold) 컷, semantic 점수와 recency의 가중 결합 비율은 업계 표준이 없는 영역이다. 벤더마다, 심지어 같은 벤더의 문서 간에도 권고가 다르다. 자기 워크로드의 평가셋으로 "주입된 메모리가 실제로 응답에 기여했는가"를 측정해 튜닝하는 것 외에 지름길이 없다.
:::

### 3. 네임스페이스: 스코핑의 물리적 단위

AgentCore Memory에서 장기 메모리 레코드는 항상 **네임스페이스**라는 계층 경로 아래 저장된다. 네임스페이스는 메모리 전략(strategy)을 정의할 때 템플릿으로 지정하며, 세 개의 내장 변수를 지원한다: `{actorId}`(메모리의 주인), `{sessionId}`(어느 대화에서 나왔나), `{memoryStrategyId}`(어느 전략이 추출했나)([AWS, Specify long-term memory organization with namespaces](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/specify-long-term-memory-organization.html)).

템플릿 `/strategy/{memoryStrategyId}/actor/{actorId}/session/{sessionId}/`는 이벤트 처리 시점에 실제 값으로 해석(resolve)되어 `/strategy/summarization-93483043/actor/actor-9830m2w3/session/session-9330sds8` 같은 경로가 된다. 공식 문서는 세분화 수준을 네 단계로 예시한다([AWS, Specify long-term memory organization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/specify-long-term-memory-organization.html)):

| 세분화 | 템플릿 |
|---|---|
| 세션 수준 | `/strategy/{memoryStrategyId}/actor/{actorId}/session/{sessionId}/` |
| 액터 수준(세션 횡단) | `/strategy/{memoryStrategyId}/actor/{actorId}/` |
| 전략 수준(액터 횡단) | `/strategy/{memoryStrategyId}/` |
| 전역 | `/` |

전략 유형별 권장 스코프는 명확하다([AWS ML Blog, Namespace design patterns](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)): **semantic(사실)·user preference는 액터 스코프**(`/actor/{actorId}/facts/`) — 1월에 배운 사실이 3월에도 검색돼야 하고, 같은 네임스페이스 안에서 consolidation 엔진이 관련 메모리를 병합하기 때문이다. **summary는 세션 스코프**(`/actor/{actorId}/session/{sessionId}/summary/`) — 요약은 본질적으로 특정 대화에 묶여 있다. 이 구분은 [메모리 유형](/07-memory/memory-types) 장의 단기/장기 구분과 직결된다.

단기 메모리(이벤트) 쪽 스코핑은 더 단순하다: 이벤트는 `actorId`와 `sessionId`로 조직되며([AWS, Memory terminology](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-terminology.html)), 세션 복원(`sessionBinding`) 시 "the `actorId` scopes the history, so one actor's history is never returned for another"([AWS, Use agentic retrieval](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)). 즉 **actorId가 단기·장기 양쪽에서 격리의 1차 키**다.

### 4. 테넌트+사용자 이중 스코핑

멀티테넌트 SaaS에서 "사용자별 격리"만으로는 부족하다. 서로 다른 테넌트의 `user-123`이 충돌하면 안 되고, 테넌트 단위 일괄 삭제(오프보딩·GDPR)도 가능해야 한다. 두 가지 구현 경로가 있다.

**(a) actorId 합성(composite key) 패턴** — actorId 자체에 테넌트를 인코딩한다:

```python
actor_id = f"{tenant_id}:{user_id}"   # 예: "tenant-a:user-123"
```

CreateEvent API의 actorId 패턴은 콜론으로 구분된 세그먼트를 명시적으로 허용하고(길이 1~255, 패턴에 `(?::[a-zA-Z0-9-_/]+)*` 포함), 이 합성 키가 네임스페이스 템플릿의 `{actorId}`에 그대로 해석되므로 `/actor/tenant-a:user-123/facts/`처럼 테넌트+사용자 이중 스코프가 경로에 박힌다([AWS, CreateEvent API Reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateEvent.html)). 장점은 단순함 — 모든 API 호출에서 actorId 하나만 올바르게 합성하면 단기(이벤트)와 장기(레코드) 격리가 동시에 걸린다. 단점은 테넌트 수준 연산(테넌트 전체 검색/삭제)이 경로 프리픽스 매칭에 의존하게 된다는 것, 그리고 합성 규칙이 코드 컨벤션일 뿐 스키마로 강제되지 않는다는 것이다. 합성 함수는 반드시 한 곳에 두고, `tenant_id`나 `user_id`에 구분자(`:`)가 포함될 수 없음을 입력 검증으로 보장하라 — `"a:b"`라는 user_id가 들어오면 스코프 경계가 이동하는 고전적 key-smuggling이 된다.

**(b) 커스텀 네임스페이스 변수 패턴** — 공식 문서는 "tenant, team, or environment 같은 추가 차원을 위해 custom namespace variables를 추가할 수 있다"고 안내한다([AWS, Specify long-term memory organization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/specify-long-term-memory-organization.html)). 템플릿을 `/tenant/{tenantId}/actor/{actorId}/facts/`로 정의하고, 이벤트 생성 시 `extractionConfig.namespaceVariables`로 값을 공급한다([AWS, CreateEvent API Reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateEvent.html)). 테넌트가 경로의 독립 세그먼트가 되므로 `namespacePath="/tenant/tenant-a/"`로 테넌트 전체 연산이 자연스럽고, IAM 정책도 깔끔해진다. 대신 이벤트 API 호출마다 변수를 챙겨야 하는 지점이 하나 늘어난다.

어느 쪽이든 **애플리케이션 코드만 믿지 말고 IAM으로 이중 방어**하라. AgentCore Memory는 `bedrock-agentcore:namespace`(StringEquals, 정확 일치)와 `bedrock-agentcore:namespacePath`(StringLike, 계층) condition key를 제공하며, `${aws:PrincipalTag/userId}` 같은 principal tag와 결합하면 "요청 주체는 자기 네임스페이스만 검색 가능"을 정책 수준에서 강제할 수 있다([AWS ML Blog, Namespace design patterns](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)):

```json
{
  "Effect": "Allow",
  "Action": ["bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
  "Resource": "arn:aws:bedrock-agentcore:us-east-1:123456789012:memory/mem-12345abcdef",
  "Condition": {
    "StringLike": {
      "bedrock-agentcore:namespacePath": "/actor/${aws:PrincipalTag/userId}/*"
    }
  }
}
```

이 이중 방어가 필요한 이유가 앞서 인용한 "agentic retrieval does not verify..."다 — 플랫폼은 호출자가 넘긴 namespace가 그 호출자의 것인지 검증하지 않으므로, 검증은 IAM condition이 하거나 아무도 안 하거나 둘 중 하나다. KMS 암호화·보존 기간 등 나머지 보안 축은 [메모리 보안과 프라이버시](/07-memory/memory-security-privacy)에서 다룬다.

### 5. 멀티 에이전트 간 메모리 공유

여러 에이전트(라우터+전문 에이전트, 또는 지원/영업/빌링 에이전트 군)가 하나의 메모리 리소스를 공유하는 것은 정상적인 설계다 — 네임스페이스는 "같은 저장소 안의 논리적 그룹"이므로, 격리는 리소스 분리가 아니라 경로 설계로 달성한다([AWS ML Blog, Namespace design patterns](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)). 경계 원칙:

- **공유해야 할 것**: 사용자에 대한 사실·선호 — 사용자가 지원 에이전트에게 말한 "우리는 서버리스 아키텍처를 쓴다"를 영업 에이전트가 다시 물어보면 UX 실패다. `/actor/{actorId}/facts/`처럼 에이전트 중립 경로에 둔다.
- **격리해야 할 것**: 에이전트별 작업 상태·중간 추론·세션 요약 — 다른 에이전트에게는 노이즈이거나 오해의 소지가 있다. 경로에 에이전트 차원을 넣어(`/agent/{agentName}/actor/{actorId}/...`) 분리한다. 참고로 AgentCore의 actor 개념 자체가 "end users or agent/user combinations"를 포괄하므로([AWS CDK, Amazon Bedrock AgentCore Construct Library](https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_bedrockagentcore/README.html)), actorId를 `agent-x:user-123`처럼 합성하는 것도 유효한 구현이다.
- **횡단 조회가 필요한 것**: 관리자·분석 에이전트가 여러 사용자를 가로질러 봐야 하는 데이터(예: 전 고객의 알려진 이슈)는 계층을 뒤집어 `/customer-issues/{actorId}/`처럼 **타입을 부모, 액터를 자식**으로 두면, `namespacePath="/customer-issues/"`로 횡단 검색과 `namespace="/customer-issues/customer-123/"`의 per-actor 격리를 동시에 얻는다([AWS ML Blog, Namespace design patterns](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)).

주의: 공유 범위가 넓어질수록 한 에이전트의 잘못된 쓰기(오염된 사실)가 모든 에이전트로 전파된다. 공유 네임스페이스에는 [메모리 쓰기 정책](/07-memory/memory-write-policies)의 검증 게이트를 더 엄격하게 적용하라.

### 6. 주입 위치와 캐시 히트

검색한 메모리를 프롬프트 어디에 넣는가는 캐시 경제성 문제다. 메모리는 본질적으로 **per-user, per-turn 가변 데이터**이므로, [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes)에서 다룬 per-user 데이터 배치 규칙이 그대로 적용된다: 캐시 브레이크포인트보다 앞(시스템 프롬프트의 정적 영역)에 메모리를 넣으면 사용자마다·턴마다 프리픽스가 달라져 tools+system 캐시 공유가 깨진다. Anthropic 문서 기준으로 캐시 히트는 브레이크포인트까지의 프리픽스가 100% 동일해야 하므로("Cache hits require 100% identical prompt segments", [Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)), 올바른 위치는 **브레이크포인트 뒤 — 시스템 프롬프트의 동적 꼬리 또는 messages 앞부분의 별도 블록**이다.

추가 규칙 두 가지. (1) 매 턴 검색이라면 주입 블록을 대화 히스토리 **앞이 아니라 최신 user 턴에 인접**시켜라 — 히스토리 앞에 끼워 넣으면 턴마다 히스토리 전체의 프리픽스가 밀려 messages 캐시까지 무효화된다. (2) 세션 시작 시 1회 프리로드한 메모리는 세션 내내 바이트 단위로 동일하게 유지하라(재직렬화 순서 포함) — 그래야 세션 두 번째 턴부터는 그 블록도 캐시 프리픽스의 일부가 된다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 사용자 선호·프로필 등 저변동 메모리 | 세션 시작 시 1회 프리로드, 세션 내 불변 유지 | 매 턴 검색 비용 제거 + 프리픽스 안정으로 캐시 친화 | 세션 중 갱신된 선호는 다음 세션부터 반영 |
| 과거 사실·세션 요약 등 쿼리 의존 메모리 | agentic 검색(툴 노출) 또는 매 턴 top-k | 필요할 때만 토큰 소비, 관련성 높음 | agentic은 "검색 안 함" 실패, 매 턴은 레이턴시+캐시 교란 |
| 특정 타입만 정확히 필요(선호만, 요약만) | `namespace` 정확 일치 | 의도치 않은 레코드 유입 차단 | 타입별로 검색 호출이 늘 수 있음 |
| "이 사용자에 대해 아는 것 전부" | `namespacePath` 계층 검색 | 서브트리 일괄 검색 | 격리 경계 검토 필수 — 넓은 경로는 유출 표면 |
| 멀티테넌트, 빠르게 시작 | actorId 합성(`tenant-a:user-123`) | API 패턴이 콜론 세그먼트 허용, 단기+장기 동시 격리, 코드 변경 최소 | 합성 규칙이 컨벤션일 뿐 — 입력 검증·중앙화 필수 |
| 멀티테넌트, 테넌트 단위 연산(삭제·감사·IAM) 많음 | 커스텀 네임스페이스 변수 `/tenant/{tenantId}/actor/{actorId}/` | 테넌트가 독립 경로 세그먼트 — namespacePath·IAM 정책이 자연스러움 | 이벤트마다 `extractionConfig.namespaceVariables` 공급 필요 |
| 여러 에이전트가 같은 사용자 서빙 | 사실/선호는 에이전트 중립 경로 공유, 에이전트 작업 상태는 에이전트별 경로 격리 | 사용자 지식 재사용 + 에이전트 노이즈 차단 | 공유 네임스페이스의 쓰기 품질 관리 부담 증가 |
| 관리자의 액터 횡단 조회 | 계층 역전: `/{type}/{actorId}/` | 타입 레벨 `namespacePath`로 횡단, 액터 레벨 `namespace`로 격리 병행 | 사용자 단위 "전부 보기"는 타입별 다중 조회로 대체 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 사용자 B의 대화에 사용자 A의 정보가 등장 | 검색 호출의 actorId/namespace를 세션 컨텍스트가 아닌 전역 변수·캐시된 클라이언트에서 가져옴, 또는 `namespacePath`를 액터 상위 레벨(`/strategy/.../`)로 지정 | 검색 요청 로그에서 namespace 파라미터와 인증된 사용자 ID를 대조, 크로스 유저 카나리 레코드(사용자별 고유 마커)를 심고 다른 사용자 세션에서 검색되는지 테스트 | 스코프 파라미터를 요청 인증 컨텍스트에서만 파생하도록 강제 + IAM condition key(`bedrock-agentcore:namespace(-Path)`)로 이중 방어 |
| 서로 다른 테넌트의 동일 user id 메모리가 병합됨 | actorId에 테넌트 미포함(`user-123`만 사용) — consolidation이 같은 네임스페이스의 레코드를 병합 | `ListMemoryRecords`로 해당 네임스페이스를 열거해 두 테넌트의 사실이 섞여 있는지 확인 | actorId 합성(`{tenant}:{user}`) 또는 테넌트 변수 도입, 기존 오염 네임스페이스는 레코드 삭제 후 재추출 |
| 검색 결과가 항상 비어 있음(레코드는 존재) | `namespace` 정확 일치인데 저장 경로와 조회 경로가 불일치 — 슬래시 유무, 템플릿 변수 미해석(리터럴 `{actorId}` 전달), 합성 규칙 불일치 | `ListMemoryRecords`를 상위 `namespacePath`로 호출해 실제 저장 경로를 확인하고 조회 경로와 diff | 네임스페이스 문자열 생성을 단일 유틸리티로 통일, leading/trailing slash 규약 고정([AWS 권고](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/)), 템플릿은 해석된 값으로 공급([AWS, agentic retrieval](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html)) |
| 턴당 입력 토큰이 세션 길이와 무관하게 지속 증가 | 매 턴 top-k 주입 + 이전 턴에 주입한 메모리를 히스토리에서 제거하지 않음 — 같은 메모리가 턴 수만큼 중복 누적 | 요청 페이로드에서 동일 메모리 레코드 텍스트의 출현 횟수를 카운트 | 주입 블록을 "최신 턴에만 존재"하도록 이전 턴의 주입 블록을 히스토리에서 치환·제거, 또는 세션 프리로드로 전환 |
| 메모리 기능 도입 후 캐시 히트율 급락 | 검색 결과를 시스템 프롬프트 정적 영역(브레이크포인트 앞)에 삽입 | 연속 두 요청의 system 필드 diff — 메모리 텍스트 위치 확인([캐시 미스 근본 원인](/04-caching/cache-miss-root-causes)의 진단법) | 주입 위치를 브레이크포인트 뒤(동적 블록/messages)로 이동 |
| 무관한 옛 메모리가 응답을 오염("작년 프로젝트" 기준으로 답변) | topK 고정 + 점수 하한 없음 — 관련 레코드가 부족해도 k개를 채워 반환 | 검색 결과의 유사도 점수 분포 로깅, 저점수 레코드가 주입되는 비율 측정 | 애플리케이션 레벨 점수 컷 + 타임스탬프 기반 재랭킹, 주입 시 "관련될 수도 있는 과거 기억" 프레이밍으로 모델의 맹신 방지 |
| 에이전트 간 응답 혼선(지원 에이전트가 영업 에이전트의 중간 메모를 사실로 인용) | 에이전트 작업 상태를 에이전트 중립 공유 네임스페이스에 기록 | 공유 네임스페이스의 레코드를 열거해 출처 에이전트별로 분류 | 에이전트별 경로 분리, 공유 경로에는 검증된 사용자 사실만 쓰기 허용 |

## 안티패턴

- ❌ 검색 스코프(actorId/namespace)를 클라이언트가 보낸 요청 바디에서 그대로 신뢰 → ✅ 서버가 인증 토큰에서 파생한 값만 사용하고, IAM condition key로 정책 수준에서 재강제한다.
- ❌ "일단 넓게" — 기본 검색을 `namespacePath="/"` 또는 전략 레벨로 잡고 나중에 좁히기 → ✅ 가장 좁은 `namespace` 정확 일치에서 시작해, 명시적 요구가 생길 때만 계층을 연다.
- ❌ 테넌트 격리를 프롬프트로 구현("다른 테넌트 정보는 무시하세요") → ✅ 격리는 검색 경로와 IAM에서 끝낸다 — 모델에게 도달한 데이터는 이미 유출된 것이다.
- ❌ actorId 합성 문자열을 호출부마다 f-string으로 즉석 조립 → ✅ 합성·파싱·검증을 단일 모듈로 중앙화하고 구분자 문자를 입력에서 금지한다.
- ❌ 매 턴 top-k를 히스토리에 append만 하고 회수하지 않음 → ✅ 주입 블록은 최신 턴에만 존재하게 하고, 이전 주입은 제거한다.
- ❌ 세션 요약을 액터 스코프 사실 네임스페이스에 저장 → ✅ 요약은 `/actor/{actorId}/session/{sessionId}/summary/`로 세션 스코프, 사실·선호만 액터 스코프에 둔다.
- ❌ 프리로드 메모리를 턴마다 재검색·재직렬화(순서 비결정) → ✅ 세션 시작 시 1회 확정한 바이트열을 세션 내내 재사용해 캐시 프리픽스를 지킨다.

## 계측 (SLI)

- **스코프 위반 시도율**: IAM condition key로 거부된 `RetrieveMemoryRecords`/`ListMemoryRecords` 호출 수(CloudTrail의 `AccessDeniedException`). 0이 아니면 애플리케이션 스코핑 버그가 IAM에 걸리고 있다는 뜻 — 방어가 작동한 것이지만 근본 원인을 잡아야 한다.
- **크로스 스코프 카나리**: 사용자·테넌트별 고유 마커 레코드를 심고, 합성 트래픽으로 다른 스코프에서 검색되는지 주기 점검. 검출 즉시 페이지(page)할 사고 수준 신호다.
- **턴당 메모리 주입 토큰**: 주입 블록의 토큰 수 분포(p50/p99). [토큰 예산](/05-context/jit-retrieval-token-budget)에서 메모리에 할당한 몫을 초과하면 topK/점수 컷을 조인다.
- **검색 기여율**: 주입된 레코드가 응답에 실제로 인용·활용된 비율(LLM-judge 또는 휴리스틱). 낮으면 검색 트리거·쿼리 생성 전략을 재검토한다.
- **검색 레이턴시**: `RetrieveMemoryRecords` p99 — 매 턴 검색이면 턴 레이턴시에 직접 가산된다.
- **캐시 히트율의 메모리 상관**: 메모리 주입 on/off(또는 위치 변경) 배포 전후의 `cache_read_input_tokens` 비율 비교([캐시 지표와 경제성](/04-caching/cache-metrics-economics) 참고).
- **빈 결과율**: 레코드가 존재하는 액터에 대한 검색이 0건을 반환하는 비율 — 네임스페이스 경로 불일치 버그의 조기 신호.

AgentCore 레벨의 트레이스·메트릭 수집(ADOT SDK 계측, `bedrock-agentcore` CloudWatch 네임스페이스)은 관측성 장의 범위이므로 여기서는 SLI 정의만 다룬다.

## 체크리스트

- [ ] 검색 트리거가 메모리 유형별로 명시돼 있다(프리로드 대상 / 매 턴 대상 / agentic 대상)
- [ ] 모든 검색 호출의 `namespace`/`namespacePath`/`actorId`가 인증 컨텍스트에서만 파생된다 — 클라이언트 입력 직접 사용 금지
- [ ] 멀티테넌트라면 actorId 합성 또는 테넌트 네임스페이스 변수 중 하나가 선택돼 있고, 합성 규칙이 단일 모듈에 중앙화·입력 검증돼 있다
- [ ] IAM 정책에 `bedrock-agentcore:namespace`/`namespacePath` condition key가 걸려 있어 애플리케이션 버그가 정책에서 차단된다
- [ ] `namespacePath` 계층 검색을 쓰는 모든 경로에 대해 서브트리에 무엇이 포함되는지 검토했다
- [ ] 네임스페이스 경로 문자열(슬래시 규약 포함)을 생성하는 유틸리티가 저장·조회 양쪽에서 동일하다
- [ ] 멀티 에이전트 공유 시 에이전트 중립(사실·선호) 경로와 에이전트 전용(작업 상태) 경로가 분리돼 있다
- [ ] 메모리 주입 블록이 캐시 브레이크포인트 뒤에 위치하고, 매 턴 주입은 이전 턴 주입을 회수한다
- [ ] topK와 점수 컷이 토큰 예산과 연동돼 있고, 주입 토큰 분포를 계측한다
- [ ] 크로스 스코프 카나리 테스트가 CI 또는 주기 잡으로 돌고 있다

## 참고

- [AWS, Memory terminology (Amazon Bedrock AgentCore Developer Guide)](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-terminology.html) — actor/session/namespace/memory record/strategy의 공식 정의
- [AWS, Specify long-term memory organization with namespaces](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/specify-long-term-memory-organization.html) — 네임스페이스 템플릿 변수(`{actorId}`, `{sessionId}`, `{memoryStrategyId}`), 세분화 4단계, 커스텀 변수(tenant/team/environment)
- [AWS ML Blog, Organizing Agents' memory at scale: Namespace design patterns in AgentCore Memory (2026-04-29)](https://aws.amazon.com/blogs/machine-learning/organizing-agents-memory-at-scale-namespace-design-patterns-in-agentcore-memory/) — 전략별 스코프 권장, `namespace` vs `namespacePath`, 계층 역전 패턴, IAM condition key 정책 예시
- [AWS CLI, bedrock-agentcore retrieve-memory-records](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/retrieve-memory-records.html) — `topK`(1~100), `searchQuery`(최대 10,000자), `namespace`/`namespacePath` 택일 필수의 출처
- [AWS, CreateEvent (AgentCore API Reference)](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateEvent.html) — actorId 길이(1~255)·콜론 세그먼트 허용 패턴, `extractionConfig.namespaceVariables`, `extractionMode: SKIP`
- [AWS, Use agentic retrieval to query a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-test-agentic-retrieve.html) — `sessionBinding`의 actorId 격리, "호출자가 스코프 값 정확성에 책임" 명시
- [Anthropic, Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — 프리픽스 정확 매칭 요구사항(주입 위치 논의의 근거)
- 관련 장: [JIT 검색과 토큰 예산](/05-context/jit-retrieval-token-budget), [캐시 미스 근본 원인](/04-caching/cache-miss-root-causes), [메모리 유형](/07-memory/memory-types), [메모리 쓰기 정책](/07-memory/memory-write-policies), [메모리 보안과 프라이버시](/07-memory/memory-security-privacy)

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요
> `actor_id: "tenant-a:user-123"` 합성 패턴 자체는 AWS가 문서로 "권장"한 패턴이 아니라, CreateEvent API의 actorId 패턴이 콜론 세그먼트를 허용한다는 사실과 CDK 문서의 "actor는 end users or agent/user combinations"라는 정의에서 도출한 실무 패턴이다. 테넌트 격리에 규제 요건이 걸린 워크로드라면 공식 경로인 커스텀 네임스페이스 변수 방식을 우선 검토하라.
