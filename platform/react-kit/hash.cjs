'use strict';
// Fixed helper for the offline engine runner: prints the pinned kit catalog hash computed by manifest.cjs.
// Usage: node hash.cjs <ignored-request.json> <output.json>
const fs = require('node:fs');
const { catalog } = require('./manifest.cjs');

if (!process.argv[3]) throw new Error('Usage: node hash.cjs request.json output.json');
fs.writeFileSync(process.argv[3], JSON.stringify({ hash: catalog().hash }));
