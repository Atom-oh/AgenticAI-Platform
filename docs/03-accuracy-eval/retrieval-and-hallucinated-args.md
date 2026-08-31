---
title: 검색과 환각된 인자
description: 에이전트 정확도를 깨는 두 갈래 실패 — 검색 실패가 생성 실패로 전이되는 경로와, 환각된 툴 인자를 계층 방어로 결정론적으로 거부하는 방법을 다룬다.
outline: [2, 3]
---

# 검색과 환각된 인자

::: tip 이 장에서 얻는 것
- "모델이 틀렸다"는 사고 보고서를 **검색 실패**와 **환각된 인자**라는 두 개의 서로 다른 근본 원인으로 분해하는 분류 체계
- 검색 실패가 생성 실패로 전이되는 메커니즘 — retrieval이 나쁘면 faithfulness 점수가 높아도 답이 틀리는 이유
- 환각된 툴 인자의 4가지 전형(존재하지 않는 ID, 허용되지 않은 enum, 단위 착오, 남의 리소스 ID)과 각각이 뚫는 방어선
- 4계층 방어: JSON Schema 제약 → 실행 전 존재/소유권 검증 → Cedar 인자 정책 → 에러 피드백 재시도
- "에이전트가 생성한 모든 파라미터는 untrusted input"이라는 원칙의 근거와 실무 적용
- schema rejection rate, empty-result rate, retry success rate 등 이 실패군을 조기에 드러내는 SLI
:::

## 왜 문제가 되는가

에이전트가 틀린 답을 내놓았을 때 온콜 엔지니어가 받는 티켓은 대개 "AI가 이상한 소리를 한다" 한 줄이다. 그러나 트레이스를 열어 보면 근본 원인은 최소 두 갈래로 갈라진다.

