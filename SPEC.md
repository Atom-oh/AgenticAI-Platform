# Agentic AI Platform 데모 — 명세 v2 (Single Boundary)

> **Claude Code 사용법**
> 이 파일을 프로젝트 루트에 `SPEC.md`로 저장하고 다음과 같이 지시한다.
> ```
> SPEC.md를 읽어라.
> 1) §14 확인 질문을 먼저 물어라.
> 2) 답을 받으면 §1 정합성 감사부터 시작한다 (코드 수정 전).
> 3) §11 데모 제약 표기 규칙과 §12 안티 요구사항은 예외 없이 지킨다.
> 4) §13 Phase 단위로 멈추고 검토받는다.
> ```
> 이 문서는 이전 명세(`agentic-ai-platform-demo-spec.md`)를 **대체**한다. 충돌 시 이 문서가 우선한다.
> 주요 변경: 아키텍처를 Two-Plane → **Single Boundary**로 전환, PII를 VPC 내부로 이동, 익명화를 유일한 경계 통과 조건으로 승격, PII 추론 모델 확정.

---

## 1. 목표와 증명 대상

부서장급이 15분 시연을 보고 아래 세 가지를 스스로 설명할 수 있어야 한다.

| # | 증명할 주장 | 증명 방법 |
|---|---|---|
| P1 | 벡터 RAG만으로는 "있는 것을 없다고" 답하는 오류를 못 막는다 | 동일 질문을 Vector RAG / GraphRAG에 던져 좌우 비교 |
| P2 | **경계는 하나뿐이고, 익명화를 통과하지 못하면 Bedrock에 도달할 수 없다** | 요청마다 무엇이 경계를 넘었는지 실측 표시 |
| P3 | 사내 AI 자산은 승인·버전 관리되고, 승인 안 된 자산은 에이전트에게 보이지 않는다 | Registry에서 Deprecated 전환 시 생성 결과가 즉시 달라짐 |

### 1-1. 첫 작업 — 정합성 감사 (코드 수정 전 수행)
현재 데모 코드와 이 명세를 대조해 표로 보고한다.

| 명세 항목 | 데모 현황 | 상태 (일치 / 불일치 / 미구현 / 명세에 없음) | 조치 |

특히 다음을 확인한다.
- "온프렘", "On-Premises", "Two-Plane", "In-Region", "서울을 벗어나지 않" 문자열이 남아 있는지 → 전부 교체 대상
- 기존 ECS 프라이빗 플레인 구성이 있는지 → VPC 프라이빗 서브넷으로 재정의
- 개인신용정보 반출 0건 카운터가 있는지 → §8 지표로 교체

불일치와 미구현만 다음 단계로 넘긴다. **명세에 없는 주장을 데모가 하지 않게 한다.**

---

## 2. 데모 시나리오

### S1. 규정 영향 분석 — GraphRAG의 존재 이유 (4분) ★ 최우선
질의: **"전세자금대출 담보 인정 규정이 개정되면 영향받는 상품 · 화면 · 컴포넌트 · 담당부서 · 수정이 필요한 문서는?"**

- 좌: Vector RAG — 규정 청크 3~5개만 반환. 영향 범위를 답하지 못함
- 우: GraphRAG — 경로를 따라가 영향 대상을 **순회 경로와 함께** 반환
- 하단: 실제 순회 경로 그래프 시각화 (전체 그래프가 아니라 경로만 강조)
- 순회 경로: `Regulation → PolicyRule → Product → Screen → Component → Department → Document`

> 다른 것을 줄여도 이 화면은 완성도를 최우선으로 한다.

### S2. 마이데이터 상담 — 숫자는 LLM이 만들지 않는다 (4분)
질의: **"제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?"**

- 개인 금융데이터는 **정확 조회**(tool call). 벡터 검색 사용 금지
- 우대금리는 **결정론적 계산엔진**이 계산, LLM은 설명만
- 화면에 분리 표시: ① 조회된 원본 값 ② 계산 내역(수식 포함) ③ LLM 생성 설명
- **Bedrock에 실제 전달된 페이로드를 기본 표시** (토글 아님)
- 추론 경로 배지 표시 (§11)

### S3. 화면 생성 — Registry 거버넌스 (3분)
질의: **"여신 심사 결과 조회 화면을 만들어줘"**

