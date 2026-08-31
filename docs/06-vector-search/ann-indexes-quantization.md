---
title: ANN 인덱스와 양자화
description: HNSW·IVF·DiskANN 인덱스 선택 기준과 양자화(SQ/PQ/binary) 트레이드오프를 recall-지연-메모리 축으로 정리한다.
outline: [2, 3]
---

# ANN 인덱스와 양자화

::: tip 이 장에서 얻는 것
- 코퍼스 규모·메모리 예산·recall 목표에 따라 HNSW / IVF-PQ / DiskANN을 고르는 결정 규칙
- HNSW 파라미터(`m`, `ef_construction`, `ef_search`)의 튜닝 순서 — **ef_search를 먼저**
- int8(SQ) / PQ / binary 양자화의 압축률-recall 트레이드오프와 rescoring으로 만회하는 패턴
- pgvector·OpenSearch 등 실전 구현체의 기본값과 운영 함정(IVFFlat rebuild 등)
- recall proxy, `ef_search` 대비 지연 곡선 등 인덱스 품질을 상시 계측하는 SLI 설계
:::

이 장은 인덱스 **알고리즘 자체**를 다룬다. AWS 관리형 벡터 스토어(OpenSearch Service, Aurora pgvector, S3 Vectors 등) 간 선택은 [aws-vector-stores](./aws-vector-stores) 장에서 다루며, 그 장의 결정 표는 이 장의 알고리즘 이해를 전제로 한다.

## 왜 문제가 되는가

Agentic AI 플랫폼에서 벡터 검색은 RAG 파이프라인과 에이전트 메모리의 공통 하부 구조다. 문제는 정확한 k-NN(brute-force)의 비용이 코퍼스 크기에 선형으로 증가한다는 점이다. 1024차원 float32 벡터 1억 개면 벡터 데이터만 약 410GB(100M × 1024 × 4B)로, 단일 노드 인메모리 flat 스캔은 지연과 비용 모두에서 성립하지 않는다. 그래서 모든 프로덕션 벡터 검색은 ANN(Approximate Nearest Neighbor) 인덱스를 쓰고, 그 순간부터 **recall(정확도) — 지연 — 메모리(비용)의 3축 트레이드오프**가 플랫폼 엔지니어의 결정 문제가 된다.

이 결정이 어려운 이유는 세 축이 서로 독립적이지 않기 때문이다:

- 인덱스 알고리즘(HNSW vs IVF vs 디스크 기반 그래프)은 주로 **지연-recall 곡선의 모양**을 결정한다.
- 양자화(int8/PQ/binary)는 주로 **메모리 축**을 움직이지만, recall을 깎는 부작용이 있다.
- 두 결정은 곱해진다 — 예컨대 IVF-PQ는 인덱스 구조와 양자화를 결합한 설계다.

에이전트 워크로드는 여기에 두 가지 압력을 더한다. 첫째, 에이전트 메모리는 **스트리밍 인서트**가 많다(대화가 진행될수록 계속 쓰기 발생). 둘째, 멀티스텝 에이전트는 한 태스크에 검색을 수십 번 수행하므로 검색 지연이 태스크 지연에 곱셈으로 반영된다. 인덱스 선택을 "나중에 바꾸면 되는 설정"으로 취급하면 안 되는 이유다 — 인덱스 타입 변경은 대부분 전체 rebuild를 의미한다.

## 핵심 개념

### HNSW — 인메모리 그래프의 기본값

