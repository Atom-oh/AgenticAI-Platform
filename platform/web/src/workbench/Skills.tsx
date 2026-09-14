import { useState } from 'react';
import { executeApprovedSkill, listWorkbench, readSkillExecution, resource, validateSkill, waitForJob, wb } from './client';
import { ActionState, Details, Empty, Field, LoadState, parseArray, Section, Status, useAction, useLoad, useWorkbench } from './shared';
import type { Job, JsonRecord, Skill, SkillExecution } from './types';

export default function Skills() {
  const { client, params, navigate } = useWorkbench();
  const state = useLoad(signal => listWorkbench<Skill>(client, '/skills', signal), [client]);
  const [selected, setSelected] = useState(params.get('skillId') || '');
  const [current, setCurrent] = useState<Skill | undefined>();
  const [goal, setGoal] = useState(''), [domain, setDomain] = useState('planning');
  const proposal = useAction();
  const skill = current?.id === selected ? current : state.data?.find(item => item.id === selected);
  function saved(value: Skill) { setCurrent(value); setSelected(value.id); navigate('skills', { skillId: value.id, executionId: undefined }); state.refresh(); }
  return <div className="wb-two-column">
    <Section title="프로젝트 Skill" description="초안부터 승인된 패키지까지 같은 버전을 추적합니다." action={<button onClick={() => { setSelected(''); setCurrent(undefined); navigate('skills', { skillId: undefined, executionId: undefined }); }}>새 Skill</button>}>
      <LoadState state={state}>{state.data?.length ? <div className="wb-record-list">{state.data.map(item =>
        <button className={`wb-record${selected === item.id ? ' is-selected' : ''}`} key={item.id} aria-pressed={selected === item.id}
          onClick={() => { setSelected(item.id); setCurrent(undefined); navigate('skills', { skillId: item.id, executionId: undefined }); }}>
          <strong>{item.title || item.name}</strong><span>v{item.version} · <Status value={item.status} /></span></button>)}</div> : <Empty>반복되는 업무를 첫 Skill로 정리하세요.</Empty>}</LoadState>
      <details className="wb-details"><summary>모델로 초안 제안받기</summary><p>사용 가능한 승인 모델이 있을 때 실행됩니다. 연결이 없으면 미설정 상태를 표시합니다.</p>
        <form onSubmit={event => { event.preventDefault(); void proposal.run(async (signal, update) => {
          const fields = { goal: goal.trim(), domain };
          const response = await client.post<{ skill: Skill; job?: Job }>(wb('/skills/propose'), { ...fields, requestId: proposal.requestId(JSON.stringify(fields)) }, signal);
          if (!response.job?.id) throw new Error('초안 생성 작업의 접수 기록이 없습니다. 목록에서 상태를 확인하세요.');
          const job = await waitForJob(client, response.job.id, { signal, onUpdate: update });
          const items = await listWorkbench<Skill>(client, '/skills', signal);
          const produced = items.find(item => item.id === (job.result?.skillId || response.skill?.id));
          if (!produced) throw new Error('완료된 Skill을 조회하지 못했습니다. 목록을 다시 조회하세요.');
          return produced;
        }, saved, '모델 작업을 완료하고 저장된 초안을 불러왔습니다.'); }}>
          <fieldset disabled={proposal.busy}><Field label="Skill 제안 목표"><textarea required rows={3} value={goal} onChange={event => setGoal(event.target.value)} placeholder="수행할 업무, 필요한 근거, 기대하는 결과" /></Field>
            <Field label="업무 분야"><select value={domain} onChange={event => setDomain(event.target.value)}><option value="planning">상품 기획</option><option value="design">UX 디자인</option>
              <option value="development">React 개발</option><option value="pension">연금 상담</option></select></Field><button disabled={!goal.trim()}>모델 초안 요청</button></fieldset>
        </form><ActionState action={proposal} /></details>
    </Section>
    <Section title={skill ? skill.title || skill.name : 'Skill 지침 편집'} description="내용 변경 시 기존 검증과 승인이 무효화됩니다. 저장한 버전을 다시 검증하세요.">
      {selected && !skill ? <LoadState state={state}><Empty>선택한 Skill을 찾지 못했습니다.</Empty></LoadState> :
        <SkillEditor key={`${skill?.id || 'new'}:${skill?.version || 0}`} skill={skill} onSaved={saved} />}
    </Section>
  </div>;
}
function SkillEditor({ skill, onSaved }: { skill?: Skill; onSaved: (skill: Skill) => void }) {
  const { client } = useWorkbench(), action = useAction();
  const [name, setName] = useState(skill?.name || ''), [title, setTitle] = useState(skill?.title || '');
  const [description, setDescription] = useState(skill?.description || ''), [instructions, setInstructions] = useState(skill?.instructions || '');
  const [refs, setRefs] = useState(JSON.stringify(skill?.sourceRefs || [], null, 2));
  const [tools, setTools] = useState(skill?.toolNames?.join(', ') || ''), [examples, setExamples] = useState(JSON.stringify(skill?.examples || [], null, 2));
  const [checked, setChecked] = useState(false), [pkg, setPkg] = useState<JsonRecord | null>(null);
  const fields = () => ({ name, title, description, instructions, sourceRefs: parseArray(refs, '원본 참조'), toolNames: tools.split(',').map(item => item.trim()).filter(Boolean), examples: parseArray(examples, '동작 예시') });
  const dirty = !skill || name !== skill.name || title !== skill.title || description !== skill.description || instructions !== skill.instructions ||
    refs !== JSON.stringify(skill.sourceRefs || [], null, 2) || tools !== (skill.toolNames?.join(', ') || '') || examples !== JSON.stringify(skill.examples || [], null, 2);
  return <>
    {skill && <div className="wb-row"><Status value={skill.status} /><span>저장된 버전 v{skill.version}</span></div>}
    <form onSubmit={event => { event.preventDefault(); void action.run(signal => skill ?
      client.put<{ skill: Skill }>(wb(`/skills/${resource(skill.id)}`), { ...fields(), version: skill.version }, signal) :
      client.post<{ skill: Skill }>(wb('/skills'), { ...fields(), requestId: action.requestId(JSON.stringify(fields())) }, signal), result => onSaved(result.skill)); }}>
      <fieldset disabled={action.busy}><div className="wb-form-grid">
        <Field label="Skill 식별 이름" hint="영문 소문자로 시작하는 소문자·숫자·하이픈"><input required pattern="[a-z](?:[a-z0-9]|-)*" maxLength={64} value={name} onChange={event => setName(event.target.value)} placeholder="product-change-review" /></Field>
        <Field label="Skill 제목"><input required maxLength={180} value={title} onChange={event => setTitle(event.target.value)} placeholder="상품 변경 검토" /></Field></div>
        <Field label="Skill 설명"><textarea required rows={2} value={description} onChange={event => setDescription(event.target.value)} /></Field>
        <Field label="Skill 지침 Markdown" hint="실제로 패키지에 저장할 지침입니다. 한국어 업무 출력과 원본 식별자를 보존하세요.">
          <textarea required rows={12} className="wb-code-editor" value={instructions} onChange={event => setInstructions(event.target.value)} spellCheck={false} placeholder="Describe the task, authoritative sources, workflow, and required evidence." /></Field>
        <details><summary>원본·도구·동작 예시 연결</summary><Field label="원본 참조 JSON"><textarea rows={3} value={refs} onChange={event => setRefs(event.target.value)} /></Field>
          <Field label="사용 도구 이름" hint="쉼표로 구분합니다. 선언만으로 접근 권한이 생기지 않습니다."><input value={tools} onChange={event => setTools(event.target.value)} /></Field>
          <Field label="동작 예시 JSON"><textarea rows={4} value={examples} onChange={event => setExamples(event.target.value)} /></Field></details>
        <button className="wb-primary" disabled={!dirty || !name.trim() || !instructions.trim()}>Skill 저장</button>
      </fieldset></form>
    {skill && <div className="wb-review-flow"><h3>검증 → 승인 → 패키지</h3>
      <p>패키지 형식 검사와 실제 행동 평가의 상태를 각각 확인하세요.</p>
      <dl className="wb-facts"><div><dt>패키지 검사</dt><dd><Status value={String((skill.validation?.package as JsonRecord | undefined)?.status || 'not-run')} /></dd></div>
        <div><dt>실제 행동 평가</dt><dd><Status value={String((skill.validation?.behavior as JsonRecord | undefined)?.status || 'not-run')} /></dd></div></dl>
      <Details title="검증·행동 평가 근거" value={skill.validation || { status: 'not-run' }} />
      <button disabled={dirty || action.busy || ['VALIDATING', 'PROPOSING', 'APPROVED', 'DEPRECATED'].includes(skill.status)}
        onClick={() => void action.run((signal, onUpdate) => validateSkill(client, skill, { signal, onUpdate }),
          onSaved, '검증 작업이 끝나 저장된 결과를 조회했습니다. 행동 평가 상태를 확인하세요.')}>저장 버전 검증</button>
      <label className="wb-check"><input type="checkbox" checked={checked} disabled={dirty || action.busy}
        onChange={event => setChecked(event.target.checked)} />현재 내용·원본·검증 근거를 확인했습니다</label>
      <div className="wb-actions"><button disabled={!checked || dirty || action.busy || !skill.contentHash || skill.status === 'APPROVED'}
        onClick={() => void action.run(signal => client.post<{ skill: Skill }>(wb(`/skills/${resource(skill.id)}/approve`),
          { version: skill.version, contentHash: skill.contentHash }, signal), result => onSaved(result.skill), '해당 버전이 승인되었습니다.')}>이 버전 승인</button>
        <button disabled={action.busy || dirty} onClick={() => void action.run(signal => client.get<JsonRecord>(wb(`/skills/${resource(skill.id)}/package`), signal), setPkg, '저장된 패키지를 조회했습니다.')}>저장 패키지 확인</button></div>
      <Details title="승인 대상 콘텐츠 해시" value={skill.contentHash} />
    </div>}
    <ActionState action={action} />
    {pkg && <div className="wb-package"><h3>저장된 패키지 원문</h3><Details title="파일·콘텐츠 해시" value={pkg} /></div>}
    {skill?.status === 'APPROVED' && <ApprovedSkillRun skill={skill} dirty={dirty} />}
  </>;
}