- 에이전트가 Registry에서 **APPROVED 상태 컴포넌트만** 조회해 React 코드 생성
- 검증 게이트 결과 표시: 빌드 / 타입 / 린트 / KWCAG 접근성 / 시각 회귀
- **실시간 반전**: 시연 중 `Button v2`를 Deprecated로 바꾸고 재생성 → `Button v3`을 쓴 다른 코드가 나온다

### S4. Single Boundary 뷰 (2분)
- S1~S3 실행 이력 타임라인
- 요청별: VPC 내부에 남은 항목 / 경계를 넘은 토큰 수 / 사용한 모델 ID / Guardrails 차단 여부
- 상단 고정 지표는 §8 참조

### S5. Guardrails 실패 시연 (2분)
- 투자권유성 질문("어떤 상품이 제일 돈 많이 벌어요?") → Bedrock Guardrails 차단
- 근거 없는 질문 → "모른다" 응답 (환각 방지)

---

## 3. 아키텍처 — Single Boundary

### 3-1. 경계는 하나다
```
        AWS 서울 리전 VPC 프라이빗 서브넷 (인터넷 게이트웨이 없음)
        ┌──────────────────────────────────────────────┐
        │ PII 원장 (Aurora)                             │
        │ RAG 인덱스 (OpenSearch)  ← PII 포함 문서 가능   │
        │ 온톨로지 (Neptune)                            │
        │ Semantic Layer                               │
        │ 결정론적 계산엔진                              │
        │ 오케스트레이터 / 도구                          │
        │ 감사로그 원문                                  │
        └───────────────┬──────────────────────────────┘
                        │
              ╔═════════▼═════════╗
              ║   익명화 게이트     ║  ← 유일한 통과 지점
              ╚═════════╤═════════╝
                        │
        ┌───────────────▼──────────────────────────────┐
        │ Amazon Bedrock (global 프로파일)              │
        └──────────────────────────────────────────────┘

        익명화 불가 워크로드 → EKS Hybrid Nodes (IDC GPU + vLLM)
                                ※ 데모에서는 Bedrock Gemma로 대체
```

### 3-2. 반드시 지킬 것
- **익명화 게이트를 우회하는 코드 경로를 만들지 않는다.** 모든 Bedrock 호출은 이 게이트를 통과해야 한다.
- **경계 계측은 실측값이다.** 하드코딩된 "0건" 금지. 실제 페이로드를 계측해 기록한다.
- **Guardrails는 실제로 설정한다.** 시뮬레이션 금지.
- **원장은 복제하지 않는다.** 개인 데이터는 조회만 하고 벡터화·그래프 적재하지 않는다.
- 프롬프트 원문은 VPC 내부에만 저장한다. CloudWatch에는 메트릭과 `traceId`만.

### 3-3. 데이터 배치
| 구성 요소 | 위치 |
|---|---|
| PII 원장 (합성 마이데이터·거래내역) | VPC 프라이빗 서브넷 · RDS PostgreSQL |
| 벡터 인덱스 | VPC · OpenSearch 또는 pgvector |
| 온톨로지 그래프 | VPC · Neptune |
| Semantic Layer 정의 | VPC · YAML + 로더 |
| 결정론적 계산엔진 | VPC · Lambda 또는 Fargate |
| 익명화 게이트 | VPC · 게이트 서비스 + 토큰 볼트 |
| 감사로그 원문 | VPC |
| LLM 생성 (Tier 0/1) | Bedrock Claude (global 프로파일) |
| PII 추론 (Tier 2) | 운영: EKS Hybrid Nodes / **데모: Bedrock Gemma** |
| Guardrails | Bedrock Guardrails |
| Registry | VPC 내 자체 구현 또는 AWS Agent Registry |

---

## 4. 모델 · 엔드포인트 (확정)

### 4-1. Tier 0/1 경로 — Claude
| 항목 | 값 |
|---|---|
| 모델 ID | `global.anthropic.claude-sonnet-5` (기본), `global.anthropic.claude-opus-5` (고품질) |
| 엔드포인트 | `bedrock-runtime`, Converse API |
| 소스 리전 | `ap-northeast-2` (서울) |
| 라우팅 | **global 프로파일 — 전 세계 상용 리전** |
| 인증 | IAM SigV4 |

**서울 리전은 geographic 교차 리전 추론(APAC 프로파일)을 지원하지 않는다.** 단일 리전 온디맨드 경로도 없다. 따라서 추론 시점의 프롬프트는 국외로 이동할 수 있고, 이것이 익명화가 필수인 이유다.

