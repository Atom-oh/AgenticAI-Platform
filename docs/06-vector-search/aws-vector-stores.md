---
title: AWS 벡터 스토어
description: OpenSearch(Serverless/관리형), Aurora pgvector, S3 Vectors, Kendra, Bedrock Knowledge Bases를 플랫폼 엔지니어의 서비스 선택 관점에서 비교한다.
outline: [2, 3]
---

# AWS 벡터 스토어

::: tip 이 장에서 얻는 것
- AWS 벡터 스토어 5개 선택지(OpenSearch Serverless / OpenSearch 관리형 / Aurora PostgreSQL + pgvector / S3 Vectors / Kendra)의 공식 문서 기반 제약과 비용 구조
- 규모·지연·비용·신선도·엔타이틀먼트·운영 부담 6개 축으로 정리한 결정 표
- Bedrock Knowledge Bases의 Quick create(관리형 프로비저닝) vs 자체 벡터 스토어 지정의 실질적 차이
- 공용 RAG 코퍼스를 제공하는 플랫폼 입장에서 테넌트 격리를 인덱스 분리로 할지 메타데이터 필터로 할지 판단하는 기준
:::

이 챕터는 **AWS 서비스 선택** 관점이다. HNSW/IVF 같은 인덱스 알고리즘과 양자화의 원리는 [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization)가 다룬다 — 여기서는 각 서비스가 어떤 알고리즘 옵션을 노출하는지만 언급하고, 파라미터 튜닝은 그쪽으로 넘긴다.

## 왜 문제가 되는가

에이전트 플랫폼에서 벡터 스토어는 "하나 골라서 끝"이 아니다. 플랫폼 엔지니어는 보통 여러 팀의 RAG 워크로드를 하나의 공용 인프라로 수용해야 하고, 이때 세 가지 압력이 동시에 걸린다.

**첫째, 비용 바닥(cost floor)이 서비스마다 다르다.** OpenSearch Serverless는 "serverless"라는 이름과 달리 컬렉션이 존재하는 동안 최소 OCU가 상시 과금된다 — 트래픽이 0이어도 프로덕션 구성 기준 최소 2 OCU(인덱싱 1 + 검색 1), OCU당 시간 요금이 계속 나간다. 팀마다 컬렉션을 하나씩 만들어주는 셀프서비스 모델을 그대로 두면, PoC 10개가 각각 월 수백 달러의 고정비를 만든다. 반대로 S3 Vectors는 저장량·요청량 과금이라 유휴 비용이 사실상 0이지만, 그 대가로 쿼리 지연 특성이 다르다.

