// Agentic AI Platform — 은행 여신 도메인 온톨로지 스키마 (SPEC §4)
// 합성데이터 전용. 실제 상품명·내규 조항을 사용하지 않는다 (SPEC §8).
// Neptune openCypher 기준. LocalGraphStore는 이 스키마를 동일하게 따른다.

// ---------- 노드 유일성 제약 ----------
CREATE CONSTRAINT regulation_code IF NOT EXISTS
  FOR (r:Regulation) REQUIRE r.code IS UNIQUE;
CREATE CONSTRAINT amendment_id IF NOT EXISTS
  FOR (a:RegulationAmendment) REQUIRE a.amendmentId IS UNIQUE;
CREATE CONSTRAINT product_code IF NOT EXISTS
  FOR (p:Product) REQUIRE p.productCode IS UNIQUE;
CREATE CONSTRAINT condition_id IF NOT EXISTS
  FOR (c:Condition) REQUIRE c.conditionId IS UNIQUE;
CREATE CONSTRAINT screen_id IF NOT EXISTS
  FOR (s:Screen) REQUIRE s.screenId IS UNIQUE;
CREATE CONSTRAINT component_id IF NOT EXISTS
  FOR (c:Component) REQUIRE c.componentId IS UNIQUE;
CREATE CONSTRAINT dept_code IF NOT EXISTS
  FOR (d:Department) REQUIRE d.deptCode IS UNIQUE;
CREATE CONSTRAINT doc_id IF NOT EXISTS
  FOR (d:Document) REQUIRE d.docId IS UNIQUE;
CREATE CONSTRAINT template_id IF NOT EXISTS
  FOR (t:Template) REQUIRE t.templateId IS UNIQUE;
CREATE CONSTRAINT customer_id IF NOT EXISTS
  FOR (c:Customer) REQUIRE c.customerId IS UNIQUE;
CREATE CONSTRAINT account_id IF NOT EXISTS
  FOR (a:Account) REQUIRE a.accountId IS UNIQUE;
CREATE CONSTRAINT merchant_id IF NOT EXISTS
  FOR (m:Merchant) REQUIRE m.merchantId IS UNIQUE;

// ---------- 관계 타입 (SPEC §4.2) ----------
// (Regulation)-[:APPLIES_TO]->(Product)
// (Regulation)-[:AMENDED_BY]->(RegulationAmendment)
// (Regulation)-[:SUPERSEDES]->(Regulation)
// (Product)-[:HAS_CONDITION]->(Condition)
// (Condition)-[:DERIVED_FROM]->(Regulation)      // 조건의 규정 근거 — S1 핵심 엣지
// (Condition)-[:EXCLUDES]->(Merchant)
// (Condition)-[:REQUIRES]->(Condition)
// (Product)-[:SOLD_VIA]->(Screen)
// (Screen)-[:USES]->(Component)
// (Screen)-[:OWNED_BY]->(Department)
// (Product)-[:OWNED_BY]->(Department)
// (Document)-[:FOLLOWS]->(Template)
// (Document)-[:REFERENCES]->(Regulation)
// (Document)-[:OWNED_BY]->(Department)
// (Customer)-[:HOLDS]->(Account)
// (Account)-[:OF_PRODUCT]->(Product)
// (Account)-[:TRANSACTED_AT]->(Merchant)
// (Component)-[:SUPERSEDED_BY]->(Component)      // 컴포넌트 버전 체인 — S3 반전의 근거

// ---------- S1 규정 영향 분석 순회 (SPEC §4.3, 반드시 동작) ----------
// MATCH (r:Regulation {code: $regCode})
// OPTIONAL MATCH (r)<-[:DERIVED_FROM]-(c:Condition)<-[:HAS_CONDITION]-(p:Product)
// OPTIONAL MATCH (p)-[:SOLD_VIA]->(s:Screen)-[:USES]->(comp:Component)
// OPTIONAL MATCH (p)-[:OWNED_BY]->(pd:Department)
// OPTIONAL MATCH (s)-[:OWNED_BY]->(sd:Department)
// OPTIONAL MATCH (d:Document)-[:REFERENCES]->(r)
// RETURN r, collect(DISTINCT c) AS conditions, collect(DISTINCT p) AS products,
//        collect(DISTINCT s) AS screens, collect(DISTINCT comp) AS components,
//        collect(DISTINCT pd) + collect(DISTINCT sd) AS departments,
//        collect(DISTINCT d) AS documents
