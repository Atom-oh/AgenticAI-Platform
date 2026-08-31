---
title: GraphRAG·agentic RAG, 그리고 쓰지 말아야 할 때
description: agentic RAG와 GraphRAG가 vanilla RAG를 언제 넘어서는지, 그리고 CAG·SQL처럼 애초에 RAG를 쓰지 않는 편이 나은 상황을 결정 표로 정리한다.
outline: [2, 3]
---

# GraphRAG·agentic RAG, 그리고 쓰지 말아야 할 때

::: tip 이 장에서 얻는 것
- "RAG를 어떻게 잘 만들까" 이전에 던져야 할 질문 — **RAG 자체가 이 워크로드에 맞는 도구인가** — 에 답하는 판단 기준
- 코퍼스가 작을 때 검색 인프라 전체를 대체하는 **CAG(Cache-Augmented Generation)** — long-context + 프롬프트 캐싱 조합의 근거와 한계
- 정형 데이터에 벡터 검색을 들이대면 안 되는 이유와 NL2SQL/API 경로
- 단일 샷 RAG를 반복 검색 루프로 확장하는 **agentic RAG**의 정확도–지연–비용 트레이드오프
- 다홉(multi-hop) 관계 질의와 전역(global) 질의에서 **GraphRAG**가 성립하는 조건, 그리고 구축·유지 비용이라는 대가
- 코퍼스 크기 × 질의 유형 × 신선도 요구로 vanilla RAG / agentic RAG / GraphRAG / CAG / SQL 중 하나를 고르는 결정 표
:::

## 왜 문제가 되는가

Part 6은 지금까지 임베딩, 청킹, 하이브리드 검색, 리랭킹, 인덱스 신선도까지 — "RAG 파이프라인을 잘 만드는 법"을 쌓아 왔다. 이 장은 그 축적 위에서 방향을 뒤집는다. 검색 파이프라인의 각 단계를 아무리 최적화해도, **워크로드 자체가 RAG에 맞지 않으면** 그 최적화는 잘못된 문제를 잘 푸는 일이 된다.

플랫폼 관점에서 이 판단이 중요한 이유는 RAG가 공짜가 아니기 때문이다. 벡터 스토어 운영, 임베딩 파이프라인, 인덱스 신선도 관리([인덱스 신선도와 마이그레이션](./index-freshness-migration.md)), 검색 품질 평가([검색 평가](./retrieval-evaluation.md))는 전부 지속 비용이다. 반대편 극단인 GraphRAG는 여기에 지식 그래프 구축·유지 비용을 얹는다 — Microsoft의 공식 저장소조차 "GraphRAG indexing can be an expensive operation, please read all of the documentation to understand the process and costs involved, and start small"이라고 경고문을 박아 두었다.[^graphrag-repo]

그리고 반대 방향의 압력도 커졌다. 모델의 컨텍스트 윈도우가 커지고 프롬프트 캐싱이 성숙하면서, "코퍼스가 충분히 작으면 검색하지 말고 전부 넣어라"가 학술적으로도(CAG[^cag]) 벤더 공식 가이드로도(Anthropic[^ctx-retrieval]) 명시된 선택지가 됐다. 즉 오늘의 설계 공간은 vanilla RAG 하나가 아니라 **CAG ← vanilla RAG → agentic RAG → GraphRAG**라는 스펙트럼이고, 정형 데이터라는 별도 축(SQL/API)까지 있다. 이 장의 결정 표가 그 선택을 다룬다.

## 핵심 개념

### 1. RAG를 쓰지 말아야 할 때 ①: 작은 코퍼스는 통째로 넣는다 (CAG)

