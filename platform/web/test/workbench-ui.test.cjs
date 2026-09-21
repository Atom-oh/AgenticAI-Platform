const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { build } = require('esbuild');
const { chromium } = require('playwright');

// Browser plugin not available. All requests below are intercepted API fixtures;
// this suite neither submits model requests nor connects to external services.
const root = path.resolve(__dirname, '..');
const views = ['planning', 'changes', 'deliverables', 'components', 'development', 'knowledge', 'ontology', 'skills', 'pension', 'reports', 'sources', 'batches', 'tools', 'operations'];
const hash = 'a'.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
const ref = { sourceId: 'source-1', documentId: 'doc-1', revision: 'synthetic-v1', contentHash: hash, generation: 'generation-1' };
const persona = { id: 'starter', title: '차근차근 준비하는 직장인', ageBand: '30대', stage: '적립 초기',
  description: '가상 연금 사례', accounts: [{ id: 'dc', name: '합성 퇴직연금 DC', type: 'DC', balance: 25000000 }] };
const topics = { diagnosis: '현황 진단', planning: '노후 설계', contribution: '적립 계획', operation: '운용 가정', withdrawal: '수령 시나리오' };
const questions = Object.fromEntries(Object.keys(topics).map(key => [key, `${topics[key]} 기준을 알려주세요.`]));
const feedbackComments = { clear: '계산 가정과 근거가 명확합니다.', 'needs-evidence': '근거 설명을 보완해야 합니다.',
  'needs-clarity': '설명을 더 이해하기 쉽게 보완해야 합니다.', incorrect: '계산 또는 답변의 재검토가 필요합니다.' };
const assumptions = { yearsToRetirement: 25, monthlyContribution: 300000, monthlyTarget: 2000000, withdrawalYears: 20, annualReturnBps: 0 };
const facts = { totalBalance: 25000000, projectedBalance: 115000000, monthlyPension: 479167, monthlyTarget: 2000000,
  monthlyGap: 1520833, monthlySurplus: 0, totalContributions: 90000000, assumptions, calculationVersion: 'illustrative-pension-v1',
  caveats: ['합성 자료와 명시한 가정의 검증용 계산입니다.'] };

