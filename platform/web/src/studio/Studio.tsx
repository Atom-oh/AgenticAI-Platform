// platform/web/src/studio/Studio.tsx
// 디자인 스튜디오 — 플랫폼 네이티브. 시안·잡은 플랫폼(DynamoDB+S3), 자산은 uiux-studio 레지스트리 프록시.
import { useEffect, useState } from 'react';
import { auth, sock } from '../lib';
import Assets from './Assets';
import Gallery from './Gallery';
import Playground from './Playground';
import ProcessStudio from './ProcessStudio';
import { Asset, Draft, Product } from './types';
import { ModelOption } from './ModelSelect';
import Workspace from '../workspace/Workspace';

export default function Studio() {
  const [tab, setTab] = useState<'workspace' | 'gallery' | 'play' | 'process' | 'assets'>('workspace');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [meta, setMeta] = useState<{ backend?: string; graphBackend?: string; model?: string }>({});
  const [editDraft, setEditDraft] = useState<Draft | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [defaultModel, setDefaultModel] = useState('');
  const [loadError, setLoadError] = useState('');
  const canWrite = !!auth.token;
  const load = () => {
    sock.request('studio_drafts').then(e => { setDrafts(e.drafts || []); setMeta(m => ({ ...m, backend: e.backend })); }).catch(() => {});
    sock.request('assets').then(e => setAssets(e.assets || [])).catch(() => {});
    sock.request('studio_products').then(e => { setProducts(e.products || []); setMeta(m => ({ ...m, graphBackend: e.graphBackend, model: e.model })); }).catch(() => {});
    sock.request('studio_models').then(e => {
      if (e.error) throw new Error(e.error);
      setModels(e.models || []); setDefaultModel(e.defaultModel || ''); setLoadError('');
    }).catch(() => setLoadError('AI 모델 목록을 불러오지 못했습니다. 다시 조회해 주세요.'));
  };
  useEffect(() => { if (tab !== 'workspace') load(); }, [tab]);
  const workspaceActive = tab === 'workspace';
  const approved = drafts.filter(d => d.status === '승인됨').length;
  const openInPlayground = (d: Draft) => { setEditDraft(d); setTab('play'); };

  return (
    <div>
      <div className="studio-summary panel p-5 mb-4 flex items-center gap-6" style={{ background: 'linear-gradient(105deg, #eaf5f4 0%, #ffffff 55%, #faf7ef 100%)' }}>
        <div>
          <div className="text-lg font-bold text-[#0b4f4b]">디자인 스튜디오</div>
          <div className="text-sm text-slate-500 mt-1">
            {workspaceActive
              ? '외부 HTML·이미지·가이드 파일을 반입하고, 확인·승인한 규칙으로 원본 HTML을 검사하거나 실행 가능한 시안을 만듭니다. 실제 브라우저 검수 근거를 보며 수정합니다.'
              : '상품과 디자인 기준을 선택하면 AI가 화면 초안을 만들고, 체크리스트로 검수하며 수정합니다. 승인한 시안은 다음 디자인의 참고 자료가 됩니다.'}
          </div>
          <div className="mt-2 text-xs text-amber-800">{workspaceActive
            ? '승인한 규칙의 동작·모의 상태를 브라우저에서 확인합니다. 시작 화면 기준 비교와 사람의 승인은 따로 확인하며, 실제 금융 API·인증·거래 연동은 별도 구현·검증 대상입니다.'
            : '이 탭의 산출물은 정적 시안입니다. 입력값 전달·인증·버튼 동작과 픽셀 일치는 아직 검증하지 않습니다.'}</div>
          {!workspaceActive && <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer">연결 정보와 검수 범위</summary>
          <div className="flex gap-1.5 mt-2 flex-wrap text-[10px]">
            <span className="chip">모델 호출: 익명화 게이트 경유</span>
            <span className="chip">그래프: {meta.graphBackend || '…'}</span>
            <span className="chip">기본 모델: {meta.model || '…'}</span>
            <span className="chip">시안 저장: {meta.backend || '…'} + S3</span>
            <span className="chip">자산 원본: uiux-studio 레지스트리(프록시)</span>
            <span className="chip">검수: 구조·문구·흐름 — 픽셀 비교 미구현</span>
          </div>
          </details>}
        </div>
        {!workspaceActive && <div className="flex gap-3 ml-auto" role="group" aria-label="기존 Studio 집계">
          {[['기존 자산', assets.length, '#008485'], ['기존 시안', drafts.length, '#AD9A5F'], ['기존 승인', approved, '#0e9f6e']].map(([l, v, c]) => (
            <div key={l as string} className="text-center px-4 py-2 rounded-xl bg-white border border-slate-200">
              <div className="text-xl font-bold" style={{ color: c as string }}>{v as number}</div>
              <div className="text-[11px] text-slate-500">{l}</div>
            </div>
          ))}
        </div>}
      </div>
      {tab !== 'workspace' && loadError && <div role="alert" className="mb-3 text-sm text-rose-700">{loadError} <button className="underline" onClick={load}>다시 조회</button></div>}
      <div className="studio-tabs flex items-center gap-2 mb-4">
        {([['workspace', '파일·스킬 작업실'], ['gallery', '🖼 시안 갤러리'], ['play', '✨ 플레이그라운드'], ['process', '🧭 프로세스 생성 (명세서→PRD)'], ['assets', '🎨 디자인 자산']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={`px-4 py-2 rounded-xl text-sm font-semibold border ${tab === id ? 'bg-[#008485] text-white border-[#008485]' : 'bg-white text-slate-600 border-slate-200 hover:border-teal-400'}`}>{label}</button>
        ))}
      </div>
      <div hidden={tab !== 'workspace'}><Workspace /></div>
      {tab === 'gallery' && <Gallery drafts={drafts} canWrite={canWrite} reload={load} onEdit={openInPlayground} />}
      {tab === 'play' && <Playground assets={assets} products={products} models={models} defaultModel={defaultModel} canWrite={canWrite} initialDraft={editDraft} onDone={load} />}
      {tab === 'process' && <ProcessStudio models={models} defaultModel={defaultModel} />}
      {tab === 'assets' && <Assets assets={assets} canRegister={!!auth.studioToken} reload={load} />}
    </div>
  );
}
