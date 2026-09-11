export type ModelOption = { id: string; label: string; provider: string };

export default function ModelSelect({ id, value, models, onChange, disabled = false }: {
  id: string; value: string; models: ModelOption[]; onChange: (model: string) => void; disabled?: boolean;
}) {
  const known = models.some(model => model.id === value);
  return (
    <div className="my-4">
      <label htmlFor={id} className="block text-sm font-bold text-slate-800 mb-1">AI 모델</label>
      <select id={id} value={value} onChange={event => onChange(event.target.value)}
        disabled={disabled || !models.length}
        className="w-full px-3 py-2 rounded-xl bg-white border border-slate-300 text-sm disabled:opacity-50">
        {!known && <option value={value}>{value ? '현재 모델을 다시 선택하세요' : '모델 목록 확인 중'}</option>}
        {models.map(model => <option key={model.id} value={model.id}>{model.label} · {model.provider}</option>)}
      </select>
      <p className="text-xs text-slate-500 mt-1">허용된 Bedrock 연결을 사용합니다. 선택한 모델로 생성·수정·정적 검수를 진행합니다.</p>
    </div>
  );
}
