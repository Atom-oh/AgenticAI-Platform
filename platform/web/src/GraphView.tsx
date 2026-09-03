// 순회 경로 그래프 시각화 (Cytoscape) — 전체 그래프가 아니라 경로만 보여준다 (SPEC §6.2)
import { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';

const TYPE_COLOR: Record<string, string> = {
  Regulation: '#E90061', Condition: '#94a3b8', Product: '#008485',
  Screen: '#0e9f6e', Component: '#7c5cd6', Department: '#AD9A5F', Document: '#d97706',
};

export type GNode = { id: string; label: string; name: string };
export type GEdge = { src: string; rel: string; dst: string };

export default function GraphView({ nodes, edges, onSelect, onOpen }:
  { nodes: GNode[]; edges: GEdge[]; onSelect?: (id: string) => void;
    onOpen?: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || nodes.length === 0) return;
    const seen = new Set<string>();
    const els: cytoscape.ElementDefinition[] = [];
    // 화면이 읽히게 노드 수를 제한한다 — Condition은 접고 핵심 타입만
    const keep = nodes.filter(n => n.label !== 'Condition').slice(0, 90);
    for (const n of keep) {
      seen.add(n.id);
      els.push({ data: { id: n.id, name: n.name.length > 14 ? n.name.slice(0, 13) + '…' : n.name, type: n.label } });
    }
    const dedup = new Set<string>();
    for (const e of edges) {
      if (!seen.has(e.src) || !seen.has(e.dst)) continue;
      const k = `${e.src}|${e.rel}|${e.dst}`;
      if (dedup.has(k)) continue;
      dedup.add(k);
      els.push({ data: { id: k, source: e.src, target: e.dst, rel: e.rel } });
    }
    const cy = cytoscape({
      container: ref.current,
      elements: els,
      style: [
        { selector: 'node', style: {
          'background-color': (el: any) => TYPE_COLOR[el.data('type')] || '#64748b',
          label: 'data(name)', color: '#3b4a47', 'font-size': 9,
          'text-valign': 'bottom', 'text-margin-y': 4, width: 16, height: 16,
        } },
        { selector: 'node[type="Regulation"]', style: { width: 34, height: 34, 'font-size': 11, color: '#a3134f' } },
        { selector: 'edge', style: {
          width: 1, 'line-color': '#c7d6d2', 'curve-style': 'bezier',
          'target-arrow-shape': 'triangle', 'target-arrow-color': '#c7d6d2', 'arrow-scale': 0.6,
        } },
      ],
      layout: { name: 'cose', animate: false, nodeRepulsion: () => 8000, idealEdgeLength: () => 55 } as any,
      wheelSensitivity: 0.2,
    });
    // 노드 클릭: 이웃 강조 + 상위 콜백(탐색기 재중심/이동)
    cy.on('tap', 'node', (ev) => {
      const n = ev.target;
      cy.elements().removeClass('dim hl');
      cy.elements().addClass('dim');
      n.closedNeighborhood().removeClass('dim').addClass('hl');
      onSelect?.(n.id());
    });
    cy.on('tap', (ev) => { if (ev.target === cy) cy.elements().removeClass('dim hl'); });
    cy.on('dbltap', 'node', (ev) => onOpen?.(ev.target.id()));
    cy.style().selector('.dim').style({ opacity: 0.15 } as any)
      .selector('node.hl').style({ 'border-width': 2, 'border-color': '#134e4a' } as any)
      .selector('edge.hl').style({ 'line-color': '#008485', 'target-arrow-color': '#008485', width: 2 } as any)
      .update();
    return () => { cy.destroy(); };
  }, [nodes, edges]);

  if (nodes.length === 0) return null;
  return (
    <div className="panel p-3">
      <div className="flex items-center gap-3 mb-2 text-xs text-slate-400">
        <span className="font-semibold text-slate-800">순회 경로 시각화</span>
        {Object.entries(TYPE_COLOR).filter(([t]) => t !== 'Condition').map(([t, c]) => (
          <span key={t} className="chip" style={{ borderColor: c + '55' }}>
            <span className="w-2 h-2 rounded-full" style={{ background: c }} />{t}
          </span>
        ))}
      </div>
      <div ref={ref} style={{ height: 420 }} />
    </div>
  );
}