Chan 등의 WWW '25 논문 "Don't Do RAG: When Cache-Augmented Generation is All You Need for Knowledge Tasks"는 제목 그대로의 주장을 한다: 코퍼스가 컨텍스트 윈도우에 들어가는 크기라면, 문서 전체를 미리 컨텍스트에 적재하고 KV cache(런타임 파라미터)를 캐싱해 두는 **CAG**가 실시간 검색을 완전히 우회하면서 검색 지연과 검색 오류(잘못된 문서 선택)를 동시에 제거한다.[^cag] Llama 3.1 8B 기준 실험에서 CAG는 HotPotQA small(문서 16개, 약 21K 토큰)에서 BERTScore 0.7951로 sparse RAG(BM25) 최고치 0.7676, dense RAG 최고치 0.7576을 모두 앞섰고, SQuAD의 세 구성 전부에서도 RAG 계열을 상회했다.[^cag]

정직하게 봐야 할 반례도 같은 논문 안에 있다: HotPotQA large(문서 64개, 약 85K 토큰)에서는 sparse RAG(0.7535)가 CAG(0.7407)를 근소하게 앞선다.[^cag] 즉 CAG의 우위는 코퍼스가 윈도우 대비 충분히 작을 때 뚜렷하고, 윈도우 한계에 다가갈수록 [context rot](../05-context/context-rot.md)로 잠식된다. 저자들 스스로 CAG가 "소규모 기업의 내부 knowledge base, FAQ, 콜센터" 같은 용례에 적합하며 "significantly larger datasets"에는 비실용적이라고 한계를 명시한다.[^cag]

이 학술 결과는 벤더 공식 가이드와 정확히 합치한다. Anthropic의 Contextual Retrieval 문서는 첫 절에서 "knowledge base가 200,000 토큰(약 500페이지)보다 작으면 RAG나 유사 기법 없이 전체를 프롬프트에 넣으면 된다"고 명시하고, 프롬프트 캐싱과 결합하면 지연 2배 이상·비용 최대 90% 절감으로 이 접근이 실용적이 된다고 안내한다.[^ctx-retrieval] 프롬프트 캐싱의 동작 원리와 캐시 브레이크포인트 설계는 [프롬프트 캐싱 기초](../04-caching/prompt-caching-basics.md)에서, "프리로딩 vs JIT 검색"이라는 컨텍스트 관점의 같은 결정은 [JIT 검색과 토큰 예산](../05-context/jit-retrieval-token-budget.md)의 결정 표에서 이미 다뤘다 — 그 표의 첫 행("코퍼스가 작고 안정적이며 반복 재사용됨 → 프리로딩 + 프롬프트 캐싱")이 곧 CAG다. 이 장은 그 판단을 검색 인프라 관점에서 다시 말한다: **그 행에 해당하면 벡터 스토어를 만들지 마라.**

CAG의 전제 조건 세 가지를 명확히 하자.

- **크기**: 코퍼스 + 시스템 프롬프트 + 대화 이력이 윈도우에 들어가고, context rot 여유가 남아야 한다. Anthropic의 200K 토큰 기준[^ctx-retrieval]이 실무적 출발점이다.
- **안정성**: 코퍼스가 자주 바뀌면 캐시가 계속 무효화되어 경제성이 무너진다([캐시 미스 근본 원인](../04-caching/cache-miss-root-causes.md) 참고). 신선도 요구가 분 단위라면 CAG는 탈락이다.
- **재사용률**: 같은 코퍼스에 여러 질의가 반복돼야 캐시 적재 비용(cache write는 기본 입력보다 비싸다)이 회수된다([캐시 비용·경제성](../04-caching/cache-metrics-economics.md) 참고).

### 2. RAG를 쓰지 말아야 할 때 ②: 정형 데이터는 SQL/API로

"지난 분기 지역별 매출 합계는?" 같은 집계 질의를 벡터 검색으로 풀려는 시도는 구조적으로 실패한다. 벡터 검색은 top-k 유사 청크를 반환하는 장치이지 전수 스캔·집계 장치가 아니다 — k개 청크에 잡히지 않은 행은 조용히 누락되고, 모델은 불완전한 부분집합 위에서 그럴듯한 합계를 지어낸다. COUNT/SUM/GROUP BY/정렬/조인이 필요한 질의는 그 연산을 위해 만들어진 엔진(SQL)이나 권위 있는 API로 보내야 한다.

