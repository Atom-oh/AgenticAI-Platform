# Agentic AI Platform — 은행 데모 (SPEC.md 구현)

루트 `SPEC.md`가 정본 요구사항이다. 이 문서는 **실제 배포 상태**와 운영 절차를 적는다 — 코드에 없는 것을
"완료"라고 쓰지 않는다. (상태 표는 §"구현 상태" 참조, 배포 시점마다 갱신)

## 구조 — Single Boundary — 두 스택, NAT 없음

```
[브라우저] ─ CloudFront(유일한 퍼블릭 진입점) ─ S3(프라이빗)
     └─ wss:// API Gateway($connect: Cognito access token 검증)
            └─ WsFn (클라우드 플레인, VPC 밖)  ── Bedrock(Claude·Titan·Guardrails) · Cognito · DynamoDB(연결/트레이스/캐시/Registry)
                  ├─ ReaderFn (F7 Reader: Bedrock만, 내부 도구 invoke 권한 없음 → AccessDenied 시연)
                  ├─ GatesFn  (F5 게이트: tsc / eslint / axe-core — Node 20)
                  └─ lambda:Invoke ─▶ BridgeFn ─────────────────────────────┐   BankPlatformPlane 스택 (VPC 10.77/16)
                                                                             │   ┌───────────────────────────────────────┐
                        cloud-isolated 서브넷: BridgeFn · WriterFn · Neptune │   │ onprem-isolated 서브넷 (인터넷 경로 0) │
                        (Bedrock/Lambda VPC 엔드포인트는 이 SG들만 허용)     ├──▶│ 내부 ALB → ECS Fargate VPC 내부 서비스   │
                                                                             │   │  정확 조회·계산엔진·마스킹·감사원문·   │
                                                                             │   │  벡터 인덱스  ── RDS PostgreSQL        │
                                                                             │   └───────────────────────────────────────┘
```

- **VPC 내부 플레인 = 실제 네트워크 분리**: NAT·퍼블릭 서브넷이 없고, VPC 내부 태스크 SG는 Bedrock 엔드포인트 SG에 포함되지 않는다.
  프롬프트 원문과 재식별 매핑은 RDS `audit_log`에만 남는다. 클라우드 트레이스 테이블에는 해시·길이·메트릭만 기록된다(§12.3).
- **브리지 Lambda가 유일한 입구**: IAM invoke만 가능. Neptune openCypher와 VPC 내부 ALB를 대리 호출하며 AWS API·인터넷을 호출하지 않는다.
- **관리 작업 분리**: Neptune 적재·Registry 시드·리셋은 `AdminFn`(IAM invoke 전용). WebSocket 사용자 경로에는 없다.
- **Guardrails는 코드로 정의**(`infra/lib/stack.ts` CfnGuardrail): 투자권유 토픽 차단, PII 탐지/익명화, 근거 점수(표시용), 비속어.
- **비용가드/오프라인 폴백**: 일일 Bedrock 토큰 상한(DAILY_TOKEN_CAP) 초과·호출 실패 시 캐시 응답을 재생하고 UI에 "캐시 응답" 배지를 띄운다.

## 에이전트 계층 — AgentCore 네이티브

- **에이전트 = AgentCore Harness** (코드 없는 관리형 에이전트 루프): 모델(`global.anthropic.claude-sonnet-5`)·시스템프롬프트·
  skills(S3 `SKILL.md`)·tools(Gateway MCP)·managed memory. `agentcore/agent_specs.py`의 시나리오 에이전트 4종을 `AdminFn seed_agents`가
  멱등 생성하고, 빌더 화면에서 사용자 정의 에이전트를 만든다.
- **도구 = AgentCore Gateway `bankplatformcore-tools`(MCP, IAM 인바운드)** → Lambda 타깃 `agentcore/gateway_tools.py` 10종
  (규정 목록·규정 영향 순회·컴포넌트 영향·Semantic 해석·**마스킹 반환 고객 조회**·우대금리/한도 계산·승인 컴포넌트(Consumer API)·화면 게이트·내부 문서 검색).
  개인데이터 도구는 VPC 내부에서 조회·계산·마스킹한 뒤 **마스킹 결과만** 반환한다 — 에이전트(LLM)는 원본을 보지 못한다 (도구 출력 = 경계).
