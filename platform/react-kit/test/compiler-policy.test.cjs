const { test } = require('node:test');
const assert = require('node:assert/strict');
const { inspectSources } = require('../policy.cjs');

const components = ['Screen', 'Stack', 'Text', 'Button', 'Input', 'Checkbox', 'AssetImage']
  .map(name => ({ name }));
const catalog = { components };
const good = `
import {useState} from 'react';
import {Screen,Stack,Text,Button,Input} from '@studio/approved-ui';
export default function App(){
 const [amount,setAmount]=useState('300000');
 const [done,setDone]=useState(false);
 return <Screen pageId="entry"><Stack><Text as="h1">신청</Text>
 {done ? <Text testId="summary">{amount}</Text> : <Input testId="amount" label="금액" value={amount} onChange={setAmount}/>}
 <Button testId="next" label="다음" onClick={()=>setDone(true)}/></Stack></Screen>;
}`;

test('allows controlled composition, real hooks and static page identities', () => {
  const result = inspectSources({ 'src/App.tsx': good }, catalog);
  assert.equal(result.ok, true, result.diagnostics.join('\n'));
  assert.deepEqual(result.pageSources, [{ pageId: 'entry', path: 'src/App.tsx' }]);
  assert(result.components.includes('Input'));
});

test('rejects rewriting kit, executable config and path traversal', () => {
  for (const path of ['src/approved-ui/index.tsx', '../App.tsx', '/tmp/code.tsx', 'vite.config.ts', 'package.json']) {
    const result = inspectSources({ 'src/App.tsx': good, [path]: 'export default 1' }, catalog);
    assert.equal(result.ok, false, path);
  }
});

test('rejects bypasses of component code and local-only behavior', () => {
  for (const code of [
    good.replace('useState', 'useEffect'),
    good.replace('<Button testId=', '<Button style={{background:"red"}} testId='),
    good.replace('<Stack>', '<Stack {...{style:{opacity:0}}}>'),
    good.replace('<Stack>', '<Stack><button>unapproved</button>'),
    good.replace('<Stack>', '<Stack><style>button{display:none}</style>'),
    good.replace('setDone(true)', "fetch('https://outside.invalid')"),
    good.replace('setDone(true)', "document.body.innerHTML='changed'"),
    good.replace('setDone(true)', "globalThis['fetch']('https://outside.invalid')"),
    good.replace('setDone(true)', "import('https://outside.invalid/module.js')"),
    good.replace('setDone(true)', "Function('return 1')()"),
    good.replace('setDone(true)', "amount['constructor']('return 1')()"),
    good.replace("from 'react'", "from 'other-package'"),
    good.replace('<Stack>', '<Stack dangerouslySetInnerHTML={{__html:"fake"}}>'),
    good.replace('pageId="entry"', 'pageId={amount}'),
    good.replace('export default function App()', "const Raw='button'; export default function App()").replace('<Button testId=', '<Raw testId='),
    good.replace('setDone(true)', "Screen({pageId:'changed',children:'fake'})"),
    good + "\ntype Outside = import('/tmp/not-allowed').Value;",
    "/// <reference path='/tmp/not-allowed.ts'/>\n" + good,
  ]) {
    assert.equal(inspectSources({ 'src/App.tsx': code }, catalog).ok, false, code);
  }
});

test('supports local page/logic files but rejects imports escaping generated source', () => {
  const files = {
    'src/App.tsx': "import Entry from './pages/entry'; export default function App(){ return <Entry/>; }",
    'src/pages/entry.tsx': good,
    'src/logic/amount.ts': 'export function amount(value:string){return Number(value);}',
  };
  assert.equal(inspectSources(files, catalog).ok, true);
  files['src/App.tsx'] = files['src/App.tsx'].replace('./pages/entry', '../approved-ui/index');
  assert.equal(inspectSources(files, catalog).ok, false);
});

test('source text is not code and duplicate page identities are rejected', () => {
  const text = good.replace('신청', 'window.fetch guide; style instruction');
  assert.equal(inspectSources({ 'src/App.tsx': text }, catalog).ok, true);
  const result = inspectSources({ 'src/App.tsx': good, 'src/pages/entry.tsx': good }, catalog);
  assert.equal(result.ok, false);
  assert(result.diagnostics.some(value => value.includes('pageId')));
});

test('malformed generated syntax is a failed gate rather than an exception', () => {
  for (const code of ['export default function App(){ let x = foo[]; return <Screen', 'import { from;', 'export * from;']) {
    assert.equal(inspectSources({ 'src/App.tsx': code }, catalog).ok, false);
  }
});
