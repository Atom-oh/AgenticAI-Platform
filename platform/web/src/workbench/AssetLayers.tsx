import { array, Details, Empty, object, text } from './shared';
import { resource } from './client';

export type AssetNode = { id: string; type: string; title: string; revision: number; reviewState: string;
  contentHash?: string; tombstone?: boolean; sourceRefs: { sourceKind: string; sourceId: string; sha256: string }[];
  properties?: Record<string, unknown> };
export type AssetEdge = { id: string; type: string; src: { id: string; revision?: number }; dst: { id: string; revision?: number };
  reviewState?: string; tombstone?: boolean; sourceRefs?: unknown };

export default function AssetLayers({ node, nodes, edges, projectId, onSelect }: {
  node: AssetNode; nodes: AssetNode[]; edges: AssetEdge[]; projectId: string; onSelect: (id: string) => void;
}) {
  const ux = object(node.properties?.uxModel), layers = object(ux.layers);
  const wireframe = object(layers.wireframe), gui = object(layers.gui), code = object(layers.code);
  const originals = node.sourceRefs.filter(ref => ref.sourceKind === 'asset' && ref.sha256 === gui.snapshotHash);
  const implementsEdges = edges.filter(edge => !edge.tombstone && edge.type === 'IMPLEMENTS' && edge.dst.id === node.id);
  const uses = edges.filter(edge => !edge.tombstone && (
    edge.type === 'COMPOSES' && edge.dst.id === node.id ||
    edge.type === 'REFERENCES' && edge.src.id === node.id));
  const records = new Map(nodes.map(row => [row.id, row]));
  return <div className="wb-stack" aria-label="자산 레이어와 사용 근거">
    <section><h3>와이어프레임</h3>
      {array(wireframe.blocks).length ? <div className="wb-record-list">{array(wireframe.blocks).map(block =>
        <button key={text(block)} className="wb-record" onClick={() => onSelect(text(block))}>
          {records.get(text(block))?.title || text(block)} · 연결 자산 열기</button>)}</div> :
        <Empty>등록된 와이어프레임 블록이 없습니다.</Empty>}
    </section>
    <section><h3>GUI</h3>
      {Object.keys(gui).length ? <>
        <p>등록된 스냅샷 선언 · {text(gui.width)} × {text(gui.height)}</p>
        <Details title="GUI 스냅샷 해시" value={gui.snapshotHash} />
        {originals.length ? originals.map(ref => <a key={ref.sourceId} className="wb-link"
          href={`#/studio?projectId=${resource(projectId)}&step=assets&assetId=${resource(ref.sourceId)}`}>
          해시가 일치하는 등록 GUI 원본 보기</a>) :
          <p className="wb-muted">해시가 일치하는 등록 원본이 연결되지 않았습니다. 화면 이미지 검증은 미확인입니다.</p>}
      </> : <Empty>등록된 GUI 스냅샷 선언이 없습니다.</Empty>}
    </section>
    <section><h3>코드</h3>
      {Object.keys(code).length ? <>
        <p>{text(code.importPath)} · {text(code.exportName)}</p>
        <Details title="선언된 속성 인터페이스" value={code} />
        <p className="wb-muted">등록된 코드 선언입니다. 컴파일·실행 검수 결과는 개별 실행 증거에서 확인하세요.</p>
      </> : <Empty>등록된 코드 인터페이스가 없습니다.</Empty>}
    </section>
    {!!implementsEdges.length && <section aria-label="화면 구현 연결 검토"><h3>승인할 화면의 코드 매핑</h3>
      <p>화면 승인 시 아래 연결 중 독립적으로 승인된 정확한 코드 버전과의 구현 연결을 함께 승인합니다.</p>
      {implementsEdges.map(edge => {
        const codeNode = records.get(edge.src.id);
        return <article className="wb-artifact" key={edge.id}>
          <button onClick={() => onSelect(edge.src.id)}>{codeNode?.title || edge.src.id} · 코드 근거 열기</button>
          <Details title="구현 관계·코드 버전·승인 근거" value={{ edge, code: codeNode }} />
        </article>;
      })}
    </section>}
    <section><h3>실제 등록된 사용 근거</h3>
      {uses.length ? uses.map(edge => {
        const identifier = edge.src.id === node.id ? edge.dst.id : edge.src.id;
        return <article className="wb-artifact" key={edge.id}>
          <button onClick={() => onSelect(identifier)}>{records.get(identifier)?.title || identifier} · 사용 항목 열기</button>
          <Details title="관계의 정확한 버전·원본 근거" value={edge} />
        </article>;
      }) : <Empty>현재 조회 범위에 등록된 구성·사용 관계가 없습니다. 사용이 없다는 판정은 아닙니다.</Empty>}
      <Details title="사용 화면 선언·패턴 승인 시점 근거" value={{
        usageIds: node.properties?.usageIds, usageBindings: node.properties?.usageBindings }} />
      <p className="wb-muted">승인 여부와 원본 버전은 관계별로 확인하세요. 등록 관계는 실행 중 사용량을 뜻하지 않습니다.</p>
    </section>
  </div>;
}
