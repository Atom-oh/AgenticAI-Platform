---
title: 검색 평가
description: recall@k·nDCG·MRR 등 검색 품질 지표의 정의와 용도, golden set 구축, recall@20 실패율 3% 전환 임계치의 근거와 개선 단계 적용 순서를 다룬다.
outline: [2, 3]
---

# 검색 평가

::: tip 이 장에서 얻는 것
- recall@k, precision@k, nDCG, MRR, faithfulness의 **정의와 각각을 언제 보는지**를 결정 표로 구분한다
- golden set(질문–정답 문서 쌍) 구축 방법과 LLM 합성 질문 생성 시의 분포 편향 함정을 익힌다
- **recall@20 실패율 > 3%** 전환 임계치의 근거와, 임계치 초과 시 contextual retrieval → Contextual BM25 하이브리드 → 리랭커를 순차 적용하는 개선 루프를 확보한다 — [여섯 가지 통점](/00-intro/six-pain-points)의 전환 임계치 표 중 이 행의 정본 설명이 이 장이다
- "retrieve only"와 "retrieve-and-generate" 평가를 분리해 검색 문제와 생성 문제를 귀속시키는 원칙을 확립한다
- MTEB/BEIR 같은 공개 벤치마크를 방향성 지표로 강등하고 자체 도메인 재측정을 기본값으로 삼는다
:::

## 왜 문제가 되는가

벡터 검색을 도입한 팀 대부분이 겪는 패턴은 동일하다: 인덱스를 만들고, 몇 개 질문을 던져 "그럴듯한" 결과가 나오는 것을 확인하고, 프로덕션에 배포한다. 이후 "답이 이상하다"는 불만이 오면 임베딩 모델을 바꾸거나 chunk 크기를 조정하는데, **바꾼 것이 개선인지 악화인지 판정할 수단이 없다.** 눈으로 본 10개 질문은 통계가 아니고, 사용자 불만은 검색 문제인지 생성 문제인지조차 알려주지 않는다.

검색 평가가 없는 상태의 구체적 비용은 세 가지다.

1. **개선 작업이 도박이 된다.** chunking 변경, 임베딩 모델 교체, 하이브리드 검색 도입은 각각 상당한 재인덱싱·운영 비용이 드는데, 효과를 수치로 확인할 수 없으면 비용만 확정이고 효과는 미지수다.
2. **오류 귀속이 안 된다.** RAG의 최종 답이 틀렸을 때 원인은 (a) 정답 문서가 검색되지 않음, (b) 검색됐지만 순위가 낮아 잘림, (c) 검색은 완벽한데 생성이 무시·왜곡함 중 하나다. 셋의 해결책은 전혀 다르지만, end-to-end 관찰만으로는 구분되지 않는다.
3. **회귀를 감지하지 못한다.** 문서 코퍼스는 계속 늘고, 신규 문서 유형이 기존 chunking 가정을 깨뜨린다. 지표 시계열이 없으면 품질 하락은 사용자 이탈로만 관측된다.

에이전트 플랫폼에서는 문제가 증폭된다. 에이전트는 한 태스크에서 검색을 여러 번 호출하므로, 단일 검색의 실패율이 3%여도 5회 검색하는 태스크의 "최소 한 번 실패" 확률은 1 − 0.97⁵ ≈ 14%가 된다. 검색 계층의 실패율을 낮게 유지하고 그것을 **측정하고 있는 것** 자체가 에이전트 정확도의 전제 조건이다.

## 핵심 개념

### 지표의 정의 — 무엇을 세는가부터 정확히

검색 평가 지표는 모두 "질의 q에 대해 시스템이 반환한 상위 k개 문서"와 "정답 문서 집합"을 비교한다. 정의를 흐릿하게 알면 지표를 잘못 고른다.

