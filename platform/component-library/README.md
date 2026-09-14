# Portal component references

This private, project-owned TypeScript package supplies 22 reference versions for
the 12 named components in the synthetic ontology seed. Each ID maps to one
version-specific export in `catalog.json`. These implementations were authored
for the platform; they are not recovered historical customer `@atom/ui` packages.

Import a version explicitly from a local workspace dependency:

```tsx
import { useState } from 'react';
import { SelectV2 } from '@atom/portal-components';

export function Example() {
  const [value, setValue] = useState('');
  return <SelectV2 label="Example" value={value} onChange={setValue}
    options={[{ value: 'sample', label: 'Sample' }]} searchable />;
}
```

The consumer must compile TSX and CSS and provide React/ReactDOM 18.3.1.
This package is not published on npm. Its package version describes the
collection; `SelectV2` implements the catalog contract version `2.0.0`.
Individual Portal downloads include the selected entry and all declared shared
files. Preserve their paths and import the selected export directly from its
entry when copying those files into another project.

`index.ts` exports every version; `demos.tsx` contains synthetic local examples.
The browser never compiles downloaded source or executes user-supplied code.
The API and renderer independently compute source hashes; the Portal requires
their exact identity and hash to agree before showing the implementation.
Registry approval history, graph status, and customer release approvals are
separate from this source binding.

Shared files are part of every source fingerprint. Any shared change therefore
changes affected fingerprints even when an entry stays unchanged. Consumers
must pin the source hash or repository revision as well as the contract version.
New incompatible prop contracts require a new version export and catalog entry.

Validation uses `platform/web/test/component-library.test.cjs`,
`portal-versions*.test.cjs`, and `platform/tests/test_portal_visual.py`.
These check public types against Registry prop schemas, source closure, seed
coverage, API/build hash agreement, and real browser interaction.
