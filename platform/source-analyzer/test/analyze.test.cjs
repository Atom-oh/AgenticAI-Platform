const { test } = require('node:test');
const assert = require('node:assert/strict');
const { analyze, sha, LIMITS } = require('../analyze.cjs');
const text = (path, source, kind = 'code') => ({ path, kind, text: source, sha256: sha(source) });
const asset = path => ({ path, kind: 'asset', sha256: sha(path) });
const request = files => ({ schemaVersion: 1, files });

test('import equals and import type retain literal dependencies; namespace aliases remain unknown', () => {
  const result = analyze(request([
    text('App.tsx', 'import Button = require("./Button");type Props = import("./Button").Props;export const App=()=> <Button/>'),
    text('Button.tsx', 'export type Props = {};export = function Button(){return <button/>}'),
  ]));
  for (const kind of ['import-equals', 'type-import', 'jsx-use'])
    assert.ok(result.references.some(r => r.kind === kind && r.resolution.targetPath === 'Button.tsx'));
  const alias = analyze(request([text('Alias.ts', 'namespace A {export const B=1;}import B=A.B;')]));
  assert.equal(alias.coverage.complete, false);
  assert.ok(alias.unresolved.some(r => r.reason === 'namespace-import-alias'));
});

test('package subpath imports require an explicit alias; code # is not an HTML fragment', () => {
  const input = request([text('App.ts', "import tokens from '#design/tokens';"), text('tokens.ts', 'export default {}')]);
  assert.equal(analyze(input).coverage.complete, false);
  input.resolver = { aliases: { '#design/tokens': 'tokens.ts' }, packages: {}, jsonAssetFields: [] };
  assert.equal(analyze(input).references[0].resolution.targetPath, 'tokens.ts');
});

test('module URLs and require.resolve preserve dependencies; workers and transforms stay unknown', () => {
  const result = analyze(request([
    text('App.ts', 'const icon=new URL("./icon.svg",import.meta.url);const w=new Worker(new URL("./worker.ts",import.meta.url));require.resolve("./theme.css");import.meta.glob("./*.ts");'),
    asset('icon.svg'), text('worker.ts', 'export const worker=true;'), text('theme.css', '.test{}', 'style'),
  ]));
  for (const target of ['icon.svg', 'worker.ts', 'theme.css'])
    assert.ok(result.references.some(ref => ref.resolution.targetPath === target));
  assert.equal(result.coverage.complete, false);
  for (const reason of ['worker-runtime-semantics', 'runtime-module-resolution', 'bundler-meta-transform'])
    assert.ok(result.unresolved.some(item => item.reason === reason));
});

test('a local require or URL function is not fabricated parser dependency evidence', () => {
  const result = analyze(request([
    text('App.ts', 'function require(x:string){return x;}class URL{constructor(...x:any[]) {}} require("./data.json");new URL("./icon.svg",import.meta.url);'),
    text('data.json', '{}', 'json'), asset('icon.svg'),
  ]));
  assert.equal(result.references.length, 0);
  assert.equal(result.coverage.complete, false);
});

test('ambient loaders and require.context remain unknown instead of disappearing', () => {
  const result = analyze(request([text('App.ts', 'declare const require:any;require("./x");require.context("./widgets");')]));
  assert.equal(result.coverage.complete, false);
  assert.ok(result.unresolved.some(item => item.reason === 'shadowed-or-ambient-module-loader'));
  assert.ok(result.unresolved.some(item => item.reason === 'unmodeled-module-loader'));
});

test('const JSX aliases retain lexical imports while mutable aliases remain unknown', () => {
  const result = analyze(request([
    text('App.tsx', "import {Button} from './Button';const Primary=Button;let Dynamic=Button;export const App=()=> <><Primary/><Dynamic/></>"),
    text('Button.tsx', 'export function Button(){return <button/>}'),
  ]));
  assert.ok(result.references.some(item => item.kind === 'jsx-use' && item.localName === 'Primary' && item.symbol === 'Button'));
  assert.equal(result.references.some(item => item.kind === 'jsx-use' && item.localName === 'Dynamic'), false);
  assert.ok(result.unresolved.some(item => item.reason === 'local-jsx-binding-not-traced'));
});

