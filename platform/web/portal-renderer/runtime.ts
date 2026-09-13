import mermaid from 'mermaid';
import { DIAGRAM_LIMITS, diagramToMermaid, validateDiagram } from '../src/portal/diagram';
import { CHANNEL, isSession, type RenderSession, type ViewCommand } from './protocol';

const viewport = document.getElementById('viewport') as HTMLDivElement;
const diagram = document.getElementById('diagram') as HTMLDivElement;
const scratch = document.getElementById('scratch') as HTMLDivElement;
const status = document.getElementById('status') as HTMLParagraphElement;
let session: (RenderSession & { origin: string }) | null = null;
let svg: SVGSVGElement | null = null;
let naturalWidth = 0, naturalHeight = 0, scale = 1;
let viewMode: 'readable' | 'fit' | 'manual' = 'readable';
const READABLE_SCALE = 0.85;

mermaid.initialize({
  startOnLoad: false, securityLevel: 'strict', htmlLabels: false,
  maxTextSize: DIAGRAM_LIMITS.dsl, maxEdges: DIAGRAM_LIMITS.edges,
  suppressErrorRendering: true, logLevel: 5,
  theme: 'base', fontFamily: 'system-ui, sans-serif',
  // Fixed ontology palette; asset metadata cannot supply theme or CSS options.
  themeVariables: {
    fontSize: '16px', primaryColor: '#fffbeb', primaryBorderColor: '#a16207', primaryTextColor: '#422006',
    lineColor: '#92400e', secondaryColor: '#fef3c7', tertiaryColor: '#fff7ed', edgeLabelBackground: '#fff7ed',
  },
  flowchart: { useMaxWidth: false, curve: 'linear', rankSpacing: 24 },
});

function send(type: 'rendered' | 'error') {
  if (session) window.parent.postMessage({ channel: CHANNEL, type, nonce: session.nonce, id: session.id },
    session.origin === 'null' ? '*' : session.origin);
}

// No links, images, HTML, scripts, animation or navigation survive the SVG mount.
// This is defense in depth; Mermaid renders only generated, fully escaped DSL.
function staticSvg(value: string): SVGSVGElement {
  const parsed = new DOMParser().parseFromString(value, 'image/svg+xml');
  const element = parsed.documentElement;
  if (element.localName !== 'svg' || element.namespaceURI !== 'http://www.w3.org/2000/svg' ||
      parsed.querySelector('parsererror')) throw new Error('invalid-svg');
  const allowed = new Set(['svg', 'g', 'defs', 'marker', 'path', 'rect', 'circle', 'ellipse', 'line',
    'polygon', 'polyline', 'text', 'tspan', 'title', 'desc', 'style']);
  for (const child of Array.from(element.querySelectorAll('*'))) {
    if (!allowed.has(child.localName) || child.namespaceURI !== element.namespaceURI) child.remove();
  }
  for (const child of [element, ...Array.from(element.querySelectorAll('*'))]) {
    for (const attribute of Array.from(child.attributes)) {
      if (/^on/i.test(attribute.name) || attribute.localName === 'href' || attribute.name === 'tabindex' ||
          (attribute.namespaceURI !== null && attribute.namespaceURI !== 'http://www.w3.org/2000/xmlns/')) {
        child.removeAttributeNode(attribute);
      }
    }
  }
  return document.importNode(element, true) as unknown as SVGSVGElement;
}

function resize() {
  if (!svg) return;
  if (viewMode !== 'manual') {
    const overview = Math.min((viewport.clientWidth - 32) / naturalWidth,
      (viewport.clientHeight - 32) / naturalHeight, 1);
    // Read first, scroll for the rest. Only an explicit Fit action requests a
    // potentially tiny overview; automatic resize must keep default text legible.
    scale = Math.max(viewMode === 'readable' ? READABLE_SCALE : 0.05, overview);
  }
  svg.style.width = `${naturalWidth * scale}px`;
  svg.style.height = `${naturalHeight * scale}px`;
  svg.style.maxWidth = 'none';
}
function control(command: ViewCommand) {
  if (!svg) return;
  if (command === 'fit') { viewMode = 'fit'; resize(); viewport.scrollTo(0, 0); return; }
  viewMode = 'manual';
  scale = Math.min(4, Math.max(0.05, scale * (command === 'zoom-in' ? 1.25 : 0.8)));
  resize();
}
new ResizeObserver(() => resize()).observe(viewport);

window.addEventListener('message', async event => {
  // Source authentication precedes examining any supplied graph or command.
  if (window.parent === window || event.source !== window.parent || !isSession(event.data)) return;
  const data = event.data as RenderSession & { channel?: unknown; type?: unknown; visual?: unknown; command?: unknown };
  if (data.channel !== CHANNEL) return;
  if (session) {
    if (data.nonce !== session.nonce || data.id !== session.id || event.origin !== session.origin) return;
    if (data.type === 'view' && (data.command === 'zoom-in' || data.command === 'zoom-out' || data.command === 'fit')) {
      control(data.command);
    }
    return; // One graph per frame. Never queue or replace renders in a bound frame.
  }
  if (data.type !== 'render') return;
  session = { nonce: data.nonce, id: data.id, origin: event.origin };
  try {
    const visual = validateDiagram(data.visual);
    const dsl = diagramToMermaid(visual);
    const result = await mermaid.render('portal-diagram-svg', dsl, scratch);
    svg = staticSvg(result.svg);
    const viewBox = svg.viewBox.baseVal;
    naturalWidth = viewBox.width; naturalHeight = viewBox.height;
    if (!Number.isFinite(naturalWidth) || !Number.isFinite(naturalHeight) || naturalWidth <= 0 || naturalHeight <= 0) {
      throw new Error('invalid-svg-dimensions');
    }
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', visual.title || '관계 다이어그램');
    // The parent never receives or mounts SVG. Only this opaque frame owns it.
    diagram.replaceChildren(svg);
    scratch.replaceChildren();
    status.hidden = true;
    viewport.hidden = false;
    resize();
    send('rendered');
  } catch {
    svg = null;
    diagram.replaceChildren(); scratch.replaceChildren();
    viewport.hidden = true;
    status.hidden = false;
    status.setAttribute('role', 'alert');
    status.textContent = '다이어그램을 표시할 수 없습니다.';
    send('error');
  }
});
