import { useEffect, useState } from 'react';
import { previewFrameDocument } from './shared';
import type { ComponentCatalogSummary } from './types';

const ROOT = '/studio-samples/';
const SAMPLE_IDS = new Set(['amount-review', 'required-consent', 'product-compare']);
const SHA = /^[a-f0-9]{64}$/;
const FILES = { preview: 'preview.html', source: 'source.zip', dist: 'dist.zip', guide: 'guide.md', contract: 'contract.json' } as const;
const STALE = '예제가 현재 React 컴포넌트 기준과 일치하지 않거나 기준을 확인할 수 없습니다. 작업 공간을 새로 조회한 뒤 다시 시도하세요.';
const INVALID = '예제 목록의 형식이나 파일 경로를 확인할 수 없습니다. 예제를 다시 불러오세요.';
type Sample = {
  id: string; title: string; description: string; checks: string[]; components: string[];
  sourceHash: string; bundleHash: string;
} & Record<keyof typeof FILES, string>;
type Manifest = { schemaVersion: 1; catalog: ComponentCatalogSummary; samples: Sample[] };

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && !!value.trim() && value.length <= maximum;
}
function strings(value: unknown, count: number, maximum: number): value is string[] {
  return Array.isArray(value) && value.length > 0 && value.length <= count && value.every(item => text(item, maximum));
}
function knownCatalog(value: unknown): value is ComponentCatalogSummary {
  return object(value) && text(value.id, 100) && text(value.version, 80) && text(value.label, 180) &&
    typeof value.hash === 'string' && SHA.test(value.hash);
}
function matches(left: ComponentCatalogSummary, right?: ComponentCatalogSummary) {
  return knownCatalog(right) && left.hash === right.hash && left.id === right.id && left.version === right.version;
}
function validate(value: unknown, catalog: ComponentCatalogSummary): Manifest {
  if (!object(value) || value.schemaVersion !== 1) throw new Error(INVALID);
  if (!knownCatalog(value.catalog) || !matches(value.catalog, catalog)) throw new Error(STALE);
  if (!Array.isArray(value.samples) || value.samples.length !== SAMPLE_IDS.size) throw new Error(INVALID);
  const seen = new Set<string>();
  for (const sample of value.samples) {
    if (!object(sample) || typeof sample.id !== 'string' || !SAMPLE_IDS.has(sample.id) || seen.has(sample.id) ||
        !text(sample.title, 180) || !text(sample.description, 1500) ||
        !strings(sample.checks, 20, 500) || !strings(sample.components, 30, 80) ||
        typeof sample.sourceHash !== 'string' || !SHA.test(sample.sourceHash) ||
        typeof sample.bundleHash !== 'string' || !SHA.test(sample.bundleHash)) throw new Error(INVALID);
    seen.add(sample.id);
    for (const [field, filename] of Object.entries(FILES)) {
      // Exact names reject URLs, encoded traversal, query strings and another
      // sample's files before any preview or download link is rendered.
      if (sample[field] !== `${sample.id}/${filename}`) throw new Error(INVALID);
    }
  }
  return value as Manifest;
}

async function readText(path: string, maximum: number, signal: AbortSignal): Promise<string> {
  const response = await fetch(path, { signal, credentials: 'omit', redirect: 'error', cache: 'no-store' });
  if (!response.ok || !response.body) throw new Error('static-file-unavailable');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximum) { await reader.cancel(); throw new Error('static-file-too-large'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}

function SamplePreview({ sample }: { sample: Sample }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setHtml(null); setError(false);
    void readText(ROOT + sample.preview, 2_500_000, abort.signal).then(value => {
      if (!abort.signal.aborted) setHtml(previewFrameDocument(value, true));
    }).catch(() => { if (!abort.signal.aborted) setError(true); });
    return () => abort.abort();
  }, [sample.preview, retry]);
  if (error) return <div className="ws-notice ws-error" role="alert">예제 미리보기를 불러오지 못했습니다.
    <button type="button" onClick={() => setRetry(value => value + 1)}>미리보기 다시 불러오기</button></div>;
  if (html === null) return <p role="status">예제 미리보기를 불러오고 있습니다…</p>;
  return <div className="ws-sample-preview"><iframe title={`${sample.title} 예제 미리보기`}
    sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={html} /></div>;
}

