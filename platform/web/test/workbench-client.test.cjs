const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { buildSync } = require('esbuild');

const root = path.resolve(__dirname, '..');
const result = buildSync({
  entryPoints: [path.join(root, 'src/workbench/client.ts')], bundle: true, write: false,
  format: 'cjs', platform: 'node', external: ['react'],
});
const compiled = { exports: {} };
new Function('module', 'exports', 'require', result.outputFiles[0].text)(compiled, compiled.exports, require);
const { contextHash, readContext, listWorkbench, waitForJob, createWorkbenchClient, validateSkill, executeApprovedSkill, readSkillExecution } = compiled.exports;

test('navigation keeps opaque context and clears scoped targets only when changing projects', () => {
  const current = '#/changes?projectId=p-a&productId=prod&changeId=c&targetId=icon%3A1&trace=a%2Fb%3D';
  assert.equal(readContext(current).get('targetId'), 'icon:1');
  const next = new URL(contextHash('development', { impactHash: 'hash' }, current), 'https://example.test');
  const query = readContext(next.hash);
  assert.equal(query.get('trace'), 'a/b=');
  assert.equal(query.get('productId'), 'prod');
  assert.equal(query.get('impactHash'), 'hash');
  const changed = readContext(contextHash('planning', { projectId: 'p-b' }, next.hash));
  assert.equal(changed.get('projectId'), 'p-b');
  assert.equal(changed.get('productId'), null);
  assert.equal(changed.get('targetId'), null);
  assert.equal(changed.get('trace'), 'a/b=');
});

test('client reuses authenticated project transport and paginates actual items', async () => {
  const calls = [];
  const client = createWorkbenchClient('p-a', {
    token: () => 'fixture', fetcher: async (url, options) => {
      calls.push([url, options]);
      return new Response(JSON.stringify(url.includes('cursor=') ? { items: [{ id: 'b' }] } :
        { items: [{ id: 'a' }], cursor: 'opaque/next' }), { status: 200 });
    },
  });
  assert.deepEqual(await listWorkbench(client, '/knowledge?q=규정'), [{ id: 'a' }, { id: 'b' }]);
  assert.equal(calls[0][0], '/studio-api/workbench/knowledge?q=규정');
  assert.match(calls[1][0], /&cursor=opaque%2Fnext$/);
  assert.equal(calls[0][1].headers.get('X-Workspace-Project'), 'p-a');
  assert.equal(calls[0][1].headers.get('Authorization'), 'Bearer fixture');
  await assert.rejects(() => listWorkbench({ get: async () => ({ items: [], cursor: 'repeat' }) }, '/tasks'), /페이지/);
});

test('job completion, failure and cancellation never manufacture successful results', async () => {
  let index = 0;
  const updates = [];
  const client = { get: async () => ({ job: { id: 'job', status: index++ ? 'completed' : 'running', result: { skillId: 's1' } } }) };
  assert.equal((await waitForJob(client, 'job', { interval: 1, onUpdate: job => updates.push(job.status) })).result.skillId, 's1');
  assert.deepEqual(updates, ['running', 'completed']);
  await assert.rejects(() => waitForJob({ get: async () => ({ job: { id: 'job', status: 'failed', error: '검증 차단' } }) }, 'job'), /검증 차단/);
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(() => waitForJob(client, 'job', { signal: abort.signal }), { name: 'AbortError' });
});

test('async Skill validation waits for its job and reloads the exact persisted content', async () => {
  const skill = { id: 's1', version: 1, contentHash: 'a'.repeat(64) };
  const calls = [];
  let finish;
  const completed = new Promise(resolve => { finish = resolve; });
  const client = {
    post: async (path, body) => {
      calls.push([path, body]);
      return { skill: { ...skill, version: 2, status: 'VALIDATING' }, job: { id: 'validation-job', status: 'queued' } };
    },
    get: async path => {
      calls.push([path]);
      if (path === '/jobs/validation-job') { await completed; return { job: { id: 'validation-job', status: 'completed' } }; }
      return { skill: { ...skill, version: 3, status: 'PENDING_APPROVAL', validation: {
        contentHash: skill.contentHash, package: { status: 'passed' }, behavior: { status: 'failed' },
      } } };
    },
  };
  let resolved = false;
  const pending = validateSkill(client, skill).then(value => { resolved = true; return value; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(resolved, false, 'A queued receipt is not a saved validation result');
  assert(!calls.some(([path]) => path === '/workbench/skills/s1'));
  finish();
  const result = await pending;
  assert.equal(result.version, 3);
  assert.equal(result.validation.behavior.status, 'failed', 'Completed evaluation does not imply a pass');
  assert.deepEqual(calls[0], ['/workbench/skills/s1/validate', { version: 1 }]);
  assert.equal(calls.at(-1)[0], '/workbench/skills/s1');
});

test('approved Skill execution pins version/hash, polls, and reads the permission-checked result endpoint', async () => {
  const skill = { id: 's1', version: 4, contentHash: 'b'.repeat(64), status: 'APPROVED' };
  const artifact = { id: 'e1', skillId: skill.id, skillVersion: skill.version, contentHash: skill.contentHash, jobId: 'execution-job', status: 'completed' };
  const calls = [];
  const client = {
    post: async (path, body) => { calls.push([path, body]); return { artifact: { ...artifact, status: 'queued' }, job: { id: 'execution-job', status: 'queued' } }; },
    get: async path => {
      calls.push([path]);
      if (path === '/jobs/execution-job') return { job: { id: 'execution-job', status: 'completed', result: { artifactId: 'e1', answer: 'Never display a job payload as an answer' } } };
      return { artifact, result: { status: 'answered', answer: 'Source-authorized result', evidenceIds: ['e1'] } };
    },
  };
  const output = await executeApprovedSkill(client, skill, '검토', 'request-1');
  assert.equal(output.result.answer, 'Source-authorized result');
  assert.deepEqual(calls[0], ['/workbench/skills/s1/execute', { version: 4, contentHash: skill.contentHash, input: '검토', requestId: 'request-1' }]);
  assert.equal(calls.at(-1)[0], '/workbench/skills/s1/executions/e1');
  await assert.rejects(() => readSkillExecution({ get: async () => { throw new Error('원본 권한 회수'); } }, skill, 'e1'), /권한 회수/);
  await assert.rejects(() => readSkillExecution({ get: async () => ({ artifact: { ...artifact, contentHash: 'c'.repeat(64) }, result: output.result }) }, skill, 'e1'), /버전/);
});
