---
title: 인덱스 신선도와 마이그레이션
description: 원본 변경·삭제를 벡터 인덱스에 전파하는 동기화 파이프라인 설계와, 임베딩 모델 교체 시 blue/green 재인덱싱 전략을 다룬다.
outline: [2, 3]
---

# 인덱스 신선도와 마이그레이션

::: tip 이 장에서 얻는 것
- 원본 문서 변경이 검색 결과에 반영되기까지의 경로를 이벤트 기반 vs 배치 동기화로 설계하는 기준
- Bedrock Knowledge Bases의 ingestion job / 데이터소스 sync가 실제로 어떻게 동작하는지 (증분 처리, 삭제 전파, `dataDeletionPolicy`)
- 삭제 전파가 규제 리스크(잊힐 권리)와 직결되는 이유와 검증 방법
- 임베딩 모델 교체 시 blue/green 인덱스 마이그레이션 절차와 듀얼 라이트 기간의 비용 구조
- 인덱스 staleness, 삭제 전파 지연, ingestion 실패율을 측정하는 SLI 정의
:::

## 왜 문제가 되는가

RAG 파이프라인의 벡터 인덱스는 원본 데이터의 **사본(derived data)** 이다. 원본이 바뀌는 순간부터 인덱스가 갱신되는 순간까지, 에이전트는 낡은 정보를 근거로 답변한다. 일반 검색 서비스에서 stale 결과는 사용자 경험 문제에 그치지만, 에이전트 환경에서는 성격이 다르다. 에이전트는 검색 결과를 **행동의 근거**로 삼는다 — 폐기된 환불 정책으로 환불을 승인하고, 지난 분기 가격표로 견적을 내고, 이미 수정된 runbook의 옛 절차로 장애 대응을 안내한다. 검색 자체는 정상 동작하므로 어떤 에러도 발생하지 않고, 문제는 사후 감사에서야 드러난다.

반대 방향도 있다. 원본에서 **삭제된** 문서가 인덱스에 남으면, 접근이 회수되었어야 할 정보가 검색 가능한 상태로 유출된다. 개인정보 삭제 요청(잊힐 권리)을 원본 DB에서만 처리하고 벡터 스토어에 남겨둔 경우, 청크와 임베딩 메타데이터에 원문 조각이 그대로 남아 있으므로 삭제 의무를 이행하지 못한 것이 된다 — GDPR Art. 17의 right to erasure는 파생 저장소를 예외로 두지 않는다([GDPR Art. 17](https://gdpr-info.eu/art-17-gdpr/)). 국내 개인정보보호법 맥락은 [12장 한국 규제 환경](/12-security-korea/)에서, 에이전트 메모리 쪽의 동일 문제는 [메모리 보안과 프라이버시](/07-memory/memory-security-privacy)에서 다룬다.

마지막으로, 인덱스 전체를 갈아엎어야 하는 사건이 주기적으로 온다. **임베딩 모델 교체**다. 서로 다른 모델이 만든 벡터는 같은 차원이라도 다른 벡터 공간에 놓이므로 상호 비교가 무의미하다 — 쿼리를 새 모델로 임베딩하고 구 모델 벡터와 유사도를 계산하면 결과는 사실상 노이즈다(왜 그런지는 [임베딩 기초](/06-vector-search/embeddings-fundamentals) 참고). 즉 모델 교체는 부분 갱신이 불가능하고 **전량 재인덱싱**을 강제한다. 이 작업을 무중단으로, 품질 검증을 거쳐, 예측 가능한 비용으로 수행하는 것이 이 장의 두 번째 주제다.

한 가지 경계선을 먼저 긋는다. IVFFlat처럼 데이터 분포에 민감한 ANN 인덱스는 데이터가 그대로여도 구조 유지보수 차원의 주기적 rebuild가 필요한데([ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization) 참고), 그것은 **인덱스 구조**의 문제다. 이 장은 **데이터와 모델이 변했을 때**의 대응, 즉 동기화와 마이그레이션을 다룬다. 둘을 같은 운영 항목으로 묶으면 "rebuild 했으니 신선하다"는 착각이 생긴다 — rebuild는 이미 인덱스에 있는 벡터를 재배치할 뿐, 원본과의 격차를 좁히지 않는다.

## 핵심 개념

### 신선도(freshness)의 정의

인덱스 신선도는 단일 시점 속성이 아니라 **지연 분포**다. 이 장에서는 다음과 같이 정의한다.

- **staleness lag**: 원본 문서의 변경 시각(`t_source`)부터 해당 변경이 검색 결과에 반영되는 시각(`t_searchable`)까지의 시간. 인덱스 쓰기 완료 시각이 아니라 **검색 가능 시각**이 기준이다 — 벡터 스토어에 따라 쓰기 후 검색 반영까지 추가 지연이 있다. 예컨대 Bedrock Knowledge Bases는 Aurora 외의 벡터 스토어에서 ingestion 완료 후에도 "임베딩이 쿼리 가능해지기까지 몇 분이 걸릴 수 있다"고 명시한다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html)).
- **deletion propagation lag**: 원본 삭제 시각부터 해당 청크가 검색 결과에서 사라지는 시각까지의 시간. staleness lag의 부분집합이지만 규제 리스크 때문에 별도 SLI로 관리한다.

