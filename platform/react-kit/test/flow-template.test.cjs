const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');
const { buildProject } = require('../compile.cjs');
const { catalog } = require('../manifest.cjs');

// Engine plan Task E12b (review round 29, AW1): the exported runner treats an absent target as satisfying
// `expectVisible false`, like the Studio verifier (workspace/browser.py), so conditionally unmounted nodes pass.
const body = {
  absent: `{false && <Text testId="cond">조건부 안내</Text>}`,
  hidden: `<Text testId="cond">{''}</Text>`,
  visible: `<Text testId="cond">잘못 보이는 안내</Text>`,
  duplicate: `<Text testId="cond">{''}</Text><Text testId="cond">{''}</Text>`,
};
const app = mode => `import {Screen,Stack,Text} from '@studio/approved-ui';
export default function App(){
 return <Screen pageId="entry" testId="entry" title="조건부 표시"><Stack><Text>항상 보이는 문장</Text>${body[mode]}</Stack></Screen>;
}`;
const contract = { viewport: { width: 390, height: 844 }, rules: [{
  id: 'R1', title: '조건부 요소 숨김', required: true, steps: [
    { action: 'expectVisible', target: 'entry', value: true },
    { action: 'expectVisible', target: 'cond', value: false },
  ],
}] };

async function exported(mode) {
  const built = await buildProject({ files: { 'src/App.tsx': app(mode) }, assets: {}, expectedCatalogHash: catalog().hash, contract });
  assert.equal(built.ok, true, JSON.stringify(built.diagnostics));
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-flow-template-'));
  execFileSync('python3', ['-c', 'import io,sys,zipfile;zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall(sys.argv[1])', directory],
    { input: Buffer.from(built.projectZipBase64, 'base64') });
  fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(directory, 'node_modules'), 'dir');
  execFileSync(process.execPath, ['compile.cjs', '--build'], { cwd: directory, timeout: 30000 });
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  const run = spawnSync('npm', ['test'], { cwd: directory, env, timeout: 60000, encoding: 'utf8' });
  fs.rmSync(directory, { recursive: true, force: true });
  return run;
}

for (const [mode, passes] of [['absent', true], ['hidden', true], ['visible', false], ['duplicate', false]]) {
  test(`exported npm test: ${mode} conditional node ${passes ? 'passes' : 'fails'} expectVisible false`, { timeout: 120_000 }, async () => {
    const run = await exported(mode);
    if (passes) assert.equal(run.status, 0, run.stdout + run.stderr);
    else {
      assert.notEqual(run.status, 0, run.stdout);
      if (mode === 'duplicate') assert.match(run.stdout + run.stderr, /Unique target required: cond/);
    }
  });
}
