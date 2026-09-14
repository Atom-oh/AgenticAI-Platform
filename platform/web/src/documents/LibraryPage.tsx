import { useCallback, useEffect, useRef, useState } from 'react';
import { DocumentBoundary, Notice, analysisHref, libraryHref, useDocumentQuery, useDocumentScope, usePrivateTask } from './DocumentScope';
import type { DocumentRecord, Reference } from './types';
import { kindLabel, Sample, Status } from './presentation';
import UploadForm from './UploadForm';
import SourceReader from './SourceReader';
import SampleCatalog, { useSampleCatalog } from './SampleCatalog';

export default function LibraryPage() {
  return <DocumentBoundary view="documents"><Library /></DocumentBoundary>;
}
function Library() {
  const { route, client } = useDocumentScope();
  const [query, setQuery] = useState(route.ref || '');
  const [search, setSearch] = useState(route.ref || '');
  const [upload, setUpload] = useState(!!(route.ref || route.register) && !route.documentId);
  const references = useDocumentQuery<{ references: Reference[]; backend: string }>('/documents/references');
  const catalog = useSampleCatalog();
  const list = useDocumentQuery<{ documents: DocumentRecord[]; cursor?: string }>('/documents' + (search ? '?q=' + encodeURIComponent(search) : ''));
  const [more, setMore] = useState<DocumentRecord[]>([]);
  const [cursor, setCursor] = useState<string>();
  const pagination = usePrivateTask();
  const listGeneration = useRef(0);
  const resetPages = useCallback(() => {
    listGeneration.current += 1; pagination.cancel(); setMore([]); setCursor(undefined);
  }, [pagination.cancel]);
  const reloadList = () => { resetPages(); list.reload(); };
  useEffect(() => { resetPages(); setCursor(list.data?.cursor); }, [list.data, resetPages]);
  const documents = [...list.data?.documents || [], ...more].filter((item, index, all) => all.findIndex(row => row.id === item.id) === index);
  return <div className="doc-stack">
    <div className="doc-row doc-between">
      <p className="doc-muted">개인 문서함은 프로젝트 생성 없이 사용할 수 있습니다.</p>
      <a href={analysisHref(route.analysisId, route.projectId)}>{route.analysisId ? '분석 결과로 돌아가기' : '규정 영향 분석 열기'}</a>
    </div>
    <div className="doc-grid doc-library-grid">
      <section className="doc-panel doc-stack" aria-label="문서 목록">
        <div className="doc-row doc-between"><h3>문서 목록</h3><button onClick={reloadList}>목록 새로 조회</button></div>
        <form className="doc-stack" onSubmit={event => {
          event.preventDefault(); resetPages();
          if (query.trim() === search) list.reload(); else setSearch(query.trim());
        }}>
          <label>문서 검색<input value={query} onChange={event => setQuery(event.target.value)} placeholder="제목 또는 연결 대상" maxLength={240} /></label>
          <button type="submit">검색</button>
        </form>
        {list.error && <Notice error>{list.error}</Notice>}
        {list.busy && <p role="status">문서 목록을 불러오고 있습니다…</p>}
        {list.data && !documents.length && <Notice>등록된 문서가 없습니다. 원문을 등록한 뒤 검토를 요청하세요.</Notice>}
        <ul className="doc-list">
          {documents.map(document => <li key={document.id} className="doc-stack">
            <a className="doc-card-title" href={libraryHref({ documentId: document.id, projectId: route.projectId, analysisId: route.analysisId })}
              aria-current={document.id === route.documentId ? 'page' : undefined}>{document.title}</a>
            <div className="doc-row"><span className="doc-chip">{kindLabel(document.kind)}</span>
              <Status value={document.status} /><Sample provenance={document.provenance} /></div>
            <p className="doc-muted">{document.approvedRevisionId ? '승인된 버전 있음' : '아직 승인된 원문 없음'}{!document.graphRef ? ' · 관계 목록 미연결' : ''}</p>
          </li>)}
        </ul>
        {cursor && <button disabled={pagination.busy} onClick={() => void pagination.run(async (signal, current) => {
          const generation = listGeneration.current;
          const params = new URLSearchParams({ cursor, ...(search ? { q: search } : {}) });
          const page = await client.get<{ documents: DocumentRecord[]; cursor?: string }>('/documents?' + params, signal);
          if (current() && generation === listGeneration.current) {
            setMore(rows => [...rows, ...page.documents]); setCursor(page.cursor === cursor ? undefined : page.cursor);
          }
        })}>문서 더 보기</button>}
        {pagination.error && <Notice error>{pagination.error}</Notice>}
        <button className="doc-primary" onClick={() => {
          if (route.documentId) window.location.hash = libraryHref({ projectId: route.projectId, analysisId: route.analysisId, register: true });
          else setUpload(true);
        }}>새 문서 등록</button>
        <a href={libraryHref({ projectId: route.projectId, analysisId: route.analysisId })}
          onClick={() => setUpload(false)}>합성 예제 구성 보기</a>
      </section>
      <div className="doc-stack">
        {route.documentId ? <SourceReader references={references.data?.references || []} onChanged={reloadList} templates={catalog.data} />
          : upload ? <UploadForm references={references.data?.references || []} onCancel={() => setUpload(false)} />
          : <SampleCatalog catalog={catalog} onChanged={reloadList} onUpload={() => setUpload(true)} />}
        {references.error && <Notice error>연결 대상 목록을 불러오지 못했습니다. {references.error}
          <button onClick={references.reload}>연결 대상 다시 조회</button></Notice>}
        <p className="doc-muted">연결 대상은 공유 시연 관계 목록입니다. 고객사에서 구성한 관계망이나 실제 내규로 확인된 자료가 아닙니다.
          {references.data && <> 현재 조회 백엔드: {references.data.backend}.</>}</p>
      </div>
    </div>
  </div>;
}
