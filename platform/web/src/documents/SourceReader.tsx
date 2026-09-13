import { useEffect, useRef, useState } from 'react';
import { resource, WorkspaceError } from '../workspace/client';
import { downloadOriginal, sourceHref } from './client';
import { libraryHref, Notice, revisionHref, useDocumentScope, usePrivateTask } from './DocumentScope';
import type { DocumentDetail, Job, Reference, Role, SourceView } from './types';
import { dateLabel, Details, Progress, roleLabel, Sample, Status, statusLabel } from './presentation';
import { watchAuthorizedResource, watchDocumentJob } from './useDocumentJob';
import UploadForm from './UploadForm';

type ReaderState = { detail: DocumentDetail; source: SourceView };
type Activity = { id: string; action: string; actorId: string; createdAt?: number; revisionId?: string };
const activityLabels: Record<string, string> = {
  created: '문서 등록', 'revision-created': '새 버전 등록', submitted: '검토 요청', approved: '원문 승인',
  rejected: '보완 요청', 'permissions-changed': '읽기 권한 변경', archived: '보관 처리',
  'intake-queued': '본문 추출 요청', 'intake-failed': '본문 추출 실패', extracted: '본문 추출 완료',
};

export default function SourceReader({ references, onChanged }: { references: Reference[]; onChanged: () => void }) {
  const { client, route } = useDocumentScope();
  const task = usePrivateTask(), action = usePrivateTask(), download = usePrivateTask(), pageTask = usePrivateTask(), activityTask = usePrivateTask();
  const [state, setState] = useState<ReaderState>();
  const [job, setJob] = useState<Job>();
  const [refresh, setRefresh] = useState(0);
  const [upload, setUpload] = useState(false);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [activityCursor, setActivityCursor] = useState<string>();
  const [activityLoaded, setActivityLoaded] = useState(false);
  const [message, setMessage] = useState('');
  const urls = useRef(new Set<string>());
  const selectedParagraph = useRef<HTMLLIElement>(null);
  const root = '/documents/' + resource(route.documentId!);
  useEffect(() => () => { for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear(); }, []);
  useEffect(() => {
    setState(undefined); setJob(undefined); setActivity([]); setActivityCursor(undefined); setActivityLoaded(false); setUpload(false);
    pageTask.cancel(); activityTask.cancel(); download.cancel();
    void task.run(async (signal, current) => {
      const load = async () => {
        const detail = await client.get<DocumentDetail>(root, signal);
        if (!current()) return;
        if (!detail.capabilities.read || detail.document.id !== route.documentId ||
            (detail.document.projectId || undefined) !== route.projectId) {
          throw new WorkspaceError('현재 문서에 접근할 권한을 확인할 수 없습니다.', 403);
        }
        const revisionId = route.revisionId || detail.document.latestRevisionId;
        if (!detail.revisions.some(revision => revision.id === revisionId)) {
          throw new WorkspaceError('지정된 원문 버전을 찾을 수 없습니다. 다른 버전으로 자동 이동하지 않습니다.', 404);
        }
        const source = await client.get<SourceView>(root + '/revisions/' + resource(revisionId) +
          (route.paragraph ? '?paragraph=' + encodeURIComponent(route.paragraph) : ''), signal);
        if (!current()) return;
        if (source.document.id !== detail.document.id || source.revision.id !== revisionId ||
            source.revision.documentId !== detail.document.id ||
            (source.document.projectId || undefined) !== route.projectId ||
            source.document.version !== detail.document.version ||
            route.textHash && source.revision.textHash !== route.textHash) {
          throw new WorkspaceError('연결된 원문 버전 또는 확인값이 바뀌었습니다. 최신 버전으로 자동 이동하지 않습니다.', 409, 'source-integrity');
        }
        if (route.paragraph && !source.paragraphs.some(paragraph => paragraph.id === route.paragraph)) {
          throw new WorkspaceError('해당 원문 버전에서 근거 문단을 찾을 수 없습니다.', 404, 'paragraph-not-found');
        }
        const next = { detail, source };
        setState(next);
        return next;
      };
      const loaded = await load();
      if (!current() || !loaded) return;
      const revision = loaded.source.revision;
      if (revision.status === 'processing' && revision.jobId) {
        let finalJob: Job;
        try {
          finalJob = await watchDocumentJob(client, revision.jobId, signal, next => { if (current()) setJob(next); });
        } catch (error) {
          if (!(error instanceof WorkspaceError) || ![403, 404].includes(error.status)) throw error;
          if (!current()) return;
          setJob(undefined);
          // A shared source can be readable while its creator's raw job is not.
          // The source endpoint rechecks actual access on every refresh.
          await watchAuthorizedResource(load, value => value.source.revision.status === 'processing', signal);
          return;
        }
        if (current()) {
          await load();
          if (current() && finalJob.status === 'failed') setMessage('본문 추출을 완료하지 못했습니다. 처리 결과를 확인하고 지원하는 원문으로 새 버전을 등록하세요.');
        }
      }
    });
  }, [client, root, route, refresh, task.run, pageTask.cancel, activityTask.cancel, download.cancel]);
  useEffect(() => {
    if (route.paragraph && state) selectedParagraph.current?.scrollIntoView({ block: 'nearest' });
  }, [route.paragraph, state?.source.revision.id]);
  const reload = () => { setState(undefined); setRefresh(value => value + 1); };
  const mutate = (path: string, body: unknown, method: 'post' | 'put' = 'post') => {
    if (action.busy) return;
    void action.run(async (signal, current) => {
      try { await client[method](path, body, signal); }
      catch (error) { if (current()) setState(undefined); throw error; }
      if (current()) { setMessage('변경 사항을 저장했습니다. 기록된 최신 상태를 확인하세요.'); onChanged(); reload(); }
    });
  };
  const source = state?.source, document = source?.document, revision = source?.revision;
  return <section className="doc-stack" aria-label="원문과 검토">
    <div className="doc-row doc-between"><h3>원문과 검토</h3><button onClick={reload} disabled={action.busy}>원문 다시 조회</button></div>
    {task.error && <Notice error>{task.error} <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId })}>문서함에서 찾기</a></Notice>}
    {action.error && <Notice error>{action.error} 현재 버전을 다시 조회한 뒤 검토하세요.</Notice>}
    {message && <Notice>{message}</Notice>}
    {task.busy && !state && <Notice>선택한 원문 버전을 불러오고 있습니다…</Notice>}
    {state && document && revision && <>
      <div className="doc-panel doc-stack">
        <div className="doc-stack"><h3>{document.title}</h3>
          <div className="doc-row"><Sample provenance={document.provenance} /><Status value={document.status} /><Status value={revision.status} /></div>
          <p><strong>{revision.versionLabel || `반입 ${revision.revision}차`}</strong> · 반입 {revision.revision}차
            {revision.effectiveDate && <> · 시행일 {revision.effectiveDate}</>}</p>
        </div>
        {document.status === 'archived' && <Notice>보관 처리된 문서입니다. 새 분석에는 사용할 수 없으며, 접근 권한이 있는 기존 원문과 검토 이력은 유지됩니다.</Notice>}
        {route.analysisId && document.approvedRevisionId !== revision.id && <Notice>현재 승인된 원문이 아니거나 승인본과 다른 버전입니다. 분석에 사용된 버전은 분석 결과의 근거 링크에서 확인하세요.</Notice>}
        {document.provenance === 'synthetic_sample' && <Notice>동작 확인을 위한 합성 자료입니다. 실제 은행 내규가 아니며 원본 교체는 지원하지 않습니다.
          <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId, register: true })}>별도 문서 등록</a></Notice>}
        <label>원문 버전<select aria-label="원문 버전" value={revision.id} onChange={event => {
          const selected = state.detail.revisions.find(row => row.id === event.target.value);
          if (selected) window.location.hash = revisionHref(document.id, selected.id, { ...route, textHash: selected.textHash || undefined });
        }}>
          {[...state.detail.revisions].sort((a, b) => b.revision - a.revision).map(row => <option key={row.id} value={row.id}>
            반입 {row.revision}차 · {row.versionLabel || row.name} · {statusLabel(row.status)}
          </option>)}
        </select></label>
        <div className="doc-row">
          <button disabled={download.busy || ['uploading', 'processing'].includes(revision.status)} onClick={() => void download.run(async (signal, current) => {
            const blob = await downloadOriginal(client, document.id, revision, signal);
            if (!current()) return;
            const url = URL.createObjectURL(blob); urls.current.add(url);
            const anchor = window.document.createElement('a');
            anchor.href = url; anchor.download = revision.name; anchor.hidden = true;
            window.document.body.append(anchor); anchor.click(); anchor.remove();
            window.setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); }, 1000);
          })}>{download.busy ? '원본 확인 중…' : '원본 내려받기'}</button>
          {state.detail.capabilities.edit && document.status === 'active' && document.provenance === 'uploaded' && <button onClick={() => setUpload(value => !value)}>새 버전 등록</button>}
        </div>
        {download.error && <Notice error>{download.error}</Notice>}
        {revision.reviewedBy && <p className="doc-muted">검토자 {revision.reviewedBy} · 반입 {revision.revision}차 · 검토 기록 버전 {revision.version} · {dateLabel(revision.reviewedAt)}</p>}
        <Details title="원본·추출본과 검토 기록">
          <p>파일: {revision.name} · {revision.size.toLocaleString()} 바이트</p>
          <p>문서 ID: <code>{document.id}</code></p><p>원문 버전 ID: <code>{revision.id}</code></p>
          <p>원본 SHA-256: <code>{revision.sha256}</code></p>
          <p>추출본 SHA-256: <code>{revision.textHash || '아직 없음'}</code></p>
          <p>원본 파일과 추출된 본문은 각각 다른 확인값으로 관리합니다.</p>
          <p>검토자: {revision.reviewedBy || '아직 검토되지 않음'} · {dateLabel(revision.reviewedAt)}</p>
          {revision.reviewNote && <p className="doc-literal">{revision.reviewNote}</p>}
          <p>표시된 검토 기록 버전: {revision.version}</p>
        </Details>
      </div>
      {upload && <UploadForm document={document} references={references} onCancel={() => setUpload(false)} />}
      {job && <Progress job={job} label="본문 추출" />}
      <section className="doc-panel doc-stack" aria-label="추출된 원문">
        <div className="doc-row doc-between"><h3>추출된 본문</h3><Status value={revision.parseStatus} /></div>
        <p className="doc-muted">전체 {source.totalParagraphs}개 문단 · {revision.pages}쪽. PDF 등의 추출 본문은 원본 파일과 표현이 다를 수 있습니다.</p>
        {route.paragraph && <a href={revisionHref(document.id, revision.id, { ...route, textHash: revision.textHash || undefined })}>본문 처음부터 보기</a>}
        {revision.parseStatus !== 'complete' && <Notice error={revision.status === 'failed'}>
          {revision.parseStatus === 'partial' ? '일부 본문만 추출되었습니다. 이 버전은 분석용으로 승인할 수 없습니다.'
            : revision.status === 'failed' ? '본문 추출에 실패했습니다. 원본 형식과 파일 상태를 확인하고 새 버전을 등록하세요.'
              : '본문이 아직 준비되지 않았거나 추출할 수 없습니다. 처리 상태와 원본을 확인하세요.'}
        </Notice>}
        {revision.warnings?.length > 0 && <ul>{revision.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
        {!source.paragraphs.length && <p>표시할 본문이 없습니다. 스캔·암호화·빈 파일 여부를 확인하세요.</p>}
        <ol className="doc-paragraphs">{source.paragraphs.map(paragraph => <li className="doc-paragraph" key={paragraph.id}
          ref={paragraph.id === route.paragraph ? selectedParagraph : undefined} aria-current={paragraph.id === route.paragraph ? true : undefined}>
          <div className="doc-row"><strong>{paragraph.id}</strong><span>{paragraph.page == null ? '쪽 정보 없음' : `${paragraph.page}쪽`}</span>
            {revision.textHash && <a href={sourceHref({ documentId: document.id, revisionId: revision.id,
              paragraphId: paragraph.id, textHash: revision.textHash }, route)}>이 문단 링크</a>}
            {paragraph.id === route.paragraph && <span className="doc-chip">선택한 근거</span>}</div>
          <p className="doc-literal">{paragraph.text}</p>
        </li>)}</ol>
        {source.cursor && <button disabled={pageTask.busy} onClick={() => void pageTask.run(async (signal, current) => {
          let page: SourceView;
          try { page = await client.get<SourceView>(root + '/revisions/' + resource(revision.id) + '?cursor=' + encodeURIComponent(source.cursor!), signal); }
          catch (error) { if (current()) setState(undefined); throw error; }
          if (!current()) return;
          if (page.document.id !== document.id || page.revision.id !== revision.id || page.revision.textHash !== revision.textHash ||
              page.document.version !== document.version || (page.document.projectId || undefined) !== route.projectId) {
            setState(undefined); throw new WorkspaceError('본문을 조회하는 동안 원문 상태가 바뀌었습니다. 다시 조회하세요.', 409);
          }
          setState({ ...state, source: { ...page, cursor: page.cursor === source.cursor ? undefined : page.cursor,
            paragraphs: [...source.paragraphs, ...page.paragraphs].filter((row, index, all) => all.findIndex(item => item.id === row.id) === index) } });
        })}>다음 문단 더 보기</button>}
      </section>
      <ReviewControls key={`${document.version}:${revision.id}:${revision.version}`} state={state} busy={action.busy} mutate={mutate} />
      <section className="doc-panel doc-stack"><h3>문서 활동 이력</h3>
        <button disabled={activityTask.busy} onClick={() => void activityTask.run(async (signal, current) => {
          const page = await client.get<{ events: Activity[]; cursor?: string }>(root + '/activity' +
            (activityCursor ? '?cursor=' + encodeURIComponent(activityCursor) : ''), signal);
          if (current()) { setActivity(rows => [...rows, ...page.events]); setActivityLoaded(true);
            setActivityCursor(page.cursor === activityCursor ? undefined : page.cursor); }
        })} hidden={activityLoaded && !activityCursor}>{activityLoaded ? '활동 더 보기' : '활동 이력 조회'}</button>
        {activityLoaded && !activity.length && <p>기록된 활동이 없습니다.</p>}
        <ul className="doc-list">{activity.map(event => <li key={event.id}>
          <strong>{Object.hasOwn(activityLabels, event.action) ? activityLabels[event.action] : '문서 상태 변경'}</strong>
          <p>{event.actorId} · {dateLabel(event.createdAt)}</p>
          {event.revisionId && state.detail.revisions.some(row => row.id === event.revisionId) &&
            <a href={revisionHref(document.id, event.revisionId, { ...route, textHash: undefined })}>해당 원문 버전 보기</a>}
        </li>)}</ul>
        {activityTask.error && <Notice error>{activityTask.error}</Notice>}
      </section>
    </>}
    {pageTask.error && <Notice error>{pageTask.error} 원문 상태를 다시 조회하세요.</Notice>}
  </section>;
}