async function harness() {
  const bundle = await build({
    stdin: { contents: `import React,{useState,useEffect} from 'react';import {createRoot} from 'react-dom/client';
      import Workbench from './src/workbench/Workbench';
      const views=${JSON.stringify(views)};
      function Harness(){const[view,setView]=useState(location.hash.split('?')[0].replace('#/wb-','')||'planning');
        useEffect(()=>{const f=()=>setView(location.hash.split('?')[0].replace('#/wb-',''));addEventListener('hashchange',f);return()=>removeEventListener('hashchange',f)},[]);
        return <><nav aria-label="테스트 작업 메뉴">{views.map(v=><button key={v} onClick={()=>{location.hash='#/wb-'+v+location.hash.slice(location.hash.indexOf('?'));}}>{v}</button>)}</nav>
        <main><Workbench view={view} onNavigate={route=>{window.navigations.push(route);location.hash='#/'+route}}
          onOverview={value=>window.overviews.push(value)}/></main></>}
      window.navigations=[];window.overviews=[];const app=createRoot(document.getElementById('root'));app.render(<Harness/>);window.dispose=()=>app.unmount();`,
      resolveDir: root, loader: 'tsx' },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{ name: 'fixture-auth', setup(builder) {
      builder.onResolve({ filter: /(^|\/)lib$/ }, () => ({ path: 'lib', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const auth={token:"fixture-token"};' }));
    } }],
  });
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, offline: true });
  await context.routeWebSocket('**/*', socket => socket.close());
  const fixture = {
    projects: Object.fromEntries(['a', 'b', 'c', 'revoked'].map((id, index) => [id, { id, name: `프로젝트 ${id.toUpperCase()}`, version: 1,
      members: { actor: { role: ['owner', 'developer', 'planner', 'owner'][index], displayName: '가상 참여자' } } }])),
    data: {}, calls: [], errors: [], external: [], held: null, holdKnowledge: false, denyWrite: false,
    failProducts: false, behaviorPass: false, jobPolls: {}, failJob: false,
    asyncValidation: false, holdJobs: false, denyExecutionRead: false,
    feedbackFreeTextAvailable: false, privacyUnavailable: false,
  };
  const scopeData = scope => fixture.data[scope] ||= { products: [], changes: [], tasks: [], skills: [], sources: [], batches: [], tools: [], reports: [],
    knowledge: [{ id: `doc-${scope}`, title: `${scope.toUpperCase()} 연금 업무 가이드`, kind: 'guide', revision: 'synthetic-v1',
      snippet: '가상 출금 조건과 안내를 확인하세요.', content: `${scope.toUpperCase()} 프로젝트의 원본 안내입니다.`, sourceRef: ref }],
    graph: { nodes: [{ id: 'example-icon', label: 'Icon', title: '가상 안내 아이콘', version: '1', provenance: 'synthetic-fixture', sourceRef: ref },
      { id: 'example-screen', label: 'Screen', title: '출금 확인 화면', version: '1', provenance: 'synthetic-fixture', sourceRef: ref },
      { id: 'example-api', label: 'API', title: '출금 신청 API', version: '1', provenance: 'synthetic-fixture', sourceRef: ref }],
    edges: [{ src: 'example-screen', rel: 'USES', dst: 'example-icon', provenance: 'synthetic-fixture', sourceRef: ref }],
    generation: 'generation-1', coverage: { complete: false, unknown: ['unmapped-dependencies'] } },
    ontology: { generation: hash, backend: 'workspace-project-ontology', cursor: null,
      coverage: { complete: false, unknown: ['outside-snapshot-not-certified'] },
      nodes: [
        { id: 'icon', type: 'Foundation', title: `${scope.toUpperCase()} 이미지`, revision: 1, reviewState: 'candidate', provenance: 'declared',
          sourceRefs: [{ sourceKind: 'asset', sourceId: 'image', revision: '1', sha256: hash, audienceRevision: '1' }] },
        { id: 'screen', type: 'Screen', title: `${scope.toUpperCase()} 화면`, revision: 1, reviewState: 'candidate', provenance: 'declared',
          sourceRefs: [{ sourceKind: 'asset', sourceId: 'screen-file', revision: '1', sha256: hash, audienceRevision: '1' }] }],
      edges: [{ id: 'edge', type: 'USES', src: { id: 'screen' }, dst: { id: 'icon' }, sourceRefs: [], provenance: 'declared' }] },
    sessions: {}, jobs: {}, executions: {},
  };
  const catalog = { ...JSON.parse(fs.readFileSync(path.join(root, '../react-kit/catalog.json'), 'utf8')), hash };
  const css = ['workspace/workspace.css', 'workbench/workbench.css'].map(file => fs.readFileSync(path.join(root, 'src', file), 'utf8')).join('\n');
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'workbench.test') { fixture.external.push(url.href); return route.abort(); }
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body:
      `<html lang="ko"><head><meta charset="utf-8"><title>Workbench fixture</title><style>body{margin:0;background:#f4f7f6}main{max-width:1320px;margin:auto;padding:24px}nav{padding:12px;display:flex;gap:6px;flex-wrap:wrap} ${css}</style></head><body><div id="root"></div><script src="/app.js"></script></body></html>` });
    if (url.pathname === '/app.js') return route.fulfill({ contentType: 'application/javascript; charset=utf-8', body: bundle.outputFiles[0].text });
    const request = route.request(), method = request.method(), target = url.pathname.replace('/studio-api', '');
    const scope = request.headers()['x-workspace-project'] || '';
    const body = method === 'GET' ? null : request.postDataJSON();
    fixture.calls.push({ scope, target, method, body, query: Object.fromEntries(url.searchParams) });
    try {
      assert.equal(request.headers().authorization, 'Bearer fixture-token');
      if (target === '/projects' && method === 'GET') return json({ projects: Object.values(fixture.projects).map(project =>
        ({ ...project, members: { actor: { role: 'owner', displayName: 'Stale index is not authority' } } })) });
      if (target === '/projects' && method === 'POST') {
        assert.equal(scope, '');
        const project = { id: 'new-project', name: body.name, version: 1, members: { actor: { role: 'owner', displayName: '가상 참여자' } } };
        fixture.projects[project.id] = project; return json({ project }, 201);
      }
      if (target.startsWith('/projects/')) {
        if (target.endsWith('/revoked')) return json({ code: 'forbidden', error: '프로젝트 참여 권한이 회수되었습니다.' }, 403);
        return json({ project: fixture.projects[target.split('/')[2]] });
      }
      if (scope === 'revoked') return json({ code: 'forbidden', error: '프로젝트 참여 권한이 회수되었습니다.' }, 403);
      assert(scope, 'Workbench resource requests must have a project');
      const data = scopeData(scope);
      if (target === '/workbench/overview') return json({ actorId: 'actor', role: fixture.projects[scope].members.actor.role,
        operator: scope === 'a', capabilities: { author: true, registerTools: scope === 'a', examples: scope !== 'b' },
        stats: { sources: data.sources.length, changes: data.changes.length, tasks: data.tasks.length, skills: data.skills.length },
        tasks: data.tasks, recentArtifacts: [], readiness: { model: { status: 'not-configured' }, knowledge: { status: 'available', backend: 'private-artifact' } } });
      if (fixture.denyWrite && method !== 'GET') return json({ code: 'operator-required', error: '서버에서 운영 권한을 거부했습니다.' }, 403);
      if (target === '/workbench/examples') {
        if (!data.products.some(item => item.id === 'example-product')) data.products.push({ id: 'example-product', title: '가상 모아저축',
          projectId: scope, version: 1, description: '프로젝트 전용 합성 예제', conditions: [], steps: [], notices: [] });
        return json({ productId: 'example-product', sourceId: 'source-1', description: '합성 자료' }, 201);
      }
      if (target === '/products' && method === 'GET') return fixture.failProducts ?
        json({ code: 'unavailable', error: '상품 조회 연결이 준비되지 않았습니다.' }, 503) : json({ products: data.products });
      if (target === '/products' && method === 'POST') {
        const product = { ...body, id: 'product-created', projectId: scope, version: 1 };
        data.products.push(product); return json({ product }, 201);
      }
      if (target.startsWith('/products/')) {
        const product = data.products.find(item => item.id === target.split('/')[2]);
        if (target.endsWith('/ontology')) return json({ ontology: { productId: product.id, guidelineId: product.publishedGuidelineId,
          revision: 1, hash, nodes: [], edges: [] } });
        if (target.endsWith('/impact')) return json({ affectedRuns: [] });
        if (target.endsWith('/publish')) {
          assert.equal(body.version, product.version); Object.assign(product, { version: product.version + 1, publishedRevision: 1, publishedGuidelineId: 'guide-1', ontologyHash: hash });
        } else if (method === 'PUT') { assert.equal(body.version, product.version); Object.assign(product, body, { version: product.version + 1 }); }
        return json({ product });
      }
      if (['/runs', '/assets', '/releases'].includes(target)) return json({ [target.slice(1)]: [] });
      if (target === '/components') return json({ catalog });
      if (target === '/ontology') return json(data.ontology);
      if (target === '/ontology/schema') return json({ analyzerConfigured: false });
      if (target === '/ontology/impact') {
        const response = { generation: data.ontology.generation, coverage: { complete: false }, items: [
          { nodeId: body.nodeIds[0], title: '선택한 항목의 영향', type: 'Screen', evidenceKind: 'candidate', witnessPath: body.nodeIds }] };
        if (fixture.holdOntology) { fixture.heldOntology = () => json(response).catch(() => {}); return; }
        return json(response);
      }
      if (/^\/ontology\/nodes\/[^/]+\/review$/.test(target)) {
        const node = data.ontology.nodes.find(item => item.id === target.split('/')[3]);
        assert.equal(body.revision, node.revision); assert.equal(body.expectedGeneration, data.ontology.generation);
        assert.equal(Object.hasOwn(body, 'actor'), false);
        node.revision++; node.reviewState = body.decision; data.ontology.generation = 'b'.repeat(64);
        return json({ node, generation: data.ontology.generation });
      }
      if (target === '/workbench/dependencies') return json(data.graph);
      if (target === '/workbench/knowledge') {
        const payload = { items: data.knowledge, generation: 'generation-1', coverage: { complete: false }, backend: { vector: 'private-artifact', embedding: 'hashing-v1' } };
        if (fixture.holdKnowledge && scope === 'a') { fixture.holdKnowledge = false; fixture.held = () => json(payload).catch(() => {}); return; }
        return json(payload);
      }
      if (target.startsWith('/workbench/knowledge/')) return json({ document: data.knowledge.find(item => item.id === target.split('/')[3]), evidence: ref, generation: 'generation-1' });
      const segment = target.split('/')[2];
      if (target === '/workbench/changes' && method === 'POST') {
        assert(['update', 'add', 'remove', 'deprecate', 'policy'].includes(body.changeType));
        const change = { ...body, id: 'change-1', version: 1, status: 'draft' }; data.changes.push(change); return json({ change }, 201);
      }
      if (target === '/workbench/changes/change-1/analyze') {
        const change = data.changes[0]; assert.equal(body.version, change.version);
        Object.assign(change, { version: change.version + 1, status: 'analyzed', impactHash: hash });
        data.impact = { impactHash: hash, generation: 'generation-1', coverage: { complete: false, unknown: ['unmapped-dependencies'] },
          items: ['planner', 'designer', 'developer'].map((role, i) => ({ targetId: `target-${i}`, title: `${topics.diagnosis} ${role}`, role,
            reason: '저장된 의존 관계', witnessPath: ['example-icon', `target-${i}`], sourceRevision: 'synthetic-v1', confidence: 'candidate' })) };
        data.tasks = data.impact.items.map((item, i) => ({ ...item, id: `task-${i}`, version: 1, changeId: change.id, impactHash: hash, status: 'open', evidenceRefs: [], sourceRefs: [ref] }));
        return json({ change, impact: data.impact, tasks: data.tasks });
      }
      if (target === '/workbench/changes/change-1/impact') return json({ impact: data.impact });
      if (target.startsWith('/workbench/tasks/') && method === 'PUT') {
        const task = data.tasks.find(item => item.id === target.split('/')[3]); assert.equal(body.version, task.version);
        assert(['open', 'in-progress', 'blocked', 'done'].includes(body.status));
        if (body.status === 'done' && !body.evidenceRefs.length) return json({ code: 'evidence-required', error: '완료에는 실제 원본 근거가 필요합니다.' }, 422);
        Object.assign(task, body, { version: task.version + 1 }); return json({ task });
      }
      if (target === '/workbench/skills/propose') return json({ code: 'model-not-configured', error: '실제 모델이 설정되지 않았습니다.' }, 503);
      if (target === '/workbench/skills' && method === 'POST') {
        const skill = { ...body, id: 'skill-1', version: 1, contentHash: hash, status: 'DRAFT',
          validation: { package: { status: 'not-run' }, behavior: { status: 'not-run' } } };
        data.skills.push(skill); return json({ skill }, 201);
      }
      if (target.startsWith('/workbench/skills/')) {
        const skill = data.skills.find(item => item.id === target.split('/')[3]);
        if (target.includes('/executions/')) {
          if (fixture.denyExecutionRead) return json({ code: 'forbidden', error: '실행 원본 권한이 회수되었습니다.' }, 403);
          return json(data.executions[target.split('/')[5]]);
        }
        if (target.endsWith('/execute')) {
          assert.equal(body.version, skill.version); assert.equal(body.contentHash, skill.contentHash); assert.equal(skill.status, 'APPROVED');
          assert.deepEqual(Object.keys(body).sort(), ['contentHash', 'input', 'requestId', 'version']);
          const artifact = { id: 'execution-1', skillId: skill.id, skillVersion: skill.version, contentHash: skill.contentHash, status: 'queued', jobId: 'skill-execution-job' };
          data.executions[artifact.id] = { artifact, result: null };
          data.jobs['skill-execution-job'] = { id: 'skill-execution-job', kind: 'skill-execution', status: 'queued' };
          return json({ artifact, job: data.jobs['skill-execution-job'] }, 202);
        }
        if (target.endsWith('/validate')) {
          assert.equal(body.version, skill.version);
          Object.assign(skill, { version: skill.version + 1, status: fixture.asyncValidation ? 'VALIDATING' : 'PENDING_APPROVAL',
            validation: { contentHash: hash, package: { status: 'passed' }, behavior: { status: fixture.asyncValidation ? 'queued' : fixture.behaviorPass ? 'passed' : 'not-run' } } });
          if (fixture.asyncValidation) {
            data.jobs['skill-validation-job'] = { id: 'skill-validation-job', kind: 'skill-validation', status: 'queued' };
            return json({ skill, validation: skill.validation, job: data.jobs['skill-validation-job'] }, 202);
          }
        }
        else if (target.endsWith('/approve')) {
          assert.equal(body.version, skill.version); assert.equal(body.contentHash, skill.contentHash);
          if (!fixture.behaviorPass) return json({ code: 'validation-required', error: '실제 행동 평가가 필요합니다.' }, 422);
          Object.assign(skill, { version: skill.version + 1, status: 'APPROVED' });
        } else if (target.endsWith('/package')) return json({ skill, contentHash: skill.contentHash, files: { 'SKILL.md': skill.instructions } });
        else if (method === 'PUT') { assert.equal(body.version, skill.version); Object.assign(skill, body, { version: skill.version + 1, status: 'DRAFT' }); }
        return json({ skill });
      }
      if (target === '/workbench/sources' && method === 'POST') {
        const source = { ...body, id: 'source-1', version: 1, status: 'registered' }; data.sources.push(source); return json({ source }, 201);
      }
      if (target === '/workbench/sources/source-1/batches') {
        assert(Array.isArray(body.documents)); assert(body.documents[0].content);
        const batch = { id: 'batch-1', sourceId: 'source-1', status: 'queued', jobId: 'batch-job' }; data.batches.push(batch);
        data.jobs['batch-job'] = { id: 'batch-job', status: 'queued', kind: 'batch' }; return json({ batch, job: data.jobs['batch-job'] }, 202);
      }
      if (target === '/workbench/batches/batch-1') return json({ batch: data.batches[0] });
      if (target === '/workbench/tools' && method === 'POST') {
        const tool = { ...body, id: 'tool-1', status: 'registered' }; data.tools.push(tool); return json({ tool }, 201);
      }
      if (target === '/workbench/mcp') return json({ jsonrpc: '2.0', id: body.id, result: { tools: [{ name: 'knowledge.search', description: '현재 지식 검색' }] } });
      if (target === '/workbench/pension/personas') return json({ personas: [persona], topics, questions, feedbackComments,
        feedbackFreeTextAvailable: fixture.feedbackFreeTextAvailable });
      if (target === '/workbench/pension/sessions') {
        assert.equal(body.personaId, 'starter');
        const session = { id: 'session-1', version: 1, title: persona.title, persona, facts: clone(facts), factHash: hash,
          insights: [{ id: 'planning', title: '목표 생활비와 비교', value: facts.monthlyGap, question: questions.planning }], answers: [], feedback: [] };
        data.sessions[session.id] = session; return json({ session }, 201);
      }
      if (target.startsWith('/workbench/pension/sessions/')) {
        const session = data.sessions[target.split('/')[4]];
        if (target.endsWith('/calculate')) {
          assert.equal(body.version, session.version); assert.equal(typeof body.assumptions.monthlyContribution, 'number');
          session.facts.assumptions = body.assumptions; session.facts.projectedBalance = 175000000; session.version++;
          session.answers.forEach(answer => answer.stale = true);
        } else if (target.endsWith('/ask')) {
          assert.equal(body.version, session.version); assert(Object.hasOwn(topics, body.topic));
          if (body.mode === 'model') {
            data.jobs['answer-job'] = { id: 'answer-job', kind: 'answer', status: 'queued' };
            return json({ session, job: data.jobs['answer-job'] }, 202);
          }
          const answer = { id: 'answer-base', mode: 'baseline', text: '합성 연금 자산은 25,000,000원입니다.', factHash: hash, stale: false };
          session.answers.push(answer); session.version++; return json({ session, answer });
        } else if (target.endsWith('/feedback')) {
          assert(session.answers.some(answer => answer.id === body.answerId));
          if (body.comment && fixture.privacyUnavailable) return json({ code: 'privacy-unavailable', error: '자유 의견의 개인정보 처리가 준비되지 않았습니다.' }, 503);
          if (body.commentCode) assert(Object.hasOwn(feedbackComments, body.commentCode));
          const feedback = { id: 'feedback-1', ...body, comment: body.comment || feedbackComments[body.commentCode] || '',
            commentMode: body.comment ? 'private-processed' : 'structured' };
          session.feedback.push(feedback); session.version++; return json({ feedback });
        }
        return json({ session });
      }
      if (target.startsWith('/jobs/')) {
        const id = target.split('/')[2], job = data.jobs[id];
        const polls = fixture.jobPolls[id] = (fixture.jobPolls[id] || 0) + 1;
        if (fixture.failJob) return json({ job: { id, status: 'failed', error: '작업 검증이 차단되었습니다.' } });
        if (polls > 1 && !fixture.holdJobs) {
          job.status = 'completed';
          if (job.kind === 'batch') Object.assign(data.batches[0], { status: 'completed', counts: { documents: 1, vectors: 1, nodes: 0, edges: 0 }, generation: 'generation-2' });
          if (job.kind === 'answer' && !job.result) {
            const session = data.sessions['session-1'];
            const answer = { id: 'answer-model', mode: 'model', modeLabel: '모델 선택 지표·서버 계산',
              text: '목표 대비 월 부족분: 1,520,833원', stale: false, factHash: hash };
            session.answers.push(answer); session.version++; job.result = { sessionId: session.id, answer };
          }
          if (job.kind === 'skill-validation' && !job.result) {
            const skill = data.skills[0];
            skill.version++; skill.status = 'PENDING_APPROVAL'; skill.validation.behavior.status = fixture.behaviorPass ? 'passed' : 'failed';
            job.result = { skillId: skill.id, version: skill.version, behaviorStatus: skill.validation.behavior.status };
          }
          if (job.kind === 'skill-execution' && !job.result) {
            const execution = data.executions['execution-1'];
            execution.artifact.status = 'completed';
            execution.result = { status: 'answered', answer: '원본 권한이 확인된 실행 결과입니다.', evidenceIds: ['e1'], executionMode: 'context-only', toolsExecuted: [] };
            job.result = { artifactId: 'execution-1', skillId: execution.artifact.skillId, status: 'completed' };
          }
        } else job.status = 'running';
        return json({ job });
      }
      if (target === '/workbench/reports' && method === 'POST') {
        const report = { ...body, id: 'report-1', version: 1, status: 'draft', contentHash: hash, sourceRefs: [{ kind: 'wb_pension', id: body.sessionId, version: 4 }], unresolved: [] };
        data.reports.push(report); return json({ report }, 201);
      }
      if (target.startsWith('/workbench/reports/')) {
        const report = data.reports[0];
        if (target.endsWith('/document')) return json({ markdown: `# ${report.title}\n\n저장된 원본 근거를 사용한 보고서입니다.`, status: report.status, contentHash: report.contentHash });
        if (target.endsWith('/approve')) { assert.equal(body.version, report.version); assert.equal(body.contentHash, report.contentHash); report.version++; report.status = 'approved'; }
        return json({ report });
      }
      if (method === 'GET' && Object.hasOwn(data, segment) && Array.isArray(data[segment])) {
        let items = data[segment];
        if (segment === 'tasks') items = items.filter(item => (!url.searchParams.get('role') || item.role === url.searchParams.get('role')) && (!url.searchParams.get('status') || item.status === url.searchParams.get('status')));
        return json({ items, ...(segment === 'tools' ? { endpoint: '/studio-api/workbench/mcp', operator: scope === 'a' } : {}) });
      }
      throw new Error(`Unexpected fixture: ${method} ${target}`);
    } catch (error) { fixture.errors.push(String(error)); return json({ error: '테스트 응답 오류' }, 500); }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => fixture.errors.push(String(error)));
  const consoleErrors = [];
  page.on('console', message => { if (message.type() === 'error' && !message.text().includes('Failed to load resource')) consoleErrors.push(message.text()); });
  async function open(view, scope = 'a', context = '') {
    await page.goto(`https://workbench.test/#/wb-${view}?projectId=${scope}&trace=opaque%2Fcontext${context}`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('heading', { level: 1 }).waitFor();
  }
  async function nav(view) { await page.getByRole('navigation').getByRole('button', { name: view, exact: true }).click(); }
  return { fixture, data: scopeData, page, open, nav, async close() {
    const screen = new URL(page.url()).hash.split('?')[0].replace(/[^a-z-]/g, '') || 'entry';
    fs.writeFileSync(`/tmp/workbench-debug-${screen}.txt`, await page.locator('body').innerText());
    await page.screenshot({ path: `/tmp/workbench-debug-${screen}.png`, fullPage: true });
    await browser.close(); assert.deepEqual(fixture.external, []); assert.deepEqual(fixture.errors, []); assert.deepEqual(consoleErrors, []);
  } };
}

