// platform/web/src/studio/Studio.tsx
// 디자인 스튜디오 — 플랫폼 네이티브. 시안·잡은 플랫폼(DynamoDB+S3), 자산은 uiux-studio 레지스트리 프록시.
import { useEffect, useState } from 'react';
import { auth, sock } from '../lib';
import Assets from './Assets';
import Gallery from './Gallery';
import Playground from './Playground';
import { Asset, Draft, Product } from './types';

export default function Studio() {
  const [tab, setTab] = useState<'gallery' | 'play' | 'assets'>('gallery');
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [meta, setMeta] = useState<{ backend?: string; graphBackend?: string; model?: string }>({});
  const [editDraft, setEditDraft] = useState<Draft | null>(null);
  const canWrite = true; // 루프 실행은 플랫폼 Cognito 계정으로 — 자산 등록만 uiux-studio 토큰(auth.studioToken)이 필요
  const load = () => {
    sock.request('studio_drafts').then(e => { setDrafts(e.drafts || []); setMeta(m => ({ ...m, backend: e.backend })); }).catch(() => {});
    sock.request('assets').then(e => setAssets(e.assets || [])).catch(() => {});
    sock.request('studio_products').then(e => { setProducts(e.products || []); setMeta(m => ({ ...m, graphBackend: e.graphBackend, model: e.model })); }).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const approved = drafts.filter(d => d.status === '승인됨').length;
  const openInPlayground = (d: Draft) => { setEditDraft(d); setTab('play'); };

  return (
    <div>
      <div className="panel p-5 mb-4 flex items-center gap-6" style={{ background: 'linear-gradient(105deg, #eaf5f4 0%, #ffffff 55%, #faf7ef 100%)' }}>
        <div>
          <div className="text-lg font-bold text-[#0b4f4b]">디자인 스튜디오</div>
          <div className="text-sm text-slate-500 mt-1">
            온톨로지의 <b className="text-[#008485]">상품 명세가 체크리스트</b>가 되어 시안을 생성→검수→수정하는 <b>에이전틱 루프</b>를 돕니다.
            <b className="text-amber-700"> 승인</b>된 시안은 다음 생성의 few-shot 레퍼런스가 됩니다.
          </div>
          <div className="flex gap-1.5 mt-2 flex-wrap text-[10px]">
            <span className="chip">모델 호출: 익명화 게이트 경유</span>
            <span className="chip">그래프: {meta.graphBackend || '…'}</span>
            <span className="chip">생성 모델(설정): {meta.model || '…'}</span>
            <span className="chip">시안 저장: {meta.backend || '…'} + S3</span>
            <span className="chip">자산 원본: uiux-studio 레지스트리(프록시)</span>
            <span className="chip">검수: 구조·문구·흐름 — 픽셀 비교 미구현</span>
          </div>
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
      <div className="flex items-center gap-2 mb-4">
        {([['gallery', '🖼 시안 갤러리'], ['play', '✨ 플레이그라운드'], ['assets', '🎨 디자인 자산']] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={`px-4 py-2 rounded-xl text-sm font-semibold border ${tab === id ? 'bg-[#008485] text-white border-[#008485]' : 'bg-white text-slate-600 border-slate-200 hover:border-teal-400'}`}>{label}</button>
        ))}
      </div>
      {tab === 'gallery' && <Gallery drafts={drafts} canWrite={canWrite} reload={load} onEdit={openInPlayground} />}
      {tab === 'play' && <Playground assets={assets} products={products} canWrite={canWrite} initialDraft={editDraft} onDone={load} />}
      {tab === 'assets' && <Assets assets={assets} canRegister={!!auth.studioToken} reload={load} />}
    </div>
  );
}