- **recall@k** — 정답 문서 중 상위 k개 안에 포함된 비율. 정답이 1개인 golden set에서는 "정답 문서가 top-k에 있는가"의 이진 판정이 되고, 질의 전체 평균이 recall@k다. 이 장과 [여섯 가지 통점](/00-intro/six-pain-points)에서 말하는 **recall@20 실패율은 1 − recall@20**, 즉 "정답 chunk가 상위 20개 안에 없는 질의의 비율"이다. Anthropic의 contextual retrieval 실험도 동일한 정의(top-20 retrieval failure rate)를 사용한다. ([Anthropic — Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval))
- **precision@k** — 상위 k개 중 정답(관련) 문서의 비율. recall이 "빠뜨렸는가"를 보면 precision은 "쓰레기를 섞었는가"를 본다. LLM에 컨텍스트로 넣을 k개가 곧 프롬프트 토큰이므로, precision@k는 컨텍스트 오염과 토큰 비용의 지표이기도 하다.
- **nDCG@k (normalized Discounted Cumulative Gain)** — 관련도에 등급(graded relevance)이 있고 **순위가 중요할 때** 쓰는 지표. 상위에 있는 관련 문서일수록 높은 가중치를 주고(로그 할인), 이상적 순위의 DCG로 정규화한다. Järvelin & Kekäläinen(2002)이 제안했고 ([ACM TOIS — Cumulated gain-based evaluation of IR techniques](https://dl.acm.org/doi/10.1145/582415.582418)), BEIR 벤치마크의 1차 지표도 nDCG@10이다. ([BEIR, Thakur et al. 2021](https://arxiv.org/abs/2104.08663))
- **MRR (Mean Reciprocal Rank)** — 각 질의에서 **첫 번째** 정답 문서 순위의 역수(1위면 1, 3위면 1/3)를 평균한 값. TREC-8 QA 트랙에서 표준화된 지표로 ([Voorhees, The TREC-8 Question Answering Track Report](https://trec.nist.gov/pubs/trec8/papers/qa_report.pdf)), "정답 하나만 빨리 찾으면 되는" 시나리오(팩트 조회, 단일 문서 라우팅)에 맞는다.
- **faithfulness** — 생성된 답의 주장(claim) 중 검색된 컨텍스트로 뒷받침되는 비율. Ragas의 정의로는 "응답의 전체 claim 수 대비 컨텍스트가 지지하는 claim 수"이며 0~1 스케일이다. ([Ragas — Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)) **이것은 검색 지표가 아니라 생성 단계 지표다.** faithfulness가 낮다는 것은 검색이 틀렸다는 뜻이 아니라, 생성이 검색 근거 밖의 내용을 지어냈다는 뜻이다. 이 구분을 놓치면 생성 문제를 인덱스 튜닝으로 고치려는 헛수고가 시작된다.

한 가지 실무적 순서 관계: **recall이 우선, 순위 지표는 그다음이다.** 정답 문서가 top-k에 아예 없으면(recall 실패) 리랭커도 프롬프트도 구제할 수 없다. 반대로 recall은 충족되는데 순위가 낮아 컨텍스트 잘림에 걸리는 문제는 리랭커나 k 확대로 해결된다. 그래서 1차 SLI는 recall@k이고, nDCG/MRR은 리랭킹 계층을 도입한 뒤의 튜닝 지표다.

### Golden set 구축 — 평가의 병목은 지표가 아니라 데이터셋

모든 오프라인 지표는 **golden set**(질문–정답 문서 쌍의 집합)을 전제한다. 만드는 방법은 세 갈래이고, 셋을 섞는 것이 정석이다.

1. **프로덕션 로그 발췌 + 수동 라벨링.** 실제 사용자 질의에서 샘플링해 도메인 전문가가 정답 chunk/문서를 지정한다. 분포가 실제와 일치한다는 결정적 장점이 있고, 비용이 크다는 결정적 단점이 있다. 최소 규모의 감은 질의 유형별 30~50개 — 이보다 작으면 실패율 3% 같은 임계치를 통계적으로 판정할 수 없다(질의 100개에서 실패 3건과 4건의 차이는 노이즈다).
2. **LLM 합성 질문 생성.** 문서(또는 chunk)를 LLM에 주고 "이 문서로 답할 수 있는 질문"을 생성시키면 질문–정답 문서 쌍이 자동으로 확보된다. Ragas의 testset generation이 이 접근의 대표 구현으로, 문서에서 knowledge graph를 구축한 뒤 단일 문서 질의(single-hop)와 복수 문서 질의(multi-hop)를 시나리오별로 합성한다. ([Ragas — RAG Testset Generation](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/))
3. **실패 사례 회수.** 사용자 불만·재질문에서 나온 "검색이 놓친" 질의를 golden set에 편입한다. 회귀 테스트로서 가장 가치가 높다.

**합성 질문의 함정은 분포 편향이다.** LLM이 문서를 보고 만든 질문은 문서의 어휘를 그대로 재사용하는 경향이 있어, 실제 사용자 질의보다 검색하기 쉽다 — 어휘가 겹치면 BM25도 임베딩도 잘 맞춘다. 그 결과 합성 셋의 recall은 실제보다 부풀려진다. Ragas가 단순 "chunk당 질문 하나" 방식 대신 knowledge graph 기반으로 질의 유형·페르소나·난이도 분포를 명시적으로 제어하는 이유가 이것이다. ([Ragas — RAG Testset Generation](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/)) 완화책: (a) 질문 생성 프롬프트에 "문서의 표현을 그대로 쓰지 말고 사용자의 언어로" 지시, (b) 합성 질문을 실제 로그 질의와 길이·어휘 분포로 대조 검사, (c) 합성 셋과 실측 셋의 지표를 분리 보고 — 합성 recall 97%, 실측 recall 89% 같은 괴리 자체가 신호다.

golden set은 코드처럼 버전 관리한다. 코퍼스가 바뀌면 정답 chunk ID가 무효화되므로, 정답을 chunk ID가 아니라 **문서 ID + 정답 판정 기준(포함해야 할 내용)**으로 기술하고 chunk 매핑은 인덱싱 시점에 재계산하는 편이 재인덱싱에 강건하다.

### 전환 임계치: recall@20 실패율 > 3% — 이 수치의 출처와 적용 순서

[여섯 가지 통점](/00-intro/six-pain-points)의 전환 임계치 표에 있는 "검색 recall@20 실패율 > 3% → contextual retrieval → Contextual BM25 하이브리드 → 리랭커 순차 적용" 행의 근거가 이 절이다.

수치의 출처는 Anthropic의 contextual retrieval 실험이다. 여러 도메인 데이터셋 평균으로, 기본 임베딩 검색의 top-20 retrieval 실패율 5.7%에서 시작해 ([Anthropic — Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)):

| 단계 | 기법 | top-20 실패율 | 개선 폭 |
|---|---|---|---|
| 0 | 기본 임베딩 검색 (베이스라인) | 5.7% | — |
| 1 | **Contextual Embeddings** (chunk에 문서 맥락 요약을 전치) | 3.7% | −35% |
| 2 | 1 + **Contextual BM25** 하이브리드 | 2.9% | −49% |
| 3 | 2 + **리랭킹** (검색 150개 → rerank → top-20) | 1.9% | −67% |

3%라는 임계치는 이 사다리에서 나온다: 실패율이 3%를 넘는다는 것은 아직 2단계(Contextual BM25 하이브리드)조차 도달하지 못한 상태라는 뜻이므로, 다음 단계를 적용할 여지가 확실히 남아 있다. 반대로 3% 아래라면 남은 개선 폭 대비 리랭커의 지연·비용이 정당화되는지 따져야 하는 구간에 들어선 것이다.

순차 적용의 순서에도 이유가 있다. **저비용·저지연 개선부터 소진한다** — contextual retrieval은 인덱싱 시점 1회 비용(prompt caching 적용 시 문서 백만 토큰당 $1.02 수준으로 Anthropic이 보고)이고 쿼리 지연에는 영향이 없다. BM25 하이브리드는 인프라 추가지만 여전히 쿼리당 밀리초 단위다. 리랭커는 쿼리 경로에 모델 호출을 얹으므로 지연·비용이 질의마다 발생한다 — 마지막에 둔다. ([Anthropic — Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval))

각 단계의 구현은 별도 장이 정본이다: contextual retrieval과 chunk 설계는 [청킹 전략](/06-vector-search/chunking-strategies), BM25 하이브리드와 리랭커는 [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank). 이 장의 역할은 **각 단계를 밟기 전후로 동일한 golden set에서 실패율을 재측정해 사다리의 어느 칸에 있는지 판정하는 것**이다.

한 가지 주의: 위 수치는 Anthropic의 실험 코퍼스(코드베이스, 논문, 소설 등) 평균이며, 자체 도메인의 절대값은 다를 수 있다. 임계치 3%는 "개선 사다리를 계속 오를지 판단하는 트리거"로 쓰고, 자체 베이스라인 대비 상대 개선 폭으로 각 단계의 효과를 판정하라.

### Retrieve-only vs retrieve-and-generate — 평가를 두 개로 쪼개는 원칙

이 장의 지표(recall, nDCG, MRR)는 전부 **retrieve-only 평가**, 즉 생성 없이 검색 결과만 채점하는 평가다. 최종 답 품질(correctness, faithfulness)은 **retrieve-and-generate 평가**의 영역이다. 둘을 항상 쌍으로 운영해야 오류가 귀속된다:

- retrieve-only 낮음 → 인덱스·chunking·임베딩·질의 처리 문제. 이 Part의 다른 장들로.
- retrieve-only 높음 + retrieve-and-generate 낮음 → 생성 프롬프트·모델·faithfulness 문제. 검색 계층을 건드리지 마라.

Amazon Bedrock Evaluations가 RAG 평가 잡을 정확히 이 두 유형(retrieve only / retrieve and generate)으로 제공하며 ([Bedrock — Evaluate the performance of RAG sources](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-kb.html)), 잡 유형·지표·BYOI 운영 방법의 정본은 [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations)다. 이 장에서 가져갈 것은 서비스 사용법이 아니라 원칙이다: **검색 문제와 생성 문제는 다른 지표, 다른 데이터셋 요구사항, 다른 해결책을 가지므로 평가 파이프라인 자체를 분리 설계한다.** 참고로 Bedrock의 retrieve-only 지표(context relevance 등)는 LLM-judge 기반이라 golden set 없이도 돌지만, 이 장의 recall@k는 정답 라벨 기반이라 golden set이 필수다 — 전자는 라벨 없는 대량 스크리닝, 후자는 임계치 판정용으로 역할이 다르다.

### 공개 벤치마크는 방향성 지표다

MTEB는 8개 태스크 유형·58개 데이터셋으로 임베딩 모델을 비교하는 벤치마크이고, 어떤 단일 모델도 전 태스크를 지배하지 못한다는 것이 원 논문의 결론이다. ([MTEB, Muennighoff et al. 2022](https://arxiv.org/abs/2210.07316)) BEIR는 18개 데이터셋에서 검색 모델의 zero-shot 일반화를 측정했는데, 핵심 발견은 **in-domain 성능이 out-of-domain 성능을 예측하지 못하며**, 학습 도메인 밖에서는 BM25가 다수의 dense retriever를 이기는 강력한 베이스라인이라는 것이다. ([BEIR, Thakur et al. 2021](https://arxiv.org/abs/2104.08663))

플랫폼 관점의 결론: 리더보드 순위는 후보 모델을 3~5개로 좁히는 용도까지만 쓰고, **최종 선택은 자체 golden set에서의 recall@k 재측정으로 한다.** 특히 한국어·사내 용어·코드 혼합 문서처럼 벤치마크 분포에서 먼 코퍼스일수록 리더보드와 실측의 괴리가 크다. 임베딩 모델 선택 기준의 정본은 [임베딩 모델 선택](/06-vector-search/embedding-model-choice)이며, 그 장의 "자체 재측정 필수" 원칙과 이 장의 golden set이 맞물린다 — 재측정에 쓸 데이터셋이 바로 이 장에서 만든 것이다.

### 온라인 신호와 오프라인 지표의 결합

golden set 기반 오프라인 지표는 통제된 회귀 감지에 강하지만, 분포 이동(사용자가 새로운 유형의 질문을 하기 시작함)은 늦게 잡는다. 프로덕션의 행동 신호를 보조 지표로 결합한다:

- **클릭/인용률** — 검색 결과가 UI에 노출되거나 답변에 인용될 때, 사용자가 클릭·확인한 비율. 순위 품질의 프록시.
- **채택률** — 에이전트가 검색 결과를 실제로 답변 근거로 사용한 비율(인용 메타데이터로 측정). 검색은 됐지만 생성이 버리는 passage가 많다면 precision 문제의 신호.
- **재질문율** — 같은 세션에서 사용자가 질문을 바꿔 다시 묻는 비율. 검색+생성 어딘가의 실패를 뜻하는 상류 신호이며, 재질문된 원 질의는 golden set 후보로 자동 회수한다.

온라인 신호는 원인을 말해주지 않고 방향만 말해준다. 운영 루프는 "온라인 신호 악화 → 해당 질의를 golden set에 편입 → 오프라인 retrieve-only/retrieve-and-generate 쌍 평가로 귀속 → 해당 계층 수정 → 오프라인 재측정으로 확인"의 순환이다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 검색 계층의 1차 SLI, 전환 임계치 판정 | **recall@k 실패율** (k = 생성에 넣는 chunk 수, 기본 20) | 정답이 top-k에 없으면 하류가 구제 불가 — 최우선 병목 | 정답 라벨(golden set) 필수; 순위 품질은 못 봄 |
| 컨텍스트 오염·토큰 비용 진단 | **precision@k** | top-k에 섞인 무관 chunk가 프롬프트를 오염시키고 비용을 올림 | 관련/무관 이진 라벨 필요; recall과 트레이드오프 |
| 리랭커 도입 전후 비교, 순위 튜닝 | **nDCG@k** | 순위 가중 — 같은 recall에서도 순위 개선을 구분 | graded relevance 라벨링 비용이 가장 큼 |
| 단일 정답 조회형 질의(팩트 조회, 문서 라우팅) | **MRR** | 첫 정답의 위치만 중요할 때 해석이 직관적 | 정답 2개 이상·순위 전체 품질에는 부적합 |
| 생성이 검색 근거를 지어내는지 진단 | **faithfulness** (생성 단계 지표) | 검색 지표가 정상인데 답이 틀릴 때의 귀속 수단 | LLM-judge 기반 — 절대값보다 추세·상대 비교로 사용 |
| 라벨 없이 대량 스크리닝 | Bedrock retrieve-only의 LLM-judge 지표 | golden set 없이 실행 가능 | judge 비용·편향; 임계치 판정의 정본은 라벨 기반 recall |
| 임베딩 모델 후보 선정 | MTEB/BEIR 리더보드로 3~5개 압축 → 자체 golden set 재측정 | zero-shot 일반화는 도메인별 편차가 큼 ([BEIR](https://arxiv.org/abs/2104.08663)) | 재측정 파이프라인 구축 비용 |
| 분포 이동·신규 실패 유형 감지 | 온라인 신호(클릭·채택·재질문율) | golden set이 못 보는 실제 분포를 관측 | 원인 귀속 불가 — 오프라인 평가로 넘겨야 함 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 합성 golden set에서 recall 97%인데 사용자 불만 지속 | LLM 합성 질문이 문서 어휘를 재사용 → 실제보다 쉬운 분포 (분포 편향) | 합성 질의와 프로덕션 로그 질의의 어휘 중복·길이 분포 비교; 실측 질의 50개로 별도 측정 | 합성 프롬프트에 패러프레이즈 강제, 실측 셋 분리 운영, 재질문 로그를 golden set에 편입 |
| recall@20은 높은데 최종 답이 오답 | 검색 문제가 아니라 생성 문제 — 근거 무시·왜곡 | retrieve-and-generate 평가(faithfulness, correctness)를 같은 질의로 실행 | 생성 프롬프트·모델 수정; 검색 계층 튜닝 중단 ([aws-evaluations](/03-accuracy-eval/aws-evaluations)) |
| nDCG 개선 작업을 했는데 체감 품질 불변 | recall이 병목인 상태에서 순위만 튜닝 — 정답이 top-k 밖 | recall@k 실패 케이스 비율 먼저 확인 | 실패율 > 3%면 순위 튜닝 전에 contextual retrieval/하이브리드부터 ([chunking-strategies](/06-vector-search/chunking-strategies), [hybrid-search-rerank](/06-vector-search/hybrid-search-rerank)) |
| 리랭커 도입 후 지표 개선 없음 | 리랭커에 주는 후보 풀이 좁아 정답이 후보에 없음 (recall 병목 재발) | 리랭커 입력 상위 N(예: 150)에서의 recall을 별도 측정 | 후보 풀 확대 후 rerank — Anthropic 실험도 150 → top-20 구성 ([Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)) |
| 재인덱싱 후 golden set 평가가 전부 실패 | 정답을 chunk ID로 고정 → chunk 경계 변경에 라벨 무효화 | 실패 케이스에서 정답 문서 자체는 검색되는지 문서 ID 단위로 확인 | 정답을 문서 ID + 내용 기준으로 기술, chunk 매핑은 인덱싱 시 재계산 |
| 지표가 배포마다 ±수 %p 요동 | golden set이 작아 통계적 검정력 부족 | 질의 수 확인 — 100개 미만이면 3% 임계치 판정 불가 | 유형별 최소 30~50개로 확충; 신뢰구간을 함께 보고 |
| 벤치마크 상위 모델로 교체했는데 recall 하락 | 리더보드 in-domain 성능이 자체 도메인으로 전이 안 됨 ([BEIR](https://arxiv.org/abs/2104.08663)) | 교체 전후를 동일 golden set에서 A/B 측정 | 리더보드는 후보 압축용으로 강등 ([embedding-model-choice](/06-vector-search/embedding-model-choice)) |
| 온라인 재질문율 상승, 오프라인 지표는 정상 | 사용자 질의 분포 이동 — golden set이 낡음 | 최근 로그 질의와 golden set의 유형 분포 비교 | 재질문·미채택 질의를 golden set에 정기 편입(분기 단위 리프레시) |

## 안티패턴

- ❌ 질문 10개 눈으로 확인하고 "검색 잘 되네" → ✅ 유형별 30~50개 golden set에서 recall@k 실패율을 수치로 재고, 배포마다 재측정한다.
- ❌ faithfulness가 낮으니 임베딩 모델 교체 → ✅ faithfulness는 생성 단계 지표다. retrieve-only recall부터 확인해 귀속시킨다.
- ❌ recall이 3% 임계치를 넘는데 리랭커부터 도입 → ✅ 저비용 순서대로: contextual retrieval → Contextual BM25 하이브리드 → 리랭커. 각 단계 후 재측정.
- ❌ 합성 질문만으로 golden set 구성 → ✅ 프로덕션 로그 발췌·실패 사례 회수를 섞고, 합성/실측 지표를 분리 보고한다.
- ❌ MTEB 1위 모델을 측정 없이 채택 → ✅ 리더보드는 후보 압축까지, 판정은 자체 golden set에서.
- ❌ golden set을 만들고 방치 → ✅ 코퍼스·질의 분포와 함께 낡는다. 재질문 로그 편입과 정기 리프레시를 프로세스로 만든다.
- ❌ 지표 하나(예: nDCG)로 모든 논의 → ✅ recall(누락) / precision(오염) / nDCG·MRR(순위)은 다른 병목을 본다. 병목에 맞는 지표를 골라 든다.

::: warning 미정착 영역
LLM-judge 기반 검색·생성 지표(context relevance, faithfulness 등)의 **절대 임계값**은 정착되지 않았다. judge 모델·프롬프트가 바뀌면 점수 분포가 이동하므로, 현재의 안전한 사용법은 동일 judge 구성 하에서의 상대 비교와 추세 감시다 ([aws-evaluations](/03-accuracy-eval/aws-evaluations)의 동일 경고 참조). 반면 라벨 기반 recall@k는 결정론적이라 임계치(3%) 운용이 가능하다 — 게이트는 라벨 기반 지표에 걸고, judge 지표는 보조 신호로 두는 것이 현시점의 실무 균형이다.
:::

## 계측 (SLI)

- **recall@20 실패율** (1차 SLI) — golden set 기반, CI와 야간 배치에서 측정. 임계치 3% 초과 시 개선 사다리의 다음 단계 착수. 코퍼스 스냅샷·golden set 버전을 결과에 태깅해 시계열 비교 가능하게 유지한다.
- **precision@k / nDCG@k** (2차) — 리랭킹 계층 도입 후 활성화. 리랭커 입력 풀(예: top-150)에서의 recall도 별도 계측 — 리랭커 효과의 상한을 결정하는 값이다.
- **retrieve-only vs retrieve-and-generate 점수 쌍** — 두 시계열의 괴리 확대가 생성 계층 회귀의 신호. 운영은 [aws-evaluations](/03-accuracy-eval/aws-evaluations)의 batch 평가 패턴 참조.
- **온라인 신호** — 검색 결과 채택률(인용 메타데이터 기반), 세션 내 재질문율. 임계치가 아니라 추세로 감시하고, 악화 시 해당 질의를 golden set으로 회수하는 파이프라인을 자동화한다.
- **평가 파이프라인 건강성** — golden set 크기, 마지막 리프레시 시점, 합성/실측 비율. 평가가 낡으면 지표가 아니라 착시를 계측하게 된다.

## 체크리스트

- [ ] golden set이 존재하고, 질의 유형별 30~50개 이상이며, 버전 관리되는가
- [ ] 합성 질문의 분포 편향을 점검했는가 (실측 질의와 어휘·길이 분포 대조, 합성/실측 지표 분리 보고)
- [ ] recall@20 실패율을 배포 파이프라인에서 자동 측정하는가
- [ ] 실패율 > 3%일 때의 다음 조치(contextual retrieval → 하이브리드 → 리랭커)가 어느 단계까지 적용됐는지 알고 있는가
- [ ] retrieve-only와 retrieve-and-generate 평가를 분리 운영해 오류를 귀속시키는가 ([aws-evaluations](/03-accuracy-eval/aws-evaluations))
- [ ] faithfulness를 검색 지표로 오용하지 않는가 (낮으면 생성 계층 조사)
- [ ] 임베딩 모델·chunking 변경을 자체 golden set A/B로 판정하는가 (리더보드 순위로 판정 금지)
- [ ] 재질문·미채택 질의를 golden set에 편입하는 회수 루프가 있는가
- [ ] 정답 라벨이 chunk ID가 아닌 문서 ID + 내용 기준으로 기술되어 재인덱싱에 강건한가
- [ ] 리랭커가 있다면 리랭커 입력 풀에서의 recall을 별도 측정하는가

## 참고

- [Anthropic — Introducing Contextual Retrieval (top-20 실패율 5.7% → 1.9%, 개선 사다리 수치의 출처)](https://www.anthropic.com/news/contextual-retrieval)
- [BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models — Thakur et al., 2021](https://arxiv.org/abs/2104.08663)
- [MTEB: Massive Text Embedding Benchmark — Muennighoff et al., 2022](https://arxiv.org/abs/2210.07316)
- [Järvelin & Kekäläinen — Cumulated gain-based evaluation of IR techniques (nDCG 원 논문, ACM TOIS 2002)](https://dl.acm.org/doi/10.1145/582415.582418)
- [Voorhees — The TREC-8 Question Answering Track Report (MRR)](https://trec.nist.gov/pubs/trec8/papers/qa_report.pdf)
- [Ragas — Faithfulness 지표 정의](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)
- [Ragas — RAG Testset Generation (합성 질의 생성과 분포 제어)](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/)
- [Amazon Bedrock — Evaluate the performance of RAG sources (retrieve-only vs retrieve-and-generate)](https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation-kb.html)
- 관련 장: [여섯 가지 통점](/00-intro/six-pain-points) · [청킹 전략](/06-vector-search/chunking-strategies) · [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank) · [임베딩 모델 선택](/06-vector-search/embedding-model-choice) · [AWS 평가 서비스](/03-accuracy-eval/aws-evaluations) · [검색 실패와 헛짚은 인자](/03-accuracy-eval/retrieval-and-hallucinated-args)
