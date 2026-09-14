const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const ts = require('typescript');
const { chromium } = require('playwright');

const web = path.resolve(__dirname, '..');
const library = path.resolve(web, '../component-library');
const dependencyPaths = [path.join(web, 'node_modules'),
  ...(process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean)];
const expected = {
  Button: 3, Input: 3, Table: 2, Modal: 2, DatePicker: 2, Select: 2,
  Card: 2, Tabs: 2, Stepper: 1, FileUpload: 1, Badge: 1, Toast: 1,
};
const expectedIds = Object.entries(expected).flatMap(([name, count]) =>
  Array.from({ length: count }, (_, i) => `CMP-${name}-v${i + 1}`)).sort();
const catalog = () => JSON.parse(fs.readFileSync(path.join(library, 'catalog.json'), 'utf8'));

function registrySchemas() {
  const script = `
import ast,json,sys
tree=ast.parse(open(sys.argv[1]).read())
entry=next(n for n in tree.body if isinstance(n,ast.AnnAssign) and getattr(n.target,'id','') in ('RICH_SCHEMAS','_UI_COMPONENT_SCHEMAS'))
out={}
for key,value in zip(entry.value.keys,entry.value.values):
 name,version=ast.literal_eval(key)
 props=value.args[0]
 out[f'CMP-{name}-v{version}']={'required':ast.literal_eval(value.args[1]),'props':{}}
 for field,spec in zip(props.keys,props.values):
  info={'kind':spec.func.id}
  if spec.func.id=='_enum':info['enum']=[ast.literal_eval(v) for v in spec.args]
  out[f'CMP-{name}-v{version}']['props'][ast.literal_eval(field)]=info
print(json.dumps(out))
`;
  const result = spawnSync('python3', ['-c', script, path.resolve(web, '../registry/seed.py')], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('catalog maps exactly 22 reference versions to matching Registry props and public types', () => {
  const data = catalog();
  assert.equal(data.schemaVersion, 1);
  assert.equal(data.package, '@atom/portal-components');
  assert.deepEqual(data.components.map(c => c.id).sort(), expectedIds);
  assert.deepEqual(data.sharedFiles, ['ui/shared.tsx', 'ui/styles.css']);
  const schemas = registrySchemas();
  const types = dependencyPaths.map(p => path.join(p, '@types')).filter(p => fs.existsSync(p));
  const react = types.map(p => path.join(p, 'react')).find(p => fs.existsSync(p));
  const sourceFiles = [...new Set(data.components.map(c => path.join(library, c.entry))
    .concat(path.join(library, 'ui/shared.tsx'), path.join(library, 'index.ts'), path.join(library, 'demos.tsx')))];
  const program = ts.createProgram(sourceFiles, {
    noEmit: true, strict: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Node10, jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true, skipLibCheck: true, typeRoots: types,
    baseUrl: library, paths: { react: [path.join(react, 'index.d.ts')], 'react/*': [path.join(react, '*')] },
  });
  const diagnostics = ts.getPreEmitDiagnostics(program);
  assert.equal(diagnostics.length, 0, diagnostics.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')).join('\n'));
  const checker = program.getTypeChecker();
  for (const component of data.components) {
    const major = component.id.match(/-v(\d+)$/)[1];
    assert.equal(component.version, `${major}.0.0`);
    assert.equal(component.entry, `ui/${component.name}/v${major}.tsx`);
    assert.equal(component.exportName, `${component.name}V${major}`);
    assert.equal(typeof component.description, 'string');
    assert.equal(typeof component.changes, 'string');
    assert(component.usage.includes(`import { ${component.exportName} } from '@atom/portal-components'`));
    assert.deepEqual(Object.keys(component.props).sort(), Object.keys(schemas[component.id].props).sort());
    assert(Object.values(component.props).every(value => typeof value === 'string'));
    const file = program.getSourceFile(path.join(library, component.entry));
    const exported = checker.getExportsOfModule(checker.getSymbolAtLocation(file))
      .find(symbol => symbol.name === component.exportName);
    assert(exported, component.id);
    const signature = checker.getTypeOfSymbolAtLocation(exported, file).getCallSignatures()[0];
    const param = signature.getParameters()[0];
    const properties = checker.getPropertiesOfType(checker.getTypeOfSymbolAtLocation(param, file));
    assert.deepEqual(properties.map(p => p.name).sort(), Object.keys(schemas[component.id].props).sort());
    assert.deepEqual(properties.filter(p => !(p.flags & ts.SymbolFlags.Optional)).map(p => p.name).sort(),
      schemas[component.id].required.slice().sort());
    for (const property of properties) {
      const schema = schemas[component.id].props[property.name];
      if (!schema.enum) continue;
      const valueType = checker.getTypeOfSymbolAtLocation(property, file);
      const members = valueType.isUnion() ? valueType.types : [valueType];
      assert.deepEqual(members.filter(t => t.isStringLiteral()).map(t => t.value).sort(), schema.enum.slice().sort());
    }
  }
});

test('entry plus sharedFiles is a complete independent package source closure', async () => {
  const data = catalog();
  const fingerprints = new Set();
  for (const component of data.components) {
    const files = [component.entry, ...data.sharedFiles].sort().map(file => ({
      path: file, content: fs.readFileSync(path.join(library, file), 'utf8'),
    }));
    const map = new Map(files.map(file => [file.path, file.content]));
    await build({
      entryPoints: [component.entry], bundle: true, write: false, outfile: '/virtual/component.js',
      jsx: 'automatic', external: ['react', 'react/jsx-runtime'],
      plugins: [{ name: 'contract-files-only', setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => {
          if (args.path === 'react' || args.path.startsWith('react/')) return { path: args.path, external: true };
          const candidate = args.kind === 'entry-point' ? args.path :
            path.posix.normalize(path.posix.join(path.posix.dirname(args.importer), args.path));
          const resolved = [candidate, candidate + '.tsx', candidate + '.ts', candidate + '.css'].find(p => map.has(p));
          assert(resolved, `${component.id} has an undeclared source dependency: ${args.path}`);
          return { path: resolved, namespace: 'reference' };
        });
        builder.onLoad({ filter: /.*/, namespace: 'reference' }, args => ({
          contents: map.get(args.path), loader: args.path.endsWith('.css') ? 'css' : 'tsx',
        }));
      } }],
    });
    const identity = {
      id: component.id, version: component.version, package: data.package, exportName: component.exportName,
      files: files.map(file => ({ path: file.path, sha256: createHash('sha256').update(file.content).digest('hex') })),
    };
    fingerprints.add(createHash('sha256').update(JSON.stringify(identity)).digest('hex'));
  }
  assert.equal(fingerprints.size, 22);
});

let bundle;
async function mount(t) {
  bundle ||= await build({
    stdin: { loader: 'tsx', resolveDir: library, contents: `
import React from 'react';import {createRoot} from 'react-dom/client';import {flushSync} from 'react-dom';
import {DEMOS} from './demos';
const root=createRoot(document.getElementById('root'));
window.demoIds=Object.keys(DEMOS);
window.show=(id)=>flushSync(()=>root.render(React.createElement(DEMOS[id],{key:id})));
` },
    bundle: true, write: false, outfile: '/virtual/app.js', jsx: 'automatic', nodePaths: dependencyPaths,
    define: { 'process.env.NODE_ENV': '"production"' },
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM,
    headless: true, args: ['--no-sandbox', '--disable-background-networking'] });
  const context = await browser.newContext({ offline: true, viewport: { width: 1000, height: 850 } });
  const errors = [], requests = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.href !== 'https://reference.invalid/') {
      requests.push(url.href); return route.abort();
    }
    const css = bundle.outputFiles.find(file => file.path.endsWith('.css'))?.text || '';
    const js = bundle.outputFiles.find(file => file.path.endsWith('.js')).text;
    return route.fulfill({ contentType: 'text/html', body:
      `<!doctype html><html lang="ko"><head><title>Reference components</title><style>${css}</style></head><body><div id="root"></div><script>${js.replace(/<\/script/gi, '<\\/script')}</script></body></html>` });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  t.after(async () => { await browser.close(); assert.deepEqual(errors, []); assert.deepEqual(requests, []); });
  await page.goto('https://reference.invalid/');
  return page;
}
const show = (page, id) => page.evaluate(id => window.show(id), id);

