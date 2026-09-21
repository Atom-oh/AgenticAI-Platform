import { useEffect, useState } from 'react';
import { messageOf, resource } from './client';
import { useWorkspaceClient } from './WorkspaceScope';
import { Notice } from './shared';
import type { Asset, GuideCategory, GuidePage, GuideRef } from './types';

export const GUIDE_CATEGORIES: Record<GuideCategory, string> = {
  foundation: '시각 디자인·컴포넌트', interaction: '화면 동작·사용자 흐름', graphics: '아이콘·그래픽',
  content: '콘텐츠 운영 디자인', writing: 'UX 라이팅', general: '공통 가이드',
  specification: '화면설계서', source: '참고 소스 · 실행 미검증', inventory: 'IA·화면 목록',
};
const TOPICS = [
  { title: '정의', description: '목적과 적용 범위', query: '정의' },
  { title: '구조', description: '요소와 배치 기준', query: '구조' },
  { title: '상태', description: '입력·오류·복구', query: '상태' },
  { title: '적용', description: '사용 조건과 예외', query: '적용' },
];
const sameAddress = (left: GuideRef, right: GuideRef) =>
  left.assetId === right.assetId && left.sourceId === right.sourceId && left.page === right.page;
const samePage = (left: GuideRef, right: GuideRef) => sameAddress(left, right) &&
  left.sourceSha256 === right.sourceSha256 && left.textSha256 === right.textSha256;

