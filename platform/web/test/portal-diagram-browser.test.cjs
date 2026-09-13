const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { chromium } = require('playwright');
const { buildRenderer } = require('../portal-renderer/build.cjs');
const root = path.join(__dirname, '..');
let assets;
const prepared = () => assets ||= Promise.all([
  buildRenderer({ write: false }),
  build({
    stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';import DiagramPreview from './src/portal/DiagramPreview';
const root=createRoot(document.getElementById('root'));
window.showDiagram=(visual,options={})=>flushSync(()=>root.render(<DiagramPreview key={options.instance} visual={visual} large={options.large}/>));
window.receipts=[];window.addEventListener('message',e=>{if(e.data?.channel==='portal-diagram/v1')window.receipts.push(e.data);});`,
    resolveDir: root, loader: 'tsx' },
    bundle: true, write: false, format: 'iife', define: { 'process.env.NODE_ENV': '"production"' },
  }),
]);
function graph(label = '시작') {
  return { kind: 'diagram', source: 'Registry synthetic fixture', title: '관계 미리보기', note: '도식 데이터',
    nodes: [{ id: 'first', label, type: 'screen' }, { id: 'second', label: '확인', type: 'screen', missing: true }],
    edges: [{ from: 'first', to: 'second', label: '다음 & "확인"', kind: 'sequence' }],
    truncated: { nodes: 1, edges: 2 } };
}
async function mount(t, respond, expectCspErrors = false) {
  const [renderer, app] = await prepared();
  const browser = await chromium.launch({ headless: true, executablePath: process.env.WORKSPACE_CHROMIUM });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const external = [], errors = [], requests = [], consoleErrors = [];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://offline.test') {
      external.push(url.href); await route.abort(); return;
    }
    requests.push(url.pathname);
    if (respond && await respond(route, url, renderer)) return;
    if (url.pathname === '/') return route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><html lang="ko"><head><title>Portal diagram QA</title></head><body style="margin:16px;font-family:system-ui"><main id="root"></main><script src="/app.js"></script></body></html>',
    });
    if (url.pathname === '/app.js') return route.fulfill({ contentType: 'text/javascript', body: app.outputFiles[0].text });
    if (url.pathname === '/portal-renderers/mermaid/index.json') return route.fulfill({
      contentType: 'application/json', body: JSON.stringify(renderer.manifest),
    });
    if (url.pathname === `/portal-renderers/mermaid/${renderer.manifest.file}`) return route.fulfill({
      contentType: 'text/html', body: renderer.html,
    });
    throw new Error(`Unexpected local request ${url.pathname}`);
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10_000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' || message.type() === 'warning') consoleErrors.push(message.text());
  });
  await page.goto('https://offline.test/');
  t.after(() => {
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    if (!expectCspErrors) assert.deepEqual(consoleErrors, []);
  });
  return { page, renderer, requests };
}

test('standalone local renderer has exact content hash and a single hashed script', { timeout: 60_000 }, async () => {
  const [{ html, manifest }] = await prepared();
  const hash = createHash('sha256').update(html).digest('hex');
  assert.equal(manifest.sha256, hash);
  assert.equal(manifest.bytes, Buffer.byteLength(html));
  assert.equal(manifest.file, `mermaid-${manifest.mermaidVersion}-${hash}.html`);
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 1);
  const scriptHash = createHash('sha256').update(scripts[0][1]).digest('base64');
  assert.ok(html.includes(`script-src 'sha256-${scriptHash}'`));
  for (const directive of ['default-src', 'connect-src', 'frame-src', 'object-src', 'form-action', 'base-uri', 'font-src']) {
    assert.ok(html.includes(`${directive} 'none'`), directive);
  }
  assert.ok(html.includes('img-src data:'));
  assert.doesNotMatch(html, /<script[^>]+src=|<link\b|unsafe-eval|script-src 'unsafe-inline'/i);
});

test('Mermaid renders literal malicious labels, isolated SVG, source and working view controls', { timeout: 60_000 }, async t => {
  const { page } = await mount(t);
  const label = '"<&>#38; &amp; ﬂ°°60¶ß 한글 😀 [x](https://outside.invalid) <img src="https://outside.invalid/p" onerror="alert(1)">';
  await page.evaluate(visual => window.showDiagram(visual), graph(label));
  const frame = page.frameLocator('iframe[data-portal-diagram]');
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
  assert.equal(await page.locator('.portal-diagram').getAttribute('data-diagram-state'), 'rendered');
  await frame.locator('svg').waitFor();
  assert.equal(await page.title(), 'Portal diagram QA');
  assert.equal(page.url(), 'https://offline.test/');
  assert.equal(await frame.locator('.node .label text').first().textContent(), label);
  assert.equal(await page.locator('svg').count(), 0);
  assert.equal(await frame.locator('a,image,foreignObject,script[src],link').count(), 0);
  assert.equal(await page.locator('iframe').getAttribute('sandbox'), 'allow-scripts');
  assert.equal(await page.locator('iframe').getAttribute('referrerpolicy'), 'no-referrer');
  assert.equal(await page.evaluate(() => {
    try { void document.querySelector('iframe').contentWindow.document; return false; } catch { return true; }
  }), true);
  const width = await frame.locator('svg').evaluate(svg => svg.getBoundingClientRect().width);
  await page.getByRole('button', { name: '다이어그램 확대', exact: true }).click();
  await page.waitForFunction(() => window.receipts.some(message => message.type === 'rendered'));
  await frame.locator('svg').evaluate(svg => new Promise(resolve => requestAnimationFrame(() => resolve(svg))));
  assert.ok(await frame.locator('svg').evaluate(svg => svg.getBoundingClientRect().width) > width);
  await page.getByRole('button', { name: '화면에 맞춤', exact: true }).click();
  await frame.locator('svg').evaluate(svg => new Promise(resolve => requestAnimationFrame(() => resolve(svg))));
  assert.ok(Math.abs(await frame.locator('svg').evaluate(svg => svg.getBoundingClientRect().width) - width) < 2);
  await page.getByRole('button', { name: '크게 보기', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '접기', exact: true }).getAttribute('aria-expanded'), 'true');
  await page.getByText('출처와 다이어그램 소스', { exact: true }).click();
  await page.getByText('Registry synthetic fixture', { exact: true }).waitFor();
  assert.match(await page.locator('pre').textContent(), /^flowchart LR/);
  await page.getByText('노드 1개 · 연결 2개 생략', { exact: true }).waitFor();
  for (const viewport of [{ width: 1200, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.evaluate(() => window.scrollTo(0, 0));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    if (process.env.PORTAL_DIAGRAM_SCREENSHOT_DIR) {
      const fs = require('node:fs');
      fs.mkdirSync(process.env.PORTAL_DIAGRAM_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.PORTAL_DIAGRAM_SCREENSHOT_DIR, `diagram-${viewport.width}.png`) });
    }
  }
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
});

test('selection changes remove the old frame immediately and ignore stale or spoofed replies', { timeout: 60_000 }, async t => {
  let held, manifests = 0;
  const { page, renderer } = await mount(t, async (route, url) => {
    if (url.pathname === '/portal-renderers/mermaid/index.json' && ++manifests === 2) { held = route; return true; }
    return false;
  });
  await page.evaluate(visual => window.showDiagram(visual), graph('이전 선택'));
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
  const stale = await page.evaluate(() => window.receipts.find(message => message.type === 'rendered'));
  const oldFrameRemoved = await page.evaluate(visual => {
    window.oldDiagramWindow = document.querySelector('iframe').contentWindow;
    window.showDiagram(visual);
    return document.querySelector('iframe') === null;
  }, graph('새 선택'));
  assert.equal(oldFrameRemoved, true);
  await page.getByRole('status').filter({ hasText: '불러오고 있습니다' }).waitFor();
  assert.equal(await page.locator('.portal-diagram').getAttribute('data-diagram-state'), 'loading');
  await page.evaluate(data => window.dispatchEvent(new MessageEvent('message', {
    source: window.oldDiagramWindow, origin: 'null', data,
  })), stale);
  assert.equal(await page.locator('iframe').count(), 0);
  while (!held) await new Promise(resolve => setTimeout(resolve, 10));
  await held.fulfill({ contentType: 'application/json', body: JSON.stringify(renderer.manifest) });
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
  const frame = page.frameLocator('iframe[data-portal-diagram]');
  await frame.getByText('새 선택', { exact: true }).waitFor();
  await page.evaluate(stale => {
    const current = window.receipts.filter(message => message.type === 'rendered').at(-1);
    for (const [source, data] of [
      [window, { ...current, type: 'error' }],
      [document.querySelector('iframe').contentWindow, { ...stale, type: 'error' }],
      [document.querySelector('iframe').contentWindow, { ...current, nonce: '0'.repeat(64), type: 'error' }],
    ]) window.dispatchEvent(new MessageEvent('message', { source, origin: 'null', data }));
  }, stale);
  assert.equal(await page.getByRole('alert').count(), 0);
  await frame.getByText('새 선택', { exact: true }).waitFor();
});

test('tampered local bytes fail before iframe creation and retry recovers', { timeout: 60_000 }, async t => {
  let corrupt = true;
  const { page } = await mount(t, async (route, url, renderer) => {
    if (corrupt && url.pathname.endsWith('.html')) {
      await route.fulfill({ contentType: 'text/html', body: renderer.html.replace('<title>', '<TITLE>') });
      return true;
    }
    return false;
  });
  await page.evaluate(visual => window.showDiagram(visual), graph());
  await page.getByRole('alert').waitFor();
  assert.equal(await page.locator('.portal-diagram').getAttribute('data-diagram-state'), 'error');
  assert.equal(await page.locator('iframe').count(), 0);
  corrupt = false;
  await page.getByRole('button', { name: '다시 불러오기', exact: true }).click();
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
});

test('renderer CSP blocks external images, fonts, links, connections, frames and unhashed scripts', { timeout: 60_000 }, async t => {
  const { page } = await mount(t, undefined, true);
  await page.evaluate(visual => window.showDiagram(visual), graph());
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
  const content = page.frames().find(frame => frame.parentFrame());
  await content.evaluate(() => {
    window.probeViolations = [];
    document.addEventListener('securitypolicyviolation', event => window.probeViolations.push(event.effectiveDirective));
    const image = document.createElement('img');
    image.src = 'https://outside.invalid/image.png';
    document.body.append(image);
    const link = document.createElement('link');
    link.rel = 'stylesheet'; link.href = 'https://outside.invalid/style.css';
    document.head.append(link);
    const style = document.createElement('style');
    style.textContent = '@font-face{font-family:PortalProbe;src:url(https://outside.invalid/font.woff2)}';
    document.head.append(style);
    void document.fonts.load('16px PortalProbe').catch(() => {});
    const frame = document.createElement('iframe');
    frame.src = 'https://outside.invalid/frame';
    document.body.append(frame);
    const script = document.createElement('script');
    script.textContent = 'document.body.dataset.untrustedScript="executed"';
    document.body.append(script);
    void fetch('https://outside.invalid/connect').catch(() => {});
  });
  await content.waitForFunction(() => ['img-src', 'font-src', 'style-src-elem', 'frame-src', 'connect-src', 'script-src-elem']
    .every(directive => window.probeViolations.includes(directive)));
  assert.equal(await content.locator('body').getAttribute('data-untrusted-script'), null);
});

test('runtime accepts only a typed graph and ignores replacement renders and stale controls', { timeout: 60_000 }, async t => {
  const { page, renderer } = await mount(t);
  await page.evaluate(visual => window.showDiagram(visual), graph());
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
  const frame = page.frameLocator('iframe[data-portal-diagram]');
  const initialWidth = await frame.locator('svg').evaluate(svg => svg.getBoundingClientRect().width);
  await page.evaluate(() => {
    const current = window.receipts.find(message => message.type === 'rendered');
    const target = document.querySelector('iframe').contentWindow;
    target.postMessage({ ...current, type: 'view', command: 'zoom-in', nonce: '0'.repeat(64) }, '*');
    target.postMessage({ ...current, type: 'render', visual: 'flowchart LR; click n0 "https://outside.invalid"' }, '*');
  });
  await frame.locator('svg').evaluate(svg => new Promise(resolve => requestAnimationFrame(() => resolve(svg))));
  assert.equal(await frame.locator('svg').evaluate(svg => svg.getBoundingClientRect().width), initialWidth);

  // Exercise the iframe input boundary directly, bypassing parent validation.
  await page.evaluate(async html => {
    const probe = document.createElement('iframe');
    probe.setAttribute('data-raw-probe', '');
    probe.setAttribute('sandbox', 'allow-scripts');
    const loaded = new Promise(resolve => probe.onload = resolve);
    probe.srcdoc = html; document.body.append(probe); await loaded;
    probe.contentWindow.postMessage({ channel: 'portal-diagram/v1', type: 'render',
      nonce: '1'.repeat(64), id: '2'.repeat(32),
      visual: '%%{init:{"securityLevel":"loose"}}%%\nflowchart LR; click n0 "https://outside.invalid"' }, '*');
  }, renderer.html);
  const probe = page.frameLocator('[data-raw-probe]');
  await probe.getByRole('alert').waitFor();
  assert.equal(await probe.locator('svg').count(), 0);
});

// Exact eight labels from the PRC-000 candidate/seed, without a /tmp dependency.
function procedure() {
  const labels = ['전세대출 상품 안내', '전세대출 한도 조회', '전세대출 신청서 작성', '전세대출 서류 제출',
    '전세대출 심사 결과 조회', '전세대출 약정 체결', '전세대출 실행 조회', '전세대출 금리 안내'];
  return {
    kind: 'diagram', source: 'procedure-steps', title: '전세자금대출 신청 절차',
    nodes: labels.map((label, i) => ({ id: `step-${i + 1}`, label, type: 'Screen', assetId: `SCR-${String(i).padStart(3, '0')}` })),
    edges: labels.slice(1).map((_, i) => ({ from: `step-${i + 1}`, to: `step-${i + 2}`, label: '다음 단계', kind: 'sequence' })),
    note: 'Procedure.steps에 저장된 순서입니다.', truncated: { nodes: 0, edges: 0 },
  };
}
async function viewMetrics(frame) {
  return frame.locator('#viewport').evaluate(viewport => {
    const svg = viewport.querySelector('svg');
    const rect = element => {
      const box = element.getBoundingClientRect();
      return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height };
    };
    return {
      viewport: rect(viewport), svg: rect(svg), width: viewport.clientWidth, height: viewport.clientHeight,
      scrollWidth: viewport.scrollWidth, scrollHeight: viewport.scrollHeight, left: viewport.scrollLeft, top: viewport.scrollTop,
      nodes: [...svg.querySelectorAll('.node')].map(rect),
      edgeLabels: [...svg.querySelectorAll('.edgeLabel text')].map(rect),
      fontSizes: [...svg.querySelectorAll('.node .label text')].map(text => {
        const matrix = text.getScreenCTM();
        return parseFloat(getComputedStyle(text).fontSize) * Math.hypot(matrix.a, matrix.b);
      }),
    };
  });
}
async function everyNodeReachable(frame) {
  const nodes = frame.locator('.node');
  for (let i = 0; i < await nodes.count(); i++) {
    await nodes.nth(i).scrollIntoViewIfNeeded();
    const visible = await nodes.nth(i).evaluate(node => {
      const box = node.getBoundingClientRect(), viewport = document.getElementById('viewport').getBoundingClientRect();
      return box.left >= viewport.left - 1 && box.right <= viewport.right + 1 &&
        box.top >= viewport.top - 1 && box.bottom <= viewport.bottom + 1;
    });
    assert.equal(visible, true, `Node ${i + 1} must be reachable through viewport scrolling`);
  }
}
async function settleFrame(frame) {
  await frame.locator('svg').evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

test('eight seeded Korean procedure steps stay readable and scrollable in compact, phone and native-dialog views', { timeout: 60_000 }, async t => {
  const { page } = await mount(t);
  for (const config of [
    { width: 1200, height: 900, large: false },
    { width: 390, height: 844, large: false },
    { width: 1920, height: 1080, large: true },
  ]) {
    await page.setViewportSize({ width: config.width, height: config.height });
    await page.evaluate(({ visual, config }) => {
      const root = document.getElementById('root');
      root.style.maxWidth = config.large ? 'none' : '520px';
      if (config.large) {
        const dialog = document.createElement('dialog');
        dialog.style.cssText = 'box-sizing:border-box;width:calc(100vw - 40px);height:calc(100dvh - 40px);max-width:none;max-height:none;overflow:auto;padding:20px';
        document.body.append(dialog); dialog.append(root); dialog.showModal();
      }
      window.showDiagram(visual, { large: config.large, instance: String(config.width) });
    }, { visual: procedure(), config });
    await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
    const frame = page.frameLocator('iframe[data-portal-diagram]');
    const initial = await viewMetrics(frame);
    assert.ok(Math.min(...initial.fontSizes) >= 12, `Default label font at ${config.width}px: ${initial.fontSizes}`);
    assert.equal(initial.nodes.length, 8);
    assert.ok(initial.nodes.every((node, i, nodes) => i === 0 || node.top > nodes[i - 1].bottom),
      'The procedure must progress vertically in its supplied order');
    if (config.large) assert.ok(initial.scrollHeight <= initial.height + 1, 'All eight short steps should fit the large viewport');
    else assert.ok(initial.scrollHeight > initial.height, 'Compact procedures need a real vertical scroll range');
    for (const label of initial.edgeLabels) assert.ok(initial.nodes.every(node =>
      label.bottom <= node.top || label.top >= node.bottom || label.right <= node.left || label.left >= node.right),
    'Compact rank spacing must not overlap an edge label with a node');
    assert.ok(initial.svg.left >= 15 && initial.svg.top >= 15, 'Default content must start inside the accessible top/left padding');
    assert.equal(initial.top, 0); assert.equal(initial.left, 0);
    const dimensions = await page.locator('iframe[data-portal-diagram]').evaluate(iframe => ({
      frame: iframe.getBoundingClientRect().height, wrapper: iframe.parentElement.clientHeight,
    }));
    assert.ok(Math.abs(dimensions.frame - dimensions.wrapper) <= 1, 'The iframe must fit its wrapper');
    if (config.large) assert.ok(Math.abs(dimensions.wrapper - Math.min(config.height * 0.65, 760)) <= 1);
    else assert.ok(dimensions.wrapper <= 360);
    await everyNodeReachable(frame);
    await frame.locator('#viewport').evaluate(viewport => viewport.scrollTo(0, 0));
    if (process.env.PORTAL_DIAGRAM_SCREENSHOT_DIR) {
      const fs = require('node:fs');
      fs.mkdirSync(process.env.PORTAL_DIAGRAM_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.PORTAL_DIAGRAM_SCREENSHOT_DIR, `procedure-${config.width}.png`) });
    }
    await page.getByRole('button', { name: '화면에 맞춤', exact: true }).click();
    await settleFrame(frame);
    const overview = await viewMetrics(frame);
    assert.ok(overview.svg.height <= overview.height - 31 && overview.svg.width <= overview.width - 31);
    assert.ok(Math.min(...overview.fontSizes) <= Math.min(...initial.fontSizes), 'Fit never enlarges the default view');
    if (!config.large) assert.ok(Math.min(...overview.fontSizes) < Math.min(...initial.fontSizes),
      'Explicit Fit may reduce the scale for a compact overview');
    await page.getByRole('button', { name: '다이어그램 확대', exact: true }).click();
    await settleFrame(frame);
    assert.ok((await viewMetrics(frame)).svg.height > overview.svg.height);
    await page.getByRole('button', { name: '다이어그램 축소', exact: true }).click();
    await settleFrame(frame);
    assert.ok(Math.abs((await viewMetrics(frame)).svg.height - overview.svg.height) < 1);
    await page.getByRole('button', { name: '다시 불러오기', exact: true }).click();
    // Match the component's render deadline when exercising repeated full-bundle
    // reloads under concurrent browser/build activity.
    await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor({ timeout: 25_000 });
    assert.ok(Math.min(...(await viewMetrics(frame)).fontSizes) >= 12, 'Retry restores readable default scale');
  }
});

test('wide graphs keep a readable default and both horizontal ends reachable without negative centering', { timeout: 45_000 }, async t => {
  const { page } = await mount(t);
  await page.evaluate(visual => {
    document.getElementById('root').style.maxWidth = '440px';
    window.showDiagram(visual);
  }, { ...procedure(), source: 'screen-navigation' });
  await page.getByRole('status').filter({ hasText: '다이어그램을 표시했습니다' }).waitFor();
  const frame = page.frameLocator('iframe[data-portal-diagram]');
  const metrics = await viewMetrics(frame);
  assert.ok(Math.min(...metrics.fontSizes) >= 12);
  assert.ok(metrics.scrollWidth > metrics.width);
  assert.ok(metrics.svg.left >= 15 && metrics.svg.top >= 15);
  await everyNodeReachable(frame);
  await frame.locator('#viewport').evaluate(viewport => viewport.scrollTo(0, 0));
  const first = await viewMetrics(frame);
  assert.ok(first.nodes[0].left >= 0 && first.nodes[0].top >= 0);
});