test('CSS module dependencies are recorded with unresolved transform semantics', () => {
  const result = analyze(request([
    text('app.css', '@value primary from "./tokens.css";.button { composes: base from "./base.css"; }', 'style'),
    text('tokens.css', ':root{--color:red}', 'style'), text('base.css', '.base{display:flex}', 'style'),
  ]));
  assert.equal(result.coverage.complete, false);
  for (const target of ['tokens.css', 'base.css'])
    assert.ok(result.references.some(item => item.resolution.targetPath === target));
  const extension = analyze(request([text('app.css', '@import "./theme";', 'style'), text('theme.ts', 'export const value=1')]));
  assert.equal(extension.references.some(item => item.resolution.status === 'resolved-local'), false);
});

test('CSS source-map discovery cannot inspect host files', () => {
  const PreviousMap = require('../node_modules/postcss/lib/previous-map');
  const original = PreviousMap.prototype.loadFile;
  let attempted = false;
  PreviousMap.prototype.loadFile = () => { attempted = true; throw new Error('unexpected host file read'); };
  try {
    const result = analyze(request([text('app.css', '.button{color:red}/*# sourceMappingURL=sentinel.map */', 'style')]));
    assert.equal(attempted, false);
    assert.deepEqual(result.diagnostics, []);
    assert.ok(result.unresolved.some(item => item.reason === 'source-map-not-loaded'));
  } finally { PreviousMap.prototype.loadFile = original; }
});

test('uninspected calls, constructors and HTML navigation never claim full coverage', () => {
  const code = analyze(request([text('app.ts', 'fetch(endpoint);new EventSource(endpoint);location.assign(nextUrl);')]));
  assert.equal(code.coverage.complete, false);
  assert.ok(code.unresolved.some(item => item.reason === 'call-semantics-not-inspected'));
  const html = analyze(request([text('index.html',
    '<form action="/submit"><object data="./diagram.svg"></object></form><meta http-equiv="refresh" content="0;url=/next">', 'html'),
    asset('diagram.svg')]));
  assert.equal(html.coverage.complete, false);
  assert.ok(html.references.some(item => item.resolution.targetPath === 'diagram.svg'));
  for (const reason of ['html-form-submission', 'html-active-content', 'html-navigation'])
    assert.ok(html.unresolved.some(item => item.reason === reason));
});

test('real TS parser connects imports, JSX symbols, image imports and CSS resources', () => {
  const input = request([
    text('src/App.tsx', `import {Button as Action} from './Button';
import hero from '../images/hero.png';
import './styles.css';
export default function App(){return <><Action/><img src={hero}/></>}`),
    text('src/Button.tsx', 'export function Button(){return <button>Next</button>}'),
    text('src/styles.css', '.hero{background:url("../images/hero.png")}', 'style'),
    asset('images/hero.png'),
  ]);
  const result = analyze(input);
  assert.equal(result.coverage.complete, true);
  assert.equal(result.coverage.runtimeComplete, false);
  assert.ok(result.references.some(r => r.kind === 'jsx-use' && r.symbol === 'Button' && r.localName === 'Action' &&
    r.resolution.targetPath === 'src/Button.tsx' && r.line === 4));
  assert.ok(result.references.some(r => r.kind === 'asset-use' && r.resolution.targetPath === 'images/hero.png'));
  assert.ok(result.references.some(r => r.kind === 'style-asset' && r.resolution.targetPath === 'images/hero.png'));
  assert.ok(result.exports.some(e => e.path === 'src/App.tsx' && e.name === 'default'));
  assert.equal(analyze(input).hash, result.hash);
});

test('lexical shadowing cannot turn a local JSX parameter into an imported component dependency', () => {
  const result = analyze(request([
    text('src/App.tsx', `import {Button} from './Button';
export function App({Button}: {Button: any}){return <Button/>}`),
    text('src/Button.tsx', 'export function Button(){return <button/>}'),
  ]));
  assert.equal(result.references.filter(r => r.kind === 'import').length, 1);
  assert.equal(result.references.filter(r => r.kind === 'jsx-use').length, 0);
});

test('declared aliases resolve exact manifest files; packages require a pinned identity', () => {
  const input = request([text('src/App.tsx', "import React from 'react';import hero from '@images/hero.png';export default ()=> <img src={hero}/>"),
    asset('images/hero.png')]);
  input.resolver = { aliases: { '@images/*': 'images/*' }, packages: { react: { version: '18.3.1', sha256: sha('approved-package') } } };
  const result = analyze(input);
  assert.equal(result.coverage.complete, true);
  assert.ok(result.references.some(r => r.resolution.status === 'approved-package' && r.resolution.package === 'react'));
  input.resolver.packages.react.sha256 = 'invented';
  assert.throws(() => analyze(input), /identity/);
});

