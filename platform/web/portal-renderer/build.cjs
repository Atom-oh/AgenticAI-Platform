const path = require('node:path');
const fs = require('node:fs/promises');
const { createHash } = require('node:crypto');
const { build } = require('esbuild');
const { literalLabelsPlugin } = require('./literal-labels.cjs');

/** Asset metadata never enters the build. Bundle the installed, lockfile-pinned runtime. */
async function buildRenderer({ write = true, outdir = path.join(__dirname, '../public/portal-renderers/mermaid') } = {}) {
  const mermaidVersion = JSON.parse(await fs.readFile(require.resolve('mermaid/package.json'), 'utf8')).version;
  if (!/^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(mermaidVersion)) throw new Error('Unsupported Mermaid package version');
  const result = await build({
    absWorkingDir: path.join(__dirname, '..'),
    entryPoints: [path.join(__dirname, 'runtime.ts')],
    bundle: true, splitting: false, write: false, minify: true, format: 'iife',
    platform: 'browser', target: ['es2022'], charset: 'utf8', legalComments: 'inline',
    metafile: true, define: { 'process.env.NODE_ENV': '"production"' },
    plugins: [literalLabelsPlugin(mermaidVersion)],
    // Escape script end tags in trusted dependency strings before HTML embedding.
    supported: { 'inline-script': false },
  });
  if (result.outputFiles.length !== 1 ||
      Object.values(result.metafile.outputs).some(output => output.imports.length !== 0)) {
    throw new Error('Renderer must be one standalone script without runtime imports');
  }
  const script = result.outputFiles[0].text;
  if (/<\/script/i.test(script)) throw new Error('Unsafe script serialization');
  const scriptHash = createHash('sha256').update(script).digest('base64');
  const policy = [
    "default-src 'none'", `script-src 'sha256-${scriptHash}'`, "script-src-attr 'none'",
    "style-src 'unsafe-inline'", 'img-src data:', "font-src 'none'", "connect-src 'none'",
    "frame-src 'none'", "object-src 'none'", "form-action 'none'", "base-uri 'none'",
    "worker-src 'none'", "media-src 'none'",
  ].join('; ');
  // The canvas grows with its SVG. Auto margins can center a small diagram, but
  // never place overflowing content at a negative, unscrollable left/top offset.
  const styles = 'html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#fff;color:#172033;font-family:system-ui,sans-serif}' +
    '#viewport{box-sizing:border-box;width:100%;height:100%;overflow:auto;overscroll-behavior:contain;padding:16px;outline-offset:-3px}' +
    '#diagram{display:flow-root;min-width:100%;width:max-content}#diagram svg{display:block;margin-inline:auto}' +
    '#scratch{position:absolute;visibility:hidden;pointer-events:none;left:0;top:0}#status{margin:0;padding:16px}';
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="referrer" content="no-referrer"><title>Portal diagram renderer</title><style>${styles}</style></head><body><p id="status" role="status">다이어그램을 준비하고 있습니다…</p><div id="viewport" role="region" aria-label="다이어그램 스크롤 영역" tabindex="0" hidden><div id="diagram"></div></div><div id="scratch" aria-hidden="true"></div><script>${script}</script></body></html>`;
  const sha256 = createHash('sha256').update(html).digest('hex');
  const manifest = { schemaVersion: 1, rendererVersion: 1, mermaidVersion,
    file: `mermaid-${mermaidVersion}-${sha256}.html`, sha256, bytes: Buffer.byteLength(html) };
  if (manifest.bytes > 8_000_000) throw new Error('Renderer exceeds client byte limit');
  if (write) {
    await fs.mkdir(outdir, { recursive: true });
    await fs.writeFile(path.join(outdir, manifest.file), html);
    // Publish the index only after the corresponding immutable HTML is complete.
    // Retain older hashes so in-flight clients never refer to a removed file.
    const temporary = path.join(outdir, `index.${process.pid}.tmp`);
    await fs.writeFile(temporary, JSON.stringify(manifest, null, 2) + '\n');
    await fs.rename(temporary, path.join(outdir, 'index.json'));
  }
  return { html, manifest };
}

module.exports = { buildRenderer };
if (require.main === module) {
  buildRenderer().then(({ manifest }) => console.log(`Portal Mermaid ${manifest.mermaidVersion}: ${manifest.file} (${manifest.bytes} bytes)`))
    .catch(error => { console.error(error); process.exitCode = 1; });
}