이 경로는 이미 매니지드로 존재한다. Amazon Bedrock Knowledge Bases는 정형 데이터 스토어(Redshift, Glue Data Catalog 등) 연결 시 자연어를 SQL로 변환(NL2SQL)해 원본에 직접 질의하는 모듈을 제공하며, 스키마와 질의 이력을 근거로 SQL을 생성해 실행한다.[^bedrock-structured] 에이전트 설계 관점에서는 "벡터 검색 도구"와 "SQL/API 도구"를 별도 도구로 노출하고 라우팅을 모델에 맡기는 구성이 자연스럽다 — 도구 설계 원칙은 [Part 1](../01-agent-design/index.md)의 규율을 따른다. 벡터 스토어 자체를 AWS에서 고르는 문제는 [AWS 벡터 스토어](./aws-vector-stores.md) 참고.

경계 사례: "계약서에서 위약금 조항을 찾아 금액을 비교"처럼 비정형 문서에서 사실을 추출한 뒤 집계하는 워크로드는, 검색(비정형) → 추출 → 정형화 → SQL(집계)의 파이프라인이지 벡터 검색 단독의 일이 아니다.

### 3. agentic RAG: 검색을 루프로 만든다

vanilla RAG는 "질의 1회 → 검색 1회 → 생성 1회"의 단일 샷이다. **agentic RAG**는 이 파이프라인에 에이전트를 심어 — 질의 재작성(query rewriting), 다단계·다소스 검색, 검색 결과 자체 평가 후 재시도, 도구 선택 — 검색을 반복 루프로 바꾼다. Singh 등의 survey는 이를 reflection·planning·tool use·multi-agent collaboration이라는 agentic design pattern으로 분류하고, 동시에 평가·조정·효율성이 미해결 과제임을 지적한다.[^agentic-survey]

효과의 방향은 명확하다: 단일 샷 검색이 놓치는 질의(모호한 표현, 여러 하위 질문으로 분해해야 하는 질문)에서 회수율이 올라간다. 대가도 명확하다: **루프 1회가 곧 모델 호출 + 도구 왕복 1세트**이므로 지연과 토큰 비용이 반복 횟수에 비례해 쌓인다. 지연의 구조는 [Tool round-trip](../02-performance/tool-roundtrips.md)과 [지연의 해부](../02-performance/latency-anatomy.md)에서 다룬 그대로이고, 비용 상한의 감각은 Anthropic의 실측 — 멀티에이전트 시스템은 채팅 대비 약 15배의 토큰을 소비한다 — 이 준다.[^multi-agent] 따라서 agentic RAG는 "vanilla RAG의 상위 호환"이 아니라, **검색 실패율이 지연·비용 증가를 정당화할 만큼 높은 질의 구간에만** 투입하는 도구다. 실무 구성은 라우팅이다: 단순 조회는 단일 샷으로 처리하고, 자체 평가에서 근거 불충분으로 판정된 질의만 루프로 승격시킨다([모델 라우팅](../02-performance/model-routing.md)과 같은 원리).

또 하나 주의할 점: agentic RAG를 도입하기 전에 [하이브리드 검색과 리랭킹](./hybrid-search-rerank.md), [청킹 전략](./chunking-strategies.md) 같은 단일 샷 품질 개선이 소진됐는지 먼저 확인하라. Anthropic 실측 기준 contextual retrieval + reranking만으로 top-20 검색 실패율을 최대 67% 줄일 수 있다[^ctx-retrieval] — 루프 없이 얻는 개선을 루프로 사면 지연만 낭비한다.

### 4. GraphRAG: 다홉 관계와 전역 질의를 위한 구조

vanilla RAG의 두 번째 구조적 한계는 질의 유형에 있다. "A사의 자회사가 인수한 회사의 경쟁사는?" 같은 다홉 관계 질의는 필요한 근거가 서로 다른 문서에 흩어져 있고 유사도 검색만으로는 그 연결 경로를 따라가지 못한다. "이 코퍼스 전체의 주요 테마는?" 같은 전역(global) 질의는 애초에 특정 청크의 검색 문제가 아니라 query-focused summarization 문제다.