- **거버넌스 = 플랫폼 Registry(DynamoDB: 상태기계·감사·유일성·하이브리드 검색) + AgentCore Registry 미러**(`agentcore/registry_mirror.py`,
  submit-for-approval / update-status 동기화). **APPROVED가 아닌 에이전트는 호출되지 않는다** (Consumer 게이트, `handlers/agents.py`).
- AgentCore Insights·Evaluations·Policy는 사용하지 않는다(SPEC v2 §11-4). Harness 화면에는 `Tier 0/1 전용` 배지.

## 디렉토리

| 경로 | 내용 |
|---|---|
| `api/ws_handler.py` | 진입점(인증·디스패치) — 얇게 유지 |
| `api/handlers/` | 액션별 핸들러: `core`(허브·탐색·트레이스·리셋), `s1`, `s2`, `registry`, `screengen`, `report` |
| `api/common/` | `ctx`(이벤트 push), `log`(원문 마스킹 로그), `tracing`(F6 트레이스), `pii`(독립 PII 스캔), `costguard`(예산·캐시), `plane`(VPC 내부 플레인 클라이언트) |
| `api/admin_handler.py` | 관리자 Lambda(IAM invoke) |
| `engine/` | GraphRAG · Vector RAG(BM25+dense+RRF+Cohere rerank, 약화 없음) · Bedrock 래퍼(실측 usage) |
| `graph/store.py` | `GraphStore` 인터페이스, `LocalGraphStore`, `NeptuneGraphStore`(브리지 경유) |
| `registry/` | F4 Registry: 상태기계·유일성·감사·Consumer API(APPROVED만)·하이브리드 검색·시드 |
| `agentcore/` | Harness 래퍼·에이전트 명세·Gateway 도구 Lambda·AgentCore Registry 미러 |
| `screengen/`, `gates/`, `skills/` | F5 화면 생성 에이전트, 실검증 게이트(Node), 퍼블리싱/접근성 스킬 |
| `report/` | F7 Reader/Writer/내부 도구 Lambda 핸들러 |
| `onprem/` | VPC 내부 컨테이너: 정확 조회(RDS)·계산엔진·마스킹·감사원문·벡터 인덱스 |
| `bridge/` | 브리지 Lambda |
| `infra/lib/plane-stack.ts`, `infra/lib/stack.ts` | CDK 두 스택 |
| `seed/`, `schema/`, `semantic/` | 합성데이터(시드 고정) · 온톨로지 스키마 · Semantic Layer |
| `web/` | React 18 + Vite + TS + Tailwind, 단일 SPA(레일 내비), Pretendard, 다크 |
| `tests/` | pytest (오프라인) |

## 운영

```bash
cd platform
python3 -m pytest tests/ -q                       # 오프라인 테스트 (AWS 호출 없음, 344개)
MAIN_STACK=BankPlatformCore bash deploy.sh --plane            # 플레인 스택 + 메인 스택 + 시드 + 프론트 (첫 배포 25~35분)
MAIN_STACK=BankPlatformCore GRAPH_BACKEND=neptune bash deploy.sh   # 시연 표준: Neptune 백엔드 (Neptune 재적재 자동)
MAIN_STACK=BankPlatformCore bash deploy.sh --no-web           # 백엔드만 재배포
python3 cli.py admin health                       # 브리지 → VPC 내부 서비스(/health: RDS·벡터·AOSS) · Neptune /status
python3 cli.py admin seed_agents                  # 시나리오 에이전트 4종 등록(AgentCore Runtime/Strands) + Registry 승인 + AgentCore Registry 미러
python3 cli.py admin reset_demo                   # 시연 리셋 (UI의 ⟲ 버튼과 동일)
bash teardown.sh                                  # 시연 후 플레인 스택 삭제 (Neptune·RDS·ECS·AOSS 상시 과금 — §10-1)
bash teardown.sh --all                            # 메인 스택까지 삭제
```

- 라이브: **https://agent.atomai.click** (CloudFront `d8f5f6gxuiuxw`, 스택 `BankPlatformCore`). 구 스택 `BankPlatform`(d15n7n9ypt87h8)은
  롤백 정리 고착 상태로 남아 있는 **구버전 예비 경로**다 — 시연 후 삭제.
