import { useEffect, useState } from 'react';
import { messageOf } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { Notice } from './shared';
import type { ComponentCatalog, ComponentCatalogSummary } from './types';

export default function ComponentCatalogPanel({ summary }: { summary?: ComponentCatalogSummary }) {
  const client = useWorkspaceClient();
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<ComponentCatalog | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); setCatalog(null); setError('');
    if (open && summary) client.get<{ catalog: ComponentCatalog }>('/components', abort.signal).then(value => {
      if (abort.signal.aborted) return;
      if (value.catalog?.hash !== summary.hash || value.catalog.id !== summary.id || value.catalog.version !== summary.version ||
          !Array.isArray(value.catalog.components)) throw new Error('컴포넌트 기준이 변경되었거나 목록을 확인하지 못했습니다. 작업 공간을 새로 조회하세요.');
      setCatalog(value.catalog);
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, open, summary?.hash, summary?.id, summary?.version, retry]);
  return <section className="ws-component-catalog"><h3>현재 플랫폼 컴포넌트</h3>
    <p>{summary ? `${summary.label} · v${summary.version}` : '현재 컴포넌트 기준 미확인'}</p>
    <p className="ws-muted">플랫폼에서 제공하는 기본 패키지입니다. 고객 사내 컴포넌트의 승인을 뜻하지 않습니다. 선택한 과거 시안의 고정 기준은 해당 라운드의 검사 근거에서 확인하세요.</p>
    {summary && <details onToggle={event => setOpen(event.currentTarget.open)}><summary>컴포넌트 목록·개발 속성</summary>
      {error ? <Notice error>{error} <button onClick={() => setRetry(value => value + 1)}>컴포넌트 다시 조회</button></Notice> :
        !catalog || catalog.hash !== summary.hash ? <p role="status">현재 목록 조회 중…</p> : <>
          <dl><dt>고정 패키지 해시</dt><dd>{catalog.hash}</dd></dl>
          {catalog.components.map(component => <details key={component.name}><summary>{component.name}</summary>
            <p>{component.description}</p><dl>{Object.entries(component.props || {}).map(([name, value]) =>
              <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
            <p>허용 변화: {component.variationAxes?.join(' · ') || '없음'}</p></details>)}
        </>}
    </details>}
  </section>;
}