test('extension ambiguity, escaping paths, missing aliases and dynamic references remain unresolved', () => {
  const result = analyze(request([
    text('App.tsx', `import X from './Thing';import Y from '../../outside';import Z from '@unknown/ui';
export const get=()=>import('./Thing.ts');export const App=({image}:any)=><img src={image}/>;`),
    text('Thing.ts', 'export default 1'), text('Thing.tsx', 'export default 2'),
  ]));
  const reasons = result.unresolved.map(r => r.reason);
  for (const reason of ['ambiguous-resolution', 'path-escape', 'unmapped-package-or-alias',
    'dynamic-or-commonjs-dependency', 'computed-asset-reference']) assert.ok(reasons.includes(reason), reason);
  assert.equal(result.coverage.complete, false);
});

test('static browser URL strings are not resolved relative to a TSX source directory', () => {
  const result = analyze(request([text('nested/App.tsx', 'export default ()=> <img src="./hero.png"/>'), asset('nested/hero.png')]));
  assert.equal(result.references.length, 0);
  assert.ok(result.unresolved.some(r => r.reason === 'runtime-url-base-not-configured'));
});

test('CSS parser ignores comments, handles relative URLs and reports SCSS interpolation', () => {
  const result = analyze(request([
    text('styles/main.scss', `/* background:url(missing.png) */ .a{background:url(icon.png)}
.b{background:url("#{$icon}")}`, 'style'),
    asset('styles/icon.png'),
  ]));
  assert.equal(result.references.filter(r => r.resolution.targetPath === 'styles/icon.png').length, 1);
  assert.equal(result.references.some(r => r.specifier === 'missing.png'), false);
  assert.ok(result.unresolved.some(r => r.reason === 'computed-style-reference'));
});

test('JSON reference fields are an explicit resolver contract, not a search over arbitrary strings', () => {
  const input = request([text('manifest.json', '{"images":["./hero.png"],"note":"./not-an-asset.png"}', 'json'), asset('hero.png')]);
  input.resolver = { jsonAssetFields: ['images'] };
  const result = analyze(input);
  assert.equal(result.references.length, 1);
  assert.equal(result.references[0].resolution.targetPath, 'hero.png');
  assert.equal(result.coverage.complete, true);
});

test('source identity, case collisions, source kinds and budgets are rejected before analysis', () => {
  assert.throws(() => analyze(request([{ ...text('App.ts', 'export const x=1'), sha256: sha('wrong') }])), /bytes/);
  assert.throws(() => analyze(request([text('../App.ts', '')])), /path/);
  assert.throws(() => analyze(request([text('App.ts', ''), text('app.ts', '')])), /ambiguous/);
  assert.throws(() => analyze(request([{ path: 'App.ts', kind: 'asset', sha256: sha('hidden-code') }])), /extension/);
  assert.throws(() => analyze(request([text('App.ts', 'x'.repeat(LIMITS.fileBytes + 1))])), /bytes/);
  assert.throws(() => analyze(request(Array.from({ length: 101 }, (_, i) => text(`f${i}.ts`, '')))), /manifest/);
});

test('parse failures are bounded diagnostics and never execute imported source', () => {
  const result = analyze(request([text('App.tsx', 'export default function { malicious')]));
  assert.equal(result.coverage.complete, false);
  assert.equal(result.diagnostics[0].code, 'parse-error');
  assert.equal(JSON.stringify(result).includes('malicious'), false);
});

test('HTML extraction connects literal resources without executing scripts or guessing a base URL', () => {
  const files = [text('pages/export.html', '<!doctype html><img src="../images/hero.png"><script>throw Error("must not run")</script>', 'html'),
    asset('images/hero.png')];
  const result = analyze(request(files));
  assert.ok(result.references.some(item => item.kind === 'html-resource' && item.resolution.targetPath === 'images/hero.png'));
  assert.ok(result.unresolved.some(item => item.reason === 'inline-script-not-executed'));
  files[0] = text('pages/export.html', '<img src="../images/hero.png"><base href="https://elsewhere.invalid/">', 'html');
  const changed = analyze(request(files));
  assert.equal(changed.references.length, 0);
  assert.ok(changed.unresolved.some(item => item.reason === 'html-resource-base-unresolved'));
});
