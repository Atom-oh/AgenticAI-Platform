'use strict';
// Source text is parsed as data. No imported module, config, loader or hook runs.
const path = require('node:path').posix;
const { createHash } = require('node:crypto');
const ts = require('typescript');
const postcss = require('postcss');
const valueParser = require('postcss-value-parser');
const { Parser: HTMLParser } = require('htmlparser2');

const LIMITS = Object.freeze({ files: 100, fileBytes: 102400, totalBytes: 2097152, references: 4000, nodes: 200000 });
const SHA = /^[a-f0-9]{64}$/;
const CODE = /\.(?:tsx?|jsx?)$/i;
const compare = (a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b));
const sha = data => createHash('sha256').update(data).digest('hex');
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort(compare)
    .map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
function safePath(value) {
  return typeof value === 'string' && value.length > 0 && Buffer.byteLength(value) <= 1024 &&
    value === value.normalize('NFC') && !/[\\:\x00-\x1f\x7f?#]/.test(value) &&
    value.split('/').every(part => part && part !== '.' && part !== '..') && !value.startsWith('/');
}
function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function fields(value, allowed) {
  if (!object(value) || Object.keys(value).some(k => !allowed.includes(k))) throw new Error('Invalid analysis fields');
}
function prepare(input) {
  fields(input, ['schemaVersion', 'files', 'resolver']);
  if (input.schemaVersion !== 1 || !Array.isArray(input.files) || input.files.length > LIMITS.files)
    throw new Error('Invalid analysis manifest');
  const files = new Map(), folded = new Set(); let bytes = 0;
  for (const file of input.files) {
    fields(file, ['path', 'sha256', 'text', 'kind']);
    if (!safePath(file.path) || typeof file.sha256 !== 'string' || !SHA.test(file.sha256) ||
        !['code', 'style', 'json', 'html', 'asset'].includes(file.kind) || files.has(file.path) ||
        folded.has(file.path.toLocaleLowerCase('en-US'))) throw new Error('Invalid or ambiguous source path');
    if (file.kind !== 'asset') {
      if (typeof file.text !== 'string' || Buffer.from(file.text).toString('utf8') !== file.text ||
          Buffer.byteLength(file.text) > LIMITS.fileBytes || sha(file.text) !== file.sha256)
        throw new Error('Source bytes do not match the manifest');
      bytes += Buffer.byteLength(file.text);
      if (bytes > LIMITS.totalBytes) throw new Error('Analysis byte limit exceeded');
    } else if (file.text !== undefined || !/\.(png|jpe?g|svg|woff2?)$/i.test(file.path))
      throw new Error('Asset manifests require a supported binary/resource extension');
    if (file.kind === 'code' && !CODE.test(file.path) || file.kind === 'style' && !/\.(css|scss)$/i.test(file.path) ||
        file.kind === 'json' && !file.path.toLowerCase().endsWith('.json') ||
        file.kind === 'html' && !/\.html?$/i.test(file.path)) throw new Error('Source kind does not match path');
    files.set(file.path, file); folded.add(file.path.toLocaleLowerCase('en-US'));
  }
  const resolver = input.resolver || { aliases: {}, packages: {}, jsonAssetFields: [] };
  fields(resolver, ['aliases', 'packages', 'jsonAssetFields']);
  if (!object(resolver.aliases || {}) || Object.keys(resolver.aliases || {}).length > 30 ||
      !object(resolver.packages || {}) || Object.keys(resolver.packages || {}).length > 30 ||
      !Array.isArray(resolver.jsonAssetFields || []) || (resolver.jsonAssetFields || []).length > 20)
    throw new Error('Invalid resolver profile');
  for (const [alias, target] of Object.entries(resolver.aliases || {})) {
    if (!alias || alias.length > 150 || (alias.match(/\*/g) || []).length > 1 ||
        typeof target !== 'string' || !safePath(target.replace('*', 'wildcard')) ||
        (target.match(/\*/g) || []).length > 1 || alias.includes('*') !== target.includes('*'))
      throw new Error('Invalid alias mapping');
  }
  for (const [name, spec] of Object.entries(resolver.packages || {})) {
    fields(spec, ['version', 'sha256']);
    if (!/^(?:@[a-z0-9._-]+\/)?[a-z0-9._-]+(?:\/[a-zA-Z0-9._/-]+)?$/.test(name) ||
        typeof spec.version !== 'string' || !spec.version || spec.version.length > 80 || !SHA.test(spec.sha256 || ''))
      throw new Error('Approved package identity is required');
  }
  if ((resolver.jsonAssetFields || []).some(k => typeof k !== 'string' || !/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(k)))
    throw new Error('Invalid JSON reference field');
  return { files, resolver, bytes };
}
function analyze(input) {
  const { files, resolver, bytes } = prepare(input);
  const references = [], exports = [], unresolved = [], diagnostics = [];
  let visited = 0, truncated = false;
  const addExport = value => {
    if (exports.length < LIMITS.references) exports.push(value);
    else truncated = true;
  };
  const virtual = new Map([...files.values()].filter(file => file.kind === 'code')
    .map(file => ['/analysis/' + file.path, ts.createSourceFile('/analysis/' + file.path, file.text, ts.ScriptTarget.Latest, true)]));
  const program = ts.createProgram([...virtual.keys()], {
    noLib: true, noResolve: true, noEmit: true, allowJs: true, jsx: ts.JsxEmit.Preserve,
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
  }, {
    getSourceFile: name => virtual.get(path.resolve('/analysis', name)),
    getDefaultLibFileName: () => '', getCurrentDirectory: () => '/analysis',
    getDirectories: () => [], getCanonicalFileName: name => name, useCaseSensitiveFileNames: () => true,
    getNewLine: () => '\n', fileExists: name => virtual.has(path.resolve('/analysis', name)),
    readFile: name => virtual.get(path.resolve('/analysis', name))?.text,
    writeFile: () => { throw new Error('The analyzer cannot emit files'); },
  });
  const checker = program.getTypeChecker();
  const problem = (file, reason, line = 1, column = 0) => {
    if (unresolved.length < LIMITS.references) unresolved.push({ path: file.path, sourceHash: file.sha256, line, column, reason });
    else truncated = true;
  };
  function resolve(from, specifier, resourceRelative = false) {
    if (typeof specifier !== 'string' || !specifier || specifier.length > 1024) return { status: 'unresolved', reason: 'invalid-reference' };
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(specifier)) return { status: 'unresolved', reason: 'external-url' };
    if (specifier.startsWith('#') && resourceRelative) return { status: 'local-fragment' };
    if (/[\\\x00-\x1f\x7f]/.test(specifier)) return { status: 'unresolved', reason: 'unsafe-reference' };
    const withoutQuery = specifier.startsWith('#') ? specifier : specifier.split(/[?#]/)[0];
    const candidates = [];
    if (withoutQuery.startsWith('.') || resourceRelative && !withoutQuery.startsWith('/'))
      candidates.push(path.normalize(path.join(path.dirname(from), withoutQuery)));
    else if (withoutQuery.startsWith('/')) return { status: 'unresolved', reason: 'root-url-needs-mapping' };
    else {
      for (const [alias, target] of Object.entries(resolver.aliases || {})) {
        const star = alias.indexOf('*');
        if (star < 0 && alias === withoutQuery) candidates.push(target);
        if (star >= 0 && withoutQuery.startsWith(alias.slice(0, star)) && withoutQuery.endsWith(alias.slice(star + 1))) {
          const captured = withoutQuery.slice(star, withoutQuery.length - (alias.length - star - 1));
          candidates.push(path.normalize(target.replace('*', captured)));
        }
      }
      if (!candidates.length && Object.hasOwn(resolver.packages || {}, withoutQuery))
        return { status: 'approved-package', package: withoutQuery, ...resolver.packages[withoutQuery] };
    }
    if (!candidates.length) return { status: 'unresolved', reason: 'unmapped-package-or-alias' };
    if (candidates.some(candidate => !safePath(candidate))) return { status: 'unresolved', reason: 'path-escape' };
    const found = new Set();
    for (const candidate of candidates) {
      const options = [candidate];
      if (!resourceRelative && !path.extname(candidate)) options.push(...['.ts', '.tsx', '.js', '.jsx', '.json'].map(ext => candidate + ext),
        ...['.ts', '.tsx', '.js', '.jsx'].map(ext => candidate + '/index' + ext));
      for (const option of options) if (files.has(option)) found.add(option);
    }
    if (found.size !== 1) return { status: 'unresolved', reason: found.size ? 'ambiguous-resolution' : 'missing-source' };
    const target = [...found][0];
    return { status: 'resolved-local', targetPath: target, targetHash: files.get(target).sha256,
      ...(specifier !== withoutQuery ? { transform: 'unverified-query-or-fragment' } : {}) };
  }
  function reference(file, kind, specifier, at, detail = {}) {
    if (references.length >= LIMITS.references) { truncated = true; return; }
    const resolution = resolve(file.path, specifier, kind.startsWith('style-') || kind === 'json-asset' || kind === 'html-resource' || kind === 'module-url');
    if (resolution.status === 'unresolved') problem(file, resolution.reason, at.line, at.column);
    if (resolution.transform) problem(file, resolution.transform, at.line, at.column);
    references.push({ path: file.path, sourceHash: file.sha256, kind, specifier, ...at, ...detail, resolution });
  }
  for (const file of [...files.values()].sort((a, b) => compare(a.path, b.path))) {
    if (file.kind === 'asset') continue;
    if (file.kind === 'code') {
      const source = program.getSourceFile('/analysis/' + file.path);
      const position = node => {
        const at = source.getLineAndCharacterOfPosition(node.getStart(source));
        return { line: at.line + 1, column: at.character };
      };
      if (source.parseDiagnostics.length) {
        diagnostics.push({ path: file.path, sourceHash: file.sha256, code: 'parse-error',
          count: source.parseDiagnostics.length });
        problem(file, 'parse-error'); continue;
      }
      const imports = new Map();
      for (const statement of source.statements) {
        if (ts.isImportEqualsDeclaration(statement)) {
          const module = statement.moduleReference;
          if (ts.isExternalModuleReference(module) && module.expression && ts.isStringLiteral(module.expression)) {
            const specifier = module.expression.text;
            reference(file, 'import-equals', specifier, position(statement), { typeOnly: !!statement.isTypeOnly });
            imports.set(checker.getSymbolAtLocation(statement.name), { specifier, symbol: '*' });
          } else problem(file, 'namespace-import-alias', position(statement).line, position(statement).column);
        }
        if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier)) {
          const specifier = statement.moduleSpecifier.text, clause = statement.importClause;
          reference(file, 'import', specifier, position(statement), { typeOnly: !!clause?.isTypeOnly });
          if (clause?.name) imports.set(checker.getSymbolAtLocation(clause.name), { specifier, symbol: 'default' });
          const bindings = clause?.namedBindings;
          if (bindings && ts.isNamespaceImport(bindings)) imports.set(checker.getSymbolAtLocation(bindings.name), { specifier, symbol: '*' });
          if (bindings && ts.isNamedImports(bindings)) for (const item of bindings.elements)
            imports.set(checker.getSymbolAtLocation(item.name), { specifier, symbol: item.propertyName?.text || item.name.text });
        }
        if (ts.isExportDeclaration(statement)) {
          if (statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier))
            reference(file, 're-export', statement.moduleSpecifier.text, position(statement), { typeOnly: !!statement.isTypeOnly });
          if (statement.exportClause && ts.isNamedExports(statement.exportClause))
            for (const element of statement.exportClause.elements) addExport({ path: file.path, name: element.name.text, ...position(element) });
        } else if (ts.isExportAssignment(statement)) {
          addExport({ path: file.path, name: statement.isExportEquals ? 'export=' : 'default', ...position(statement) });
          if (statement.isExportEquals) problem(file, 'commonjs-export-semantics', position(statement).line, position(statement).column);
        }
        else if (statement.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)) {
          const name = statement.modifiers.some(m => m.kind === ts.SyntaxKind.DefaultKeyword) ? 'default' : statement.name?.text;
          if (name) addExport({ path: file.path, name, ...position(statement) });
          if (ts.isVariableStatement(statement)) for (const declaration of statement.declarationList.declarations)
            if (ts.isIdentifier(declaration.name)) addExport({ path: file.path, name: declaration.name.text, ...position(declaration) });
        }
      }
      function importBinding(expression, seen = new Set()) {
        if (!expression || seen.size >= 16) return null;
        if (ts.isPropertyAccessExpression(expression)) {
          const base = importBinding(expression.expression, seen);
          return base?.symbol === '*' ? { ...base, symbol: expression.name.text } : null;
        }
        const symbol = checker.getSymbolAtLocation(expression);
        if (!symbol || seen.has(symbol)) return null;
        if (imports.has(symbol)) return imports.get(symbol);
        seen.add(symbol);
        const declaration = symbol.valueDeclaration;
        if (declaration && ts.isVariableDeclaration(declaration) &&
            declaration.parent.flags & ts.NodeFlags.Const)
          return importBinding(declaration.initializer, seen);
        return null;
      }
      function walk(node) {
        if (++visited > LIMITS.nodes) { truncated = true; return; }
        const globalName = (expression, name) => ts.isIdentifier(expression) && expression.text === name &&
          !checker.getSymbolAtLocation(expression)?.declarations?.length;
        const importMeta = expression => ts.isMetaProperty(expression) &&
          expression.keywordToken === ts.SyntaxKind.ImportKeyword && expression.name.text === 'meta';
        if (ts.isCallExpression(node) && node.expression.kind !== ts.SyntaxKind.ImportKeyword &&
            !globalName(node.expression, 'require') && !(ts.isPropertyAccessExpression(node.expression) &&
              globalName(node.expression.expression, 'require') && node.expression.name.text === 'resolve'))
          problem(file, 'call-semantics-not-inspected', position(node).line, position(node).column);
        if (ts.isNewExpression(node) && !globalName(node.expression, 'URL'))
          problem(file, 'constructor-semantics-not-inspected', position(node).line, position(node).column);
        if (ts.isNewExpression(node) && globalName(node.expression, 'URL')) {
          const [value, base] = node.arguments || [];
          if (value && ts.isStringLiteral(value) && base && ts.isPropertyAccessExpression(base) &&
              base.name.text === 'url' && importMeta(base.expression))
            reference(file, 'module-url', value.text, position(node));
          else problem(file, 'computed-url-reference', position(node).line, position(node).column);
        }
        if (ts.isNewExpression(node) && ['Worker', 'SharedWorker'].some(name => globalName(node.expression, name)))
          problem(file, 'worker-runtime-semantics', position(node).line, position(node).column);
        if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
          const expression = node.expression;
          if (ts.isIdentifier(expression.expression) && ['require', 'module'].includes(expression.expression.text) &&
              !(globalName(expression.expression, 'require') && expression.name.text === 'resolve'))
            problem(file, 'unmodeled-module-loader', position(node).line, position(node).column);
          if (globalName(expression.expression, 'require') && expression.name.text === 'resolve') {
            problem(file, 'runtime-module-resolution', position(node).line, position(node).column);
            if (node.arguments.length === 1 && ts.isStringLiteral(node.arguments[0]))
              reference(file, 'conditional-import', node.arguments[0].text, position(node), { conditional: true });
          }
          if (importMeta(expression.expression))
            problem(file, 'bundler-meta-transform', position(node).line, position(node).column);
        }
        if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'require' &&
            !globalName(node.expression, 'require'))
          problem(file, 'shadowed-or-ambient-module-loader', position(node).line, position(node).column);
        if (ts.isVariableDeclaration(node) && node.initializer && globalName(node.initializer, 'require'))
          problem(file, 'module-loader-alias', position(node).line, position(node).column);
        if (ts.isImportTypeNode(node)) {
          if (ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal))
            reference(file, 'type-import', node.argument.literal.text, position(node), { typeOnly: true });
          else problem(file, 'computed-type-import', position(node).line, position(node).column);
        }
        if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
            globalName(node.expression, 'require'))) {
          problem(file, 'dynamic-or-commonjs-dependency', position(node).line, position(node).column);
          if (node.arguments.length === 1 && ts.isStringLiteral(node.arguments[0]))
            reference(file, 'conditional-import', node.arguments[0].text, position(node), { conditional: true });
        }
        if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
          const tag = node.tagName.getText(source), member = tag.split('.').slice(1).join('.');
          let base = node.tagName;
          while (ts.isPropertyAccessExpression(base)) base = base.expression;
          const binding = importBinding(base);
          if (binding) reference(file, 'jsx-use', binding.specifier, position(node),
            { symbol: binding.symbol === '*' ? member || '*' : binding.symbol, localName: tag });
          else if (/^[A-Z]/.test(tag) || tag.includes('.'))
            problem(file, 'local-jsx-binding-not-traced', position(node).line, position(node).column);
          for (const attr of node.attributes.properties) {
            if (!ts.isJsxAttribute(attr) || !['src', 'href', 'poster'].includes(attr.name.getText(source))) continue;
            const init = attr.initializer, at = position(attr);
            if (init && ts.isStringLiteral(init) || init && ts.isJsxExpression(init) && init.expression && ts.isStringLiteral(init.expression))
              problem(file, 'runtime-url-base-not-configured', at.line, at.column);
            else if (init && ts.isJsxExpression(init) && init.expression && ts.isIdentifier(init.expression) &&
                imports.has(checker.getSymbolAtLocation(init.expression)))
              reference(file, 'asset-use', imports.get(checker.getSymbolAtLocation(init.expression)).specifier, at);
            else problem(file, 'computed-asset-reference', at.line, at.column);
          }
        }
        ts.forEachChild(node, walk);
      }
      walk(source);
    } else if (file.kind === 'style') {
      try {
        const css = postcss.parse(file.text, { from: file.path, map: false });
        css.walk(node => {
          if (++visited > LIMITS.nodes) { truncated = true; return false; }
          const at = { line: node.source?.start?.line || 1, column: Math.max(0, (node.source?.start?.column || 1) - 1) };
          if (node.type === 'comment' && /sourceMappingURL/.test(node.text || ''))
            problem(file, 'source-map-not-loaded', at.line, at.column);
          if (node.type === 'rule' && /[#$]\{/.test(node.selector || ''))
            problem(file, 'computed-style-reference', at.line, at.column);
          const value = node.type === 'decl' ? node.value : node.type === 'atrule' ? node.params : '';
          if (!value) return;
          if (/[#$]\{|\$[a-zA-Z_]/.test(value)) problem(file, 'computed-style-reference', at.line, at.column);
          const parsed = valueParser(value);
          if (node.type === 'atrule' && node.name.toLowerCase() === 'value' ||
              node.type === 'decl' && ['composes', 'compose-with'].includes(node.prop.toLowerCase())) {
            problem(file, 'css-module-transform', at.line, at.column);
            const tokens = parsed.nodes.filter(token => !['space', 'comment'].includes(token.type));
            if (tokens.at(-1)?.type === 'string' && tokens.at(-2)?.value === 'from')
              reference(file, 'style-import', tokens.at(-1).value, at);
          }
          parsed.walk(token => {
            if (token.type === 'function' && token.value.toLowerCase() === 'url') {
              const children = token.nodes.filter(n => !['space', 'comment'].includes(n.type));
              if (children.length === 1 && ['string', 'word'].includes(children[0].type) && !/[\\{}$]/.test(children[0].value))
                reference(file, node.type === 'atrule' && node.name.toLowerCase() === 'import' ? 'style-import' : 'style-asset', children[0].value, at);
              else problem(file, 'computed-style-reference', at.line, at.column);
              return false;
            }
          });
          if (node.type === 'atrule' && ['import', 'use', 'forward'].includes(node.name.toLowerCase())) {
            const first = parsed.nodes.find(n => !['space', 'comment'].includes(n.type));
            if (first?.type === 'string' && !/[\\{}$]/.test(first.value)) reference(file, 'style-import', first.value, at);
            if (node.name.toLowerCase() !== 'import') problem(file, 'scss-module-semantics', at.line, at.column);
          }
        });
      } catch { diagnostics.push({ path: file.path, code: 'style-parse-error' }); problem(file, 'style-parse-error'); }
    } else if (file.kind === 'html') {
      const found = [], stack = []; let base = false, attributes = new Set();
      const lineStarts = [0];
      for (let i = 0; i < file.text.length; i++) if (file.text[i] === '\n') lineStarts.push(i + 1);
      const at = index => {
        let low = 0, high = lineStarts.length - 1;
        while (low < high) { const mid = Math.ceil((low + high) / 2); if (lineStarts[mid] <= index) low = mid; else high = mid - 1; }
        return { line: low + 1, column: index - lineStarts[low] };
      };
      const parser = new HTMLParser({
        onopentagname() { attributes = new Set(); },
        onattribute(name) {
          if (attributes.has(name)) problem(file, 'duplicate-html-attribute');
          attributes.add(name);
        },
        onopentag(name, attrs) {
          if (++visited > LIMITS.nodes) { truncated = true; return; }
          stack.push(name);
          const position = at(Math.max(0, parser.startIndex));
          if (name === 'base' && attrs.href) { base = true; problem(file, 'html-base-url', position.line, position.column); }
          for (const key of ['src', 'href', 'poster', 'data', 'background', 'longdesc', 'cite']) if (attrs[key] && name !== 'base')
            found.push({ value: attrs[key], at: position });
          if (name === 'form' || attrs.action || attrs.formaction)
            problem(file, 'html-form-submission', position.line, position.column);
          if (name === 'object' || name === 'embed' || attrs.srcdoc || attrs.manifest || attrs.codebase || attrs.archive || attrs.ping)
            problem(file, 'html-active-content', position.line, position.column);
          if (name === 'meta' && String(attrs['http-equiv']).toLowerCase() === 'refresh')
            problem(file, 'html-navigation', position.line, position.column);
          if (attrs.srcset) problem(file, 'html-srcset-semantics', position.line, position.column);
          if (attrs.style) problem(file, 'inline-style-references', position.line, position.column);
          if (Object.keys(attrs).some(key => key.startsWith('on'))) problem(file, 'inline-script-not-executed', position.line, position.column);
        },
        onclosetag() { stack.pop(); },
        ontext(value) {
          if (value.trim() && ['script', 'style'].includes(stack.at(-1)))
            problem(file, stack.at(-1) === 'script' ? 'inline-script-not-executed' : 'inline-style-references');
        },
      }, { decodeEntities: true, lowerCaseTags: true, lowerCaseAttributeNames: true });
      parser.write(file.text); parser.end();
      if (base) problem(file, 'html-resource-base-unresolved');
      else for (const item of found) reference(file, 'html-resource', item.value, item.at);
    } else if (file.kind === 'json') {
      try {
        const data = JSON.parse(file.text);
        const keys = resolver.jsonAssetFields || [];
        if (!keys.length) problem(file, 'json-reference-profile-not-configured');
        for (const key of keys) {
          if (!object(data) || !Object.hasOwn(data, key)) continue;
          const entries = Array.isArray(data[key]) ? data[key] : [data[key]];
          for (const entry of entries) {
            if (typeof entry === 'string') reference(file, 'json-asset', entry, { line: 1, column: 0 }, { field: key });
            else problem(file, 'unsupported-json-reference');
          }
        }
      } catch { diagnostics.push({ path: file.path, code: 'json-parse-error' }); problem(file, 'json-parse-error'); }
    }
  }
  const result = { schemaVersion: 1, analyzer: { name: 'platform-source-analyzer', version: '1.0.0', typescript: ts.version },
    inputHash: sha(canonical(input.files.map(f => ({ path: f.path, sha256: f.sha256 })).sort((a, b) => compare(a.path, b.path)))),
    resolverHash: sha(canonical(resolver)), references, exports, unresolved, diagnostics,
    coverage: { scope: 'static-source-manifest', complete: !unresolved.length && !diagnostics.length && !truncated,
      runtimeComplete: false, truncated, files: files.size, bytes } };
  result.hash = sha(canonical(result));
  return result;
}
module.exports = { analyze, canonical, sha, safePath, LIMITS };
if (require.main === module) {
  const fs = require('node:fs');
  try {
    const raw = fs.readFileSync(process.argv[2]);
    if (raw.length > 4000000) throw new Error('Analysis request exceeds limit');
    const result = analyze(JSON.parse(raw.toString('utf8')));
    const output = JSON.stringify(result);
    if (Buffer.byteLength(output) > 4000000) throw new Error('Analysis result exceeds limit');
    fs.writeFileSync(process.argv[3], output);
  } catch {
    process.stderr.write('Source analysis failed; inspect validated input and limits.\n');
    process.exitCode = 1;
  }
}
