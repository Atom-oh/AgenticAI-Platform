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