export default function GuidelineLibrary({ assets, selected, onSelected, onContinue, onUpload }: {
  assets: Asset[]; selected: GuideRef[]; onSelected: (refs: GuideRef[]) => void; onContinue: () => void; onUpload: () => void;
}) {
  const client = useWorkspaceClient();
  const packs = assets.filter(asset => !asset.archived && asset.uploadStatus === 'stored' && asset.guidelineSources?.length);
  const [chosenPack, setChosenPack] = useState('');
  const [sourceQuery, setSourceQuery] = useState('');
  const matchingPacks = packs.filter(asset => !sourceQuery.trim() || [asset.name, ...(asset.guidelineSources || []).flatMap(source =>
    [source.name, ...Object.values(source.metadata || {})])].join(' ').normalize('NFKC').toLowerCase().includes(sourceQuery.trim().normalize('NFKC').toLowerCase()));
  const pack = packs.find(asset => asset.id === chosenPack) || packs[0];
  const [sourceId, setSourceId] = useState('');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [cursor, setCursor] = useState('');
  const [result, setResult] = useState<{ pages: GuidePage[]; total: number; cursor?: string | null } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setResult(null); setError('');
    if (!pack) { setLoading(false); return; }
    const abort = new AbortController(); setLoading(true);
    const parameters = new URLSearchParams({ q: search, sourceId, cursor: cursor || '0' });
    client.get<{ pages: GuidePage[]; total: number; cursor?: string | null }>(
      `/assets/${resource(pack.id)}/guidelines?${parameters}`, abort.signal).then(value => {
      if (!abort.signal.aborted) setResult(value);
    }).catch(reason => { if (!abort.signal.aborted) setError(messageOf(reason)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [client, pack?.id, sourceId, search, cursor, retry]);
  const find = (value: string) => { setQuery(value); setSearch(value); setCursor(''); };
  const toggle = (page: GuidePage) => {
    if (!pack) return;
    const ref: GuideRef = { assetId: pack.id, sourceId: page.sourceId, page: page.page,
      sourceSha256: page.sourceSha256, textSha256: page.textSha256 };
    if (selected.some(item => samePage(item, ref))) onSelected(selected.filter(item => !samePage(item, ref)));
    else if (selected.length < 12 || selected.some(item => sameAddress(item, ref)))
      onSelected([...selected.filter(item => !sameAddress(item, ref)), ref]);
  };
  return <section className="ws-section ws-guide-library">
    <div className="ws-section-heading"><div><h2>고객 가이드 · AI UX 기준</h2>
      <p>업무 흐름·디자인·그래픽·콘텐츠·문구의 원문을 찾아, 이번 화면에 적용할 페이지를 선택하세요.</p></div>
      <button className="ws-primary" disabled={!selected.length} onClick={onContinue}>선택한 {selected.length}페이지로 규칙 만들기</button></div>
    <div className="ws-guide-method" aria-label="가이드 검토 관점">{TOPICS.map(topic => <button key={topic.title}
      onClick={() => find(topic.query)}><strong>{topic.title}</strong><span>{topic.description}</span></button>)}</div>
    {!packs.length ? <div className="ws-empty"><h3>아직 반입한 고객 가이드가 없습니다</h3>
      <p>PDF·PPTX에서 준비한 UX 가이드 묶음(JSON)을 파일 준비에서 비공개 보관하면, 원본별 전체 페이지를 검색할 수 있습니다.</p>
      <button onClick={onUpload}>가이드 파일 반입하기</button></div> : <>
      <div className="ws-guide-search">
        <label className="ws-field">자료 이름·원본 화면 ID<input value={sourceQuery} maxLength={128}
          placeholder="자료명, sid, 원본 경로로 찾기" onChange={event => setSourceQuery(event.target.value)} /></label>
        <label className="ws-field">가이드 묶음<select value={pack?.id || ''} onChange={event => {
          setChosenPack(event.target.value); setSourceId(''); setCursor(''); setResult(null);
        }}>{pack && !matchingPacks.some(asset => asset.id === pack.id) && <option value={pack.id}>{pack.name} · 현재 선택</option>}
          {matchingPacks.map(asset => <option key={asset.id} value={asset.id}>{asset.name} · 반입 v{asset.importRevision || 1}</option>)}</select></label>
        <form onSubmit={event => { event.preventDefault(); find(query.trim()); }}>
          <label className="ws-field">원문 검색<input maxLength={120} value={query} onChange={event => setQuery(event.target.value)}
            placeholder="예: 유효성, 취소, 버튼, 오류 메시지, 네이밍" /></label>
          <button type="submit">검색</button></form>
      </div>
      <div className="ws-guide-sources" aria-label="가이드 원본 선택">
        <button aria-pressed={!sourceId} onClick={() => { setSourceId(''); setCursor(''); }}>전체 원본</button>
        {pack?.guidelineSources?.map(source => <button key={source.id} aria-pressed={sourceId === source.id}
          className={sourceId === source.id ? 'is-selected' : ''} onClick={() => { setSourceId(source.id); setCursor(''); }}>
          <strong>{GUIDE_CATEGORIES[source.category]}</strong><span>{source.name}</span>
          <small>텍스트 있는 페이지 {source.nonemptyPages}/{source.pageCount} · 원문 대조 필요</small>
          {source.metadata?.sid && <small>원본 ID: {source.metadata.sid}</small>}
          {source.metadata?.status && <small>원본 표기: {source.metadata.status} · 플랫폼 승인 아님</small>}
        </button>)}
      </div>
      <Notice>텍스트 추출본이며 그림·레이아웃은 포함하지 않습니다. JSON으로 준비한 원본은 로컬에 별도 보관되며 직접 반입한 원본은 내 파일에서 내려받습니다.
        페이지 번호는 문서에 인쇄된 쪽수가 아닌 파일의 순서입니다. 파일명만으로 최신 기준이나 승인 여부를 판단하지 마세요.</Notice>
      {error && <Notice error>{error} <button onClick={() => setRetry(value => value + 1)}>다시 조회</button></Notice>}
      {loading && <p role="status">원문 페이지를 찾고 있습니다…</p>}
      {result && <p role="status">{result.total}페이지 검색 · AI 적용 {selected.length}/12페이지</p>}
      {result?.total === 0 && <div className="ws-empty">일치하는 원문이 없습니다. 다른 검색어 또는 원본을 선택하세요.</div>}
      <div className="ws-guide-pages">{result?.pages.map(page => {
        const checked = selected.some(ref => samePage(ref, { ...page, assetId: pack!.id }));
        const source = pack?.guidelineSources?.find(source => source.id === page.sourceId);
        const deleted = ['삭제', '폐기', 'deleted', 'deprecated', 'discarded'].includes((source?.metadata?.status || '').normalize('NFKC').trim().toLowerCase());
        const unusable = deleted || page.truncated || !page.text.trim();
        return <article className={`ws-guide-page${checked ? ' is-selected' : ''}`} key={`${page.sourceId}:${page.page}`}>
          <div className="ws-section-heading"><div><span className="ws-guide-kind">{GUIDE_CATEGORIES[page.category]}</span>
            <h3>{page.sourceName} · {page.page}페이지</h3></div>
            <label className="ws-check"><input type="checkbox" aria-label={`${page.sourceName} ${page.page}페이지 적용`}
              checked={checked} disabled={unusable || (!checked && selected.length >= 12)} onChange={() => toggle(page)} />AI 기준에 포함</label></div>
          <p className="ws-guide-excerpt">{page.text.slice(0, 500) || '추출 가능한 텍스트가 없습니다.'}</p>
          {source?.metadata && <details><summary>원본 ID·버전·의존성 확인</summary>
            <dl>{Object.entries(source.metadata).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
            <p>텍스트·소스의 원래 표기이며 실행 패키지나 플랫폼 승인 상태가 아닙니다.</p></details>}
          {deleted && <Notice>삭제·폐기 원본은 이력 조회만 가능하며 생성 기준에 포함할 수 없습니다.</Notice>}
          {unusable && <Notice>텍스트가 비어 있거나 상한에서 잘렸습니다. 원본을 확인하고 필요한 범위를 다시 준비하세요.</Notice>}
          <details><summary>{page.page}페이지 추출 원문 전체</summary><pre className="ws-text">{page.text}</pre>
            <p className="ws-muted">원본 SHA-256: {page.sourceSha256}</p></details>
        </article>;
      })}</div>
      {result && <div className="ws-actions">
        <button disabled={!cursor || loading} onClick={() => setCursor(String(Math.max(0, Number(cursor) - 8)))}>이전 페이지</button>
        <button disabled={!result.cursor || loading} onClick={() => setCursor(result.cursor || '')}>다음 페이지</button>
        <button onClick={onUpload}>다른 가이드 반입</button>
      </div>}
    </>}
    {selected.length > 0 && <div className="ws-guide-selection"><h3>이번 화면에 적용할 원문</h3>
      {selected.map(ref => {
        const source = assets.find(asset => asset.id === ref.assetId)?.guidelineSources?.find(source => source.id === ref.sourceId);
        return <div key={`${ref.assetId}:${ref.sourceId}:${ref.page}`}><span>{source?.name || ref.sourceId} · {ref.page}페이지</span>
          <button aria-label={`${source?.name || ref.sourceId} ${ref.page}페이지 제외`} onClick={() => onSelected(selected.filter(item => !samePage(item, ref)))}>제외</button></div>;
      })}
      <p>선택은 승인과 별개입니다. AI가 제안한 근거·조건·예외를 규칙 화면에서 확인하세요.</p>
    </div>}
    <details><summary>고객 가이드에서 실행 가능한 화면까지</summary>
      <p>원문 선택 → 규칙 제안 → 조건·예외 검토 → 버전 승인 → React 생성·브라우저 검수 → 결과 승인</p>
      <p>문서에 있는 컴포넌트 이름은 실행 코드가 아닙니다. 현재 제공되는 React 컴포넌트와 연결되지 않는 요구는 확인할 내용으로 남깁니다.</p>
    </details>
  </section>;
}