test('all exact-version demos render real reference components without external requests', async t => {
  const page = await mount(t);
  assert.deepEqual((await page.evaluate(() => window.demoIds)).sort(), expectedIds);
  for (const item of catalog().components) {
    await show(page, item.id);
    if (item.name === 'Modal') await page.getByRole('button', { name: '대화상자 열기', exact: true }).click();
    const root = page.locator(`[data-component-id="${item.id}"]`);
    await root.waitFor();
    assert.equal(await root.getAttribute('data-portal-component'), item.name);
    assert.equal(await root.getAttribute('data-portal-version'), item.version);
    assert.equal(await page.locator('[data-studio-component]').count(), 0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
});

test('button and input versions preserve their own props, disabled behavior and accessible errors', async t => {
  const page = await mount(t);
  for (const id of ['CMP-Button-v1', 'CMP-Button-v2', 'CMP-Button-v3']) {
    await show(page, id);
    await page.getByRole('button', { name: '가상 신청 확인', exact: true }).click();
    await page.getByText('확인 횟수: 1', { exact: true }).waitFor();
  }
  await page.getByLabel('버튼 비활성화').check();
  assert(await page.getByRole('button', { name: '가상 신청 확인', exact: true }).isDisabled());
  for (const id of ['CMP-Input-v1', 'CMP-Input-v2', 'CMP-Input-v3']) {
    await show(page, id);
    await page.getByLabel('가상 신청명', { exact: true }).fill('합성 신청');
    await page.getByText('현재 입력: 합성 신청', { exact: true }).waitFor();
  }
  await page.getByLabel('오류 표시').check();
  const input = page.getByLabel('가상 신청명', { exact: true });
  assert.equal(await input.getAttribute('aria-invalid'), 'true');
  const described = await input.getAttribute('aria-describedby');
  assert(described);
  assert.equal(await page.locator(`[id="${described}"]`).innerText(), '입력 내용을 다시 확인하세요.');
});

test('table v2 sorts actual rows and select v2 searches without corrupting controlled selection', async t => {
  const page = await mount(t);
  await show(page, 'CMP-Table-v2');
  await page.getByRole('button', { name: '금액 정렬', exact: true }).click();
  assert.equal(await page.locator('tbody tr').first().locator('td').first().innerText(), '가상 B');
  await page.getByRole('button', { name: '금액 정렬', exact: true }).click();
  assert.equal(await page.locator('tbody tr').first().locator('td').first().innerText(), '가상 A');
  await show(page, 'CMP-Select-v2');
  await page.getByLabel('가상 업무', { exact: true }).selectOption('pension');
  await page.getByLabel('가상 업무 검색', { exact: true }).fill('다른 결과 없음');
  assert.equal(await page.getByLabel('가상 업무', { exact: true }).inputValue(), 'pension');
  await page.getByText('선택한 업무: pension', { exact: true }).waitFor();
  await page.getByLabel('가상 업무 검색', { exact: true }).fill('저축');
  await page.getByLabel('가상 업무', { exact: true }).selectOption('savings');
  await page.getByText('선택한 업무: savings', { exact: true }).waitFor();
});

test('modal versions trap focus, dismiss on Escape and restore focus to the trigger', async t => {
  const page = await mount(t);
  for (const id of ['CMP-Modal-v1', 'CMP-Modal-v2']) {
    await show(page, id);
    const trigger = page.getByRole('button', { name: '대화상자 열기', exact: true });
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: '가상 신청 확인', exact: true });
    await dialog.waitFor();
    await page.getByRole('button', { name: '확인 후 닫기', exact: true }).focus();
    await page.keyboard.press('Tab');
    assert(await dialog.getByRole('button', { name: '대화상자 닫기', exact: true }).evaluate(el => el === document.activeElement));
    await page.keyboard.press('Shift+Tab');
    assert(await dialog.getByRole('button', { name: '확인 후 닫기', exact: true }).evaluate(el => el === document.activeElement));
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert(await trigger.evaluate(el => el === document.activeElement));
  }
});

