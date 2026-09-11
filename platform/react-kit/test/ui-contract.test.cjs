const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createHash } = require('node:crypto');
const { createRequire, Module } = require('node:module');

const root = path.resolve(__dirname, '..');
const local = createRequire(path.join(root, 'package.json'));
const existing = createRequire(path.resolve(root, '../web/package.json'));
const dependencies = ['react', 'react-dom/server', 'esbuild', 'typescript', '@types/react/package.json'];
const runtime = dependencies.every(name => { try { local.resolve(name); return true; } catch { return false; } }) ? local : existing;
const React = runtime('react');
const { renderToStaticMarkup } = runtime('react-dom/server');
const { buildSync } = runtime('esbuild');
const ts = runtime('typescript');
const expected = ['Screen', 'Stack', 'Grid', 'Inline', 'Panel', 'Text', 'Button', 'Input',
  'Checkbox', 'Select', 'RadioGroup', 'Alert', 'Stepper', 'Summary', 'AssetImage'];
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function kit() {
  const filename = path.join(root, 'ui/index.tsx');
  const bundle = buildSync({ entryPoints: [filename], bundle: true, write: false, format: 'cjs', platform: 'node',
    jsx: 'automatic', loader: { '.css': 'empty' }, external: ['react', 'react/jsx-runtime'] });
  const compiled = new Module(filename);
  compiled.require = runtime;
  compiled._compile(bundle.outputFiles[0].text, filename);
  return compiled.exports;
}

test('exports are the real 15-component React 18.3.1 kit', () => {
  assert.equal(React.version, '18.3.1');
  assert.deepEqual(Object.keys(kit()).sort(), expected.sort());
});

test('fixed component markup ignores caller class/style/raw HTML', () => {
  const { Button, Panel } = kit();
  const markup = renderToStaticMarkup(React.createElement(Panel, { title: '기준 패널' },
    React.createElement(Button, {
      label: '<script>문자 그대로</script>', testId: 'continue', id: 'continue-button',
      className: 'injected-class', style: { backgroundColor: 'hotpink' },
      dangerouslySetInnerHTML: { __html: '<img src="https://outside.invalid">' },
      onMouseOver: () => {},
    })));
  assert(markup.includes('data-studio-component="Panel"'));
  assert(markup.includes('data-studio-component="Button"'));
  assert(markup.includes('data-studio-version="1.0.0"'));
  assert(markup.includes('data-testid="continue"'));
  assert(markup.includes('type="button"'));
  assert(markup.includes('&lt;script&gt;문자 그대로&lt;/script&gt;'));
  assert(!markup.includes('injected-class'));
  assert(!markup.includes('hotpink'));
  assert(!markup.includes('outside.invalid'));
});

test('runtime casts cannot select foreign elements or escape approved variant tokens', () => {
  const { Text, Stack, Grid, Inline, Panel, Button, Alert } = kit();
  let invoked = false;
  const foreign = () => { invoked = true; return React.createElement('script', null, 'bad'); };
  for (const as of ['script', 'iframe', 'style', foreign, { toString() { throw Error('must not coerce'); } }]) {
    const markup = renderToStaticMarkup(React.createElement(Text, { as, tone: 'bad', size: 'bad' }, '안전한 내용'));
    assert(markup.startsWith('<p '), markup);
    assert(markup.includes('data-tone="default"') && markup.includes('data-size="md"'));
  }
  assert.equal(invoked, false);
  const markup = renderToStaticMarkup(React.createElement(Stack, { gap: 999 },
    React.createElement(Grid, { columns: 999, gap: 'url(outside)' }, '내용'),
    React.createElement(Inline, { gap: 8, align: 'stretch', justify: 'around' }, '내용'),
    React.createElement(Panel, { tone: 'custom' }, '내용'),
    React.createElement(Button, { label: '다음', kind: 'link', type: 'reset' }),
    React.createElement(Alert, { message: '안내', tone: 'custom' })));
  assert(!markup.includes('999') && !markup.includes('url(outside)'));
  assert(markup.includes('data-columns="1"'));
  assert(markup.includes('data-align="center"') && markup.includes('data-justify="start"'));
  assert(markup.includes('data-kind="primary"') && markup.includes('data-tone="info"'));
  assert(markup.includes('type="button"'));
});

test('form inputs expose native control targets, labels and error evidence', () => {
  const { Input, Select, Checkbox } = kit();
  const markup = renderToStaticMarkup(React.createElement(React.Fragment, null,
    React.createElement(Input, { id: 'amount', testId: 'amount-test', label: '납입금액', value: '', onChange() {},
      type: 'number', min: 1, max: 100, required: true, hint: '1~100', error: '금액을 입력하세요.' }),
    React.createElement(Select, { id: 'period', testId: 'period-test', label: '납입주기', value: 'month', onChange() {},
      options: [{ value: 'month', label: '매월' }] }),
    React.createElement(Checkbox, { id: 'consent', testId: 'consent-test', label: '필수 동의', checked: false, onChange() {}, required: true })));
  assert.match(markup, /<input[^>]*data-testid="amount-test"/);
  assert.match(markup, /<select[^>]*data-testid="period-test"/);
  assert.match(markup, /<input[^>]*data-testid="consent-test"/);
  assert(markup.includes('for="amount"'));
  assert(markup.includes('aria-invalid="true"'));
  assert(markup.includes('aria-describedby='));
  assert(markup.includes('min="1"') && markup.includes('max="100"'));
  assert(markup.includes('role="alert"'));
});

