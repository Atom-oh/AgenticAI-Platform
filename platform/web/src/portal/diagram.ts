export type PortalDiagram = {
  kind: 'diagram';
  source: string;
  title: string;
  nodes: { id: string; label: string; type: string; assetId?: string; missing?: boolean }[];
  edges: { from: string; to: string; label: string; kind: 'sequence' | 'reference' }[];
  note: string;
  truncated: { nodes: number; edges: number };
};

export const DIAGRAM_LIMITS = Object.freeze({
  nodes: 80, edges: 160, label: 240, id: 500, type: 100, title: 300, source: 1000, note: 4000, dsl: 100_000,
});

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function text(value: unknown, maximum: number, required = false): value is string {
  return typeof value === 'string' && value.length <= maximum && (!required || value.trim().length > 0);
}
function count(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}
function invalid(): never { throw new Error('invalid-diagram'); }

/** Copy only graph data. Never evaluate, import, fetch or resolve asset metadata. */
export function validateDiagram(value: unknown): PortalDiagram {
  if (!record(value) || value.kind !== 'diagram' ||
      !text(value.source, DIAGRAM_LIMITS.source) || !text(value.title, DIAGRAM_LIMITS.title) ||
      !text(value.note, DIAGRAM_LIMITS.note) || !record(value.truncated) ||
      !count(value.truncated.nodes) || !count(value.truncated.edges) ||
      !Array.isArray(value.nodes) || value.nodes.length === 0 || value.nodes.length > DIAGRAM_LIMITS.nodes ||
      !Array.isArray(value.edges) || value.edges.length > DIAGRAM_LIMITS.edges) invalid();
  const ids = new Set<string>();
  const nodes: PortalDiagram['nodes'] = value.nodes.map(node => {
    if (!record(node) || !text(node.id, DIAGRAM_LIMITS.id, true) || ids.has(node.id) ||
        !text(node.label, DIAGRAM_LIMITS.label) || !text(node.type, DIAGRAM_LIMITS.type) ||
        (node.assetId !== undefined && !text(node.assetId, DIAGRAM_LIMITS.id)) ||
        (node.missing !== undefined && typeof node.missing !== 'boolean')) invalid();
    ids.add(node.id);
    return { id: node.id, label: node.label, type: node.type,
      ...(node.assetId !== undefined ? { assetId: node.assetId } : {}),
      ...(node.missing !== undefined ? { missing: node.missing } : {}) };
  });
  const edges: PortalDiagram['edges'] = value.edges.map(edge => {
    if (!record(edge) || typeof edge.from !== 'string' || !ids.has(edge.from) ||
        typeof edge.to !== 'string' || !ids.has(edge.to) || !text(edge.label, DIAGRAM_LIMITS.label) ||
        (edge.kind !== 'sequence' && edge.kind !== 'reference')) invalid();
    return { from: edge.from, to: edge.to, label: edge.label, kind: edge.kind };
  });
  return { kind: 'diagram', source: value.source, title: value.title, nodes, edges, note: value.note,
    truncated: { nodes: value.truncated.nodes, edges: value.truncated.edges } };
}

// Mermaid's numeric entity syntax is #34;, not HTML's &#34;. Encode every
// code point, including '#' and '&', so entity-looking input stays literal.
// Newlines/control characters become spaces; labels cannot open DSL statements.
export function escapeDiagramLabel(label: string): string {
  return Array.from(label.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, ' '),
    character => `#${character.codePointAt(0)};`).join('');
}

/** Only this generated flowchart grammar is supported; raw Mermaid is not input. */
export function diagramToMermaid(input: PortalDiagram): string {
  const visual = validateDiagram(input);
  const ids = new Map(visual.nodes.map((node, index) => [node.id, `n${index}`]));
  // Presentation direction only: the supplied node order and edges stay intact.
  const lines = [visual.source === 'procedure-steps' ? 'flowchart TB' : 'flowchart LR'];
  for (const node of visual.nodes) {
    const label = escapeDiagramLabel(node.label || ' ');
    lines.push(`  ${ids.get(node.id)}["${label}"]${node.missing ? ':::missing' : ''}`);
  }
  for (const edge of visual.edges) {
    const arrow = edge.kind === 'reference' ? '-.->' : '-->';
    const label = edge.label ? `|"${escapeDiagramLabel(edge.label)}"|` : '';
    lines.push(`  ${ids.get(edge.from)} ${arrow}${label} ${ids.get(edge.to)}`);
  }
  if (visual.nodes.some(node => node.missing)) {
    lines.push('  classDef missing fill:#fff7ed,stroke:#b45309,stroke-dasharray:5 5');
  }
  const dsl = lines.join('\n');
  if (dsl.length > DIAGRAM_LIMITS.dsl) throw new Error('diagram-too-large');
  return dsl;
}