Microsoft의 GraphRAG는 이 두 유형을 겨냥한다. LLM으로 소스 문서에서 entity knowledge graph를 추출하고, 밀접하게 연결된 entity 군집(community)별 요약을 사전 생성해 두었다가, 질의 시 community 요약들로 부분 응답을 만들고 다시 종합한다. 원 논문은 100만 토큰 규모 데이터셋의 전역 sensemaking 질의에서 vanilla RAG 대비 응답의 포괄성(comprehensiveness)과 다양성(diversity)이 크게 개선됨을 보고한다.[^graphrag-paper]

대가는 인덱싱 비용이다. 그래프 추출과 community 요약이 전부 LLM 호출이므로 코퍼스 전체에 대한 사전 처리 비용이 크고(공식 저장소의 비용 경고[^graphrag-repo]), 도메인에 맞는 프롬프트 튜닝 없이는 "out of the box" 결과가 좋지 않다고 저장소가 직접 명시한다.[^graphrag-repo] 코퍼스가 갱신되면 그래프·요약도 갱신해야 하므로 신선도 요구가 높은 코퍼스와는 상성이 나쁘다 — [인덱스 신선도와 마이그레이션](./index-freshness-migration.md)에서 다룬 재인덱싱 문제가 훨씬 무거운 형태로 재현된다. Microsoft 스스로 이 비용을 문제로 인식해, 사전 요약 없이 질의 시점에 그래프를 지연 활용하는 LazyGraphRAG를 내놓으며 "데이터 인덱싱 비용이 vector RAG와 동일하고 full GraphRAG의 0.1%"라고 밝혔다[^lazy] — 뒤집어 말하면 full GraphRAG의 인덱싱 비용이 vector RAG의 약 1,000배 수준이었다는 자기 고백이다.

::: warning 미정착 영역
GraphRAG의 실효성은 도메인·질의 유형 의존적이며, 벤치마크 결과가 엇갈린다. Han 등의 체계적 비교(arXiv 2502.11371)는 RAG와 GraphRAG 중 일관된 승자가 없고 상호 보완적이라고 결론짓는다 — 단일 홉·세부 사실 질의에서는 vanilla RAG가, 다홉·추론 집약 질의에서는 GraphRAG가 우세했다.[^rag-vs-graphrag] 원 논문의 우위 자체도 comprehensiveness·diversity라는 LLM 심사 기반 지표에서의 결과이지 사실 정확도 벤치마크의 전면 우위가 아니다.[^graphrag-paper] "GraphRAG가 RAG보다 낫다"는 일반 명제는 성립하지 않으며, 자신의 질의 분포에서 다홉·전역 질의 비중을 측정한 뒤에만 도입을 정당화할 수 있다. LazyGraphRAG 같은 저비용 변형이 full GraphRAG를 어디까지 대체하는지도 아직 수렴되지 않았다.[^lazy]
:::

### 5. 스펙트럼으로 보기

다섯 선택지는 두 축으로 정렬된다. **코퍼스 크기** 축에서 CAG(윈도우 내) → vanilla/agentic RAG(윈도우 초과) 순으로, **질의 복잡도** 축에서 vanilla RAG(단순 조회) → agentic RAG(재작성·다단계가 필요한 질의) → GraphRAG(다홉 관계·전역 요약) 순으로 복잡도와 비용이 함께 오른다. 정형 데이터의 집계·조인은 이 스펙트럼 바깥의 별도 축(SQL/API)이다. 원칙은 하나다: **질의 분포가 요구하는 최소 복잡도의 도구를 골라라.** 스펙트럼의 오른쪽으로 갈수록 정확도 상한과 함께 지연·비용·운영 부담의 하한도 올라간다.

## 결정 표

