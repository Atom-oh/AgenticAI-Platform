// Agentic AI Platform — 온톨로지 스키마 v2 (SPEC v2 §5) — 여신 도메인(§5-1) + UX 자산 도메인(§5-2)
// 합성데이터 전용. 실제 상품명·내규 조항을 사용하지 않는다 (SPEC §9 · §12.10).
// Neptune openCypher 기준. LocalGraphStore 는 이 스키마를 동일하게 따른다 (graph/store.py).
// 모든 노드는 도메인 `id` 프로퍼티를 가지며(= 라벨별 자연키), 적재·조회는 항상 라벨을 지정해 MATCH 한다.
//
// 참고: Neptune openCypher 는 CREATE CONSTRAINT 를 지원하지 않는다. 아래 제약은 문서화 목적이며
// 유일성은 seed/generate.py 가 생성 시점에 보장한다 (node() 가 중복 id 를 assert).

// ---------- 노드 라벨 · 자연키 (여신 도메인 §5-1) ----------
// Regulation            code          — title, article, effectiveDate, version, status        (60)   REG-LN-### / REG-CS-### / REG-GN-###
// RegulationAmendment   amendmentId   — date, summary, diffType                              (25)   AMD-###
// Product               productCode   — name, category, launchDate, status                   (120)  PRD-LN-### / PRD-###
// Condition             conditionId   — type, operator, value, unit, priority                (800)  CND-####
// Department            deptCode      — name, role                                           (20)   D-XXX
// Document              docId         — title, type, deptCode, updatedAt                     (200)  DOC-###
// Template              templateId    — name, sections(JSON 배열 문자열)                      (12)   TPL-##
// Customer              customerId    — segment, joinDate            ※ 가명 토큰 (CUST-####)  (500)
// Account               accountId     — productCode, balance, openDate ※ 토큰 (ACCT-####)     (1,200)
// Merchant              merchantId    — name, mccCode, category                              (150)  MRC-###

// ---------- 노드 라벨 · 자연키 (UX 자산 도메인 §5-2 — 6종 라이브러리) ----------
// Screen        screenId     — name, channel, route, status                                   (150)  SCR-###
// Component     componentId  — name, version, approvalStatus, propsSchema(JSON 문자열), owner  (80)   CMP-<Name>-v# / CMP-GEN-##
// Pattern       patternId    — name, category, status                                         (40)   PAT-###   Pattern Library
// Procedure     procedureId  — name, steps(JSON 배열 문자열), status                           (30)   PRC-###   Procedure Library
// PolicyRule    ruleId       — title, ruleType, severity, status                              (60)   POL-###   Policy Rule
// UXTerm        termId       — term, definition, category                                     (200)  TRM-####  UX Dictionary
// ScreenMeta    screenNo     — purpose, entryCondition, prevScreens(JSON), nextScreens(JSON)   (150)  SM-###    Screen Metadata (화면당 1건)
//
// 합계 목표: 노드 약 3,800 / 관계 약 11,000 (seed/generate.py 실행 결과가 정본 — 테스트가 검증)

