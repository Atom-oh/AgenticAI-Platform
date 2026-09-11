const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { buildSync } = require('esbuild');

// Exercise the shipped TypeScript with the project's existing build toolchain.
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-report-test-'));
const output = path.join(dir, 'reports.cjs');
buildSync({
  entryPoints: [path.join(__dirname, '../src/studio/reports.ts')],
  bundle: true, platform: 'node', format: 'cjs', outfile: output,
});
const { loadRoundReport } = require(output);
after(() => fs.rmSync(dir, { recursive: true, force: true }));

const report = {
  round: 2, itemsComplete: true,
  items: [{ id: 'REQ', verdict: 'fail', evidence: '금액🔒\n"'.repeat(20_000), fix: '값을 전달하세요.' }],
};
const serialized = JSON.stringify(report);

test('large Unicode report is restored only after every chunk arrives', async () => {
  let calls = 0;
  const result = await loadRoundReport('job', 2, async (action, body) => {
    assert.equal(action, 'studio_round');
    assert.equal(body.jobId, 'job');
    assert.equal(body.round, 2);
    assert.equal(body.reportVersion, calls ? 'v1' : undefined);
    calls++;
    const end = Math.min(serialized.length, body.offset + 10_007);
    return { type: action, chunk: serialized.slice(body.offset, end), offset: body.offset,
      nextOffset: end < serialized.length ? end : null, reportVersion: 'v1' };
  });
  assert.ok(calls > 2);
  assert.deepEqual(result, report);
});

test('existing unchunked and historical responses remain readable', async () => {
  assert.deepEqual(await loadRoundReport('job', 2, async () => ({ type: 'studio_round', round: report })), report);
  const legacy = { round: 2, itemsComplete: false, items: [], failures: [{ id: 'OLD', verdict: null }] };
  const result = await loadRoundReport('job', 2, async () => ({ type: 'studio_round', round: legacy }));
  assert.equal(result.itemsComplete, false);
  assert.deepEqual(result.items, legacy.failures);
});

test('changed versions, wrong offsets and partial transfer errors reject the entire report', async () => {
  for (const invalid of [
    { offset: 99, nextOffset: 200, reportVersion: 'v1', chunk: 'x' },
    { offset: 100, nextOffset: 200, reportVersion: 'v2', chunk: 'x' },
    { offset: 100, nextOffset: 100, reportVersion: 'v1', chunk: 'x' },
    { offset: 100, nextOffset: 200, reportVersion: 'v1', chunk: '' },
    { error: 'report changed' },
  ]) {
    let calls = 0;
    await assert.rejects(loadRoundReport('job', 2, async () => {
      if (calls++) return { type: 'studio_round', ...invalid };
      return { type: 'studio_round', offset: 0, nextOffset: 100, reportVersion: 'v1', chunk: serialized.slice(0, 100) };
    }));
  }
});

test('a response for another round or missing report evidence is not shown', async () => {
  for (const invalid of [
    { ...report, round: 1 },
    { round: 2, itemsComplete: true },
    { round: 2, items: report.items },
  ]) {
    await assert.rejects(loadRoundReport('job', 2, async () => ({ type: 'studio_round', round: invalid })));
  }
});

test('parallel report reads keep their version and content separate', async () => {
  const read = jobId => {
    const text = JSON.stringify({ round: 2, itemsComplete: true, items: [{ id: jobId, verdict: 'pass' }] });
    return loadRoundReport(jobId, 2, async (_, body) => {
      await Promise.resolve();
      const end = Math.min(text.length, body.offset + 13);
      return { type: 'studio_round', offset: body.offset, chunk: text.slice(body.offset, end),
        nextOffset: end === text.length ? null : end, reportVersion: jobId };
    });
  };
  const [a, b] = await Promise.all([read('a'), read('b')]);
  assert.equal(a.items[0].id, 'a');
  assert.equal(b.items[0].id, 'b');
});
