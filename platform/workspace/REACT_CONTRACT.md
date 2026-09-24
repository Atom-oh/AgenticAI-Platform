# React workspace implementation interfaces

Current code audit: 2026-09-13. Follow [root instructions](../../AGENTS.md),
[review context](../../docs/REVIEW_CONTEXT.md), and [SPEC.md](../../SPEC.md) §7-1.
Extends [CONTRACT.md](CONTRACT.md); older personal HTML runs remain prototypes.
Current React deliverables use the pinned package and verified release path.
This is an implemented interface contract, not the earlier task ownership plan.
Paths below are relative to `platform/` unless stated otherwise.

## React kit

Directory `react-kit/`. Node/CommonJS tooling; React source is TypeScript/TSX.
Exact versions: React/ReactDOM 18.3.1, TypeScript 5.6.3, esbuild 0.25.12,
@types/react 18.3.31, @types/react-dom 18.3.7, Playwright 1.62.0.
`package-lock.json` is committed. No runtime npm install.

- `ui/index.tsx`: real named exports listed below; generated code imports from `@studio/approved-ui`.
- `ui/tokens.css`: authoritative styles/tokens. Generated code cannot replace this file or supply CSS.
  Every `font-size` is `calc(var(--studio-text-scale, 1) * <n>px)`; unitless line-heights are unchanged, so the kit
  is identical at scale 1. Only the verifier sets `--studio-text-scale` (engine plan E12a).
- `catalog.json`: `{schemaVersion:1,id:"studio-ui",version:"1.2.0",label:"플랫폼 기본 React 컴포넌트",components:[...]}`.
  Each component has `{name,description,props,variationAxes}`. `props` is descriptive data; actual TS types are the compiler authority.
- `manifest.cjs`: `catalog()` returns descriptor plus `{hash,files:[{path,sha256}]}` calculated from actual `ui/*` and catalog bytes.
  `hash` is SHA256 of canonical JSON of sorted file hashes. It excludes generated timestamps and node_modules.
- `compile.cjs`: `buildProject({files,assets,expectedCatalogHash,contract})` returns the result below.
  CLI: `node compile.cjs request.json output.json`, all model input treated as data.

Shared component props: `testId?:string` becomes data-testid; `id?:string`; no caller-provided className, style, ref or raw HTML.
Components include `data-studio-component` and `data-studio-version` on their root.

| Export | Required/important props |
|---|---|
| Screen | `pageId:string`, `children:ReactNode`, `width?:"mobile"\|"content"\|"wide"`, `title?:string` |
| Stack | `children`, `gap?:1\|2\|3\|4\|6\|8` |
| Grid | `children`, `columns?:1\|2\|3`, `gap?:1\|2\|3\|4\|6\|8` |
| Inline | `children`, `gap?:1\|2\|3\|4\|6`, `align?:"start"\|"center"\|"end"`, `justify?:"start"\|"between"\|"end"` |
| Panel | `children`, `title?:string`, `tone?:"default"\|"subtle"\|"brand"` |
| Text | `children`, `as?:"span"\|"p"\|"h1"\|"h2"\|"h3"`, `size?:"sm"\|"md"\|"lg"`, `tone?:"default"\|"muted"\|"brand"\|"danger"` |
| Button | `label:string`, `onClick?:()=>void`, `kind?:"primary"\|"secondary"\|"danger"`, `disabled?:boolean`, `type?:"button"\|"submit"` |
| Input | `label:string`, `value:string`, `onChange:(value:string)=>void`, `type?:"text"\|"number"\|"email"\|"tel"`, `hint?:string`, `error?:string`, `required?:boolean`, `disabled?:boolean`, `min?:number`, `max?:number`, `placeholder?:string` |
| Checkbox | `label:string`, `checked:boolean`, `onChange:(checked:boolean)=>void`, `disabled?:boolean`, `required?:boolean` |
| Select | `label:string`, `value:string`, `onChange:(value:string)=>void`, `options:{value:string,label:string}[]`, `disabled?:boolean` |
| RadioGroup | `label:string`, `value:string`, `onChange:(value:string)=>void`, `options:{value:string,label:string}[]` |
| Alert | `message:string`, `title?:string`, `tone?:"info"\|"success"\|"warning"\|"danger"` |
| Stepper | `steps:{id:string,label:string}[]`, `current:string` |
| Summary | `title?:string`, `items:{label:string,value:string}[]` |
| AssetImage | `src:string` (data image only), `alt:string`, `width?:number`, `height?:number` |

