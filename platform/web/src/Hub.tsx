// 종합 플랫폼 허브 — 하나의 Agentic AI Platform 아래 전 서피스를 묶는다.
import { useEffect, useState } from 'react';
import { auth } from './lib';

type Surface = {
  name: string; role: string; href: string; external?: boolean;
  desc: string; accent: string; tag: string;
};

const SURFACES: Surface[] = [
  {
    name: '규정 영향 분석', role: '심사·기획 담당자', href: '#/s1', accent: 'var(--cloud)',
    tag: 'GraphRAG · S1', desc: 'Vector RAG와 GraphRAG를 나란히 실행해 규정 개정의 영향 범위(상품·화면·부서·문서)를 그래프 순회로 증명합니다.',
  },
  {
    name: 'UI/UX 스튜디오', role: '디자이너', href: 'https://d4zwmnh2s47e9.cloudfront.net/', external: true, accent: '#a78bfa',
    tag: '디자인 자산 · 생성', desc: '디자인 자산 8종 등록·버전 관리, 자산 선택형 생성 플레이그라운드, AgentCore Memory 취향 학습. 산출물은 개발자·기획자와 공유됩니다.',
  },
  {
    name: '에이전트 컨트롤룸', role: '플랫폼 엔지니어 · 현업', href: 'https://d1twhttjtzqewp.cloudfront.net/', external: true, accent: 'var(--onprem)',
    tag: '카탈로그 · 거버넌스', desc: '팀 셀프서비스 에이전트 생성, evals-as-gate 승인, 예산 서킷브레이커, 중앙 MCP(Gateway), 개인 HR 스코핑.',
  },
  {
    name: '가이드북', role: '전 직군', href: 'https://www.atomai.click/AgenticAI-Platform/', external: true, accent: 'var(--ok)',
    tag: '레퍼런스 북', desc: 'Agentic AI 플랫폼 엔지니어링 — 93개 챕터. 이 플랫폼의 모든 통제가 책의 어느 장에 근거하는지 연결됩니다.',
  },
];

const CONTROL_PLANES = [
  { name: 'AgentCore Agent Registry', d: '전 서피스 자산의 승인 수명주기 정본 — CloudTrail 감사' },
  { name: 'Cognito (초대 전용)', d: '가입 없음 · admin-create only — 전 서피스 공통 원칙' },
  { name: 'AgentCore Gateway (중앙 MCP)', d: '플랫폼 툴·디자인 자산 툴을 JWT 인바운드 MCP로 노출' },
  { name: 'CloudFront 단일 진입', d: '모든 서피스는 CloudFront만 공개, 컴퓨트 직접 노출 없음' },
];

export default function Hub({ studioAssets }: { studioAssets: number | null }) {
  return (
    <div className="max-w-[1100px] mx-auto p-6">
      <div className="mb-8 mt-6">
        <div className="text-3xl font-bold tracking-tight">
          Agentic AI <span className="text-sky-400">Platform</span>
        </div>
        <div className="text-slate-400 mt-2 text-sm">
          하나의 플랫폼, 역할별 서피스 — 심사·기획, 디자이너, 플랫폼 엔지니어, 전 직군이 같은
          거버넌스(Registry·Cognito·중앙 MCP) 위에서 일합니다. <span className="text-slate-500">{auth.email}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 mb-8">
        {SURFACES.map(s => (
          <a key={s.name} href={s.href} target={s.external ? '_blank' : undefined}
            className="panel p-5 hover:border-slate-500 transition-colors block"
            style={{ borderLeft: `3px solid ${s.accent}` }}>
            <div className="flex items-center gap-2">
              <b className="text-lg">{s.name}</b>
              <span className="chip text-xs" style={{ borderColor: s.accent + '66' }}>{s.tag}</span>
              {s.external && <span className="text-slate-500 text-xs">↗</span>}
              {s.name === 'UI/UX 스튜디오' && studioAssets !== null && (
                <span className="chip text-xs text-violet-300">자산 {studioAssets}건</span>
              )}
            </div>
            <div className="text-xs text-slate-500 mt-0.5 mb-2">{s.role}</div>
            <div className="text-sm text-slate-300 leading-relaxed">{s.desc}</div>
          </a>
        ))}
      </div>

      <div className="panel p-5">
        <div className="text-sm font-semibold mb-3 text-slate-200">공통 통제면 — 서피스가 달라도 거버넌스는 하나</div>
        <div className="grid grid-cols-2 gap-3">
          {CONTROL_PLANES.map(c => (
            <div key={c.name} className="text-sm">
              <span className="text-sky-300">{c.name}</span>
              <div className="text-xs text-slate-400">{c.d}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
