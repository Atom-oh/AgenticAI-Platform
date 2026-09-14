/** Work areas are a navigation preference; API membership remains authoritative. */
export type MenuItem = { id: string; label: string };
export type MenuGroup = { id: string; label: string; items: MenuItem[]; operator?: boolean };
export const MENU_GROUPS: MenuGroup[] = [
  { id: 'planning', label: '기획자', items: [
    { id: 'wb-planning', label: '상품기획서' }, { id: 'wb-changes', label: '변경 요청 · 영향 분석' },
    { id: 'wb-knowledge', label: '업무 흐름 · 가이드' },
    { id: 's1', label: '규정 영향 검토' },
  ] },
  { id: 'design', label: '디자이너', items: [
    { id: 'studio', label: '디자인 스튜디오' }, { id: 'wb-deliverables', label: '디자인 산출물' },
    { id: 'portal', label: '디자인 시스템 · 자산' },
  ] },
  { id: 'development', label: '개발자', items: [
    { id: 'wb-components', label: 'React 컴포넌트' }, { id: 'wb-development', label: '개발 작업 · 검증' },
    { id: 'agents', label: 'AgentCore 에이전트 빌더' },
  ] },
  { id: 'business', label: '업무 담당자', items: [
    { id: 'wb-pension', label: '연금 상담' }, { id: 'wb-reports', label: '보고서 작업실' },
  ] },
  { id: 'common', label: '공통 작업', items: [
    { id: 'wb-development', label: '내 할 일 · 진행 현황' }, { id: 'wb-skills', label: 'Skill 제작실' },
    { id: 'wb-knowledge', label: '규정집 · 위키 검색' }, { id: 'guide', label: '설명 · 가이드북' },
    { id: 'documents', label: '내부 문서함' },
  ] },
  { id: 'operator', label: '운영 센터', operator: true, items: [
    { id: 'wb-sources', label: '지식 원본 등록' }, { id: 'wb-batches', label: '수집 · ETL 배치' },
    { id: 'wb-operations', label: '연결 · 운영 현황' },
    { id: 'wb-tools', label: 'MCP 도구 등록' },
  ] },
  { id: 'reference', label: '기술 데모 · 이전 시나리오', items: [
    { id: 'home', label: '플랫폼 대시보드' },
    { id: 's2', label: '마이데이터 경계 검증' }, { id: 'explore', label: '온톨로지 탐색기' },
    { id: 'registry', label: '기존 Registry' }, { id: 'screengen', label: '기존 화면 생성' },
    { id: 'report', label: '기존 보고서 생성' }, { id: 'boundary', label: '경계 측정' },
    { id: 'guardrails', label: 'Guardrails 이력' }, { id: 'controlroom', label: '레거시 컨트롤룸' },
  ] },
];
export const WORKBENCH_VIEWS = new Set(MENU_GROUPS.flatMap(group => group.items)
  .map(item => item.id).filter(id => id.startsWith('wb-')));
export const WORKBENCH_TITLES = Object.fromEntries(MENU_GROUPS.flatMap(group => group.items)
  .map(item => [item.id, item.label]));