### 4-2. Tier 2 경로 — Gemma (데모 대체)
| 항목 | 값 |
|---|---|
| 모델 ID | `google.gemma-4-31b` (Gemma 4 31B, 307억 파라미터, 256K 컨텍스트) |
| 엔드포인트 | **`bedrock-mantle`** — `bedrock-runtime` 미지원 |
| 호출 URL | `https://bedrock-mantle.us-west-2.api.aws/openai/v1` |
| API | OpenAI 호환 — `Chat Completions` / `Responses`. **Converse · InvokeModel 미지원** |
| 리전 | `us-west-2` (서울 미제공, 교차 리전 추론 미지원 → 리전 직접 호출) |
| 인증 | Bedrock **장기 API 키** (`OPENAI_API_KEY`) → **Secrets Manager 경유 필수** |
| 제약 | 병렬 도구 호출 미지원 (순차 호출), 요청 페이로드 최대 3.5MB |

**구현 요구**: Claude 경로와 클라이언트가 완전히 다르므로 `LLMClient` 인터페이스를 정의하고 두 어댑터를 만든다. 환경변수 `LLM_ROUTE=claude | gemma`로 전환하고, 운영 전환 시 `onprem-vllm` 어댑터를 추가할 수 있게 설계한다.

```python
# 예시 — Gemma 경로
from openai import OpenAI
client = OpenAI(
    api_key=get_secret("bedrock/api-key"),
    base_url="https://bedrock-mantle.us-west-2.api.aws/openai/v1",
)
resp = client.chat.completions.create(
    model="google.gemma-4-31b",
    messages=[{"role": "user", "content": prompt}],
)
```

---

## 5. 온톨로지 (최우선 산출물)

### 5-1. 여신 도메인 노드
| 라벨 | 목표 건수 | 핵심 속성 |
|---|---|---|
| `Regulation` | 60 | `code`, `title`, `article`, `effectiveDate`, `version`, `status` |
| `RegulationAmendment` | 25 | `amendmentId`, `date`, `summary`, `diffType` |
| `Product` | 120 | `productCode`, `name`, `category`, `launchDate`, `status` |
| `Condition` | 800 | `conditionId`, `type`, `operator`, `value`, `unit`, `priority` |
| `Department` | 20 | `deptCode`, `name`, `role` |
| `Document` | 200 | `docId`, `title`, `type`, `deptCode`, `updatedAt` |
| `Template` | 12 | `templateId`, `name`, `sections[]` |
| `Customer` | 500 | `customerId`(가명), `segment`, `joinDate` |
| `Account` | 1,200 | `accountId`(토큰), `productCode`, `balance`, `openDate` |
| `Merchant` | 150 | `merchantId`, `name`, `mccCode`, `category` |

### 5-2. UX 자산 도메인 노드 (고객 PoC 요건 6종 라이브러리)
| 라벨 | 목표 건수 | 대응 라이브러리 |
|---|---|---|
| `Screen` | 150 | — |
| `Component` | 80 | Component Library |
| `Pattern` | 40 | Pattern Library |
| `Procedure` | 30 | Procedure Library |
| `PolicyRule` | 60 | Policy Rule |
| `UXTerm` | 200 | UX Dictionary |
| `ScreenMeta` | 150 | Screen Metadata |

`Component` 속성: `componentId`, `name`, `version`, `approvalStatus`, `propsSchema`, `owner`
`ScreenMeta` 속성: `screenNo`, `purpose`, `entryCondition`, `prevScreens[]`, `nextScreens[]`

**합계 목표: 노드 약 3,800 / 관계 약 11,000**

### 5-3. 관계
```
// 여신
(Regulation)-[:APPLIES_TO]->(Product)
(Regulation)-[:AMENDED_BY]->(RegulationAmendment)
(Regulation)-[:SUPERSEDES]->(Regulation)
(Product)-[:HAS_CONDITION]->(Condition)
(Condition)-[:DERIVED_FROM]->(Regulation)        // S1 핵심 엣지
(Condition)-[:EXCLUDES]->(Merchant)
(Condition)-[:REQUIRES]->(Condition)
(Product)-[:OWNED_BY]->(Department)
(Document)-[:REFERENCES]->(Regulation)
(Document)-[:FOLLOWS]->(Template)
(Document)-[:OWNED_BY]->(Department)
(Customer)-[:HOLDS]->(Account)
(Account)-[:OF_PRODUCT]->(Product)
(Account)-[:TRANSACTED_AT]->(Merchant)

// UX 자산
(Product)-[:SOLD_VIA]->(Screen)
(Screen)-[:USES]->(Component)
(Screen)-[:FOLLOWS]->(Pattern)
(Pattern)-[:COMPOSES]->(Component)
(Procedure)-[:INCLUDES]->(Screen)
(Screen)-[:OWNED_BY]->(Department)
(PolicyRule)-[:CONSTRAINS]->(Screen)
(PolicyRule)-[:DERIVED_FROM]->(Regulation)       // 두 도메인 연결점
(Component)-[:SUPERSEDED_BY]->(Component)
(ScreenMeta)-[:DESCRIBES]->(Screen)
(UXTerm)-[:USED_IN]->(Screen)
```