test('React components expose source download and link the selected component with project context', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('components');
    await h.page.getByRole('button', { name: 'React 코드 다운로드 (ZIP)', exact: true }).waitFor();
    await h.page.getByLabel('컴포넌트 검색', { exact: true }).fill('Button');
    await h.page.locator('.wb-component').filter({ has: h.page.getByRole('heading', { name: 'Button', exact: true }) })
      .getByRole('button', { name: '소스·실행 예제', exact: true }).click();
    await h.page.waitForURL(/#\/portal\?/);
    const params = new URLSearchParams(new URL(h.page.url()).hash.split('?')[1]);
    assert.equal(params.get('component'), 'Button');
    assert.equal(params.get('projectId'), 'a');
    assert.equal(params.get('tab'), null);
  } finally { await h.close(); }
});

test('canonical ontology shows provenance and unknown coverage, keeps review revisions and cancels stale impact', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('ontology');
    await h.page.getByRole('heading', { name: '자산·상품·규정·코드의 연결', exact: true }).waitFor();
    assert.equal(await h.page.getByRole('button', { name: '원본 분석·매핑 후보 등록', exact: true }).isDisabled(), true);
    const choose = title => h.page.locator('.wb-record-list button').filter({ hasText: title }).click();
    await choose('A 이미지');
    assert.equal(await h.page.getByRole('button', { name: '이 버전 승인', exact: true }).isDisabled(), true);
    h.fixture.holdOntology = true;
    await h.page.getByRole('button', { name: '이 항목 변경 영향 보기', exact: true }).click();
    while (!h.fixture.heldOntology) await new Promise(resolve => setTimeout(resolve, 10));
    await choose('A 화면');
    await h.fixture.heldOntology();
    assert.equal(await h.page.getByRole('heading', { name: '변경 영향 경로', exact: true }).count(), 0);
    await h.page.getByLabel('검토 근거', { exact: true }).fill('원본의 화면 연결을 확인했습니다.');
    await h.page.getByRole('button', { name: '검토 완료 기록', exact: true }).click();
    await h.page.waitForFunction(() => [...document.querySelectorAll('.wb-record-list button')].some(el => el.textContent.includes('검토 완료')));
    await choose('A 화면');
    await h.page.getByLabel('검토 근거', { exact: true }).fill('현재 버전 승인');
    assert.equal(await h.page.getByRole('button', { name: '이 버전 승인', exact: true }).isDisabled(), false);
    await h.page.getByLabel('프로젝트', { exact: true }).selectOption('b');
    await h.page.locator('.wb-record-list button').filter({ hasText: 'B 이미지' }).waitFor();
    assert.equal(await h.page.getByLabel('검토 근거', { exact: true }).count(), 0);
    assert.equal(await h.page.locator('.wb-record-list button').filter({ hasText: 'A 화면' }).count(), 0);
  } finally { await h.close(); }
});

