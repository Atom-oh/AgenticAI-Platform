# React workspace implementation interfaces

2026-09-11. Extends `CONTRACT.md`; old personal HTML runs remain readable as prototypes.
New deliverables use a real pinned React package and a verified release, not the old stub gates.
Implementation owners must preserve these interfaces or coordinate a change before editing another owner's files.

## React kit

Directory `platform/react-kit`. Node/CommonJS tooling; React source is TypeScript/TSX.
Exact versions: React/ReactDOM 18.3.1, TypeScript 5.6.3, esbuild 0.25.12,
@types/react 18.3.31, @types/react-dom 18.3.7, Playwright 1.62.0.
`package-lock.json` is committed. No runtime npm install.

- `ui/index.tsx`: real named exports listed below; generated code imports from `@studio/approved-ui`.
- `ui/tokens.css`: authoritative styles/tokens. Generated code cannot replace this file or supply CSS.
- `catalog.json`: `{schemaVersion:1,id:"studio-ui",version:"1.0.0",label:"플랫폼 기본 React 컴포넌트",components:[...]}`.
  Each component has `{name,description,props,variationAxes}`. `props` is descriptive data; actual TS types are the compiler authority.
- `manifest.cjs`: `catalog()` returns descriptor plus `{hash,files:[{path,sha256}]}` calculated from actual `ui/*` and catalog bytes.
  `hash` is SHA256 of canonical JSON of sorted file hashes. It excludes generated timestamps and node_modules.
- `compile.cjs`: `buildProject({files,assets,expectedCatalogHash,contract})` returns the result below.
  CLI: `node compile.cjs request.json output.json`, all model input treated as data.

Shared component props: `testId?:string` becomes data-testid; `id?:string`; no caller-provided className, style, ref or raw HTML.
Components include `data-studio-component` and `data-studio-version` on their root.

| Export | Required/important props |
|---|---|
| Screen | `pageId:string`, `children:ReactNode`, `width?:"mobile"|"content"|"wide"`, `title?:string` |
| Stack | `children`, `gap?:1|2|3|4|6|8` |
| Grid | `children`, `columns?:1|2|3`, `gap?:1|2|3|4|6|8` |
| Inline | `children`, `gap?:1|2|3|4|6`, `align?:"start"|"center"|"end"`, `justify?:"start"|"between"|"end"` |
| Panel | `children`, `title?:string`, `tone?:"default"|"subtle"|"brand"` |
| Text | `children`, `as?:"span"|"p"|"h1"|"h2"|"h3"`, `size?:"sm"|"md"|"lg"`, `tone?:"default"|"muted"|"brand"|"danger"` |
| Button | `label:string`, `onClick?:()=>void`, `kind?:"primary"|"secondary"|"danger"`, `disabled?:boolean`, `type?:"button"|"submit"` |
| Input | `label:string`, `value:string`, `onChange:(value:string)=>void`, `type?:"text"|"number"|"email"|"tel"`, `hint?:string`, `error?:string`, `required?:boolean`, `disabled?:boolean`, `min?:number`, `max?:number`, `placeholder?:string` |
| Checkbox | `label:string`, `checked:boolean`, `onChange:(checked:boolean)=>void`, `disabled?:boolean`, `required?:boolean` |
| Select | `label:string`, `value:string`, `onChange:(value:string)=>void`, `options:{value:string,label:string}[]`, `disabled?:boolean` |
| RadioGroup | `label:string`, `value:string`, `onChange:(value:string)=>void`, `options:{value:string,label:string}[]` |
| Alert | `message:string`, `title?:string`, `tone?:"info"|"success"|"warning"|"danger"` |
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

Python integration is parent-owned: a `react` task in the credential-free browser child runs compile, then
the existing behavioral/a11y/image verifier against the actual static bundle. The response contains build fields and browser report.
`evaluate_html` remains compatible; `evaluate_bundle` fulfills only exact local bundle URLs in memory and denies every other request.
Do not silently replace a failed React build with HTML or a stub.

## Projects, scoped access and planning

