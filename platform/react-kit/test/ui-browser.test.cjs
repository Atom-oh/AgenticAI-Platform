const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

const root = path.resolve(__dirname, '..');
const local = createRequire(path.join(root, 'package.json'));
const existing = createRequire(path.resolve(root, '../web/package.json'));
const dependencies = ['react', 'react-dom/client', 'esbuild', 'playwright'];
const runtime = dependencies.every(name => { try { local.resolve(name); return true; } catch { return false; } }) ? local : existing;
const { build } = runtime('esbuild');
const { chromium } = runtime('playwright');

test('real React kit renders accessible native controls, events, locked styling and responsive layouts', { timeout: 60_000 }, async t => {
  const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=';
  const bundle = await build({
    stdin: { loader: 'tsx', resolveDir: root, contents: `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Screen,Stack,Grid,Inline,Panel,Text,Button,Input,Checkbox,Select,RadioGroup,Alert,Stepper,Summary,AssetImage} from './ui/index';
function App(){
  const [amount,setAmount]=useState(''),[consent,setConsent]=useState(false),[period,setPeriod]=useState('monthly');
  const [channel,setChannel]=useState('email'),[step,setStep]=useState('entry'),[submits,setSubmits]=useState(0);
  const error=amount!==''&&(Number(amount)<1||Number(amount)>100)?'금액은 1부터 100까지 입력하세요.':'';
  return <Screen pageId={step} title="플랫폼 기본 가입 화면" width="wide" testId="screen">
    <Stack gap={6} testId="stack">
      <Stepper current={step} steps={[{id:'entry',label:'정보 입력'},{id:'confirm',label:'입력 확인'}]} testId="steps"/>
      <Text tone="muted">플랫폼 기본 React 컴포넌트 · 실제 거래가 아닌 합성 입력 예시</Text>
      <Grid columns={2} gap={6} testId="grid">
        <Panel title="가입 정보"><Stack gap={4}>
          <Input id="amount-field" testId="amount" label="납입금액" type="number" value={amount}
            onChange={value=>{window.amountType=typeof value;setAmount(value)}} required min={1} max={100} hint="1~100 사이의 금액" error={error}/>
          <Select label="납입 주기" testId="period" value={period}
            onChange={value=>{window.selectType=typeof value;setPeriod(value)}} options={[{value:'monthly',label:'매월'},{value:'yearly',label:'매년'}]}/>
          <RadioGroup label="안내 방법" testId="channel" value={channel}
            onChange={value=>{window.radioType=typeof value;setChannel(value)}} options={[{value:'email',label:'이메일'},{value:'sms',label:'문자'}]}/>
          <Checkbox label="필수 안내에 동의합니다" testId="consent" checked={consent} required
            onChange={value=>{window.checkType=typeof value;setConsent(value)}}/>
          <Inline gap={3} justify="between" testId="actions">
            <Button label="입력 확인" testId="continue" disabled={!amount||!consent||!!error}
              onClick={function(){window.buttonArgs=arguments.length;setStep('confirm')}}/>
            <Button label="처음으로" kind="secondary" testId="reset" onClick={()=>setStep('entry')}/>
          </Inline>
        </Stack></Panel>
        <Panel title="입력한 내용" tone="subtle"><Stack gap={4}>
          <Summary title="확인 요약" testId="summary" items={[{label:'금액',value:amount?amount+'원':'미입력'},
            {label:'납입 주기',value:period==='monthly'?'매월':'매년'},{label:'안내 방법',value:channel==='email'?'이메일':'문자'},
            {label:'동의',value:consent?'동의함':'미동의'}]}/>
          <Alert title="시뮬레이션" message="실제 금융 API에 연결하지 않습니다." tone="info" testId="alert"/>
          <AssetImage src="${image}" alt="반입 기준 이미지" width={48} height={48} testId="image"/>
        </Stack></Panel>
      </Grid>
      <Panel title="버튼 기본 동작"><form onSubmit={event=>{event.preventDefault();setSubmits(value=>value+1)}}>
        <Inline><Button label="일반 버튼" testId="plain"/><Button label="제출 버튼" type="submit" testId="submit"/></Inline>
        <Text testId="submitted">제출 횟수 {submits}</Text>
      </form></Panel>
    </Stack>
  </Screen>;
}
createRoot(document.getElementById('root')).render(<App/>);` },
    bundle: true, write: false, outdir: '/virtual-studio-ui', format: 'iife', jsx: 'automatic',
    nodePaths: [path.dirname(path.dirname(runtime.resolve('react/package.json')))],
  });
  const js = bundle.outputFiles.find(file => file.path.endsWith('.js')).text;
  const css = bundle.outputFiles.find(file => file.path.endsWith('.css')).text;
  const browser = await chromium.launch({ executablePath: process.env.WORKSPACE_CHROMIUM, headless: true,
    args: ['--no-sandbox', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'] });
  try {
    const context = await browser.newContext({ offline: true, viewport: { width: 1440, height: 1000 } });
    const outside = [], errors = [];
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', route => {
      if (route.request().url() !== 'https://kit.invalid/') { outside.push(route.request().url()); return route.abort(); }
      return route.fulfill({ contentType: 'text/html', body: '<html lang="ko"><head><title>React kit offline test</title></head><body><div id="root"></div></body></html>' });
    });
    const page = await context.newPage(); page.setDefaultTimeout(5000);
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto('https://kit.invalid/');
    await page.addStyleTag({ content: css });
    await page.addScriptTag({ content: js });
    await page.getByRole('main', { name: '플랫폼 기본 가입 화면', exact: true }).waitFor();
    const names = await page.locator('[data-studio-component]').evaluateAll(nodes => [...new Set(nodes.map(node => node.dataset.studioComponent))].sort());
    assert.deepEqual(names, ['Screen', 'Stack', 'Grid', 'Inline', 'Panel', 'Text', 'Button', 'Input', 'Checkbox', 'Select', 'RadioGroup', 'Alert', 'Stepper', 'Summary', 'AssetImage'].sort());
    assert(await page.locator('[data-studio-component]').evaluateAll(nodes => nodes.every(node => node.dataset.studioVersion === '1.0.0')));
    const amount = page.getByRole('spinbutton', { name: '납입금액', exact: true });
    assert.equal(await amount.getAttribute('data-testid'), 'amount');
    assert.equal(await amount.getAttribute('required'), '');
    assert.equal(await page.getByTestId('continue').isDisabled(), true);
    await page.getByTestId('continue').evaluate(button => button.click());
    assert.equal(await page.evaluate(() => window.buttonArgs), undefined);
    await amount.fill('150');
    await page.getByRole('alert').getByText('금액은 1부터 100까지 입력하세요.', { exact: true }).waitFor();
    assert.equal(await amount.getAttribute('aria-invalid'), 'true');
    assert.equal(await amount.evaluate(input => input.validity.rangeOverflow), true);
    assert(await amount.evaluate(input => input.getAttribute('aria-describedby').split(' ').every(id => document.getElementById(id))));
    await amount.fill('50');
    assert.equal(await amount.getAttribute('aria-invalid'), null);
    await page.getByRole('checkbox', { name: '필수 안내에 동의합니다', exact: true }).check();
    await page.getByRole('combobox', { name: '납입 주기', exact: true }).selectOption('yearly');
    await page.getByRole('radio', { name: '이메일', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.getByRole('radio', { name: '문자', exact: true }).isChecked(), true);
    assert.equal(await page.getByRole('radiogroup', { name: '안내 방법', exact: true }).getAttribute('data-testid'), 'channel');
    assert.equal(await page.getByTestId('channel-sms').isChecked(), true);
    const primary = page.getByTestId('continue');
    assert.equal(await primary.isDisabled(), false);
    assert.equal(await primary.evaluate(button => getComputedStyle(button).backgroundColor), 'rgb(0, 132, 133)');
    await primary.focus();
    assert.equal(await primary.evaluate(button => getComputedStyle(button).outlineWidth), '3px');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.buttonArgs), 0);
    assert.equal(await page.getByTestId('screen').getAttribute('data-page-id'), 'confirm');
    assert.match(await page.locator('[aria-current="step"]').innerText(), /입력 확인/);
    assert.match(await page.getByTestId('summary').innerText(), /50원/);
    assert.match(await page.getByTestId('summary').innerText(), /매년/);
    assert.match(await page.getByTestId('summary').innerText(), /문자/);
    assert.deepEqual(await page.evaluate(() => [window.amountType, window.checkType, window.selectType, window.radioType]), ['string', 'boolean', 'string', 'string']);
    await page.getByTestId('plain').click();
    assert.equal(await page.getByTestId('submitted').innerText(), '제출 횟수 0');
    await page.getByTestId('submit').click();
    assert.equal(await page.getByTestId('submitted').innerText(), '제출 횟수 1');
    assert.equal(await page.getByTestId('image').evaluate(image => image.naturalWidth), 1);
    for (const width of [320, 390, 768, 1440, 2560, 3440]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(await amount.inputValue(), '50');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `viewport ${width}`);
      if (process.env.STUDIO_UI_QA_DIR && [390, 1440].includes(width)) {
        fs.mkdirSync(process.env.STUDIO_UI_QA_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.STUDIO_UI_QA_DIR, `kit-${width}.png`), fullPage: true });
      }
    }
    const ids = await page.locator('[id]').evaluateAll(nodes => nodes.map(node => node.id));
    assert.equal(new Set(ids).size, ids.length);
    assert.deepEqual(outside, []);
    assert.deepEqual(errors, []);
    t.diagnostic('Real React 18.3.1 kit: native value/boolean/no-event callbacks, keyboard radio/button behavior, labels/errors, primary token, focus and 320–3440 layouts verified.');
  } finally { await browser.close(); }
});
