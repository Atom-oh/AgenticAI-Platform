const fs = require('node:fs/promises');

/**
 * Mermaid 11.17.2's SVG label path decodes only &amp;/&lt;/&gt;, leaving
 * numeric entities visible and measuring the entity source instead of the text.
 * Adapt this pinned path for our numeric-only, non-Markdown label grammar.
 * Decoding happens once before wrapping/measurement, and reaches D3 .text(),
 * never an HTML sink. Do not apply this adapter to a general Mermaid editor.
 */
function literalLabelsPlugin(version) {
  if (version !== '11.17.2') throw new Error('Review the Portal SVG label adapter before changing Mermaid 11.17.2');
  let labelModules = 0, cleanupModules = 0;
  const plugin = {
    name: 'portal-literal-svg-labels',
    setup(build) {
      build.onLoad({ filter: /mermaid[/\\]dist[/\\].*\.mjs$/ }, async ({ path }) => {
        let source = await fs.readFile(path, 'utf8');
        if (source.includes('function nonMarkdownToLines(nonMarkdownText) {')) {
          const labelFunction = /function nonMarkdownToLines\(nonMarkdownText\) \{[\s\S]*?\n\}/;
          const decodeFunction = /function decodeHTMLEntities\(text\) \{[\s\S]*?\n\}/;
          if (!labelFunction.test(source) || !decodeFunction.test(source)) throw new Error('Mermaid SVG label adapter mismatch');
          source = source.replace(labelFunction, `function nonMarkdownToLines(nonMarkdownText) {
  if (!/^(?:&#[0-9]+;)*$/.test(nonMarkdownText)) throw new Error("Nonliteral Portal label");
  const literal = nonMarkdownText.replace(/&#([0-9]+);/g, (_, digits) => String.fromCodePoint(Number(digits)));
  return [[{content: literal, type: "normal"}]];
}`);
          // Input such as the literal string "&amp;" must not be decoded again.
          source = source.replace(decodeFunction, 'function decodeHTMLEntities(text) { return text; }');
          labelModules++;
          return { contents: source, loader: 'js' };
        }
        if (source.includes('cleanedUpSvg = decodeEntities(cleanedUpSvg);')) {
          // Our labels have already been decoded into DOM text. Avoid re-decoding
          // user text that happens to match Mermaid's internal entity sentinels.
          source = source.replace('cleanedUpSvg = decodeEntities(cleanedUpSvg);',
            '// Portal: literal labels were decoded before text measurement.');
          cleanupModules++;
          return { contents: source, loader: 'js' };
        }
        return null;
      });
      build.onEnd(() => {
        if (labelModules !== 1 || cleanupModules !== 1) {
          return { errors: [{ text: 'Mermaid SVG label adapter did not match exactly once' }] };
        }
      });
    },
  };
  return plugin;
}

module.exports = { literalLabelsPlugin };
