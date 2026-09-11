import { useEffect, useRef, useState } from 'react';
import { aborted, messageOf, resource, uploadFile, type UploadCheckpoint } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { PURPOSES, manualAssets, ocrLabel, stateLabel, suggestedPurpose } from './rules';
import { JobProgress, Notice, PrivatePreview, useDownload } from './shared';
import type { Analysis, Asset, Job, Purpose, WorkspaceConfig } from './types';

type PendingFile = {
  id: string; file: File; purpose: Purpose; parentId?: string; checkpoint?: UploadCheckpoint;
  progress: number; state: 'ready' | 'uploading' | 'sent' | 'failed'; error?: string; job?: Job;
};
export default function IntakePanel({ config, assets, selected, onSelected, refresh, onContinue }: {
  config: WorkspaceConfig; assets: Asset[]; selected: string[]; onSelected: (ids: string[]) => void; refresh: () => void; onContinue: () => void;
}) {
  const workspaceClient = useWorkspaceClient();
  const [purpose, setPurpose] = useState<Purpose | 'auto'>('auto');
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [parent, setParent] = useState<Asset | null>(null);
  const [filter, setFilter] = useState('');
  const [inspect, setInspect] = useState<Asset | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const supportsCss = config.extensions.some(extension => extension.replace(/^\./, '').toLowerCase() === 'css');
  const input = useRef<HTMLInputElement>(null);
  const control = useRef<AbortController | null>(null);
  useEffect(() => () => control.current?.abort(), []);
  const add = (chosen: FileList | File[]) => {
    const incoming = Array.from(chosen);
    if (incoming.length > 20) { setError('한 번에 최대 20개 파일을 추가하세요.'); return; }
    setError('');
    setFiles(previous => [...previous, ...incoming.map(file => ({
      id: crypto.randomUUID(), file, purpose: parent?.purpose || (purpose === 'auto' ? suggestedPurpose(file.name) : purpose), parentId: parent?.id,
      progress: 0, state: 'ready' as const,
    }))]);
    setParent(null);
  };
  const patch = (id: string, value: Partial<PendingFile>) => setFiles(previous => previous.map(file => file.id === id ? { ...file, ...value } : file));
  const start = async (file: PendingFile) => {
    if (control.current) return;
    if (file.parentId && assets.some(asset => asset.id === file.parentId && asset.system)) {
      patch(file.id, { state: 'failed', error: '게시 지침은 재반입할 수 없습니다. 기획 초안을 새 버전으로 게시하세요.' }); return;
    }
    const controller = new AbortController(); control.current = controller; setBusy(true);
    patch(file.id, { state: 'uploading', error: '' });
    try {
      const result = await uploadFile(workspaceClient, file.file, {
        purpose: file.purpose, parentId: file.parentId, maxFileBytes: config.maxFileBytes, extensions: config.extensions,
        signal: controller.signal, checkpoint: file.checkpoint,
        onCheckpoint: checkpoint => { if (!controller.signal.aborted) patch(file.id, { checkpoint }); },
        onProgress: progress => { if (!controller.signal.aborted) patch(file.id, { progress }); },
      });
      if (controller.signal.aborted) return;
      patch(file.id, { state: 'sent', progress: 100, job: result.job }); refresh();
    } catch (reason) {
      if (!controller.signal.aborted) patch(file.id, { state: 'failed', error: messageOf(reason) });
    } finally { if (control.current === controller) control.current = null; if (!controller.signal.aborted) setBusy(false); }
  };
  const toggle = (asset: Asset) => {
    if (asset.system) return;
    if (selected.includes(asset.id)) onSelected(selected.filter(id => id !== asset.id));
    else if (selected.length >= 20) setError('생성에 사용할 파일은 최대 20개까지 선택할 수 있습니다.');
    else { setError(''); onSelected([...selected, asset.id]); }
  };
  const active = manualAssets(assets).filter(asset => !filter || asset.purpose === filter);
  const inspected = inspect && assets.find(asset => asset.id === inspect.id);
  return <div className="ws-intake">
    <section className="ws-section">
      <div className="ws-section-heading"><div><h2>HTML 시안과 이미지로 시작하세요</h2><p>HTML은 화면 시안으로, PNG/JPG는 화면 기준으로, SVG는 아이콘·벡터 자산으로 준비합니다.</p></div>
        <button onClick={onContinue} className="ws-primary" disabled={!selected.length}>선택한 {selected.length}개로 규칙 만들기</button></div>
      <div className="ws-drop" onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; }}
        onDrop={event => { event.preventDefault(); add(event.dataTransfer.files); }}>
        <div><strong>파일을 여기에 놓거나 선택하세요</strong>
          <p>HTML·HTM{supportsCss ? ' + CSS' : ''} / PNG·JPG·JPEG / SVG · 파일당 최대 {Math.floor(config.maxFileBytes / 1048576)}MB</p>
          {supportsCss && <p>HTML에 연결된 CSS 파일도 함께 추가하세요. 스타일 가이드 자료로 보관합니다.</p>}
          <p>추천된 파일 용도를 확인하거나 바꾼 뒤 보관하세요.</p></div>
        <label className="ws-field">파일 용도<select value={purpose} onChange={event => setPurpose(event.target.value as Purpose | 'auto')}>
          <option value="auto">파일 형식에 맞춰 추천</option>
          {Object.entries(PURPOSES).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select></label>
        <button className="ws-primary" onClick={() => { setParent(null); input.current?.click(); }}>파일 선택</button>
        <input ref={input} type="file" aria-label="반입할 파일" accept={config.extensions.map(e => '.' + e.replace(/^\./, '')).join(',')}
          multiple={!parent} className="ws-file-input" onChange={event => { if (event.target.files) add(event.target.files); event.target.value = ''; }} />
      </div>
      <details className="ws-intake-help"><summary>PDF·FIG·가이드 파일과 반입 안내</summary>
        <p>PDF는 지원 가능한 페이지를 확인합니다. FIG는 원본 보관용이며 내부 화면 해석과 미리보기는 지원하지 않습니다.
          Markdown·TXT·JSON 가이드와 스킬도 추가할 수 있습니다. 스킬은 참고 지침이며 파일 안의 프로그램을 실행하지 않습니다.</p>
        <p>정해진 반입 절차를 거친 외부 산출물을 사용하세요. Figma 연결이나 플러그인은 필요하지 않습니다.</p>
        <p>허용 형식: {config.extensions.map(extension => extension.replace(/^\./, '').toUpperCase()).join(' · ')}</p>
      </details>
      {parent && <Notice>{parent.name}의 새 반입 버전으로 추가할 파일을 선택하세요. 이전 원본과 이력은 보존됩니다.</Notice>}
      {error && <Notice error>{error}</Notice>}
      {files.length > 0 && <div className="ws-upload-list">
        {files.map(file => <div className="ws-upload-row" key={file.id}>
          <div><strong>{file.file.name}</strong><small>{(file.file.size / 1024).toFixed(1)}KB{file.parentId ? ' · 새 반입 버전' : ''}</small>
            <progress aria-label={`${file.file.name} 전송률`} value={file.progress} max={100} /></div>
          <label className="ws-field"><span className="ws-sr">용도: {file.file.name}</span>
            <select value={file.purpose} disabled={!!file.checkpoint || busy || file.state === 'sent'} onChange={event => patch(file.id, { purpose: event.target.value as Purpose })}>
              {Object.entries(PURPOSES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
          {file.state !== 'sent' && <button onClick={() => void start(file)} disabled={busy} className="ws-primary">
            {file.state === 'uploading' ? '전송 중…' : file.state === 'failed' ? '전송 다시 시도' : '비공개 보관하기'}</button>}
          <button disabled={file.state === 'uploading'} onClick={() => setFiles(previous => previous.filter(item => item.id !== file.id))}>
            {file.state === 'sent' ? '진행 표시 닫기' : '목록에서 빼기'}</button>
          {file.error && <Notice error>{file.error}</Notice>}
          {file.job && <JobProgress job={file.job} label={`${file.file.name} 확인`} onComplete={refresh} onFailure={refresh} />}
        </div>)}
      </div>}
    </section>
    <div className="ws-library-layout">
      <section className="ws-section">
        <div className="ws-section-heading"><h2>내 파일</h2><label className="ws-field"><span className="ws-sr">파일 용도 필터</span>
          <select value={filter} onChange={event => setFilter(event.target.value)}><option value="">모든 용도</option>
            {Object.entries(PURPOSES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
        {assets.some(asset => asset.system) && <p className="ws-muted">게시한 상품 지침은 기획 패널의 버전 이력에서 확인합니다. 파일 선택·재반입·사용 제외 대상에 포함되지 않습니다.</p>}
        {!active.length && <div className="ws-empty">보관한 파일이 없습니다. 파일을 추가하고 비공개 보관하기를 눌러 주세요.</div>}
        <div className="ws-assets">{active.map(asset => <article key={asset.id} className={`ws-asset ${selected.includes(asset.id) ? 'is-selected' : ''}`}>
          <label className="ws-select-file"><input type="checkbox" checked={selected.includes(asset.id)} disabled={asset.uploadStatus !== 'stored'}
            onChange={() => toggle(asset)} /><span>{asset.name}</span></label>
          <p>{PURPOSES[asset.purpose]} · 반입 v{asset.importRevision || 1}{asset.parentId ? ' · 재반입' : ''}</p>
          <div className="ws-status-line"><span>{stateLabel(asset.uploadStatus)}</span><span>{stateLabel(asset.parseStatus)}</span></div>
          {asset.parseStatus === 'unsupported' && <small>원본은 보관되며 내부 화면 해석은 지원하지 않습니다.</small>}
          {asset.error && <Notice error>{asset.error}</Notice>}
          {asset.uploadStatus === 'processing' && asset.jobId && !files.some(file => file.job?.id === asset.jobId) &&
            <JobProgress job={{ id: asset.jobId, task: 'finalize', status: 'queued' }} label="파일 확인 계속 조회"
              onComplete={refresh} onFailure={refresh} />}
          <div className="ws-actions"><button onClick={() => setInspect(asset)}>파일 확인</button>
            <button disabled={busy} onClick={() => { setParent(asset); setTimeout(() => input.current?.click(), 0); }}>새 반입 버전 추가</button></div>
        </article>)}</div>
      </section>
      <section className="ws-section">
        {inspect && !inspected?.system ? <AssetInspector key={inspect.id} asset={inspected || inspect} refresh={refresh} onArchived={() => {
          onSelected(selected.filter(id => id !== inspect.id)); setInspect(null); refresh();
        }} /> : <div className="ws-empty">내 파일에서 ‘파일 확인’을 선택하면 원본과 해석 상태를 확인할 수 있습니다.</div>}
      </section>
    </div>
  </div>;
}

function AssetInspector({ asset: initial, refresh, onArchived }: { asset: Asset; refresh: () => void; onArchived: () => void }) {
  const workspaceClient = useWorkspaceClient();
  const [asset, setAsset] = useState(initial);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [archiving, setArchiving] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const downloading = useDownload();
  const preview = asset.previews?.find(item => item.page === page);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort; setError('');
    workspaceClient.get<{ asset: Asset; analysis?: Analysis }>(`/assets/${resource(initial.id)}`, abort.signal)
      .then(value => { if (!abort.signal.aborted) { setAsset(value.asset); setAnalysis(value.analysis || null); setPage(value.asset.previews?.[0]?.page ?? 1); } })
      .catch(reason => { if (!aborted(reason)) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [initial.id, initial.uploadStatus, retry]);
  const archive = async () => {
    if (asset.system || initial.system) return;
    if (!confirm('사용 목록에서 이 파일을 제외할까요? 원본 파일과 생성·검수 이력은 유지됩니다.')) return;
    setArchiving(true);
    try { await workspaceClient.remove(`/assets/${resource(asset.id)}`, controller.current?.signal); if (!controller.current?.signal.aborted) onArchived(); }
    catch (reason) { if (!aborted(reason)) setError(messageOf(reason)); }
    finally { if (!controller.current?.signal.aborted) setArchiving(false); }
  };
  return <div className="ws-inspector"><h2>{asset.name}</h2>
    <div className="ws-status-line"><span>반입 v{asset.importRevision || 1}</span><span>보관: {stateLabel(asset.uploadStatus)}</span><span>해석: {stateLabel(asset.parseStatus)}</span></div>
    <p className="ws-muted">반입 버전은 작업실에 파일을 반입한 순서입니다. 제작 도구의 원본 버전을 뜻하지 않습니다.</p>
    <div className="ws-actions"><button disabled={downloading.busy || asset.uploadStatus !== 'stored'} onClick={() =>
      void downloading.download(`/assets/${resource(asset.id)}/blob?kind=original`, asset.name)}>원본 내려받기</button>
      <button onClick={() => { setRetry(n => n + 1); refresh(); }}>상태 새로 확인</button>
      {!asset.system && !initial.system && <button disabled={archiving} onClick={() => void archive()}>사용 목록에서 제외</button>}</div>
    {(error || downloading.error) && <Notice error>{error || downloading.error}</Notice>}
    {(asset.previews?.length || 0) > 1 && <label className="ws-field">미리보기 페이지<select value={page} onChange={event => setPage(Number(event.target.value))}>
      {asset.previews!.map(preview => <option key={preview.page} value={preview.page}>{preview.page}페이지</option>)}</select></label>}
    <PrivatePreview path={asset.previews?.some(preview => preview.page === page) ? `/assets/${resource(asset.id)}/blob?kind=preview&page=${page}` : null}
      title={`${asset.name} 업로드 파일 미리보기`} format={asset.previews?.find(preview => preview.page === page)?.mime} />
    {preview && (preview.mime === 'image/png' || preview.ocrStatus !== undefined) && <Notice>
      <strong>{preview.page}페이지 · {ocrLabel(preview.ocrStatus)}</strong>
      {preview.ocrStatus && preview.ocrStatus !== 'complete' && <p>이 페이지는 AI의 이미지 직접 참조에서 제외됩니다.</p>}
      <p>문자 인식 상태는 AI 반영 여부나 시작 화면 비교 결과와 별개입니다.</p>
      <p>실제로 반영하거나 제외한 자료는 시안 결과의 ‘자료 적용 범위’에서 확인하세요.</p>
    </Notice>}
    {[...new Set([...(asset.warnings || []), ...(analysis?.warnings || [])])].map((warning, index) => <Notice key={index}>{warning}</Notice>)}
    {asset.parseStatus === 'unsupported' && <Notice>FIG 등 해석 미지원 파일은 원본 보관만 제공합니다. 함께 받은 이미지나 PDF를 참고 파일로 추가할 수 있습니다.</Notice>}
    {analysis?.text && <details><summary>추출된 내용 확인</summary><pre className="ws-text">{analysis.text}</pre></details>}
    <p className="ws-muted">원본 보관, 내용 해석, 화면 동작 검증은 각각 별도 상태입니다.</p>
  </div>;
}