test('semantic progress, summary and alerts do not invent completed steps', () => {
  const { Stepper, Summary, Alert } = kit();
  const markup = renderToStaticMarkup(React.createElement(React.Fragment, null,
    React.createElement(Stepper, { steps: [{ id: 'entry', label: '입력' }, { id: 'confirm', label: '확인' }], current: 'confirm' }),
    React.createElement(Summary, { title: '입력 확인', items: [{ label: '금액', value: '10,000원' }] }),
    React.createElement(Alert, { tone: 'warning', title: '확인 필요', message: '실제 거래가 아닙니다.' })));
  assert.equal((markup.match(/aria-current="step"/g) || []).length, 1);
  assert(markup.includes('<dl') && markup.includes('<dt') && markup.includes('<dd'));
  assert(markup.includes('10,000원'));
  assert(markup.includes('실제 거래가 아닙니다.'));
});

test('AssetImage rejects URL sources before rendering a network-bearing element', () => {
  const { AssetImage } = kit();
  for (const src of ['https://outside.invalid/image.png', '/image.png', 'javascript:alert(1)', 'data:text/html,<script>bad</script>']) {
    assert.throws(() => renderToStaticMarkup(React.createElement(AssetImage, { src, alt: '이미지' })), /data:image/);
  }
  const src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=';
  assert(renderToStaticMarkup(React.createElement(AssetImage, { src, alt: '기준 이미지', width: 1, height: 1 })).includes(src));
});

function diagnostics(source) {
  const virtual = path.join(root, '__type_test__.tsx');
  const reactTypes = path.dirname(runtime.resolve('@types/react/package.json'));
  const options = { strict: true, noEmit: true, skipLibCheck: true, target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, jsx: ts.JsxEmit.ReactJSX,
    lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], types: [], baseUrl: root,
    paths: { react: [path.join(reactTypes, 'index.d.ts')], 'react/jsx-runtime': [path.join(reactTypes, 'jsx-runtime.d.ts')] } };
  const host = ts.createCompilerHost(options);
  const originalGet = host.getSourceFile.bind(host);
  host.getSourceFile = (file, version, onError, fresh) => file === virtual ? ts.createSourceFile(file, source, version, true) : originalGet(file, version, onError, fresh);
  const program = ts.createProgram([virtual, path.join(root, 'ui/styles.d.ts')], options, host);
  return ts.getPreEmitDiagnostics(program).map(item => ts.flattenDiagnosticMessageText(item.messageText, '\n'));
}

test('actual kit types accept composition but reject styling/ref/raw-HTML escape props', () => {
  const prefix = `import {Screen,Button,Input,Text} from './ui/index';\n`;
  const valid = diagnostics(prefix + `const view=<Screen pageId="entry"><Text as="h1">가입</Text><Input label="금액" value="" onChange={value=>{}}/><Button label="다음" onClick={()=>{}}/></Screen>;`);
  assert.deepEqual(valid, []);
  for (const props of ['className="override"', 'style={{color:"red"}}', 'ref={()=>{}}', 'dangerouslySetInnerHTML={{__html:"bad"}}']) {
    const errors = diagnostics(prefix + `const view=<Button label="다음" ${props}/>;`);
    assert(errors.some(message => message.includes('does not exist')), errors.join('\n'));
  }
  assert(diagnostics(prefix + 'const view=<Input label="비밀번호" value="" onChange={()=>{}} type="password"/>;').length);
});

test('catalog is honest, complete and source hash is canonical and sensitive to actual bytes', () => {
  const manifest = require(path.join(root, 'manifest.cjs'));
  const first = manifest.catalog();
  assert.equal(first.schemaVersion, 1);
  assert.equal(first.id, 'studio-ui');
  assert.equal(first.version, '1.0.0');
  assert.equal(first.label, '플랫폼 기본 React 컴포넌트');
  assert.deepEqual(first.components.map(component => component.name).sort(), expected.sort());
  assert(first.components.every(component => component.description && component.props && Array.isArray(component.variationAxes)));
  assert.deepEqual(first.files.map(file => file.path), [...first.files.map(file => file.path)].sort());
  assert.equal(first.hash, digest(Buffer.from(JSON.stringify(first.files))));
  for (const file of first.files) assert.equal(file.sha256, digest(fs.readFileSync(path.join(root, file.path))));
  assert.deepEqual(first, manifest.catalog());
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-ui-manifest-'));
  try {
    fs.cpSync(path.join(root, 'ui'), path.join(temporary, 'ui'), { recursive: true });
    for (const name of ['manifest.cjs', 'catalog.json']) fs.copyFileSync(path.join(root, name), path.join(temporary, name));
    const copy = require(path.join(temporary, 'manifest.cjs'));
    assert.equal(copy.catalog().hash, first.hash);
    fs.mkdirSync(path.join(temporary, 'node_modules'));
    fs.writeFileSync(path.join(temporary, 'node_modules/ignored'), 'not catalog material');
    assert.equal(copy.catalog().hash, first.hash);
    fs.appendFileSync(path.join(temporary, 'ui/tokens.css'), '\n/* changed token bytes */\n');
    assert.notEqual(copy.catalog().hash, first.hash);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});