Layout widths, spacing, typography, colors, disabled/error/focus states and accessibility belong to the kit.
Button primary enabled background initially `#008485`, following the current platform brand.
The kit is a platform baseline, not an invented claim about a customer's approved package.

## Compiler result and isolation

Generated `files` is a map of UTF-8 source text: `src/App.tsx`, optional `src/pages/<safe-name>.tsx`
and `src/logic/<safe-name>.ts`. No config/package/scripts/kit files from the model.
Total source <=128KiB, at most24 files, normalized relative paths only.
`assets` is a map of opaque asset IDs to local data-image URIs assembled by the server.
Generated code may import React named hooks (useState/useReducer/useMemo/useCallback) and types, the kit,
and relative generated modules. It cannot import arbitrary packages, use JSX spreads, intrinsic interactive HTML,
style/script/raw HTML, dynamic imports, eval/Function, direct DOM/network/storage APIs or mutation of the kit.
This static policy supplements the isolated browser; it is not described as a general JavaScript sandbox.

```json
{
  "ok": true,
  "gates": {"policy":{"status":"pass"},"types":{"status":"pass"},"build":{"status":"pass"},"components":{"status":"pass"}},
  "diagnostics": [],
  "catalogHash": "sha256",
  "sourceHash": "sha256",
  "bundleHash": "sha256",
  "files": {"index.html":"base64","assets/app.js":"base64","assets/app.css":"base64"},
  "projectZipBase64": "...",
  "distZipBase64": "...",
  "previewHtml": "...",
  "pageSources": [{"pageId":"entry","path":"src/pages/entry.tsx"}]
}
```

Hashes cover canonical sorted path+byte hashes. ZIP timestamps/order are fixed for reproducibility.
Source ZIP includes actual kit files, source, trusted package/lock/config/build scripts, approved test contract and tests.
Dist has only local paths; no outside fonts/scripts/images. Preview is a derivative of the same JS/CSS, never a separate AI generation.
Compilation does not execute generated module code, user configuration or package hooks.

A `kind="react"` task runs compilation in the credential-free browser child, then
the behavioral/a11y/image verifier checks the actual static bundle. The response contains build fields and browser report.
`evaluate_html` remains compatible; `evaluate_bundle` fulfills only exact local bundle URLs in memory and denies every other request.
`evaluate_html`/`evaluate_bundle(..., text_scale=1.0)`: a scale in 1..3 other than 1 sets `--studio-text-scale` through the
verifier's init script and adds `largeText: {status, overflow:[testId], unscaled:[testId]}` (tagged elements that overflow,
and tagged elements whose text did not scale). A failed or unmeasured large-text pass is a blocking finding. The 1.1.0
catalog bump changes `catalogHash`, so runs approved under 1.0.0 become historical.
The exported runner (`templates/flow.test.cjs`, E12b) matches the verifier for `expectVisible false`: it polls the
target count up to the step timeout; 0 passes (a conditionally unmounted node), 1 must not be visible, more than 1
fails with "Unique target required". The 1.2.0 catalog bump records this runner change.
Do not silently replace a failed React build with HTML or a stub.

## Projects, scoped access and planning

Project endpoints use the root authenticated API. Resource endpoints use optional `X-Workspace-Project`.
Header absence means the existing personal scope. Header presence requires canonical project membership on every request.
Storage scope is server-derived `project:<id>`, never accepted as owner from a body. Acting user remains JWT `sub` for audit.

Roles: owner, planner, designer, developer. Canonical `project.members` maps user sub to `{role,displayName}`.
Membership index rows only aid listing; stale index rows never authorize a revoked member.
Owner manages members. Planner/owner publishes product guidelines. Designer/owner approves UX.
All members read/discuss/upload; planner/designer/owner edit rules; designer/owner generates.
Designer/developer/owner may create a release from approved source;
developer/owner exports a ready release. Existing personal owner retains all personal actions.

Project/member/product/comment routes:

- GET/POST `/projects` -> `{projects}` / `{project}`; POST `{name,requestId}` creates project + owner membership atomically.
- GET `/projects/:id` -> `{project}`; PUT `/projects/:id/members` `{version,members}` owner only.
- GET `/projects/:id/people?q=...` -> existing user lookup, owner only, bounded. No email/message sending.
- GET/POST `/products` -> `{products}` / `{product}` in current project only.
- GET/PUT `/products/:id` -> `{product}`; PUT CAS `{version,title,description,conditions,steps,notices}`.
- POST `/products/:id/publish` `{version}` -> `{product,guideline,ontology}`.
- GET `/products/:id/ontology?revision=<guidelineId>` -> `{ontology}`; default published revision.
- GET `/products/:id/impact` -> `{currentGuidelineId,affectedRuns}`.
- GET/POST `/comments` -> `{comments}` / `{comment}`; anchors `{productId?,guidelineId?,runId?,round?,pageId?}`.
  POST includes `{requestId,text,anchor}`; text <=4000, immutable authorship, retry idempotent.