test('tabs use keyboard selection, date v2 bounds dates, and local files/toasts can be cleared', async t => {
  const page = await mount(t);
  for (const id of ['CMP-Tabs-v1', 'CMP-Tabs-v2']) {
    await show(page, id);
    await page.getByRole('tab', { name: '신청', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('tab', { name: '검토', exact: true }).getAttribute('aria-selected'), 'true');
    await page.keyboard.press('Home');
    assert.equal(await page.getByRole('tab', { name: '신청', exact: true }).getAttribute('aria-selected'), 'true');
  }
  await show(page, 'CMP-DatePicker-v2');
  const date = page.getByLabel('가상 검토일', { exact: true });
  assert.equal(await date.getAttribute('min'), '2026-01-01');
  assert.equal(await date.getAttribute('max'), '2026-12-31');
  await date.fill('2027-01-01');
  assert.equal(await date.evaluate(el => el.validity.rangeOverflow), true);
  await show(page, 'CMP-FileUpload-v1');
  await page.getByLabel('가상 첨부 파일', { exact: true }).setInputFiles({
    name: 'synthetic.txt', mimeType: 'text/plain', buffer: Buffer.from('Synthetic local selection'),
  });
  await page.getByText('synthetic.txt', { exact: true }).first().waitFor();
  await page.getByRole('button', { name: '파일 선택 지우기', exact: true }).click();
  await page.getByText('선택한 파일 없음', { exact: true }).waitFor();
  await show(page, 'CMP-Toast-v1');
  await page.getByRole('button', { name: '알림 닫기', exact: true }).click();
  assert.equal(await page.locator('[data-component-id="CMP-Toast-v1"]').count(), 0);
});
