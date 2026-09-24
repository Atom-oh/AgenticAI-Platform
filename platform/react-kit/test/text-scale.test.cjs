const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createRequire } = require('node:module');

// Engine plan Task E12a (V-02, review round 3 F11): every kit font-size scales with --studio-text-scale and the kit
// is unchanged at scale 1. The pre-change kit is reconstructed exactly (its sha256 is pinned below).
const root = path.resolve(__dirname, '..');
const local = createRequire(path.join(root, 'package.json'));
const { build } = local('esbuild');
const { chromium } = local('playwright');
const PRE_CHANGE_SHA256 = 'eb4c6a9ba551d25300bd6b4079c0c3ca2d071d409188cb87e2e805c0bb6879c8';
const SCALED = /calc\(var\(--studio-text-scale, 1\) \* ([0-9.]+px)\)/g;
const tokens = fs.readFileSync(path.join(root, 'ui/tokens.css'), 'utf8');
const previous = tokens.replace(SCALED, '$1');

test('every font-size token is scaled and unitless line-heights are unchanged', () => {
  assert.equal(createHash('sha256').update(previous).digest('hex'), PRE_CHANGE_SHA256, 'reconstruction of the pre-change kit');
  const sizes = [...tokens.matchAll(/font-size:\s*([^;}]+)/g)].map(match => match[1].trim());
  assert(sizes.length >= 19);
  for (const size of sizes) assert.match(size, /^calc\(var\(--studio-text-scale, 1\) \* [0-9.]+px\)$/);
  assert.match(tokens, /line-height: 1\.6/);
  assert.doesNotMatch(tokens, /line-height:\s*[0-9.]+px/);
});

async function page(browser, css) {
  const bundle = await build({
    stdin: { loader: 'tsx', resolveDir: root, contents: `
import {createRoot} from 'react-dom/client';
import {Screen,Stack,Grid,Inline,Panel,Text,Button,Input,Checkbox,Select,RadioGroup,Alert,Stepper,Summary} from './ui/index';
const noop=()=>{};
function App(){
  return <Screen pageId="scale" title="큰글씨 확인" width="mobile" testId="screen"><Stack>
    <Stepper current="a" steps={[{id:'a',label:'입력'},{id:'b',label:'확인'}]} testId="steps"/>
    <Text testId="text">본문 문장</Text><Text as="h2" testId="heading">부제목</Text><Text size="sm" testId="small">작은 글씨</Text>
    <Grid columns={2}><Panel title="패널" testId="panel"><Text>패널 본문</Text></Panel><Inline><Button label="다음" testId="button"/></Inline></Grid>
    <Input label="금액" value="100" onChange={noop} hint="안내" error="오류" testId="input"/>
    <Checkbox label="동의" checked={false} onChange={noop} testId="check"/>
    <Select label="주기" value="m" onChange={noop} options={[{value:'m',label:'매월'}]} testId="select"/>
    <RadioGroup label="방법" value="e" onChange={noop} options={[{value:'e',label:'이메일'}]} testId="radio"/>
    <Alert title="알림" message="안내 문구" testId="alert"/>
    <Summary title="요약" items={[{label:'금리',value:'표시값'}]} testId="summary"/>
  </Stack></Screen>;
}
createRoot(document.getElementById('root')).render(<App/>);` },
    bundle: true, write: false, outdir: '/virtual-scale', format: 'iife', jsx: 'automatic', loader: { '.css': 'empty' },
  });
  const js = bundle.outputFiles.find(file => file.path.endsWith('.js')).text;
  const context = await browser.newContext({ offline: true, viewport: { width: 390, height: 844 } });
  await context.route('**/*', route => route.fulfill({ contentType: 'text/html',
    body: '<html lang="ko"><head><title>scale</title></head><body><div id="root"></div></body></html>' }));
  const view = await context.newPage();
  await view.goto('https://kit.invalid/');
  await view.addStyleTag({ content: css });
  await view.addScriptTag({ content: js });
  await view.getByTestId('summary').waitFor();
  return { view, context };
}

const computed = view => view.evaluate(() => [...document.querySelectorAll('#root *')].map(element => {
  const style = getComputedStyle(element), all = {};
  for (let index = 0; index < style.length; index++) all[style[index]] = style.getPropertyValue(style[index]);
  return { tag: element.tagName, testId: element.getAttribute('data-testid'), all };
}));

test('computed styles at scale 1 equal the pre-change kit, and scale 2 doubles text and button sizes', { timeout: 60_000 }, async () => {
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true, args: ['--no-sandbox'] });
  try {
    const before = await page(browser, previous);
    const baseline = await computed(before.view);
    await before.context.close();
    const after = await page(browser, tokens);
    const current = await computed(after.view);
    assert.equal(current.length, baseline.length);
    assert(current.length > 40);
    for (let index = 0; index < current.length; index++) assert.deepEqual(current[index], baseline[index]);
    const size = testId => after.view.getByTestId(testId).evaluate(node => parseFloat(getComputedStyle(node).fontSize));
    const one = { text: await size('text'), button: await size('button'), heading: await size('heading'), small: await size('small') };
    await after.view.evaluate(() => document.documentElement.style.setProperty('--studio-text-scale', '2'));
    for (const [testId, value] of Object.entries(one)) assert.equal(await size(testId), value * 2, testId);
    await after.context.close();
  } finally { await browser.close(); }
});
