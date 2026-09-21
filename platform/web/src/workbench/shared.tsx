import { Children, cloneElement, createContext, isValidElement, useContext, useEffect, useId, useRef, useState, type DependencyList, type ReactNode } from 'react';
import { errorText, type WorkspaceClient } from './client';
import type { Job, JsonRecord, Overview, Project } from './types';

export const LABELS: Record<string, string> = {
  owner: '프로젝트 관리자', planner: '기획자', designer: '디자이너', developer: '개발자',
  DRAFT: '초안', PENDING_APPROVAL: '승인 대기', APPROVED: '승인됨', DEPRECATED: '사용 중단',
  draft: '초안', approved: '승인됨', pending: '대기', queued: '대기 중', running: '처리 중',
  completed: '완료', complete: '완료', failed: '실패', ready: '준비됨', partial: '일부 완료',
  open: '대기', todo: '대기', in_progress: '진행 중', 'in-progress': '진행 중', blocked: '차단됨', done: '완료',
  pass: '통과', passed: '통과', fail: '실패', 'not-run': '미실행', 'not-configured': '미설정', unknown: '미확인',
  confirmed: '근거 확인', candidate: '검토 후보', snapshot: '문서 스냅샷', confluence: '사내 Confluence', git: 'Git 자산',
  sources: '지식 원본', batches: '수집 배치', documents: '지식 문서', changes: '변경 요청', tasks: '작업',
  skills: 'Skill', reports: '보고서', products: '상품', artifacts: '산출물', nodes: '연결 항목', edges: '관계',
  indexed: '색인 완료', unresolved: '미해결', total: '전체', verified: '검증됨', active: '활성',
  'change-impact': '변경 영향 보고서', 'pension-evaluation': '연금 상담 평가', management: '경영 보고서',
  underwriting: '심사 보고서', regulation: '규정 검토', baseline: '결정론적 기준 응답',
  model: '모델 생성 응답', 'deterministic-baseline': '결정론적 기준 응답',
  behaviorEvaluation: 'Skill 행동 평가', knowledge: '지식 검색', externalSources: '외부 원본 연결',
  mcp: 'MCP 제공', available: '사용 가능', configured: '설정됨', 'configured-only': '개별 연결 설정 필요',
  registered: '등록됨', indexing: '색인 중', analyzed: '분석됨', 'needs-mapping': '매핑 확인 필요', vectors: '벡터 항목', PROPOSING: '초안 생성 중',
  VALIDATING: '행동 평가 중', answered: '응답 생성됨', refused: '요청 거부', 'insufficient-evidence': '근거 부족',
};
export const label = (value?: string) => value ? LABELS[value] || value : '미확인';
export const object = (value: unknown): JsonRecord => value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
export const text = (value: unknown, fallback = '미확인'): string =>
  typeof value === 'string' || typeof value === 'number' ? String(value) : typeof value === 'boolean' ? value ? '예' : '아니요' : fallback;
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export function parseArray(value: string, name: string): unknown[] {
  let result: unknown;
  try { result = JSON.parse(value); } catch { throw new Error(`${name}: 올바른 JSON 배열을 입력하세요.`); }
  if (!Array.isArray(result)) throw new Error(`${name}: JSON 배열이 필요합니다.`);
  return result;
}
export function parseObject(value: string, name: string): JsonRecord {
  let result: unknown;
  try { result = JSON.parse(value); } catch { throw new Error(`${name}: 올바른 JSON 객체를 입력하세요.`); }
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error(`${name}: JSON 객체가 필요합니다.`);
  return result as JsonRecord;
}
type Scope = {
  client: WorkspaceClient; project: Project; overview: Overview; params: URLSearchParams;
  navigate: (view: string, patch?: Record<string, string | undefined>) => void; refresh: () => void;
};
export const WorkbenchScope = createContext<Scope | null>(null);
export function useWorkbench() {
  const value = useContext(WorkbenchScope);
  if (!value) throw new Error('작업 공간을 선택하세요.');
  return value;
}
export function useLoad<T>(loader: (signal: AbortSignal) => Promise<T>, deps: DependencyList) {
  const [state, setState] = useState<{ data?: T; error: string; loading: boolean }>({ error: '', loading: true });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setState({ error: '', loading: true });
    void loader(abort.signal).then(data => {
      if (!abort.signal.aborted) setState({ data, error: '', loading: false });
    }).catch(error => {
      if (!abort.signal.aborted) setState({ error: errorText(error), loading: false });
    });
    return () => abort.abort();
  }, [...deps, revision]);
  return { ...state, refresh: () => setRevision(value => value + 1) };
}
export function useAction() {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const active = useRef<AbortController | null>(null), ids = useRef(new Map<string, string>());
  useEffect(() => () => { active.current?.abort(); }, []);
  function requestId(key: string) {
    if (!ids.current.has(key)) ids.current.set(key, crypto.randomUUID());
    return ids.current.get(key)!;
  }
  async function run<T>(work: (signal: AbortSignal, update: (job: Job) => void) => Promise<T>, done?: (value: T) => void, success = '서버에 저장했습니다.') {
    if (active.current) return;
    const abort = new AbortController(); active.current = abort;
    setBusy(true); setError(''); setMessage(''); setJob(null);
    try {
      const result = await work(abort.signal, value => { if (!abort.signal.aborted) setJob(value); });
      if (!abort.signal.aborted) { done?.(result); setMessage(success); }
    } catch (reason) {
      if (!abort.signal.aborted) setError(errorText(reason));
    } finally {
      if (!abort.signal.aborted) setBusy(false);
      if (active.current === abort) active.current = null;
    }
  }
  function cancel() {
    active.current?.abort(); active.current = null; setBusy(false); setJob(null);
    setMessage('응답 대기를 중지했습니다. 이미 접수된 서버 작업은 계속될 수 있으므로 목록에서 확인하세요.');
  }
  return { busy, error, message, job, run, cancel, requestId };
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`wb-notice${error ? ' wb-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
export function ActionState({ action }: { action: ReturnType<typeof useAction> }) {
  const progress = action.job?.progress;
  const percent = typeof progress === 'number' ? progress : object(progress).percent;
  const progressMessage = typeof progress === 'string' ? progress : text(object(progress).message, '');
  return <>{action.error && <Notice error>{action.error}</Notice>}{action.message && <Notice>{action.message}</Notice>}
    {action.busy && <Notice>{action.job ? `백그라운드 작업 · ${label(action.job.status)}` : '서버에서 처리하고 있습니다…'}
      {progressMessage && <span>{progressMessage}</span>}
      {typeof percent === 'number' && percent >= 0 && percent <= 100 && <progress aria-label="서버 작업 진행률" max={100} value={percent}>{percent}%</progress>}
      <button type="button" className="wb-link" onClick={action.cancel}>대기 중지</button></Notice>}</>;
}
export function LoadState({ state, children }: { state: { loading: boolean; error: string; refresh: () => void }; children: ReactNode }) {
  if (state.loading) return <p className="wb-loading" role="status">최신 작업을 불러오고 있습니다…</p>;
  if (state.error) return <Notice error>{state.error} <button type="button" onClick={state.refresh}>다시 조회</button></Notice>;
  return <>{children}</>;
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="wb-empty"><span aria-hidden="true">＋</span><p>{children}</p></div>;
}
export function Status({ value }: { value?: string }) {
  return <span className={`wb-status ${/^(pass|passed|approved|APPROVED|ready|completed|done)$/.test(value || '') ? 'wb-good' :
    /fail|blocked|DEPRECATED/.test(value || '') ? 'wb-bad' : ''}`}>{label(value)}</span>;
}
export function Field({ label: title, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const hintId = useId();
  return <label className="wb-field"><span>{title}</span>{Children.map(children, child =>
    isValidElement<Record<string, unknown>>(child) && typeof child.type === 'string' && ['input', 'select', 'textarea'].includes(child.type) ?
      cloneElement(child, { 'aria-label': title, ...(hint ? { 'aria-describedby': hintId } : {}) }) : child)}
    {hint && <small id={hintId}>{hint}</small>}</label>;
}
export function Details({ title = '원본 근거·버전', value }: { title?: string; value: unknown }) {
  return <details className="wb-details"><summary>{title}</summary><pre>{pretty(value) ?? '기록 없음'}</pre></details>;
}
export function Metrics({ values }: { values?: JsonRecord }) {
  const entries = Object.entries(values || {}).filter(([, value]) => typeof value === 'number');
  return entries.length ? <dl className="wb-metrics">{entries.map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{Number(value).toLocaleString('ko-KR')}</dd></div>)}</dl> : null;
}
export function Section({ title, description, children, action }: { title: string; description?: string; children: ReactNode; action?: ReactNode }) {
  return <section className="wb-section"><div className="wb-section-head"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</div>{children}</section>;
}
