# Portal Mermaid renderer

`src/portal/DiagramPreview.tsx` exports a default and named `DiagramPreview` with
props `{ visual: PortalDiagram, title?: string, large?: boolean }`. `PortalDiagram` is exported
from that module and `src/portal/diagram.ts`. Its fields are data, never code:

```ts
type PortalDiagram = {
  kind: 'diagram';
  source: string;
  title: string;
  nodes: { id: string; label: string; type: string; assetId?: string; missing?: boolean }[];
  edges: { from: string; to: string; label: string; kind: 'sequence' | 'reference' }[];
  note: string;
  truncated: { nodes: number; edges: number };
};
```

## Application integration

The web package and Portal integrate the renderer as follows:

- Pin `mermaid` to `11.17.2` in dependencies.
- Pin `esbuild` to `0.25.12` in devDependencies.
- The `predev` and `prebuild` scripts run `node portal-renderer/build.cjs`.
  Run the command from `platform/web`; it also resolves its output relative
  to its own file.
- Serve `/portal-renderers/mermaid/index.json` and the corresponding immutable
  `mermaid-11.17.2-<sha256>.html`. Vite copies these public assets into its build.
  The application requires a secure context (HTTPS or localhost) for Web Crypto.

The builder writes only `public/portal-renderers/mermaid/`. It retains earlier
HTML hashes and replaces the index after writing the HTML, without deleting
directories or touching the separate React renderer. Build output is generated;
it does not need a TypeScript import or check-in.

The component supplies its own compact layout, expanded inline view, zoom/fit
buttons, loading/error status, retry, source disclosure, and truncation notice.
Its section exposes `data-diagram-state="loading|rendered|error"` for parent CSS
and browser assertions; the loading/error status remains available.
The graph is a relationship view, not executable UI or UX validation.
Metadata `source`, `assetId`, `type`, `title`, and `note` never cause navigation,
network requests, module imports, or code execution. Unverified references get a dashed
border; sequence edges are solid and reference edges dashed.

Procedure-step graphs use top-to-bottom orientation without changing nodes or
edges. Other sources keep left-to-right orientation. The fixed ontology theme
uses warm fills, dark amber borders/text, and 16px base type; metadata cannot
configure its CSS or theme. Fixed rank spacing is 24px.

Default scale never goes below 0.85 (13.6px base text); larger graphs scroll in a
keyboard-focusable viewport. Content starts at the top, and oversized content
never receives negative centering offsets. Explicit Fit can zoom out to show
an overview; manual zoom remains available. Pass `large` in the parent's native
dialog to use `min(65dvh, 760px)` height (minimum 240px), without enlarging the
iframe beyond its wrapper. The ordinary viewport stays 360px tall. Rebuild the
local Mermaid assets after changing runtime/layout code.

## Boundary

The client requests the fixed local index without credentials or redirects,
validates the pinned version and exact hash filename, bounds index/HTML bytes and elapsed
time, and verifies byte length and SHA-256 before setting `srcDoc`. The index
and HTML must be served from the trusted application origin: the hash detects
corrupt/mismatched bytes, not a compromise that can replace both files.

Only the trusted, bundled runtime script is authorized by the HTML's CSP hash.
Default, network connections, fonts, child frames, objects, forms, base URLs,
workers and media are blocked; styles may be inline and images may be data URLs.
The iframe uses `sandbox="allow-scripts"` and no `allow-same-origin`.
The client receives status only and never inserts SVG into the parent DOM.
The runtime also removes links, images, HTML, scripts and animation elements
before mounting its SVG.

Escape from the focused diagram emits a window/session-bound dismissal signal
to the optional `onEscape` callback. The Portal's full-window dialog uses it to
close and restore focus without granting same-origin access to the frame.

Every selection/source revision or retry mounts a fresh frame. The parent
removes the old frame during React's commit, before the next fetch effect.
A 256-bit nonce and 128-bit render ID bind commands/replies to that frame,
alongside source-window and origin checks. The runtime accepts one render,
then only bound zoom/fit commands. The client ignores stale responses and
removes the frame on an error or 25-second render deadline.

Raw Mermaid, frontmatter, directives, click handlers, and URL syntax are not an
input mode. Both parent and runtime validate the typed graph. The generator
uses stable `n0`, `n1`, ... IDs in input order and numeric-entity-encodes every
label code point. Graph IDs and asset metadata never become Mermaid syntax.

Limits: 80 nodes, 160 edges, nonempty unique IDs up to 500 UTF-16 units, labels
240, types 100, titles 300, sources 1000, notes 4000, and generated DSL 100,000
characters. Endpoints must identify supplied nodes; truncated counts must be
nonnegative safe integers. Newline/control characters in labels become spaces.
The component rejects excess input instead of silently changing its graph.
The index limit is 4 KiB; HTML is 8 MB; loading has a 15-second deadline.

## Pinned SVG label adapter

In Mermaid 11.17.2, the `htmlLabels: false` path leaves numeric entities visible
and measures their source text. `literal-labels.cjs` adapts the bundled SVG text
path to decode only our numeric-entity grammar before text measurement and
wrapping. It uses D3's text sink, without parsing labels as HTML or Markdown,
and disables subsequent entity decoding so entity-looking labels stay literal.

This adapter is build-local; it does not modify `node_modules` or package files.
It requires exactly Mermaid 11.17.2 and exactly one match for each affected
module. A dependency change fails the build until the adapter is reviewed.
It is intentionally unsuitable for a general-purpose Mermaid editor.

## Validation

With the web dependencies and Playwright Chromium already installed:

```bash
node --test test/portal-diagram*.test.cjs
npx tsc --noEmit
npx tsc --noEmit --target ES2022 --module ESNext --moduleResolution bundler --lib ES2022,DOM,DOM.Iterable --skipLibCheck --strict portal-renderer/runtime.ts
```

Tests build the real renderer in memory, avoiding writes to public assets.
Browser tests route all requests locally and fail if an external request
escapes the CSP. They cover hostile literal labels, text measurement, frame
isolation, view controls, mobile overflow, stale selections/replies, raw DSL
rejection, CSP enforcement, and integrity failure/retry. Unit tests cover DSL escaping/limits, message binding,
exact manifest validation and bounded/cancelled reads. The Portal routing suite
uses explicit React test doubles; the separate React renderer suite exercises
the real kit. Release QA also checks the combined interface in a browser.
The eight-step PRC-000 regression measures effective on-screen font size and
checks every node's scroll reachability in compact, phone, and native-dialog
views, plus oversized horizontal graphs and explicit overview/zoom controls.