Guideline field arrays are `{id,text}` for conditions, `{id,title,description}` for steps,
and `{id,title,content,required:boolean}` for notices. IDs are safe identifiers and stable across edits.
Product has title/description, editable draft, `publishedGuidelineId`, `publishedRevision` and internal CAS version.
Publication creates immutable guideline text, a private system guide asset, typed ontology JSON and product pointer.
Ontology `{schemaVersion:1,projectId,productId,guidelineId,revision,hash,nodes,edges}` uses existing Node/Edge concepts:
Product, Condition, PolicyRule, Procedure, ScreenMeta; source nodes carry the exact guideline text/revision.
Persist graph bytes in private storage. Reads/generation use the saved projection, not an ephemeral graph label.
Old projections and approvals remain historical; changed published guideline makes associated runs stale for release/export.

Python module `workspace/collaboration.py` exposes `Collaboration(storage,directory=None)` and:
`resolve_scope(actor,project_id)->{owner,actor,project,role}`, `require(scope,action)`,
`handle(method,parts,body,query,actor,project_id)->(status,payload)|None`,
`published_context(scope,product_id)->{product,guideline,ontology,assetId}`,
`is_current(scope,run)->bool`. It raises `CollaborationError(status,code,message)`.
HTTP and worker integration use this module; routes return None for paths it does not handle.

Storage extension: `put_many(writes)` atomically writes CAS records across scopes.
Each write is `{owner,kind,item,expected_version}` and returns the prepared records in order.
Use one DynamoDB TransactWriteItems request; identical validation/version semantics to `put`.
No fallback sequential writes in production. User directory callable is injected; no direct Cognito client at module import.

## Runs, batches and release

Implemented by `workspace/http.py`, `batches.py`, `releases.py`, and `git_service.py`:

- Contract/run snapshots add `projectId?`, `productId?`, `guidelineId?`, `ontologyHash?`, `catalogHash`.
- Run `outputType:"react"|"html"`. New contracts carry `catalogHash` and default to React;
  older contracts without it retain HTML behavior. Imported HTML inspection is `html`.
- POST `/batches` `{contractId,contractVersion,model,mode:"creative"|"guided",variationCount?,maxRounds,...referenceSettings}`.
  Creative creates one run. Guided validates 2..5 and creates baseline + N variations, all sharing frozen inputs.
  Batch has `{id,mode,baselineRunId?,runIds,variationCount,contractHash,catalogHash}` and per-run status.
- Each React round adds build gates/sourceHash/bundleHash/catalogHash, private source/dist ZIP keys, preview and pageSources.
- Approval requires all React build and required behavioral/accessibility gates.
  A reference baseline uses exact visual policy. Creative/guided variations can retain
  measured `comparisonStatus` under `status:"review-required"`; approval requires
  `acceptVariation:true` and records `acceptedVariation`. Missing/invalid evidence
  never becomes an accepted variation. Without a reference, visual is `not-run`.
  Legacy HTML approval is prototype review and cannot authorize a release.
- POST `/releases` `{runId,round,requestId}` creates a rebuild/retest job for the exact approved source.
  It makes no AI call and compares the rebuilt page against the approved screenshot
  at tolerance `0.02`; this does not prove fidelity to an original design image.
- GET `/releases/:id` and `/releases/:id/blob?kind=source|dist|manifest|report` expose authorized results.
- Release record includes sourceHash,bundleHash,catalogHash,contractHash,guidelineId,approval,rebuildEvidence,status.
- GET `/git-connections` exposes configured connection IDs/labels/repository visibility only; no credentials.
- POST `/releases/:id/git` `{connectionId,requestId}` starts authorized feature-branch export.
  GET release includes actual export `{status,branch,commitSha,commitUrl,filesUrl,baseSha}` or explicit unavailable/failure.

Git destinations come from `WORKSPACE_GIT_CONNECTIONS` (CDK context
`workspaceGitConnections`), not request URLs. No customer destination or credential
is bundled. The adapter and local bare-repository tests exist; their presence does
not prove a configured remote connection. Do not infer permission to export
private/customer input to the public platform repository.
Only registered destinations, feature branch prefixes and generated project directories are writable.
No main writes, force updates, hook execution, or invented successful commit URLs.

Git adapter interface (`workspace/git_export.py`):

