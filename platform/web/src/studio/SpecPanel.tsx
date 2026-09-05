// platform/web/src/studio/SpecPanel.tsx
import { Spec } from './types';

export default function SpecPanel({ spec, loading, error }: { spec: Spec | null; loading: boolean; error?: string }) {
  if (loading) return <div className="text-xs text-slate-400">체크리스트 미리보기 로드 중…</div>;
  if (error) return <div className="text-xs text-[#E90061]">{error}</div>;
  if (!spec) return <div className="text-xs text-slate-400">상품을 선택하면 검수 체크리스트를 미리 봅니다.</div>;
  const req = spec.items.filter(i => i.required).length;
  return (
    <div className="text-xs space-y-2">
      <div className="flex items-center gap-2">
        <b className="text-slate-800">{spec.productName}</b>
        <span className="chip text-[10px]">{spec.category}</span>
        {spec.hasPreferential
          ? <span className="chip text-[10px] text-amber-700 border-amber-300 bg-amber-50">우대 조건 → 조건 입력 스텝 필수</span>
          : <span className="chip text-[10px] text-slate-500">우대 조건 없음 → 추가 스텝 금지</span>}
      </div>
      <div className="text-slate-600">절차: {spec.steps.map((s, i) => `${i + 1}.${s.name}`).join(' → ')}</div>
      <div className="text-slate-600">조건: {spec.conditions.map(c => `[${c.type}] ${c.name}`).join(' · ')}</div>
      <div className="text-slate-500">검수 항목 {spec.items.length}개 (필수 {req}) · 결정적 {spec.items.filter(i => i.check !== 'llm').length} · 리뷰어 {spec.items.filter(i => i.check === 'llm').length}</div>
      <details><summary className="cursor-pointer text-slate-500">항목 보기</summary>
        <ul className="mt-1 space-y-0.5 max-h-56 overflow-auto">
          {spec.items.map(i => <li key={i.id} className="text-slate-600">{i.required ? '● ' : '○ '}{i.text} <span className="text-slate-400">[{i.category}·{i.check}]</span></li>)}
        </ul>
      </details>
    </div>
  );
}
