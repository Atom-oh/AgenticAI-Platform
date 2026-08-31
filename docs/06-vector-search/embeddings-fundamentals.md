---
title: 임베딩 기초
description: 플랫폼 엔지니어 관점에서 임베딩의 운영적 성질 — 모델 종속성, 유사도 스케일의 비이식성, 비대칭 검색, 차원-비용 트레이드오프와 MRL — 을 정리하고 Part 6 전체의 지도를 그린다.
outline: [2, 3]
---

# 임베딩 기초

::: tip 이 장에서 얻는 것
- "임베딩이란 무엇인가"가 아니라 **플랫폼이 임베딩을 어떻게 다뤄야 하는가** — 운영 관점에서 놓치기 쉬운 네 가지 성질
- 임베딩이 모델에 종속된 좌표계라는 사실의 귀결: 모델 교체 = 전체 재인덱싱, 그리고 그것을 전제로 한 설계
- 코사인 유사도 절대값이 모델 간에 이식되지 않는 이유와, 하드코딩된 유사도 임계값이 배포 후 조용히 깨지는 메커니즘
- asymmetric retrieval — 쿼리와 문서가 다른 인코딩 경로를 타야 하는 모델(query prefix, `input_type`)에서 발생하는 실패군
- 차원 수 × 벡터 수 × 정밀도 = 저장/메모리 비용의 산수, 그리고 Matryoshka Representation Learning(MRL)이 이 트레이드오프를 바꾼 방식
- dense / sparse / hybrid의 개념 지도와 Part 6 나머지 챕터로 가는 길 안내
:::

## 왜 문제가 되는가

Part 3에서 확인했듯, 에이전트 정확도 사고의 트리아지 1번 질문은 "그 턴에 검색된 컨텍스트를 열어봤는가"다 — 검색이 오염되면 모델이 컨텍스트에 아무리 충실해도 답은 틀린다([검색과 환각된 인자](../03-accuracy-eval/retrieval-and-hallucinated-args)). 즉 **검색 품질은 에이전트 정확도의 상류(upstream)** 이고, 그 검색 품질의 최상류에 임베딩이 있다. 임베딩 단계에서 잘못된 결정은 인덱스, 하이브리드 가중치, 리랭커, 프롬프트 어디에서도 완전히 복구되지 않는다.

문제는 임베딩이 애플리케이션 코드처럼 다뤄지는 경우가 많다는 것이다. 애플리케이션 코드는 버전을 올리면 새 코드가 옛 코드를 대체한다. 임베딩은 다르다 — **임베딩 벡터는 특정 모델(정확히는 특정 모델의 특정 버전)이 정의한 좌표계 위의 점**이고, 그 좌표계는 모델 바깥에서 아무 의미가 없다. 모델 A로 인덱싱한 100M 벡터와 모델 B로 인코딩한 쿼리 벡터를 같은 공간에서 비교하는 것은 타입 에러인데, 벡터 DB는 이것을 잡아주지 않는다. float 배열의 차원만 맞으면 연산은 성공하고, 결과는 그럴듯한 쓰레기다.

이 성질에서 플랫폼 엔지니어가 감당해야 할 결정들이 파생된다. 임베딩 모델 교체는 라이브러리 업그레이드가 아니라 **데이터 마이그레이션**이다(전 코퍼스 재인코딩 + 재인덱싱 + 컷오버). 유사도 임계값은 모델의 함수이므로 설정 파일에 박아둔 `0.78`은 모델이 바뀌는 순간 의미를 잃는다. 요즘 검색 특화 모델 다수는 쿼리와 문서를 서로 다르게 인코딩하도록 훈련되어, 인제스천 파이프라인과 쿼리 파이프라인이 **정확히 짝이 맞는 호출 규약**을 유지해야 한다. 그리고 차원 수는 검색 품질 지표이기 이전에 저장·메모리·레이턴시 비용의 1차 결정 변수다.

이 장은 Part 6의 서론으로서 이 네 가지 성질을 고정하고, 각 성질이 어느 챕터에서 깊이 다뤄지는지 지도를 그린다.

## 핵심 개념

### 성질 1 — 임베딩은 모델에 종속된 좌표계다: 모델 교체 = 전체 재인덱싱

