import { useState } from 'react';
import { Notice, useDocumentQuery, useDocumentScope, usePrivateTask } from './DocumentScope';
import { newRequest } from './client';
import { kindLabel } from './presentation';
import type { DocumentRecord, DocumentTemplate, Revision, TemplateCatalog } from './types';

function readCatalog(value: unknown): TemplateCatalog | undefined {
  if (!value || typeof value !== 'object') return;
  const data = value as TemplateCatalog;
  const text = (item: unknown, limit: number) => typeof item === 'string' && item.trim().length > 0 && item.length <= limit;
  if (data.schemaVersion !== 1 || !text(data.notice, 2000) || !Array.isArray(data.samples) ||
      !data.samples.length || data.samples.length > 50) return;
  const ids = new Set<string>();
  for (const row of data.samples) {
    if (!row || !text(row.graphRef, 128) || !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(row.graphRef) ||
        ids.has(row.graphRef) || !text(row.title, 240) || !text(row.kind, 80) || !text(row.name, 240) ||
        !text(row.versionLabel, 120) || !/^[a-f0-9]{64}$/.test(row.sha256) ||
        !Array.isArray(row.sections) || !row.sections.length || row.sections.length > 12 ||
        !row.sections.every(section => section && text(section.title, 120) && text(section.text, 4000))) return;
    ids.add(row.graphRef);
  }
  return data;
}

export function useSampleCatalog() {
  const query = useDocumentQuery<unknown>('/documents/samples');
  const data = readCatalog(query.data);
  return { ...query, data, error: query.error || (query.data && !data ? '합성 예제 구성을 확인하지 못했습니다.' : '') };
}

export function matchingTemplate(catalog: TemplateCatalog | undefined, document: DocumentRecord | undefined, revision: Revision | undefined) {
  if (!catalog || !document || !revision || document.provenance !== 'synthetic_sample') return;
  return catalog.samples.find(row => row.graphRef === document.graphRef && row.kind === document.kind &&
    row.name === revision.name && row.sha256 === revision.sha256);
}

export function TemplateOverview({ template }: { template: DocumentTemplate }) {
  return <section className="doc-template-overview doc-stack" aria-label="이 문서 안내">
    <h4>이 문서는 무엇인가요?</h4>
    <p>{template.sections[0].text}</p>
    <div className="doc-row"><span className="doc-muted">포함 내용</span>
      {template.sections.map(section => <span className="doc-chip" key={section.title}>{section.title}</span>)}</div>
  </section>;
}

export default function SampleCatalog({ catalog, onChanged, onUpload }: {
  catalog: ReturnType<typeof useSampleCatalog>; onChanged: () => void; onUpload: () => void;
}) {
  const { client, config } = useDocumentScope();
  const task = usePrivateTask();
  const [requestId] = useState(newRequest);
  const [message, setMessage] = useState('');
  const data = catalog.data;
  return <section className="doc-stack" aria-label="합성 예제 구성">
    <div className="doc-panel doc-stack">
      <div className="doc-row doc-between"><div>
        <p className="doc-eyebrow">처음이라면 예제부터 살펴보세요</p>
        <h3>{data ? `합성 문서 ${data.samples.length}종 살펴보기` : '합성 문서 살펴보기'}</h3>
      </div><button onClick={onUpload}>내 문서 직접 등록</button></div>
      <p>담보 기준이 바뀌었을 때 함께 검토하는 문서들입니다. 제목과 설명을 읽고 각 문서에 어떤 내용이 들어 있는지 확인하세요.</p>
      <p className="doc-muted">아래는 등록 전 예제 구성입니다. 이미 등록한 문서의 원문과 검토 상태는 왼쪽 문서 목록에서 확인할 수 있습니다.</p>
      {catalog.busy && !data && <p role="status">예제 내용을 불러오고 있습니다…</p>}
      {catalog.error && <Notice error>{catalog.error} <button onClick={catalog.reload}>예제 다시 조회</button></Notice>}
      {data && <>
        <Notice>{data.notice}</Notice>
        {config.role === 'owner'
          ? <div className="doc-row"><button className="doc-primary" disabled={task.busy} onClick={() => void task.run(async (signal, current) => {
            await client.post('/documents/samples', { requestId }, signal);
            if (current()) {
              setMessage('예제를 문서함에 등록했습니다. 목록에서 원문을 열고 추출 결과를 확인한 뒤 필요한 문서만 검토·승인하세요.');
              onChanged();
            }
          })}>{task.busy ? '예제 등록 중…' : `합성 예제 ${data.samples.length}종 새로 등록`}</button>
            <span className="doc-muted">미리보기만으로 등록되거나 승인되지 않습니다.</span></div>
          : <p className="doc-muted">예제를 문서함에 새로 등록하려면 소유자에게 요청하세요.</p>}
      </>}
      {task.error && <Notice error>{task.error}</Notice>}
      {message && <Notice>{message}</Notice>}
    </div>
    {data && <div className="doc-template-grid">
      {data.samples.map((sample, index) => <article key={sample.graphRef} className="doc-panel doc-stack doc-template-card">
        <div className="doc-row"><span className="doc-template-number">{index + 1}</span><span className="doc-chip">{kindLabel(sample.kind)}</span></div>
        <h4>{sample.title}</h4>
        <p>{sample.sections[0].text}</p>
        <details className="doc-details"><summary>포함 내용 보기</summary><div>
          {sample.sections.map(section => <section className="doc-stack" key={section.title}>
            <h4>{section.title}</h4><p>{section.text}</p>
          </section>)}
          <p className="doc-muted">원본 파일: {sample.name} · {sample.versionLabel}</p>
        </div></details>
      </article>)}
    </div>}
  </section>;
}