export default function SampleGallery({ catalog, active = true }: { catalog?: ComponentCatalogSummary; active?: boolean }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Manifest | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setLoaded(null); setSelectedId(''); setError('');
    if (open && active) {
      if (!knownCatalog(catalog)) setError(STALE);
      else void readText(ROOT + 'index.json', 64_000, abort.signal).then(body => {
        const manifest = validate(JSON.parse(body), catalog);
        if (!abort.signal.aborted) { setLoaded(manifest); setSelectedId(manifest.samples[0].id); }
      }).catch(reason => {
        if (!abort.signal.aborted) setError(reason instanceof Error && [STALE, INVALID].includes(reason.message)
          ? reason.message : '예제 목록을 불러오지 못했습니다. 다시 시도하세요.');
      });
    }
    return () => abort.abort();
  }, [open, active, catalog?.hash, catalog?.id, catalog?.version, retry]);
  // Hide old samples immediately when the authoritative catalog changes,
  // including the render before the new fetch effect runs.
  const manifest = open && active && loaded && matches(loaded.catalog, catalog) ? loaded : null;
  const selected = manifest?.samples.find(sample => sample.id === selectedId);
  return <section className="ws-samples ws-section" aria-label="React 예제로 연습하기">
    <details className="ws-sample-disclosure" onToggle={event => setOpen(event.currentTarget.open)}>
      <summary><span className="ws-sample-summary-title">React 샘플 둘러보기</span>
        <span className="ws-sample-summary-note">3개 동작 예제 / 고정 React 구성</span></summary>
      {open && active && <div className="ws-sample-content">
        <p>플랫폼 기본 React 코드로 만든 합성 예제입니다. 고객 승인 결과가 아니며 실제 은행 거래를 수행하지 않습니다.</p>
        {error ? <div className="ws-notice ws-error" role="alert">{error}
          <button type="button" onClick={() => setRetry(value => value + 1)}>예제 다시 불러오기</button></div>
          : !manifest ? <p role="status">예제 목록을 불러오고 있습니다…</p> : <>
            <p className="ws-muted">{manifest.catalog.label} · {manifest.catalog.version}</p>
            <div className="ws-sample-cards">
              {manifest.samples.map(sample => <article className={`ws-sample-card${sample.id === selectedId ? ' is-selected' : ''}`} key={sample.id}>
                <h3>{sample.title}</h3><p>{sample.description}</p>
                <ul>{sample.checks.map((check, index) => <li key={index}>{check}</li>)}</ul>
                <button type="button" aria-pressed={sample.id === selectedId} onClick={() => setSelectedId(sample.id)}>
                  {sample.title} 미리보기</button>
              </article>)}
            </div>
            {selected && <div className="ws-sample-selected">
              <h3>{selected.title} · 직접 조작해 보기</h3>
              <SamplePreview key={`${selected.id}:${selected.sourceHash}:${selected.bundleHash}`} sample={selected} />
              <div className="ws-sample-downloads">
                <a href={ROOT + selected.preview} download={`${selected.id}-preview.html`}>HTML 예제 내려받기</a>
                <a href={ROOT + selected.guide} download={`${selected.id}-guide.md`}>연습 가이드 내려받기</a>
              </div>
              <p>HTML과 가이드를 내려받아 아래 파일 준비에 올려 연습할 수 있습니다. 예제를 선택해도 모델 입력에 자동으로 추가되지 않습니다.</p>
              <p className="ws-muted">React 소스 ZIP은 현재 직접 반입할 수 없습니다. 개발용으로 내려받아 사용하세요.</p>
              <details className="ws-sample-developer"><summary>개발용 소스·검증 규칙</summary>
                <p>React 소스 ZIP에는 고정된 ui 코드·타입·토큰과 화면 소스가 포함됩니다. MD 설명만 있는 자료가 아닙니다.</p>
                <p>사용 컴포넌트: {selected.components.join(', ')}</p>
                <div className="ws-sample-downloads">
                  <a href={ROOT + selected.source} download={`${selected.id}-source.zip`}>React 소스 ZIP</a>
                  <a href={ROOT + selected.dist} download={`${selected.id}-dist.zip`}>배포용 dist ZIP</a>
                  <a href={ROOT + selected.contract} download={`${selected.id}-contract.json`}>검증 규칙 JSON</a>
                </div>
                <dl><dt>컴포넌트 기준</dt><dd>{manifest.catalog.hash}</dd>
                  <dt>소스 식별</dt><dd>{selected.sourceHash}</dd><dt>번들 식별</dt><dd>{selected.bundleHash}</dd></dl>
              </details>
            </div>}
          </>}
      </div>}
    </details>
  </section>;
}