function ApprovedSkillRun({ skill, dirty }: { skill: Skill; dirty: boolean }) {
  const { client, params, navigate } = useWorkbench(), action = useAction();
  const [input, setInput] = useState('');
  const [executionId, setExecutionId] = useState(params.get('executionId') || '');
  const [output, setOutput] = useState<SkillExecution | null>(null);
  const runnable = !dirty && skill.sourceStatus !== 'stale' && skill.consumable !== false;
  return <section className="wb-review-flow"><h3>승인 Skill 실행</h3>
    <p>저장된 승인 버전 v{skill.version}을 사용합니다. 실제 결과는 원본 권한을 다시 확인한 뒤 표시합니다.</p>
    {!runnable && <p className="wb-error-text">편집 중이거나 원본이 변경되었습니다. 최신 내용을 검증·승인한 뒤 실행하세요.</p>}
    <form onSubmit={event => { event.preventDefault(); setOutput(null); void action.run((signal, onUpdate) =>
      executeApprovedSkill(client, skill, input.trim(), action.requestId(`${skill.id}:${skill.version}:${skill.contentHash}:${input.trim()}`), {
        signal, onUpdate, onQueued: id => { setExecutionId(id); navigate('skills', { skillId: skill.id, executionId: id }); },
      }), setOutput, '저장된 Skill 실행 결과를 조회했습니다. 응답 상태와 근거를 확인하세요.'); }}>
      <fieldset disabled={action.busy || !runnable}><Field label="승인 Skill 실행 입력">
        <textarea required rows={3} maxLength={4000} value={input} onChange={event => setInput(event.target.value)} placeholder="승인된 지침으로 수행할 업무를 입력하세요." />
      </Field><button className="wb-primary" disabled={!input.trim()}>승인 버전으로 실행</button></fieldset>
    </form>
    {executionId && <div className="wb-actions"><button disabled={action.busy} onClick={() => {
      setOutput(null); void action.run((signal, onUpdate) => readSkillExecution(client, skill, executionId, { signal, onUpdate }),
        setOutput, '저장된 실행 상태와 결과를 다시 확인했습니다.');
    }}>실행 상태 확인</button><Details title="연결된 실행 기록" value={{ id: executionId, skillId: skill.id, version: skill.version, contentHash: skill.contentHash }} /></div>}
    <ActionState action={action} />
    {output?.result && !action.busy && !action.error && <article className="wb-answer"><div className="wb-row"><h4>저장된 Skill 실행 결과</h4><Status value={output.result.status} /></div>
      <p className="wb-prose">{output.result.answer}</p>
      {output.result.executionMode === 'context-only' && <p className="wb-muted">원본 맥락 기반 응답 · 이 실행에서 외부 도구를 직접 실행하지 않았습니다.</p>}
      <Details title="실행 버전·응답 근거·검증 기록" value={output} />
    </article>}
  </section>;
}