CREATE CONSTRAINT regulation_code IF NOT EXISTS FOR (r:Regulation) REQUIRE r.code IS UNIQUE;
CREATE CONSTRAINT amendment_id IF NOT EXISTS FOR (a:RegulationAmendment) REQUIRE a.amendmentId IS UNIQUE;
CREATE CONSTRAINT product_code IF NOT EXISTS FOR (p:Product) REQUIRE p.productCode IS UNIQUE;
CREATE CONSTRAINT condition_id IF NOT EXISTS FOR (c:Condition) REQUIRE c.conditionId IS UNIQUE;
CREATE CONSTRAINT dept_code IF NOT EXISTS FOR (d:Department) REQUIRE d.deptCode IS UNIQUE;
CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.docId IS UNIQUE;
CREATE CONSTRAINT template_id IF NOT EXISTS FOR (t:Template) REQUIRE t.templateId IS UNIQUE;
CREATE CONSTRAINT customer_id IF NOT EXISTS FOR (c:Customer) REQUIRE c.customerId IS UNIQUE;
CREATE CONSTRAINT account_id IF NOT EXISTS FOR (a:Account) REQUIRE a.accountId IS UNIQUE;
CREATE CONSTRAINT merchant_id IF NOT EXISTS FOR (m:Merchant) REQUIRE m.merchantId IS UNIQUE;
CREATE CONSTRAINT screen_id IF NOT EXISTS FOR (s:Screen) REQUIRE s.screenId IS UNIQUE;
CREATE CONSTRAINT component_id IF NOT EXISTS FOR (c:Component) REQUIRE c.componentId IS UNIQUE;
CREATE CONSTRAINT pattern_id IF NOT EXISTS FOR (p:Pattern) REQUIRE p.patternId IS UNIQUE;
CREATE CONSTRAINT procedure_id IF NOT EXISTS FOR (p:Procedure) REQUIRE p.procedureId IS UNIQUE;
CREATE CONSTRAINT policy_rule_id IF NOT EXISTS FOR (p:PolicyRule) REQUIRE p.ruleId IS UNIQUE;
CREATE CONSTRAINT ux_term_id IF NOT EXISTS FOR (t:UXTerm) REQUIRE t.termId IS UNIQUE;
CREATE CONSTRAINT screen_meta_no IF NOT EXISTS FOR (m:ScreenMeta) REQUIRE m.screenNo IS UNIQUE;

// ---------- 관계 타입 (SPEC v2 §5-3) ----------
// 여신
// (Regulation)-[:APPLIES_TO]->(Product)
// (Regulation)-[:AMENDED_BY]->(RegulationAmendment)
// (Regulation)-[:SUPERSEDES]->(Regulation)
// (Product)-[:HAS_CONDITION]->(Condition)
// (Condition)-[:DERIVED_FROM]->(Regulation)        // S1 핵심 엣지 — 조건의 규정 근거
// (Condition)-[:EXCLUDES]->(Merchant)
// (Condition)-[:REQUIRES]->(Condition)
// (Product)-[:OWNED_BY]->(Department)
// (Document)-[:REFERENCES]->(Regulation)
// (Document)-[:FOLLOWS]->(Template)
// (Document)-[:OWNED_BY]->(Department)
// (Customer)-[:HOLDS]->(Account)
// (Account)-[:OF_PRODUCT]->(Product)
// (Account)-[:TRANSACTED_AT]->(Merchant)
//
// UX 자산
// (Product)-[:SOLD_VIA]->(Screen)
// (Screen)-[:USES]->(Component)
// (Screen)-[:FOLLOWS]->(Pattern)                    // ※ FOLLOWS 는 Document→Template 과 공유 — 순회 시 라벨로 구분
// (Pattern)-[:COMPOSES]->(Component)
// (Procedure)-[:INCLUDES]->(Screen)                 // steps 순서 = 화면 순서 → ScreenMeta.prev/nextScreens 의 근거
// (Screen)-[:OWNED_BY]->(Department)
// (PolicyRule)-[:CONSTRAINS]->(Screen)
// (PolicyRule)-[:DERIVED_FROM]->(Regulation)        // 두 도메인 연결점 — S1 v2 순회가 이 경로를 탄다
// (Component)-[:SUPERSEDED_BY]->(Component)         // 컴포넌트 버전 사슬 — S3 반전 · version_chain()
// (ScreenMeta)-[:DESCRIBES]->(Screen)
// (UXTerm)-[:USED_IN]->(Screen)