test('Workbench creates a project, saves and publishes real product fields, preserves opaque navigation', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('planning');
    await h.page.getByText('새 프로젝트', { exact: true }).click();
    await h.page.getByLabel('프로젝트 이름', { exact: true }).fill('가상 상품 협업');
    await h.page.getByRole('button', { name: '프로젝트 만들기', exact: true }).click();
    await h.page.waitForURL(/projectId=new-project/);
    assert(h.page.url().includes('trace=opaque%2Fcontext'));
    await h.page.getByLabel('상품 이름', { exact: true }).fill('가상 연금 상품');
    await h.page.getByLabel('상품 설명', { exact: true }).fill('저장할 업무 기준');
    await h.page.getByRole('button', { name: '조건 추가', exact: true }).click();
    await h.page.getByLabel('조건 1', { exact: true }).fill('합성 예제만 사용합니다.');
    await h.page.getByRole('button', { name: '상품 지침 저장', exact: true }).click();
    await h.page.waitForURL(/productId=product-created/);
    assert.equal(h.data('new-project').products[0].conditions[0].text, '합성 예제만 사용합니다.');
    await h.page.getByLabel('이 초안을 새 기준으로 게시합니다', { exact: true }).check();
    await h.page.getByRole('button', { name: '버전 고정·지침 게시', exact: true }).click();
    await h.page.getByText('게시 지침 1차', { exact: true }).waitFor();
    await h.page.getByRole('button', { name: '이 상품의 변경 요청', exact: true }).click();
    await h.page.waitForURL(/wb-changes/);
    assert(h.page.url().includes('productId=product-created'));
    const context = new URLSearchParams(new URL(h.page.url()).hash.split('?')[1]);
    assert.equal(context.get('targetId'), null, 'Product IDs must not be invented as graph targets');
    assert.equal(await h.page.getByLabel('변경 대상', { exact: true }).inputValue(), '');
    await h.page.getByText('연결 정보에서 확인한 변경 대상을 선택하세요.', { exact: false }).waitFor();
    assert(await h.page.getByRole('button', { name: '변경 요청 저장', exact: true }).isDisabled());
    assert.deepEqual(await h.page.evaluate(() => window.navigations), ['wb-changes']);
    await h.page.screenshot({ path: '/tmp/workbench-planning-flow.png', fullPage: false });
  } finally { await h.close(); }
});

