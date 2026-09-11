import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { aborted, messageOf, pollJob, readPrivateBlob } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { stateLabel } from './rules';
import type { Job } from './types';

export function Notice({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return <div className={`ws-notice ${error ? 'ws-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}
export function JobProgress({ job: initial, label, onComplete, onFailure }: {
  job: Job; label: string; onComplete: (job: Job) => void; onFailure?: (job: Job) => void;
}) {
  const workspaceClient = useWorkspaceClient();
  const [job, setJob] = useState(initial);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const callbacks = useRef({ onComplete, onFailure });
  callbacks.current = { onComplete, onFailure };
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    let last = initial;
    pollJob(workspaceClient, initial.id, { signal: controller.signal, onUpdate: value => { last = value; setJob(value); } })
      .then(value => { if (!controller.signal.aborted) callbacks.current.onComplete(value); })
      .catch(reason => {
        if (aborted(reason) || controller.signal.aborted) return;
        setError(messageOf(reason)); callbacks.current.onFailure?.(last);
      });
    return () => controller.abort();
  }, [workspaceClient, initial.id, retry]);
  const percent = typeof job.progress === 'number' ? job.progress : typeof job.progress === 'object' ? job.progress.percent : undefined;
  const detail = typeof job.progress === 'string' ? job.progress : typeof job.progress === 'object' ? job.progress.message : '';
  return <div className="ws-job" aria-live="polite">
    <div><strong>{label}</strong><span>{stateLabel(job.status)}</span></div>
    {percent !== undefined && <progress aria-label={`${label} 진행률`} value={Math.max(0, Math.min(100, percent))} max={100} />}
    {detail && <p>{detail}</p>}
    {error && <Notice error>{error} <button type="button" onClick={() => setRetry(n => n + 1)}>진행 다시 조회</button></Notice>}
  </div>;
}

// The sandbox origin stays opaque: never add allow-same-origin to either preview.
export function previewDocument(html: string, executable: boolean): string {
  const policy = `default-src 'none'; script-src ${executable ? "'unsafe-inline'" : "'none'"}; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
  return `<meta http-equiv="Content-Security-Policy" content="${policy}">${html}`;
}
function previewFrameDocument(html: string, executable: boolean): string {
  // A document's own CSP does not prevent it from navigating its own frame.
  // This trusted parent controls navigation of the opaque content frame instead.
  // srcdoc remains usable with frame-src 'none'; no URL navigation is needed.
  const shell = document.implementation.createHTMLDocument('비공개 미리보기');
  const policy = shell.createElement('meta');
  policy.httpEquiv = 'Content-Security-Policy';
  policy.content = "frame-src 'none'";
  shell.head.prepend(policy);
  const style = shell.createElement('style');
  style.textContent = 'html,body{margin:0;width:100%;height:100%;overflow:hidden}iframe{display:block;width:100%;height:100%;border:0}';
  shell.head.append(style);
  const content = shell.createElement('iframe');
  content.title = '시안 내용';
  content.setAttribute('data-workspace-preview-content', '');
  content.setAttribute('sandbox', executable ? 'allow-scripts' : '');
  content.referrerPolicy = 'no-referrer';
  // DOM serialization escapes the entire untrusted attribute value, including
  // quotes and closing iframe tags; imported HTML never becomes wrapper markup.
  content.srcdoc = previewDocument(html, executable);
  shell.body.append(content);
  return '<!doctype html>' + shell.documentElement.outerHTML;
}
export function PrivatePreview({ path, title, executable = false, height = 560, width, format }: {
  path: string | null; title: string; executable?: boolean; height?: number; width?: number; format?: string;
}) {
  const workspaceClient = useWorkspaceClient();
  const [result, setResult] = useState<{ path: string; url?: string; html?: string; mime: string } | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let url = '';
    setResult(null); setError('');
    if (path) void readPrivateBlob(workspaceClient, path, controller.signal).then(async value => {
      if (controller.signal.aborted) return;
      // Active bytes intentionally travel as attachment/octet-stream. Interpret
      // only the requested preview's known format, never an original download.
      const mime = executable ? 'text/html' : format || value.mime.split(';')[0];
      if (mime === 'text/html') {
        const html = await value.blob.text();
        if (!controller.signal.aborted) setResult({ path, html, mime: value.mime });
      } else if (['image/png', 'image/jpeg', 'image/svg+xml', 'image/webp'].includes(mime)) {
        url = URL.createObjectURL(new Blob([value.blob], { type: mime }));
        if (!controller.signal.aborted) setResult({ path, url, mime: value.mime });
        else URL.revokeObjectURL(url);
      } else setError('이 파일의 미리보기는 지원하지 않습니다. 원본 내려받기를 이용하세요.');
    }).catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [workspaceClient, path, retry, format, executable]);
  if (!path) return <div className="ws-empty">미리보기가 없습니다. 원본 보관과 화면 해석은 별도 상태입니다.</div>;
  if (error) return <Notice error>{error} <button onClick={() => setRetry(n => n + 1)}>미리보기 다시 조회</button></Notice>;
  if (!result || result.path !== path) return <div className="ws-empty" role="status">비공개 미리보기를 불러오고 있습니다…</div>;
  return <div className="ws-preview" style={{ minHeight: Math.min(height, 240) }}>
    {result.html !== undefined
      ? <iframe title={title} sandbox={executable ? 'allow-scripts' : ''} referrerPolicy="no-referrer"
          srcDoc={previewFrameDocument(result.html, executable)} style={{ height, ...(width ? { width } : {}) }} />
      : <img src={result.url} alt={title} />}
  </div>;
}

export function useDownload() {
  const workspaceClient = useWorkspaceClient();
  const controllers = useRef(new Set<AbortController>());
  const urls = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => () => {
    controllers.current.forEach(controller => controller.abort());
    urls.current.forEach((timer, url) => { clearTimeout(timer); URL.revokeObjectURL(url); }); urls.current.clear();
  }, [workspaceClient]);
  const download = useCallback(async (path: string, filename: string) => {
    if (controllers.current.size) return;
    const controller = new AbortController(); controllers.current.add(controller); setBusy(true); setError('');
    try {
      const { blob } = await readPrivateBlob(workspaceClient, path, controller.signal);
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(new Blob([blob], { type: 'application/octet-stream' }));
      const link = document.createElement('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
      const timer = setTimeout(() => { URL.revokeObjectURL(url); urls.current.delete(url); }, 10_000);
      urls.current.set(url, timer);
    } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
    finally { controllers.current.delete(controller); if (!controller.signal.aborted) setBusy(false); }
  }, [workspaceClient]);
  return { download, busy, error };
}

export function ModelPicker({ models, value, onChange, disabled }: {
  models: { id: string; label: string; provider?: string }[]; value: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  const id = useId();
  return <div className="ws-field"><label htmlFor={id}>AI 모델</label>
    <select id={id} value={value} onChange={event => onChange(event.target.value)} disabled={disabled || !models.length}>
      {!models.some(model => model.id === value) && <option value="">모델을 선택하세요</option>}
      {models.map(model => <option value={model.id} key={model.id}>{model.label}{model.provider ? ` · ${model.provider}` : ''}</option>)}
    </select>
  </div>;
}
