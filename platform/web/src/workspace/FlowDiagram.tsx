import { useId, useState } from 'react';
import { casePath, validCases, type ConditionCase } from './flowCases';

type Screen = { id: string; title: string; removed?: boolean };
type Transition = { id: string; from: string; to: string; condition?: string | null; action?: string; retention?: string; navigation?: string };

/** Read-only projection of the caller's flow. Selection never changes approval criteria. */
export default function FlowDiagram({ screens, transitions, entry, terminals = [], conditionCases, caseCoverage = 'complete' }: {
  screens: Screen[]; transitions: Transition[]; entry?: string; terminals?: string[];
  conditionCases?: ConditionCase[]; caseCoverage?: 'complete' | 'incomplete';
}) {
  const arrow = useId().replace(/:/g, '');
  const [chosen, setChosen] = useState('');
  const [caseIndex, setCaseIndex] = useState(-1);
  const selected = transitions.find(link => link.id === chosen);
  const position = new Map(screens.map((screen, index) => [screen.id, index]));
  const invalid = transitions.filter(link => !position.has(link.from) || !position.has(link.to) ||
    screens.some(screen => screen.removed && [link.from, link.to].includes(screen.id)));
  const casesReady = caseCoverage === 'complete' && validCases(conditionCases) && !!entry && position.has(entry) &&
    terminals.length > 0 && terminals.every(id => position.has(id));
  const values = casesReady && caseIndex >= 0 ? conditionCases![caseIndex] : undefined;
  const path = values ? casePath(entry, terminals, transitions, values) : null;
  const names = (ids: string[]) => ids.map(id => screens.find(screen => screen.id === id)?.title || `미연결 (${id})`).join(' → ');
  const width = 340 + transitions.length * 18;
  const height = Math.max(140, screens.length * 112 + 40);
  return <section className="ws-flow-diagram" aria-label="화면 이동 흐름도">
    <h4>화면 이동 흐름도</h4>
    {!screens.length ? <p>화면을 추가하면 이동 경로가 표시됩니다.</p> : <>
      {conditionCases !== undefined && (casesReady ? <>
        <label className="ws-field">조건 조합<select value={values ? String(caseIndex) : ''} onChange={event => {
          setCaseIndex(event.target.value === '' ? -1 : Number(event.target.value)); setChosen('');
        }}>
          <option value="">전체 흐름</option>{conditionCases!.map((item, index) => <option key={index} value={index}>
            {`케이스 ${index + 1} · ${Object.entries(item).map(([id, active]) => `${id}: ${active ? '충족' : '미충족'}`).join(', ') || '조건 없음'}`}
          </option>)}
        </select></label>
        {path && <p role="status">선택한 경로: {names(path.screens)}</p>}
        {path && path.end !== 'terminal' && <p role="alert">선택한 조건의 경로를 완료 화면까지 확인하지 못했습니다.
          {path.end === 'ambiguous' ? ' 조건 또는 분기 선택이 불명확합니다.' : path.end === 'loop' ? ' 반복되는 이동이 있습니다.' : ' 이어지는 이동이 없습니다.'}</p>}
        <p>조건별 예상 경로입니다. 실제 화면 검증과 흐름 승인은 별도로 진행합니다.</p>
      </> : <p role="alert">조건 조합 또는 시작·완료 화면의 검증 범위가 불완전하여 조건별 경로를 표시할 수 없습니다.</p>)}
      <label className="ws-field">강조할 이동<select value={selected?.id || ''} onChange={event => setChosen(event.target.value)}>
        <option value="">전체 이동</option>{transitions.map((link, index) =>
          <option key={link.id} value={link.id}>{index + 1}. {link.action || link.condition || '항상'}</option>)}
      </select></label>
      <div className="ws-flow-scroll" tabIndex={0} role="region" aria-label="흐름도 확대 보기">
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width, height }} role="img" aria-label={`화면 ${screens.length}개, 이동 ${transitions.length}개`}>
          <defs><marker id={arrow} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" />
          </marker></defs>
          {transitions.map((link, index) => {
            const from = position.get(link.from), to = position.get(link.to);
            if (from === undefined || to === undefined) return null;
            const y1 = 52 + from * 112, y2 = 76 + to * 112, lane = 306 + index * 18;
            const active = !path || path.transitions.includes(link.id);
            return <g key={link.id} data-case-active={path ? String(active) : undefined}
              className={!active || (selected && selected.id !== link.id) ? 'is-muted' : ''}>
              <path d={`M 260 ${y1} H ${lane} V ${y2} H 262`} fill="none" stroke={invalid.includes(link) ? '#b42318' : '#008485'} strokeWidth="2" markerEnd={`url(#${arrow})`} />
              <circle cx={lane} cy={(y1 + y2) / 2} r="10" fill="#fff" stroke="#008485" />
              <text x={lane} y={(y1 + y2) / 2 + 4} textAnchor="middle" fontSize="11">{index + 1}</text>
            </g>;
          })}
          {screens.map((screen, index) => <g key={screen.id} data-case-active={path ? String(path.screens.includes(screen.id)) : undefined}
            className={(path && !path.screens.includes(screen.id)) || (selected && ![selected.from, selected.to].includes(screen.id)) ? 'is-muted' : ''}>
            <title>{screen.title} · {screen.id}{screen.removed ? ' · 삭제 예정' : ''}</title>
            <rect x="16" y={20 + index * 112} width="244" height="84" rx="10" fill={screen.removed ? '#fff4f2' : '#f0f8f6'} stroke={screen.removed ? '#b42318' : '#008485'} />
            <text x="28" y={42 + index * 112} fontSize="11">화면 {index + 1}{screen.removed ? ' · 삭제 예정' : ''}</text>
            {[...screen.title].join('').match(/.{1,17}/gu)?.slice(0, 2).map((line, row) =>
              <text key={row} x="28" y={65 + index * 112 + row * 19} fontSize="14">{line}{row === 1 && [...screen.title].length > 34 ? '…' : ''}</text>)}
          </g>)}
        </svg>
      </div>
      {!transitions.length && <p>등록된 이동이 없습니다. 화면 순서만으로 연결을 추정하지 않습니다.</p>}
      {!!invalid.length && <p role="alert">연결할 화면이 없거나 삭제 예정인 이동이 {invalid.length}개 있습니다. 이동의 출발·도착 화면을 확인하세요.</p>}
      <ol className="ws-flow-paths">{transitions.map(link => <li key={link.id} aria-current={selected?.id === link.id ? 'true' : undefined}>
        <strong>{screens.find(screen => screen.id === link.from)?.title || `미연결 (${link.from || '출발'})`} → {screens.find(screen => screen.id === link.to)?.title || `미연결 (${link.to || '도착'})`}</strong>
        <p>{link.action ? `${link.action} · ` : ''}조건: {link.condition || '항상'}</p>
        {link.retention && <p>유지할 값: {link.retention}</p>}
      </li>)}</ol>
    </>}
  </section>;
}