test('project switch cancels stale requests, resets overview, denies operator direct routes and revoked membership', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    h.fixture.holdKnowledge = true;
    await h.open('knowledge');
    await h.page.waitForFunction(() => window.overviews.some(value => value?.operator));
    while (!h.fixture.held) await new Promise(resolve => setTimeout(resolve, 10));
    await h.page.getByLabel('프로젝트', { exact: true }).selectOption('b');
    await h.page.getByText('B 연금 업무 가이드', { exact: true }).waitFor();
    await h.fixture.held();
    assert.equal(await h.page.getByText('A 연금 업무 가이드', { exact: true }).count(), 0);
    assert((await h.page.evaluate(() => window.overviews)).some(value => value === null));
    assert.equal((await h.page.evaluate(() => window.overviews)).at(-1).role, 'developer');
    await h.nav('sources');
    await h.page.getByRole('alert').filter({ hasText: '운영자 화면에 접근할 권한이 없습니다' }).waitFor();
    assert.equal(await h.page.getByLabel('원본 이름', { exact: true }).count(), 0);
    assert(!h.fixture.calls.some(call => call.scope === 'b' && call.target === '/workbench/sources'));
    await h.page.getByLabel('프로젝트', { exact: true }).selectOption('revoked');
    await h.page.getByRole('alert').filter({ hasText: '권한이 회수' }).waitFor();
    assert.equal((await h.page.evaluate(() => window.overviews)).at(-1), null);
    await h.page.screenshot({ path: '/tmp/workbench-denied.png', fullPage: false });
  } finally { await h.close(); }
});