### 5-4. 커버리지 제약 (검증 테스트 필수)
합성데이터는 아래를 만족해야 한다. 시연에서 결과가 빈약하면 논지가 무너진다.

- **최소 3개 `Regulation`**에 대해 S1 순회가 `Products ≥ 4`, `Screens ≥ 6`, `Components ≥ 8`, `Departments ≥ 3`, `Documents ≥ 5` 반환
- **최소 3개 `Component`**에 대해 영향 분석이 `Screens ≥ 12`, `Patterns ≥ 4`, `PolicyRules ≥ 2` 반환

### 5-5. S1 순회 쿼리 (동작 필수)
```cypher
MATCH (r:Regulation {code: $regCode})
OPTIONAL MATCH (r)<-[:DERIVED_FROM]-(c:Condition)<-[:HAS_CONDITION]-(p:Product)
OPTIONAL MATCH (r)<-[:DERIVED_FROM]-(pol:PolicyRule)-[:CONSTRAINS]->(s2:Screen)
OPTIONAL MATCH (p)-[:SOLD_VIA]->(s:Screen)-[:USES]->(comp:Component)
OPTIONAL MATCH (p)-[:OWNED_BY]->(pd:Department)
OPTIONAL MATCH (s)-[:OWNED_BY]->(sd:Department)
OPTIONAL MATCH (d:Document)-[:REFERENCES]->(r)
RETURN r,
  collect(DISTINCT c) AS conditions,
  collect(DISTINCT p) AS products,
  collect(DISTINCT s) + collect(DISTINCT s2) AS screens,
  collect(DISTINCT comp) AS components,
  collect(DISTINCT pd) + collect(DISTINCT sd) AS departments,
  collect(DISTINCT d) AS documents
```

---

## 6. Semantic Layer

온톨로지와 **분리된** 지표 정의 계층. YAML로 정의하고 Text-to-SQL이 이것만 참조한다.

```yaml
# semantic/metrics.yaml
metrics:
  - name: 우대금리_적용율
    korean_aliases: [우대금리, 금리우대, 우대이율]
    description: 기본금리 대비 실제 적용금리의 차이
    sql_template: |
      SELECT (base_rate - applied_rate) AS value
      FROM account_rate WHERE account_id = :account_id
    unit: percent
    owner_dept: 여신기획부
  - name: 전월실적
    korean_aliases: [전월 실적, 지난달 사용액, 전월 이용금액]
    description: 직전월 1일~말일 승인 합계 (취소분 제외)
    sql_template: |
      SELECT COALESCE(SUM(amount), 0) AS value FROM txn
      WHERE account_id = :account_id
        AND approved_at >= date_trunc('month', now()) - interval '1 month'
        AND approved_at <  date_trunc('month', now())
        AND status = 'APPROVED'
    unit: krw
    owner_dept: 여신기획부
dimensions:
  - name: 고객세그먼트
    korean_aliases: [고객등급, 세그먼트]
    values: [일반, 우대, 프리미엄, VIP]
```

**데모 포인트**: Semantic Layer를 끄면 LLM이 "전월실적"을 당월로 계산한다. 숫자가 조용히 틀리는 문제를 토글로 보여준다.

---

## 7. Agent Registry

- `recordType`: `MCP` / `AGENT` / `SKILL` / `CUSTOM`
- 상태 전이: `DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED` (+ `REJECTED` → `DRAFT` 복귀)
- `name` + `recordVersion` 유일성 제약 (같은 이름의 다중 버전 허용)
- **Consumer API는 `APPROVED`만 반환한다.** 이 제약이 S3 데모의 전부다.
- 승인 / 반려 시 감사 이벤트 기록
- 검색: 키워드 + 자연어(임베딩) 하이브리드