> 출처: [Amazon OpenSearch Serverless가 모든 컬렉션 타입의 진입 비용을 절반으로 인하](https://aws.amazon.com/about-aws/whats-new/2024/06/amazon-opensearch-serverless-entry-cost-half-collection-types/), [Amazon OpenSearch Service 요금](https://aws.amazon.com/opensearch-service/pricing/)

**둘째, 선택을 되돌리기 어렵다.** 벡터 스토어 교체는 전체 코퍼스 재임베딩·재인덱싱을 의미하고, Bedrock Knowledge Bases는 생성 후 벡터 스토어를 바꿀 수 없어 KB 자체를 다시 만들어야 한다. 잘못 고르면 마이그레이션 비용이 초기 선택 비용을 압도한다(마이그레이션 절차는 [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration) 참고).

**셋째, 엔타이틀먼트가 스토어 선택을 제약한다.** 테넌트별 데이터 격리, 문서 레벨 ACL, 쿼리 시점 필터링 요구가 있으면 메타데이터 필터의 표현력·크기 한도가 곧 서비스 선택 기준이 된다. 예컨대 S3 Vectors는 벡터당 filterable 메타데이터가 2 KB로 제한되어, 큰 allow-list를 메타데이터에 밀어넣는 설계가 애초에 불가능하다.

> 출처: [S3 Vectors — Limitations and restrictions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-limitations.html)

## 핵심 개념

### OpenSearch Serverless — 벡터 검색 컬렉션

Bedrock Knowledge Bases의 기본 선택지이자 AWS 문서가 가장 자주 안내하는 경로다. `VECTORSEARCH` 타입 컬렉션을 만들면 k-NN 인덱스를 OCU(OpenSearch Compute Unit) 기반으로 자동 스케일링해준다.

핵심 제약은 **최소 OCU 바닥**이다. 2024년 6월 인하 이후 최소 배포 단위는 0.5 OCU 단위가 되어, 프로덕션(가용영역 이중화) 기준 최소 2 OCU — 인덱싱 primary/standby 각 0.5 OCU + 검색 active replica 2×0.5 OCU — 이고, 이중화가 필요 없는 dev/test는 인덱싱 0.5 + 검색 0.5 = 1 OCU까지 내려간다. OCU 단가는 리전별로 다르며 us-east-1 기준 $0.24/OCU-시간이다. 즉 프로덕션 컬렉션 하나의 유휴 바닥이 약 $0.48/시간 ≈ **월 $350** 수준이다. 또한 벡터 검색 컬렉션은 다른 컬렉션 타입과 OCU를 공유하지 못한다.

> 출처: [OpenSearch Serverless 진입 비용 인하 발표](https://aws.amazon.com/about-aws/whats-new/2024/06/amazon-opensearch-serverless-entry-cost-half-collection-types/), [OpenSearch Service 요금 페이지](https://aws.amazon.com/opensearch-service/pricing/), [Working with vector search collections](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vector-search.html)

강점은 운영 부담이 가장 낮은 완전관리형이면서 증분 인덱싱(문서 추가/삭제 즉시 반영)과 풍부한 필터 DSL을 모두 갖췄다는 것. 하이브리드 검색(BM25 + 벡터)도 같은 컬렉션에서 처리된다([하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank) 참고).

### OpenSearch 관리형 도메인 — k-NN + disk-optimized 벡터 엔진

직접 인스턴스 타입·노드 수·샤딩을 통제하고 싶거나, 이미 OpenSearch 도메인을 운영 중이라면 관리형 클러스터의 k-NN이 선택지다. OpenSearch 2.17 이상 도메인에서는 **disk-optimized 벡터 엔진**을 켤 수 있다 — 압축(binary quantization)된 벡터를 메모리에 두고 full-precision 벡터는 디스크에 두어 rescoring으로 recall을 회복하는 방식으로, AWS는 in-memory 모드 대비 약 1/3 비용으로 운영 가능하다고 소개한다. 대신 지연이 한 자릿수 ms가 아닌 low hundreds-of-ms 급으로 늘어난다.

> 출처: [Disk-optimized vector engine 출시 발표](https://aws.amazon.com/about-aws/whats-new/2024/11/disk-optimized-vector-engine-amazon-opensearch-service), [OpenSearch — Disk-based vector search](https://docs.opensearch.org/latest/vector-search/optimizing-storage/disk-based-vector-search/), [Amazon OpenSearch Service — Vector search](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/vector-search.html)

Bedrock Knowledge Bases도 2025년 3월부터 OpenSearch 관리형 클러스터를 벡터 스토어로 지원한다.

> 출처: [Bedrock KB — OpenSearch Managed Cluster 지원 발표](https://aws.amazon.com/about-aws/whats-new/2025/03/amazon-bedrock-knowledge-bases-opensearch-cluster-vector-storage)

관리형 도메인의 트레이드오프는 명확하다: 인스턴스 선택·버전 업그레이드·샤드 리밸런싱이 전부 플랫폼 팀 몫이 된다. Serverless 대비 대규모(수억 벡터 이상)에서 단가를 낮출 수 있지만, 그 절감은 운영 인력으로 지불하는 것이다.

### Aurora PostgreSQL + pgvector — "이미 RDB가 있다면"

트랜잭션 데이터와 벡터를 같은 DB에 두고 조인·정합성을 원하면 pgvector가 자연스럽다. Aurora PostgreSQL은 15.4/14.9/13.12/12.16부터 pgvector 0.5.0(HNSW 인덱싱 포함)을 지원한다.

> 출처: [Aurora PostgreSQL pgvector v0.5.0 HNSW 지원 발표](https://aws.amazon.com/about-aws/whats-new/2023/10/amazon-aurora-postgresql-pgvector-v0-5-0-hnsw-indexing)

구조적 제약 두 가지를 알고 골라야 한다.

1. **차원 한도**: `vector` 타입에 대한 HNSW/IVFFlat 인덱스는 최대 2,000차원까지만 생성된다(PostgreSQL 8 KB 페이지에 인덱스 튜플이 들어가야 하는 구조적 한계). `halfvec`으로 4,000차원, binary quantization(`bit`)으로 64,000차원까지 확장할 수 있지만 정밀도 트레이드오프가 따른다. 3,072차원 임베딩 모델을 그대로 인덱싱하려다 여기서 막히는 사례가 흔하다([임베딩 모델 선택](/06-vector-search/embedding-model-choice) 참고).

   > 출처: [pgvector GitHub — 차원 한도 논의 #461](https://github.com/pgvector/pgvector/issues/461), [Scale pgvector with binary quantization on Aurora (AWS 블로그)](https://aws.amazon.com/blogs/database/scale-pgvector-with-binary-quantization-on-amazon-aurora-postgresql/)

2. **수직 확장 중심**: pgvector 인덱스 빌드·검색은 기본적으로 단일 writer 인스턴스의 메모리·CPU에 종속된다. Aurora read replica로 읽기는 분산되지만 인덱스 자체를 샤딩해주는 관리형 기능은 없다 — 수억 벡터급에서 애플리케이션 레벨 샤딩을 직접 설계해야 한다면 애초에 다른 서비스를 검토하라.

강점: Aurora Serverless v2와 결합하면 유휴 시 ACU가 최소치로 내려가 OpenSearch Serverless보다 소규모 워크로드 고정비가 크게 낮고, `WHERE tenant_id = ?` 같은 SQL 필터·RLS(Row Level Security)로 엔타이틀먼트를 DB 레이어에서 강제할 수 있다.

### S3 Vectors — 저비용 콜드/웜 벡터의 새 기본값

S3 Vectors는 실존하는 정식 서비스명이며, 2025년 7월 프리뷰를 거쳐 **2025년 12월 2일 GA**됐다. 벡터 전용 버킷(vector bucket)에 벡터 인덱스를 만들고 `PutVectors`/`QueryVectors` API로 쓰는, 스토리지 네이티브 벡터 스토어다.

GA 기준 공식 스펙:

| 항목 | 값 |
|---|---|
| 인덱스당 벡터 수 | 최대 20억 |
| 버킷당 인덱스 수 | 최대 10,000 |
| 벡터 차원 | 1–4,096 |
| 벡터당 메타데이터 | 총 40 KB, 그중 filterable 최대 2 KB |
| 인덱스당 non-filterable 메타데이터 키 | 최대 10개 |
| 쿼리 지연 | 드문(cold) 쿼리 1초 미만, 빈번한 쿼리 ~100 ms 이하 |

> 출처: [S3 Vectors GA 발표 (What's New, 2025-12)](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-s3-vectors-generally-available/), [S3 Vectors — Limitations and restrictions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-limitations.html), [Working with S3 Vectors and vector buckets](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors.html)

과금은 저장량 + 요청(PUT/query) 기반이라 프로비저닝 바닥이 없다. AWS는 제품 페이지에서 벡터 업로드·저장·쿼리의 총비용을 최대 90%까지 절감할 수 있다고 소개한다(비교 기준이 명시된 마케팅 수치이므로 자체 워크로드로 검증할 것).

> 출처: [Amazon S3 Vectors 제품 페이지](https://aws.amazon.com/s3/features/vectors/)

플랫폼 관점의 위치: **p50 한 자릿수 ms가 필요 없는 대다수 RAG에는 이제 S3 Vectors가 비용 기본값**이다. 반대로 대화형 에이전트의 타이트한 지연 예산(챕터 [지연 예산 설계](/02-performance/) 참고), 높은 QPS, 복잡한 하이브리드 검색이 필요하면 OpenSearch 계열로 간다. AWS 스스로도 "자주 쓰는 서브셋은 OpenSearch로 export하고 전체는 S3 Vectors에 두는" 계층화(tiering)를 안내한다.

### Amazon Kendra — 이제는 신규 도입 불가

Kendra는 벡터 스토어가 아니라 **관리형 엔터프라이즈 검색 서비스**다 — 커넥터(43종)로 소스를 크롤링하고, 하이브리드(키워드+시맨틱) 인덱스와 사용자 권한 필터링을 내장한 retriever를 제공한다. Kendra GenAI Index는 Bedrock Knowledge Bases의 managed retriever로 연결해 쓸 수 있었다.

> 출처: [Kendra GenAI Index 발표](https://aws.amazon.com/about-aws/whats-new/2024/12/genai-index-amazon-kendra/), [Bedrock KB with Kendra GenAI index](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-kendra-genai-index-create.html)

그러나 **Kendra는 2026년 6월 30일부로 maintenance mode에 들어갔고, 2026년 7월 30일부터 신규 고객을 받지 않는다.** 기존 고객은 계속 지원되지만 신규 기능 개발은 없으며, AWS는 신규 검색 워크로드를 Bedrock의 managed knowledge base로 구현할 것을 권고한다. 신규 플랫폼 설계에서 Kendra는 선택지에서 제외하고, 기존 Kendra 자산이 있다면 마이그레이션 계획을 세워야 한다.

> 출처: [Amazon Kendra availability change (공식 문서)](https://docs.aws.amazon.com/kendra/latest/dg/kendra-availability-change.html), [AWS Service Availability Updates](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-service-availability/)

### Bedrock Knowledge Bases — Quick create vs 자체 벡터 스토어

Bedrock Knowledge Bases는 벡터 스토어가 아니라 **관리형 RAG 파이프라인**(수집→청킹→임베딩→저장→검색)이고, 그 밑에 벡터 스토어를 꽂는 구조다. 선택지는 두 갈래다.

- **Quick create(관리형 프로비저닝)**: KB 생성 시 AWS가 벡터 스토어를 대신 만들어준다 — OpenSearch Serverless, Aurora PostgreSQL Serverless, S3 Vectors 중 선택. 빠르지만 만들어진 리소스의 수명주기·비용이 KB 콘솔 바깥(각 서비스 계정)에 잡히므로, IaC로 통제하는 플랫폼에서는 리소스가 "야생"으로 남기 쉽다.
- **자체 벡터 스토어 지정(bring your own)**: 미리 만든 OpenSearch Serverless/관리형 클러스터, Aurora, Neptune Analytics, 또는 서드파티(Pinecone, MongoDB Atlas, Redis Enterprise Cloud)를 연결한다. 인덱스 스키마·필드 매핑을 직접 맞춰야 하지만 수명주기가 플랫폼 통제 하에 있다.

> 출처: [Prerequisites for using a vector store you created](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html), [Create a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-create.html)

주의할 결합 제약: **데이터소스가 Confluence·SharePoint·Salesforce면 벡터 스토어는 OpenSearch Serverless만 지원**된다. 커넥터 선택이 스토어 선택을 강제하는 경우다.

> 출처: [Create a knowledge base — 데이터소스별 제약](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-create.html)

KB의 메타데이터 필터 스펙(연산자, 10 KB 사이드카 한도, managed KB에서의 차이)과 실시간 ACL 검증은 이 책의 정본 챕터인 [RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping)에서 다룬다 — 여기서 중복 기술하지 않는다.

### 선택 축 6개

결정 표에 들어가기 전에, 어떤 축으로 재는지 명시한다.

1. **규모** — 벡터 수. 수백만까지는 어느 서비스든 되고, 수억~수십억이면 S3 Vectors(인덱스당 20억) 또는 OpenSearch 샤딩 설계가 필요하다.
2. **지연 요구** — 대화형 에이전트의 retrieval 단계 예산. 한 자릿수~수십 ms가 필요하면 in-memory OpenSearch, ~100 ms급이면 disk-optimized 또는 S3 Vectors(warm), 콜드 1초를 견딜 수 있으면 S3 Vectors 단독.
3. **비용 구조** — 상시 프로비저닝(OpenSearch, Aurora provisioned, Kendra) vs 사용량 과금(S3 Vectors, Aurora Serverless v2 부분적). 유휴 시간이 길수록 후자가 유리.
4. **신선도** — 증분 인덱싱 요구. 전 서비스가 증분 upsert를 지원하지만, 삭제 반영·재인덱싱 비용의 차이는 [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration)에서 다룬다.
5. **엔타이틀먼트** — 필터 표현력(OpenSearch DSL > SQL/RLS > KB RetrievalFilter > S3 Vectors 2 KB 필터)과 문서 레벨 ACL 요구.
6. **운영 부담** — 완전관리형(S3 Vectors, OpenSearch Serverless, KB Quick create) vs 관리형이지만 튜닝 주체가 나(OpenSearch 도메인, Aurora) vs 자체 호스팅(이 책에서는 다루지 않음).

### 테넌트 격리: 인덱스 분리 vs 메타데이터 필터

공용 RAG 코퍼스를 제공하는 플랫폼의 반복 질문이다. 판단 기준:

**메타데이터 필터(단일 인덱스 + `tenant_id` 필터)가 맞는 경우** — 테넌트 수가 많고(수백~수천), 테넌트당 문서가 적고, 격리 요구가 "논리적 분리" 수준일 때. 인덱스 수·컬렉션 수에 비례하는 고정비(특히 OpenSearch Serverless의 컬렉션당 OCU 바닥)를 피할 수 있다. 대신 필터 누락 한 번이 곧 크로스테넌트 유출이므로, 필터 주입을 애플리케이션 코드가 아닌 공용 retrieval 게이트웨이에서 강제해야 한다(안티패턴 절 참고).

**인덱스/컬렉션 분리가 맞는 경우** — 테넌트가 소수의 대형 고객이고, 계약상 물리적 분리·별도 암호화 키·별도 삭제 증빙이 요구될 때. 또는 테넌트별 임베딩 모델/차원이 다를 때(같은 인덱스에 섞을 수 없다). S3 Vectors는 버킷당 인덱스 10,000개까지 지원하고 인덱스별 고정비가 없어, **"테넌트당 인덱스" 패턴의 고정비 문제를 사실상 제거**한다 — 격리 강도와 비용의 오랜 트레이드오프가 여기서 상당히 완화됐다.

> 출처: [S3 Vectors GA 발표 — 버킷당 10,000 인덱스](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-s3-vectors-generally-available/)

::: warning 미정착 영역
"메타데이터 필터만으로 규제 산업의 테넌트 격리 요건을 충족할 수 있는가"는 업계 합의가 없다. SOC 2/ISMS 감사에서 논리적 분리가 수용되는 사례가 다수지만, 금융·의료의 일부 계약은 인덱스·키 레벨 분리를 명시적으로 요구한다. 계약서와 감사 기준을 먼저 확인하고 아키텍처를 정하라 — 반대 순서로 가면 재인덱싱이 기다린다.
:::

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| Bedrock KB 기반 표준 RAG, 지연 여유(수백 ms OK), 비용 민감 | **S3 Vectors** (KB Quick create 또는 직접) | 프로비저닝 바닥 없음, 인덱스당 20억 벡터, KB 네이티브 통합 | 콜드 쿼리 1초 미만급, filterable 메타데이터 2 KB, 하이브리드 검색은 별도 구성 |
| 대화형 에이전트, retrieval p99 수십 ms, 하이브리드 검색 필요 | **OpenSearch Serverless** | 완전관리형 + BM25/벡터 하이브리드 + 풍부한 필터 DSL | 컬렉션당 최소 OCU 고정비(프로덕션 약 $0.48/h, us-east-1), 컬렉션 난립 시 비용 폭발 |
| 수억 벡터 이상 + 비용 단가를 직접 최적화할 운영 역량 보유 | **OpenSearch 관리형 + disk-optimized** | in-memory 대비 약 1/3 비용(AWS 공식), 인스턴스·샤딩 직접 통제 | 지연 low hundreds-of-ms, 클러스터 운영 부담 전가 |
| 벡터가 트랜잭션 데이터와 강결합(조인, 정합성, RLS) | **Aurora PostgreSQL + pgvector** | SQL 필터/RLS로 엔타이틀먼트, 기존 Aurora 운영 자산 재사용 | `vector` 타입 인덱스 2,000차원 한도, 단일 writer 수직 확장 중심 |
| Confluence/SharePoint/Salesforce 커넥터로 KB 구성 | **OpenSearch Serverless** (강제) | 해당 데이터소스는 KB에서 이 스토어만 지원 | 선택권 없음 — 커넥터가 스토어를 강제 |
| 엔터프라이즈 검색(사람이 쓰는 검색 UI) 신규 구축 | ~~Kendra~~ → **Bedrock managed KB** | Kendra는 2026-07-30부터 신규 고객 불가(maintenance mode) | managed KB의 커넥터·필터 제약 확인 필요([RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping)) |
| 소수 대형 테넌트, 계약상 물리적 격리 요구 | 테넌트당 인덱스(S3 Vectors) 또는 테넌트당 컬렉션 | 인덱스 분리 + 키 분리로 격리 증빙 | OpenSearch Serverless로 하면 테넌트 수 × OCU 바닥 |
| 다수 소형 테넌트, 논리적 격리로 충분 | 단일 인덱스 + `tenant_id` 메타데이터 필터 | 고정비 통제, 운영 단순 | 필터 강제 게이트웨이 필수 — 누락 = 크로스테넌트 유출 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| PoC 몇 개 후 월 청구서 급증 | OpenSearch Serverless 컬렉션당 최소 OCU 고정비 누적 | Cost Explorer에서 `Amazon OpenSearch Service` 사용 타입 중 `ServerlessIndexingOCU`/`ServerlessSearchOCU` 항목별 컬렉션 태그 확인 | dev/test 컬렉션은 1 OCU(이중화 해제) 구성, PoC는 S3 Vectors로 이전, 컬렉션 발급을 플랫폼 승인 플로우로 통제 |
| pgvector `CREATE INDEX` 실패: "column cannot have more than 2000 dimensions" | `vector` 타입 HNSW/IVFFlat의 2,000차원 한도 | 임베딩 모델 출력 차원 확인 (예: 3,072차원 모델) | 모델의 축소 차원 옵션 사용, `halfvec`(4,000차원) 전환, 또는 Matryoshka 임베딩 절단 — [임베딩 모델 선택](/06-vector-search/embedding-model-choice) |
| S3 Vectors 쿼리가 간헐적으로 1초 가까이 걸림 | 콜드(드문) 쿼리 경로 — 빈번 쿼리만 ~100 ms 이하 | 쿼리 빈도별 지연 분포 측정(계측 절 참고), 트래픽 희소 인덱스 식별 | 지연 SLO가 타이트한 인덱스는 OpenSearch로 티어링, 또는 SLO 자체를 재협상 |
| `PutVectors` 실패 또는 필터링이 안 되는 메타데이터 | filterable 메타데이터 2 KB / 키 50개 / non-filterable 키 10개 한도 초과, non-filterable 키는 인덱스 생성 후 변경 불가 | [Limitations 문서](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-limitations.html) 대비 페이로드 크기 점검 | 필터 키를 최소 집합으로 재설계(엔타이틀먼트 ID는 짧은 코드로), 본문성 메타데이터는 non-filterable로 선언 |
| KB 검색 결과에 다른 테넌트 문서 혼입 | retrieval 호출 경로 중 하나가 `tenant_id` 필터를 누락 | 전 호출 경로에서 `RetrievalFilter` 유무를 로깅·감사, 카나리 테넌트로 크로스 조회 테스트 | 필터 주입을 공용 retrieval 게이트웨이로 일원화 — [RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping) |
| KB 벡터 스토어를 바꾸려니 KB 재생성 필요 | KB는 생성 후 벡터 스토어 변경 불가 | [knowledge-base-setup 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html) | 신규 KB 병행 구축 후 트래픽 전환 — [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration) |
| Kendra 기반 신규 프로젝트가 계정 개설 단계에서 막힘 | Kendra maintenance mode(2026-06-30), 신규 고객 차단(2026-07-30) | [공식 availability change 문서](https://docs.aws.amazon.com/kendra/latest/dg/kendra-availability-change.html) | Bedrock managed KB로 설계 변경, 기존 Kendra 워크로드는 마이그레이션 로드맵 수립 |

## 안티패턴

- ❌ **팀마다 OpenSearch Serverless 컬렉션 셀프서비스 발급** → ✅ 컬렉션은 고정비 자산으로 취급하고 발급을 승인제로. 소규모·실험 워크로드는 S3 Vectors 또는 공용 컬렉션 내 인덱스 분리로 수용.
- ❌ **"serverless니까 안 쓰면 과금 안 되겠지"라는 가정으로 비용 모델링** → ✅ OpenSearch Serverless는 최소 OCU 상시 과금, Aurora Serverless v2도 최소 ACU가 있다. 서비스별 유휴 바닥을 [요금 페이지](https://aws.amazon.com/opensearch-service/pricing/)에서 확인하고 견적에 명시.
- ❌ **에이전트/클라이언트가 스스로 `tenant_id` 필터를 구성하게 두기** → ✅ 필터는 검증된 세션 컨텍스트로부터 서버 측 게이트웨이가 생성·주입한다. 클라이언트 구성 필터는 confused deputy다 — [RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping).
- ❌ **Quick create로 만든 벡터 스토어를 IaC 밖에 방치** → ✅ Quick create는 데모까지만. 프로덕션은 벡터 스토어를 IaC로 선생성하고 KB에 연결해 수명주기·태깅·비용 귀속을 플랫폼이 소유.
- ❌ **벤치마크 없이 "S3 Vectors는 싸니까 느리다/OpenSearch는 비싸니까 빠르다"로 결정** → ✅ 자기 코퍼스·자기 쿼리 분포로 recall@k와 지연 분포를 측정해 결정([검색 품질 평가](/06-vector-search/retrieval-evaluation)). AWS의 "최대 90% 절감"도 비교 기준이 있는 마케팅 수치다.
- ❌ **임베딩 모델을 먼저 고르고 스토어 제약을 나중에 확인** → ✅ 차원 한도(pgvector 2,000 / S3 Vectors 4,096)와 모델 출력 차원을 함께 결정. 스토어 교체보다 모델 차원 옵션 조정이 싸다.

## 계측 (SLI)

플랫폼이 공용 벡터 스토어를 제공한다면 최소 다음을 SLI로 노출한다.

- **Retrieval 지연 분포**: `QueryVectors`/`Retrieve`/k-NN 쿼리의 p50/p95/p99를 **인덱스(테넌트)별**로. S3 Vectors는 콜드/웜 경로가 갈리므로 평균이 아닌 분포로 봐야 하고, 쿼리 빈도를 라벨로 함께 기록해 콜드 비율을 추적한다.
- **검색 품질**: recall@k 또는 downstream 답변 품질의 주기적 오프라인 평가([검색 품질 평가](/06-vector-search/retrieval-evaluation)). 스토어·인덱스 파라미터 변경 전후 비교의 기준선이 된다.
- **비용 SLI**: 테넌트당 월 벡터 스토어 비용(OCU-시간, S3 Vectors 저장/요청, ACU). 태깅과 Cost Explorer 사용 타입 분해로 산출 — 격리 모델(인덱스 분리 vs 필터) 재검토의 근거 데이터다.
- **필터 커버리지**: 전체 retrieval 호출 중 엔타이틀먼트 필터가 포함된 비율. **100%가 아니면 인시던트**로 취급한다.
- **인덱스 신선도 랙**: 소스 변경 → 검색 반영까지의 시간. KB sync job 기준으로는 마지막 성공 sync 경과 시간([인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration)).
- **OCU 사용률**(OpenSearch Serverless): CloudWatch `SearchOCU`/`IndexingOCU` 대비 실제 트래픽 — 바닥만 깔고 노는 컬렉션 식별.

## 체크리스트

- [ ] 워크로드의 벡터 수(현재/2년 후), 쿼리 QPS, 지연 예산(p99)을 숫자로 적었다 — "많이/빨리"가 아니라.
- [ ] 후보 서비스별 **유휴 비용 바닥**을 요금 페이지에서 확인해 견적에 넣었다(OpenSearch Serverless 최소 OCU, Aurora 최소 ACU, S3 Vectors는 저장/요청).
- [ ] 임베딩 모델 출력 차원이 스토어의 인덱스 차원 한도(pgvector `vector` 2,000 / S3 Vectors 4,096) 안에 있다.
- [ ] 엔타이틀먼트 요구(테넌트 격리 수준, 문서 ACL, 필터 크기)를 스토어의 필터 스펙과 대조했다 — 특히 S3 Vectors filterable 2 KB.
- [ ] 테넌트 격리 모델(인덱스 분리 vs 메타데이터 필터)을 계약·감사 요건 문서와 함께 결정하고 ADR로 남겼다.
- [ ] Bedrock KB를 쓴다면: Quick create가 아닌 IaC 선생성 스토어를 연결했고, 데이터소스(커넥터)가 스토어 선택을 강제하는지 확인했다.
- [ ] 신규 설계에 Kendra가 없다(2026-07 이후 신규 불가). 기존 Kendra가 있다면 마이그레이션 로드맵이 있다.
- [ ] 자기 코퍼스로 recall@k + 지연 분포 벤치마크를 돌린 뒤 결정했다 — 요금표만 보고 결정하지 않았다.
- [ ] retrieval 필터 커버리지 100%를 감시하는 알람이 있다.
- [ ] 스토어 교체(재인덱싱) 시나리오의 러프한 비용·절차를 결정 시점에 한 번 계산해봤다.

## 참고

- [Amazon S3 Vectors — Working with S3 Vectors and vector buckets](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors.html) / [Limitations and restrictions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-limitations.html) / [GA 발표 (2025-12)](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-s3-vectors-generally-available/)
- [Amazon OpenSearch Serverless — Working with vector search collections](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vector-search.html) / [진입 비용 인하 발표 (2024-06)](https://aws.amazon.com/about-aws/whats-new/2024/06/amazon-opensearch-serverless-entry-cost-half-collection-types/) / [요금](https://aws.amazon.com/opensearch-service/pricing/)
- [Amazon OpenSearch Service — Vector search](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/vector-search.html) / [Disk-optimized vector engine 발표 (2024-11)](https://aws.amazon.com/about-aws/whats-new/2024/11/disk-optimized-vector-engine-amazon-opensearch-service)
- [Aurora PostgreSQL pgvector 0.5.0 HNSW 지원 발표](https://aws.amazon.com/about-aws/whats-new/2023/10/amazon-aurora-postgresql-pgvector-v0-5-0-hnsw-indexing) / [pgvector GitHub](https://github.com/pgvector/pgvector)
- [Amazon Bedrock Knowledge Bases — Prerequisites for using a vector store you created](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html) / [Create a knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-create.html)
- [Amazon Kendra availability change](https://docs.aws.amazon.com/kendra/latest/dg/kendra-availability-change.html) / [AWS Service Availability Updates (2026-06)](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-service-availability/)
- 이 책의 관련 챕터: [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization), [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration), [RAG 엔타이틀먼트 스코핑](/09-authorization/rag-entitlement-scoping)
