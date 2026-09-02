# 데모 아키텍처와 설계 결정

라이브: <https://agent.atomai.click/> (`demo@atomai.click` — 비밀번호는 Secrets Manager `bank-platform/demo-user`)


## 인터랙티브 다이어그램 (archify)

- **[플랫폼 아키텍처 (인터랙티브)](https://www.atomai.click/AgenticAI-Platform/platform-architecture.html)** — Single Boundary VPC·중앙 MCP·거버넌스를 가이드 뷰 3장으로 탐색 (Play story 지원)
- **[S2 상담 파이프라인 워크플로우 (인터랙티브)](https://www.atomai.click/AgenticAI-Platform/s2-workflow.html)** — 정상 상담·S5 차단·계측/감사 경로

## 전체 구성

```mermaid
flowchart LR
  U[브라우저 SPA] -->|HTTPS| CF[CloudFront + S3<br/>유일한 퍼블릭 진입점]
  U -->|wss + Cognito 토큰| WS[WebSocket 오케스트레이터]
  WS -->|관리형 실행 위임| H[AgentCore Harness<br/>Strands]
  H -->|MCP 도구 · IAM| GW[AgentCore Gateway<br/>툴 10종]
  GW -->|invoke| BR[브리지 Lambda<br/>VPC의 유일한 입구]
  subgraph VPC[VPC 프라이빗 서브넷 — 인터넷 게이트웨이 없음]
    BR --> ECS[플레인 서비스 ECS<br/>정확 조회·계산·감사 원문]
    ECS --> RDS[(RDS — PII 원장)]
    BR --> NEP[(Neptune — 온톨로지)]
    ECS --> GATE{{익명화 게이트<br/>유일한 통과 지점}}
  end
  GATE -->|익명화된 페이로드만| BED[Bedrock<br/>Claude global · Gemma mantle<br/>Guardrails 실물]
```

## 핵심 설계 결정

| 결정 | 이유 | 책의 근거 |
|---|---|---|
| GraphRAG와 Vector RAG를 **같은 질문에 병렬 실행** | 비교군을 약화시키지 않고(하이브리드 BM25+dense+리랭커) 관계 추적의 차이만 보여준다 | Part 6 |
| 숫자는 **결정론적 계산엔진**만 생성, LLM은 설명 | 출력 검증기가 계산엔진에 없는 수치를 잡아낸다 — "숫자가 조용히 틀리는" 문제 차단 | Part 3 |
| **데이터 배치 원칙(개정 A1)**: PII는 VPC 안에, 모델 호출 페이로드는 익명화 필수 | 국내 금융 클라우드 규제상 PII도 요건 충족 시 VPC 보관 가능 — 진짜 경계는 "외부 SaaS 모델"이다. Bedrock 호출에는 익명화(마스킹) 게이트가 필수 통제 | Part 9·12, SPEC §17 A1 |
| Guardrails **실물** (목 금지) | STANDARD 티어 + APAC 크로스리전 프로파일 — 클래식 티어는 한국어 토픽 미탐지 | Part 12 |
| WebSocket `$connect`에서 Cognito 토큰 검증 | CloudFront가 wss를 프록시하지 못하므로, 무인증 연결 즉시 거부로 공개 노출 원칙 유지 | Part 9 |
| 에이전트 실행은 **AgentCore Harness(Strands) + Gateway MCP(IAM)** | 관리형 거버넌스(승인 수명주기·CloudTrail)를 실물로 — Tier 2는 자체 LLMClient 직행(§11-4) | SPEC §16 |
| Tier 2 PII 추론은 **open-weight Gemma 4 31B**(bedrock-mantle) 데모 대체 | 운영 IDC GPU+vLLM의 정직한 대체 — 화면 배지 필수 | SPEC §11-1 |
| 컨트롤룸·스튜디오는 **서버사이드 프록시로 네이티브 통합** | CORS 우회가 아니라 사용자 본인 토큰을 전달 — 기존 백엔드의 RBAC·예산·감사가 그대로 시행 | Part 11 |
| `GraphStore` 인터페이스 + local/neptune 이중 구현 | Neptune 상시 과금 통제. UI가 현재 백엔드를 항상 표시 — 로컬로 시연하며 Neptune이라 말하지 않는다 | Part 8 |
| 합성데이터 시드 고정(20260902) | 3,317노드/8,520엣지 재현 가능. 개인 식별자는 생성 시점부터 토큰(CUST-/ACCT-) | Part 5 |

## 데이터 배치 — 원장과 AI-Ready 파생

- **PII 원장**: 고객 데이터 플레인(PII VPC — 인터넷 차단 격리 서브넷의 RDS). 복제·벡터화하지 않고 키 기반 정확 조회만.
- **AI-Ready 파생 지식**: 규정↔상품↔화면↔부서 온톨로지(Neptune)와 Semantic Layer — 원장에서 파생된 비식별 기업 지식만 그래프로 재구조화.
- **모델 호출 경계**: Bedrock으로 나가는 페이로드는 익명화 게이트 통과가 필수(F6이 실측). 외부 SaaS 모델에는 개인정보를 태우지 않는다.

## S2 파이프라인 (F3)

```
질의 → ①입력 Guardrails → ②Semantic Layer 지표 해석(YAML 정의만 신뢰)
     → ③정확 조회(키 기반, 벡터 검색 없음) → ④결정론적 계산엔진(수식 단계 반환)
     → ⑤마스킹/토큰화 → ⑥Bedrock 스트리밍 설명 → ⑦출력 Guardrails + 수치 검증 → 재식별
```

각 단계가 UI에서 펼쳐지고, 온프렘(앰버)/클라우드(시안) 색 규칙이 전 화면 공통이다.

## 검증된 수치 (실측)

- S1: seed 신뢰도 80%, 영향 상품 12·화면 39·부서 7·문서 7, 환각 노드 ID 0건, 첫 토큰 3.1초
- S2: 5단계 8.5초, 우대금리 3.90%·한도 2억 — LLM 설명의 전 수치가 계산엔진 출력과 일치
- S4: 경계 통과 개인식별자 누계 **0건** (DynamoDB 기록 합산 — 하드코딩 아님)
- S5: 투자권유 질문 차단 / 정상 상담 통과 — 4케이스 검증 후 버전 발행

## 정직한 미완 (::: warning 미정착 영역)

::: warning 미정착 영역
- **온프렘 플레인의 물리 분리**(별도 VPC 프라이빗 서브넷 + ECS Fargate)는 Phase 3 예정 —
  현재 계산엔진·마스킹은 단일 Lambda 안의 논리 분리이며 UI에 그렇게 명시한다.
- **Neptune 전환**은 Phase 3 (현재 LocalGraphStore, UI 배지에 표시).
- **S3 "컴포넌트 Deprecate → 생성 반전" 시연**은 Phase 4 (Registry 뷰는 실물 연동 완료).
- 벡터 인덱스의 OpenSearch Serverless 이관은 온프렘 플레인 구성과 함께 진행.
:::

## 저장소 배치

| 경로 | 내용 |
|---|---|
| `platform/` | 이 데모 전체 (엔진·API·웹·CDK·시드·테스트) |
| `SPEC.md` | 요구사항 명세 원문 (Phase별 진행) |
| `demo/builder-harness/` | 자매 데모 — 에이전트 컨트롤룸 (한울증권) |
| `demo/uiux-studio/` | 스튜디오 백엔드 원본 (네이티브 통합의 소스) |

재현: `platform/README.md`의 Phase별 명령. 배포는 `platform/deploy.sh` 한 번.
