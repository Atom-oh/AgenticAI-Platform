import { useState } from 'react';
import ProductPlanner from '../workspace/ProductPlanner';
import ReactSourcePanel from '../portal/ReactSourcePanel';
import { buildDetails, releaseChecksPassed } from '../workspace/project';
import type { Asset, ComponentCatalog, Product, Release, Run } from '../workspace/types';
import { listPages, resource } from './client';
import { Details, Empty, Field, LoadState, Section, Status, useLoad, useWorkbench } from './shared';
import '../workspace/workspace.css';

export function Planning() {
  const { client, params, navigate } = useWorkbench();
  const state = useLoad(signal => listPages<Product>(client, '/products', 'products', signal), [client]);
  const [chosen, setChosen] = useState(params.get('productId') || '');
  const [saved, setSaved] = useState<Product | undefined>();
  const product = saved?.id === chosen ? saved : state.data?.find(item => item.id === chosen);
  return <div className="wb-two-column">
    <Section title="상품 목록" description="상품별 게시 기준을 선택하세요." action={<button onClick={() => { setChosen(''); setSaved(undefined); navigate('planning', { productId: undefined }); }}>새 상품</button>}>
      <LoadState state={state}>{!state.data?.length ? <Empty>첫 상품기획서를 작성하세요.</Empty> :
        <div className="wb-record-list">{state.data.map(item => <button key={item.id} className={`wb-record${chosen === item.id ? ' is-selected' : ''}`}
          aria-pressed={chosen === item.id} onClick={() => { setChosen(item.id); setSaved(undefined); navigate('planning', { productId: item.id }); }}>
          <strong>{item.title}</strong><span>{item.publishedGuidelineId ? `게시 ${item.publishedRevision}차` : '아직 게시하지 않음'} · 편집 v{item.version}</span>
        </button>)}</div>}
      </LoadState>
    </Section>
    <Section title={product ? product.title : '새 상품기획서'} description="저장과 게시를 구분해 팀의 업무 기준을 관리합니다.">
      <LoadState state={state}>
        {chosen && !product ? <Empty>연결된 상품을 찾지 못했습니다. 현재 프로젝트의 목록에서 다시 선택하세요.</Empty> :
          <div className="designer-workspace wb-planner"><ProductPlanner key={chosen || 'new'} product={product}
            onSaved={value => { setSaved(value); setChosen(value.id); navigate('planning', { productId: value.id }); }} refresh={state.refresh} /></div>}
        {product && <div className="wb-actions">
          <button onClick={() => navigate('changes', { productId: product.id, targetId: undefined, target: undefined,
            changeId: undefined, change: undefined, impactHash: undefined })}>이 상품의 변경 요청</button>
          <button onClick={() => navigate('studio', { productId: product.id })}>디자인 스튜디오로 이어가기</button>
        </div>}
      </LoadState>
    </Section>
  </div>;
}