| 상황 (코퍼스 × 질의 × 신선도) | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 코퍼스 < 200K 토큰, 안정적, 질의가 반복됨 | **CAG** (long-context 프리로딩 + 프롬프트 캐싱) | 검색 지연·검색 오류 자체가 없음, 캐시 적중 시 비용 최대 90% 절감[^ctx-retrieval][^cag] | 코퍼스 갱신 시 캐시 무효화, 윈도우 한계 근접 시 context rot·성능 역전(HotPotQA large 사례[^cag]) |
| 코퍼스가 윈도우 초과, 질의 대부분이 단순 사실 조회 | **vanilla RAG** (+ 하이브리드 검색·리랭킹) | 단일 홉·세부 사실 질의에서는 GraphRAG보다도 우세[^rag-vs-graphrag], 지연·비용 최소 | 다홉·전역 질의에서 회수 실패, 검색 품질이 청킹·임베딩 품질에 종속 |
| 검색 실패율이 높은 질의 구간 존재(모호한 질의, 하위 질문 분해 필요, 다소스) | **agentic RAG** (질의 재작성 + 반복 검색 + 자체 평가 루프) | 단일 샷이 놓치는 질의의 회수율 개선[^agentic-survey] | 루프 횟수에 비례한 지연·토큰 비용(멀티에이전트는 채팅 대비 ~15배 토큰[^multi-agent]), 루프 폭주 시 비용 상한 필요 |
| 다홉 관계 질의("A의 자회사의 경쟁사")·전역 요약 질의가 핵심 워크로드 | **GraphRAG** | entity graph + community 요약으로 관계 경로·전역 구조를 명시적으로 보유[^graphrag-paper] | LLM 기반 인덱싱 비용이 큼(vector RAG 대비 ~1,000배 수준이었다는 것이 LazyGraphRAG 발표의 함의[^lazy]), 프롬프트 튜닝·그래프 갱신 운영 부담[^graphrag-repo] |
| 다홉·전역 질의는 있으나 저빈도이거나 코퍼스가 스트리밍성 | **LazyGraphRAG류** (질의 시점 그래프 활용) 또는 agentic RAG로 대체 검토 | 인덱싱 비용이 vector RAG와 동일[^lazy] | 접근법 자체가 미성숙(위 미정착 영역 참고), 질의 시점 비용으로 이전됨 |
| 집계·조인·정렬이 필요한 정형 데이터 질의 | **SQL/API** (NL2SQL 포함) | 전수 연산은 그 목적의 엔진이 수행해야 정확[^bedrock-structured] | NL2SQL 자체의 오류율 관리 필요, 스키마 컨텍스트 제공 필요 |
| 신선도 요구가 분·시간 단위인 코퍼스 | vanilla RAG(증분 인덱싱) 또는 JIT 도구 조회, CAG·GraphRAG 배제 | CAG는 캐시 무효화, GraphRAG는 재인덱싱 비용이 신선도 요구와 충돌 | 증분 인덱싱 파이프라인 운영 부담([인덱스 신선도](./index-freshness-migration.md)) |
| 혼합 워크로드(위 유형들이 섞여 들어옴) | **질의 라우팅** (단순→vanilla, 실패→agentic 승격, 정형→SQL) | 질의별 최소 비용 경로, [모델 라우팅](../02-performance/model-routing.md)과 동일 원리 | 라우터 자체의 오분류율·지연이 새 계측 대상이 됨 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 수십 페이지짜리 사내 문서에 RAG를 구축했는데 답변 품질이 문서 통째 첨부보다 나쁨 | 코퍼스가 윈도우에 들어가는 크기인데도 관성적으로 RAG 도입 — 검색이 오류 지점만 추가 | 코퍼스 총 토큰 수 측정, 전체 프리로딩 구성과 A/B 비교 | 200K 토큰 미만이면 CAG로 전환[^ctx-retrieval], 벡터 스토어 제거 |
| 집계성 질문("전체 몇 건?", "합계는?")에 그럴듯하지만 틀린 숫자 반환 | top-k 검색이 전수 데이터를 보장하지 않는데 벡터 검색으로 집계 시도 | 동일 질의를 SQL로 실행해 결과 대조 | 정형 질의를 NL2SQL/API 경로로 라우팅[^bedrock-structured] |
| agentic RAG 도입 후 P95 지연이 SLO 초과, 토큰 비용 급증 | 모든 질의를 루프로 처리 — 단순 조회까지 재작성·재검색 반복 | 트레이스에서 질의당 검색 반복 횟수 분포 확인 | 단일 샷 기본 + 자체 평가 실패 시에만 루프 승격, 반복 횟수 상한 설정 |
| agentic RAG 루프가 같은 질의를 표현만 바꿔 반복 검색하며 종료되지 않음 | 자체 평가 기준이 모호해 "근거 불충분" 판정이 반복됨 | 루프 종료 사유 로깅(성공/상한 도달/포기 비율) | 평가 기준을 명시적 rubric으로, 상한 도달 시 확보한 근거로 응답하도록 강제 |
| GraphRAG 인덱싱 비용이 예산을 초과, 코퍼스 갱신마다 재발 | 다홉 질의 비중을 측정하지 않고 전체 코퍼스에 full GraphRAG 적용 | 질의 로그에서 다홉·전역 질의 비율 산출, 인덱싱 1회당 LLM 호출 비용 집계 | 다홉 질의가 저빈도면 vanilla RAG + agentic 승격으로 대체, 또는 LazyGraphRAG류 검토[^lazy] |
| GraphRAG가 자사 도메인에서 벤치마크만큼 성능이 안 나옴 | 기본 프롬프트로 entity 추출 — 도메인 엔티티·관계가 그래프에 잡히지 않음 | 추출된 그래프에서 핵심 도메인 엔티티의 존재 여부 표본 검사 | 공식 권고대로 도메인 프롬프트 튜닝 수행[^graphrag-repo], 그래도 안 되면 도입 재검토(미정착 영역) |
| CAG 구성에서 캐시 비용이 오히려 RAG보다 비쌈 | 코퍼스가 자주 갱신되거나 질의 재사용률이 낮아 cache write만 반복 | 캐시 적중률·write/read 비율 계측([캐시 비용·경제성](../04-caching/cache-metrics-economics.md)) | 갱신 주기가 빠른 부분만 분리해 JIT 조회, 안정 부분만 캐싱 |
| 코퍼스가 성장해 CAG 품질이 서서히 저하 | 윈도우 점유율 상승 → context rot, HotPotQA large에서 RAG가 역전한 것과 같은 구간 진입[^cag] | 코퍼스 토큰 수 추세 + 응답 품질 평가를 함께 트래킹 | 임계(예: 윈도우의 절반) 초과 시 RAG로 이행하는 마이그레이션 계획을 사전에 수립 |

