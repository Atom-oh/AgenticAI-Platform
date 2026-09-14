# Design asset previews

The `#/portal` page separates executable platform components from ontology
metadata. A visible preview is not an approval or a successful UX execution test.

## Designer workflow

- **Components → Studio UI kit:** browse the current platform kit, select a
  component and change its example controls. Inputs, selections and callbacks
  execute the original `platform/react-kit/ui` code. The kit is a platform sample,
  not an approved customer package. Its version and source hash identify the
  implementation being displayed.
- **Components → Versioned components:** inspect existing ontology entries and their
  relationships. The 22 named seed versions have exact-ID reference implementations
  in `platform/component-library`, under `@atom/portal-components`. Each version
  has a separate export, interactive example, source files, props, and change notes.
  These newly authored platform references are not historical customer `@atom/ui`
  package contents. Existing Registry approvals are not changed or applied to
  these implementations. The 58 generated volume-test widgets remain explicit
  metadata placeholders. Unknown or mismatched identities remain unlinked;
  matching a Studio kit component name alone never creates a binding.
- **User flows:** view recorded procedure steps in their stored order. Exact,
  unique screen-name matches may link to the corresponding screen asset.
- **Screens:** view registered previous/next screen relationships. An unresolved
  reference remains visible and is marked; missing screenshots are not replaced
  with invented page layouts.
- **Patterns / business rules:** view composition, application and reference
  relationships. These are distinct from executable screen transitions.
- **Images:** display connected imported image bytes or permitted static app image
  paths. External URLs, including Figma URLs, are metadata only. The preview
  does not download external originals.
- **UX terms / common terms:** read the registered explanation and follow stored
  outgoing `USED_IN` links to screens. A term does not require a graphical preview.
  Missing approval metadata is not presented as a draft; absent version/owner
  badges are omitted. Explanations are displayed verbatim, not rewritten or
  promoted to approved business guidance. Usage lists deduplicate returned screen
  IDs, disclose truncated samples and do not imply a comparison with live copy.
  Raw properties and relationship data remain in a secondary disclosure; term
  refresh reloads the source without offering an unsupported Registry publish action.

Diagrams support zoom, fit and an expanded view. The Portal also provides a
full-window dialog and links to the graph assets. Properties, version history,
Registry operations and impact analysis remain available in the details
disclosure. Sync refreshes the selected detail and its visual projection.

## Data contract

`portal_detail` adds `visual`; component cards also expose `implementationStatus`
(`reference`, `placeholder`, or `unlinked`). Diagram projections are bounded to
25 nodes and 50 edges:

```ts
type Visual =
  | {
      kind: 'diagram';
      source: string;
      title: string;
      nodes: { id: string; label: string; type: string; assetId?: string; missing?: boolean }[];
      edges: { from: string; to: string; label: string; kind: 'sequence' | 'reference' }[];
      note: string;
      truncated: { nodes: number; edges: number };
    }
  | { kind: 'empty'; reason: string; note: string }
  | {
      kind: 'react-component'; id: string; name: string; version: string;
      package: '@atom/portal-components'; exportName: string; sourceHash: string;
    };
```

The backend reuses the existing detail neighborhood reads. Screen names use at
most one additional Neptune query for up to 24 referenced IDs; local mode uses
bounded in-memory lookups. Ordered steps are
not inferred from unordered `INCLUDES` edges. Truncation and incomplete neighbor
coverage are disclosed; missing conditions, success paths or validations are
not invented. The frontend generates a restricted Mermaid flowchart from this
typed graph; arbitrary Mermaid source is not an input mode.

The React renderer manifest comes from the real kit's `manifest.cjs`. The client
checks the local manifest, size limits, HTML hash and renderer message binding.
Catalog identity is exposed only after the corresponding HTML is verified;
both metadata and previews use that same cached revision snapshot.
Examples are local, synthetic and reset on selection; they do not initiate
transactions or update approved assets.

Versioned references use a separate catalog and renderer. API bindings require
exact node ID, name, and semantic version. The source hash is SHA-256 of compact
UTF-8 JSON in this key order:
`{id,version,package,exportName,files:[{path,sha256}]}`.
Files comprise the entry and catalog `sharedFiles`, deduplicated and sorted by
path. Both the API deployment and web build hash their actual file bytes. The
browser verifies each downloadable source, aggregate identity, catalog hash,
and HTML hash, then requires a match to the API binding before displaying it.
A stale or mismatched deployment produces an error, not a name-based fallback.
Source JSON downloads retain paths and hashes; individual files are also
available as text. The local package is not claimed to be published on npm.

## Rendering boundary

React and Mermaid are bundled locally into separate immutable HTML files under
`public/portal-renderers/{react,mermaid,versions}/`. Build commands run automatically
through the web package's `predev` and `prebuild` scripts. Deploy their manifests
and referenced HTML before publishing the new web index; retain older hashes
for existing clients.
The API package must include `component-library/catalog.json` and `component-library/ui/`.
Do not reseed Registry or graph data to deploy a source binding.

Preview frames use `sandbox="allow-scripts"` without `allow-same-origin`.
Restrictive CSP blocks network connections, child frames, objects, forms and
external resources. Trusted renderer scripts use an exact CSP hash. Frame
messages must match the expected window and session binding. Neither arbitrary
asset JavaScript nor SVG is inserted into the parent application.

Image frames accept imported data URLs or image paths under `/samples/`,
`/studio-samples/` and `/studio/assets/`; arbitrary application/API paths are
rejected. Their nonce-bound script reports image decoding status only.

Mermaid is pinned to `11.17.2` for the existing Node 20 web build. Its build-local
literal-label adapter is version-bound and must be reviewed on upgrades. See
[`portal-renderer/README.md`](../web/portal-renderer/README.md) for details.

## Verification

From the repository root, with the pinned dependencies installed:

```sh
python3 -m pytest platform/tests/test_portal.py platform/tests/test_portal_visual.py -q
cd platform/web
npm run build
node --test test/portal-*.test.cjs
```

Browser checks must include real component interactions, image success/failure,
diagram rendering and navigation, stale-response handling, CSP isolation and
desktop/mobile containment. These checks establish preview behavior, not the
business correctness or approval of a generated product flow.
