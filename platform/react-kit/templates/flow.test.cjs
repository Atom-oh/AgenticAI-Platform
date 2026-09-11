'use strict';
// Trusted export template. Requirements are data from the approved contract.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const contract = JSON.parse(fs.readFileSync(path.join(__dirname, 'contract.json'), 'utf8'));
const required = (contract.rules || []).filter(rule => rule.required !== false);
assert(required.length > 0, 'At least one approved required rule is necessary');
const root = path.resolve(__dirname, '../dist');
assert(fs.existsSync(path.join(root, 'index.html')), 'Run npm run build before npm test');

async function trusted(page, expression) {
  if (!page.__studioWorld) {
    const session = await page.context().newCDPSession(page);
    const tree = await session.send('Page.getFrameTree');
    const world = await session.send('Page.createIsolatedWorld', { frameId: tree.frameTree.frame.id, worldName: 'studio-export-verifier' });
    page.__studioWorld = { session, contextId: world.executionContextId };
  }
  const { session, contextId } = page.__studioWorld;
  const result = await session.send('Runtime.evaluate', { expression, contextId, returnByValue: true, awaitPromise: true });
  assert(!result.exceptionDetails, 'Trusted browser assertion failed to evaluate');
  return result.result.value;
}
for (const rule of required) test(rule.id + ' · ' + rule.title, { timeout: 45000 }, async () => {
  const server = http.createServer((request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      const relative = pathname === '/' ? 'index.html' : pathname.slice(1);
      const absolute = path.resolve(root, relative);
      if (!absolute.startsWith(root + path.sep) || !fs.existsSync(absolute) || !fs.statSync(absolute).isFile()) {
        response.writeHead(404); response.end(); return;
      }
      const extension = path.extname(absolute);
      response.setHeader('Content-Type', ({ '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css' })[extension] || 'application/octet-stream');
      response.end(fs.readFileSync(absolute));
    } catch { response.writeHead(400); response.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let context;
  const forbidden = [], errors = [];
  try {
    const origin = `http://127.0.0.1:${server.address().port}`;
    const environment = Object.fromEntries(['PATH', 'HOME', 'LANG', 'LD_LIBRARY_PATH', 'PLAYWRIGHT_BROWSERS_PATH', 'TMPDIR']
      .filter(name => process.env[name]).map(name => [name, process.env[name]]));
    context = await chromium.launchPersistentContext('', {
      headless: true, executablePath: process.env.WORKSPACE_CHROMIUM || undefined, env: environment,
      viewport: contract.viewport || { width: 390, height: 844 }, serviceWorkers: 'block', acceptDownloads: false, timeout: 10000,
      args: ['--no-sandbox', '--no-zygote', '--single-process', '--disable-dev-shm-usage', '--disable-background-networking'] });
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      forbidden.push('outside resource'); return route.abort();
    });
    await context.routeWebSocket('**/*', socket => { forbidden.push('websocket'); socket.close(); });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.name));
    page.on('console', message => { if (message.type() === 'error') errors.push('console error'); });
    page.on('popup', popup => { forbidden.push('popup'); void popup.close(); });
    await page.goto(origin + '/', { waitUntil: 'load' });
    page.setDefaultTimeout(3000);
    let assertions = 0;
    for (const step of rule.steps) {
      const byId = page.getByTestId(step.target);
      const selector = contract.bindings?.[step.target];
      let target = byId;
      if (await byId.count() !== 1 && selector) target = page.locator(selector);
      await target.waitFor({ state: 'attached' });
      assert.equal(await target.count(), 1, `Unique target required: ${step.target}`);
      const execute = async () => {
      switch (step.action) {
        case 'fill': await target.fill(String(step.value)); break;
        case 'click': await target.click(); break;
        case 'check': await target.setChecked(step.value); break;
        case 'select': await target.selectOption(String(step.value)); break;
        case 'press': await target.press(String(step.value)); break;
        case 'expectValue': assertions++; assert.equal(await target.inputValue(), String(step.value)); break;
        case 'expectEnabled': assertions++; assert.equal(await target.isEnabled(), step.value); break;
        case 'expectChecked': assertions++; assert.equal(await target.isChecked(), step.value); break;
        case 'expectVisible': assertions++; assert.equal(await target.isVisible(), step.value); break;
        case 'expectText': {
          assertions++; assert(await target.isVisible());
          let actual = await target.innerText(), expected = String(step.value);
          if (step.normalizeWhitespace) {
            actual = actual.replace(/\s+/g, ' ').trim(); expected = expected.replace(/\s+/g, ' ').trim();
          }
          if (step.match === 'equals') assert.equal(actual, expected);
          else assert(actual.includes(expected));
          break;
        }
        case 'expectStyle': {
          assertions++; assert(await target.isVisible());
          const expression = `(() => {
            const matches = document.querySelectorAll('[data-testid='+CSS.escape(${JSON.stringify(step.target)})+']');
            const element = matches.length===1 ? matches[0] : document.querySelector(${JSON.stringify(selector || ':not(*)')});
            if (!element) return null;
            const probe=document.createElement('span');
            probe.style[${JSON.stringify(step.property)}]=${JSON.stringify(String(step.value))};
            let expected=probe.style[${JSON.stringify(step.property)}];
            let actual=getComputedStyle(element)[${JSON.stringify(step.property)}];
            if(${JSON.stringify(step.property)}==='fontWeight') expected=({normal:'400',bold:'700'})[expected]||expected;
            if(expected&&(${JSON.stringify(step.property)}==='color'||${JSON.stringify(step.property)}.endsWith('Color'))){
              const painter=document.createElement('canvas').getContext('2d');
              painter.fillStyle=expected;expected=painter.fillStyle;
              painter.fillStyle=actual;actual=painter.fillStyle;
            }
            return {actual,expected};
          })()`;
          const values = await trusted(page, expression);
          assert(values && values.expected); assert.equal(values.actual, values.expected);
          break;
        }
        default: throw new Error('Unsupported contract action: ' + step.action);
      }
      };
      if (step.action.startsWith('expect')) {
        const deadline = Date.now() + 1500;
        while (true) {
          try { await execute(); break; }
          catch (error) {
            if (Date.now() >= deadline) throw error;
            await page.waitForTimeout(50);
          }
        }
      } else await execute();
    }
    assert(assertions > 0, 'A rule must assert behavior');
    assert.deepEqual(forbidden, [], 'Outside network must not be used');
    assert.deepEqual(errors, [], 'Browser errors must be resolved');
  } finally {
    if (context) await context.close();
    await new Promise(resolve => server.close(resolve));
  }
});