두 벡터의 유사도가 의미를 가지려면 **같은 모델, 같은 버전, 같은 전처리**로 생성되어야 한다. 서로 다른 모델의 임베딩 공간은 차원이 같아도 축의 의미가 다르고, 같은 모델 계열의 버전 업조차 공간 호환을 보장하지 않는다. 이 사실의 운영적 귀결은 세 가지다.

1. **임베딩 모델은 인덱스의 스키마다.** 인덱스 메타데이터에 모델 ID·버전·차원·정규화 여부·프롬프트/prefix 규약을 기록하고, 쿼리 경로에서 이 메타데이터와 쿼리 인코더가 일치하는지 검증할 수 있어야 한다. 이 검증이 없으면 "모델을 살짝 바꿨는데 검색이 미묘하게 나빠졌다"는 사고가 조용히 진행된다.
2. **모델 교체 비용에는 재인코딩 비용이 포함된다.** 새 모델의 벤치마크 점수가 아무리 좋아도, 교체하려면 전 코퍼스를 다시 인코딩해 새 인덱스를 만들고 트래픽을 컷오버해야 한다. 코퍼스가 크면 재인코딩 비용(API 과금 또는 GPU 시간)과 이중 인덱스 유지 기간의 저장 비용이 모델 선택 자체보다 큰 의사결정이 되기도 한다. 무중단 마이그레이션 절차(dual-write, shadow read, 컷오버)는 [index-freshness-migration](./index-freshness-migration)이 정본이다.
3. **임베딩 API의 "silent model update" 리스크.** 관리형 임베딩 API에서 별칭(alias) 모델 ID를 쓰면 제공자의 업데이트가 곧 좌표계 변경이 될 수 있다. 버전이 고정된(pinned) 모델 ID를 쓰고, 교체는 항상 명시적 마이그레이션으로 취급한다.

설계 시사점: **처음부터 재인덱싱이 일상 운영이라고 가정하라.** "임베딩은 한 번 만들면 끝"이라는 가정 위에 지은 파이프라인(원문을 버리고 벡터만 저장, 재인코딩 배치 경로 부재)은 첫 모델 교체에서 무너진다. 원문(또는 청크 원문)과 벡터를 항상 함께 보존하고, 전 코퍼스 재인코딩을 언제든 돌릴 수 있는 배치 경로를 상비하는 것이 기본기다.

### 성질 2 — 코사인 유사도의 절대값은 모델의 함수다: 임계값은 이식되지 않는다