**등록 대상 매핑**
| 사내 자산 | recordType | 원본 |
|---|---|---|
| 컴포넌트 계약 (props · variants) | SKILL | Git |
| 화면 스펙 (MDX / JSON) | CUSTOM | Git |
| 레지스트리 · GitLab MCP 서버 | MCP | VPC 내 EKS |
| Skills (은행 퍼블리싱 규약) | SKILL | Git · 마크다운 |
| 화면 생성 에이전트 | AGENT | AgentCore Runtime |
| Figma · draw.io MCP | MCP | 외부 SaaS |

---

## 8. 화면

### 8-1. 화면 목록
| # | 화면 | 목적 |
|---|---|---|
| 1 | 로그인 | 데모 계정 인증 |
| 2 | 플랫폼 대시보드 | 에이전트 · 도구 · 스킬 수, Registry 상태, 경계 지표 |
| 3 | **규정 영향 분석** | S1 — Vector vs Graph 좌우 비교 + 경로 시각화 |
| 4 | 온톨로지 탐색기 | 노드 클릭 → 이웃 확장, 타입 필터 |
| 5 | **마이데이터 상담** | S2 — 채팅 + 단계별 펼침 패널 |
| 6 | **Agent Registry** | S3 — 카탈로그, 승인 워크플로우, 버전 체인 |
| 7 | 화면 생성 | S3 — 생성 코드 + 검증 게이트 결과 |
| 8 | **UX Asset Portal** | 고객 요건 반영 (§8-2) |
| 9 | 보고서 생성 | Reader / Writer 권한 분리 시각화 |
| 10 | **Single Boundary 뷰** | S4 — 경계 통과 실측 |
| 11 | Guardrails 로그 | S5 — 차단 이력 |

### 8-2. UX Asset Portal (신설)
- 좌측 카테고리: `Foundation` / `Components` / `Patterns` / `Screens` / `Procedures` / `Policies` / `UX Writing`
- 자산 상세 카드: `ID`, `Status`(Approved · Draft · Deprecated), `Version`, `Owner`
- **Related 카운트는 그래프 순회 결과다.** 하드코딩 금지. 예: `6 Patterns · 24 Screens · 3 Policies`
- `Version History`, `Publish / Sync` 액션
- 상세 카드에 **"이 컴포넌트를 변경하면 영향받는 화면"** 버튼 → 영향 분석 결과로 이동

### 8-3. Single Boundary 뷰 지표 (기존 "반출 0건" 카운터 교체)
1. **VPC 내부에 남은 항목** — 인덱스 / 원장 / 감사로그 건수
2. **경계를 넘은 토큰 수** + 전달된 필드 목록 (실측)
3. **사용한 모델 ID** — `global.anthropic.claude-sonnet-5` 또는 `google.gemma-4-31b`
4. **배지** — `저장: 서울 리전 / 추론: global 라우팅`
5. Guardrails 차단 여부

### 8-4. 시각 요구사항
- VPC 내부 영역과 Bedrock 영역을 **전 화면 동일한 색 규칙**으로 구분
- 경계를 넘는 데이터는 애니메이션으로 표시
- 그래프는 순회 경로만 강조 (전체 그래프 렌더 금지)
- 한국어 UI, Pretendard, 다크 테마 우선

### 8-5. 데모 안전장치
- **리셋 버튼** — Registry 상태와 대화 이력 초기화
- **시나리오 프리셋** — S1~S5 질문 원클릭 입력 (오타 방지)
- **스트리밍 필수** — 5초 내 첫 토큰. 8초 침묵은 데모를 죽인다
- **폴백** — Bedrock 실패 시 캐시 응답 + "캐시 응답" 배지

---

## 9. 합성데이터

- **실제 고객데이터 · 실제 상품명 · 실제 내규 조항을 사용하지 않는다.** 상품명은 가상으로 만든다.
- 규정 조항은 실제 전자금융감독규정을 인용하지 않고 구조만 모사한 가상 조항으로 만든다.
- **개인 식별자는 생성 시점부터 토큰 형태로 만든다.** 주민번호 형식조차 만들지 않는다.
  → 이렇게 하면 익명화 변환 로직이 없어도 게이트 통과 결과가 동일하다 (§11-2)
- 생성 스크립트는 `seed/`에 두고 시드값을 고정해 재현 가능하게 한다
- §5-4 커버리지 제약 검증 테스트를 포함한다

---

