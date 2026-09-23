const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

// Compile every relative source dependency from one immutable revision. HEAD's
// require cache is never used for those modules. Package dependencies are shared
// only after the caller verifies identical package/configuration locks.
function loadBaselineModule(entry, { root, readSource, compile }) {
  const modules = new Map();
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