코사인 유사도의 수학적 범위는 [-1, 1]이지만, **실제 점수 분포는 모델마다 다르다.** 어떤 모델은 무관한 문서 쌍도 0.6 이상을 찍고, 어떤 모델은 관련 문서가 0.4 근처에 분포한다. 이는 훈련 방식(contrastive loss의 temperature, 하드 네거티브 구성), 정규화 여부, 임베딩 공간의 이방성(anisotropy — 컨텍스트 임베딩 공간에서 임의의 두 벡터조차 높은 코사인 유사도를 보이는 현상, [Ethayarajh, 2019](https://arxiv.org/abs/1909.00512)) 등에 좌우되는 모델 고유의 성질이다.

플랫폼 관점의 귀결:

- **유사도 임계값은 모델과 코퍼스에 종속된 튜닝 파라미터다.** "0.8 이상이면 관련 있음" 같은 규칙을 코드나 설정에 하드코딩하면, 모델을 바꾸는 순간 필터가 전부를 통과시키거나 전부를 걸러낸다. 임계값이 필요하다면(예: "관련 문서 없음" 판정) 반드시 해당 모델 + 해당 코퍼스의 골든셋에서 분포를 측정해 캘리브레이션하고, 모델 마이그레이션 체크리스트에 임계값 재캘리브레이션을 포함시켜야 한다.
- **점수의 절대값이 아니라 순위(rank)가 1차 신호다.** 벡터 검색의 기본 계약은 "top-k의 순서"이지 "점수 0.83의 의미"가 아니다. 점수를 사용자에게 노출하거나 시스템 간 비교에 쓰는 설계는 그 자체로 냄새다.
- **거리 메트릭과 정규화의 짝을 확인하라.** 벡터가 단위 길이로 정규화되어 있으면 코사인 유사도와 내적(dot product)이 동일해지고 유클리드 거리와도 단조 관계가 된다 — OpenAI는 자사 임베딩이 길이 1로 정규화되어 있음을 명시한다([OpenAI Embeddings Guide](https://developers.openai.com/api/docs/guides/embeddings)). 정규화되지 않은 모델에서 내적을 쓰면 벡터 크기(norm)가 점수에 섞여 들어가며, 이 역시 모델별로 다르게 행동한다.

::: warning 미정착 영역
"관련 없음"을 유사도 절대값으로 판정하는 최선의 방법은 업계 합의가 없다. 임계값 캘리브레이션, top-1과 top-k 점수의 상대 격차 활용, 리랭커(cross-encoder) 점수로의 위임 등이 혼용되며, 어느 것도 모델 교체에 완전히 면역이 아니다. 확실한 것은 하나 — 캘리브레이션 없이 옮겨 적은 임계값은 반드시 깨진다는 것이다.
:::

### 성질 3 — 쿼리와 문서는 비대칭이다: asymmetric retrieval

검색은 본질적으로 비대칭 과제다 — 짧은 질문("환불 규정이 어떻게 돼?")과 긴 문서(환불 정책 전문)는 표면 형태가 전혀 다르지만 매칭되어야 한다. 최근 검색 특화 임베딩 모델 다수는 이 비대칭을 훈련에 반영해, **쿼리와 문서를 서로 다른 방식으로 인코딩**한다.

- **E5 계열**: 입력 텍스트에 `query: ` / `passage: ` prefix를 붙여야 하며, 모델 카드 FAQ는 prefix가 필수인지에 대해 "그렇게 훈련되었으므로, 빼면 성능 저하를 보게 된다"고 명시한다([multilingual-e5-large model card](https://huggingface.co/intfloat/multilingual-e5-large)).
- **Cohere Embed**: `input_type` 파라미터로 `search_query` / `search_document`를 구분하며, 시맨틱 검색에서 쿼리와 문서에 서로 다른 input type을 쓰도록 문서화되어 있다([Cohere Embeddings docs](https://docs.cohere.com/docs/embeddings)).
- **instruction 기반 모델**(BGE, GTE, Voyage 등 다수): 쿼리 앞에 태스크 지시문을 붙이는 규약을 갖는다. 규약의 구체 형태는 모델마다 다르다.

플랫폼 관점에서 이것이 위험한 이유는 **실패가 조용하기 때문**이다. prefix를 빠뜨려도 예외는 발생하지 않는다 — 벡터는 생성되고, 검색은 돌아가고, 품질만 벤치마크 대비 몇 포인트 낮은 상태로 프로덕션에 안착한다. 더 흔한 변종은 파이프라인 간 불일치다: 인제스천 워커는 `passage: `를 붙이는데 쿼리 서비스는 붙이지 않거나, 초기 인덱싱 배치와 증분 인덱싱 경로가 서로 다른 규약을 쓰는 경우. 이 불일치는 recall 지표의 완만한 하락으로만 나타나서, [retrieval-evaluation](./retrieval-evaluation)의 골든셋 회귀 테스트 없이는 사실상 탐지되지 않는다.

설계 시사점: 임베딩 호출을 각 서비스가 직접 하게 두지 말고, **prefix/instruction/전처리 규약을 캡슐화한 단일 임베딩 클라이언트(또는 내부 서비스)** 를 통해서만 인코딩하게 하라. 규약은 코드 리뷰가 아니라 아키텍처로 강제하는 것이다.

### 성질 4 — 차원 수는 비용의 1차 변수다, 그리고 MRL이 규칙을 바꿨다

벡터 하나의 저장 비용은 산수다: `차원 × 정밀도`. float32 기준 1,024차원은 벡터당 4KB, 3,072차원은 12KB다. 100M 청크 코퍼스라면 벡터 원본만 각각 약 0.4TB / 1.2TB이고, HNSW 같은 그래프 인덱스는 통상 이를 메모리에 올린 채 간선 오버헤드를 더한다 — 즉 차원 수는 곧 **메모리 풋프린트, 인스턴스 크기, 그리고 월 비용**이다. 차원이 늘면 거리 계산량도 선형으로 늘어 쿼리 레이턴시에도 직결된다. (정밀도 축의 압축 — int8/binary quantization — 은 [ann-indexes-quantization](./ann-indexes-quantization)에서 다룬다.)

전통적으로 이 트레이드오프는 모델 선택 시점에 고정되었다 — 모델이 1,536차원이면 1,536차원을 저장하는 수밖에 없었다. **Matryoshka Representation Learning(MRL)** 이 이 규칙을 바꿨다. MRL은 훈련 시 여러 접두(prefix) 차원 구간에 동시에 손실을 걸어 **정보를 앞쪽 차원에 집중**시키는 기법으로, 결과 임베딩은 뒤쪽 차원을 잘라내도 성능이 완만하게 감소한다. 원 논문은 ImageNet-1K 분류에서 정확도 손실 없이 최대 14배 작은 임베딩, 대규모 검색에서 최대 14배의 실제 속도 향상을 보고했다([Kusupati et al., 2022, arXiv:2205.13147](https://arxiv.org/abs/2205.13147)).

상용 모델들이 이를 채택했다. OpenAI의 text-embedding-3 계열은 `dimensions` 파라미터로 임베딩을 뒤에서부터 잘라 쓸 수 있게 하며("concept-representing properties"를 잃지 않고 축소), 3,072차원 text-embedding-3-large를 **256차원으로 잘라도 1,536차원 text-embedding-ada-002보다 높은 성능**을 유지한다고 문서화한다([OpenAI Embeddings Guide](https://developers.openai.com/api/docs/guides/embeddings)). Amazon Titan Text Embeddings V2도 1,024(기본)/512/256차원 출력을 지원한다([AWS Bedrock — Titan Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)).

플랫폼 관점의 귀결:

- **차원 수가 모델 선택과 분리된 독립 튜닝 축이 되었다.** "좋은 모델 + 잘라낸 차원"이 "그 차원이 네이티브인 저급 모델"보다 나은 경우가 많다.
- **잘라낸 벡터는 재정규화가 필요하다** (단위 벡터의 접두는 더 이상 단위 벡터가 아니다). 자르기를 지원하는 API는 대개 이를 처리해 주지만, 직접 자를 때는 빠뜨리기 쉽다.
- **coarse-to-fine 검색 패턴**이 가능해진다: 짧은 차원으로 후보를 빠르게 추리고 전체 차원으로 재정렬(adaptive retrieval). 이는 [ann-indexes-quantization](./ann-indexes-quantization)의 양자화와 결합 가능한 직교 축이다.

단, MRL 여부와 절단 시 품질 곡선은 모델마다 다르다 — MRL로 훈련되지 않은 모델의 벡터를 자르는 것은 그냥 정보 파괴다. 모델별 지원 여부와 수치는 [embedding-model-choice](./embedding-model-choice)가 정본이다.

한국어 코퍼스 관련 각주 하나: Titan V2처럼 다국어를 지원하는 모델도 교차 언어 검색(한국어 코퍼스를 독일어로 질의하는 식)은 sub-optimal하다고 명시한다([AWS Bedrock — Titan Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)). 한국어 특화 고려사항 역시 [embedding-model-choice](./embedding-model-choice)에서 다룬다.

### 개념 지도 — dense, sparse, hybrid

임베딩(dense) 검색만이 검색이 아니다. 세 계열의 성질을 한 장으로 요약하면:

| 계열 | 대표 | 매칭 원리 | 강점 | 약점 |
|---|---|---|---|---|
| **Sparse (lexical)** | BM25 계열 | 토큰 단위 정확 일치 + 빈도 통계 | 고유명사·코드·에러 코드·SKU 등 정확 일치, 설명 가능성, 재인덱싱 불필요(모델 없음) | 동의어·패러프레이즈에 취약, 어휘 불일치(vocabulary mismatch) |
| **Dense (embedding)** | bi-encoder 임베딩 | 의미 공간에서의 근접성 | 패러프레이즈·다국어·개념적 유사, 어휘가 달라도 매칭 | 정확 일치에 약함, 모델 종속(이 장의 성질 1~3 전부), 도메인 밖 어휘에서 붕괴 |
| **Hybrid** | dense + sparse 융합(RRF 등) + 리랭커 | 두 후보 집합의 융합 | 두 계열의 실패 모드가 겹치지 않아 상호 보완 | 파이프라인 복잡도, 융합 가중치라는 새 튜닝 축 |

플랫폼 서론에서 기억할 것은 하나다: **dense와 sparse는 실패하는 지점이 다르다.** 사용자가 에러 코드 `E4031`을 검색하는데 dense 검색이 "인증 오류 개요" 문서를 가져오는 것, 사용자가 "돈 돌려받는 법"을 검색하는데 BM25가 아무것도 못 찾는 것 — 이 상보성이 프로덕션 검색이 대부분 하이브리드로 수렴하는 이유다. 융합 방법(RRF), 가중치 튜닝, cross-encoder 리랭킹은 [hybrid-search-rerank](./hybrid-search-rerank)가 정본이다.

### Part 6 지도 — 이 파트에서 다루는 것

이 장의 네 성질이 각 챕터로 어떻게 이어지는지:

| 챕터 | 다루는 질문 | 이 장과의 연결 |
|---|---|---|
| [embedding-model-choice](./embedding-model-choice) | 어떤 모델을 고를 것인가 — 벤치마크 해석, 차원/비용, 한국어, 모델별 MRL 수치 | 성질 1·4의 구체화 |
| [chunking-strategies](./chunking-strategies) | 무엇을 임베딩할 것인가 — 청크 크기·경계·메타데이터 | 임베딩 입력의 설계 |
| [ann-indexes-quantization](./ann-indexes-quantization) | 어떻게 저장·탐색할 것인가 — HNSW/IVF, 양자화, recall-비용 곡선 | 성질 4의 인덱스 측 대응 |
| [hybrid-search-rerank](./hybrid-search-rerank) | dense만으로 부족할 때 — sparse 융합과 리랭킹 | 개념 지도의 상세 |
| [aws-vector-stores](./aws-vector-stores) | AWS에서 무엇으로 서빙할 것인가 — OpenSearch, S3 Vectors, pgvector 등 | 성질 4의 비용이 실제 청구서가 되는 곳 |
| [retrieval-evaluation](./retrieval-evaluation) | 잘 되는지 어떻게 아는가 — recall@k, NDCG, 골든셋 | 성질 2·3의 실패를 탐지하는 유일한 수단 |
| [index-freshness-migration](./index-freshness-migration) | 데이터와 모델이 바뀔 때 — 증분 인덱싱, 무중단 재인덱싱 | 성질 1의 운영 절차 |
| [graphrag-agentic-when-not](./graphrag-agentic-when-not) | 벡터 검색이 정답이 아닐 때 — GraphRAG, agentic retrieval, 그리고 안 쓰는 선택 | 상위 아키텍처 결정 |

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 임베딩 API 모델 ID 지정 | 버전 고정(pinned) ID | 별칭 ID는 제공자 업데이트가 곧 좌표계 변경 | 신모델 혜택을 자동으로 못 받음 — 교체는 명시적 마이그레이션으로 |
| "관련 문서 없음" 판정이 필요 | 골든셋에서 캘리브레이션한 모델별 임계값 + 리랭커 점수 병용 | 유사도 절대값은 모델의 함수 (성질 2) | 캘리브레이션 유지 비용, 모델 교체 시 재측정 필수 |
| 검색 특화 모델(E5, Cohere 등) 도입 | prefix/`input_type` 규약을 캡슐화한 단일 임베딩 클라이언트 | 규약 불일치는 조용한 품질 저하로만 나타남 (성질 3) | 공용 클라이언트라는 추가 컴포넌트 유지 |
| 대형 코퍼스 + 비용 압박, MRL 지원 모델 사용 가능 | 상위 모델 + 차원 절단 (예: 3-large @ 256d) | 절단된 상위 모델이 네이티브 소형 모델보다 나은 경우가 문서화됨 ([OpenAI](https://developers.openai.com/api/docs/guides/embeddings)) | 모델별 품질 곡선 상이 — 자체 골든셋 검증 필요, [embedding-model-choice](./embedding-model-choice) 참조 |
| 고유명사·코드·ID 검색이 트래픽에 섞임 | 처음부터 hybrid (dense + BM25) | dense와 sparse는 실패 지점이 다름 | 융합 가중치 튜닝 축 추가 — [hybrid-search-rerank](./hybrid-search-rerank) |
| 벡터만 저장 vs 원문 동반 저장 | 항상 원문(청크) 동반 저장 | 모델 교체 시 전 코퍼스 재인코딩이 가능해야 함 (성질 1) | 저장 비용 증가 — 그러나 벡터만 있는 인덱스는 마이그레이션 불가 자산 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 모델 업그레이드 후 검색이 전면적으로 무의미해짐 | 신모델 쿼리 벡터로 구모델 인덱스를 질의 (좌표계 불일치) | 인덱스 메타데이터의 모델 버전 vs 쿼리 인코더 버전 대조 | 전 코퍼스 재인코딩 + [index-freshness-migration](./index-freshness-migration)의 컷오버 절차; 인덱스-인코더 버전 검증을 쿼리 경로에 추가 |
| 모델 교체 후 "관련 없음" 필터가 전부 통과/전부 차단 | 구모델에서 캘리브레이션한 유사도 임계값 하드코딩 | 신모델로 골든셋 점수 분포를 그려 임계값 위치 확인 | 임계값 재캘리브레이션; 마이그레이션 체크리스트에 항목화 |
| 벤치마크 대비 recall이 이유 없이 몇 포인트 낮음 | query/passage prefix 또는 `input_type` 누락·불일치 | 인제스천과 쿼리 경로의 실제 API 페이로드를 각각 캡처해 비교 | 규약을 공용 임베딩 클라이언트로 캡슐화; 영향 범위의 문서 재인코딩 |
| 증분 인덱싱된 신규 문서만 검색이 안 됨 | 초기 배치와 증분 경로의 전처리/prefix/모델 버전 불일치 | 같은 문서를 두 경로로 인코딩해 벡터 비교 | 두 경로를 동일 클라이언트로 통일; 경로별 인코딩 회귀 테스트 |
| 코퍼스 성장에 따라 메모리 비용이 예산 초과 | 차원 수를 품질 지표로만 보고 비용 변수로 계획하지 않음 | `벡터 수 × 차원 × 정밀도` + 인덱스 오버헤드 산출 | MRL 차원 절단 검토(성질 4), 양자화([ann-indexes-quantization](./ann-indexes-quantization)), 스토리지 계층 선택([aws-vector-stores](./aws-vector-stores)) |
| 에러 코드·SKU·함수명 검색이 계속 실패 | dense 단독 구성 — 어휘 정확 일치는 dense의 구조적 약점 | 실패 쿼리를 BM25로 재실행해 비교 | hybrid 도입 — [hybrid-search-rerank](./hybrid-search-rerank) |
| 검색 품질 저하가 몇 주간 아무도 모르게 진행 | 검색 품질 SLI·골든셋 회귀 테스트 부재 (성질 2·3의 실패는 조용함) | 애초에 확인 수단이 없다는 것 자체가 확인 | [retrieval-evaluation](./retrieval-evaluation)의 골든셋 + 아래 SLI 상비 |

## 안티패턴

- ❌ 유사도 임계값 `0.8`을 설정 파일에 넣고 모델 교체 시 그대로 유지 → ✅ 임계값은 (모델, 코퍼스) 쌍의 캘리브레이션 산출물로 취급하고, 모델 마이그레이션 체크리스트에 재측정을 포함
- ❌ 각 서비스가 임베딩 API를 직접 호출하며 prefix를 알아서 처리 → ✅ 전처리·prefix·모델 버전을 캡슐화한 단일 임베딩 클라이언트/서비스를 유일한 인코딩 경로로 강제
- ❌ 저장 절약을 위해 원문을 버리고 벡터만 보관 → ✅ 원문 동반 저장 — 벡터는 특정 모델 버전에서만 유효한 파생 데이터이고, 원문 없이는 모델 교체가 불가능
- ❌ MRL 미지원 모델의 벡터를 뒤에서 잘라 저장 비용 절감 → ✅ 절단은 MRL로 훈련된 모델에서만 유효 ([arXiv:2205.13147](https://arxiv.org/abs/2205.13147)); 미지원 모델은 양자화 등 다른 축을 사용
- ❌ 별칭 모델 ID(`-latest` 류)로 인제스천과 쿼리를 운영 → ✅ 버전 고정 ID + 명시적 마이그레이션
- ❌ "임베딩 점수 0.91"을 사용자/타 시스템에 절대적 신뢰도로 노출 → ✅ 점수는 순위 산출용 내부 신호로 한정; 신뢰도가 필요하면 캘리브레이션된 별도 지표 설계
- ❌ 모델 선택을 벤치마크 1등으로 결정하고 차원·비용·재인덱싱 비용은 사후 정산 → ✅ `차원 × 벡터 수 × 정밀도`와 재인코딩 비용을 선택 기준에 포함 — [embedding-model-choice](./embedding-model-choice)

## 계측 (SLI)

임베딩 단계의 실패는 예외가 아니라 분포 이동으로 나타난다. 최소 계측 세트:

- **golden-set recall@k / NDCG (모델·인덱스 버전별)**: 성질 2·3의 조용한 실패를 잡는 유일한 신호. 배포 파이프라인의 회귀 게이트로 상비 — 측정 설계는 [retrieval-evaluation](./retrieval-evaluation).
- **top-1 유사도 점수 분포 (p50/p95, 시계열)**: 분포가 계단식으로 이동하면 모델·전처리·prefix 변경이 새어 들어간 것. 절대값이 아니라 **이동**을 알람 조건으로.
- **encoder-index version mismatch rate**: 쿼리 인코더 버전과 인덱스 메타데이터 버전의 불일치 카운트. 정상 상태에서 0이어야 하며, 0이 아니면 즉시 페이지.
- **empty / low-confidence retrieval rate**: 임계값 미달로 빈 결과가 반환된 비율. 급등은 임계값-모델 불일치 또는 코퍼스 공백 신호 — Part 3의 empty-result rate와 같은 계열([retrieval-and-hallucinated-args](../03-accuracy-eval/retrieval-and-hallucinated-args)).
- **embedding API 오류·스로틀률과 인제스천 지연(ingest-to-searchable lag)**: Bedrock 임베딩 모델은 RPM 기준으로 스로틀링됨을 유의 ([AWS Bedrock — Titan Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)) — 신선도 SLI는 [index-freshness-migration](./index-freshness-migration).
- **벡터 저장량·메모리 사용량 vs 코퍼스 크기 추세**: 성질 4의 비용 곡선을 예산 사고 전에 보이게 한다.

## 체크리스트

- [ ] 인덱스 메타데이터에 임베딩 모델 ID·버전·차원·정규화 여부·prefix/instruction 규약이 기록되어 있는가
- [ ] 쿼리 경로가 인코더 버전과 인덱스 버전의 일치를 검증하는가 (mismatch 시 fail-fast)
- [ ] 임베딩 인코딩이 단일 클라이언트/서비스로 캡슐화되어 있고, 인제스천·증분·쿼리 세 경로가 모두 그것만 쓰는가
- [ ] 사용 모델의 asymmetric 규약(query/passage prefix, `input_type`, instruction)을 확인했고 모델 카드 기준으로 테스트했는가
- [ ] 유사도 임계값이 존재한다면, 현재 (모델, 코퍼스)에서 캘리브레이션된 값인가 — 그리고 모델 마이그레이션 체크리스트에 재캘리브레이션이 있는가
- [ ] 원문(청크)이 벡터와 함께 보존되어 전 코퍼스 재인코딩이 언제든 가능한가
- [ ] `차원 × 벡터 수 × 정밀도` 기반의 저장·메모리 비용 추정과 성장 전망이 있는가 — MRL 차원 절단/양자화 옵션을 검토했는가
- [ ] 골든셋 기반 recall@k 회귀 테스트가 배포 게이트에 들어 있는가
- [ ] 모델 ID가 버전 고정인가 (별칭/latest 미사용)
- [ ] 정확 일치성 쿼리(코드, ID, 고유명사) 트래픽을 확인했고, 필요 시 hybrid 경로가 계획되어 있는가

## 참고

- Kusupati et al., *Matryoshka Representation Learning*, 2022 — https://arxiv.org/abs/2205.13147
- OpenAI, *Vector embeddings guide* (dimensions 파라미터, 절단·정규화·MTEB 수치) — https://developers.openai.com/api/docs/guides/embeddings
- AWS, *Amazon Titan Text Embeddings models* (V2 차원 256/512/1024, 8,192 토큰, 교차 언어 주의) — https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html
- Cohere, *Embeddings — input_type* — https://docs.cohere.com/docs/embeddings
- intfloat, *multilingual-e5-large model card* (query/passage prefix FAQ) — https://huggingface.co/intfloat/multilingual-e5-large
- Wang et al., *Text Embeddings by Weakly-Supervised Contrastive Pre-training* (E5), 2022 — https://arxiv.org/abs/2212.03533
- Ethayarajh, *How Contextual are Contextualized Word Representations?*, 2019 (임베딩 공간 이방성) — https://arxiv.org/abs/1909.00512
- 이 책: [검색과 환각된 인자](../03-accuracy-eval/retrieval-and-hallucinated-args) · [embedding-model-choice](./embedding-model-choice) · [hybrid-search-rerank](./hybrid-search-rerank) · [retrieval-evaluation](./retrieval-evaluation) · [index-freshness-migration](./index-freshness-migration)