// ---------- S1 규정 영향 분석 순회 (SPEC v2 §5-5, 반드시 동작) ----------
// GraphStore.impact_of_regulation(code) 가 이 질의와 같은 결과 집합을 돌려준다.
// screens = SOLD_VIA 화면 ∪ PolicyRule 제약 화면, components/departments 는 그 화면 전체에서 이어진다.
// MATCH (r:Regulation {code: $regCode})
// OPTIONAL MATCH (r)<-[:DERIVED_FROM]-(c:Condition)<-[:HAS_CONDITION]-(p:Product)
// OPTIONAL MATCH (r)<-[:DERIVED_FROM]-(pol:PolicyRule)-[:CONSTRAINS]->(s2:Screen)
// OPTIONAL MATCH (p)-[:SOLD_VIA]->(s:Screen)-[:USES]->(comp:Component)
// OPTIONAL MATCH (s2)-[:USES]->(comp2:Component)
// OPTIONAL MATCH (p)-[:OWNED_BY]->(pd:Department)
// OPTIONAL MATCH (s)-[:OWNED_BY]->(sd:Department)
// OPTIONAL MATCH (s2)-[:OWNED_BY]->(sd2:Department)
// OPTIONAL MATCH (d:Document)-[:REFERENCES]->(r)
// RETURN r,
//   collect(DISTINCT pol) AS policyRules,
//   collect(DISTINCT c) AS conditions,
//   collect(DISTINCT p) AS products,
//   collect(DISTINCT s) + collect(DISTINCT s2) AS screens,
//   collect(DISTINCT comp) + collect(DISTINCT comp2) AS components,
//   collect(DISTINCT pd) + collect(DISTINCT sd) + collect(DISTINCT sd2) AS departments,
//   collect(DISTINCT d) AS documents

// ---------- 컴포넌트 변경 영향 (SPEC v2 §5-4 두 번째 제약 · §8-2 "이 컴포넌트를 변경하면 영향받는 화면") ----------
// GraphStore.impact_of_component(componentId) 가 이 질의와 같은 결과 집합을 돌려준다.
// MATCH (comp:Component {componentId: $componentId})
// OPTIONAL MATCH (s:Screen)-[:USES]->(comp)
// OPTIONAL MATCH (s)-[:FOLLOWS]->(pat:Pattern)
// OPTIONAL MATCH (pat2:Pattern)-[:COMPOSES]->(comp)
// OPTIONAL MATCH (pol:PolicyRule)-[:CONSTRAINS]->(s)
// OPTIONAL MATCH (p:Product)-[:SOLD_VIA]->(s)
// OPTIONAL MATCH (s)-[:OWNED_BY]->(sd:Department)
// OPTIONAL MATCH (p)-[:OWNED_BY]->(pd:Department)
// OPTIONAL MATCH (prc:Procedure)-[:INCLUDES]->(s)
// RETURN comp,
//   collect(DISTINCT s) AS screens,
//   collect(DISTINCT pat) + collect(DISTINCT pat2) AS patterns,
//   collect(DISTINCT pol) AS policyRules,
//   collect(DISTINCT p) AS products,
//   collect(DISTINCT sd) + collect(DISTINCT pd) AS departments,
//   collect(DISTINCT prc) AS procedures

// ---------- Related 카운트 (SPEC v2 §8-2 — 그래프 순회 결과, 하드코딩 금지 §12.8) ----------
// GraphStore.related_counts(id): 양방향 이웃을 라벨별로 센다.
// MATCH (a:<Label> {id: $id})-[]-(m) RETURN head(labels(m)) AS label, count(DISTINCT m) AS c

// ---------- 버전 사슬 (SPEC v2 §8-2 Version History) ----------
// GraphStore.version_chain(id): SUPERSEDED_BY 를 양방향으로 따라가 과거→최신 순으로 정렬.
// MATCH p = (a:Component)-[:SUPERSEDED_BY*0..20]->(b:Component {componentId: $id}) ... (구현은 홉 단위 순회)

// ---------- 커버리지 제약 (SPEC v2 §5-4 — tests/test_coverage.py · tests/test_ontology_v2.py) ----------
// 히어로 규정 REG-LN-001 · REG-LN-014 · REG-CS-003 : products>=4, screens>=6, components>=8, departments>=3, documents>=5
// 히어로 컴포넌트 CMP-Button-v2 · CMP-Button-v3 · CMP-Input-v3 : screens>=12, patterns>=4, policyRules>=2
