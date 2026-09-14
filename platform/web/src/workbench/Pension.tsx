import { useState } from 'react';
import { resource, waitForJob, wb } from './client';
import { ActionState, array, Details, Empty, Field, label, LoadState, object, Section, Status, text, useAction, useLoad, useWorkbench } from './shared';
import type { Job, JsonRecord, PensionSession, Persona } from './types';

const FINANCIAL_LABELS: Record<string, string> = {
  age: '현재 나이', retirementAge: '은퇴 나이', lifeExpectancy: '예상 수명', monthlyContribution: '월 납입액 (원)',
  monthlyContributionKrw: '월 납입액 (원)', annualReturnRate: '연 수익률', annualReturnPct: '연 수익률 (%)',
  annualReturn: '연 수익률', inflationRate: '물가상승률', inflationPct: '물가상승률 (%)',
  targetMonthlyIncome: '목표 월 생활비', targetMonthlyIncomeKrw: '목표 월 생활비 (원)',
  monthlyTarget: '목표 월 생활비 (원)', retirementYears: '은퇴 후 기간', yearsToRetirement: '은퇴까지 남은 연수',
  currentBalance: '현재 연금 잔액', totalBalance: '연금 총 잔액 (원)', projectedBalance: '은퇴 시 예상 잔액 (원)',
  projectedBalanceKrw: '은퇴 시 예상 잔액 (원)', monthlyPension: '단순 월 수령액 (원)', monthlyPensionKrw: '예상 월 연금 (원)',
  expectedMonthlyPension: '예상 월 연금', monthlyGap: '월 생활비 부족액 (원)', monthlyGapKrw: '월 생활비 부족액 (원)',
  gap: '생활비 부족액', targetMonthlySpending: '목표 월 생활비', monthlyIncome: '월 소득', nationalPension: '국민연금',
  publicPensionMonthlyKrw: '국민연금 월 수령액 (원)', pensionBalanceKrw: '연금 잔액 (원)', monthlyWithdrawalKrw: '월 인출액 (원)',
  pensionYears: '연금 수령 기간', withdrawalYears: '수령 기간 (년)', annualReturnBps: '연 수익률 (bp)',
  monthlySurplus: '월 생활비 여유분 (원)', totalContributions: '추가 적립 원금 (원)',
};
const moneyLabel = (key: string) => FINANCIAL_LABELS[key] || key;
export default function Pension() {
  const { client, params, navigate } = useWorkbench();
  const personas = useLoad(signal => client.get<{ personas: Persona[]; topics: Record<string, string>; questions: Record<string, string>;
    feedbackComments?: Record<string, string>; feedbackFreeTextAvailable?: boolean }>(wb('/pension/personas'), signal), [client]);
  const sessionId = params.get('sessionId') || '';
  const loaded = useLoad(signal => sessionId ? client.get<{ session: PensionSession }>(wb(`/pension/sessions/${resource(sessionId)}`), signal) : Promise.resolve(null), [client, sessionId]);
  const [current, setCurrent] = useState<PensionSession | null>(null), [selected, setSelected] = useState('');
  const action = useAction();
  const session = current?.id === sessionId ? current : loaded.data?.session;
  const persona = session?.persona || personas.data?.personas.find(item => item.id === (session?.personaId || selected));
  function saved(value: PensionSession) { setCurrent(value); navigate('pension', { sessionId: value.id }); }
  return <div className="wb-stack"><div className="wb-disclosure"><span>가상 데이터 기반 PoC</span>실제 고객 계좌·거래에 연결되지 않습니다. 금액은 저장된 계산 기준과 가정으로 확인하세요.</div>
    {!sessionId ? <Section title="상담할 가상 페르소나 선택" description="상담 세션별로 같은 페르소나와 계산 기준을 유지합니다.">
      <LoadState state={personas}><div className="wb-personas">{personas.data?.personas.map((item, index) =>
        <button className={`wb-persona${selected === item.id ? ' is-selected' : ''}`} aria-pressed={selected === item.id} key={item.id} onClick={() => setSelected(item.id)}>
          <span className="wb-avatar" aria-hidden="true">{index + 1}</span><strong>{item.name || item.title || item.id}</strong>
          <span>{item.ageBand ? text(item.ageBand) : item.age !== undefined ? `${item.age}세` : ''} · {text(item.stage, '')}</span>
          <p>{item.description || text(item.summary, '서버가 제공하는 가상 상담 사례')}</p></button>)}</div>
        {!personas.data?.personas.length && <Empty>사용 가능한 가상 페르소나가 없습니다.</Empty>}</LoadState>
      <button className="wb-primary" disabled={!selected || action.busy} onClick={() => void action.run(signal =>
        client.post<{ session: PensionSession }>(wb('/pension/sessions'), { personaId: selected, requestId: action.requestId(selected) }, signal),
      result => saved(result.session), '선택한 페르소나의 상담 세션을 만들었습니다.')}>이 페르소나로 상담 시작</button><ActionState action={action} />
    </Section> : session ? <>
      <div className="wb-row"><div><span className="wb-eyebrow">저장된 상담 v{session.version}</span><h2>{persona?.name || persona?.title || session.personaId}</h2></div>
        <div className="wb-actions"><button onClick={() => { setCurrent(null); navigate('pension', { sessionId: undefined }); }}>다른 페르소나</button>
          <button onClick={() => navigate('reports', { sessionId: session.id })}>상담 평가 보고서</button></div></div>
      <PensionDashboard key={session.id} session={session} onSaved={saved} topics={personas.data?.topics || {}} questions={personas.data?.questions || {}}
        feedbackComments={personas.data?.feedbackComments || {}} feedbackFreeTextAvailable={personas.data?.feedbackFreeTextAvailable === true} />
    </> : <LoadState state={loaded}><Empty>상담 세션을 찾지 못했습니다. 새로운 페르소나로 시작하세요.</Empty></LoadState>}
  </div>;
}
function PensionDashboard({ session, onSaved, topics, questions, feedbackComments, feedbackFreeTextAvailable }: {
  session: PensionSession; onSaved: (session: PensionSession) => void; topics: Record<string, string>; questions: Record<string, string>;
  feedbackComments: Record<string, string>; feedbackFreeTextAvailable: boolean;
}) {
  const { client } = useWorkbench();
  const [question, setQuestion] = useState(''), [topic, setTopic] = useState('diagnosis'), [mode, setMode] = useState('baseline');
  const action = useAction();
  const facts = session.calculation || session.dashboard || session.facts || object(session.summary);
  const numbers = Object.entries(facts).filter(([, value]) => typeof value === 'number');
  const insights = array(session.insights || object(session.dashboard).insights);
  const answers = array(session.answers);
  const lastAnswer = object(answers.at(-1));
  const accounts = array(session.persona?.accounts || session.accounts || object(session.dashboard).accounts || object(session.facts).accounts);
  const questionText = mode === 'baseline' ? questions[topic] || '' : question;
  return <>
    <Section title="연금 현황과 계산 결과" description="서버에 저장된 숫자를 표시합니다. 가정 변경은 아래 계산기에 반영하세요.">
      {numbers.length ? <dl className="wb-financial-metrics">{numbers.map(([key, value]) =>
        <div key={key}><dt>{moneyLabel(key)}</dt><dd>{Number(value).toLocaleString('ko-KR', { maximumFractionDigits: 4 })}</dd></div>)}</dl> :
        <p className="wb-muted">요약 금액이 응답에 포함되지 않았습니다. 계산 기준 상세를 확인하세요.</p>}
      {accounts.length > 0 && <div className="wb-table-wrap"><table><thead><tr><th>연금 계좌</th><th>유형</th><th>잔액</th></tr></thead>
        <tbody>{accounts.map((entry, index) => { const account = object(entry); return <tr key={text(account.id, String(index))}>
          <td>{text(account.name || account.label)}</td><td>{text(account.type || account.kind)}</td><td>{typeof (account.balanceKrw ?? account.balance) === 'number' ?
            Number(account.balanceKrw ?? account.balance).toLocaleString('ko-KR') + '원' : '미확인'}</td></tr>; })}</tbody></table></div>}
      {array(facts.caveats).length > 0 && <ul className="wb-muted">{array(facts.caveats).map((item, index) => <li key={index}>{text(item)}</li>)}</ul>}
      <Details title="계산 기준·정확한 금액·버전" value={{ calculation: session.calculation, dashboard: session.dashboard, facts: session.facts, assumptions: session.assumptions, version: session.version }} />
    </Section>
    <div className="wb-two-column"><Section title="은퇴 가정 계산기" description="서버가 제공한 가정을 변경하고 다시 계산합니다.">
      <AssumptionForm key={`${session.id}:${session.version}`} session={session} onSaved={onSaved} />
    </Section><Section title="함께 살펴볼 인사이트" description="궁금한 항목을 선택하면 상담 질문으로 이어집니다.">
      {insights.length ? insights.map((entry, index) => { const insight = typeof entry === 'string' ? { title: entry } : object(entry);
        return <button className="wb-insight" key={text(insight.id, String(index))} onClick={() => {
          setQuestion(text(insight.question || insight.title || insight.message, '이 계산 결과를 설명해 주세요.'));
          const selectedTopic = text(insight.topic || insight.id, '');
          if (topics[selectedTopic]) setTopic(selectedTopic);
          document.getElementById('wb-pension-question')?.focus();
        }}><span aria-hidden="true">↗</span><strong>{text(insight.title || insight.message)}</strong>
          {typeof insight.value === 'number' && <p>{insight.value.toLocaleString('ko-KR')}원</p>}<p>{text(insight.description || insight.detail, '')}</p></button>;
      }) : <Empty>이 사례의 저장된 인사이트가 없습니다. 아래에서 계산 결과에 대해 질문할 수 있습니다.</Empty>}
    </Section></div>
    <Section title="근거를 확인하는 연금 상담" description="응답 방식과 계산 기준을 확인한 뒤 상담 품질을 평가하세요.">
      <form onSubmit={event => { event.preventDefault(); void action.run(async (signal, update) => {
        const response = await client.post<{ answer?: JsonRecord; session: PensionSession; job?: Job }>(wb(`/pension/sessions/${resource(session.id)}/ask`),
          { version: session.version, question: questionText.trim(), topic, mode }, signal);
        if (response.job) {
          const job = await waitForJob(client, response.job.id, { signal, onUpdate: update });
          const latest = await client.get<{ session: PensionSession }>(wb(`/pension/sessions/${resource(session.id)}`), signal);
          const producedId = object(job.result?.answer).id;
          const answer = object(array(latest.session.answers).find(item => object(item).id === producedId));
          if (!Object.keys(answer).length) throw new Error('작업이 완료되었지만 저장된 상담 답변을 확인하지 못했습니다.');
          return { ...latest, answer };
        }
        if (!response.answer || !response.session) throw new Error('저장된 답변과 상담 세션을 확인하지 못했습니다.');
        return response;
      }, result => { onSaved(result.session); setQuestion(''); }, '상담 답변을 저장했습니다. 응답 방식과 근거를 확인하세요.'); }}>
        <fieldset disabled={action.busy}><div className="wb-form-grid"><Field label="상담 주제"><select value={topic} onChange={event => setTopic(event.target.value)}>
          {Object.entries(topics).map(([key, title]) => <option value={key} key={key}>{title}</option>)}</select></Field>
          <Field label="요청할 응답 방식"><select value={mode} onChange={event => setMode(event.target.value)}>
            <option value="baseline">저장된 사실 기반 기준 응답</option><option value="model">모델로 관련 지표 선택</option></select></Field></div>
          {mode === 'baseline' && <p className="wb-muted">기준 응답은 선택한 주제에 대해 계산 사실을 안내합니다. 자유 입력 질문을 모델이 해석한 응답이 아닙니다.</p>}
          {mode === 'model' && <p className="wb-muted">모델은 질문에 관련된 지표를 선택합니다. 지표 이름·금액·원(KRW) 단위는 서버가 저장된 계산값으로 표시합니다.</p>}
          <Field label="상담 질문"><textarea id="wb-pension-question" required maxLength={500} rows={3} value={questionText} readOnly={mode === 'baseline'}
            onChange={event => setQuestion(event.target.value)} placeholder="현재 가정에서 은퇴 후 생활비를 어떻게 살펴보면 좋을까요?" /></Field>
          <button className="wb-primary" disabled={!questionText.trim()}>상담 질문 보내기</button></fieldset>
      </form><ActionState action={action} />
      {Object.keys(lastAnswer).length > 0 && <div className="wb-answer"><div className="wb-row"><h3>저장된 상담 답변</h3>
        {(lastAnswer.mode || lastAnswer.responseMode) === 'model' ? <span className="wb-status">모델 선택 지표·서버 계산</span> :
          <Status value={text(lastAnswer.mode || lastAnswer.responseMode, 'unknown')} />}</div>
        {lastAnswer.stale === true && <p className="wb-error-text" role="status">이 답변 이후 계산 가정이 바뀌었습니다. 현재 금액으로 다시 질문하세요.</p>}
        <p className="wb-prose">{text(lastAnswer.text || lastAnswer.answer || lastAnswer.content, '답변 본문이 응답에 포함되지 않았습니다.')}</p>
        <Details title="응답 방식·계산 근거·경계 검증" value={lastAnswer} />
        <Feedback key={text(lastAnswer.id, `${session.id}:${session.version}`)} sessionId={session.id}
          answerId={typeof lastAnswer.id === 'string' ? lastAnswer.id : undefined} onSaved={onSaved}
          comments={feedbackComments} freeTextAvailable={feedbackFreeTextAvailable} />
      </div>}
    </Section>
  </>;
}
function AssumptionForm({ session, onSaved }: { session: PensionSession; onSaved: (session: PensionSession) => void }) {
  const { client } = useWorkbench(), action = useAction();
  const assumptions = session.assumptions || object(session.facts?.assumptions);
  const [values, setValues] = useState<JsonRecord>(assumptions);
  const editable = Object.entries(values).filter(([, value]) => typeof value === 'number' || typeof value === 'string');
  return <><form onSubmit={event => { event.preventDefault(); void action.run(signal => client.post<{ session: PensionSession }>(
    wb(`/pension/sessions/${resource(session.id)}/calculate`), { version: session.version, assumptions: values }, signal), result => onSaved(result.session), '변경한 가정으로 계산하고 저장했습니다.'); }}>
    <fieldset disabled={action.busy}><div className="wb-form-grid">{editable.map(([key, value]) =>
      <Field label={moneyLabel(key)} key={key} hint={key === 'annualReturnBps' ? '100bp = 1%. 확정 수익이 아닌 계산 가정입니다.' : undefined}><input required type={typeof assumptions[key] === 'number' ? 'number' : 'text'} step="1"
        value={text(value, '')} onChange={event => setValues(current => ({ ...current, [key]: typeof assumptions[key] === 'number' ?
          event.target.value === '' ? '' : Number(event.target.value) : event.target.value }))} /></Field>)}</div>
      {!editable.length && <Empty>이 세션에서 변경 가능한 가정이 제공되지 않았습니다.</Empty>}
      <button className="wb-primary" disabled={!editable.length}>가정 적용·다시 계산</button></fieldset>
  </form><ActionState action={action} /></>;
}
function Feedback({ sessionId, answerId, onSaved, comments, freeTextAvailable }: {
  sessionId: string; answerId?: string; onSaved: (session: PensionSession) => void; comments: Record<string, string>; freeTextAvailable: boolean;
}) {
  const { client } = useWorkbench(), action = useAction();
  const [rating, setRating] = useState(''), [commentCode, setCommentCode] = useState(''), [comment, setComment] = useState('');
  const [saved, setSaved] = useState<JsonRecord | null>(null);
  return <div className="wb-feedback"><h4>직원 상담 평가</h4><form onSubmit={event => { event.preventDefault(); void action.run(async signal => {
    const result = await client.post<{ feedback: JsonRecord }>(wb(`/pension/sessions/${resource(sessionId)}/feedback`),
      { rating: Number(rating), ...(commentCode ? { commentCode } : {}),
        ...(freeTextAvailable && comment.trim() ? { comment: comment.trim() } : {}), ...(answerId ? { answerId } : {}) }, signal);
    const latest = await client.get<{ session: PensionSession }>(wb(`/pension/sessions/${resource(sessionId)}`), signal);
    return { ...result, ...latest };
  }, result => { setSaved(result.feedback); setComment(''); onSaved(result.session); }, '평가 의견을 서버에 저장했습니다.'); }}>
    <fieldset disabled={action.busy}><div className="wb-form-grid"><Field label="상담 만족도"><select required value={rating} onChange={event => setRating(event.target.value)}>
      <option value="">점수 선택</option>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}점</option>)}</select></Field>
      <Field label="평가 의견" hint="선택한 평가 문구는 서버에 정의된 내용으로 저장됩니다."><select value={commentCode} onChange={event => setCommentCode(event.target.value)}>
        <option value="">선택하지 않음 · 평점만 저장</option>{Object.entries(comments).map(([code, title]) => <option key={code} value={code}>{title}</option>)}
      </select></Field></div>
      {freeTextAvailable && <details><summary>추가 의견 직접 입력</summary><Field label="추가 평가 의견"
        hint="사내 개인정보 처리 서비스로 검사한 뒤 저장합니다. 직접 입력하면 선택 문구 대신 검사된 내용을 저장하며, 서비스가 준비되지 않았으면 저장이 차단됩니다.">
        <textarea rows={2} maxLength={1000} value={comment} onChange={event => setComment(event.target.value)} />
      </Field></details>}
      <button disabled={!rating || !answerId}>평가 저장</button></fieldset></form><ActionState action={action} />{saved && <Details title="저장된 평가 기록" value={saved} />}</div>;
}
