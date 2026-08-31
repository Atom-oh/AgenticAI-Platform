---
title: 임베딩 모델 선택
description: 관리형 vs 자체 서빙, 차원 수, MRL, 한국어 특성, 재인덱싱 비용까지 — 임베딩 모델 선택을 플랫폼 관점의 되돌리기 어려운 결정으로 다룬다.
outline: [2, 3]
---

# 임베딩 모델 선택

::: tip 이 장에서 얻는 것
- 임베딩 모델 선택이 왜 "나중에 바꾸면 되는" 결정이 아니라 전체 재인덱싱을 수반하는 준-비가역 결정인지 이해한다
- Titan V2 / Cohere Embed v3 / OpenAI text-embedding-3 / BGE-M3 / multilingual-E5 / Voyage-3 등 주요 모델의 검증된 스펙을 한 표에서 비교한다
- Matryoshka Representation Learning(MRL)로 차원을 줄일 때 실제로 잃는 성능과 아끼는 저장 비용을 공식 수치로 가늠한다
- 한국어(교착어) 환경에서 MTEB/BEIR 점수를 그대로 믿으면 안 되는 이유와 자체 평가셋 구축의 최소 요건을 얻는다
:::

## 왜 문제가 되는가

임베딩 모델 선택은 벡터 검색 스택에서 가장 되돌리기 어려운 결정이다. 이유는 단순하다 — **서로 다른 모델(또는 같은 모델의 다른 버전)이 만든 벡터는 같은 공간에 있지 않다.** 쿼리 벡터와 문서 벡터는 반드시 같은 모델·같은 버전·같은 차원으로 생성돼야 하므로, 모델을 바꾸는 순간 코퍼스 전체를 다시 임베딩해야 한다. 문서 1천만 건 규모라면 재임베딩 비용(토큰 과금 또는 GPU 시간) + 재인덱싱 시간 + 이중 운영 기간의 저장 비용이 한꺼번에 발생한다. 이 마이그레이션을 어떻게 무중단으로 수행하는지는 [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration)에서 다루고, 이 장은 그 비용을 애초에 줄이는 선택 기준에 집중한다.

