import { useEffect, useState } from 'react';
import { messageOf, readPrivateBlob, resource } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { Notice, useDownload } from './shared';
import type { Asset, Product } from './types';

export default function GuidelineHistory({ product, assets }: { product: Product; assets: Asset[] }) {
  const client = useWorkspaceClient();
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState('');
  const [text, setText] = useState<{ id: string; value: string } | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const downloading = useDownload();
  const guides = assets.filter(asset => asset.system && asset.productId === product.id)
    .sort((a, b) => (b.importRevision || 0) - (a.importRevision || 0));
  const guide = guides.find(asset => asset.id === chosen) ||
    guides.find(asset => asset.id === product.guideAssetId || asset.guidelineId === product.publishedGuidelineId) || guides[0];
  useEffect(() => {
    const abort = new AbortController(); setText(null); setError('');
    if (open && guide) readPrivateBlob(client, `/assets/${resource(guide.id)}/blob?kind=original`, abort.signal).then(async value => {
      if (value.sha256 !== guide.sha256) throw new Error('게시 지침 원본의 해시가 일치하지 않습니다.');
      const content = await value.blob.text();
      if (!abort.signal.aborted) setText({ id: guide.id, value: content });
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, open, guide?.id, guide?.sha256, retry]);
  return <section className="ws-guideline-history"><h3>게시 지침 이력</h3>
    <p className="ws-muted">게시된 원본은 읽기 전용입니다. 지침 수정은 기획 초안을 새 버전으로 게시하며, 이전 원본과 시안의 연결은 유지됩니다.</p>
    {!guides.length ? <p>조회된 게시 지침 원본이 없습니다. 지침 게시 후 작업을 새로 조회하세요.</p> :
      <details onToggle={event => setOpen(event.currentTarget.open)}><summary>지침 버전과 원본 확인</summary>
        <label className="ws-field">게시 지침 버전<select aria-label="게시 지침 버전" value={guide?.id || ''} onChange={event => setChosen(event.target.value)}>
          {guides.map(asset => <option key={asset.id} value={asset.id}>
            {asset.importRevision ? `${asset.importRevision}차 지침` : '버전 기록 미확인'}{asset.guidelineId === product.publishedGuidelineId ? ' · 현재 게시 기준' : ' · 이전 기준'}
          </option>)}</select></label>
        {error ? <Notice error>{error} <button onClick={() => setRetry(value => value + 1)}>지침 원본 다시 조회</button></Notice> :
          text?.id === guide?.id ? <pre className="ws-text">{text?.value}</pre> : <p role="status">게시 원본 조회 중…</p>}
        <button disabled={!guide || downloading.busy} onClick={() => guide && void downloading.download(
          `/assets/${resource(guide.id)}/blob?kind=original`, guide.name)}>게시 지침 원본 내려받기</button>
        {downloading.error && <Notice error>{downloading.error}</Notice>}
      </details>}
  </section>;
}
