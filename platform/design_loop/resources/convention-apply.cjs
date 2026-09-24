'use strict';
// Applies the customer screen-path convention to the approved handoff project (engine plan E17, roadmap D-6).
//
//   node convention/apply.cjs <outDir>        (run after `npm ci` inside project/)
//
// project/ is never modified. Each approved page is copied to <outDir>/<conventionPath>; the page-to-logic import
// specifiers ('../logic/<name>') are the only text rewritten. src/logic and the pinned kit ui/ are copied unchanged
// to <outDir>/logic and <outDir>/ui. The transformed layout is then type-checked with its own generated
// <outDir>/convention/tsconfig.json, which includes every .tsx/.ts file of <outDir>, using the TypeScript that
// project/package-lock.json pins (the local equivalent of `npx tsc --noEmit -p convention/tsconfig.json`).
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const project = path.join(root, 'project');
const out = path.resolve(process.argv[2] || path.join(root, 'convention-out'));
const LOGIC_IMPORT = /from '\.\.\/logic\/([A-Za-z0-9_-]+)'/g;
const OTHER_RELATIVE = /from '\.{1,2}\//;

function fail(message) {
  console.error(message);
  process.exit(2);
}
function inside(base, target) {
  const relative = path.relative(base, target);
  return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative);
}
function files(dir) {
  const result = [];
  for (const name of fs.readdirSync(dir).sort()) {
    const absolute = path.join(dir, name);
    const stat = fs.lstatSync(absolute);
    if (stat.isSymbolicLink()) fail('symlinks are not copied: ' + absolute);
    if (stat.isDirectory()) result.push(...files(absolute));
    else result.push(absolute);
  }
  return result;
}
function copy(from, to) {
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.copyFileSync(from, to);
}
const posix = value => value.split(path.sep).join('/');

if (inside(project, out) || path.resolve(project) === out) fail('the output directory must be outside project/');
const screens = JSON.parse(fs.readFileSync(path.join(__dirname, 'screens.json'), 'utf8'));
fs.mkdirSync(out, { recursive: true });
for (const [from, to] of [['src/logic', 'logic'], ['ui', 'ui']]) {
  for (const file of files(path.join(project, from))) copy(file, path.join(out, to, path.relative(path.join(project, from), file)));
}
const relocated = [];
for (const [source, entry] of Object.entries(screens.entries).sort()) {
  if (!/^project\/src\/pages\/[A-Za-z0-9_-]+\.tsx$/.test(source)) fail('unexpected page source: ' + source);
  const target = path.join(out, entry.conventionPath);
  if (!inside(out, target) || !/^[A-Za-z0-9_./-]+\.tsx$/.test(entry.conventionPath)) fail('unsafe convention path: ' + entry.conventionPath);
  let text = fs.readFileSync(path.join(root, source), 'utf8');
  text = text.replace(LOGIC_IMPORT, (_, name) => {
    let specifier = posix(path.relative(path.dirname(target), path.join(out, 'logic', name)));
    if (!specifier.startsWith('.')) specifier = './' + specifier;
    return `from '${specifier}'`;
  });
  const leftover = text.split('\n').filter(line => OTHER_RELATIVE.test(line) && !/from '\.[^']*\/logic\/[A-Za-z0-9_-]+'/.test(line));
  if (leftover.length) fail('unsupported relative import in ' + source);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, text);
  relocated.push(target);
}

const types = path.join(project, 'node_modules', '@types');
const conventionDir = path.join(out, 'convention');
fs.mkdirSync(conventionDir, { recursive: true });
const fromOut = target => posix(path.relative(out, target));
const tsconfig = {
  compilerOptions: {
    target: 'ES2020', module: 'ESNext', moduleResolution: 'Bundler', jsx: 'react-jsx',
    strict: true, esModuleInterop: true, skipLibCheck: true, noEmit: true,
    lib: ['ES2020', 'DOM', 'DOM.Iterable'], types: ['react', 'react-dom'],
    typeRoots: [posix(path.relative(conventionDir, types))],
    allowImportingTsExtensions: true, baseUrl: '..',
    paths: {
      '@studio/approved-ui': ['ui/index.tsx'],
      react: [fromOut(path.join(types, 'react'))], 'react/*': [fromOut(path.join(types, 'react')) + '/*'],
      'react-dom': [fromOut(path.join(types, 'react-dom'))], 'react-dom/*': [fromOut(path.join(types, 'react-dom')) + '/*'],
    },
  },
  include: ['../**/*.tsx', '../**/*.ts'],
  exclude: ['../convention'],
};
const config = path.join(conventionDir, 'tsconfig.json');
fs.writeFileSync(config, JSON.stringify(tsconfig, null, 2) + '\n');
let tsc;
try {
  tsc = require.resolve('typescript/bin/tsc', { paths: [project] });
} catch {
  fail('TypeScript is not installed: run npm ci inside project/ first');
}
const check = spawnSync(process.execPath, [tsc, '--noEmit', '-p', config, '--listFiles'], { encoding: 'utf8' });
process.stdout.write(check.stdout || '');
process.stderr.write(check.stderr || '');
for (const file of relocated) console.log('relocated: ' + posix(path.relative(out, file)));
process.exit(check.status === 0 ? 0 : 1);