export function Deliverables() {
  const { client, navigate, params } = useWorkbench();
  const [kind, setKind] = useState('all');
  const state = useLoad(async signal => {
    const [runs, assets, releases] = await Promise.all([
      listPages<Run>(client, '/runs', 'runs', signal), listPages<Asset>(client, '/assets', 'assets', signal),
      listPages<Release>(client, '/releases', 'releases', signal),
    ]);
    return { runs, assets, releases };
  }, [client]);
  const product = params.get('productId');
  const runs = (state.data?.runs || []).filter(run => !product || run.productId === product);
  const assets = (state.data?.assets || []).filter(asset => !product || asset.productId === product);
  return <Section title="저장된 산출물" description="생성·검증·승인·릴리스 상태는 각각 별도 기록입니다."
    action={<button className="wb-primary" onClick={() => navigate('studio')}>디자인 스튜디오 열기</button>}>
    <div className="wb-toolbar"><Field label="산출물 유형"><select value={kind} onChange={event => setKind(event.target.value)}>
      <option value="all">전체 산출물</option><option value="react">React 화면</option><option value="html">HTML 프로토타입</option>
      <option value="guide">업무 지침·가이드</option><option value="assets">업로드 자료</option>
    </select></Field><button onClick={state.refresh}>새로 조회</button></div>
    <LoadState state={state}>
      {(kind === 'all' || kind === 'react' || kind === 'html') && runs.filter(run => kind === 'all' || run.outputType === kind).map(run => {
        const round = run.rounds.find(item => item.number === run.bestRound);
        const releases = state.data?.releases.filter(item => item.runId === run.id) || [];
        return <article className="wb-artifact" key={run.id}><div className="wb-row">
          <div><span className="wb-eyebrow">{run.outputType === 'react' ? 'React 화면' : 'HTML 프로토타입'}</span>
            <h3>{run.contract?.title || run.id}</h3></div><Status value={run.status} /></div>
          <p>{run.contract?.brief || '저장된 실행 결과'} · {run.rounds.length}개 라운드</p>
          <dl className="wb-facts"><div><dt>기능 검증</dt><dd><Status value={round?.functionalStatus} /></dd></div>
            <div><dt>화면 검증</dt><dd><Status value={round?.visualStatus} /></dd></div>
            <div><dt>사용자 승인</dt><dd>{run.approval ? `${run.approval.round}라운드 승인` : '미승인'}</dd></div>
            <div><dt>현재 지침</dt><dd>{run.needsRevalidation ? '변경됨 · 재검증 필요' : run.guidelineId || '연결 기록 없음'}</dd></div></dl>
          {releases.map(release => <p key={release.id}>개발 전달 · <Status value={release.status} /> · {releaseChecksPassed(release) ? '릴리스 검증 근거 확인' : '릴리스 검증 근거 미확인'}</p>)}
          <Details title="소스·검증 근거" value={{ runId: run.id, sourceHash: round?.sourceHash, build: buildDetails(round), approval: run.approval, releases }} />
          <button onClick={() => navigate('studio', { runId: run.id, ...(run.productId ? { productId: run.productId } : {}) })}>스튜디오에서 열기</button>
        </article>;
      })}
      {(kind === 'all' || kind === 'guide' || kind === 'assets') && assets.filter(asset => kind !== 'guide' || asset.purpose === 'guide').map(asset =>
        <article className="wb-artifact" key={asset.id}><div className="wb-row"><div><h3>{asset.name}</h3>
          <p>{asset.purpose === 'guide' ? '업무 지침' : asset.purpose} · 원본 {asset.importRevision || 1}차 · {asset.size.toLocaleString('ko-KR')} 바이트</p></div>
          <Status value={asset.parseStatus} /></div><Details value={{ id: asset.id, sha256: asset.sha256, guidelineId: asset.guidelineId, warnings: asset.warnings }} />
          <button onClick={() => navigate('studio', { assetId: asset.id })}>스튜디오에서 자료 확인</button></article>)}
      {!runs.length && !assets.length && <Empty>이 프로젝트에 저장된 산출물이 없습니다. 스튜디오에서 자료를 추가하거나 화면을 생성하세요.</Empty>}
    </LoadState>
  </Section>;
}

export function Components() {
  const { client, navigate } = useWorkbench();
  const [query, setQuery] = useState('');
  const state = useLoad(signal => client.get<{ catalog: ComponentCatalog }>('/components', signal), [client]);
  return <Section title="플랫폼 기본 React 패키지" description="실제 타입과 고정 버전의 플랫폼 패키지입니다. 고객 사내 컴포넌트 승인 여부는 별도 확인이 필요합니다.">
    <LoadState state={state}>{state.data && <>
      <ReactSourcePanel hash={state.data.catalog.hash} version={state.data.catalog.version} />
      <div className="wb-toolbar"><div><strong>{state.data.catalog.label}</strong><p>버전 {state.data.catalog.version} · {state.data.catalog.components.length}개 컴포넌트</p></div>
        <Field label="컴포넌트 검색"><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="이름 또는 설명" /></Field></div>
      <div className="wb-component-grid">{state.data.catalog.components.filter(component => `${component.name} ${component.description}`.toLowerCase().includes(query.toLowerCase())).map(component =>
        <article className="wb-component" key={component.name}><span className="wb-code-symbol" aria-hidden="true">&lt;/&gt;</span>
          <h3>{component.name}</h3><p>{component.description}</p>
          <dl className="wb-props">{Object.entries(component.props).map(([name, value]) => <div key={name}><dt><code>{name}</code></dt><dd>{value}</dd></div>)}</dl>
          <p className="wb-muted">허용 변화: {component.variationAxes.join(' · ') || '별도 정의 없음'}</p>
          <button onClick={() => navigate('portal', { component: component.name, tab: undefined, id: undefined, tool: undefined })}>소스·실행 예제</button>
          <button onClick={() => navigate('knowledge', { targetId: component.name })}>사용 관계·근거</button>
        </article>)}</div>
      <Details title="패키지 기준 해시" value={{ id: state.data.catalog.id, hash: state.data.catalog.hash }} />
    </>}</LoadState>
  </Section>;
}