function ReviewControls({ state, busy, mutate }: { state: ReaderState; busy: boolean;
  mutate: (path: string, body: unknown, method?: 'post' | 'put') => void }) {
  const { config } = useDocumentScope();
  const { document, revision } = state.source;
  const { capabilities } = state.detail;
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [archiveConfirmed, setArchiveConfirmed] = useState(false);
  const [roles, setRoles] = useState<Role[]>(document.readRoles);
  const root = '/documents/' + resource(document.id);
  const active = document.status === 'active';
  const canSubmit = active && capabilities.edit && ['draft', 'rejected'].includes(revision.status) && revision.parseStatus === 'complete';
  const newerApproved = state.detail.revisions.some(row => row.id === document.approvedRevisionId && row.revision > revision.revision);
  const review = active && capabilities.review && revision.status === 'in_review';
  return <div className="doc-panel doc-stack"><h3>원문 검토와 관리</h3>
    <p>검토 요청 후 소유자 또는 기획 담당자가 원문과 추출 범위를 확인해 승인합니다. 자동 승인은 없습니다.</p>
    {canSubmit && <button disabled={busy} onClick={() => mutate(root + '/revisions/' + resource(revision.id) + '/submit', { version: revision.version })}>이 버전 검토 요청</button>}
    {review && <div className="doc-stack">
      <p>반입 {revision.revision}차 · {revision.versionLabel || revision.name} · 검토 기록 버전 {revision.version}</p>
      {newerApproved && <Notice>더 새로운 반입 버전이 승인되어 있습니다. 이 이전 버전으로 승인본을 되돌릴 수 없습니다.</Notice>}
      <label>원문 검토 의견<textarea aria-label="원문 검토 의견" value={note} maxLength={4000} onChange={event => setNote(event.target.value)} /></label>
      <label className="doc-check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />이 원문 버전과 추출된 본문을 확인했습니다</label>
      <div className="doc-row">
        <button className="doc-primary" disabled={busy || !confirmed || !note.trim() || revision.parseStatus !== 'complete' || newerApproved}
          onClick={() => mutate(root + '/revisions/' + resource(revision.id) + '/review', { version: revision.version, decision: 'approved', note: note.trim() })}>이 버전 원문 승인</button>
        <button disabled={busy || !confirmed || !note.trim()} onClick={() => mutate(root + '/revisions/' + resource(revision.id) + '/review',
          { version: revision.version, decision: 'rejected', note: note.trim() })}>보완 요청</button>
      </div>
    </div>}
    {!capabilities.review && revision.status === 'in_review' && <Notice>소유자 또는 기획 담당자의 검토를 기다리고 있습니다.</Notice>}
    {active && capabilities.manage && <details className="doc-details"><summary>읽기 권한 관리</summary><div>
      <p>현재 문서 기록 버전 {document.version}. 선택한 역할만 본문과 이 문서를 사용한 분석 결과를 볼 수 있습니다. 소유자 권한은 유지됩니다.</p>
      <fieldset disabled={busy} className="doc-stack"><legend>문서를 읽을 수 있는 역할</legend>
        {config.roles.map(role => <label className="doc-check" key={role}><input type="checkbox" checked={roles.includes(role)} disabled={role === 'owner'}
          onChange={event => setRoles(values => event.target.checked ? [...values, role] : values.filter(value => value !== role))} />{roleLabel(role)}</label>)}
      </fieldset>
      <button disabled={busy} onClick={() => mutate(root + '/permissions', { version: document.version, readRoles: roles }, 'put')}>읽기 권한 저장</button>
    </div></details>}
    {active && capabilities.edit && <details className="doc-details"><summary>문서 보관 처리</summary><div>
      <p>보관 처리하면 새 분석에 사용할 수 없습니다. 기존 원문과 이력은 접근 권한에 따라 유지되며 삭제되지 않습니다.</p>
      <label className="doc-check"><input type="checkbox" checked={archiveConfirmed} onChange={event => setArchiveConfirmed(event.target.checked)} />새 분석에서 사용을 중단하고 이력을 보관합니다</label>
      <button className="doc-danger" disabled={busy || !archiveConfirmed} onClick={() => mutate(root + '/archive', { version: document.version })}>문서 보관 처리</button>
    </div></details>}
  </div>;
}