- 데모 계정: `demo@atomai.click`. 비밀번호는 Secrets Manager `bank-platform/demo-user` — 문서·코드에 적지 않는다(§9).
  Cognito 풀은 초대 전용(`AllowAdminCreateUserOnly=true`), 가입 UI 없음.
- 관측성: CloudWatch 대시보드 `BankPlatformCore-ops`, 알람(WsFn 오류·스로틀·p95, ReaderFn 오류). 로그는 JSON 1행/이벤트, `traceId` 포함,
  프롬프트·개인데이터 키는 해시로 치환된다(§12.5). VPC 내부 서비스 로그도 메트릭만.
- CI: `.github/workflows/platform-ci.yml` — pytest · 웹 타입체크/빌드 · 게이트 node:test · cdk synth · 비밀번호/키 패턴 검사.
  (고객 제안은 GitLab CI 전제 — 이 저장소의 GitHub Actions는 같은 파이프라인의 검증용이다. CodeCommit/CodePipeline 미사용.)

## 시연 리허설 체크리스트 (SPEC v2 §8-5·§10-1)

1. `python3 cli.py admin health` → `storeReady:true`, `aossReady:true`(AOSS 177 docs), Neptune `healthy`.
2. 대시보드 사이드바: **그래프 백엔드 = Neptune Serverless**, **VPC 내부 플레인 = 연결됨**, LLM 경로 = Tier 0/1 Claude global.
3. S1 프리셋 → GraphRAG 카운트(상품 12·화면 55·컴포넌트 77·부서 7·문서 7·정책규칙 5, Neptune 실측)와 경로 그래프, Vector 패널의 "VPC 내부 벡터 인덱스(AOSS)" 배지.
4. S2 프리셋 → ⓪추론 경로 배지 → ⑤ 익명화 게이트(페이로드 기본 표시, 식별자 0건) → ⑧ Semantic 검증. Semantic Layer OFF 토글로 "조용히 틀림" 재현.
5. S5 프리셋 → Guardrails 차단(`investment-solicitation`, STANDARD 티어·APAC 프로파일).
6. Registry: Button v2 → DEPRECATED, v3 → APPROVED → 화면 생성 재실행 → 게이트 6종(빌드·타입·린트·KWCAG·구조 스냅샷·Registry) 결과와 Button v3 사용 확인.
7. 에이전트 빌더: 시나리오 에이전트(AgentCore Runtime · Strands) 채팅 → 도구 호출 카드(Gateway MCP) + 경계 계측 이벤트. 새 에이전트 만들기 → PENDING_APPROVAL → 승인 전 호출 거부(Consumer 게이트) → 승인 후 호출.
8. Single Boundary 뷰: 5개 지표(VPC 잔류 항목·경계 토큰·모델 ID·저장/추론 배지·차단) 모두 실측값. 대시보드 헤더 "데모 대체 표기"(§11) 열어 3개 대체 지점 확인.
9. 문제 발생 시 ⟲ 시연 리셋 → Registry 기준선 복원; Bedrock 장애 시 "캐시 응답" 배지가 붙은 재생.

### e2e 실측 (2026-09-03 08:00 KST, `tests/e2e/ws_e2e.py`, 라이브 백엔드)

