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

## 다음 Phase

SPEC §13 참조 — Phase 2: GraphRAG 엔진과 S1 화면 (완료 시 검토), Phase 3: 온프렘
플레인 분리(ECS Fargate·RDS·Guardrails), Phase 4: Registry와 S3, Phase 5: 마무리.
