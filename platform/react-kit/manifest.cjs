const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');

function catalog() {
  const root = __dirname;
  const descriptorBytes = fs.readFileSync(path.join(root, 'catalog.json'));
  const descriptor = JSON.parse(descriptorBytes.toString('utf8'));
  const files = [{ path: 'catalog.json', sha256: sha256(descriptorBytes) }];
  function walk(relative) {
    for (const entry of fs.readdirSync(path.join(root, relative), { withFileTypes: true })) {
      const name = `${relative}/${entry.name}`;
      if (entry.isSymbolicLink()) throw new Error(`Catalog sources cannot be symbolic links: ${name}`);
      if (entry.isDirectory()) walk(name);
      else if (entry.isFile()) files.push({ path: name, sha256: sha256(fs.readFileSync(path.join(root, name))) });
      else throw new Error(`Unsupported catalog source: ${name}`);
    }
  }
  walk('ui');
  files.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  // Fixed object keys + sorted paths are canonical, with no timestamps or host paths.
  return { ...descriptor, hash: sha256(Buffer.from(JSON.stringify(files), 'utf8')), files };
}

module.exports = { catalog };