## 10. 기술 스택

| 계층 | 선택 |
|---|---|
| 프론트엔드 | React 18 + Vite + TypeScript + Tailwind |
| 그래프 시각화 | Cytoscape.js 또는 react-force-graph (경로 강조 필수) |
| 배포 | S3 + CloudFront (기존 파이프라인 재사용) |
| API | API Gateway + Lambda (Python 3.12) |
| VPC 워크로드 | Lambda / ECS Fargate (프라이빗 서브넷, NAT 없음) |
| 그래프 DB | Amazon Neptune (§10-1 비용 주의) |
| 벡터 검색 | pgvector on RDS (기본) 또는 OpenSearch Serverless |
| PII 원장 | RDS PostgreSQL (프라이빗, 합성데이터) |
| LLM | Bedrock — Claude (Tier 0/1) / Gemma 4 31B (Tier 2 대체) |
| 가드레일 | Bedrock Guardrails (실제 설정) |
| 에이전트 | §14 확정 |
| 인증 | Amazon Cognito |
| IaC | AWS CDK (TypeScript) |

**사용 금지**: AWS CodeCommit, AWS CodePipeline. CI/CD는 기존 GitLab을 전제로 표기한다.

### 10-1. 비용 주의
- **Neptune은 상시 과금된다.** `GraphStore` 인터페이스 + 두 구현(`NeptuneGraphStore` / `LocalGraphStore`)을 만들고 `GRAPH_BACKEND=neptune|local`로 전환한다.
- **고객 시연은 반드시 `neptune`으로 실행한다.** 로컬 구현으로 시연하면서 "Neptune입니다"라고 말하는 것은 허용되지 않는다. UI 하단에 현재 백엔드를 항상 표시한다.
- CDK `destroy` 스크립트와 시연 후 정리 절차를 README에 적는다.

---

## 11. 데모 제약과 표기 규칙 ★

데모는 운영 아키텍처의 일부를 대체한다. **대체 사실을 화면에서 숨기지 않는다.** 배지를 안 달고 넘어가다 시연 중 들키는 것이 최악이다.

### 11-1. PII 추론 경로 대체
- 운영: EKS Hybrid Nodes의 IDC GPU + vLLM
- 데모: Bedrock Gemma 4 31B @ `us-west-2` (GPU 미구성)
- **화면 배지 (필수)**
  ```
  PII 추론 경로
  운영: IDC GPU + vLLM (EKS Hybrid Nodes)
  데모: Bedrock Gemma 4 31B @ us-west-2 — GPU 미구성 대체
  ```
- 이 경로를 "IDC GPU"라고 표시해서는 안 된다.

### 11-2. 익명화 변환 로직 미구현
- 운영: 게이트에서 가명처리 · 식별자 토큰화 · 재식별 수행
- 데모: **변환 로직을 구현하지 않는다** (시간 제약)
- 대신 §9에 따라 **합성데이터를 처음부터 가명 · 토큰 형태로 생성**한다. 변환할 것이 없으므로 게이트 통과 결과가 운영과 동일하다.
- 게이트 자체는 **경계 통과 지점으로 실제 존재해야 한다.** 모든 Bedrock 호출이 이 지점을 지나가고, 여기서 페이로드를 계측한다.
- **화면 배지 (필수)**
  ```
  익명화 게이트
  운영: 가명처리 · 토큰화 · 재식별
  데모: 합성데이터가 이미 가명 형태 — 변환 로직 미구현
  ```

### 11-3. 온프렘 구성 없음
- 운영: 계정계 원장을 Direct Connect로 조회
- 데모: 합성 원장을 VPC 내 RDS에 둔다
- 화면에서 "온프렘"이라는 표현을 쓰지 않는다. "VPC 프라이빗 서브넷"으로 표기한다.

### 11-4. AgentCore 사용 제약
AgentCore insights · Evaluations · Policy는 서울에서 global 교차 리전 추론이 강제되고 SCP로 차단되지 않으며 목적지 리전에 프롬프트 · 응답이 저장될 수 있다.
- Tier 2 워크로드 경로에서 사용하지 않는다
- 사용하는 화면에는 `Tier 0/1 전용` 배지를 표시한다

---

## 12. 안티 요구사항 (예외 없음)