## 안티패턴

- ❌ "일단 RAG부터 구축"이 기본값 → ✅ 코퍼스 토큰 수부터 센다. 200K 토큰 미만이면 CAG가 기본값이고 RAG가 예외다.[^ctx-retrieval]
- ❌ 벡터 검색 하나로 비정형 검색과 정형 집계를 모두 처리 → ✅ 집계·조인은 SQL/API 도구로 분리하고 라우팅한다.[^bedrock-structured]
- ❌ 검색 품질이 낮다고 곧장 agentic 루프 추가 → ✅ 하이브리드 검색·리랭킹·contextual retrieval 등 단일 샷 개선(실패율 최대 67% 감소[^ctx-retrieval])을 먼저 소진한다.
- ❌ 모든 질의를 agentic RAG 루프로 처리 → ✅ 단순 조회는 단일 샷, 자체 평가 실패 질의만 루프로 승격 + 반복 상한.
- ❌ "다홉 질의도 있을 수 있으니" 전체 코퍼스에 GraphRAG 선제 적용 → ✅ 질의 로그에서 다홉·전역 질의 비중을 측정한 뒤, 인덱싱 비용[^graphrag-repo] 대비 효과를 계산하고 시작은 작게 한다.
- ❌ GraphRAG를 기본 프롬프트로 돌리고 결과로 접근법을 평가 → ✅ 도메인 프롬프트 튜닝은 전제 조건이다[^graphrag-repo] — 튜닝 전 결과로 도입/폐기를 결정하지 않는다.
- ❌ CAG 도입 후 코퍼스 성장을 방치 → ✅ 코퍼스 크기 임계와 RAG 이행 계획을 도입 시점에 함께 정의한다.

