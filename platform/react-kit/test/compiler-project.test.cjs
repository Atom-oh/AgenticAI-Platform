const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { hashFiles, zip } = require('../project.cjs');

test('file-set identity is order independent and changes with bytes or paths', () => {
  assert.equal(hashFiles({ b: 'second', a: 'first' }), hashFiles({ a: 'first', b: 'second' }));
  assert.notEqual(hashFiles({ a: 'first' }), hashFiles({ a: 'changed' }));
  assert.notEqual(hashFiles({ a: 'first' }), hashFiles({ b: 'first' }));
});

test('deterministic ZIP round-trips through an independent standard decoder', () => {
  const files = { 'src/App.tsx': 'export default function App(){ return "신청"; }', 'assets/pixel.bin': Buffer.from([0, 255, 9, 128]) };
  const archive = zip(files);
  assert.deepEqual(archive, zip({ 'assets/pixel.bin': files['assets/pixel.bin'], 'src/App.tsx': files['src/App.tsx'] }));
  const output = execFileSync('python3', ['-c', [
    'import io,json,sys,zipfile,base64',
    'z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))',
    'assert z.testzip() is None',
    'assert all(v.date_time==(1980,1,1,0,0,0) for v in z.infolist())',
    'print(json.dumps({n:base64.b64encode(z.read(n)).decode() for n in z.namelist()}))',
  ].join('\n')], { input: archive });
  const extracted = JSON.parse(output);
  for (const [name, value] of Object.entries(files)) assert.deepEqual(Buffer.from(extracted[name], 'base64'), Buffer.from(value));
});

test('export cannot write parent or absolute paths', () => {
  for (const name of ['../bad', '/tmp/bad', 'src/../bad', 'src\\bad', 'src//bad']) {
    assert.throws(() => zip({ [name]: 'bad' }), /Unsafe export path/);
  }
});
