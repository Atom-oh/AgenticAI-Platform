'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const esbuild = require('esbuild');
const { buildProject } = require('./compile.cjs');
const { catalog } = require('./manifest.cjs');
const { samples } = require('./samples/index.cjs');

async function initialMarkup(sampleId) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-sample-markup-'));
  try {
    const script = path.join(directory, 'render.cjs');
    // This entry executes only the repository's own examples, after the normal
    // component/type/policy build. No uploaded sources are accepted here.
    await esbuild.build({
      stdin: {
        contents: `import {renderToStaticMarkup} from 'react-dom/server';import App from './samples/${sampleId}/src/App';process.stdout.write(renderToStaticMarkup(<App/>));`,
        loader: 'tsx', resolveDir: __dirname,
      },
      outfile: script, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
      define: { 'process.env.NODE_ENV': '"production"' },
      alias: { '@studio/approved-ui': path.join(__dirname, 'ui/index.tsx') },
      logLevel: 'silent',
    });
    return execFileSync(process.execPath, [script], { encoding: 'utf8', timeout: 10000,
      maxBuffer: 2_500_000, env: { PATH: process.env.PATH } });
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
}

// Only repository-owned, synthetic examples are published. No workspace records or uploaded files are read.
async function buildSamples(destination) {
  const { id, label, version, hash } = catalog();
  const manifest = { schemaVersion: 1, catalog: { id, label, version, hash }, samples: [] };
  fs.mkdirSync(destination, { recursive: true });
  // Never leave an older manifest advertising partially rebuilt samples after a failed build.
  fs.rmSync(path.join(destination, 'index.json'), { force: true });
  for (const sample of samples) {
    if (!/^[a-z][a-z0-9-]+$/.test(sample.id)) throw new Error('Invalid repository sample identifier');
    const contract = { title: sample.title, viewport: { width: 390, height: 844 }, rules: sample.rules };
    const files = { 'src/App.tsx': fs.readFileSync(path.join(__dirname, 'samples', sample.id, 'src/App.tsx'), 'utf8') };
    const built = await buildProject({ files, assets: {}, expectedCatalogHash: hash, contract });
    if (!built.ok) throw new Error(`${sample.id}: ${built.diagnostics.join('\n')}`);
    const output = path.join(destination, sample.id);
    fs.mkdirSync(output, { recursive: true });
    const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
    const markup = await initialMarkup(sample.id);
    const html = built.previewHtml.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${policy}">`)
      .replace('<div id="root"></div>', () => `<div id="root">${markup}</div>`);
    fs.writeFileSync(path.join(output, 'preview.html'), html);
    fs.writeFileSync(path.join(output, 'source.zip'), Buffer.from(built.projectZipBase64, 'base64'));
    fs.writeFileSync(path.join(output, 'dist.zip'), Buffer.from(built.distZipBase64, 'base64'));
    fs.writeFileSync(path.join(output, 'contract.json'), JSON.stringify(contract, null, 2) + '\n');
    fs.writeFileSync(path.join(output, 'guide.md'), [
      `# ${sample.title} — 동작 확인용 샘플`, '',
      '고객사의 승인을 받은 상품·약관·컴포넌트가 아닙니다. 실제 거래와 개인정보 수집은 실행하지 않습니다.', '',
      '## 샘플 업무 규칙', '', sample.guidance, '',
      '## 확인할 동작', '', ...sample.checks.map(check => `- ${check}`), '',
      '## Studio에서 사용하기', '',
      'HTML과 이 가이드 파일을 내려받아 파일 준비에서 함께 업로드하세요. AI가 제안한 규칙을 확인·수정하고 승인한 뒤 생성·검수할 수 있습니다.',
      'HTML에는 같은 React 코드로 미리 렌더링한 첫 화면이 들어 있어, 스크립트를 실행하지 않는 반입 미리보기에서도 화면과 문구를 읽을 수 있습니다.',
      '샘플의 React 소스 ZIP은 개발자용입니다. 현재 파일 반입은 React 프로젝트 ZIP을 직접 실행하거나 승인하지 않습니다.',
      'HTML 반입에서 다시 생성한 React는 새로운 산출물이며 별도 검수와 승인이 필요합니다.', '',
      '## 개발자용 소스', '',
      `실제 기준 코드는 @studio/approved-ui v${version}의 ui/ 폴더입니다. 패키지 해시: ${hash}`,
      'Markdown은 업무 설명이며 컴포넌트의 타입·스타일·동작 기준은 React 코드와 studio.lock.json에 고정됩니다.',
      'source.zip을 풀고 허용된 사내 저장소/캐시에서 npm ci 후 npm run typecheck, npm run build, npm test를 실행하세요.',
      '테스트에는 Playwright Chromium이 필요합니다. WORKSPACE_CHROMIUM으로 설치된 브라우저 경로를 지정할 수 있습니다.',
      'dist.zip은 S3 정적 호스팅용 빌드 파일입니다. 접근 제어·배포 정책은 고객 환경에 맞게 설정하세요.', '',
    ].join('\n'));
    manifest.samples.push({
      id: sample.id, title: sample.title, description: sample.description, checks: sample.checks,
      components: built.components, sourceHash: built.sourceHash, bundleHash: built.bundleHash,
      preview: `${sample.id}/preview.html`, source: `${sample.id}/source.zip`, dist: `${sample.id}/dist.zip`,
      guide: `${sample.id}/guide.md`, contract: `${sample.id}/contract.json`,
    });
  }
  fs.writeFileSync(path.join(destination, 'index.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}
if (require.main === module) {
  buildSamples(path.resolve(__dirname, '../web/public/studio-samples'))
    .then(result => console.log(`Built ${result.samples.length} React samples with kit ${result.catalog.version} (${result.catalog.hash}).`))
    .catch(error => { console.error(error.message); process.exitCode = 1; });
}
module.exports = { buildSamples };
