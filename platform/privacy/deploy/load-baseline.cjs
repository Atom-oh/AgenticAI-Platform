const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

// Compile every relative source dependency from one immutable revision. HEAD's
// require cache is never used for those modules. Package dependencies are shared
// only after the caller verifies identical package/configuration locks.
function loadBaselineModule(entry, { root, readSource, compile, exists }) {
  const modules = new Map();
  const relativePath = value => {
    assert(typeof value === 'string', 'Unsupported baseline filesystem path');
    const relative = path.relative(root, path.resolve(value));
    assert(!relative.startsWith('..') && !path.isAbsolute(relative), 'Baseline read escaped repository');
    return relative;
  };
  const supportedFs = {
    readFileSync: (filename, options) => {
      const relative = relativePath(filename);
      const value = readSource(relative);
      assert(value !== null, `Missing baseline file: ${relative}`);
      const encoding = typeof options === 'string' ? options : options?.encoding;
      return encoding ? Buffer.from(value).toString(encoding) : Buffer.from(value);
    },
    existsSync: filename => {
      const relative = relativePath(filename);
      return exists ? exists(relative) : readSource(relative) !== null;
    },
  };
  const baselineFs = new Proxy(supportedFs, { get: (target, method) => {
    if (Object.hasOwn(target, method)) return target[method];
    if (method === '__esModule') return false;
    throw new Error(`Unsupported baseline filesystem operation: ${String(method)}`);
  } });
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative).exports;
    const source = readSource(relative);
    if (source === null) throw new Error(`Missing baseline source: ${relative}`);
    const filename = path.join(root, relative);
    const mod = new Module(filename, module);
    mod.filename = filename;
    mod.paths = Module._nodeModulePaths(path.dirname(filename));
    modules.set(relative, mod);
    const externalRequire = mod.require.bind(mod);
    mod.require = request => {
      if (request === 'fs' || request === 'node:fs') return baselineFs;
      if (!request.startsWith('.')) return externalRequire(request);
      const target = path.resolve(path.dirname(filename), request);
      const candidate = path.relative(root, target);
      assert(!candidate.startsWith('..') && !path.isAbsolute(candidate), 'Baseline import escaped repository');
      for (const suffix of ['', '.ts', '.js', '.cjs', '.json', '/index.ts', '/index.js']) {
        const name = candidate + suffix;
        const content = readSource(name);
        if (content !== null) {
          if (name.endsWith('.json')) return JSON.parse(content);
          return load(name);
        }
      }
      throw new Error(`Missing baseline dependency: ${candidate}`);
    };
    mod._compile(compile(source, relative), filename);
    return mod.exports;
  }
  return load(entry);
}

module.exports = { loadBaselineModule };
