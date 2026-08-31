---
title: 임베딩과 벡터 검색 개관
description: 검색 품질이 에이전트 정확도의 상한을 정한다 — 임베딩 선택부터 인덱스 운영, RAG를 쓰지 말아야 할 때까지.
outline: [2, 3]
---

# 임베딩과 벡터 검색 개관

::: tip 이 파트가 다루는 것
6대 통증점 중 **2번(정확도)**의 상류를 담당한다. 검색이 나쁘면 아무리 좋은 모델도 그 위에서 그럴듯한 오답을 만든다.
:::

이 파트는 임베딩 모델 선택 → 청킹 → 인덱스 → 하이브리드·리랭킹 → 평가 → 운영(신선도·마이그레이션)의 전체 수명주기를 다루고, 마지막으로 "RAG를 쓰지 말아야 할 때"(CAG, SQL, GraphRAG)를 다룬다. 모든 벤치마크 수치는 특정 데이터셋 결과다 — **자체 도메인·한국어 데이터로 재측정이 필수**다.

## 챕터 요약

| 챕터 | 한 줄 요약 |
|---|---|
| [임베딩 기초](/06-vector-search/embeddings-fundamentals) | 모델 종속성, 임계값 이식 불가, 비대칭 검색 — 플랫폼 관점의 함정 4가지 |
| [임베딩 모델 선택](/06-vector-search/embedding-model-choice) | 모델 지형과 MRL, 한국어 재측정 필수 |
| [청킹 전략](/06-vector-search/chunking-strategies) | Contextual Retrieval(실패율 최대 67% 감소), late chunking, Bedrock KB 청킹 옵션 |
| [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization) | HNSW/IVF-PQ/DiskANN 결정 규칙, 양자화 트레이드오프 |
| [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank) | RRF, cross-encoder 2단계 파이프라인, 순차 적용 순서 |
| [검색 평가](/06-vector-search/retrieval-evaluation) | recall@k·nDCG·MRR, golden set, recall@20 > 3% 전환 임계치의 정본 |
| [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration) | staleness, 삭제 전파, blue/green 재인덱싱 |
| [GraphRAG·agentic RAG, 그리고 쓰지 말아야 할 때](/06-vector-search/graphrag-agentic-when-not) | CAG·SQL·GraphRAG — RAG가 답이 아닌 경우들 |
| [AWS 벡터 스토어](/06-vector-search/aws-vector-stores) | OpenSearch·pgvector·S3 Vectors·Bedrock KB 선택 축 |

## 결정 요약

- 임베딩 모델을 무엇으로 하고, 교체 시 재인덱싱 비용을 감당할 수 있는가
- 청킹 전략과 contextual retrieval 적용 여부
- recall@20 실패율이 3%를 넘으면 어떤 순서로 개선할 것인가(contextual retrieval → 하이브리드 → 리랭커)
- 애초에 RAG가 맞는가 — 코퍼스가 작으면 CAG, 정형 데이터면 SQL
- 테넌트 격리를 인덱스 분리로 할 것인가 메타데이터 필터로 할 것인가

## 관련 다른 파트

- [Part 3 정확도와 평가](/03-accuracy-eval/) — 검색 실패가 정확도 문제로 전이되는 메커니즘
- [Part 4 프롬프트 캐싱](/04-caching/) — contextual retrieval 비용 절감과 CAG의 기반
- [Part 9 세밀 권한 제어](/09-authorization/rag-entitlement-scoping) — KB 메타데이터 필터 기반 엔타이틀먼트의 정본
