'use strict';
const { createHash } = require('node:crypto');
const { deflateRawSync } = require('node:zlib');
const fs = require('node:fs');
const path = require('node:path');

const sha256 = value => createHash('sha256').update(value).digest('hex');
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function fileHashes(files) {
  return Object.keys(files).sort().map(name => ({ path: name, sha256: sha256(files[name]) }));
}
const hashFiles = files => sha256(canonical(fileHashes(files)));
const TS_CONFIG = {
  compilerOptions: {
    target: 'ES2020', module: 'ESNext', moduleResolution: 'Bundler', jsx: 'react-jsx',
    strict: true, esModuleInterop: true, skipLibCheck: true, noEmit: true,
    lib: ['ES2020', 'DOM', 'DOM.Iterable'], types: ['react', 'react-dom'],
    allowImportingTsExtensions: true, baseUrl: '.', paths: { '@studio/approved-ui': ['ui/index.tsx'] },
  },
  include: ['src/**/*', 'ui/**/*'],
};

const crcTable = Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});
function crc32(bytes) {
  let result = 0xffffffff;
  for (const byte of bytes) result = crcTable[(result ^ byte) & 0xff] ^ (result >>> 8);
  return (result ^ 0xffffffff) >>> 0;
}
function zip(files) {
  const local = [], central = [];
  let offset = 0;
  const names = Object.keys(files).sort();
  if (names.length > 500) throw new Error('Too many export files');
  for (const name of names) {
    if (!name || name.startsWith('/') || name.includes('\\') || name.split('/').some(part => !part || part === '.' || part === '..')) {
      throw new Error('Unsafe export path');
    }
    const label = Buffer.from(name), bytes = Buffer.from(files[name]), compressed = deflateRawSync(bytes, { level: 9 });
    if (label.length > 1024 || bytes.length > 16 * 1024 * 1024) throw new Error('Export file limit exceeded');
    const crc = crc32(bytes);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x0800, 6); header.writeUInt16LE(8, 8); header.writeUInt16LE(33, 12);
    header.writeUInt32LE(crc, 14); header.writeUInt32LE(compressed.length, 18); header.writeUInt32LE(bytes.length, 22);
    header.writeUInt16LE(label.length, 26);
    local.push(header, label, compressed);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8); entry.writeUInt16LE(8, 10); entry.writeUInt16LE(33, 14);
    entry.writeUInt32LE(crc, 16); entry.writeUInt32LE(compressed.length, 20); entry.writeUInt32LE(bytes.length, 24);
    entry.writeUInt16LE(label.length, 28); entry.writeUInt32LE(offset, 42);
    central.push(entry, label); offset += header.length + label.length + compressed.length;
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(names.length, 8); end.writeUInt16LE(names.length, 10);
  end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}

function sourceProject(root, files, descriptor, contract) {
  const output = { ...files };
  for (const item of descriptor.files) {
    const absolute = path.resolve(root, item.path);
    if (!absolute.startsWith(path.resolve(root) + path.sep)) throw new Error('Invalid kit file path');
    const bytes = fs.readFileSync(absolute);
    if (sha256(bytes) !== item.sha256) throw new Error('Component kit changed during build');
    output[item.path] = bytes;
  }
  for (const name of ['compile.cjs', 'policy.cjs', 'project.cjs', 'manifest.cjs', 'package-lock.json']) {
    output[name] = fs.readFileSync(path.join(root, name));
  }
  const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  packageJson.scripts = {
    typecheck: 'node compile.cjs --check',
    build: 'node compile.cjs --build',
    test: 'node --test test/flow.test.cjs',
  };
  output['package.json'] = JSON.stringify(packageJson, null, 2) + '\n';
  output['tsconfig.json'] = JSON.stringify(TS_CONFIG, null, 2) + '\n';
  output['studio.lock.json'] = canonical({ schemaVersion: 1, catalogHash: descriptor.hash, files: descriptor.files }) + '\n';
  output['test/contract.json'] = canonical(contract || { rules: [], viewport: { width: 390, height: 844 } }) + '\n';
  output['test/flow.test.cjs'] = fs.readFileSync(path.join(root, 'templates/flow.test.cjs'));
  output['README.md'] = [
    '# 검증된 React 프로젝트', '',
    '컴포넌트의 기준은 ui/ 코드와 studio.lock.json입니다. 컴포넌트를 바꾸면 기존 승인과 동일한 산출물이 아닙니다.',
    'src/App.tsx와 src/pages/는 생성한 화면 구성·상태 로직입니다. 기준 컴포넌트 소스와 분리되어 있습니다.', '',
    '허용된 사내 패키지 저장소 또는 캐시에서 npm ci를 실행하세요. 의존성은 package-lock.json에 고정되어 있습니다.',
    'npm run typecheck → npm run build → npm test 순서로 실행합니다. 테스트에는 설치된 Playwright Chromium이 필요합니다.',
    '빌드한 dist/는 외부 리소스가 필요 없는 정적 배포 파일입니다. 소스 프로젝트 전체를 웹에 게시하지 마세요.',
    'npm test는 저장된 업무 규칙의 브라우저 동작을 검사합니다. Studio의 별도 검수 보고서에는 접근성·시각 비교 범위가 기록됩니다.',
    '실제 은행 API와 고객 환경의 통합 검증은 제공된 연결과 승인된 테스트 범위에서 추가로 수행해야 합니다.', '',
  ].join('\n');
  return output;
}

module.exports = { canonical, sha256, fileHashes, hashFiles, zip, sourceProject, TS_CONFIG };
