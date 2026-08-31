---
title: 청킹 전략
description: 청킹이 retrieval 품질을 결정하는 이유와 fixed/recursive/semantic/구조 기반 전략, contextual retrieval과 late chunking까지의 결정 기준을 다룬다.
outline: [2, 3]
---

# 청킹 전략

::: tip 이 장에서 얻는 것
- 청킹이 embedding 모델·인덱스보다 먼저 retrieval 품질의 상한을 결정하는 이유
- 기본 전략 5종(fixed-size, 문장/문단 경계, recursive, semantic, 문서 구조 기반)의 결정 표
- Anthropic Contextual Retrieval의 실제 수치 — top-20 retrieval 실패율 5.7% → 3.7%(−35%), BM25 결합 시 → 2.9%(−49%), reranking까지 → 1.9%(−67%) — 와 그 수치를 그대로 믿으면 안 되는 이유
- Late chunking(전체 문서를 먼저 embedding 모델에 통과시킨 뒤 분할)의 동작 원리와 적용 조건
- Bedrock Knowledge Bases의 청킹 옵션(default/fixed/hierarchical/semantic/no chunking)과의 매핑
- 문맥 생성 비용을 prompt caching으로 흡수하는 방법 ([프롬프트 캐싱 기초](/04-caching/prompt-caching-basics) 교차 참조)
:::

## 왜 문제가 되는가

RAG 파이프라인에서 청킹은 가장 앞단의 비가역 결정이다. embedding 모델은 나중에 바꿔 낄 수 있고, 인덱스는 재빌드하면 되지만, 청크 경계가 잘못 그어지면 그 뒤의 어떤 컴포넌트도 잃어버린 정보를 복원하지 못한다. "회사의 매출은 전 분기 대비 3% 성장했다"라는 청크는 어느 회사, 어느 분기인지에 대한 정보를 청크 밖에 두고 왔고, 이 청크의 embedding은 그 문맥 없이 계산된다. 쿼리가 "ACME Corp 2023 Q2 매출 성장률"이라면 이 청크는 의미적으로 가장 정답에 가까우면서도 검색에서 밀려난다.