test('saved impact reloads with baseline/proposal, all role worklists and evidence-gated task completion', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('changes');
    await h.page.getByLabel('변경 제목', { exact: true }).fill('아이콘 안내 변경');
    await h.page.getByLabel('변경 대상', { exact: true }).selectOption('example-icon');
    await h.page.getByLabel('기준안', { exact: true }).fill('기존 안내 아이콘');
    await h.page.getByLabel('제안안', { exact: true }).fill('의미가 명확한 안내 아이콘');
    await h.page.getByLabel('변경 이유', { exact: true }).fill('화면 접근성 개선');
    await h.page.getByRole('button', { name: '변경 요청 저장', exact: true }).click();
    await h.page.getByRole('button', { name: '영향 분석 실행', exact: true }).click();
    await h.page.getByRole('heading', { name: '영향 범위와 근거 경로' }).waitFor();
    assert.equal(h.data('a').tasks.length, 3);
    await h.page.reload();
    await h.page.getByRole('heading', { name: '영향 범위와 근거 경로' }).waitFor();
    await h.page.getByRole('button', { name: '생성된 작업 확인', exact: true }).click();
    await h.page.getByRole('heading', { name: '현황 진단 developer', exact: true }).waitFor();
    await h.page.getByLabel('담당 역할', { exact: true }).selectOption('developer');
    const task = h.page.locator('.wb-task').filter({ hasText: '현황 진단 developer' });
    await task.getByText('작업 상태·완료 근거 제출', { exact: true }).click();
    await task.getByLabel('현황 진단 developer 상태', { exact: true }).selectOption('done');
    await task.getByRole('button', { name: '작업 상태 저장', exact: true }).click();
    await task.getByRole('alert').filter({ hasText: '실제 원본 근거' }).waitFor();
    assert.equal(h.data('a').tasks[2].status, 'open');
    await task.getByRole('button', { name: '이 작업의 원본 근거 입력', exact: true }).click();
    await task.getByRole('button', { name: '작업 상태 저장', exact: true }).click();
    await task.locator('.wb-status.wb-good').filter({ hasText: /^완료$/ }).waitFor();
    assert.equal(h.data('a').tasks[2].status, 'done');
  } finally { await h.close(); }
});

test('Skill editor saves bytes, distinguishes package/behavior validation, rejects unavailable model and gates approval', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('skills');
    await h.page.getByLabel('Skill 식별 이름').fill('change-review');
    await h.page.getByLabel('Skill 제목', { exact: true }).fill('변경 검토');
    await h.page.getByLabel('Skill 설명', { exact: true }).fill('변경 근거를 확인하는 지침');
    await h.page.getByLabel('Skill 지침 Markdown').fill('Review the pinned sources. Report unknown dependencies explicitly.');
    await h.page.getByRole('button', { name: 'Skill 저장', exact: true }).click();
    await h.page.getByRole('button', { name: '저장 버전 검증', exact: true }).click();
    await h.page.getByText('저장된 버전 v2', { exact: true }).waitFor();
    await h.page.getByLabel('현재 내용·원본·검증 근거를 확인했습니다', { exact: true }).check();
    await h.page.getByRole('button', { name: '이 버전 승인', exact: true }).click();
    await h.page.getByRole('alert').filter({ hasText: '실제 행동 평가' }).waitFor();
    assert.equal(h.data('a').skills[0].status, 'PENDING_APPROVAL');
    h.fixture.behaviorPass = true;
    await h.page.getByRole('button', { name: '저장 버전 검증', exact: true }).click();
    await h.page.getByText('저장된 버전 v3', { exact: true }).waitFor();
    await h.page.getByLabel('현재 내용·원본·검증 근거를 확인했습니다', { exact: true }).check();
    await h.page.getByRole('button', { name: '이 버전 승인', exact: true }).click();
    await h.page.getByText('저장된 버전 v4', { exact: true }).waitFor();
    assert.equal(h.data('a').skills[0].status, 'APPROVED');
    await h.page.getByRole('button', { name: '저장 패키지 확인', exact: true }).click();
    await h.page.getByRole('heading', { name: '저장된 패키지 원문' }).waitFor();
    await h.page.getByText('모델로 초안 제안받기', { exact: true }).click();
    await h.page.getByLabel('Skill 제안 목표', { exact: true }).fill('출금 업무 변경 검토');
    await h.page.getByRole('button', { name: '모델 초안 요청', exact: true }).click();
    await h.page.getByRole('alert').filter({ hasText: 'model-not-configured' }).waitFor();
    await h.page.screenshot({ path: '/tmp/workbench-skills.png', fullPage: false });
  } finally { await h.close(); }
});

