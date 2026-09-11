'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const { catalog } = require('../manifest.cjs');
const { hashFiles } = require('../project.cjs');

test('three sample source exports rebuild exactly and pass their real browser contracts', { timeout: 240000 }, async () => {
  const builderPath = path.resolve(__dirname, '../build-samples.cjs');
  assert(fs.existsSync(builderPath), 'The sample exporter must exist');
  const { buildSamples } = require(builderPath);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-samples-test-'));
  const childEnv = { ...process.env };
  delete childEnv.NODE_TEST_CONTEXT;
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true, args: ['--no-sandbox'] });
  try {
    const manifest = await buildSamples(path.join(directory, 'public'));
    assert.deepEqual(manifest.samples.map(sample => sample.id), ['amount-review', 'required-consent', 'product-compare']);
    assert.equal(manifest.catalog.hash, catalog().hash);
    for (const sample of manifest.samples) {
      // Use the real intake parser: imported previews intentionally disable scripts.
      const expectedText = { 'amount-review': '월 납입금액', 'required-consent': '이용 조건', 'product-compare': '차곡차곡 기본형' }[sample.id];
      const imported = execFileSync('python3', ['-c',
        'from workspace.intake import extract_file; import sys; from pathlib import Path; r=extract_file("sample.html",Path(sys.argv[1]).read_bytes()); assert sys.argv[2] in r["text"],r["text"]; sys.stdout.buffer.write(r["previews"][0]["data"])',
        path.join(directory, 'public', sample.preview), expectedText], { cwd: path.resolve(__dirname, '../..'), maxBuffer: 4 * 1024 * 1024 });
      const context = await browser.newContext({ javaScriptEnabled: false });
      try {
        await context.route('**/*', route => route.fulfill({ contentType: 'text/html', body: imported }));
        const page = await context.newPage();
        await page.goto('https://sample-import.invalid/');
        assert((await page.getByRole('main').innerText()).includes(expectedText), 'Uploaded HTML must show actual UI without scripts');
      } finally { await context.close(); }
      const exported = path.join(directory, sample.id);
      fs.mkdirSync(exported);
      const archive = fs.readFileSync(path.join(directory, 'public', sample.source));
      execFileSync('python3', ['-c',
        'import io,sys,zipfile;zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall(sys.argv[1])', exported], { input: archive });
      const files = {};
      function collect(base, output, root) {
        for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
          const absolute = path.join(base, entry.name);
          if (entry.isDirectory()) collect(absolute, output, root);
          else output[path.relative(root, absolute).split(path.sep).join('/')] = fs.readFileSync(absolute);
        }
      }
      collect(exported, files, exported);
      assert.equal(hashFiles(files), sample.sourceHash);
      assert.equal(JSON.parse(files['studio.lock.json']).catalogHash, manifest.catalog.hash);
      assert(sample.checks.length >= 3, 'Each sample must explain its behavior checks');
      assert(fs.existsSync(path.join(directory, 'public', sample.preview)));
      assert.match(fs.readFileSync(path.join(directory, 'public', sample.guide), 'utf8'), /샘플/);
      const contract = JSON.parse(fs.readFileSync(path.join(directory, 'public', sample.contract), 'utf8'));
      assert(contract.rules.length >= 3);
      assert(contract.rules.every(rule => rule.required && rule.steps.some(step => step.action.startsWith('expect'))));
      fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(exported, 'node_modules'), 'dir');
      execFileSync(process.execPath, ['compile.cjs', '--check'], { cwd: exported, timeout: 30000 });
      execFileSync(process.execPath, ['compile.cjs', '--build'], { cwd: exported, timeout: 30000 });
      const dist = {};
      collect(path.join(exported, 'dist'), dist, path.join(exported, 'dist'));
      assert.equal(hashFiles(dist), sample.bundleHash, 'Downloaded source must rebuild to the previewed sample');
      const output = execFileSync(process.execPath, ['--test', 'test/flow.test.cjs'],
        { cwd: exported, env: childEnv, timeout: 60000, stdio: 'pipe' }).toString();
      assert.match(output, /# fail 0/);
    }
  } finally { await browser.close(); fs.rmSync(directory, { recursive: true, force: true }); }
});