- `GitExportError(code, message)` carries sanitized failures; no token/HTTP body logging.
- `GitExporter(connection, token_provider=None, transport=None).export_release(release_id, source_hash, files, project_key, expected_base_sha=None, commit_time=None)`.
- `files` is the verified source archive's relative-path-to-bytes map; adapter recomputes `source_hash` and rejects unsafe paths.
- Connection is operator configuration: `{id,provider:"github"|"gitlab"|"local",repository,baseUrl?,webUrl?,baseBranch:"main",pathPrefix:"generated/studio",branchPrefix:"feature/studio-",barePath?}`.
  Local provider is for explicit local/integration-test destinations; production Git destinations are registered, never supplied by a model/request URL.
- Target directory is `pathPrefix/project_key`, branch is `branchPrefix/release_id`.
  Validate bounded safe IDs/prefixes; branchPrefix must start `feature/`. Never write outside the target generated directory.
- Remote auth comes only from `token_provider`; the worker resolves configured Secrets Manager references.
  Injected transport signature: `transport(method, url, headers, payload_or_none) -> parsed JSON`.
  Default HTTPS transport refuses redirects and returns sanitized error codes.
- Capture the actual base SHA; if expected_base_sha is supplied and differs, fail with conflict.
  Create a feature branch and commit the complete approved source under the generated directory, with no stale files left in that directory.
- Repeated same release/source on the same destination returns the original verified commit. An occupied unrelated branch is a conflict, never a force update.
- Return `{status:"committed",branch,baseSha,commitSha,commitUrl,filesUrl,repository,connectionId,sourceHash}`.
  Local destinations return `commitUrl:null,filesUrl:null` rather than invented HTTP links.
- Tests must create a real local bare repository and verify commit contents, idempotence, occupied-branch conflict, base changes and source/path rejection.
  Remote adapters have injected transport tests; no customer remote publication without a configured target.

## UI scope

The additive canonical ontology interfaces are in [ONTOLOGY_CONTRACT.md](ONTOLOGY_CONTRACT.md).
The current default execution remains the existing isolated browser/compiler.
AgentCore transport and generation/release context binding are subsequent gated
implementation work, not implied by the presence of ontology APIs.

Project selection is explicit and remounts project-bound panels. Use a project-bound HTTP client via React context;
never mutate a global client's owner/scope while async downloads/polling are active.
The planning/discussion/developer sidebar stays tied to the selected product/run/page and exposes actual permissions.
Guided comparison pins baseline and shows 2..5 variants with independent evidence and failures.
Unknown/missing build, ontology, release or Git evidence never becomes a green badge.

Studio keeps the React workspace, existing playground, process generator, gallery
and assets directly visible in its tool navigation. `#/studio?tool=play|process|gallery|assets`
opens the corresponding tool; omitting `tool` selects the React workspace.
The explicit `workspace` value and unknown values also fall back to that workspace.
Tool navigation
retains project/product/run context and browser history, and previously opened
tools stay mounted so switching tools does not discard an in-progress edit.
Tool-only route changes leave the selected run/page discussion anchor intact.
The default workspace stage is the canvas (`review`); developers retain the
handoff default and explicit stage links retain their meaning. Request/settings
and history share a left rail, with the private preview and revision composer
beside it. A new brief proposes criteria for explicit review/approval before
generation. Verification evidence and human approval remain available in a
disclosure below the canvas.
Element selection uses bounded advisory messages through the two opaque preview
frames. It does not relax sandbox/CSP restrictions, run selectors in the host,
or change approval evidence. The selected hint is bound to the viewed round and
artifact hashes. Unsent revision text and its hint remain private in-memory
drafts for that exact evidence key; switching runs/rounds cannot discard them or
apply them to a different artifact. An accepted request clears its submitted
draft only when the user has not edited it in flight. Changing preview width does not
change the frozen verification viewport or reload the simulated state.
Explicitly opening a gallery draft starts a fresh Playground session for that
selection, including a repeated selection, so another draft's score cannot carry over.
The global ontology explorer remains visible in common navigation across work-area
filters. Neither exposing these tools nor component-source downloads grants a React
release approval.

## Validation and evidence

The component table matches `react-kit/ui/types.ts`; hashes come from
`react-kit/manifest.cjs`. Policy/build implementation is in `react-kit/policy.cjs`
and `compile.cjs`; approval and release checks are in `workspace/http.py`,
`react_quality.py`, and `releases.py`. Run relevant `tests/test_workspace_*.py`
and `react-kit` tests with the dependencies in CI. Historical test totals and
live Astra/Fable runs are dated in [the platform README](../README.md); they do
not certify the current deployment or a customer's package/Git integration.
