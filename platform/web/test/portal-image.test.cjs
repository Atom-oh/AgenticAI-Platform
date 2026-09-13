const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { transformSync } = require('esbuild');
const mod = { exports: {} };
new Function('module', 'exports', transformSync(fs.readFileSync(path.join(__dirname, '../src/portal/image-source.ts'), 'utf8'), {
  loader: 'ts', format: 'cjs',
}).code)(mod, mod.exports);
const { imageSource, imageDocument } = mod.exports;

test('image preview accepts actual imported images and explicit app static images', () => {
  assert.equal(imageSource({ imageDataUrl: 'data:image/png;base64,AA==' }, 'image').field, 'imageDataUrl');
  assert.equal(imageSource({ screenshot: '/studio-samples/demo/frame-1.svg' }, 'image').field, 'screenshot');
});
test('external, credential-bearing, API and traversal paths are not fetched', () => {
  for (const src of ['https://figma.com/x', '//example.com/x.png', 'javascript:alert(1)', '/api/reset.png',
    '/studio/assets/../private/x.png', '/samples/x.png?token=secret', '/samples/%2e%2e/x.png', 'data:text/html,<script>']) {
    assert.equal(imageSource({ src }, 'image'), null, src);
  }
});
test('image markup permits only its bound status reporter and escapes untrusted content', () => {
  const image = imageSource({ src: 'data:image/svg+xml,<svg onload="alert(1)"></svg>', alt: '"><script>bad</script>' }, 'image');
  const html = imageDocument(image, 'https://app.example', 'a'.repeat(32));
  assert.match(html, /script-src 'nonce-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'/);
  assert.equal((html.match(/<script nonce=/g) || []).length, 1);
  assert.match(html, /connect-src 'none'/);
  assert.equal(html.includes('<script>'), false);
  assert.match(html, /&quot;&gt;&lt;script&gt;/);
  assert.equal(imageSource({ src: 'data:image/png,' + 'x'.repeat(2_000_000) }, 'x'), null);
  assert.throws(() => imageDocument(image, 'https://app.example', 'invalid'), /binding/);
});
