import { useEffect, useMemo, useState } from 'react';
import { messageOf, resource } from './client';
import { useWorkspaceClient, useWorkspaceScope } from './WorkspaceScope';
import { Notice, previewFrameDocument } from './shared';
import ComparisonTray from './ComparisonTray';

type Variant = { artifactId: string; sourceHash: string; bundleHash: string; passed: boolean };
export default function DesignComparison({ designId, variants, selected, onSelect }: {
  designId: string; variants: Variant[]; selected: string; onSelect: (id: string) => void;
}) {
  const { project, actorId, role } = useWorkspaceScope();
  // Remount on a scope/revision change so old results cannot label a new selection.
  return <ComparisonTray key={JSON.stringify([project?.id, actorId, role, designId, variants])}>
    {variants.map((variant, index) => <article key={variant.artifactId} className="ws-comparison-card">
      <h4>시안 {index + 1}</h4><p>{variant.passed ? '검증 통과' : '수정 필요'}</p>
      <VariantPreview designId={designId} variant={variant} title={`시안 ${index + 1} 비교 미리보기`} />
      <button aria-pressed={variant.artifactId === selected} onClick={() => onSelect(variant.artifactId)}>시안 {index + 1} 검토하기</button>
    </article>)}
  </ComparisonTray>;
}

function VariantPreview({ designId, variant, title }: { designId: string; variant: Variant; title: string }) {
  const client = useWorkspaceClient();
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); setHtml(null); setError('');
    void client.get<{ previewHtml: string; sourceHash: string; bundleHash: string; verification: { verdict: string } }>(
      `/designs/${resource(designId)}/artifacts/${resource(variant.artifactId)}`, abort.signal).then(value => {
      if (abort.signal.aborted) return;
      if (value.sourceHash !== variant.sourceHash || value.bundleHash !== variant.bundleHash ||
          (variant.passed && value.verification.verdict !== 'pass')) throw new Error('선택한 시안과 표시할 검증 근거가 다릅니다.');
      setHtml(value.previewHtml);
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, designId, variant.artifactId, variant.sourceHash, variant.bundleHash, variant.passed, retry]);
  const document = useMemo(() => html === null ? '' : previewFrameDocument(html, true), [html]);
  if (error) return <Notice error>{error}<button onClick={() => setRetry(value => value + 1)}>비교 미리보기 다시 조회</button></Notice>;
  if (html === null) return <p role="status">비교할 시안을 불러오고 있습니다…</p>;
  return <iframe title={title} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={document} />;
}
