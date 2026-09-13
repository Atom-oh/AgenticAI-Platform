import { useEffect, useRef, useState } from 'react';
import { newRequest, uploadDocument } from './client';
import type { DocumentUpload } from './client';
import type { DocumentRecord, Reference } from './types';
import { Notice, revisionHref, useDocumentScope, usePrivateTask } from './DocumentScope';

export default function UploadForm({ document, references, onCancel }: {
  document?: DocumentRecord; references: Reference[]; onCancel: () => void;
}) {
  const { client, config, route } = useDocumentScope();
  const task = usePrivateTask();
  const [file, setFile] = useState<File>();
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState('regulation');
  const [graphRef, setGraphRef] = useState(references.some(ref => ref.id === route.ref) ? route.ref! : '');
  const [referenceChosen, setReferenceChosen] = useState(false);
  useEffect(() => {
    if (!referenceChosen && route.ref && references.some(ref => ref.id === route.ref)) {
      setGraphRef(route.ref);
    }
  }, [route.ref, references, referenceChosen]);
  const awaitingReference = !!route.ref && !referenceChosen && graphRef !== route.ref;
  const [versionLabel, setVersionLabel] = useState('');
  const [effectiveDate, setEffectiveDate] = useState('');
  const [checkpoint, setCheckpoint] = useState<DocumentUpload>();
  const [progress, setProgress] = useState(0);
  const request = useRef<{ fingerprint: string; id: string }>();
  const submit = () => {
    if (!file || task.busy || awaitingReference) return;
    setReferenceChosen(true);
    const fingerprint = JSON.stringify([file.name, file.size, file.lastModified, title, kind, graphRef, versionLabel, effectiveDate]);
    if (!request.current || request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: newRequest() };
    const requestId = request.current.id;
    void task.run(async (signal, current) => {
      const result = await uploadDocument(client, file,
        { title: title.trim(), kind, graphRef: graphRef || undefined, versionLabel: versionLabel.trim(),
          effectiveDate: effectiveDate || undefined, document, requestId }, config,
        { signal, checkpoint, onCheckpoint: value => { if (current()) setCheckpoint(value); },
          onProgress: value => { if (current()) setProgress(value); } });
      if (!current()) return;
      setFile(undefined); setCheckpoint(undefined);
      window.location.hash = revisionHref(result.document.id, result.revision.id, { ...route, textHash: undefined });
    });
  };
  if (document?.provenance === 'synthetic_sample') return <Notice>합성 예제 원본은 교체할 수 없습니다. 실제 자료는 새 문서로 등록하세요.</Notice>;
  return <form className="doc-stack doc-panel" onSubmit={event => { event.preventDefault(); submit(); }}>
    <div className="doc-row doc-between"><h3>{document ? '새 버전 등록' : '원문 등록'}</h3>
      <button type="button" onClick={() => { task.cancel(); onCancel(); }}>등록 닫기</button></div>
    <p className="doc-muted">PDF, HTML, Markdown, TXT · 최대 {Math.floor(config.maxFileBytes / 1048576)} MiB. 본문 추출 후 별도로 검토를 요청하세요.</p>
    {document && <p>{document.title}의 원문을 새 버전으로 보관합니다. 기존 버전과 검토 기록은 유지됩니다.</p>}
    <fieldset disabled={task.busy || !!checkpoint} className="doc-stack">
      <label>원문 파일<input type="file" required accept={config.extensions.map(ext => '.' + ext).join(',')}
        onChange={event => { const selected = event.target.files?.[0]; setFile(selected); request.current = undefined;
          if (selected && !title) setTitle(selected.name.replace(/\.[^.]+$/, '')); }} /></label>
      {!document && <div className="doc-fields">
        <label>문서 제목<input value={title} required maxLength={240} onChange={event => setTitle(event.target.value)} /></label>
        <label>문서 종류<select aria-label="문서 종류" value={kind} onChange={event => setKind(event.target.value)}>
          <option value="regulation">규정</option><option value="policy">업무 기준</option><option value="report">보고서</option>
          <option value="specification">화면·기능 명세</option><option value="notice">공문·안내</option><option value="guide">가이드</option>
          <option value="reference">참고 자료</option>
        </select></label>
        <label>관계 목록의 연결 대상<select aria-label="관계 목록의 연결 대상" value={graphRef} onChange={event => {
          setReferenceChosen(true); setGraphRef(event.target.value);
        }}>
          <option value="">연결하지 않음</option>
          {references.map(ref => <option key={ref.id} value={ref.id}>{ref.label === 'Regulation' ? '규정' : '문서'} · {ref.title}</option>)}
        </select></label>
      </div>}
      <div className="doc-fields">
        <label>버전 이름<input value={versionLabel} maxLength={120} placeholder="예: 2026년 9월 개정안" onChange={event => setVersionLabel(event.target.value)} /></label>
        <label>시행일 (선택)<input type="date" value={effectiveDate} onChange={event => setEffectiveDate(event.target.value)} /></label>
      </div>
    </fieldset>
    {checkpoint && <Notice>전송 기록은 이 화면의 메모리에만 유지됩니다. 같은 파일과 등록 내용으로 이어서 전송할 수 있습니다. 문서함을 이동하거나 화면을 닫으면 재시도 기록이 사라집니다.</Notice>}
    {(task.busy || checkpoint) && <div className="doc-progress" role="status">원문 전송 {progress}%
      <progress aria-label="원문 전송" value={progress} max={100} /></div>}
    {task.error && <Notice error>{task.error}</Notice>}
    {awaitingReference && <Notice>요청한 연결 대상을 확인하고 있습니다. 목록이 로드된 뒤 전송하거나, 연결 대상을 직접 선택하세요.
      <button type="button" onClick={() => { setReferenceChosen(true); setGraphRef(''); }}>연결 없이 등록하기</button>
    </Notice>}
    <div className="doc-row"><button type="submit" className="doc-primary" disabled={task.busy || awaitingReference || !file || (!document && !title.trim())}>
      {task.busy ? '원문 전송 중…' : checkpoint ? '전송 다시 시도' : document ? '새 버전 전송' : '원문 전송'}
    </button></div>
    <p className="doc-muted">원문은 승인 전까지 분석에 사용되지 않습니다. HTML은 실행하지 않고 추출한 글로 확인합니다.</p>
  </form>;
}