test('operator snapshot import polls actual jobs, tool registration errors never claim saved state', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('sources');
    await h.page.getByLabel('원본 이름', { exact: true }).fill('가상 연금 규정');
    await h.page.getByRole('button', { name: '원본 등록', exact: true }).click();
    await h.page.getByRole('button', { name: '이 원본 수집·배치 확인', exact: true }).click();
    await h.page.getByRole('button', { name: '가상 문서 예시 입력', exact: true }).click();
    await h.page.getByRole('button', { name: '수집·색인 배치 시작', exact: true }).click();
    await h.page.getByText('백그라운드 작업 · 처리 중', { exact: false }).waitFor();
    await h.page.getByText('배치 작업이 완료되었습니다.', { exact: false }).waitFor();
    assert.equal(h.data('a').batches[0].status, 'completed');
    assert(h.fixture.jobPolls['batch-job'] >= 2);
    await h.nav('tools');
    await h.page.getByRole('button', { name: '제공 도구 조회', exact: true }).click();
    await h.page.getByRole('button', { name: 'knowledge.search 추가', exact: true }).click();
    await h.page.getByLabel('도구 묶음 이름', { exact: true }).fill('내부 지식 검색');
    await h.page.getByLabel('도구 설명', { exact: true }).fill('프로젝트별 원본 검색');
    h.fixture.denyWrite = true;
    await h.page.getByRole('button', { name: '내부 도구 등록', exact: true }).click();
    await h.page.getByRole('alert').filter({ hasText: '서버에서 운영 권한을 거부' }).waitFor();
    assert.equal(h.data('a').tools.length, 0);
    h.fixture.denyWrite = false;
    await h.page.getByRole('button', { name: '내부 도구 등록', exact: true }).click();
    await h.page.getByRole('heading', { name: '내부 지식 검색', exact: true }).waitFor();
    assert.equal(h.data('a').tools.length, 1);
  } finally { await h.close(); }
});

test('pension actual response schema supports assumptions, honest baseline/model jobs, feedback and approved report', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('pension');
    await h.page.getByRole('button', { name: /차근차근 준비하는 직장인/ }).click();
    await h.page.getByRole('button', { name: '이 페르소나로 상담 시작', exact: true }).click();
    await h.page.getByLabel('월 납입액 (원)', { exact: true }).fill('500000');
    await h.page.getByRole('button', { name: '가정 적용·다시 계산', exact: true }).click();
    await h.page.getByText('175,000,000', { exact: true }).waitFor();
    assert.equal(h.data('a').sessions['session-1'].facts.assumptions.monthlyContribution, 500000);
    await h.page.getByRole('button', { name: /목표 생활비와 비교/ }).click();
    assert.equal(await h.page.getByLabel('상담 주제', { exact: true }).inputValue(), 'planning');
    await h.page.getByRole('button', { name: '상담 질문 보내기', exact: true }).click();
    await h.page.getByText('결정론적 기준 응답', { exact: true }).waitFor();
    await h.page.getByLabel('상담 만족도', { exact: true }).selectOption('4');
    assert.equal(await h.page.getByLabel('추가 평가 의견', { exact: true }).count(), 0);
    await h.page.getByLabel('평가 의견', { exact: true }).selectOption('clear');
    await h.page.getByRole('button', { name: '평가 저장', exact: true }).click();
    await h.page.getByText('평가 의견을 서버에 저장했습니다.', { exact: true }).waitFor();
    const sentFeedback = h.fixture.calls.find(call => call.target.endsWith('/feedback') && call.method === 'POST');
    assert.equal(sentFeedback.body.commentCode, 'clear');
    assert.equal(Object.hasOwn(sentFeedback.body, 'comment'), false);
    await h.page.getByLabel('요청할 응답 방식', { exact: true }).selectOption('model');
    await h.page.getByLabel('상담 질문', { exact: true }).fill(questions.operation);
    await h.page.getByLabel('상담 주제', { exact: true }).selectOption('operation');
    await h.page.getByRole('button', { name: '상담 질문 보내기', exact: true }).click();
    await h.page.getByText('모델 선택 지표·서버 계산', { exact: true }).waitFor();
    await h.page.getByText('목표 대비 월 부족분: 1,520,833원', { exact: true }).waitFor();
    assert.equal(await h.page.getByRole('option', { name: '모델로 관련 지표 선택', exact: true }).count(), 1);
    assert.equal(h.data('a').sessions['session-1'].answers.at(-1).mode, 'model');
    await h.page.screenshot({ path: '/tmp/workbench-pension.png', fullPage: true });
    await h.page.getByRole('button', { name: '상담 평가 보고서', exact: true }).click();
    await h.page.getByLabel('보고서 제목', { exact: true }).fill('가상 연금 상담 평가');
    await h.page.getByRole('button', { name: '근거 모아 보고서 생성', exact: true }).click();
    await h.page.getByText('저장된 보고서 원문', { exact: true }).waitFor();
    await h.page.getByLabel('문서 내용과 연결된 근거를 확인했습니다', { exact: true }).check();
    await h.page.getByRole('button', { name: '이 보고서 버전 승인', exact: true }).click();
    await h.page.waitForFunction(() => [...document.querySelectorAll('button')].find(b => b.textContent === '이 보고서 버전 승인')?.disabled);
    assert.equal(h.data('a').reports[0].status, 'approved');
    await h.page.setViewportSize({ width: 390, height: 844 });
    await h.page.screenshot({ path: '/tmp/workbench-mobile-report.png', fullPage: true });
    assert(await h.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Mobile page must not overflow horizontally');
    await h.page.reload();
    await h.page.getByText('저장된 보고서 원문', { exact: true }).waitFor();
  } finally { await h.close(); }
});

test('unmapped deep-link targets stay unselected and cannot create a change', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    await h.open('changes', 'a', '&productId=product-outside-graph&targetId=product-outside-graph');
    await h.page.getByLabel('변경 제목', { exact: true }).fill('미확인 대상 변경');
    await h.page.getByLabel('기준안', { exact: true }).fill('이전');
    await h.page.getByLabel('제안안', { exact: true }).fill('이후');
    await h.page.getByLabel('변경 이유', { exact: true }).fill('실제 그래프 대상을 선택해야 합니다.');
    assert.equal(await h.page.getByLabel('변경 대상', { exact: true }).inputValue(), '');
    assert(await h.page.getByRole('button', { name: '변경 요청 저장', exact: true }).isDisabled());
    assert(!h.fixture.calls.some(call => call.target === '/workbench/changes' && call.method === 'POST'));
    await h.page.getByLabel('변경 대상', { exact: true }).selectOption({ label: '가상 안내 아이콘 · Icon' });
    assert(await h.page.getByRole('button', { name: '변경 요청 저장', exact: true }).isEnabled());
  } finally { await h.close(); }
});