### 동기화 파이프라인: 이벤트 기반 vs 배치

**이벤트 기반(event-driven)**: 원본 변경 이벤트(S3 `ObjectCreated`/`ObjectRemoved`, DB CDC, CMS webhook)를 받아 해당 문서만 즉시 재인덱싱한다. staleness lag이 초~분 단위로 내려가지만, 파이프라인 복잡도가 올라간다 — 이벤트 유실 대비(DLQ, 재시도), 순서 역전(같은 문서의 update 후 delete가 뒤바뀌는 경우), 폭주 시 임베딩 API rate limit 흡수(버퍼 큐) 를 모두 설계해야 한다. 이벤트 기반만으로는 유실을 100% 막을 수 없으므로, 실무에서는 **이벤트 기반 + 저빈도 배치 reconciliation**의 하이브리드가 표준이다.

**배치(batch)**: 주기적으로 원본과 인덱스를 비교해 차이만 반영(증분) 하거나 전체를 다시 싱크한다. 구현이 단순하고 유실에 강하지만 staleness lag의 하한이 배치 주기다. 배치 주기는 SLO에서 역산한다 — "변경이 1시간 내 반영되어야 한다"면 배치 주기는 그보다 짧아야 하고, 배치 1회의 소요 시간(문서 스캔 + 임베딩 + 인덱싱)이 주기를 초과하지 않는지 확인해야 한다.

### Bedrock Knowledge Bases의 sync 동작

Bedrock Knowledge Bases를 쓰는 경우 위 파이프라인의 상당 부분이 관리형으로 제공되지만, 정확한 동작을 알아야 SLO를 걸 수 있다. [공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html) 기준:

- **sync는 증분(incremental)이다.** 마지막 sync 이후 추가·수정·삭제된 문서만 처리한다. 변경 없는 문서는 skip, 내용·메타데이터가 바뀐 문서는 재파싱→재청킹→재임베딩→재인덱싱, 삭제된 문서는 벡터 스토어에서 제거된다.
- **sync는 자동으로 돌지 않는다.** 콘솔의 Sync 버튼 또는 `StartIngestionJob` API로 명시적으로 트리거해야 하며, 문서가 안내하는 방법은 이 둘뿐이다. 즉 "주기적 sync"나 "S3 업로드 즉시 반영"은 사용자가 EventBridge Scheduler(주기)나 S3 이벤트 → Lambda → `StartIngestionJob`(이벤트 기반)으로 직접 구성해야 한다. 진행 상태는 `GetIngestionJob`/`ListIngestionJobs`로 추적하고, 응답의 `statistics`로 문서별 성공/실패를 확인한다.
- **메타데이터 전용 최적화**: `metadata.json`만 바뀐 경우(콘텐츠 파일 불변, CSV 아님, 커스텀 transformation Lambda 미사용) 기존 임베딩을 재사용하고 메타데이터만 병합해 임베딩 모델 호출을 생략한다. 단 CSV는 `documentStructureConfiguration` 변경 여부를 판단할 수 없어 메타데이터 변경만으로도 항상 재수집된다.
- **`dataDeletionPolicy`**: 데이터소스 생성 시 `DELETE` 또는 `RETAIN`을 지정한다 — 데이터소스가 삭제될 때 벡터 스토어의 임베딩을 함께 지울지 남길지를 결정한다([공식 문서](https://docs.aws.amazon.com/bedrock/latest/userguide/data-source-connectors.html)). 규제 대상 데이터라면 `RETAIN`은 삭제 의무 위반의 씨앗이다 — 명시적으로 `DELETE`를 지정하고, 정책과 무관하게 삭제 후 검증(아래 SLI)을 수행하라.

### 삭제 전파는 "tombstone까지" 확인해야 한다

삭제는 갱신보다 어렵다. 갱신 누락은 낡은 정보를 주지만, 삭제 누락은 있어서는 안 되는 정보를 준다. 설계 시 확인할 지점:

1. **원본 삭제 이벤트가 파이프라인에 도달하는가.** S3라면 `ObjectRemoved` 이벤트, DB라면 CDC의 delete 레코드. "존재하는 것만 스캔"하는 배치 파이프라인은 삭제를 감지하지 못한다 — 인덱스 측 문서 ID 목록과 원본 목록의 차집합을 계산하는 reconciliation이 필요하다.
2. **청크 단위 삭제가 되는가.** 문서 1개가 청크 N개로 쪼개져 있으므로, 문서 ID → 청크 ID 매핑(메타데이터의 source document ID)이 없으면 부분 삭제 잔재가 남는다.
3. **캐시 계층까지 전파되는가.** 검색 결과 캐시, 시맨틱 캐시([4장](/04-caching/)) 에 삭제된 문서의 내용이 남아 있으면 인덱스를 지워도 노출된다.

### 임베딩 모델 마이그레이션: blue/green 인덱스

모델 교체 시 전량 재인덱싱이 강제되는 이유는 위에서 말한 벡터 공간 비호환 때문이다. "기존 벡터는 구 모델, 신규 문서만 새 모델"로 섞는 순간 인덱스는 망가진다. 표준 절차는 blue/green이다:

1. **병렬 구축**: 새 인덱스(green)를 새 모델로 전량 임베딩해 별도 구축한다. 기존 인덱스(blue)는 그대로 서비스한다.
2. **듀얼 라이트**: 구축 기간 동안 들어오는 신규/변경 문서는 blue와 green **양쪽**에 쓴다(각자의 모델로 각각 임베딩). 이걸 생략하면 green이 완성되는 시점에 이미 구축 시작 이후의 변경분만큼 stale하다. 듀얼 라이트 기간에는 임베딩 비용과 쓰기 비용이 2배가 된다는 점을 예산에 반영하라.
3. **검증**: green에 대해 retrieval 평가셋을 돌려 blue 대비 품질을 확인한다(평가 방법은 [검색 품질 평가](/06-vector-search/retrieval-evaluation)). 새 모델이 항상 낫다는 보장은 없다 — 도메인에 따라 회귀할 수 있다.
4. **트래픽 전환**: 검색 경로를 green으로 원자적으로 바꾼다. OpenSearch 계열이면 `_aliases` API의 add+remove를 한 요청으로 묶어 alias를 원자적으로 옮기는 것이 정석이다([OpenSearch index alias 문서](https://docs.opensearch.org/latest/im-plugin/index-alias/)). pgvector라면 뷰 교체나 애플리케이션 설정 플래그로 동일한 효과를 낸다. **쿼리 임베딩 모델도 같은 순간에 함께 전환**되어야 한다 — 인덱스만 바꾸고 쿼리 인코더를 안 바꾸면 그 자체가 공간 불일치다.
5. **롤백 창 유지 후 폐기**: blue를 즉시 지우지 말고 롤백 가능 기간(듀얼 라이트를 유지하는 기간) 동안 보존한 뒤 폐기한다. 폐기 전까지 스토리지 비용도 2배다.

Bedrock Knowledge Bases에서는 인덱스 alias 같은 세밀한 전환 수단이 없으므로, 새 임베딩 모델로 **새 Knowledge Base를 만들어** ingestion을 완료한 뒤 애플리케이션이 참조하는 `knowledgeBaseId`를 전환하는 방식이 blue/green에 해당한다(KB의 임베딩 모델은 생성 시 고정된다).

### 재인덱싱 비용 추정 프레임

전량 재인덱싱 비용은 다음 프레임으로 추정한다:

```
임베딩 비용 ≈ 문서 수 × 문서당 평균 청크 수 × 청크당 평균 토큰 수 × 토큰당 임베딩 단가
총 비용 = 임베딩 비용 + 벡터 스토어 쓰기/인덱싱 비용 + (듀얼 라이트 기간 × 일일 변경분 × 2)
소요 시간 ≈ 총 토큰 수 ÷ (임베딩 API 처리량 상한)   ← rate limit이 병목인 경우가 많다
```

구체 단가는 모델·시점에 따라 달라지므로 여기 박아두지 않는다 — 모델별 단가 비교와 선택 기준은 [임베딩 모델 선택](/06-vector-search/embedding-model-choice)을 보라. 중요한 것은 **소요 시간 항**이다. 비용은 감내해도 임베딩 API의 rate limit 때문에 전량 재인덱싱이 며칠씩 걸리는 경우가 흔하고, 그 기간만큼 듀얼 라이트 비용과 롤백 창이 늘어난다.

## 결정 표

| 상황 | 선택 | 이유 | 트레이드오프 |
|---|---|---|---|
| 원본 변경 빈도 낮음(일 수십 건), 반영 SLO 시간 단위 | 배치 증분 sync (EventBridge Scheduler → `StartIngestionJob` 등) | 구현 단순, 유실에 강함 | staleness lag 하한 = 배치 주기 |
| 반영 SLO 분 단위, 변경 이벤트 소스 존재(S3 이벤트, CDC) | 이벤트 기반 + 저빈도 배치 reconciliation | 즉시 반영 + 유실 보정 | 파이프라인 복잡도(DLQ, 순서 역전, rate limit 버퍼) |
| 개인정보/규제 대상 문서 포함 | 삭제 전파를 별도 SLI로 계측 + 삭제 후 검증 쿼리, Bedrock KB라면 `dataDeletionPolicy: DELETE` | 삭제 누락은 UX 문제가 아니라 컴플라이언스 위반 | 검증 파이프라인 추가 비용 |
| 임베딩 모델 교체 | blue/green 신규 인덱스 + 듀얼 라이트 + 평가 후 원자적 전환 | 벡터 공간 비호환으로 in-place 갱신 불가 | 구축 기간 동안 임베딩·쓰기·스토리지 2배 비용 |
| 청킹 전략만 변경(모델 동일) | 역시 전량 재인덱싱 (blue/green 권장) | 청크 경계가 바뀌면 기존 청크 ID 체계와 호환 불가 | 모델 교체와 동일한 비용 구조, 단 쿼리 인코더 전환은 불필요 |
| 인덱스 recall 저하(데이터·모델 불변) | 이 장 아님 — [ANN 인덱스 rebuild](/06-vector-search/ann-indexes-quantization) | 구조 유지보수 문제 | — |

## 실패 모드

| 증상 | 근본 원인 | 확인 방법 | 해결 |
|---|---|---|---|
| 에이전트가 폐기된 정책/구버전 문서를 인용 | sync 미트리거 또는 배치 주기가 SLO보다 김 | 해당 문서의 원본 수정 시각 vs 인덱스 메타데이터의 ingestion 시각 비교 | 이벤트 기반 트리거 추가 또는 배치 주기 단축 |
| 원본에서 지운 문서가 검색에 계속 등장 | 배치가 "존재하는 파일만" 스캔해 삭제 미감지, 또는 캐시 잔존 | 삭제된 문서 ID로 인덱스 직접 조회 + 캐시 계층 조회 | reconciliation(차집합) 배치 추가, 캐시 invalidation을 삭제 파이프라인에 연결 |
| 데이터소스 삭제 후에도 임베딩이 벡터 스토어에 잔존 (Bedrock KB) | `dataDeletionPolicy: RETAIN` (또는 미지정 상태의 정책 확인 누락) | `GetDataSource`로 정책 확인, 벡터 스토어 직접 조회 | `DELETE` 정책 명시, 잔존분 수동 삭제 |
| sync 후에도 몇 분간 새 문서가 검색 안 됨 | 벡터 스토어의 쓰기→검색 가시성 지연 (Bedrock KB는 Aurora 외 스토어에서 문서화된 동작) | ingestion COMPLETE 시각 vs 검색 히트 시각 측정 | SLO에 가시성 지연 포함, "COMPLETE = 검색 가능" 가정 제거 |
| 일부 문서만 반영되고 나머지 누락, 에러는 안 보임 | ingestion job 부분 실패 (형식 미지원, 파일 크기 초과 등) | `GetIngestionJob` 응답의 `statistics`와 sync history의 warning 확인 | 실패 문서 목록 알람화, 지원 형식/크기 사전 검증 |
| 모델 교체 후 검색 품질 급락 | 인덱스는 새 모델, 쿼리 인코더는 구 모델(또는 그 반대) — 공간 불일치 | 쿼리 임베딩 호출 로그의 모델 ID vs 인덱스 구축 모델 ID 대조 | 인덱스와 쿼리 인코더를 단일 설정으로 묶어 원자적으로 전환 |
| green 전환 직후 최신 문서만 검색 안 됨 | 듀얼 라이트 없이 병렬 구축 — 구축 시작 이후 변경분이 green에 없음 | green의 최신 ingestion 시각 확인 | 전환 전 듀얼 라이트 기간 운영, 또는 전환 직전 catch-up sync |
| 이벤트 기반 파이프라인에서 문서가 "삭제됐다가 다시 나타남" | update/delete 이벤트 순서 역전 | 이벤트 타임스탬프와 인덱스 반영 순서 대조 | 문서별 버전/타임스탬프 비교 후 최신만 적용 (last-write-wins) |

## 안티패턴

- ❌ 원본 삭제를 원본 DB에서만 처리하고 벡터 스토어는 "어차피 임베딩이니까"라고 방치 → ✅ 청크 메타데이터에는 원문이 들어 있다. 삭제 요청 처리 파이프라인에 벡터 스토어·캐시를 명시적 대상으로 포함하고 삭제 후 검증 쿼리로 확인.
- ❌ 임베딩 모델을 바꾸면서 신규 문서부터 새 모델로 "점진 전환" → ✅ 한 인덱스 안에 두 모델의 벡터를 섞는 것은 점진이 아니라 오염이다. blue/green 전량 재인덱싱.
- ❌ ingestion job이 COMPLETE면 성공이라고 간주 → ✅ `statistics`의 문서별 실패 수와 warning을 확인. 부분 실패는 status로 드러나지 않는다.
- ❌ "IVFFlat rebuild를 주 1회 돌리니 인덱스는 신선하다" → ✅ rebuild는 기존 벡터의 재배치일 뿐이다. 신선도는 원본 대비 lag으로 별도 계측.
- ❌ blue/green 전환 시 인덱스만 바꾸고 쿼리 임베딩 모델 설정은 나중에 배포 → ✅ 인덱스 참조와 쿼리 인코더를 하나의 설정 단위로 묶어 동시 전환.
- ❌ 전량 재인덱싱 비용을 임베딩 API 단가만으로 추정 → ✅ 듀얼 라이트 기간의 2배 쓰기, 롤백 창 동안의 2배 스토리지, rate limit로 인한 소요 시간까지 프레임에 포함.

## 계측 (SLI)

파이프라인이 자체 보고하는 지표만 믿지 말고, **end-to-end 프로브**로 측정한다.

- **index staleness lag** (p50/p95/max): 원본 변경 시각 → 검색 반영 시각. 측정법: 카나리 문서(내용에 타임스탬프를 넣은 합성 문서)를 주기적으로 원본에 쓰고, 검색 API로 폴링해 반영 시각을 기록. 인덱스 쓰기 시각이 아닌 **검색 히트 시각**을 취해야 가시성 지연까지 잡힌다.
- **deletion propagation lag** (p95/max): 카나리 문서 삭제 → 검색에서 소실 확인까지. staleness와 분리해 별도 SLO(규제 대상이면 더 엄격하게)로 관리. 캐시 계층을 통과하는 실제 검색 경로로 측정할 것.
- **ingestion 실패율**: (실패 문서 수) ÷ (처리 대상 문서 수), job 단위가 아니라 **문서 단위**. Bedrock KB라면 `GetIngestionJob`의 `statistics`에서 추출. job 실패율만 보면 부분 실패가 가려진다.
- **reconciliation drift**: 주기적 차집합 배치가 발견한 (원본에 없는데 인덱스에 있는 문서 수) + (원본에 있는데 인덱스에 없는 문서 수). 0이 정상 상태이며, 지속적으로 0이 아니면 이벤트 파이프라인에 유실이 있다는 신호.
- **마이그레이션 중 추가 지표**: green 인덱스의 catch-up lag(blue 대비 문서 수 격차), 듀얼 라이트 실패율(한쪽만 성공한 쓰기 건수 — 이 값이 곧 전환 후 불일치가 된다).

알람 기준은 SLO에서 역산한다: staleness p95가 SLO의 80%를 넘으면 warning, 초과하면 page. deletion lag은 max 기준으로 건다 — 분포 꼬리 하나가 곧 컴플라이언스 사건이다.

## 체크리스트

- [ ] 문서 변경의 반영 SLO(시간)와 삭제 전파 SLO를 수치로 정의했다 — "가능한 빨리"는 SLO가 아니다.
- [ ] 동기화 방식(이벤트/배치/하이브리드)을 SLO에서 역산해 선택했고, 배치라면 1회 소요 시간 < 주기임을 확인했다.
- [ ] 삭제 감지 경로가 존재한다 — 이벤트 기반이면 delete 이벤트 구독, 배치면 차집합 reconciliation.
- [ ] 청크 메타데이터에 원본 문서 ID가 있어 문서 단위 삭제가 청크 전체에 전파된다.
- [ ] 검색 결과/시맨틱 캐시의 invalidation이 삭제 파이프라인에 연결되어 있다.
- [ ] (Bedrock KB) sync 트리거(스케줄 또는 이벤트)가 구성되어 있다 — KB는 자동으로 sync하지 않는다.
- [ ] (Bedrock KB) `dataDeletionPolicy`를 명시적으로 지정했고, 규제 대상 데이터면 `DELETE`다.
- [ ] ingestion job의 문서 단위 실패(`statistics`)를 알람화했다.
- [ ] 카나리 문서 기반 staleness/deletion lag 프로브가 실제 검색 경로로 돌고 있다.
- [ ] 임베딩 모델 교체 runbook이 있다: blue/green 구축 → 듀얼 라이트 → retrieval 평가 → 인덱스+쿼리 인코더 동시 전환 → 롤백 창 → 폐기.
- [ ] 재인덱싱 비용·소요 시간을 프레임(문서 수 × 청크 × 토큰 × 단가, rate limit 병목)으로 사전 추정했고 듀얼 라이트 2배 비용을 포함했다.
- [ ] IVFFlat 류 인덱스 rebuild 일정은 이 체크리스트와 **별개 항목**으로 관리한다.

## 참고

- Amazon Bedrock — [Sync your data with your knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html) (증분 sync, 삭제 처리, 메타데이터 최적화, `StartIngestionJob`/`GetIngestionJob`)
- Amazon Bedrock — [Connect a data source to your knowledge base](https://docs.aws.amazon.com/bedrock/latest/userguide/data-source-connectors.html) (`dataDeletionPolicy: DELETE | RETAIN`)
- OpenSearch — [Index aliases](https://docs.opensearch.org/latest/im-plugin/index-alias/) (`_aliases` API를 통한 원자적 alias 전환)
- GDPR — [Art. 17 Right to erasure](https://gdpr-info.eu/art-17-gdpr/)
- 관련 장: [임베딩 기초](/06-vector-search/embeddings-fundamentals) · [임베딩 모델 선택](/06-vector-search/embedding-model-choice) · [ANN 인덱스와 양자화](/06-vector-search/ann-indexes-quantization) · [검색 품질 평가](/06-vector-search/retrieval-evaluation) · [메모리 보안과 프라이버시](/07-memory/memory-security-privacy) · [12장 한국 규제 환경](/12-security-korea/)