| 시나리오 | 결과 | 실측 |
|---|---|---|
| S1 규정 영향 분석 | 통과 | 전체 23s · 첫 토큰 2.9s(토큰 배치 후), Neptune 카운트 상품 12·화면 55·컴포넌트 77·부서 7·문서 7·정책규칙 5, 그래프 노드 186, 벡터 검색 = VPC 내부(AOSS), 근거 검증 위반 0 |
| S2 마이데이터 상담 | 통과 | 전체 11.4s · 첫 토큰 5.8s, 브리지 경유 VPC 내부 플레인, 마스킹 필드 customerName·customer_id·account_id, 독립 PII 스캔 0건, 수치 검증 위반 0, 출력 가드레일 NONE |
| S5 Guardrails | 차단 | `investment-solicitation` 토픽 (STANDARD 티어·APAC 프로파일) |
| S3 화면 생성 | 통과 (2회차) | 1차 타입 게이트 실패(Badge tone 'danger' 비허용) → 실패 사유 반영 1회 재생성 → 빌드·타입·린트·KWCAG·구조 스냅샷·Registry 승인 전부 통과, 컴포넌트 10종 사용 |
| 에이전트 빌더 — Strands 런타임 호출 | 통과 | AgentCore Runtime `bank_platform_agents`(READY), 전체 37s · 첫 토큰 25s(microVM 콜드스타트+도구 2회), 도구 list_regulations → analyze_regulation_impact (Gateway MCP), 실측 토큰 7,905/2,504; AgentCore Registry 미러 APPROVED 4건 |
| UX Asset Portal | 통과 | Components 80 카드, Related = 그래프 순회(computedBy graph-traversal), CMP-Button-v2 영향: 화면 21·패턴 35·정책규칙 45 |
| Single Boundary 뷰 | 통과 | VPC 잔류: 벡터 청크 177·감사 원문 N·온톨로지 3,797(neptune) — 플레인 /health 실측; 모델 ID 집계; 차단/캐시 건수 |
| F7 보고서 | 재검증 필요 | 1차 e2e에서 `WEB_URL` 미설정 오류 → env 추가 후 재배포 (아래 재검증 절 참조) |

알려진 한계(정직 표기): S2 LLM 첫 토큰 ~6초(플레인 준비·입력 가드레일 포함 — 단계 이벤트는 1초 내 도착하므로 화면 침묵은 없다), 에이전트 런타임 첫 호출은 microVM 콜드스타트로 20초 이상(리허설 직전 한 번 워밍업 권장). API Gateway
@connections 프레임 상한으로 토큰 프레임을 32자/60ms 단위로 배치한다(스로틀 시 캐시 응답 폴백이 동작함을 실측).

## 구현 상태 (2026-09-03 새벽 배포 기준 — 코드·배포·e2e로 확인된 것만)

| 항목 | 상태 | 근거 |
|---|---|---|
| S1 규정 영향 분석 (GraphRAG vs Vector RAG, PolicyRule 경로) | 배포 | Neptune v2 3,797/11,052 적재, `impact_of_regulation` 실측 카운트; 벡터 인덱스 AOSS(177 docs) VPC 엔드포인트 |
| S2 마이데이터 상담 (게이트·배지·Semantic 토글) | 배포 | `api/handlers/s2.py`, `tests/test_s2_v2.py` 31 통과, 가드레일 v3 STANDARD |
| S3 Registry 상태기계·Consumer API·화면 생성 + 실검증 게이트 | 배포 | `registry/`, `screengen/`, `gates/`(tsc·eslint·axe), 테스트 통과 |
| S4 Single Boundary 뷰 (§8-3 지표 5종) | 배포 | `handlers/core.py traces.retained`, `Views.tsx` |
| S5 Guardrails 실차단 | 배포·실측 | `apply-guardrail` 투자권유 질문 → `GUARDRAIL_INTERVENED` (v2 이후) |
| F7 Reader/Writer IAM 분리 + 인젝션 | 배포 | ReaderFn(권한 없음, AccessDenied 실측) / WriterFn(격리 서브넷, 인터넷 경로 없음) |
| UX Asset Portal (Related = 그래프 순회) | 배포 | `handlers/portal.py`, `views/Portal.tsx`, `tests/test_portal.py` 27 통과 |
| 에이전트 계층 (AgentCore Runtime · Strands · Gateway MCP · Registry 미러 · 빌더) | 배포 | `agents/`, `agentcore/`, `handlers/agents.py`; 로컬 컨테이너 스모크에서 Gateway 도구 호출·스트리밍·경계 계측 확인 |
| Tier 2 Gemma 경로 (bedrock-mantle) | 코드 완료 · 가용성 런타임 확인 | `engine/llm.py GemmaAdapter` — 모델/키 미확인 시 배지에 "미가용" 표기 |
| 익명화 변환(ML 가명처리·재식별 볼트) | 미구현 (배지 표기) | §11-2 배지: 규칙 기반 토큰화만 구현 |
| pgvector | 미사용 (AOSS 확정) | §16 |