test('optional free feedback requires server availability and does not claim success when privacy processing fails', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    h.fixture.feedbackFreeTextAvailable = true; h.fixture.privacyUnavailable = true;
    await h.open('pension');
    await h.page.getByRole('button', { name: /차근차근 준비하는 직장인/ }).click();
    await h.page.getByRole('button', { name: '이 페르소나로 상담 시작', exact: true }).click();
    await h.page.getByRole('button', { name: '상담 질문 보내기', exact: true }).click();
    await h.page.getByText('결정론적 기준 응답', { exact: true }).waitFor();
    await h.page.getByLabel('상담 만족도', { exact: true }).selectOption('3');
    await h.page.getByText('추가 의견 직접 입력', { exact: true }).click();
    await h.page.getByLabel('추가 평가 의견', { exact: true }).fill('사내 검사를 거쳐야 하는 추가 의견입니다.');
    await h.page.getByRole('button', { name: '평가 저장', exact: true }).click();
    await h.page.getByRole('alert').filter({ hasText: 'privacy-unavailable' }).waitFor();
    assert.equal(h.data('a').sessions['session-1'].feedback.length, 0);
    assert.equal(await h.page.getByText('평가 의견을 서버에 저장했습니다.', { exact: true }).count(), 0);
    await h.page.getByLabel('추가 평가 의견', { exact: true }).fill('');
    await h.page.getByLabel('평가 의견', { exact: true }).selectOption('needs-evidence');
    await h.page.getByRole('button', { name: '평가 저장', exact: true }).click();
    await h.page.getByText('평가 의견을 서버에 저장했습니다.', { exact: true }).waitFor();
    assert.equal(h.data('a').sessions['session-1'].feedback[0].commentMode, 'structured');
  } finally { await h.close(); }
});

test('all workbench routes render meaningful screens and failed load can recover', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    for (const view of ['deliverables', 'components', 'knowledge', 'ontology', 'development', 'operations']) {
      await h.open(view);
      await h.page.getByText('서버에서 확인한 현재 권한', { exact: true }).waitFor();
      assert.equal(await h.page.title(), 'Workbench fixture');
      assert((await h.page.locator('main').innerText()).length > 150);
      assert.equal(await h.page.locator('vite-error-overlay').count(), 0);
    }
    await h.open('knowledge');
    await h.page.getByRole('button', { name: /A 연금 업무 가이드/ }).click();
    await h.page.getByText('A 프로젝트의 원본 안내입니다.', { exact: true }).waitFor();
    await h.page.screenshot({ path: '/tmp/workbench-knowledge.png', fullPage: true });
    h.fixture.failProducts = true;
    await h.nav('planning');
    await h.page.getByRole('alert').first().waitFor();
    h.fixture.failProducts = false;
    await h.page.getByRole('button', { name: '다시 조회', exact: true }).first().click();
    await h.page.getByLabel('상품 이름', { exact: true }).waitFor();
  } finally { await h.close(); }
});

test('queued Skill validation and approved execution wait for real jobs and reject revoked result access', { timeout: 60000 }, async () => {
  const h = await harness();
  try {
    h.data('a').skills.push({ id: 'skill-1', name: 'source-review', title: '원본 검토', description: '고정 원본 검토',
      instructions: 'Review pinned evidence.', version: 1, contentHash: hash, status: 'DRAFT', sourceRefs: [], toolNames: [], examples: [],
      validation: { contentHash: hash, package: { status: 'not-run' }, behavior: { status: 'not-run' } } });
    h.fixture.asyncValidation = true; h.fixture.behaviorPass = true; h.fixture.holdJobs = true;
    await h.open('skills', 'a', '&skillId=skill-1');
    await h.page.getByRole('button', { name: '저장 버전 검증', exact: true }).click();
    await h.page.getByText('백그라운드 작업 · 처리 중', { exact: false }).waitFor();
    assert.equal(await h.page.getByText('검증 작업이 끝나 저장된 결과를 조회했습니다.', { exact: false }).count(), 0);
    assert.equal(await h.page.getByText('저장된 버전 v3', { exact: true }).count(), 0);
    h.fixture.holdJobs = false;
    await h.page.getByText('저장된 버전 v3', { exact: true }).waitFor();
    assert(h.fixture.calls.some(call => call.target === '/workbench/skills/skill-1' && call.method === 'GET'));
    await h.page.getByLabel('현재 내용·원본·검증 근거를 확인했습니다', { exact: true }).check();
    await h.page.getByRole('button', { name: '이 버전 승인', exact: true }).click();
    await h.page.getByText('저장된 버전 v4', { exact: true }).waitFor();
    await h.page.getByLabel('승인 Skill 실행 입력', { exact: true }).fill('현재 원본을 검토해 주세요.');
    h.fixture.holdJobs = true;
    await h.page.getByRole('button', { name: '승인 버전으로 실행', exact: true }).click();
    await h.page.getByText('백그라운드 작업 · 처리 중', { exact: false }).waitFor();
    await h.page.getByRole('button', { name: '대기 중지', exact: true }).click();
    await h.page.getByText('응답 대기를 중지했습니다.', { exact: false }).waitFor();
    assert.equal(await h.page.getByText('원본 권한이 확인된 실행 결과입니다.', { exact: true }).count(), 0);
    assert(h.page.url().includes('executionId=execution-1'));
    h.fixture.holdJobs = false;
    await h.page.getByRole('button', { name: '실행 상태 확인', exact: true }).click();
    await h.page.getByText('원본 권한이 확인된 실행 결과입니다.', { exact: true }).waitFor();
    assert(h.fixture.calls.some(call => call.target === '/workbench/skills/skill-1/executions/execution-1'));
    h.fixture.denyExecutionRead = true;
    await h.page.getByRole('button', { name: '실행 상태 확인', exact: true }).click();
    await h.page.getByRole('alert').filter({ hasText: '실행 원본 권한이 회수' }).waitFor();
    assert.equal(await h.page.getByText('원본 권한이 확인된 실행 결과입니다.', { exact: true }).count(), 0);
  } finally { await h.close(); }
});