두 번째 문제는 평가다. 대부분의 팀이 MTEB 리더보드 순위로 모델을 고르지만, MTEB/BEIR는 특정 데이터셋·특정 언어 분포에서의 결과일 뿐이다. **한국어 도메인 문서에서의 실제 검색 품질은 리더보드 순위와 다르게 나올 수 있으며, 자체 데이터로 재측정하지 않은 선택은 선택이 아니라 도박이다.** 이 원칙은 [이 책의 한계](/00-intro/index#이-책의-한계)에서 선언한 것과 동일하다.

세 번째는 운영 모델이다. Bedrock에서 API 한 줄로 쓰는 관리형 모델(Titan V2, Cohere Embed)과 직접 서빙해야 하는 오픈소스 모델(BGE-M3, multilingual-E5 등)은 품질 이전에 운영 부담의 차원이 다르다. 플랫폼 팀이라면 "가장 좋은 모델"이 아니라 "우리 운영 역량과 마이그레이션 예산 안에서 가장 좋은 모델"을 골라야 한다.

## 핵심 개념

### 선택 축 4가지

1. **검색 품질** — 단, 자체 도메인·자체 언어에서의 품질. 리더보드 점수는 후보 압축용(shortlist)으로만 쓴다.
2. **차원 수와 저장 비용** — 벡터 저장 비용과 ANN 인덱스 메모리는 차원 수에 선형으로 비례한다. 3072차원은 1024차원 대비 저장·메모리·유사도 연산이 3배다. MRL 지원 여부가 이 축의 유연성을 결정한다.
3. **컨텍스트 길이** — 임베딩 모델의 최대 입력 토큰이 청킹 전략의 상한을 정한다. 512 토큰 모델(multilingual-E5)과 32K 토큰 모델(Voyage-3, Granite R2)은 [청킹 전략](/06-vector-search/chunking-strategies)의 자유도가 다르다.
4. **운영 모델** — 관리형 API(Bedrock, OpenAI, Voyage) vs 자체 서빙(BGE-M3, E5 등 오픈소스). 데이터 주권·비용 구조·레이턴시 통제권이 갈린다.

### 모델 지형 (2026년 기준, 각 셀 출처 확인)

| 모델 | 차원 | 컨텍스트 | 다국어 | MRL | 제공 형태 |
|---|---|---|---|---|---|
| [Amazon Titan Text Embeddings V2](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html) | 1024(기본)/512/256 | 8,192 토큰 | 100+ 언어 사전학습 | O (256/512/1024) | Bedrock 관리형 |
| [Cohere Embed v3 (multilingual)](https://docs.cohere.com/docs/amazon-bedrock) | 1024 | 512 토큰 | 100+ 언어 | X (v3 기준) | Bedrock 관리형 (`cohere.embed-multilingual-v3`) |
| [OpenAI text-embedding-3-small](https://openai.com/index/new-embedding-models-and-api-updates/) | 1536(기본, 축소 가능) | 8,191 토큰 | 다국어 | O (`dimensions` 파라미터) | OpenAI API |
| [OpenAI text-embedding-3-large](https://openai.com/index/new-embedding-models-and-api-updates/) | 3072(기본, 축소 가능) | 8,191 토큰 | 다국어 | O (`dimensions` 파라미터) | OpenAI API |
| [BGE-M3](https://huggingface.co/BAAI/bge-m3) | dense 1024 + sparse + multi-vector | 8,192 토큰 | 100+ 언어 | X | 오픈소스 (자체 서빙) |
| [multilingual-e5-large](https://huggingface.co/intfloat/multilingual-e5-large) | 1024 | 512 토큰 | 100 언어 (XLM-RoBERTa 기반) | X | 오픈소스 (자체 서빙) |
| [Voyage-3](https://blog.voyageai.com/2024/09/18/voyage-3/) | 1024 | 32K 토큰 | 다국어 | X (voyage-3 기준) | Voyage API / AWS Marketplace |
| [Snowflake arctic-embed-l-v2.0](https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0) | 1024 | 8,192 토큰 | 다국어 | O (256 지원점) | 오픈소스 (Apache 2.0) |
| [jina-embeddings-v3](https://arxiv.org/abs/2409.10173) | 1024(기본, 32까지 축소) | 8,192 토큰 | 다국어 + task LoRA | O | 오픈소스 / API |
| [Granite Embedding Multilingual R2 (311M)](https://huggingface.co/blog/ibm-granite/granite-embedding-multilingual-r2) | 768(기본)/512/384/256/128 | 32,768 토큰 | 200+ 언어 | O | 오픈소스 (Apache 2.0) |

표 읽을 때 주의점:

- **가격**: [OpenAI 공식 발표](https://openai.com/index/new-embedding-models-and-api-updates/) 기준 text-embedding-3-small은 $0.00002/1K 토큰(= **$0.02/1M 토큰**), text-embedding-3-large는 $0.00013/1K 토큰(= $0.13/1M 토큰)이다. Bedrock 모델 가격은 리전·시점에 따라 다르므로 [Bedrock pricing 페이지](https://aws.amazon.com/bedrock/pricing/)에서 배포 전 재확인해야 한다.
- **Cohere Embed의 강점은 Rerank와의 짝**이다. Cohere는 Embed와 [Rerank 3.5](https://docs.cohere.com/docs/amazon-bedrock)를 모두 Bedrock에서 제공하며, 100+ 언어 리랭킹을 지원한다. 1단계 recall(Embed) + 2단계 precision(Rerank)을 같은 벤더·같은 관리형 플랫폼에서 구성할 수 있다는 것이 운영상 이점이다 — 상세는 [하이브리드 검색과 리랭킹](/06-vector-search/hybrid-search-rerank).
- **BGE-M3의 차별점은 하이브리드 네이티브**다. [M3-Embedding 논문](https://arxiv.org/abs/2402.03216)에 따르면 dense retrieval, sparse(lexical) retrieval, multi-vector retrieval을 단일 모델이 동시에 수행한다. 별도 BM25 파이프라인 없이 dense+sparse 하이브리드를 한 번의 인퍼런스로 얻을 수 있어, 자체 서빙을 감수할 수 있는 팀에게는 아키텍처를 단순화한다.
- **Titan V2는 Bedrock Knowledge Bases의 기본 선택지**다. [Bedrock Knowledge Bases에서 임베딩 모델로 지원](https://aws.amazon.com/about-aws/whats-new/2024/06/amazon-titan-text-embeddings-v2-bedrock-knowledge-bases)되며, KB를 쓰면 임베딩 호출·청킹·인덱싱이 관리형으로 처리된다.

### MRL: 차원을 줄일 때 실제로 잃는 것

Matryoshka Representation Learning은 학습 시 최종 차원뿐 아니라 앞쪽 부분 차원(prefix)에도 손실 함수를 적용해, 벡터의 앞 N차원만 잘라 재정규화해도 유효한 임베딩이 되도록 만드는 기법이다. 공식 발표에서 확인되는 수치들:

- **Titan V2**: 1024→512 축소 시 약 99%, 1024→256 축소 시 약 97%의 정확도를 유지하며, 256차원 사용 시 저장 비용 75% 절감 ([AWS News Blog](https://aws.amazon.com/blogs/aws/amazon-titan-text-v2-now-available-in-amazon-bedrock-optimized-for-improving-rag/)).
- **Granite Embedding Multilingual R2 (311M)**: MTEB Multilingual Retrieval 기준 768차원 65.2 → 256차원 64.7(저장 3배 절감에 0.5점 하락) → 128차원 63.7(저장 6배 절감, full 대비 97%+ 유지) ([IBM 공식 발표](https://huggingface.co/blog/ibm-granite/granite-embedding-multilingual-r2)).
- **EmbeddingGemma (308M)**: MTEB Multilingual 평균 768차원 61.15 → 512차원 60.71 → 256차원 59.68 → 128차원 58.23 ([Google 모델 카드](https://ai.google.dev/gemma/docs/embeddinggemma/model_card)). 128차원(저장 약 83% 절감)에서도 full 대비 약 95%를 유지한다.
- **arctic-embed-l-v2.0**: MRL 256차원 truncation으로 벡터 크기를 3–4배 줄이면서 품질 저하 3% 미만 ([Hugging Face 모델 카드](https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0)).

패턴은 일관적이다: **절반~1/4 축소는 거의 공짜, 1/6~1/8 축소부터 1.5~3점대 하락이 나타난다.** 단 이 수치들은 모두 벤치마크 평균이다 — 특정 도메인·특정 언어에서는 하락 폭이 더 클 수 있으므로, 축소 차원 결정도 자체 평가셋으로 검증해야 한다. MRL의 플랫폼 관점 가치는 별도에 있다: full 차원으로 저장해 두면 **재임베딩 없이** 축소 차원으로 다운그레이드하며 저장 비용·레이턴시를 조정할 수 있다. 양자화와의 결합은 [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization) 참고.

### 한국어: 리더보드가 말해주지 않는 것

한국어는 교착어다. "학교에 갔다"는 형태소로 학교(체언) + 에(조사) + 가-(어간) + -았-(선어말어미) + -다(어말어미)로 분해된다. 영어 중심으로 학습된 서브워드 토크나이저(BPE 등)는 이 형태소 경계를 존중하지 않고 통계적 빈도로 쪼개기 때문에, 같은 의미의 문장도 조사·어미 변화("갔다/갔습니다/갔어요")에 따라 전혀 다른 토큰 시퀀스가 된다. 결과적으로:

- **토큰 효율이 나쁘다.** 같은 정보량의 한국어 문서가 영어보다 더 많은 토큰을 소비한다 — 임베딩 과금(토큰 단위)과 유효 컨텍스트 길이 양쪽에 영향을 준다.
- **어휘 변형에 대한 강건성이 모델별로 크게 다르다.** 다국어 벤치마크 평균이 높아도 한국어 경어 변화·복합 명사 분해에서 약한 모델이 있다.
- **도메인 용어(사내 약어, 한영 혼용 표기)는 어떤 공개 벤치마크에도 없다.**

**따라서 MTEB/BEIR 점수는 후보를 3~4개로 압축하는 방향성 지표로만 쓰고, 최종 선택은 반드시 자체 도메인·한국어 평가셋에서의 재측정으로 해야 한다.** 이것은 이 책 전체의 원칙([이 책의 한계](/00-intro/index#이-책의-한계))이기도 하다. 최소 요건: 실제 사용자 쿼리(또는 예상 쿼리) 100~500개 + 정답 문서 매핑, recall@k / nDCG@k 측정 — 구축 방법은 [검색 평가](/06-vector-search/retrieval-evaluation)에서 다룬다. MTEB에는 한국어 태스크가 일부 포함되어 있고 [MMTEB](https://arxiv.org/abs/2502.13595)로 언어 커버리지가 확장됐지만, 여전히 "당신의 문서, 당신의 쿼리"는 아니다.

### 관리형 vs 자체 서빙

| | Bedrock 관리형 (Titan V2, Cohere) | 자체 서빙 (BGE-M3, E5, arctic 등) |
|---|---|---|
| 운영 부담 | API 호출만. 스케일링·패치 없음 | GPU 서빙 인프라, 오토스케일링, 모델 버전 고정 직접 관리 |
| 비용 구조 | 토큰 종량제 — 대량 배치 재인덱싱 시 비용 급증 | 인프라 고정비 — 볼륨 크면 단가 하락 |
| 데이터 경로 | 리전 내 관리형 엔드포인트 | VPC 내부에서 종결 가능 |
| 모델 수명 | 벤더의 deprecation 일정에 종속 | 가중치를 소유 — 영구 재현 가능 |
| IAM/거버넌스 | Bedrock IAM·CloudTrail에 통합 | 별도 구축 |
| 품질 상한 | 벤더 라인업 내로 제한 | 최신 오픈소스·파인튜닝 즉시 적용 |

플랫폼 관점에서 가장 저평가되는 항목은 **모델 수명**이다. 관리형 모델이 deprecated되면 재인덱싱이 강제된다. 자체 서빙은 가중치를 소유하므로 마이그레이션 시점을 스스로 정한다 — 대신 그 자유의 대가로 서빙 스택 전체를 운영해야 한다. 절충안으로 SageMaker/EKS에 오픈소스 모델을 올리되 Bedrock Knowledge Bases 대신 자체 파이프라인을 쓰는 구성이 있으며, 벡터 스토어 선택과 엮이는 부분은 [AWS 벡터 스토어](/06-vector-search/aws-vector-stores) 참고.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| Bedrock 중심 스택, 운영 인력 최소, 한국어 포함 다국어 | Titan Text Embeddings V2 (1024, 필요시 MRL 512) | KB 통합·IAM 일원화·MRL로 저장 비용 조절 | 벤더 deprecation 일정에 종속, 품질 상한 |
| 관리형 유지 + 리랭킹까지 품질 극대화 | Cohere Embed multilingual v3 + Rerank 3.5 (Bedrock) | 100+ 언어 Embed/Rerank를 같은 관리형 플랫폼에서 짝으로 구성 | 컨텍스트 512 토큰 → 청크가 짧아짐, MRL 없음 |
| dense+sparse 하이브리드 필요, GPU 운영 가능 | BGE-M3 자체 서빙 | 단일 인퍼런스로 dense+sparse+multi-vector, 8192 토큰, 오픈소스 | 서빙 인프라 운영 비용, 스파스 지원 벡터 스토어 필요 |
| 데이터가 VPC를 못 벗어남 + 라이선스 민감 | arctic-embed-l-v2.0 또는 Granite R2 (Apache 2.0) | 상용 안전 라이선스, MRL, 가중치 소유로 수명 통제 | 자체 평가·서빙·버전 관리 전부 자체 부담 |
| 초장문 문서, 청킹 최소화 | Voyage-3 (32K) 또는 Granite R2 (32K) | 긴 컨텍스트로 청킹 자유도 확보 | Voyage는 외부 API 의존, Granite는 자체 서빙 |
| 저장 비용이 지배적 제약 (수억 벡터) | MRL 지원 모델을 full로 임베딩, 축소 차원으로 서빙 | 재임베딩 없이 차원 다운그레이드 가능 | 축소 폭이 크면(≥6x) 1.5~3점대 품질 하락 |
| 아직 평가셋이 없음 | **모델 확정 금지** — 평가셋부터 구축 | 자체 재측정 없는 선택은 재인덱싱 비용을 걸고 하는 도박 | 초기 일정 1~2주 지연 |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 검색 결과가 전부 무관 (recall≈0) | 쿼리와 문서를 다른 모델/버전/차원으로 임베딩 | 쿼리·인제스천 파이프라인의 모델 ID·차원 설정 diff | 모델 ID+버전+차원을 인덱스 메타데이터에 기록하고 쿼리 시 검증 |
| E5 계열에서 벤치마크 대비 품질 급락 | `query:`/`passage:` 프리픽스 누락 | 임베딩 호출 직전 실제 입력 문자열 로깅 | [모델 카드](https://huggingface.co/intfloat/multilingual-e5-large)의 프리픽스 규약 준수 |
| 긴 문서의 후반부 내용이 검색 안 됨 | 모델 최대 토큰 초과분 무음 잘림(silent truncation) | 청크 토큰 길이 분포 vs 모델 한도 비교 | 청크 상한을 모델 한도 이하로 강제, 초과 시 알람 |
| 영어 문서는 잘 되는데 한국어만 품질 저하 | 영어 중심 모델 선택 + 한국어 미평가 | 언어별 recall@k 분리 측정 | 다국어 모델로 교체 + 한국어 평가셋 상시 운용 |
| MRL 축소 후 특정 쿼리 유형만 성능 급락 | 벤치마크 평균에 가려진 도메인별 하락 | 축소 전/후 자체 평가셋 A/B | 축소 폭 완화(예: 128→256), full 차원 원본 보존 |
| 모델 업그레이드 후 신규 문서만 검색됨/구 문서만 검색됨 | 부분 재인덱싱 — 혼합 벡터 공간 | 인덱스 내 모델 버전 태그 집계 | 전체 재인덱싱 + blue/green 전환 ([마이그레이션](/06-vector-search/index-freshness-migration)) |
| 재인덱싱 비용 폭탄 | 종량제 API로 수천만 청크 배치 재임베딩 | 청크 수 × 평균 토큰 × 단가 사전 계산 | 배치 할인/자체 서빙 검토, MRL로 재임베딩 없는 차원 조정 |
| 임베딩 레이턴시 스파이크로 인제스천 적체 | 관리형 API 스로틀링(RPM/TPM 쿼터) | 429/ThrottlingException 비율 모니터링 | 쿼터 상향 신청, 배치 API, 백프레셔 큐 |

## 안티패턴

- ❌ MTEB 1위 모델을 그대로 채택 → ✅ 리더보드로 3~4개 후보를 압축하고, 자체 한국어·도메인 평가셋에서 recall@k/nDCG로 최종 결정
- ❌ 모델 버전을 "latest"로 참조 → ✅ 모델 ID·버전·차원을 고정(pin)하고 인덱스 메타데이터에 기록 — 임베딩 모델의 버전 변경은 코드 배포가 아니라 데이터 마이그레이션이다
- ❌ 저장 비용 절감을 위해 처음부터 128차원으로 임베딩 → ✅ MRL 모델은 full 차원으로 임베딩·보관하고 서빙 차원만 축소 — 되돌릴 수 있는 결정으로 유지
- ❌ 영어 데모로 검증하고 한국어 프로덕션에 투입 → ✅ 언어별 평가를 분리하고 한국어 지표를 릴리스 게이트에 포함
- ❌ 임베딩 모델 교체를 "설정값 변경"으로 계획 → ✅ 전체 재인덱싱 프로젝트(비용 산정, 이중 운영, 컷오버)로 계획 — [인덱스 신선도와 마이그레이션](/06-vector-search/index-freshness-migration)
- ❌ 관리형/자체 서빙을 품질만으로 비교 → ✅ 재인덱싱 단가, deprecation 리스크, 데이터 경로, 운영 인력까지 TCO로 비교

## 계측 (SLI)

임베딩 모델은 배포하고 끝이 아니라 상시 계측 대상이다:

- **검색 품질**: 자체 평가셋 기준 recall@k, nDCG@k — 언어별(최소 한국어/영어 분리)·도메인별 분해. 모델/차원 변경 시 회귀 게이트로 사용
- **임베딩 파이프라인**: 임베딩 API p50/p99 레이턴시, 오류율, 스로틀링(429) 비율, 인제스천 큐 적체 깊이
- **비용**: 임베딩 토큰 사용량(신규 인제스천 vs 재인덱싱 분리 태깅), 벡터 저장 GB, 1천 쿼리당 비용
- **정합성**: 인덱스 내 모델 버전·차원 태그 분포 — 단일 값이 아니면 혼합 공간 사고가 진행 중이라는 뜻
- **잘림(truncation) 비율**: 모델 토큰 한도를 초과해 잘린 청크의 비율 — 0이 아니면 청킹 설정과 모델 선택이 어긋난 것

평가셋 구축과 온라인 품질 신호(클릭/인용 기반)는 [검색 평가](/06-vector-search/retrieval-evaluation) 참고.

## 체크리스트

- [ ] 자체 도메인·한국어 평가셋(쿼리 100개 이상 + 정답 매핑)을 모델 확정 **전에** 구축했다
- [ ] 후보 모델을 MTEB/BEIR로 압축한 뒤, 최종 선택은 자체 평가셋 recall@k/nDCG로 했다
- [ ] 모델 ID·버전·차원·정규화 여부를 인덱스 메타데이터에 기록하고 쿼리 경로에서 검증한다
- [ ] 모델별 입력 규약(E5 프리픽스, Cohere `input_type`, 정규화 옵션)을 인제스천·쿼리 양쪽에 동일 적용했다
- [ ] 청크 최대 토큰이 모델 컨텍스트 한도 이하임을 파이프라인에서 강제한다
- [ ] 전체 재인덱싱 비용(청크 수 × 토큰 × 단가 또는 GPU 시간)을 산정해 두었고, 무중단 마이그레이션 경로가 있다
- [ ] MRL 모델이라면 full 차원 원본을 보관하고, 축소 차원은 자체 평가셋으로 검증 후 적용했다
- [ ] 관리형 모델의 deprecation 공지를 구독하고 있으며, EOL 시 재인덱싱 리드타임을 계획에 반영했다
- [ ] 언어별 검색 품질 SLI가 대시보드에 분리되어 있다
- [ ] 임베딩 API 쿼터(RPM/TPM)가 최대 인제스천 처리량 + 재인덱싱 시나리오를 감당하는지 확인했다

## 참고

- [Amazon Titan Text Embeddings V2 — Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html) / [AWS News Blog: Titan V2 발표 (MRL 정확도·저장 절감 수치)](https://aws.amazon.com/blogs/aws/amazon-titan-text-v2-now-available-in-amazon-bedrock-optimized-for-improving-rag/)
- [Cohere Models on Amazon Bedrock (Embed v3, Rerank 3.5)](https://docs.cohere.com/docs/amazon-bedrock)
- [OpenAI: New embedding models and API updates (text-embedding-3, 가격, dimensions 파라미터)](https://openai.com/index/new-embedding-models-and-api-updates/)
- [BGE-M3 모델 카드 (Hugging Face)](https://huggingface.co/BAAI/bge-m3) / [M3-Embedding 논문 (arXiv:2402.03216)](https://arxiv.org/abs/2402.03216)
- [multilingual-e5-large 모델 카드](https://huggingface.co/intfloat/multilingual-e5-large)
- [Voyage-3 발표 블로그 (32K 컨텍스트)](https://blog.voyageai.com/2024/09/18/voyage-3/)
- [Snowflake arctic-embed-l-v2.0 모델 카드](https://huggingface.co/Snowflake/snowflake-arctic-embed-l-v2.0)
- [jina-embeddings-v3 논문 (arXiv:2409.10173)](https://arxiv.org/abs/2409.10173)
- [IBM Granite Embedding Multilingual R2 발표 (MRL 차원별 MTEB 수치)](https://huggingface.co/blog/ibm-granite/granite-embedding-multilingual-r2)
- [EmbeddingGemma 모델 카드 (차원별 MTEB Multilingual 수치)](https://ai.google.dev/gemma/docs/embeddinggemma/model_card)
- [MMTEB: Massive Multilingual Text Embedding Benchmark (arXiv:2502.13595)](https://arxiv.org/abs/2502.13595)