HNSW(Hierarchical Navigable Small World, [Malkov & Yashunin 2016](https://arxiv.org/abs/1603.09320))는 다층 근접 그래프를 탐욕 탐색하는 구조로, 인메모리 조건에서 지연-recall 곡선이 가장 좋은 축에 속해 사실상의 기본 선택이다. 파라미터는 세 개만 기억하면 된다:

| 파라미터 | 시점 | 의미 | 대표 기본값 |
|---|---|---|---|
| `m` | 빌드 | 노드당 최대 간선 수. 메모리와 빌드 시간에 직결 | pgvector 16 ([README](https://github.com/pgvector/pgvector)), hnswlib 16 ([README](https://github.com/nmslib/hnswlib)), OpenSearch 16 ([docs](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/)) |
| `ef_construction` | 빌드 | 빌드 시 후보 리스트 크기. 그래프 품질(recall 상한) 결정 | pgvector 64, hnswlib 200, OpenSearch 100 — **라이브러리마다 다르므로 반드시 확인** |
| `ef_search` (`ef`) | 쿼리 | 쿼리 시 후보 리스트 크기. recall-지연을 실시간으로 조절 | pgvector `hnsw.ef_search`=40, OpenSearch `ef_search`=100 |

**튜닝 순서가 핵심이다: `ef_search`를 먼저 올려라.** `ef_search`는 re-index 없이 쿼리(또는 세션) 단위로 바꿀 수 있는 유일한 손잡이다. OpenSearch의 [HNSW 하이퍼파라미터 선택 가이드](https://opensearch.org/blog/a-practical-guide-to-selecting-hnsw-hyperparameters/)도 같은 순서를 권고한다 — `ef_search`로 목표 recall에 도달하는지 먼저 확인하고, 그것으로 부족할 때만 `m`/`ef_construction`을 올려 rebuild한다. 반대로 하면 rebuild 몇 번을 낭비한 뒤에야 쿼리 파라미터 하나로 해결됐음을 알게 된다.

HNSW의 비용은 메모리다. 벡터 원본에 더해 노드당 간선(대략 `m` × 상위 레벨 보정 × 포인터 크기)이 추가되며, 그래프 전체가 RAM에 있어야 지연 특성이 유지된다. 삭제는 대부분 구현에서 tombstone(soft delete) 방식이라, 삭제·갱신이 잦은 에이전트 메모리 워크로드에서는 주기적 vacuum/재빌드 정책이 필요하다.

### IVF — 파티셔닝 기반, 빌드는 싸고 분포에 민감

IVF(Inverted File)는 k-means로 코퍼스를 `nlist`개 셀로 나누고, 쿼리 시 `nprobe`개 셀만 스캔한다. Faiss 문서는 `nlist ≈ C·√n`(C≈10) 수준을 출발점으로 제시하고 ([Faiss wiki](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)), pgvector는 IVFFlat에 대해 100만 행까지는 `rows/1000`, 그 이상은 `sqrt(rows)`, `probes`는 `sqrt(lists)`에서 시작하라고 안내한다 ([pgvector README](https://github.com/pgvector/pgvector)).

IVF의 구조적 약점은 **센트로이드가 빌드 시점 데이터 분포의 스냅샷**이라는 점이다. pgvector 공식 README는 IVFFlat 인덱스를 "테이블에 데이터가 어느 정도 쌓인 뒤" 만들라고 명시하는데, 같은 이유로 데이터 분포가 바뀌면(새 도메인 문서 대량 유입, 임베딩 모델 교체 등) 셀 경계가 실제 분포와 어긋나 recall이 조용히 열화된다 — **분포 변화가 예상되는 워크로드라면 주기적 rebuild(REINDEX)를 운영 계획에 넣어야 한다.** pgvector에서 HNSW와 IVFFlat은 동일한 SQL surface(`CREATE INDEX ... USING hnsw|ivfflat`)를 쓰므로 전환 자체는 쉽지만, README가 요약하듯 IVFFlat은 "빌드가 빠르고 메모리를 덜 쓰는 대신 speed-recall 트레이드오프에서 쿼리 성능이 낮다."

### DiskANN — 십억 규모를 단일 노드에서

DiskANN([Subramanya et al., NeurIPS 2019](https://proceedings.neurips.cc/paper/2019/hash/09853c7fb1d3f8ee67a61b6bf4a7f8e6-Abstract.html))은 Vamana 그래프를 사용해 **PQ 압축 코드는 RAM에, 그래프와 원본 벡터는 SSD에** 두는 하이브리드 설계다. RAM의 압축 코드로 탐색 방향을 정하고, SSD에서 읽은 원본 벡터로 거리를 재계산(rescoring)한다. 논문 기준 SIFT-1B(10억 벡터)를 64GB RAM + NVMe SSD 단일 워크스테이션에서 인덱싱하고, 95%+ recall@1을 5ms 미만 평균 지연으로 서빙했다. "십억 규모인데 전량 인메모리 HNSW는 비용이 성립하지 않는" 구간의 표준 답안이며, Azure AI Search·Milvus 등 여러 엔진이 이 계열 구현을 탑재하고 있다.

### ScaNN과 anisotropic quantization

Google의 ScaNN은 [anisotropic vector quantization](https://arxiv.org/abs/1908.10396) (Guo et al., ICML 2020)을 쓴다. 핵심 아이디어: MIPS(maximum inner product search)에서는 양자화 오차의 모든 방향이 동등하지 않다 — 원본 벡터에 **평행한 방향의 오차가 inner product 결과를 더 크게 왜곡**하므로, 이 방향에 더 큰 페널티를 주는 손실 함수로 코드북을 학습한다. inner product 기반 검색(정규화하지 않은 임베딩, 학습된 유사도)이라면 등방성 PQ보다 같은 비트 수에서 더 나은 recall을 기대할 수 있다는 것이 논문의 주장이다.

### 양자화 — 메모리를 사고 recall을 지불한다

양자화의 일반 원칙은 명확하다: **압축률만큼 메모리(와 캐시 적중률)를 얻고, 그 대가로 recall을 깎는다. 깎인 recall은 원본(또는 고정밀) 벡터로 상위 후보를 재채점하는 rescoring/refinement로 상당 부분 만회할 수 있다.** pgvector README도 binary quantization에 대해 "원본 벡터로 re-rank하라"고 명시한다.

| 기법 | 압축률 (float32 대비) | 특성 |
|---|---|---|
| fp16 / halfvec | 2× | recall 손실 사실상 무시 가능한 안전한 첫 수 (pgvector `halfvec`) |
| int8 / SQ (scalar quantization) | 4× | 차원별 스칼라 매핑. 구현 단순, 중간 압축 |
| PQ (product quantization) | 8~64× (서브벡터·비트 설정에 따라) | 코드북 학습 필요. 고압축 구간의 표준, rescoring 전제 |
| binary (1-bit) | 32× | Hamming 거리로 초고속 1차 필터. 반드시 re-rank와 결합 |

압축률 수치 자체는 산술이다(float32 4B → int8 1B = 4×, → 1bit = 32×). 어떤 기법이 어느 recall을 주는지는 임베딩 모델과 데이터셋에 강하게 의존하므로, 아래 실험 수치는 그 조건에서의 한 관측으로 읽어야 한다.

한 arXiv preprint 실험(voyage-3.5, 1024d, RAG 검색 품질 평가)은 다음을 보고했다 ([arXiv:2511.09545](https://arxiv.org/pdf/2511.09545)): flat(정확 탐색) 대비 **HNSW-float32는 지연을 약 26% 줄이면서 @10/@20 기준 측정 가능한 품질 손실이 없었고**, 반면 **HNSW+int8은 지연이 HNSW-float32와 사실상 동일한데 품질이 8~18% 하락**했다(N-Recall 지표 기준 -12.0% @10 ~ -17.3% @20).

> ⚠️ 비공식 출처 기반 — 공식 문서 교차확인 필요. 위 26% / 8~18% 수치는 단일 preprint의 특정 설정(voyage-3.5 1024d, 해당 코퍼스)에서의 결과다. 그러나 방향성 자체는 실무 직관과 일치한다: **인메모리 HNSW에서 int8의 이득은 메모리이지 지연이 아니다** — 거리 계산이 병목이 아닌 구간에서는 int8이 지연을 거의 줄이지 못하면서 recall만 깎을 수 있다. 메모리가 실제로 부족하지 않다면 int8을 "성능 최적화"로 켜지 마라.

"동일 압축률에서 PQ가 SQ보다 recall이 우수하다"는 주장은 자주 인용되지만 범용 공식 벤치마크로 확인하지 못했다 — 데이터셋 의존적이므로 일반화하지 말고 자기 코퍼스에서 측정하라. 확실히 말할 수 있는 것은 구조적 차이다: SQ는 차원별 독립 매핑이라 4× 부근이 실용 한계인 반면, PQ는 서브공간 코드북으로 8× 이상 고압축 구간을 커버하며 그 구간에서는 rescoring이 사실상 필수다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| recall 우선 + 코퍼스가 RAM에 들어감 (수백만~수천만) | **HNSW** (기본값에서 시작, `ef_search` 튜닝) | 인메모리 지연-recall 곡선 최상급, 스트리밍 인서트 지원 | 메모리 비용 최대. 삭제는 tombstone → 주기 정리 필요 |
| 십억 규모 또는 RAM 예산 엄격 | **DiskANN 계열** (PQ 코드 RAM + 그래프·원본 SSD) | 단일 노드 64GB RAM으로 1B 벡터, 95%+ recall@1, <5ms (논문 조건) | NVMe 필수. 빌드 시간 길고 구현체 선택지 제한 |
| RAM 제약 + 십억 미만 + 배치성 데이터 | **IVF-PQ** | 빌드 저렴, 메모리 최소, Faiss 등 성숙한 구현 | 분포 변화에 취약 → rebuild 계획 필수. 동일 recall에서 HNSW보다 지연 열위 |
| 스트리밍 인서트·삭제가 지배적 (에이전트 메모리) | **HNSW** (또는 인서트 친화 엔진) + 삭제 정리 정책 | IVF는 인서트가 센트로이드를 갱신하지 않아 분포 이탈 누적 | 메모리 비용. tombstone 누적 시 지연·recall 열화 |
| inner product 기반, 재현율-지연 극한 튜닝 | **ScaNN** (anisotropic PQ) | MIPS 특화 양자화 손실 함수 | 생태계가 Google 중심, 운영 선택지 좁음 |
| PostgreSQL 안에서 해결해야 함 | **pgvector HNSW** (IVFFlat은 빌드 속도가 절실할 때만) | 동일 SQL surface, 트랜잭션·조인과 결합 | 단일 노드 스케일 한계 → 초과 시 전용 엔진/관리형으로 |

메모리 산술로 감을 잡는 예: 1024d float32 1억 벡터는 벡터만 ~410GB — 인메모리 HNSW라면 그래프 오버헤드까지 512GB+ RAM 노드(들)가 필요하다. 같은 코퍼스를 DiskANN 계열로 옮기면 RAM에는 PQ 코드(예: 64B/벡터 ≈ 6.4GB)만 남고 원본과 그래프는 NVMe로 내려간다. 지연은 SSD 랜덤 리드가 더해져 인메모리보다 열위지만(논문 조건 <5ms 수준), 노드 비용은 한 자릿수 배로 줄어든다. **p95 몇 ms를 사기 위해 월 수천 달러의 RAM을 지불할 것인가**가 이 결정의 실체이며, 구체 수치는 자기 워크로드에서 측정해야 한다.

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| recall이 배포 후 점진 하락 (IVFFlat) | 데이터 분포 변화로 센트로이드-실분포 괴리 | 골든 쿼리셋으로 flat 스캔 대비 recall 주기 측정 | `REINDEX` 스케줄링. 변화 잦으면 HNSW 전환 |
| int8 켰는데 지연 개선 없음 + 품질 하락 | 인메모리 HNSW에서 거리 계산이 병목 아님 | 양자화 전후 p95와 recall을 같은 쿼리셋으로 A/B | 메모리가 목적이 아니면 롤백. 필요 시 fp16부터 |
| 양자화 후 특정 쿼리군만 품질 급락 | 코드북/스칼라 범위가 해당 서브도메인 미대표 | 도메인별 슬라이스 recall 측정 | 대표 샘플로 코드북 재학습 + rescoring 도입 |
| `ef_search` 올려도 recall 정체 | `ef_construction`/`m`이 낮아 그래프 품질이 상한 | `ef_search`를 크게 올려도 recall 포화 확인 | `m`·`ef_construction` 상향 후 rebuild |
| HNSW 지연이 시간이 지나며 악화 | 삭제 tombstone 누적, 그래프 파편화 | 삭제 비율 vs p95 추세 상관 확인 | 주기적 vacuum/재빌드, 삭제 많은 컬렉션 분리 |
| IVFFlat 생성 직후 recall 바닥 | 데이터가 적은 상태에서 인덱스 생성 (`lists` 대비 행 부족) | pgvector 문서의 트러블슈팅 항목 그대로 | 인덱스 drop 후 데이터 적재 뒤 재생성 |
| 빌드 중 OOM / 빌드 수 시간 초과 | `ef_construction` 과대 또는 인메모리 빌드 전제 초과 | 빌드 리소스 모니터링 | 파라미터 하향, 병렬 빌드 옵션, 디스크 기반 빌드 검토 |

## 안티패턴

- ❌ recall 미달을 rebuild 파라미터(`m`, `ef_construction`)부터 손대서 해결 → ✅ **`ef_search` 먼저** 올려 목표 도달 여부 확인, 포화할 때만 rebuild
- ❌ "양자화 = 성능 최적화"로 이해하고 기본 활성화 → ✅ 양자화는 **메모리 최적화**다. 메모리 병목이 확인됐을 때, recall 측정과 함께 도입
- ❌ 고압축 양자화(PQ/binary)를 rescoring 없이 단독 사용 → ✅ 원본(또는 fp16) 벡터로 상위 후보 re-rank를 기본 결합
- ❌ IVFFlat을 만들어 두고 분포 변화를 방치 → ✅ rebuild를 운영 캘린더에 명시(임베딩 모델 교체 시에는 무조건 전체 재색인)
- ❌ 라이브러리 기본값이 같을 거라 가정 (`ef_construction`이 64인지 100인지 200인지) → ✅ 사용 중인 구현체의 문서에서 기본값을 확인하고 IaC/DDL에 **명시적으로** 기록
- ❌ 벤치마크 recall(공개 데이터셋)로 프로덕션 품질 추정 → ✅ 자기 코퍼스에서 골든 쿼리셋 + flat 스캔 ground truth로 측정
- ❌ 인덱스 선택을 벡터 DB 제품 선택과 동일시 → ✅ 알고리즘 요구사항(이 장)을 먼저 정하고, 그것을 지원하는 스토어(다음 장)를 고른다

## 계측 (SLI)

인덱스는 "배포하면 끝"이 아니라 열화하는 자산이다. 최소 다음을 상시 계측한다:

- **Recall proxy**: 골든 쿼리셋(수백 개)에 대해 ANN 결과 vs flat 스캔(또는 `ef_search`를 매우 크게 준 결과)의 recall@k를 배치로 주기 측정. 절대값보다 **추세 하락**이 경보 대상이다.
- **지연 분포**: 검색 p50/p95/p99를 `ef_search`(또는 `nprobe`) 값과 함께 태깅해 기록 — 파라미터 변경의 효과를 곡선으로 볼 수 있어야 한다.
- **인덱스 신선도**: 쓰기 시점 대비 검색 가능 시점 지연(freshness lag). 에이전트 메모리에서는 "방금 저장한 기억이 다음 스텝에 검색되는가"가 기능 요구사항이다.
- **삭제/갱신 부채**: tombstone 비율, 마지막 rebuild 이후 경과와 유입 데이터 비율(IVF 계열은 이것이 rebuild 트리거).
- **자원**: 인덱스 상주 메모리, (DiskANN 계열이면) SSD 랜덤 리드 IOPS와 쿼리당 read 수.
- **rescoring 효과**: 양자화 사용 시 re-rank 전/후 recall 차이를 별도 지표로 — 이 차이가 커지면 양자화 설정이 과도하다는 신호다.

## 체크리스트

- [ ] 코퍼스 현재 크기와 12개월 성장 전망으로 메모리 풋프린트(벡터 + 인덱스 오버헤드)를 산술 계산했다
- [ ] recall 목표(예: recall@10 ≥ 0.95)를 **수치로** 합의하고 골든 쿼리셋 + flat 스캔 ground truth를 만들었다
- [ ] 인메모리 가능 + recall 우선 → HNSW, RAM 제약/십억 규모 → IVF-PQ 또는 DiskANN 계열로 1차 후보를 정했다
- [ ] 사용 구현체의 `m`/`ef_construction`/`ef_search` **기본값을 공식 문서에서 확인**하고 DDL/IaC에 명시했다
- [ ] 튜닝 순서를 `ef_search` → (`포화 시`) `m`·`ef_construction` rebuild로 문서화했다
- [ ] IVFFlat 사용 시: 데이터 적재 후 인덱스 생성, 분포 변화 시 rebuild 절차와 트리거 조건을 정의했다
- [ ] 양자화 도입 시: 메모리 병목 근거, 전/후 recall·p95 A/B 결과, rescoring 단계 유무를 기록했다
- [ ] 임베딩 모델 교체 = 전체 재색인임을 용량·시간 계획에 반영했다
- [ ] recall proxy·freshness lag·tombstone 비율을 대시보드와 경보에 연결했다
- [ ] 스토어 선택(관리형 서비스)은 [aws-vector-stores](./aws-vector-stores) 장의 결정 표로 이어서 검토했다

## 참고

- Malkov & Yashunin, *Efficient and robust approximate nearest neighbor search using HNSW* — <https://arxiv.org/abs/1603.09320>
- Subramanya et al., *DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node* (NeurIPS 2019) — <https://proceedings.neurips.cc/paper/2019/hash/09853c7fb1d3f8ee67a61b6bf4a7f8e6-Abstract.html>
- Guo et al., *Accelerating Large-Scale Inference with Anisotropic Vector Quantization* (ScaNN, ICML 2020) — <https://arxiv.org/abs/1908.10396>
- pgvector README (HNSW/IVFFlat 파라미터, halfvec, binary_quantize, re-rank 가이드) — <https://github.com/pgvector/pgvector>
- hnswlib README (M=16, ef_construction=200 기본값) — <https://github.com/nmslib/hnswlib>
- OpenSearch, *k-NN methods and engines* — <https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/>
- OpenSearch Blog, *A practical guide to selecting HNSW hyperparameters* — <https://opensearch.org/blog/a-practical-guide-to-selecting-hnsw-hyperparameters/>
- Faiss wiki, *Faiss indexes* (nlist/nprobe, 인코딩 압축 스펙트럼) — <https://github.com/facebookresearch/faiss/wiki/Faiss-indexes>
- *Practical RAG Evaluation: Cost-Latency-Quality Trade-offs* (HNSW-f32 -26% 지연 / int8 -8~18% 품질 실험, preprint) — <https://arxiv.org/pdf/2511.09545>
