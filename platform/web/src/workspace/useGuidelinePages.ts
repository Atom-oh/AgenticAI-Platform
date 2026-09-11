import { useEffect, useState } from 'react';
import { messageOf, resource } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { guidelinePages, type GuidelinePage } from './guidelinePages';
import type { Ontology } from './types';

export function useGuidelinePages(productId?: string, guidelineId?: string, ontologyHash?: string) {
  const client = useWorkspaceClient();
  const [result, setResult] = useState<{ key: string; pages: GuidelinePage[] } | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const key = `${productId || ''}:${guidelineId || ''}:${ontologyHash || ''}`;
  useEffect(() => {
    const abort = new AbortController(); setResult(null); setError('');
    if (productId && guidelineId) client.get<{ ontology: Ontology }>(
      `/products/${resource(productId)}/ontology?revision=${resource(guidelineId)}`, abort.signal).then(value => {
        if (abort.signal.aborted) return;
        if (value.ontology?.productId !== productId || value.ontology.guidelineId !== guidelineId ||
            !ontologyHash || value.ontology.hash !== ontologyHash) throw new Error('이 규칙에 연결된 게시 지침을 확인하지 못했습니다. 상품과 규칙 버전을 새로 조회하세요.');
        setResult({ key, pages: guidelinePages(value.ontology) });
      }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); });
    return () => abort.abort();
  }, [client, productId, guidelineId, ontologyHash, key, retry]);
  return { pages: result?.key === key ? result.pages : [], error, ready: !productId || !guidelineId || result?.key === key,
    reload: () => setRetry(value => value + 1) };
}