이것이 청킹의 근본 딜레마다. Anthropic은 이 문제를 "청크가 충분한 문맥을 갖지 못하는(insufficient context)" 문제로 정식화하고, 전통적 RAG가 "문맥을 파괴하는(destroys context)" 방식으로 문서를 분할한다고 표현했다([Anthropic, Contextual Retrieval, 2024-09](https://www.anthropic.com/news/contextual-retrieval)).

플랫폼 관점에서 청킹이 문제가 되는 두 번째 이유는 **조용한 실패**다. 청킹이 나빠도 파이프라인은 에러 없이 돌고, retrieval은 "무언가"를 반환하며, LLM은 그 무언가로 그럴듯한 답을 생성한다. 증상은 "가끔 답이 이상하다"는 정성적 불만으로만 나타나고, 근본 원인이 6개월 전에 정한 `chunk_size=512`라는 사실은 [retrieval 평가 체계](/06-vector-search/retrieval-evaluation) 없이는 드러나지 않는다.

세 번째 이유는 비용 구조다. 청크 크기는 인덱스 크기(= 벡터 저장 비용), 검색 시 top-k로 끌어오는 컨텍스트 토큰 수(= 추론 비용), 그리고 재인덱싱 빈도(= 인제스천 비용)를 동시에 결정한다. 청킹 전략 변경은 전체 코퍼스 재임베딩을 의미하므로, [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration)에서 다루는 재빌드 비용이 그대로 청킹 실험의 반복 비용이 된다.

한 가지 예외 조건부터 확인하자. Anthropic 원문은 지식 베이스가 **200,000 토큰(약 500페이지) 미만이면 RAG 없이 전체를 프롬프트에 넣는 것**이 낫다고 명시한다([출처](https://www.anthropic.com/news/contextual-retrieval)). 이 경우 청킹 자체가 불필요하며, 반복 질의 비용은 [prompt caching](/04-caching/prompt-caching-basics)으로 흡수한다. 청킹 전략을 고민하기 전에 코퍼스가 이 임계값을 넘는지부터 확인하라.

## 핵심 개념

### 청크는 세 가지 역할을 동시에 수행한다

청크 하나가 (1) **embedding 단위**(벡터 하나로 압축되는 텍스트 범위), (2) **검색 단위**(top-k로 랭킹되는 원자), (3) **생성 컨텍스트 단위**(LLM에 전달되는 텍스트)를 겸한다. 세 역할의 최적 크기는 서로 다르다 — embedding은 작고 초점이 좁을수록 정밀하고, 생성 컨텍스트는 클수록 유리하다. 뒤에서 볼 hierarchical chunking(child로 검색, parent로 전달)과 contextual retrieval(작은 청크에 문맥을 주입)은 모두 이 역할 분리를 겨냥한 설계다. AWS 공식 문서도 hierarchical chunking의 근거를 같은 논리로 설명한다: "작은 텍스트 embedding이 더 정밀하지만, retrieval은 포괄적 문맥을 지향한다"([AWS Bedrock KB 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html)).

### 기본 전략 5종

- **Fixed-size**: N 토큰마다 자르고 인접 청크 간 overlap을 둔다. 구현·운영이 가장 단순하고 청크 수가 예측 가능하다. 문장·표·코드 블록 한가운데를 자르는 것이 태생적 결함.
- **문장/문단 경계**: 문장 분리기(또는 빈 줄)로 경계를 존중한다. Bedrock KB의 default chunking이 이 계열로, "약 300 토큰 단위로 자르되 문장 경계를 보존"한다([AWS 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html)).
- **Recursive**: 구분자 우선순위(예: `\n\n` → `\n` → 문장 → 공백)를 재귀적으로 적용해, 큰 구조 단위를 우선 보존하면서 크기 상한을 지킨다. LangChain의 `RecursiveCharacterTextSplitter`가 대표 구현이며, 공식 문서가 "일반 텍스트에 권장되는 기본값"으로 안내한다([LangChain 문서](https://python.langchain.com/docs/how_to/recursive_text_splitter/)).
- **Semantic**: 문장별 embedding을 계산하고, 인접 문장 간 유사도가 급락하는 지점(breakpoint)에서 자른다. 주제 전환을 따라가므로 경계 품질이 높지만, 인제스천 시점에 embedding 호출이 추가로 발생한다 — Bedrock 문서도 semantic chunking에 "foundation model 사용으로 인한 추가 비용"을 명시한다([AWS 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html)).
- **문서 구조 기반**: Markdown 헤딩, HTML 태그, PDF 섹션 같은 저자가 이미 그어 놓은 경계를 그대로 쓴다. 기술 문서·위키·코드 저장소처럼 구조가 신뢰할 만한 코퍼스에서 가장 강력하다. 헤딩 경로(`H1 > H2 > H3`)를 청크 메타데이터로 보존하면 뒤에 나올 contextual retrieval의 "문맥 프리펜드"를 LLM 호출 없이 공짜로 얻는 효과가 있다.

### Contextual Retrieval — 청크에 문맥을 주입한다

Anthropic의 Contextual Retrieval(2024-09)은 청킹 전략을 바꾸는 대신, **각 청크 앞에 그 청크를 문서 전체 맥락에서 설명하는 짧은 문맥(보통 50~100 토큰)을 LLM으로 생성해 프리펜드**한 뒤 embedding과 BM25 인덱스를 만든다([출처](https://www.anthropic.com/news/contextual-retrieval)). 원문 보고 수치:

| 구성 | top-20 retrieval 실패율 | 감소율 |
|---|---|---|
| baseline (일반 embedding) | 5.7% | — |
| + Contextual Embeddings | 3.7% | −35% |
| + Contextual BM25 (하이브리드) | 2.9% | −49% |
| + Reranking | 1.9% | −67% |

세 단계가 누적 결합이라는 점이 중요하다. 가장 큰 폭의 개선은 contextual embedding 단독이 아니라 **하이브리드 검색·reranking과의 결합**에서 나온다 — 이 결합 계층은 [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank)에서 별도로 다룬다.

::: warning 미정착 영역 — 이 수치를 그대로 옮겨 심지 마라
위 개선율은 Anthropic이 자체 선정한 데이터셋(코드베이스, 소설, ArXiv 논문 등)과 자체 평가 프로토콜에서 측정된 값이다. 원문 스스로 "Always run evals"라며 도메인별 재측정을 권고하고, 청크 크기·경계·overlap, embedding 모델(원문 실험에서는 Gemini와 Voyage embedding이 특히 효과적이었다고 보고), 문맥 생성 프롬프트가 모두 결과에 영향을 준다고 명시한다([출처](https://www.anthropic.com/news/contextual-retrieval)). 자체 코퍼스에서 [retrieval 평가](/06-vector-search/retrieval-evaluation)로 재측정하기 전까지 이 수치는 "가능성의 상한 힌트"일 뿐이다.
:::

**비용은 prompt caching이 결정한다.** 문맥 생성은 청크마다 LLM을 한 번씩 호출하는데, 매 호출의 프롬프트에 문서 전체가 들어간다. 문서 전체를 캐시 prefix에 두고 청크별 질의만 바꾸면, Anthropic 계산 기준 **문서 100만 토큰당 $1.02의 일회성 비용**(Claude 3 Haiku + prompt caching 기준)으로 떨어진다([출처](https://www.anthropic.com/news/contextual-retrieval)). 캐시 키 구조와 TTL, cache write/read 가격 배수는 [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics)에서 정의한 메커니즘을 그대로 따른다 — 청크 순회 간격이 캐시 TTL을 넘지 않도록 인제스천 파이프라인을 문서 단위로 배치하는 것이 핵심이다.

### Late Chunking — 분할을 embedding 뒤로 미룬다

Jina AI의 late chunking(2024-08)은 순서를 뒤집는다. 먼저 **문서 전체를 long-context embedding 모델의 transformer 레이어에 통과시켜 토큰 단위 벡터를 얻고, 그 다음 청크 경계별로 mean pooling**한다. 각 청크 벡터가 문서 전체의 attention을 거친 토큰 표현에서 나오므로, "그 회사(the company)" 같은 대용어가 선행 문맥의 의미를 담은 채 임베딩된다([Jina AI 공식 블로그](https://jina.ai/news/late-chunking-in-long-context-embedding-models/), [arXiv:2409.04701](https://arxiv.org/abs/2409.04701)).

BeIR 벤치마크에서 naive chunking 대비 nDCG@10 개선을 보고했다: SciFact 64.20% → 66.10%, TRECCOVID 63.36% → 64.70%, NFCorpus 23.46% → 29.98% (`jina-embeddings-v2-small-en`, 8,192 토큰 컨텍스트). 원문은 "문서가 길수록 late chunking이 더 효과적"이라고 보고한다([출처](https://jina.ai/news/late-chunking-in-long-context-embedding-models/)).

적용 조건이 명확하다: (1) 토큰 벡터에 접근 가능한 **long-context embedding 모델**이 필요하고(문서가 컨텍스트 윈도를 넘으면 별도 처리 필요), (2) **mean pooling** 기반이어야 하며, (3) 경계 결정 자체는 여전히 필요하다 — 분할 시점만 뒤로 미룰 뿐이다. Contextual retrieval이 인제스천 시 LLM 비용을 내는 대신 어떤 embedding 모델과도 조합 가능한 것과 달리, late chunking은 추가 LLM 호출이 없는 대신 모델 선택을 제약한다. 이 트레이드오프는 [embedding 모델 선택](/06-vector-search/embedding-model-choice)과 함께 결정해야 한다.

::: warning 미정착 영역
Contextual retrieval과 late chunking은 같은 문제("청크가 문서 문맥을 잃는다")에 대한 경쟁 답안이고, 두 방식의 공정한 head-to-head 비교는 각 벤더의 자체 벤치마크 외에 업계 표준 평가가 정착되지 않았다. 어느 쪽이 우월한지에 대한 일반론을 신뢰하지 말고 자체 데이터로 비교하라.
:::

### Bedrock Knowledge Bases와의 매핑

관리형 경로를 쓴다면 Bedrock KB가 위 전략의 부분집합을 설정만으로 제공한다([AWS 공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html)):

- **Default chunking**: 약 300 토큰, 문장 경계 보존.
- **Fixed-size chunking**: 최대 토큰 수 + 인접 청크 overlap 퍼센트 지정.
- **Hierarchical chunking**: parent/child 최대 토큰과 overlap(절대 토큰 수) 지정. 검색은 child로, 반환은 parent로 치환 — 그래서 반환 결과 수가 요청한 k보다 적을 수 있다. 문서는 S3 vector bucket을 벡터 스토어로 쓸 때 hierarchical chunking을 권장하지 않으며, 합산 8,000 토큰을 넘는 설정에서 메타데이터 크기 제한에 걸릴 수 있다고 경고한다.
- **Semantic chunking**: maximum tokens, buffer size(문장 앞뒤 몇 개를 함께 임베딩해 경계를 판단할지), breakpoint percentile threshold(임계 백분위가 높을수록 청크 수 감소·평균 크기 증가) 3개 하이퍼파라미터. foundation model 사용에 따른 추가 비용 발생.
- **No chunking**: 문서 1개 = 청크 1개. 사전 분할된 파일에 적합하며, 이 경우 페이지 번호 citation과 `x-amz-bedrock-kb-document-page-number` 메타데이터 필터를 쓸 수 없다.
- 이 외에 인제스천 시 **custom transformation(Lambda)** 훅으로 자체 청킹 로직을 끼워 넣을 수 있다 — no chunking 전략을 선택하고 청킹 로직을 담은 Lambda와 중간 저장용 S3 버킷을 지정하는 방식이며, contextual retrieval의 문맥 프리펜드를 관리형 인제스천 안에 구현하는 통로이기도 하다([AWS 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-custom-transformation.html)).

주의: contextual retrieval이나 late chunking은 Bedrock KB의 내장 옵션이 아니다. 관리형 옵션으로 부족하면 custom transformation 또는 자체 인제스천 파이프라인으로 넘어가야 하며, 이 결정은 벡터 스토어 선택과 얽힌다 — [AWS 벡터 스토어](/06-vector-search/aws-vector-stores) 참조.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 코퍼스 < 200k 토큰 | 청킹 없이 전체를 프롬프트에 + prompt caching | RAG 오버헤드 자체가 불필요 ([Anthropic](https://www.anthropic.com/news/contextual-retrieval)) | 코퍼스가 자라면 어차피 RAG로 이행 필요 |
| 구조 없는 균질 텍스트, 빠른 baseline | fixed-size + overlap 또는 recursive | 구현 단순, 청크 수·비용 예측 가능 | 의미 경계 절단 — baseline 이상을 기대하지 말 것 |
| 일반 산문, 기본값이 필요 | recursive (문단→문장 우선순위) | 구조 보존과 크기 상한의 균형, 사실상 업계 기본값 | 구분자 휴리스틱이 도메인과 안 맞으면 fixed와 다를 바 없음 |
| Markdown/HTML/기술 문서 | 문서 구조 기반 (헤딩 분할) + 헤딩 경로 메타데이터 | 저자가 그은 경계가 최고 품질, 문맥 메타데이터 공짜 | 구조 없는 문서에 무력, 섹션 크기 편차 큼 |
| 주제 전환이 잦은 긴 문서, 구조 부재 | semantic chunking | 경계 품질이 유사도 급락 지점을 따라감 | 인제스천 비용 증가, 하이퍼파라미터 3개 튜닝 부담 |
| 정밀 검색 + 넓은 생성 문맥 동시 필요 | hierarchical (child 검색 / parent 반환) | embedding 단위와 생성 단위의 역할 분리 | 반환 수 < k 가능, S3 vector bucket 비권장 ([AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html)) |
| 청크 단독으로 의미가 성립 안 하는 코퍼스 (재무 보고서, 계약서, 코드) | contextual retrieval | 실패율 최대 −67% 보고 (자체 재측정 전제) | 인제스천 LLM 비용 (~$1.02/M 토큰, caching 적용 시), 파이프라인 복잡도 |
| long-context embedding 모델 사용 가능, 추가 LLM 비용 회피 | late chunking | 인제스천 LLM 호출 없이 문맥 보존 | 모델 제약 (mean pooling, long context), 관리형 서비스 미지원 |
| 운영 최소화, AWS 관리형 | Bedrock KB 내장 옵션에서 선택 | 인제스천·재시도·인덱싱 관리 위임 | contextual/late chunking 내장 없음, 커스텀은 Lambda 훅으로 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 정답이 코퍼스에 있는데 top-k에 안 잡힘 | 청크가 지시어("그 회사", "이 방식")만 남기고 문맥을 잃음 | 실패 쿼리의 정답 청크를 눈으로 검사 — 청크 단독으로 질문에 답할 수 있는가 | contextual retrieval 또는 구조 기반 청킹 + 헤딩 메타데이터 |
| 표·코드 블록 질의만 유독 실패 | fixed-size가 표/코드 한가운데를 절단 | 청크 경계 덤프에서 잘린 markdown 표·코드 fence 비율 측정 | 구조 인지 splitter (표·코드 블록을 원자 단위로), recursive 구분자에 fence 추가 |
| 답은 맞는데 근거 문맥이 빈약, 환각성 보강 발생 | 검색 정밀도에 맞춘 소형 청크가 생성 문맥으로도 그대로 전달됨 | 프롬프트에 실제 주입된 컨텍스트 토큰 수 및 내용 검사 | hierarchical (child 검색 → parent 반환) 또는 검색 후 이웃 청크 확장 |
| 인접 청크가 top-k를 중복 점유해 유효 recall 하락 | 과도한 overlap → 준중복 벡터 다수 | top-k 결과 내 문서·오프셋 중복률 측정 | overlap 축소, MMR/중복 제거 후처리, k 상향 후 rerank |
| 인제스천 비용·시간 급증 | semantic chunking 또는 문맥 생성 LLM 호출이 문서량에 비례 | 인제스천 파이프라인의 모델 호출 수·비용 계측 | 문맥 생성에 소형 모델 + [prompt caching](/04-caching/prompt-caching-basics), 코퍼스 서브셋으로 A/B 후 전체 적용 |
| Bedrock KB에서 반환 결과가 요청 k보다 적음 | hierarchical chunking의 parent 치환 동작 (여러 child → 같은 parent) | retrieve API 응답의 결과 수와 parent 중복 확인 ([AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html)) | k 상향 또는 child/parent 크기 비율 조정 |
| 청킹 개선 배포 후에도 품질 그대로 | 기존 벡터가 재인덱싱되지 않아 신구 청크 혼재 | 인덱스 내 청크 메타데이터(전략 버전 태그) 분포 확인 | 전략 버전을 메타데이터로 태깅, 전체 재빌드 — [인덱스 마이그레이션](/06-vector-search/index-freshness-migration) 절차 |
| 특정 문서만 검색 품질 열화 | 파서가 PDF 레이아웃(다단, 각주)을 선형화하며 문장 순서 파괴 | 해당 문서의 파싱 결과 원문 대조 | 파서 교체/FM 파싱, 문제 포맷은 별도 파이프라인 분리 |

## 안티패턴

- ❌ `chunk_size=512, overlap=50`을 튜닝 없이 전 코퍼스에 영구 적용 → ✅ 코퍼스 유형별로 결정 표를 적용하고, [retrieval 평가](/06-vector-search/retrieval-evaluation)로 최소 2~3개 설정을 비교한 뒤 고정
- ❌ Anthropic의 −67% 수치를 근거로 성능 목표를 약속 → ✅ 해당 수치는 특정 데이터셋 결과임을 전제하고, 자체 golden set 재측정 값으로만 보고 (원문도 "Always run evals" 명시)
- ❌ 문맥 생성 LLM 호출을 청크마다 문서 전체를 새로 보내며 수행 → ✅ 문서 전체를 캐시 prefix로 고정하고 청크별 질의만 변경 — [prompt caching](/04-caching/prompt-caching-basics)으로 인제스천 비용을 구조적으로 절감
- ❌ 검색 정밀도를 위해 청크를 무한정 줄임 → ✅ 검색 단위와 생성 단위를 분리 (hierarchical / 이웃 확장) — 소형 청크의 정밀도와 생성 문맥의 완전성은 다른 축이다
- ❌ Markdown 코퍼스에 semantic chunking부터 도입 → ✅ 저자가 이미 그은 헤딩 경계를 먼저 활용 — 더 싸고 대개 더 정확하며, semantic은 구조 없는 텍스트를 위한 도구다
- ❌ 청킹 전략을 바꾸고 기존 인덱스에 신규 문서만 새 전략으로 추가 → ✅ 전략 변경은 전체 재임베딩 이벤트로 취급하고 버전 태그로 롤아웃 관리
- ❌ "no chunking이면 정보 손실 제로"라는 가정 → ✅ 문서 전체가 벡터 하나로 압축되면 오히려 표현이 희석된다 — no chunking은 사전 분할된 소형 문서 전용 (Bedrock KB에선 페이지 citation도 잃는다)

## 계측 (SLI)

청킹은 배포 후 조용히 썩는 컴포넌트이므로, 다음을 지속 계측한다.

- **Retrieval 실패율 (recall@k의 보수)**: golden set 기준 "정답 청크가 top-k에 없는 쿼리 비율". Anthropic이 개선율의 기준으로 쓴 지표(top-20 실패율)와 동일한 형태로 측정하면 외부 수치와의 비교가 가능하다. 측정 체계는 [retrieval 평가](/06-vector-search/retrieval-evaluation) 참조.
- **청크 크기 분포**: 평균이 아니라 p5/p50/p95 토큰 수. 구조 기반 청킹에서 p95가 embedding 모델 입력 한계에 근접하면 절단이 발생하고 있다는 신호다.
- **경계 절단률**: 문장/표/코드 블록 중간에서 끝나는 청크의 비율. 인제스천 시점에 정적으로 계산 가능한 가장 싼 품질 지표.
- **top-k 중복률**: 검색 결과 내 같은 문서·인접 오프셋 청크 비율. overlap 과다의 조기 경보.
- **인제스천 단가**: 문서 100만 토큰당 embedding + (해당 시) 문맥 생성 LLM 비용. contextual retrieval 도입 시 cache hit rate와 함께 추적 — cache miss가 늘면 단가가 배수로 뛴다 ([캐시 지표와 경제성](/04-caching/cache-metrics-economics) 참조).
- **컨텍스트 주입 토큰 수**: 쿼리당 LLM 프롬프트에 실제로 들어간 retrieval 토큰. 청크 크기 × k의 실효값으로, 추론 비용과 직결된다.

## 체크리스트

- [ ] 코퍼스가 200k 토큰 미만인지 확인했다 — 미만이면 청킹 대신 전체 프롬프트 + prompt caching을 검토했다
- [ ] 코퍼스를 구조 유형별(Markdown/PDF/코드/표 중심)로 분류하고, 유형별 청킹 전략을 결정 표에 따라 분리했다
- [ ] 청크 단독 가독성 검사를 했다 — 무작위 샘플 청크가 문서 없이 스스로 의미가 성립하는가
- [ ] golden set 기반 retrieval 실패율 측정 하네스를 청킹 변경 **전에** 구축했다
- [ ] contextual retrieval 도입 시 문맥 생성 경로에 prompt caching이 실제로 적중하는지(cache read 토큰 비율) 확인했다
- [ ] late chunking 검토 시 embedding 모델이 long-context + mean pooling 조건을 만족하는지 확인했다
- [ ] Bedrock KB 사용 시 hierarchical + S3 vector bucket 조합을 피했고, 합산 토큰이 8,000을 넘지 않는지 확인했다
- [ ] 청크 메타데이터에 전략 버전·헤딩 경로·문서 오프셋을 태깅했다
- [ ] 청킹 전략 변경 = 전체 재인덱싱 이벤트로 취급하는 롤아웃 절차가 있다
- [ ] 경계 절단률·청크 크기 분포·top-k 중복률이 대시보드에 있다

## 참고

- Anthropic, "Introducing Contextual Retrieval" (2024-09) — <https://www.anthropic.com/news/contextual-retrieval>
- Jina AI, "Late Chunking in Long-Context Embedding Models" (2024-08) — <https://jina.ai/news/late-chunking-in-long-context-embedding-models/>
- Günther et al., "Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models" — <https://arxiv.org/abs/2409.04701>
- AWS, "How content chunking works for knowledge bases" — <https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking-parsing.html>
- AWS, "Custom transformation (Lambda) during ingestion" — <https://docs.aws.amazon.com/bedrock/latest/userguide/kb-custom-transformation.html>
- LangChain, "How to recursively split text by characters" — <https://python.langchain.com/docs/how_to/recursive_text_splitter/>
- 관련 챕터: [프롬프트 캐싱 기초](/04-caching/prompt-caching-basics) · [AWS 벡터 스토어](/06-vector-search/aws-vector-stores) · [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank) · [retrieval 평가](/06-vector-search/retrieval-evaluation) · [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration)