## 계측 (SLI)

- **질의 유형 분포**: 단순 조회 / 다단계·모호 / 다홉·전역 / 정형 집계의 비율. 이 분포가 결정 표의 입력값이며, 분기마다 재측정해 아키텍처 선택을 재검증한다.
- **검색 실패율(단일 샷)**: 응답 근거 불충분으로 판정된 질의 비율. agentic 승격의 트리거이자, 승격 정책의 효과 측정 기준. 측정 방법론은 [검색 평가](./retrieval-evaluation.md) 참고.
- **agentic 루프 반복 횟수 분포(P50/P95)와 루프당 추가 지연·토큰**: 루프 비용이 상한 내에 있는지, 승격이 남발되지 않는지 감시.
- **캐시 적중률과 코퍼스 토큰 수 추세(CAG)**: 적중률 하락은 코퍼스 불안정의 신호, 토큰 수 증가는 RAG 이행 시점의 신호.
- **정형 질의 오라우팅률**: 집계성 질의가 벡터 검색으로 흘러간 비율(도구 호출 로그에서 질의 분류와 실제 경로를 대조).
- **인덱싱 비용/신선도 지연(GraphRAG)**: 코퍼스 갱신 1회당 재인덱싱 LLM 비용과, 갱신이 그래프에 반영되기까지의 지연.

## 체크리스트

- [ ] 코퍼스 총 토큰 수를 측정했는가? 200K 토큰 미만이라면 CAG(프리로딩 + 캐싱)를 먼저 검토했는가?[^ctx-retrieval]
- [ ] 코퍼스 갱신 주기와 질의 재사용률을 근거로 CAG의 캐시 경제성을 계산했는가?
- [ ] 질의 로그를 유형별(단순/다단계/다홉·전역/정형 집계)로 분류해 분포를 확보했는가?
- [ ] 집계·조인·정렬 질의를 SQL/API 경로로 분리했는가?
- [ ] agentic RAG 도입 전에 단일 샷 개선(하이브리드 검색, 리랭킹, contextual retrieval)을 소진했는가?
- [ ] agentic 루프에 반복 상한과 종료 rubric, 비용 계측이 있는가?
- [ ] GraphRAG 도입 근거로 다홉·전역 질의 비중과 인덱싱 비용 추정치를 문서화했는가? 소규모 파일럿으로 시작했는가?[^graphrag-repo]
- [ ] GraphRAG 파일럿에서 도메인 프롬프트 튜닝을 수행하고, vanilla RAG 대비 자사 질의셋 기준 A/B 평가를 했는가?[^rag-vs-graphrag]
- [ ] 코퍼스 성장·질의 분포 변화 시 아키텍처를 재평가하는 주기를 정했는가?

## 참고

