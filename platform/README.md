# Agentic AI Platform — 은행 데모 (SPEC.md 구현)

루트 `SPEC.md`의 구현. 종합 Agentic AI Platform의 정본 서피스로, 기존
`demo/builder-harness`(한울증권 컨트롤룸)·`demo/uiux-studio`(디자이너 스튜디오)를 잇는다.

## §15 확정 사항 (2026-09-02)

| 항목 | 결정 |
|---|---|
| 기존 자산 | 하나의 종합 플랫폼으로 재편 — 기존 배포는 무시 가능 (사용자 확정) |
| 에이전트 SDK | Strands + AgentCore 실사용 (Gateway·Registry 기존 자산 재사용) |
| 그래프 DB | Neptune 상시 기동 (예산 승인) + LocalGraphStore 개발용 이중화 |
| 벡터 저장소 | OpenSearch Serverless |
| 도메인 | 여신(전세대출) — SPEC 제안 채택 |
| 계정·리전 | 180294183052 / ap-northeast-2 (Bedrock 액세스 확인) · Registry us-east-1 |
| 시연 시간 | 15분 |

## Phase 1 — 온톨로지와 데이터 (완료)

```bash
cd platform
python3 seed/generate.py          # 합성데이터 생성 (시드 고정 20260902)
python3 -m pytest tests/ -q       # §4.3 커버리지 + §8 식별자 + Semantic Layer 검증
python3 cli.py impact REG-LN-001  # §4.3 4-hop 순회 — Phase 1 완료 조건
python3 cli.py metric "지난달 사용액"
```

- `schema/ontology.cypher` — 노드 12종·관계 18종 스키마 (openCypher)
- `seed/generate.py` — 노드 3,317 / 엣지 8,520. 가상 "아톰은행" 여신 도메인.
  히어로 규정 3건(REG-LN-001 전세 담보, REG-LN-014 LTV, REG-CS-003 설명의무)에
  §4.3 커버리지를 보장. 개인 식별자는 생성 시점부터 토큰(CUST-/ACCT-).
- `graph/store.py` — `GraphStore` 인터페이스 + `LocalGraphStore`(인메모리).
  `GRAPH_BACKEND=neptune|local` 전환 (Neptune 구현은 Phase 2).
  `impact_of_regulation()`이 §4.3 순회를 수행하고 **순회 경로 엣지**를 함께 반환한다(시각화용).
- `semantic/` — 지표 정의 YAML + 로더. Text-to-SQL은 이 계층만 참조한다.
- `tests/test_coverage.py` — 볼륨·커버리지·버전체인·식별자·시맨틱 5종.

## Phase 2 — GraphRAG 엔진과 S1 화면 (완료)

라이브: **https://d15n7n9ypt87h8.cloudfront.net/** (demo@atomai.click — 초대 전용, 가입 없음)

```bash
cd platform && bash deploy.sh   # api-dist 조립 → CDK 배포 → 프론트 빌드/업로드
```

- `engine/` — GraphRAG(의도분해→seed 선택(신뢰도)→4-hop 순회→생성→근거검증) +
  Vector RAG(BM25+dense RRF+Cohere Rerank, 약화 없음)
- `api/ws_handler.py` — WebSocket 스트리밍. $connect에서 Cognito access token 필수
  (무토큰/무효 토큰 연결 거부 — e2e 확인). 두 엔진 병렬 실행, 토큰 단위 push.
- `web/` — React 18+Vite+TS+Tailwind, Pretendard, 다크. S1 좌우 비교 + seed 신뢰도 +
  counts 칩 + 근거검증 배지 + Cytoscape 순회 경로 시각화 + 시나리오 프리셋 버튼 +
  그래프 백엔드 상시 표시(local/neptune).
- `infra/` — CDK TS: S3+CloudFront(OAC, 유일한 퍼블릭 웹 진입점), WebSocket API
  (스로틀 20rps), Lambda(py3.12, 예약 동시성 10), DDB 연결 테이블(TTL).
- e2e 실측: 첫 토큰 3.1s(SPEC 5s 내), Vector 60토큰/Graph 187토큰 스트리밍,
  counts = 상품12·화면39·부서7·문서7, 무토큰 연결 거부.

정리(teardown): `cd infra && npx cdk destroy` (Neptune은 Phase 3에서 추가 예정 — 상시 과금 주의)

## 단일 앱 재편 + Phase 3 일부 (2026-09-02)

"하나의 페이지" 지시에 따라 모든 화면을 **한 SPA**(레일 내비게이션)로 통합했다:
대시보드 · S1 규정 영향 분석 · S2 마이데이터 상담 · 온톨로지 탐색기 · Agent Registry(S3 뷰) ·
Two-Plane 뷰(S4) · Guardrails 로그(S5) · 에이전트(컨트롤룸 프록시 — RBAC·예산·감사는
컨트롤룸 백엔드가 그대로 시행) · 디자인 스튜디오/가이드북(임베드).

- **S2 파이프라인 (F3)**: 입력 Guardrails → Semantic Layer → 정확 조회(합성, 벡터 검색 없음)
  → 결정론적 계산엔진(수식 단계 표시) → 마스킹/토큰화(경계 페이로드 토글) → 스트리밍 설명
  → 출력 Guardrails + 수치 검증기 → 재식별. 단계별 온프렘(앰버)/클라우드(시안) 색 규칙.
- **실물 Bedrock Guardrails** `iol2t2rp0q9i` v3 (STANDARD tier + apac 프로파일): 투자권유
  차단·정상 상담 통과 4케이스 검증. 목 아님 (§12.4).
- **F6 경계 계측**: 요청마다 traceId·마스킹 필드·경계 페이로드 PII 실측 스캔·토큰 추정을
  DynamoDB에 기록(TTL 7일). S4 카운터는 이 실측값의 합 — 하드코딩 아님. 현재 PII 반출 0건.
- 미완(정직): 온프렘 플레인 물리 분리(별도 VPC·ECS)와 Neptune 전환은 Phase 3 인프라,
  S3 화면 생성 반전 시연은 Phase 4. UI에 해당 문구 명시.

## 다음 Phase

SPEC §13 참조 — Phase 2: GraphRAG 엔진과 S1 화면 (완료 시 검토), Phase 3: 온프렘
플레인 분리(ECS Fargate·RDS·Guardrails), Phase 4: Registry와 S3, Phase 5: 마무리.
