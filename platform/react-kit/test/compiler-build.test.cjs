const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { buildProject } = require('../compile.cjs');
const { catalog } = require('../manifest.cjs');
const { hashFiles } = require('../project.cjs');

const app = `import {useState} from 'react';
import {Screen,Stack,Text,Input,Checkbox,Button} from '@studio/approved-ui';
export default function App(){
 const [amount,setAmount]=useState('300000'),[agreed,setAgreed]=useState(false),[done,setDone]=useState(false);
 return <Screen pageId="entry"><Stack><Text as="h1">모의 신청</Text>
 {done ? <Text testId="summary">{Number(amount).toLocaleString('ko-KR')}원</Text> :
 <><Input testId="amount" label="금액" value={amount} onChange={setAmount}/>
 <Checkbox testId="agree" label="필수 동의" checked={agreed} onChange={setAgreed}/></>}
 <Button testId="next" kind="primary" label="다음" disabled={!agreed} onClick={()=>setDone(true)}/>
 </Stack></Screen>;
}`;
const contract = { viewport: { width: 390, height: 844 }, rules: [{
  id: 'R1', title: '동의와 금액 전달', required: true, steps: [
    { action: 'expectEnabled', target: 'next', value: false },
    { action: 'fill', target: 'amount', value: '10000' },
    { action: 'check', target: 'agree', value: true },
    { action: 'click', target: 'next' },
    { action: 'expectText', target: 'summary', value: '10,000원', match: 'equals' },
  ],
}] };

test('real component types and production bundle are checked, with reproducible hashes', async () => {
  const input = { files: { 'src/App.tsx': app }, assets: {}, expectedCatalogHash: catalog().hash, contract };
  const first = await buildProject(input);
  assert.equal(first.ok, true, JSON.stringify(first.diagnostics));
  assert.equal(first.gates.types.status, 'pass');
  assert.equal(first.gates.build.status, 'pass');
  const bytes = Object.fromEntries(Object.entries(first.files).map(([name, value]) => [name, Buffer.from(value, 'base64')]));
  assert.equal(hashFiles(bytes), first.bundleHash);
  assert(bytes['assets/app.js'].length > 10000, 'actual React runtime must be bundled');
  assert.match(bytes['assets/app.css'].toString(), /008485/i);
  const second = await buildProject(input);
  assert.equal(first.bundleHash, second.bundleHash);
  assert.equal(first.sourceHash, second.sourceHash);
  assert.equal(first.distZipBase64, second.distZipBase64);
});

test('wrong real props and changed component locks are blocking failures', async () => {
  const input = { files: { 'src/App.tsx': app.replace('kind="primary"', 'kind="ultraviolet"') },
    assets: {}, expectedCatalogHash: catalog().hash, contract };
  const wrong = await buildProject(input);
  assert.equal(wrong.ok, false);
  assert.equal(wrong.gates.types.status, 'fail');
  const changed = await buildProject({ ...input, files: { 'src/App.tsx': app }, expectedCatalogHash: '0'.repeat(64) });
  assert.equal(changed.ok, false);
  assert.equal(changed.gates.components.status, 'fail');
  assert.equal(changed.projectZipBase64, undefined);
});

test('exported source rebuilds to the exact tested dist and rejects kit modification', async () => {
  const built = await buildProject({ files: { 'src/App.tsx': app }, assets: {}, expectedCatalogHash: catalog().hash, contract });
  assert.equal(built.ok, true, JSON.stringify(built.diagnostics));
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-export-check-'));
  try {
    execFileSync('python3', ['-c', 'import io,sys,zipfile;zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall(sys.argv[1])', directory],
      { input: Buffer.from(built.projectZipBase64, 'base64') });
    fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(directory, 'node_modules'), 'dir');
    execFileSync(process.execPath, ['compile.cjs', '--build'], { cwd: directory, timeout: 30000 });
    const dist = {};
    function collect(base) {
      for (const name of fs.readdirSync(base)) {
        const absolute = path.join(base, name);
        if (fs.statSync(absolute).isDirectory()) collect(absolute);
        else dist[path.relative(path.join(directory, 'dist'), absolute).split(path.sep).join('/')] = fs.readFileSync(absolute);
      }
    }
    collect(path.join(directory, 'dist'));
    assert.equal(hashFiles(dist), built.bundleHash);
    fs.appendFileSync(path.join(directory, 'ui/tokens.css'), '\nbutton{background:red}\n');
    assert.throws(() => execFileSync(process.execPath, ['compile.cjs', '--build'], { cwd: directory, timeout: 30000, stdio: 'pipe' }));
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('exported React project runs actual browser rules and rejects broken value propagation', { timeout: 60000 }, async () => {
  const built = await buildProject({ files: { 'src/App.tsx': app }, assets: {}, expectedCatalogHash: catalog().hash, contract });
  assert.equal(built.ok, true);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-react-flow-'));
  const childEnv = { ...process.env };
  delete childEnv.NODE_TEST_CONTEXT;
  try {
    execFileSync('python3', ['-c', 'import io,sys,zipfile;zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall(sys.argv[1])', directory],
      { input: Buffer.from(built.projectZipBase64, 'base64') });
    fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(directory, 'node_modules'), 'dir');
    execFileSync(process.execPath, ['compile.cjs', '--build'], { cwd: directory, timeout: 30000 });
    const passed = execFileSync(process.execPath, ['--test', 'test/flow.test.cjs'], { cwd: directory, env: childEnv, timeout: 25000 }).toString();
    assert.match(passed, /# fail 0/);
    fs.writeFileSync(path.join(directory, 'src/App.tsx'), app.replace('Number(amount).toLocaleString', 'Number(300000).toLocaleString'));
    execFileSync(process.execPath, ['compile.cjs', '--build'], { cwd: directory, timeout: 30000 });
    assert.throws(() => execFileSync(process.execPath, ['--test', 'test/flow.test.cjs'],
      { cwd: directory, env: childEnv, timeout: 25000, stdio: 'pipe' }));
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
