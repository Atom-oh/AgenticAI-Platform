import { DocumentBoundary } from './documents/DocumentScope';
import ImpactAnalysis from './documents/ImpactAnalysis';

export const S1_PRESET = '전세자금대출 담보 인정 규정이 개정되면 영향받는 상품 · 화면 · 컴포넌트 · 담당부서 · 수정이 필요한 문서는?';

export default function S1() {
  return <DocumentBoundary view="s1"><ImpactAnalysis preset={S1_PRESET} /></DocumentBoundary>;
}
