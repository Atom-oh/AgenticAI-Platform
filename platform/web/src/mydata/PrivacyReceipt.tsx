import './privacy.css';

export type PrivacyEvidence = {
  status?: string; processor?: string; model?: string; modelId?: string; modelRevision?: string;
  method?: string; entityCounts?: { type: string; count: number }[]; total?: number;
  sourceChars?: number; outputChars?: number; ruleResidualCount?: number;
  independentNer?: string; latencyMs?: number; promptVersion?: string; sanitizedPreview?: string;
  detectors?: string[]; modelInvoked?: boolean;
};

export function PrivacyReceipt({ step, evidence }: { step: string; evidence: PrivacyEvidence }) {
  if (step === 'privacy_payload') {
    const checked = evidence.processor === 'schema-and-independent-scan' && evidence.status === 'pass' && evidence.method === 'structured-tokenization'
      && evidence.modelInvoked === false && evidence.ruleResidualCount === 0
      && evidence.detectors?.includes('rules') && evidence.detectors?.includes('guardrail');
    return <section aria-label="설명 자료 최종 개인정보 검사" className="mydata-privacy panel p-3 mb-2">
      <h3 className="text-xl font-semibold">설명 자료 최종 개인정보 검사</h3>
      <p className={checked ? 'text-emerald-700' : 'text-amber-800'}>{checked ? '전체 전달 자료 검사 통과' : '검사 상태 미확인'}</p>
      <p>정형 고객정보는 내부 코드의 정해진 필드 규칙으로 토큰화합니다.
        계산 결과와 상품 조건을 생성 모델로 다시 분류하거나 수정하지 않습니다.</p>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-1">
        <dt>독립 검사</dt><dd>규칙 + Bedrock Guardrails</dd>
        <dt>검사 자료</dt><dd>{evidence.sourceChars ?? '—'}자</dd>
        <dt>잔여 식별자</dt><dd>{evidence.ruleResidualCount ?? '미확인'}건</dd>
      </dl>
    </section>;
  }
  const title = step === 'privacy_input' ? '질문 프라이버시 처리' : '설명 페이로드 프라이버시 처리';
  const passed = evidence.status === 'pass' && evidence.processor === 'eks-sllm'
    && evidence.method === 'redaction' && evidence.ruleResidualCount === 0
    && !!evidence.modelId && !!evidence.modelRevision
    && ['not-configured', 'pass'].includes(evidence.independentNer || '');
  const counts = Array.isArray(evidence.entityCounts) ? evidence.entityCounts : [];
  return (
    <section aria-label={title} className="mydata-privacy panel p-3 mb-2 break-words"
      style={{ borderLeft: '3px solid var(--onprem)' }}>
      <div className="flex gap-2 items-center flex-wrap mb-2">
        <h3 className="text-xl font-semibold">{title}</h3>
        <span className={`chip ${passed ? 'text-emerald-700 border-emerald-300' : 'text-amber-800 border-amber-300'}`}>
          {passed ? '처리 통과' : evidence.status === 'blocked' ? '처리 차단' : '처리 상태 미확인'}
        </span>
        <span className="chip ml-auto">VPC 내부</span>
      </div>
      <p className="mb-2">개체명 인식(NER, Named Entity Recognition)은 문장에서 이름·주소 등을 찾는 추가 검사입니다. 미구성은 검사를 수행하지 않았다는 뜻입니다.</p>
      <dl className="grid grid-cols-1 sm:grid-cols-[minmax(0,.9fr)_minmax(0,1.6fr)] gap-x-3 gap-y-1">
        <dt className="text-slate-500">처리기</dt><dd>{evidence.processor || '미확인'}</dd>
        <dt className="text-slate-500">실행 모델</dt><dd className="break-all">{evidence.model || '미확인'} · {evidence.modelId || '미확인'}</dd>
        <dt className="text-slate-500">모델 리비전</dt><dd className="break-all">{evidence.modelRevision || '미확인'}</dd>
        <dt className="text-slate-500">처리 방식</dt><dd>{evidence.method === 'redaction' ? '식별자 제거 (redaction)' : '미확인'}</dd>
        <dt className="text-slate-500">탐지 유형·건수</dt><dd>{counts.map((item, i) => <span key={i} className="mr-2">{item.type} ×{item.count}</span>)}총 {evidence.total ?? '미확인'}건</dd>
        <dt className="text-slate-500">문자 수</dt><dd>{evidence.sourceChars ?? '—'}자 → {evidence.outputChars ?? '—'}자</dd>
        <dt className="text-slate-500">규칙 잔여 식별자</dt><dd>{evidence.ruleResidualCount ?? '미확인'}건</dd>
        <dt className="text-slate-500">독립 NER</dt><dd>{evidence.independentNer === 'pass' ? '통과' : evidence.independentNer === 'not-configured' ? '미구성' : '미확인'}</dd>
        <dt className="text-slate-500">처리 지연</dt><dd>{evidence.latencyMs == null ? '미확인' : `${evidence.latencyMs}ms`}</dd>
        <dt className="text-slate-500">프롬프트 버전</dt><dd className="break-all">{evidence.promptVersion || '미확인'}</dd>
      </dl>
      {passed && typeof evidence.sanitizedPreview === 'string' && (
        <div className="mt-2">
          <p className="text-slate-500 mb-1">제거 후 미리보기 (서버 제공)</p>
          <pre className="bg-slate-50 rounded p-2 whitespace-pre-wrap break-all max-h-48 overflow-y-auto">{evidence.sanitizedPreview}</pre>
        </div>
      )}
    </section>
  );
}

export function PrivacyBlocked({ message, code, stage, types }: { message?: string; code?: string; stage?: string; types?: string[] }) {
  return (
    <div role="alert" className="mydata-privacy text-rose-700 border border-rose-300 rounded-lg p-3">
      <div className="font-semibold">프라이버시 처리 차단</div>
      {stage && <p>차단 단계: {({ input: '질문 검사', payload: '설명 자료 검사', output: '생성된 답변 검사' } as Record<string, string>)[stage] || '검사 단계 미확인'}</p>}
      {!!types?.length && <p>검출 유형: {types.join(', ')}</p>}
      <p className="mt-1">{message || '프라이버시 처리를 확인하지 못해 상담을 중단했습니다.'}</p>
      {code && <p className="mt-1 break-all">오류 코드: {code}</p>}
      <p className="mt-1">실패한 단계의 입력은 다음 단계로 전달하지 않습니다.</p>
    </div>
  );
}
