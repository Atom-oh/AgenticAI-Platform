# React workspace implementation interfaces

The 2026-10-14 ontology workbench addition is governed by
[repeated composition proposals](ONTOLOGY_CONTRACT.md#repeated-screen-composition-proposals-2026-10-14-candidate).
Its UI selects accepted analyses and exact approved Screens, preserves request
identity through retry, displays file/Screen support and unknown scope, and
requires exact evidence for human candidate adoption. Project/client changes
clear pending selections and abort polling. Current role gates mirror the API;
only the creator may cancel their queued or running execution.
Asset details show wireframe, GUI and code declarations with missing/unbound
states, authenticated original navigation and actual revision-bound usage.
These views and candidate decisions do not replace UX/frontend release approval.
Screen approval waits for the complete directly owned mapping review view in
ONTOLOGY_CONTRACT; review/rejection remain available when the dependency closure
is truncated.

Baseline code audit: 2026-09-13. Candidate interface amendments: 2026-10-11 and
2026-10-12; their validation and live gates remain separate. Follow [root instructions](../../AGENTS.md),
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

Engine-derived contracts (`design_loop/contract.py`, engine plan E13) are composition-independent and frozen at
flow approval. Targets come only from `design_loop.convention.Registry`: the page root `<pageId>`, `k<p>-cta`,
`k<p>-f<n>`, `k<p>-n<n>`, `k<p>-f<n>-o<j>`, `case-select` (the generated App's `검증용 케이스` Select, options
`<caseIndex>:<screenId>:<state>[:empty]`) and `flow-finished`. `derive` returns one normalized contract or the
critical findings `contract-capacity`, `published-page-missing`, `visibility-unverifiable` or
`contract-unsupported`, never a partial contract. Every compile, repair and Browser run uses that exact contract.
Do not silently replace a failed React build with HTML or a stub.

Engine handoff package (`design_loop/handoff.py`, engine plan E17; offline and unwired until C). The customer
screen id comes from `Registry.assign` (`<area>-<num>` under the synthetic `seed/design_poc/convention.json`; the
tenant copy is private) and is compiled into each page as `export const meta = {sid, type, dver, status, level1,
level2, level3} as const;`. `handoff.build(release source.zip, layout, manifest_fields=...)` places every release
`source.zip` entry byte-identical under `project/` (nothing is relocated, so the policy and relative imports hold),
adds `convention/screens.json` (`project/src/pages/<slug>.tsx` → `{sid, type, dver, status, levels,
conventionPath, state, sha256}`), `convention/apply.cjs`, `manifest.json` (`approvalHash`, `releaseId`,
`sourceHash`, `bundleHash`, `sourceZipSha256`, `componentPackage: "@studio/approved-ui"`,
`customerPackage: "not-verified"`), `CHANGES.md` and `verification-report.json`. It refuses a `sourceHash` that
differs from the entries, an unmapped page, a page whose compiled meta sid differs from the layout, and duplicate
convention paths. The ZIP is deterministic, at most 512 entries and 40 MiB. The customer applies the physical path
layout after approval with `convention/apply.cjs`, which type-checks the relocated layout with its own
`convention/tsconfig.json`; the project's `npm run typecheck` covers only `src/`.

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
  Persistent design discussions use the dedicated route below; legacy comment
  listings do not return those records.

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
- Round and release bytes share one delivery gate (`Sources.round_delivery`,
  `ONTOLOGY_CONTRACT.md` "Source authority"): every chunk of
  `/runs/:id/blob?kind=html|screenshot|diff|report|source|dist|candidate`, every
  `/releases/:id/blob` chunk, `/runs/:id/baseline` and `/releases/:id/git` apply the
  publishing-handoff/1 round-state content permission and the upstream-lineage
  permission checks; a restricted state or revoked upstream is `404 not-found`.
  The same reader rechecks every observed version and deadline after each chunk's
  bytes are read and before the chunk is returned.
- Authorization precedes artifact validation, and missing and inaccessible runs,
  rounds, releases and baselines return the byte-identical body
  `404 {"code":"not-found","error":"Resource not found"}`; `409 baseline-unavailable`
  is returned only to a caller authorized for that round.
- Content-bearing JSON routes use the same reader in a project scope:
  `GET /contracts/:id` (`Sources.contract_access`: bound `assetIds` and baseline
  still permitted), `GET /runs/:id` (`Sources.run_access`: run-level lineage; rounds
  whose own admission lineage is revoked, or whose publishing-handoff/1 state
  (draft/failed/needs-changes) is restricted from the caller's role, are omitted), `GET /releases/:id`, the
  `/contracts`, `/runs` and `/releases` listings and `/batches/:id` runs. An
  inaccessible record is `404 not-found` or omitted; listings page only authorized
  rows with an opaque `pagecur-…` cursor (5-minute `ontology_cursor` record bound
  to actor, role, project, authority epoch, listing and `limit`, default 100;
  `409 list-cursor-stale` otherwise).
- One response gate (`workspace.http.ResponseGate`) serves every project-scoped
  workspace and publication route. `workspace.http.ROUTES` is the complete route
  inventory; `match_route` dispatches nothing else, and every route declares the
  authorized views its JSON fields or blob bytes are serialized through
  (`tests/test_response_gate.py` fails for an unregistered route or view). Before
  processing, the addressed resource (asset, contract, run, release, job, batch)
  and any run-round target (`/runs/:id/blob`, `/runs/:id/baseline`,
  `/runs/:id/approve`, `POST /releases`) are authorized; afterwards every embedded
  record is re-read and serialized through its view (`asset_access`,
  `contract_access`, `run_access` with revoked rounds omitted, round delivery,
  job inputs, publication/grant visibility), each on a probe reader absorbed into
  one aggregate reader. ONE final aggregate recheck (plus retained destination
  readers for publication impact) runs after all storage reads, including each
  blob chunk, immediately before the response is returned. An inaccessible
  singular record is the same `404` as a missing one, inaccessible list rows are
  omitted, and an undeclared response field fails closed (`503`). Contract and
  run approvals are fenced by that authorization: every version the gate observed
  joins the approval transaction and each attempt first reruns the aggregate
  recheck (currency, expiry, historical references), so a revocation before
  storage persists no approval. Upload, workbench,
  document and intake jobs keep their own module authority; the delegated prefixes
  (`http.DELEGATED`) are listed by name.
- Queued `propose`/`run` generation and `release` rebuild jobs are gated at
  execution by `ontology_sources.job_lineage` (recorded actor's current authority and
  the same lineage checks) before any model call or rebuild, and the same reader is
  rechecked before the outcome (job completion / release `ready`) is recorded.
  Inside the generation, repair and rebuild loops every model call and protected
  service call (browser verifier, React build) goes through one wrapper
  (`ontology_sources.protected_calls`) that rechecks the retained reader
  immediately before the call; a revoked input refuses the call
  (`ProtectedCallRefused`) and fails the job without further rounds.
- The queued Git export worker (`git_service.process_export`) rebuilds the recorded
  actor's current `export` scope (`ontology_sources.job_reader`), reruns the same
  round-delivery lineage check at execution and rechecks that reader after reading
  the source archive, immediately before the external exporter call. The same
  retained reader travels into the exporter (`export_release(..., guard=
  ontology_sources.authority_guard(reader))`): every outbound request — metadata
  reads, each content (blob/tree/commit) transfer and the branch publication, and
  every local repository write — rechecks it first, and a refusal fails the export
  before any further transfer.
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

## Persistent procedure design (2026-10-11 candidate)

A compiler rejection is an interrupted execution with no bundle, Browser result
or UX approval. The completed Interpreter observation and hash-bound private
compiler diagnostics/candidate source are retained. The design detail's optional
`failure` contains those diagnostics only for a currently authorized owner,
designer or developer; all consumed admissions and the final response fence are
rechecked. The UI exposes the failed source for repair and explicit regeneration
under the frozen criteria. It does not fabricate a Browser pass to produce a
`needs_changes` bundle. Findings on a successfully compiled/inspected candidate
continue to use the existing `needs_changes` publication path.

An incomplete or newly rejected procedure makes its design inaccessible; design
listings omit that row without breaking the rest of the page. Invalid pagination
limits return `400 design-page-limit`.

The flow review UI projects the current screens and directed transitions into a
read-only diagram. Selecting a transition highlights its endpoints and retains
its full condition/retention text; missing or removed endpoints remain warnings.
The condition-case view uses only the frozen boolean case assignments and the
same closed conjunction grammar as `ontology_ux.evaluate`. It follows forward
transitions from the recorded entry to a declared terminal, highlights the
chosen path and shows missing/ambiguous/looping outcomes. Incomplete case coverage
or missing entry/terminal information blocks that view. This read-only forecast
does not add a flow approval or replace actual Browser interaction evidence.

On-demand side-by-side comparison uses authorized exact-round screenshots for
legacy batches and isolated React previews for persistent design variants.
Each candidate retains its own status and evidence identity. Design previews
reject source/bundle or verdict mismatches and unmount on scope/revision changes;
failed or unavailable candidates remain visible as failures. Comparison selection
only opens the existing individual review; it never creates an approval/release.

The [design delivery path](../docs/DESIGN_EXECUTION_DELIVERY.md) adds persisted
PRD/flow approvals and Runtime-backed generated variants to the existing canvas.
Its immutable contract records project the exact human-approved flow contract.
Generated runs and releases are ledger-owned and refuse legacy generation or
approval routes. The dedicated design UI owns their exact UX decision and
source-preserving rebuild; it does not weaken existing personal/legacy flows.

Design release download checks every chunk and whole-object hash. Frontend
package acceptance is a separate developer/owner decision over exact source,
bundle and approval hashes. Registered Git export requires that acceptance and
retains its original source/criteria/destination checks. Customer API/auth/deploy
integration remains explicitly unverified.

### Canonical design criteria and delivery

A persistent design assertion contract binds `projectId`, `designId`,
`designSnapshotHash` and `catalogHash` as a complete criteria set. It does not
combine those with product-guideline criteria. The human-approved flow record
and current canonical/source admissions remain authoritative. Legacy product
contracts still require their complete original product/guideline/asset/hash set.
Sources and Git delivery validate these design criteria and retain every observed
version and admission expiry for their final outgoing-request check. Frozen
contract approval includes its stored version and exact normalized hash.

Failed design previews and each source/bundle/screenshot chunk apply the same
owner/designer/developer round-state permission as legacy content. Delayed preview
responses cannot change the selected evidence; edit instruction approval is bound
to the exact submitted text, design and variant. Switching canvas/procedure mode
keeps both editors mounted with independent dirty-state tracking.

Partial edit approval requires both structural stability and Browser checkpoint
layout stability within one pixel outside the selected subtree. Missing or
incomplete geometry fails verification. Full findings remain private downloadable
evidence; bounded stage receipts carry summaries and exact object references.

Design release views include the recorded Git status, branch, base SHA, commit SHA,
source hash and available commit URL under the same final source/export fence.
The design handoff panel polls queued/running exports and displays committed,
failed and delivered-unverified outcomes in place. A job completion flag alone
never supplies the commit receipt; missing or mismatched SHA evidence stays unknown.

### Selected screen assembly (2026-10-12 candidate)

Designer/owner `POST /designs/:id/compose` accepts `{requestId, version,
selections: [{screenId, artifactId}], variant?, maxRounds?,
acknowledgeUnknownOutcome?}`. `variant` is a bounded label (0–4, default 0).
The common request validator accepts `maxRounds` (1–5, default 3), but
composition uses the exact selected sources and does not call a generator or
repair them. An uncertain prior execution requires explicit
`acknowledgeUnknownOutcome: true`. Selections cover every frozen flow screen
exactly once; each selected verified variant supplies that screen's default and
all required states. The server loads their immutable source and reviewed
judgments, retains every consumed admission and admits `design.compose`.
Completed artifacts retain their backend revision and execution profile hash.
Admission compares those immutable bindings with the current configuration;
unrelated job accounting changes or job retention do not replace source
authorization. Current artifact/run/admission versions and deadlines remain
predicates of the final publication transaction.
The operation has no model stage or invocation. It independently rechecks all
applicable cached judgments and rebuilds/tests the combined project, including
large text and coverage. Generation retains these judgments in its signed,
hash-checked private result. Reuse requires a passing judgment for every
applicable rendered context with the exact model, rule revisions and execution
profile, plus current source admission. Missing, failed, tampered, incomplete
or stale review/source evidence blocks the combined round.

The composed result is a new round with its own source/bundle/evidence hashes.
Selection has no approval effect. Exact human UX approval, model-free release
rebuild, source-preserving ZIP and separate frontend acceptance remain required.
The public UI provides per-screen selectors and displays the new round through
the existing individual review and handoff path.

Admission refuses an incomplete/duplicate/unknown screen selection with
`design-selection-incomplete` or `design-selection-invalid`. An unverified
variant or missing review profile in the retained result is `design-compose-review-required`;
unavailable or different artifact backend/profile metadata is
`design-profile-changed`. The common exact
version, role, idempotency, expiry and history limits still apply. Runtime
revalidation failure leaves an interrupted execution with
`design-execution-failed`; it cannot produce a reviewable combined round.

### Validation repair loop (2026-10-12 candidate)

`maxRounds` bounds complete generate/edit validation rounds (1–5). Each failure
feeds the current private checker findings into a repair under the same approved
flow, component catalog, financial bindings and source/file policy. The repaired
project receives fresh compilation, Browser, large-text and coverage evidence.
Missing evidence stops the loop. An uncertain paid call is not automatically
retried. At the limit, a failed verified candidate stays needs-changes and
requires human action; it has no effective UX approval.

The private result retains every round's findings and hash-bound source/build/
Browser references. Artifact preview exposes `validationRounds` with round,
verdict, compiler status, source/bundle hashes and findings, under the same
current source/role/expiry fence; object keys remain private. The UI displays
failed and passing rounds alongside the selected artifact.
Failed/blocked round details are returned only to owner, designer and developer;
other members receive passing rounds only. A compiler refusal
has no Browser/bundle evidence; its current diagnostic/source view follows the
existing owner/designer/developer restriction.

### Discussion on design evidence (2026-10-12 candidate)

All current project members can discuss the exact evidence they may read.
`GET/POST /designs/:id/comments` returns `{comments, cursor?}` / `{comment}`.
POST accepts `{requestId, text, anchor}` with text up to 4000 characters and
immutable authorship from the authenticated actor. An identical retry returns
the same comment; changing the text, design or anchor under that request ID
is `request-changed`.

Anchors have one of these closed shapes:

- PRD: `{kind: "prd", id: "prd", sha256}` uses the stored PRD object's hash.
- Flow node: `{kind: "flow-node", id, sha256}` uses a screen in the exact stored
  flow and that flow object's hash.
- Rule: `{kind: "rule", id, sha256}` uses a rule in that flow's validation
  contract and the same flow object's hash.
- Actual screen: `{kind: "screen", id, artifactId, sourceHash, bundleHash}`
  uses a generated screen in the selected immutable artifact and its exact
  source/bundle hashes.

The design detail supplies `prdHash` and `flowHash` for these selections.
GET accepts the JSON-encoded `anchor` query field, optional `limit` (1–10,
default 5), and opaque `cursor`. Cursors bind actor, role, project authority,
design and exact anchor, expire within five minutes, and cannot switch targets.
Pages may be empty when other comments occupy the storage page; follow the
cursor until it is absent. Current membership, source admission, canonical
context, selected variant visibility and expiry are rechecked for admission,
idempotent replay and the final response. Unknown targets/hashes do not accept
comments; inaccessible reads return not-found.

Discussion writes are separately fenced comment records. They do not mutate
design/run/job versions, approve a candidate or change the project authority
epoch. The design panel offers the four anchor choices alongside the current
PRD, flow and verified preview. It preserves request identity on retry, shows
the current storage page of persisted comments on reopening, provides manual
pagination, and aborts stale reads when the target changes.
Local API/compiler/Browser/UI checks remain separate from deployment-bound
HAND-01 and AUTH-01/08 acceptance.

### Independent reference sets and actual observations (2026-10-12 candidate)

The design assertion contract can retain an `observationPlan` with schema
version 1, the bounded complete Boolean case product, trusted case-control
target, entry page, page-target/screen/state/action mappings and terminal
screens. Flow approval fixes its normalized hash with the ordinary criteria.
The Browser opens a fresh isolated context for each case, selects only the
entry, follows the actual rendered action controls and records visible page
text before navigation plus the observed terminal. It does not use expected
graph paths, generated composition text or application postMessage results as
observations. Ordinary per-page assertion tests do not become journey evidence.

The private Browser report retains the plan hash, case/page/state checkpoints,
explicit text truncation, actual ends and incomplete reasons. Bounds are 64
cases, 19 page states, 50 hops/case, 16000 characters/checkpoint, 1024 points,
2MiB observed text and the existing 80-second verifier deadline. Loops,
missing/ambiguous pages or case controls, unavailable actions, truncation and
capacity/deadline exhaustion remain incomplete. Ordinary rule approval and
complete benchmark evidence are separate decisions.

Designer/owner `POST /designs/reference-sets` accepts `{requestId, title,
designId, artifactId, sourceHash, bundleHash, golden}`. `golden` contains 1–64
independently authored criteria entries, at most 64KiB total. Each entry has `{id, case,
mustShow: [{screen, text}], mustNotShow: [{screen, text}], endsAt}`; `"*"` in
`mustNotShow.screen` means every observed page in that actual journey. The
definition/criteria hash and baseline source/observation/model/profile/kit/
PRD/generation bindings are immutable. Changing a criterion requires a new
reference set; ontology changes do not rewrite or weaken expected results.
An identical retry returns the saved reference under current source access.
Entries and assertions use these closed shapes. Malformed arrays, identifiers,
text or criterion fields return `422 benchmark-reference-invalid` before source
reads or reference writes. Exceeding the case/byte bounds remains
`422 benchmark-reference-limit`.

`GET /designs/reference-sets` returns currently authorized `{references,
cursor?}` pages, and `GET /designs/reference-sets/:id` returns `{reference}`.
List cursors bind actor, role, project authority, listing and page limit, and
expire within five minutes. A mismatched or expired list cursor returns
`409 design-cursor-stale`; inaccessible singular references remain `404 not-found`.
Private object keys are omitted. Missing actual baseline observations refuse
creation with `benchmark-observations-required`; a locally reconstructed
checkpoint cannot substitute. Missing or empty checkpoint maps likewise refuse
creation. A version conflict at the final singular-read fence returns the same
`404 not-found` as an inaccessible reference.

The panel's regeneration action uses the existing source-bound `design.generate`
admission and complete verification loop. After an ontology change, a new
design must bind the new source/context and receive exact PRD/flow approval;
the old context cannot authorize generation or delivery. `POST
/designs/reference-sets/:id/compare` accepts `{requestId, criteriaHash,
designId, artifactId, sourceHash, bundleHash}` and compares the actual regenerated
observation under the same model, execution profile, kit and PRD criteria.
It retains before/after source/generation/observation hashes, scores, improved/
regressed IDs and blocked state in an immutable comparison. Different
model/profile/kit/PRD scope is `benchmark-scope-changed`.

Changed criteria are `409 benchmark-criteria-changed`; changed source, bundle
or retained evidence bindings are `409 benchmark-source-changed`. A request
ID reused with different content is `409 request-changed`. An atomic write
whose source predicates exceed the transaction budget is
`422 benchmark-scope-limit`. Format and case/byte bounds use the 422 errors
defined above; none of these errors authorizes source omission or approval.

`GET /designs/comparisons/:id` rechecks both retained sources before returning
the recorded comparison. Baseline inspection may label obsolete ontology
context as history only; it still requires current project/role/admission/
source/expiry authority for every consumed input. This exception grants no
generation, approval, source reuse, release or export authority. A revoked or
inaccessible baseline blocks rather than being silently omitted.

Comparison has `approvalEffect: false`. The rendered form lets a person select
conditions/screens, enter exact required/forbidden text and expected arrival,
then keep those criteria fixed across regeneration. The current form applies
forbidden text to the whole observed journey. No source/UI assertion here
certifies G0-BROWSER/RUNTIME or deployment-bound GEN-01/05 and HAND-01.

### Human intervention and feedback (2026-10-12 candidate)

Design records retain up to 200 human action entries without silent trimming.
New records mark coverage `complete`; older records without the field are
`not-recorded`, and their first new action starts `partial` coverage. Absence of
old events does not prove that no person changed a result. At the limit, the
current history stays intact and a new design is required before another action.

A scoped edit request is recorded in the same B0 admission transaction as its
job/artifact/design. It records actor, timestamp, base/output artifact IDs,
base source/bundle hashes and exact screen/state/target scope. It retains every
consumed admission privately, including the instruction, without copying
instruction text into metadata. An idempotent submission does not add an event.
Completion or failure updates its outcome atomically with the terminal state.
`editRequests` measures accepted human requests, including interrupted requests;
an outcome remains visible separately and is not a claim of an approved change.

Exact UX approval records an `approve` action in the existing design/run
transaction. Designer/owner `POST /designs/:id/reject` accepts `{requestId,
version, artifactId, sourceHash, bundleHash, reason}`; reason is 1–1000
characters. It rechecks current source, role, variant visibility and expiry.
An identical retry returns the saved decision, while changed input under the
same actor/request ID is `request-changed`. Rejection atomically clears any
approval on the target run, making its previous releases ineffective even when
another variant is now selected. It clears the design's current UX approval
only if that approval targets the rejected variant, and removes frontend
acceptance for a rejected release. Rejection does not invoke a model or alter
the canonical ontology.

`GET /designs/:id/artifacts/:artifactId/interventions` returns exact artifact/
source/bundle hashes, coverage, events, metrics, `editRequests`, `rejectionCount`,
`failureChecklist` and `rejectionCandidates`. Edits identify both the base and
new output; the view includes actions on that artifact and requests targeting
it. Counts cover only currently readable events. Pending/failed output details
remain restricted to owner/designer/developer; all consumed admissions and
deadlines are rechecked even after the original job's retention expires.
Storage keys, raw instructions and internal admission bindings are omitted.
The final response is fenced against the design/project/source versions.

The private feedback object retains the exact human reason and source-bound
declared PolicyRule proposals from the selected run round. They remain
candidates in the feedback queue, labelled `업무 근거 연결 필요`; they are neither
canonical active rules nor approved model input. Connecting an authoritative
business source and human governance review remains required. The checklist
collects distinct verifier role/code failures and their observed rounds; it
labels resolution only against the final visible round, without claiming
that missing/hidden history passed.

The rendered panel shows human request scope/outcome, reasons, checklist and
feedback candidates. Rejection keeps its request identity on retry and only
enables after the selected evidence hashes match. These source/API/local UI
checks do not establish deployment-bound GEN-04/05, HAND-01/02 or AUTH-01/08.
