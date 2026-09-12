import type { usePrivacyModels } from './usePrivacyModels';
import './privacy.css';

const EXAMPLES = [
  { label: '이름·주소', query: '저는 김하나이고 주소는 서울특별시 중구 가상로 123, 101동 202호입니다. 우대금리 조건을 확인해 주세요.' },
  { label: '전화·이메일', query: '연락처는 010-0000-0000, 이메일은 mydata@example.invalid입니다. 이 상품의 우대금리 조건을 알려 주세요.' },
  { label: '계좌·금액 보존', query: '계좌번호 123-456789-01234의 잔액 1,250,000원, 금리 3.5%, 기간 12개월을 기준으로 안내해 주세요.' },
];

export default function PrivacyControls({ models, running, onExample }: {
  models: ReturnType<typeof usePrivacyModels>; running: boolean; onExample: (query: string) => void;
}) {
  return (
    <section className="mydata-privacy panel p-3 mb-3" aria-label="MyData 프라이버시">
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <b className="text-xl">EKS sLLM 프라이버시 처리</b>
        <span className="chip text-amber-800 border-amber-300">항상 필수</span>
        <button type="button" className="chip ml-auto disabled:opacity-40" onClick={models.refresh}
          disabled={running || models.status === 'loading'}>모델 상태 새로고침</button>
      </div>
      <p className="text-slate-600 mb-2">
        자유 문장은 Bedrock Guardrails 호출 전에 EKS 모델로 처리합니다.
        정형 고객정보는 내부 고정 코드로 토큰화하고, 설명 자료 전체를 마지막에 독립 검사합니다.
        아래 프라이버시 모델과 설명용 모델은 별도로 선택합니다.
      </p>
      <fieldset>
        <legend className="font-semibold mb-2">필수 프라이버시 모델</legend>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          {models.models.map(model => (
            <label key={model.id} className={`min-w-0 rounded-lg border p-3 ${model.available ? 'border-amber-300' : 'border-slate-200 bg-slate-50 text-slate-500'}`}>
              <span className="flex items-center gap-2">
                <input type="radio" name="privacy-model" aria-label={model.label} value={model.id}
                  checked={models.selected === model.id} disabled={running || !model.available}
                  onChange={() => models.select(model.id)} />
                <b>{model.label}</b>
              </span>
              <span className={`block mt-1 ${model.available ? 'text-emerald-700' : 'text-amber-800'}`}>
                {model.available ? '사용 가능 · 실행 전' : '사용 불가'}
              </span>
              {model.available
                ? <span className="block mt-1 break-all text-slate-600">eks-sllm · {model.family}<br />{model.modelId}<br />리비전 {model.revision}</span>
                : <span className="block mt-1">{model.reason}</span>}
            </label>
          ))}
        </div>
      </fieldset>
      <div aria-live="polite" className="mt-2">
        {models.status === 'loading' ? <p>프라이버시 모델 확인 중…</p>
          : models.status === 'error' ? <p role="alert" className="text-rose-700">{models.message}</p>
            : <>
              {models.message && <p className="text-amber-800">{models.message}</p>}
              {!models.ready && <p className="text-amber-800">사용 가능한 프라이버시 모델이 없어 상담을 실행할 수 없습니다.</p>}
            </>}
      </div>
      <p className="mt-2 text-slate-600">
        새로 입력한 자유 텍스트 식별자는 제거하며 복원하지 않습니다.
        기존 신뢰 영역의 토큰은 고객 응답에서만 원래 값으로 복원할 수 있습니다.
        처리 통과는 표시된 탐지·검사 결과를 뜻하며 법적 익명성을 보증하지 않습니다.
      </p>
      <div className="flex gap-2 flex-wrap items-center mt-3">
        <span className="text-slate-500">합성 입력 예시</span>
        {EXAMPLES.map(example => <button key={example.label} type="button"
          aria-label={`합성 예시: ${example.label}`} disabled={running} onClick={() => onExample(example.query)}
          className="chip hover:border-teal-500 disabled:opacity-40">{example.label}</button>)}
        <span className="text-slate-500">가상 데이터 · 질문에 채운 뒤 상담 실행</span>
      </div>
    </section>
  );
}