Project endpoints use the root authenticated API. Resource endpoints use optional `X-Workspace-Project`.
Header absence means the existing personal scope. Header presence requires canonical project membership on every request.
Storage scope is server-derived `project:<id>`, never accepted as owner from a body. Acting user remains JWT `sub` for audit.

Roles: owner, planner, designer, developer. Canonical `project.members` maps user sub to `{role,displayName}`.
Membership index rows only aid listing; stale index rows never authorize a revoked member.
Owner manages members. Planner/owner publishes product guidelines. Designer/owner approves UX.
All members read/discuss/upload; planner/designer/owner edit rules; designer/owner generates.
Developer/owner exports an already-approved release. Existing personal owner retains all personal actions.

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
HTTP glue and worker integration remain parent-owned; module routes return None for non-owned paths.

Storage extension: `put_many(writes)` atomically writes CAS records across scopes.
Each write is `{owner,kind,item,expected_version}` and returns the prepared records in order.
Use one DynamoDB TransactWriteItems request; identical validation/version semantics to `put`.
No fallback sequential writes in production. User directory callable is injected; no direct Cognito client at module import.

## Runs, batches and release

Parent-owned extensions:

- Contract/run snapshots add `projectId?`, `productId?`, `guidelineId?`, `ontologyHash?`, `catalogHash`.
- Run `outputType:"react"|"html"`. New generation defaults React; imported HTML inspection remains `html`.
- POST `/batches` `{contractId,contractVersion,model,mode:"creative"|"guided",variationCount?,maxRounds,...referenceSettings}`.
  Creative creates one run. Guided validates 2..5 and creates baseline + N variations, all sharing frozen inputs.
  Batch has `{id,mode,baselineRunId?,runIds,variationCount,contractHash,catalogHash}` and per-run status.
- Each React round adds build gates/sourceHash/bundleHash/catalogHash, private source/dist ZIP keys, preview and pageSources.
- Existing approval API must require all React build + required browser gates for a React run.
  Legacy HTML approval is prototype review and cannot authorize a release.
- POST `/releases` `{runId,round,requestId}` creates a rebuild/retest job for the exact approved source.
- GET `/releases/:id` and `/releases/:id/blob?kind=source|dist|manifest|report` expose authorized results.
- Release record includes sourceHash,bundleHash,catalogHash,contractHash,guidelineId,approval,rebuildEvidence,status.
- GET `/git-connections` exposes configured connection IDs/labels/repository visibility only; no credentials.
- POST `/releases/:id/git` `{connectionId,requestId}` starts authorized feature-branch export.
  GET release includes actual export `{status,branch,commitSha,commitUrl,filesUrl,baseSha}` or explicit unavailable/failure.

Git destination is not supplied yet. Implement a configurable connection adapter and local bare-repository contract tests;
do not export private/customer input to the public platform repository by assumption.
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
- Remote auth comes only from `token_provider`; the parent resolves configured Secrets Manager references.
  Injected transport signature: `transport(method, url, headers, payload_or_none) -> parsed JSON`.
  Default HTTPS transport refuses redirects and returns sanitized error codes.
- Capture the actual base SHA; if expected_base_sha is supplied and differs, fail with conflict.
  Create a feature branch and commit the complete approved source under the generated directory, with no stale files left in that directory.
- Repeated same release/source on the same destination returns the original verified commit. An occupied unrelated branch is a conflict, never a force update.
- Return `{status:"committed",branch,baseSha,commitSha,commitUrl,filesUrl,repository,connectionId,sourceHash}`.
  Local destinations return `commitUrl:null,filesUrl:null` rather than invented HTTP links.
- Tests must create a real local bare repository and verify commit contents, idempotence, occupied-branch conflict, base changes and source/path rejection.
  Remote adapters use primary API documentation and injected transport tests; no customer remote publication without a configured target.

## UI ownership

Project selection is explicit and remounts project-bound panels. Use a project-bound HTTP client via React context;
never mutate a global client's owner/scope while async downloads/polling are active.
The planning/discussion/developer sidebar stays tied to the selected product/run/page and exposes actual permissions.
Guided comparison pins baseline and shows 2..5 variants with independent evidence and failures.
Unknown/missing build, ontology, release or Git evidence never becomes a green badge.