[^cag]: Brian J Chan, Chao-Ting Chen, Jui-Hung Cheng, Hen-Hsen Huang, ["Don't Do RAG: When Cache-Augmented Generation is All You Need for Knowledge Tasks"](https://arxiv.org/abs/2412.15605), arXiv:2412.15605, The Web Conference 2025 (WWW '25) short paper 채택 — CAG 정의, Llama 3.1 8B 기준 SQuAD/HotPotQA BERTScore(HotPotQA small: CAG 0.7951 vs sparse RAG 0.7676 vs dense RAG 0.7576; large: sparse RAG 0.7535 > CAG 0.7407), "소규모 내부 knowledge base·FAQ·콜센터에 적합, 훨씬 큰 데이터셋에는 비실용적"이라는 한계 명시의 출처.
[^ctx-retrieval]: Anthropic, ["Introducing Contextual Retrieval"](https://www.anthropic.com/engineering/contextual-retrieval), Anthropic Engineering Blog — "200K 토큰(약 500페이지) 미만 knowledge base는 RAG 없이 프롬프트에 전부 포함" 가이드, 프롬프트 캐싱 결합 시 지연 2배 이상·비용 최대 90% 절감, contextual retrieval + reranking으로 top-20 검색 실패율 최대 67% 감소 수치의 출처.
[^graphrag-paper]: Darren Edge et al., ["From Local to Global: A Graph RAG Approach to Query-Focused Summarization"](https://arxiv.org/abs/2404.16130), arXiv:2404.16130, Microsoft Research — entity knowledge graph + community 요약 구조, 1M 토큰 규모 데이터셋의 전역 sensemaking 질의에서 vanilla RAG 대비 comprehensiveness·diversity 개선 결과의 출처.
[^graphrag-repo]: Microsoft, [microsoft/graphrag](https://github.com/microsoft/graphrag), GitHub — "GraphRAG indexing can be an expensive operation" 경고문과 도메인 프롬프트 튜닝 권고(out-of-the-box 결과가 최선이 아님)의 출처.
[^lazy]: Microsoft Research, ["LazyGraphRAG: Setting a new standard for quality and cost"](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/), Microsoft Research Blog (2024년 11월) — "LazyGraphRAG의 데이터 인덱싱 비용은 vector RAG와 동일하며 full GraphRAG 인덱싱 비용의 0.1%" 수치의 출처.
[^rag-vs-graphrag]: Haoyu Han et al., ["RAG vs. GraphRAG: A Systematic Evaluation and Key Insights"](https://arxiv.org/abs/2502.11371), arXiv:2502.11371 — 통제된 비교에서 일관된 승자 없음, 단일 홉·세부 사실 질의는 RAG 우세·다홉·추론 집약 질의는 GraphRAG 우세라는 상호 보완 결론의 출처.
[^agentic-survey]: Aditi Singh et al., ["Agentic Retrieval-Augmented Generation: A Survey on Agentic RAG"](https://arxiv.org/abs/2501.09136), arXiv:2501.09136 — agentic RAG의 정의(reflection·planning·tool use·multi-agent collaboration 패턴), 아키텍처 분류, 평가·효율성 미해결 과제의 출처.
[^multi-agent]: Anthropic, ["How we built our multi-agent research system"](https://www.anthropic.com/engineering/built-multi-agent-research-system), Anthropic Engineering Blog — "멀티에이전트 시스템은 채팅 대비 약 15배 더 많은 토큰을 쓴다"는 수치의 출처.
[^bedrock-structured]: AWS, ["Amazon Bedrock Knowledge Bases now supports structured data retrieval"](https://aws.amazon.com/about-aws/whats-new/2024/12/amazon-bedrock-knowledge-bases-structured-data-retrieval) 및 [구조화된 데이터 스토어 연결 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-structured-create.html) — 매니지드 NL2SQL 모듈(Redshift 등)로 정형 데이터에 직접 질의하는 경로의 출처.

- [JIT 검색과 토큰 예산](../05-context/jit-retrieval-token-budget.md) — 같은 결정("프리로딩 vs 검색")의 컨텍스트 관점 결정 표. 그 표의 "프리로딩 + 프롬프트 캐싱" 행이 이 장의 CAG다.
- [프롬프트 캐싱 기초](../04-caching/prompt-caching-basics.md), [캐시 비용·경제성](../04-caching/cache-metrics-economics.md) — CAG의 경제성을 결정하는 캐싱 메커니즘과 비용 모델
- [Context rot](../05-context/context-rot.md) — CAG가 윈도우 한계에 다가갈 때 감당하는 근본 위험
- [하이브리드 검색과 리랭킹](./hybrid-search-rerank.md), [청킹 전략](./chunking-strategies.md) — agentic 루프 이전에 소진해야 할 단일 샷 개선
- [검색 평가](./retrieval-evaluation.md) — 검색 실패율(agentic 승격 트리거) 측정 방법론
- [인덱스 신선도와 마이그레이션](./index-freshness-migration.md) — GraphRAG에서 훨씬 무거워지는 재인덱싱 문제의 원형
- [Tool round-trip](../02-performance/tool-roundtrips.md), [모델 라우팅](../02-performance/model-routing.md) — agentic RAG 루프의 지연 구조와 질의 라우팅 원리