1. **익명화 게이트를 우회하는 코드 경로를 만들지 않는다.**
2. **대체 구성을 화면에서 숨기지 않는다.** (§11 배지 필수)
3. **개인 금융데이터를 벡터화하지 않는다.** 임베딩은 문서(약관 · 규정)에만.
4. **LLM이 금액 · 금리 · 한도를 생성하지 않는다.** 계산엔진 출력만 사용하고 출력 검증기를 둔다.
5. **프롬프트 원문을 CloudWatch에 남기지 않는다.** 메트릭과 `traceId`만.
6. **Guardrails를 목(mock)으로 만들지 않는다.**
7. **Vector RAG 비교군을 의도적으로 약화시키지 않는다.** 하이브리드 + 리랭커까지 정상 구현한다.
8. **하드코딩된 수치를 실측처럼 보이게 하지 않는다.** (Related 카운트, 경계 통과량 포함)
9. **비밀번호 · API 키를 코드나 문서에 하드코딩하지 않는다.** Secrets Manager 또는 `.env.local`(gitignore).
10. **실제 은행 상품명 · 내규 조항을 사용하지 않는다.**
11. **무한 재시도 루프를 만들지 않는다.** 재생성은 최대 1회.
12. **브라우저 스토리지에 개인데이터를 저장하지 않는다.**
13. "온프렘", "Two-Plane", "In-Region", "서울을 벗어나지 않" 표현을 쓰지 않는다.

---

## 13. Phase

### Phase 1 — 온톨로지와 데이터 ★
- [ ] §5 스키마 정의 (`schema/ontology.cypher`) — 여신 + UX 자산 도메인
- [ ] 합성데이터 생성 스크립트 (`seed/generate.py`), 시드 고정, 식별자는 토큰 형태
- [ ] §5-4 커버리지 검증 테스트
- [ ] `GraphStore` 인터페이스 + Local 구현
- [ ] §6 Semantic Layer YAML + 로더

> **완료 조건**: §5-5 쿼리가 CLI에서 실행되어 커버리지 제약을 만족한다.

### Phase 2 — GraphRAG 엔진과 S1
- [ ] 의도 분해 → Seed 선택 → 순회 → Context 조립
- [ ] Vector RAG 비교군 (하이브리드 + 리랭커)
- [ ] S1 좌우 비교 화면 + 경로 시각화
- [ ] `LLMClient` 인터페이스 + Claude 어댑터 + 스트리밍

> **완료 조건**: S1을 처음부터 끝까지 시연할 수 있다. 여기서 멈추고 검토받는다.

### Phase 3 — 경계와 S2
- [ ] VPC 프라이빗 서브넷 구성 (CDK)
- [ ] RDS 합성 원장 + 정확 조회 API
- [ ] 결정론적 계산엔진 + 단위테스트
- [ ] 익명화 게이트 (통과 지점 + 계측, 변환 로직 미구현)
- [ ] Gemma 어댑터 (`bedrock-mantle`, OpenAI 호환)
- [ ] Bedrock Guardrails 설정
- [ ] S2 상담 화면 + 단계별 펼침 패널
- [ ] Single Boundary 뷰 (§8-3)
- [ ] §11 배지 전체 적용

### Phase 4 — Registry · Portal · S3
- [ ] Registry 데이터모델 + 상태 전이 + 승인 API
- [ ] Consumer API (APPROVED만 반환)
- [ ] Registry 관리 화면
- [ ] UX Asset Portal (§8-2), Related 카운트는 그래프 쿼리
- [ ] 화면 생성 에이전트 + 실제 검증 게이트

### Phase 5 — 보고서와 마무리
- [ ] Reader / Writer IAM 역할 분리 + 인젝션 시연 데이터
- [ ] Guardrails 로그 화면
- [ ] 리셋 / 프리셋 / 폴백
- [ ] Neptune 전환 및 리허설
- [ ] README (시연 스크립트, 비용 정리 절차)

---

## 14. 시작 전 확인 질문

1. **기존 데모 코드베이스 위치** — `d1twhttjtzqewp.cloudfront.net`의 저장소는 어디인가? 확장인가 신규인가?
2. **에이전트 SDK** — Strands Agents / LangGraph / Claude Agent SDK 중 무엇? (Skills 사용 여부가 여기에 걸림)
3. **AgentCore 사용 범위** — Runtime · Gateway · Registry를 실제로 쓸까, 자체 구현으로 개념만 보여줄까? 서울 리전 가용성 확인 필요.
4. **벡터 저장소** — pgvector on RDS(기본 제안) vs OpenSearch Serverless?
5. **AWS 계정 · 리전** — 배포 대상 계정, 그리고 `ap-northeast-2`의 Claude 모델 액세스와 `us-west-2`의 Gemma 액세스가 활성화되어 있는지?
6. **Neptune 예산** — 시연 기간 상시 가동 가능한가, 직전에만 띄울까?
7. **시연 시간** — 15분 기준으로 설계했다. 30분이면 시나리오를 늘릴 수 있다.
8. **데모 계정 비밀번호** — `DEMO_USER_PASSWORD`로 주입한다. 채팅으로 전달된 값은 교체 권장.