**첫째, 검색 실패(retrieval failure).** RAG 파이프라인이 잘못된 문서, 오래된 문서, 혹은 아무 문서도 가져오지 못했는데 모델이 그 위에서 그럴듯한 답을 생성한 경우다. 이때 모델 자체는 "주어진 컨텍스트에 충실하게" 동작했을 수 있다 — 컨텍스트가 틀렸을 뿐이다. RAG의 원 논문이 제시한 가치가 "파라메트릭 지식을 검색된 비파라메트릭 지식으로 보강해 사실성을 높인다"는 것이었음을 상기하면([Lewis et al., 2020](https://arxiv.org/abs/2005.11401)), 검색이 무너지면 그 전제 자체가 무너진다.

**둘째, 환각된 툴 인자(hallucinated arguments).** 모델이 툴을 호출하되 존재하지 않는 `customer_id`, 스키마에 없는 enum 값, 임의로 지어낸 파라미터를 채워 넣는 경우다. 이쪽은 검색과 무관하게 발생하며, 읽기 툴이면 오답으로, 쓰기 툴이면 **잘못된 부수효과**(엉뚱한 계정 환불, 남의 리소스 조회)로 이어진다. OWASP LLM Top 10은 이런 하류 시스템으로의 무검증 전달을 LLM05 Improper Output Handling으로, 과도한 행동 권한과 결합된 형태를 LLM06 Excessive Agency로 분류한다.[[OWASP LLM05]](https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/) [[OWASP LLM06]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

두 실패는 증상이 비슷해 보여도(둘 다 "정확도 문제"로 접수된다) **원인, 재현 방법, 수리 지점이 완전히 다르다.** 검색 실패는 인덱스·청킹·쿼리 재작성 문제이고, 환각된 인자는 툴 계약·검증·인가 문제다. 이 구분 없이 "프롬프트를 고치자"로 대응하면 두 실패 모두 재발한다. 이 장의 목표는 이 분류를 트리아지 절차와 방어 계층으로 고정하는 것이다.

## 핵심 개념

### 검색 실패는 생성 실패로 전이된다

RAG 평가에서 가장 널리 쓰이는 분해는 세 축이다: 검색된 컨텍스트가 질문과 관련 있는가(context relevance), 답이 컨텍스트에 근거하는가(groundedness/faithfulness), 답이 질문에 맞는가(answer relevance). TruLens는 이를 "RAG Triad"로 정식화하며, **세 축을 모두 만족해야만 환각 없음을 주장할 수 있다**고 명시한다.[[TruLens RAG Triad]](https://www.trulens.org/getting_started/core_concepts/rag_triad/) Ragas의 faithfulness 메트릭 정의도 같은 구조다 — 응답의 각 claim이 retrieved context에서 추론 가능한지를 측정할 뿐, **context 자체가 옳은지는 측정하지 않는다**.[[Ragas Faithfulness]](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)

여기서 전이 메커니즘이 나온다.

1. **오염 전이**: retrieval이 오래된 정책 문서 v1을 가져오면, 모델은 v1에 완벽히 faithful한 — 그러나 현재 기준으로 틀린 — 답을 생성한다. faithfulness 1.0, 정답률 0. 검색 품질이 나쁘면 faithfulness는 무의미한 지표가 된다.
2. **공백 전이**: retrieval이 빈 결과 또는 주변부 문서만 가져오면, 모델은 파라메트릭 지식으로 공백을 메운다. 이때 생성물은 컨텍스트에 근거하지 않으므로 faithfulness가 낮게 나오지만, 사용자에게는 근거 있는 답과 구별되지 않는 유창한 문장으로 전달된다. OWASP는 이렇게 모델이 통계적 패턴으로 공백을 메우는 현상을 LLM09 Misinformation의 핵심 원인으로 지목한다.[[OWASP LLM09]](https://genai.owasp.org/llmrisk/llm092025-misinformation/)
3. **인자 전이**: 에이전틱 파이프라인에서는 검색 결과가 곧 다음 툴 호출의 인자 재료가 된다. 잘못 검색된 문서에 적힌 legacy ID를 모델이 그대로 다음 호출에 넣으면, 검색 실패가 환각된 인자와 같은 증상(존재하지 않는 ID 호출)으로 나타난다. 트리아지에서 두 실패군을 오가며 봐야 하는 이유다.

따라서 정확도 사고의 트리아지 1번 질문은 항상 같다: **"그 턴에 검색된 컨텍스트를 열어봤는가?"** 컨텍스트가 틀렸으면 검색 문제(인덱스 신선도, 청킹, 쿼리)로, 컨텍스트는 맞는데 답이 틀렸으면 생성 문제(faithfulness, 프롬프트)로, 컨텍스트와 무관한 값이 툴 인자에 나타났으면 환각된 인자 문제로 라우팅한다.

recall@k, precision@k, MRR/NDCG 같은 검색 품질 지표의 정의·측정 방법·골든셋 구축은 Part 6 [retrieval-evaluation](../06-vector-search/retrieval-evaluation)이 정본이다. 이 장에서는 그 지표들을 "정확도 사고의 원인 분류에 어느 지표가 어떤 가설을 기각하는가" 관점으로만 소비한다 — recall@k가 낮으면 오염/공백 전이 가설이 강화되고, recall@k가 높은데도 오답이면 생성 측 또는 인자 측으로 넘어간다.

### 환각된 인자의 전형 4가지

툴 인자 환각은 무작위로 발생하지 않는다. 실무에서 반복되는 전형은 네 가지이며, 각각 뚫는 방어선이 다르다.

1. **존재하지 않는 식별자.** `customer_id="cust_12345"` — 형식은 그럴듯하지만 DB에 없는 값. 대화 앞부분에 실제 ID가 없었거나, 검색 결과에서 잘못 복사했거나, 모델이 예시 패턴에서 지어낸다. Part 1 [tool-design](../01-agent-design/tool-design)에서 다뤘듯 파라미터 이름·설명의 모호성이 이 추측을 유발한다 — Anthropic의 툴 작성 가이드는 `user` 같은 모호한 이름 대신 `user_id`처럼 의미가 고정된 이름을 쓰고, description에 형식 예시를 넣으라고 권고한다.[[Anthropic — Writing tools for agents]](https://www.anthropic.com/engineering/writing-tools-for-agents)
2. **허용되지 않은 enum 값.** 스키마의 `status`가 `["active", "suspended"]`인데 모델이 `"disabled"`를 넣는 경우. 스키마에 `enum`이 선언돼 있으면 검증 계층에서 결정론적으로 잡히지만, 스키마가 `"type": "string"`으로만 선언돼 있으면 그대로 백엔드까지 흘러간다. JSON Schema의 `enum` 키워드는 정확히 이 용도의 제약이다.[[JSON Schema — enum]](https://json-schema.org/understanding-json-schema/reference/enum)
3. **단위·통화 착오.** "5만 원 환불해줘"를 `amount=50000`으로 넣었는데 툴의 계약 단위가 USD인 경우. 값 자체는 스키마상 유효하므로(숫자 맞음, 범위 안일 수도 있음) 스키마 검증을 통과한다. 방어는 계약을 스키마에 명시하는 것(`amount_krw`처럼 단위를 이름에 박거나, `currency` 필드를 required enum으로 강제)과 금액 상한을 정책 계층에 두는 것이다.
4. **유효하지만 남의 리소스 ID.** `account_id`가 실존하지만 현재 사용자 소유가 아닌 경우. 스키마 검증도, 존재 검증도 통과한다 — 이것은 정확도 문제인 동시에 인가 문제이며, IDOR와 confused deputy의 에이전트 버전이다. 방어는 Part 9의 소유권 검증과 [confused-deputy](../09-authorization/confused-deputy), [per-tool-obo](../09-authorization/per-tool-obo)의 on-behalf-of 자격증명 축소가 담당한다.

이 네 전형을 나란히 놓으면 핵심이 보인다: **단일 검증 지점으로는 네 가지를 모두 막을 수 없다.** ①·②는 스키마가, ①·④는 실행 전 검증이, ③·④는 정책이 잡는다. 그래서 방어는 계층이어야 한다.

### 원칙: 에이전트가 생성한 모든 파라미터는 untrusted input이다

방어 계층 설계의 출발점은 신뢰 경계를 올바르게 긋는 것이다. LLM이 생성한 툴 인자는 — 그 LLM이 아무리 좋은 모델이어도 — 사용자가 웹 폼에 입력한 값과 같은 등급의 **untrusted input**으로 취급해야 한다. OWASP LLM05는 "LLM 생성 콘텐츠를 검증·새니타이즈 없이 하류 컴포넌트에 전달하는 것" 자체를 취약점으로 정의하며, LLM 출력에 대해 zero-trust 접근과 입력 검증 적용을 권고한다.[[OWASP LLM05]](https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/) AWS의 AgentCore Policy 설계 문서 역시 같은 구분 위에 서 있다 — JWT 클레임(위조 불가능한 신원 신호)과 LLM이 생성한 툴 인자(정책으로 제약해야 하는 가변 입력)를 명시적으로 분리하고, 후자를 Cedar 정책의 `context.input.*`으로 결정론적으로 제약한다.[[AWS — Policy in AgentCore core concepts]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) [[AWS Security Blog — Why Policy in AgentCore chose Cedar]](https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/)

이 원칙의 실무적 귀결은 세 가지다.

- 인자 검증을 "모델이 잘 뽑겠지"에 맡기지 않는다. 프롬프트로 "정확한 ID만 사용하세요"라고 지시하는 것은 확률적 완화일 뿐 제어가 아니다.
- 검증 실패는 예외가 아니라 **정상 경로의 하나**다. 발생을 전제로 계측하고, 모델에게 되돌려 재시도시키는 루프까지가 설계 범위다.
- 위조 불가능한 신호(인증된 세션의 user ID, JWT 클레임)는 절대 LLM이 채우게 하지 않는다 — 파라미터에서 아예 빼고 서버 측에서 주입한다. LLM이 채울 필요가 없는 값은 환각될 수도 없다.

### 4계층 방어

**계층 1 — 스키마 검증 (툴 정의 단계).** 표현 가능한 모든 제약을 JSON Schema에 인코딩한다: `enum`(허용 값 집합), `pattern`(ID 형식 — 예: `"^cust_[0-9a-z]{12}$"`), `minimum`/`maximum`(수치 범위), `format`(date-time 등), `required`, 그리고 `additionalProperties: false`(지어낸 파라미터 차단).[[JSON Schema Reference]](https://json-schema.org/understanding-json-schema/reference) 이 계층의 장점은 두 방향으로 작동한다는 것이다 — 사후 검증기로서 위반을 거부할 뿐 아니라, 스키마 자체가 모델에 전달되는 프롬프트의 일부이므로 **사전에 환각 확률을 낮춘다**. 제약된 디코딩을 지원하는 스택에서는 스키마 위반이 생성 단계에서 원천 차단되기도 한다 — OpenAI는 Structured Outputs 도입 시 스키마 준수 평가에서 100% 일치를 보고했다.[[OpenAI — Introducing Structured Outputs]](https://openai.com/index/introducing-structured-outputs-in-the-api/) 단, 제약 디코딩은 "스키마상 유효한 값"을 보장할 뿐 "의미상 옳은 값"(실존하는 ID, 올바른 단위)을 보장하지 않는다. 스키마 설계 원칙 전반은 [tool-design](../01-agent-design/tool-design)을 따른다.

**계층 2 — 실행 전 존재/소유권 검증 (툴 구현 내부).** 스키마를 통과한 인자에 대해, 부수효과를 일으키기 **전에** 참조 무결성을 확인한다: `customer_id`가 실존하는가, 이 리소스가 요청 주체 소유인가, 이 상태 전이가 현재 상태에서 유효한가. 쓰기 툴이라면 이 검증과 실행 사이에 race가 없도록 트랜잭션 경계 안에서 확인한다. 존재하지 않는 ID에 대한 응답은 조용한 빈 결과가 아니라 명시적 not-found 에러여야 한다 — 빈 결과는 모델이 "데이터가 없다"로 오독하고, 그 오독 위에 다음 환각을 쌓는다.

**계층 3 — 정책 계층의 인자 제약 (Cedar).** 툴 구현이 검증을 빠뜨려도, 게이트웨이 레벨의 Cedar 정책이 인자에 대한 최후 방어선을 친다. `context.input.amount < 500` 같은 조건은 모델이 금액을 얼마로 환각하든 결정론적으로 평가된다 — LLM이 $50,000을 넣어도 정책이 거부한다.[[AWS — Understanding Cedar policies]](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html) 계층 2와의 차이는 소유 주체다: 계층 2는 툴 개발팀의 코드이고, 계층 3은 플랫폼/보안팀이 소유하는 선언적 정책이라 툴 코드 배포 없이 조이거나 풀 수 있다. 정책 문법, 4개 집행점, LOG_ONLY→ENFORCE 운영 절차는 [cedar-verified-permissions](../09-authorization/cedar-verified-permissions)에서 다뤘다.

**계층 4 — 에러 피드백 재시도 (에이전트 루프).** 계층 1~3의 거부는 파이프라인의 끝이 아니라 모델에게 되돌아가는 입력이다. "어떤 필드가, 왜, 어떻게 고치면 되는지"를 담은 구조화된 에러를 반환하면 모델은 다음 턴에 교정된 호출을 만들 수 있다 — Anthropic의 권고대로 불투명한 에러 코드가 아니라 구체적이고 실행 가능한 개선안을 담아야 한다.[[Anthropic — Writing tools for agents]](https://www.anthropic.com/engineering/writing-tools-for-agents) 재시도가 무의미한 실패(권한 거부, 정책 거부)는 `retryable: false`를 명시해 무한 루프를 끊는다. 에러 메시지 설계의 세부는 [tool-design의 에러 설계](../01-agent-design/tool-design#에러-메시지는-모델에게-되돌아가는-입력이다)를 따른다. 재시도 예산(예: 인자 검증 실패 최대 2회 재시도 후 사용자에게 보고)을 루프에 하드코딩하는 것까지가 이 계층이다.

::: warning 미정착 영역
계층 4의 재시도 예산과 "검증 에러를 얼마나 상세히 모델에 노출할 것인가"는 아직 업계 합의가 없다. 상세한 에러는 교정 성공률을 높이지만, enum 전체 목록이나 존재하는 ID의 힌트를 에러에 담으면 열거(enumeration) 공격 표면이 된다 — 예를 들어 "cust_123은 없지만 cust_124는 있음"을 구별할 수 있는 에러는 ID 스캔에 악용될 수 있다. 신뢰 수준이 낮은 입력 경로(외부 사용자 대화)에서는 에러 상세도를 낮추고, 내부 운영 에이전트에서는 높이는 식의 차등이 현재로서는 합리적 절충이다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 인자 값이 닫힌 집합(상태, 카테고리, 통화) | 스키마 `enum`으로 선언 (계층 1) | 결정론적 거부 + 모델에 허용 집합이 프롬프트로 전달돼 사전 예방 | enum이 크면(수백 개) 토큰 비용 증가 — 그 경우 검색 툴로 값을 먼저 조회하게 설계 |
| 인자 값이 열린 집합이지만 형식이 고정(ID, 날짜) | 스키마 `pattern`/`format` + 계층 2 존재 검증 | 형식 위반은 계층 1에서, 실존 여부는 계층 2에서만 판정 가능 | pattern은 형식만 보장 — 존재 검증 없이 쓰면 "그럴듯한 가짜 ID"가 통과 |
| 금액·수량 등 비즈니스 한도가 있는 수치 | 스키마 `maximum` + Cedar `context.input` 상한 (계층 1+3) | 한도는 보안 속성이므로 툴 코드와 독립적으로 정책팀이 소유해야 함 | 두 곳의 한도가 어긋나면 혼란 — 스키마는 기술적 상한, 정책은 비즈니스 상한으로 역할 분리 |
| 인증 주체 자신의 식별자(현재 사용자 ID) | 파라미터에서 제거, 서버 측 주입 | LLM이 채우지 않는 값은 환각 불가 — 계층 ④번 전형(남의 ID) 원천 차단 | 툴이 "다른 사용자 조회" 관리 기능을 겸하면 별도 admin 툴로 분리 필요 |
| 오답 사고의 원인 분류 | 트레이스에서 retrieved context를 먼저 확인 | 검색 실패와 생성 실패는 수리 지점이 다름 — 컨텍스트 오염이면 프롬프트 수정은 헛수고 | 트레이스에 컨텍스트 원문 저장 필요 → 저장 비용·PII 처리 부담 |
| 검색 품질 자체의 정량 평가 | Part 6 [retrieval-evaluation](../06-vector-search/retrieval-evaluation)의 골든셋 기반 recall@k 등 | 이 장의 트리아지는 원인 분류까지만 — 지표 정의·개선 루프는 Part 6이 정본 | — |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 답이 유창하고 컨텍스트에 충실한데 사실과 다름 | 오염 전이 — 오래된/잘못된 문서가 검색됨 | 트레이스에서 retrieved chunks 원문 확인, 문서 버전 대조 | 인덱스 신선도 파이프라인 점검([index-freshness-migration](../06-vector-search/index-freshness-migration)), 문서 메타데이터에 유효기간 |
| 답에 근거가 없는데 확신에 찬 어조 | 공백 전이 — empty/저관련 검색 결과를 파라메트릭 지식으로 메움 | empty-result rate 지표, faithfulness 점수 확인 | "검색 결과 없음"을 명시적 신호로 모델에 전달하고 모른다고 답하게 프롬프트 계약, 쿼리 재작성 |
| 존재하지 않는 ID로 툴 호출 | 전형 ① — 컨텍스트에 실제 ID가 없어 모델이 형식을 모방해 생성 | 툴 로그에서 not-found 에러율, 해당 턴 컨텍스트에 ID 출처 존재 여부 | ID는 반드시 선행 조회 툴의 응답에서 나오도록 워크플로 설계, `pattern` + 계층 2 검증 |
| 스키마에 없는 enum 값·파라미터로 호출 | 전형 ② — 스키마 제약 미선언 또는 유사 툴의 스키마와 혼동 | schema rejection rate, 거부된 페이로드 샘플링 | `enum`/`additionalProperties: false` 선언, 유사 툴 간 파라미터 네이밍 통일([tool-design](../01-agent-design/tool-design)) |
| 금액·수량이 자릿수 단위로 어긋남 | 전형 ③ — 단위/통화 계약이 스키마에 없음 | 정책 거부 로그(Cedar), 이상치 탐지 | 단위를 파라미터 이름에 명시(`amount_krw`), `currency` required enum, Cedar 상한 |
| 유효한 호출인데 남의 데이터가 반환/변경됨 | 전형 ④ — 소유권 검증 부재, 과도한 서비스 자격증명 | 감사 로그에서 요청 주체와 리소스 소유자 대조 | 서버 측 주체 주입 + 계층 2 소유권 검증 + [per-tool-obo](../09-authorization/per-tool-obo) |
| 같은 검증 에러로 무한 재시도 | 계층 4 설계 결함 — 에러에 교정 정보 없음 또는 retryable 미표시 | 턴 로그에서 동일 툴 연속 실패 패턴 | 에러에 필드·형식·예시 포함, `retryable: false` 명시, 루프에 재시도 예산 |
| 검증을 강화했더니 태스크 완료율 하락 | 계층 1~3 거부가 계층 4 없이 dead-end로 끝남 | retry-after-rejection success rate 확인 | 거부 응답을 모델이 소비 가능한 구조화 에러로 재설계 |

## 안티패턴

- ❌ 프롬프트에 "반드시 실제 customer_id만 사용하세요"라고 지시하고 검증 생략 → ✅ 지시는 유지하되, `pattern` 스키마 + 실행 전 존재 검증 + Cedar 제약으로 결정론적 방어. 프롬프트는 확률을 낮출 뿐 보장하지 않는다.
- ❌ 현재 사용자의 ID를 LLM이 파라미터로 채우게 함 → ✅ 인증 세션에서 서버 측 주입. LLM이 채우지 않는 값은 환각될 수 없다.
- ❌ `"type": "string"`만 선언하고 허용 값을 description 산문에 나열 → ✅ `enum`으로 선언. description은 모델만 읽지만 `enum`은 검증기와 (지원 시) 제약 디코더도 읽는다.
- ❌ 존재하지 않는 ID 조회에 빈 배열 `[]` 반환 → ✅ 명시적 `not_found` 에러. 빈 결과는 "데이터 없음"으로, 에러는 "인자가 틀림"으로 — 모델에게 전혀 다른 신호다.
- ❌ 검색 empty-result를 조용히 넘기고 모델이 알아서 답하게 둠 → ✅ "검색 결과 없음"을 명시 신호로 전달하고, 근거 없이 답하지 않도록 응답 계약을 프롬프트에 고정.
- ❌ faithfulness 점수 하나로 RAG 품질을 대표 → ✅ context relevance / groundedness / answer relevance 세 축을 분리 계측 — faithfulness는 컨텍스트가 옳다는 전제 위의 지표다.[[TruLens RAG Triad]](https://www.trulens.org/getting_started/core_concepts/rag_triad/)
- ❌ 스키마 검증 실패를 5xx로 던지고 세션 종료 → ✅ 구조화 에러로 모델에 반환해 교정 재시도 유도, 재시도 예산 소진 시 사용자에게 명시 보고.
- ❌ 검증 로직을 툴마다 복붙 → ✅ 게이트웨이/미들웨어 한 곳에서 스키마 검증을 일괄 수행하고, 존재/소유권 검증만 툴 내부에 둔다.

## 계측 (SLI)

이 실패군은 사용자 불만보다 툴 경계의 지표에서 먼저 드러난다. 다음을 대시보드의 1차 지표로 둔다.

- **Schema rejection rate** = 스키마 검증 거부 호출 수 / 전체 툴 호출 수. 툴별·필드별로 분해한다. 특정 툴의 특정 필드에 거부가 몰리면 그 필드의 이름·description·enum이 모델에게 모호하다는 신호다 — 검증을 완화할 게 아니라 스키마 계약을 다시 쓴다. 신규 모델 배포 직후 이 지표의 계단식 변화는 모델 회귀의 조기 경보다.
- **Not-found / ownership-denied rate** (계층 2 거부율) = 실존하지 않거나 소유권 없는 리소스 참조 비율. 전형 ①·④의 직접 지표. 스키마는 통과했으므로 schema rejection rate와 반드시 별도로 센다.
- **Policy denial rate** (계층 3, Cedar DENY율)와 그중 `context.input` 조건 기인 비율. LOG_ONLY 기간에 이 지표로 정책 강화의 영향 범위를 미리 측정한다([cedar-verified-permissions](../09-authorization/cedar-verified-permissions)).
- **Retrieval empty-result rate** = 검색 툴 호출 중 0건 반환 비율, 그리고 저유사도(임계값 미달) 반환 비율. 공백 전이의 선행 지표. 급등 시 인덱스 누락·임베딩 모델 불일치·쿼리 분포 변화를 의심한다.
- **Retry-after-rejection success rate** = 검증/정책 거부 후 N턴 내 같은 툴 호출이 성공한 비율. 계층 4가 작동하는지의 직접 지표다. 이 값이 낮으면 에러 메시지에 교정 정보가 부족하다는 뜻이고, 재시도 자체가 드물면 모델이 거부를 포기 신호로 읽고 있다는 뜻이다.
- **Per-task rejection budget 소진율** = 재시도 예산을 다 쓰고 사용자 보고로 종료된 태스크 비율. 최종 사용자 체감과 가장 가까운 합성 지표.

트레이스에는 각 턴의 retrieved context 식별자(최소한 chunk ID와 유사도 점수)와 툴 인자 원문, 각 계층의 거부 사유를 남긴다 — 위의 트리아지 1번 질문("컨텍스트를 열어봤는가")은 이 저장 없이는 답할 수 없다. 트레이스 기반 평가 체계 전반은 [llm-judge-trajectory](./llm-judge-trajectory)와 연결된다.

## 체크리스트

- [ ] 오답 사고 트리아지 절차에 "retrieved context 원문 확인"이 1단계로 문서화되어 있다
- [ ] 모든 툴 스키마에서 닫힌 집합 파라미터는 `enum`, 형식 고정 파라미터는 `pattern`/`format`, 수치는 `minimum`/`maximum`이 선언되어 있다
- [ ] 모든 툴 스키마에 `additionalProperties: false`가 설정되어 있다 (지어낸 파라미터 차단)
- [ ] 인증 주체 자신의 식별자는 LLM 파라미터가 아니라 서버 측에서 주입된다
- [ ] 부수효과가 있는 툴은 실행 전에 참조 리소스의 존재와 소유권을 검증한다
- [ ] 금액·수량 등 비즈니스 한도는 Cedar `context.input` 조건으로 정책 계층에도 선언되어 있다
- [ ] 검증/정책 거부 에러는 필드·이유·교정 방법·`retryable` 여부를 담은 구조화 형식이다
- [ ] 에이전트 루프에 인자 검증 실패 재시도 예산이 하드코딩되어 있고, 소진 시 사용자에게 명시 보고한다
- [ ] 외부 사용자 경로의 검증 에러는 열거 공격에 쓰일 수 있는 힌트(존재 여부 구별, enum 전체 목록)를 노출하지 않는다
- [ ] schema rejection rate, not-found rate, empty-result rate, retry success rate가 툴별로 대시보드에 있다
- [ ] 신규 모델/프롬프트 배포 시 위 지표의 배포 전후 비교가 릴리스 절차에 포함되어 있다
- [ ] 검색 품질 지표(recall@k 등)는 Part 6의 골든셋 체계로 별도 측정하고 있다 (이 장의 지표는 원인 분류용)

## 참고

- Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" — https://arxiv.org/abs/2005.11401
- OWASP Top 10 for LLM Applications 2025, LLM05: Improper Output Handling — https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/
- OWASP LLM06: Excessive Agency — https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
- OWASP LLM09: Misinformation — https://genai.owasp.org/llmrisk/llm092025-misinformation/
- TruLens, "The RAG Triad" — https://www.trulens.org/getting_started/core_concepts/rag_triad/
- Ragas, Faithfulness metric — https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
- Anthropic, "Writing tools for agents" — https://www.anthropic.com/engineering/writing-tools-for-agents
- JSON Schema Reference (enum, pattern, numeric bounds) — https://json-schema.org/understanding-json-schema/reference
- OpenAI, "Introducing Structured Outputs in the API" — https://openai.com/index/introducing-structured-outputs-in-the-api/
- AWS, Policy in Amazon Bedrock AgentCore — Core concepts — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html
- AWS, Understanding Cedar policies — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-understanding-cedar.html
- AWS Security Blog, "Why Policy in Amazon Bedrock AgentCore chose Cedar" — https://aws.amazon.com/blogs/security/why-policy-in-amazon-bedrock-agentcore-chose-cedar-for-securing-agentic-workflows/
- 관련 장: [tool-design](../01-agent-design/tool-design) · [tool-overload](./tool-overload) · [llm-judge-trajectory](./llm-judge-trajectory) · [retrieval-evaluation](../06-vector-search/retrieval-evaluation) · [cedar-verified-permissions](../09-authorization/cedar-verified-permissions) · [per-tool-obo](../09-authorization/per-tool-obo) · [confused-deputy](../09-authorization/confused-deputy)
