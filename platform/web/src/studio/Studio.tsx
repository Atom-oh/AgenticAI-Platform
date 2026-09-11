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

export default function Studio() {
  const [tab, setTab] = useState<'gallery' | 'play' | 'process' | 'assets'>('gallery');
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
  useEffect(() => { load(); }, []);
  const approved = drafts.filter(d => d.status === '승인됨').length;
  const openInPlayground = (d: Draft) => { setEditDraft(d); setTab('play'); };

  return (
    <div>
      <div className="studio-summary panel p-5 mb-4 flex items-center gap-6" style={{ background: 'linear-gradient(105deg, #eaf5f4 0%, #ffffff 55%, #faf7ef 100%)' }}>
        <div>
          <div className="text-lg font-bold text-[#0b4f4b]">디자인 스튜디오</div>
          <div className="text-sm text-slate-500 mt-1">
            상품과 디자인 기준을 선택하면 AI가 화면 초안을 만들고, 체크리스트로 검수하며 수정합니다.
            승인한 시안은 다음 디자인의 참고 자료가 됩니다.
          </div>
          <div className="mt-2 text-xs text-amber-800">현재 산출물은 정적 시안입니다. 입력값 전달·인증·버튼 동작과 픽셀 일치는 아직 검증하지 않습니다.</div>
          <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer">연결 정보와 검수 범위</summary>
          <div className="flex gap-1.5 mt-2 flex-wrap text-[10px]">
            <span className="chip">모델 호출: 익명화 게이트 경유</span>
            <span className="chip">그래프: {meta.graphBackend || '…'}</span>
            <span className="chip">기본 모델: {meta.model || '…'}</span>
            <span className="chip">시안 저장: {meta.backend || '…'} + S3</span>
            <span className="chip">자산 원본: uiux-studio 레지스트리(프록시)</span>
            <span className="chip">검수: 구조·문구·흐름 — 픽셀 비교 미구현</span>
          </div>
          </details>
        </div>
        <div className="flex gap-3 ml-auto">
          {[['자산', assets.length, '#008485'], ['시안', drafts.length, '#AD9A5F'], ['승인', approved, '#0e9f6e']].map(([l, v, c]) => (
            <div key={l as string} className="text-center px-4 py-2 rounded-xl bg-white border border-slate-200">
              <div className="text-xl font-bold" style={{ color: c as string }}>{v as number}</div>
              <div className="text-[11px] text-slate-500">{l}</div>
            </div>
          ))}
        </div>
      </div>
      {loadError && <div role="alert" className="mb-3 text-sm text-rose-700">{loadError} <button className="underline" onClick={load}>다시 조회</button></div>}
      <div className="studio-tabs flex items-center gap-2 mb-4">
        {([['gallery', '🖼 시안 갤러리'], ['play', '✨ 플레이그라운드'], ['process', '🧭 프로세스 생성 (명세서→PRD)'], ['assets', '🎨 디자인 자산']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={`px-4 py-2 rounded-xl text-sm font-semibold border ${tab === id ? 'bg-[#008485] text-white border-[#008485]' : 'bg-white text-slate-600 border-slate-200 hover:border-teal-400'}`}>{label}</button>
        ))}
      </div>
      {tab === 'gallery' && <Gallery drafts={drafts} canWrite={canWrite} reload={load} onEdit={openInPlayground} />}
      {tab === 'play' && <Playground assets={assets} products={products} models={models} defaultModel={defaultModel} canWrite={canWrite} initialDraft={editDraft} onDone={load} />}
      {tab === 'process' && <ProcessStudio models={models} defaultModel={defaultModel} />}
      {tab === 'assets' && <Assets assets={assets} canRegister={!!auth.studioToken} reload={load} />}
    </div>
  );
}