---

## 15. 데모 ↔ 제안서 대응

| 데모 화면 | 뒷받침하는 슬라이드 |
|---|---|
| S1 규정 영향 분석 | "Vector RAG와 GraphRAG는 다른 질문에 답합니다" |
| S1 + 온톨로지 탐색기 | "AI-Ready 데이터는 두 계층입니다" |
| S2 마이데이터 상담 | "과제 2 아키텍처", "정확성 문제는 두 방향으로 실패합니다" |
| S3 Registry | "사내 AI 자산 거버넌스", "자산 공유 체계의 3대 원칙" |
| UX Asset Portal | "과제 1 — AI-Readable 화면/에셋 관리" |
| S4 Single Boundary 뷰 | "설계 원칙 — 경계는 하나", "추론 경로와 데이터 소재" |
| S5 Guardrails | "규제 판단 — 추론 경로를 명시한다" |
| Reader / Writer 분리 | "과제 3 아키텍처" |

---

## 부록. 데모 접속
- URL: 배포 후 `platform/README.md`의 라이브 URL 참조 (`d1twhttjtzqewp`는 이전 컨트롤룸 데모 URL)
- 계정: `demo@atomai.click`
- 비밀번호: 환경변수 `DEMO_USER_PASSWORD` / Secrets Manager `bank-platform/demo-user` (이 문서에 기록하지 않는다)
- Cognito 사용자 풀에 데모 계정 1개만 생성, 셀프 가입 비활성화

---

## 16. 구현 메모 (2026-09-02 — 명세 수령 시점의 검증 결과)

### §14 확정 답변 (2026-09-02, 발주자)

1. **에이전트 SDK: Strands Agents** — Harness 컨테이너·화면 생성 에이전트의 구현 SDK.
2. **AgentCore 사용 범위** (설계 위임에 따른 확정): **실행 = AgentCore Harness(관리형),
   도구 = AgentCore Gateway(MCP, IAM 인바운드), 자산 승인 = Agent Registry** 를 실사용한다.
   경계: Tier 2(PII 추론) 경로는 AgentCore를 태우지 않고 자체 `LLMClient` 어댑터로 직행한다(§11-4).
   AgentCore insights·Evaluations·Policy는 Tier 0/1 화면에 한정하고 배지를 단다.
   근거 — 이 조합이 "관리형 거버넌스(CloudTrail 감사·승인 수명주기)"라는 제안 논지를 실물로 보여주면서
   §11-4의 리전 제약을 위반하지 않는 유일한 경계선이다.
3. **벡터 저장소: OpenSearch Serverless** (v1 답변 유지 — §10 기본 제안 pgvector를 대체).


- §4-1: 서울(ap-northeast-2)에서 `global.anthropic.claude-sonnet-5`, `global.anthropic.claude-opus-5` 추론 프로파일 ACTIVE 확인. `apac.anthropic.claude-sonnet-4-*` 프로파일도 ACTIVE로 존재한다(APAC 프로파일 미지원이라는 문구와 다름) — 명세에 따라 global 프로파일을 기본으로 쓰고, 트레이스에 실제 모델 ID를 기록한다.
- §4-2: us-west-2 표준 카탈로그(`list-foundation-models`)에는 `google.gemma-3-{4b,12b,27b}-it`만 보인다. `google.gemma-4-31b`는 bedrock-mantle 카탈로그에서 런타임에 확인한다(어댑터 헬스체크). 장기 API 키 시크릿은 계정에 없다 → 어댑터는 IAM 자격으로 단기 Bearer 토큰(12h)을 발급해 사용하고, 장기 키가 Secrets Manager에 있으면 그것을 우선한다.
- §11-2: 데모는 규칙 기반 토큰화(마스킹 게이트)를 **구현했다**. 배지는 사실대로 "합성데이터 가명 생성 + 규칙 기반 토큰화 (ML 가명처리·재식별 볼트 미구현)"로 표기한다.
