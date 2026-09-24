# Platform module contracts

Current code audit: 2026-09-13. Read [root instructions](../../AGENTS.md),
[review context](../../docs/REVIEW_CONTEXT.md), and [SPEC.md](../../SPEC.md) first.
Security implementation amendment: 2026-09-23; separate live evidence is required.
This document describes integration interfaces, not a permanent allocation of
files to earlier implementation workers. Dated plans and guidebooks are not
additional requirements unless the current task explicitly adopts them.

Paths below are relative to `platform/`. Workspace interfaces are defined in
[CONTRACT.md](../workspace/CONTRACT.md) and
[REACT_CONTRACT.md](../workspace/REACT_CONTRACT.md).
The internal library and source-bound S1 HTTP workflow are specified in
[documents/CONTRACT.md](../documents/CONTRACT.md). Their private document kinds
are separate from ordinary workspace assets, and their analyses never use the
legacy shared WebSocket event cache.

The [platform architecture](ARCHITECTURE.md) assigns cross-module implementation
ownership and staged delivery. Follow the owning contract for exact schemas and
permissions; update both when an integration boundary changes.

## 1. WebSocket handlers

`api/ws_handler.py` authenticates connections and dispatches `ROUTES` from
`api/handlers/`. Deployment places `api/common` at the package root:

```python
from common.ctx import Ctx

def handle_x(ctx: Ctx, body: dict) -> None: ...

ROUTES = {"x": handle_x}
```

- `ctx.post(payload)` supplies `reqId` and `traceId`. Request/reply events normally
  use the action name as `type`. Streaming uses `<kind>.stage`,
  `<kind>.token`, and `<kind>.done`; `sock.run` completes on `.done`.
- `ctx.email` is authenticated connection identity used for scenario audit.
  Workspace HTTP uses JWT `sub` instead; do not interchange their owner keys.
- `ctx.user_sub` is the verified Cognito subject for actor-bound agent sessions.
  WebSocket tokens must belong to the configured pool/client and remain within
  their recorded expiry. Legacy connections without that binding must reconnect.
- `ctx.error(message)` emits `type="error"`; terminal scenario failures can use
  `ctx.done(kind, error=...)`. Entry-point exceptions are also reported as errors.
- Use `engine.gate` for platform LLM generation; `engine.bedrock.Stream` and
  `generate` are compatibility wrappers. Actual usage comes from the adapter.
  Strands has its separate pre-model boundary hook, which checks system content,
  messages and tool schemas with rules plus strict independent Guardrail coverage.
  Runtime verification is bounded at 100,000 characters; Gateway/Harness at 20,000;
  all reject unverified overflow rather than truncate. Gateway/Harness block EMAIL
  and KR_PASSPORT as well as other identifier types. Raw customer email references
  are not admitted there; the sample lookup is separate from authenticated S2.
- Agent creation/transition user actions store local requests. AgentCore
  provisioning, approval mirroring and Harness PassRole are IAM AdminFn
  operations; WsFn has read actions, bank-prefix Harness invocation and the
  existing exact bank Runtime invocation grant.
  See [WP1–WP3](BANK_AGENTCORE_WORK_PACKAGES.md) for separate offline/live gates.
- `agent.done.sessionId` is the client conversation ID; `runtimeSessionId`
  is the server-derived ID bound to the verified subject and agent version.
  `toolsMissing` lists configured unavailable Runtime tools and sets `code=502`.
  Missing tools, terminal error codes and incomplete streams remain failures
  even when some text was already emitted. A concurrent same-session Runtime
  request returns `409` with `stopReason=session_busy`.
  Stream error events never forward upstream error bodies.
- `common.tracing.record_trace` records scenario, identity/query hashes, model,
  token counts, timing, masking/block/cache evidence and plane labels.
  `common.log.log_event` hashes sensitive fields. Do not add raw prompts,
  responses, personal values or credentials to logs/traces.
- `common.plane.call(path, body)` invokes the internal service through the
  configured bridge/direct path; `plane.mode()` is `bridge|direct|local|none`.
  Local mode is a labelled development fallback, not network separation.
- Python Lambda packaging is not dependency-free: `deploy.sh` vendors
  boto3/botocore and converts semantic YAML to JSON. Container dependencies are
  specified separately in `agents/requirements.txt` and `workspace/requirements.txt`.
- `studio_run` acknowledges the job, then `StudioLoopFn` pushes
  `studio.stage/.token/.done` to the same connection. Missing `STUDIO_LOOP_FN`
  reports an explicit failure.

`deploy.sh` copies API files plus `engine`, `graph`, `onprem`, `semantic`,
`registry`, `screengen`, `report`, `agentcore`, `design_loop`, `studio`,
`workspace`, selected React-kit files, skills and generated seeds into `api-dist/`.
The EKS gateway and privacy relay have separate build contexts.

## 2. Frontend

The SPA uses React, TypeScript, Vite and Tailwind. Existing scenario views use
`sock`/`auth` from `web/src/lib.ts`; workspace panels use their scoped HTTP client.

```typescript
const event = await sock.request('registry_list', { type: 'CUSTOM', subtype: 'COMPONENT' });
await sock.run('screengen', { prompt }, (event) => { /* render evidence */ });
```

Show progress, errors and actual evidence. A `cached: true` event needs the
`캐시 응답` label; `cache.replay` resets the displayed run. S2 never replays the
shared scenario cache. Do not store personal values or authentication tokens in
`localStorage`/`sessionStorage`; current auth tokens live in module memory.
Keep internal/cloud/success labels consistent with `--onprem`, `--cloud`, `--ok`.
Legacy scenario styling does not define the React kit's component contract.

## 3. Registry

```python
# registry/api.py
def counts() -> dict: ...
def list_approved(record_type=None, subtype=None) -> list[dict]: ...
def get_record(name, version) -> dict | None: ...
def search(query, record_type=None) -> list[dict]: ...

# registry/seed.py
def seed(actor, reset=False) -> dict: ...
def reset_demo_state(actor) -> dict: ...
```

Record example (illustrative values, not a fixed approved version):

```json
{"name":"Button","recordVersion":"v2","recordType":"CUSTOM","subtype":"COMPONENT",
 "status":"APPROVED","description":"...","owner":"UI플랫폼팀","tags":["form"],
 "payload":{"propsSchema":{},"import":"@atom/ui/button","supersededBy":"v3"},
 "createdAt":1690000000000,"updatedAt":1690000000000,"updatedBy":"demo@atomai.click"}
```

- `recordType`: `MCP|AGENT|SKILL|CUSTOM`. Current component contracts use `CUSTOM` /
  `COMPONENT`, a recorded deviation from SPEC §7’s required `SKILL` mapping. Keep
  the requirement, implementation and portal deviation badge distinct.
- Transitions: `DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED` and
  `PENDING_APPROVAL → REJECTED → DRAFT`. Other transitions fail with code `400`.
  `REJECTED` and `DEPRECATED` require a reason.
- Generic MCP/SKILL/CUSTOM creation and draft submission are available to invited
  users. Approval, rejection and deprecation are IAM-only Admin decisions:
  `inspect_registry_record` returns the current record and `expectedHash`;
  `transition_registry_record` requires that exact hash and conditionally commits
  the decision and audit. Generic Admin decisions cannot target AGENT/internal
  request records. User attempts return `403`. Public `allowedTargets` excludes
  these decisions and `adminRequiredTargets` identifies them for the UI.
- IAM decision audit actors are `iam-invoke:<Lambda aws_request_id>`, supplied by
  trusted execution context, not request fields. This identifies the invocation;
  direct Lambda context does not expose the calling IAM principal. Operator
  identity correlation requires retained AWS invocation audit and operator
  receipts; this contract does not claim that CloudTrail data events are enabled.
- After creation submits the initial draft, user AGENT transitions return
  pending `AGENT_ADMIN_REQUEST` receipts; only
  IAM administration applies them. Generic creation of AGENT records or the
  administrative request namespace, and user transitions of requests, return
  `403`. Request payloads and audit entries are omitted from public get,
  list, search, version and consumer APIs. Trusted request collision checks and
  IAM processing opt into `get_record(..., include_internal=True)` internally;
  user request bodies cannot enable that option.
- Generic decision responses separate local `applied` from mirror `completed`.
  Mirror failures retain the committed local audit; a fresh exact-hash inspection
  permits synchronization retry without another lifecycle event.
- Generic transitions conditionally bind the authorized record type, subtype,
  payload and revision in the same transaction as the status and audit writes.
  Missing or concurrently reclassified records cannot bypass Agent administration.
- `name` + `recordVersion` is unique. Conditional writes detect conflicts;
  record creation/status changes and their actor/from/to/reason/time audit
  insertions commit atomically. Agent discovery omits operational prompts;
  Agent audit responses expose identity/reason hashes rather than private text.
- `REGISTRY_TABLE` uses `pk`, `sk`, and GSI `byStatus(status, updatedAt)`.
  No table configuration selects the in-memory test store.
- Consumer queries re-read GSI candidates with strongly consistent base-table
  reads and return only current `APPROVED` records. New approvals can wait for
  index propagation; deprecated/removed candidates are rejected immediately.  Administrative hybrid search
  is separate: `search` returns scored hit objects containing `record`.
  Embeddings can be disabled or unavailable; `search_detailed` reports
  keyword-only operation rather than claiming dense retrieval.

## 4. Legacy F5 screen generation

Action `screengen` accepts `{prompt}`. Stages include `registry_lookup`, `skills`,
`generate`, `gates`, and at most one `regenerate`. The result includes `code`,
`componentsUsed`, `gates`, `attempts`, `ok`, usage, provenance and failure details.
Only approved component schemas enter the prompt; this lookup does not use
vector search.

`screengen/agent.py` loads the three non-`studio` skill files. Generated TSX uses
the exact `@atom/ui/<module>` import from each approved record, and a first-line
`// registry: Name@version` header. The module-root import is not the documented
generation format, even where a lower-level checker tolerates it.

`GATES_FN` invokes the Node runner with code and approved component schemas.
Results include `build`, `types`, `lint`, `a11y`, `visual`, plus the orchestrator's
Registry check. Type declarations and rendered UI are generated stubs, not the
customer's component implementation. `visual` compares normalized HTML structure,
not pixels; a changed snapshot is evidence, not automatically a failure.
The a11y runner sets `ok` from violations and separately returns `incomplete`.
An `ok` result therefore does not certify checks that were incomplete.

The publishing skill's status/error examples must defer to the selected schema:
live seed `Badge.tone` uses `critical` and `Alert` uses `severity`; the isolated
fixture uses `danger` and `kind`. The live FormField seed also omits fixture
`required`/`error` props. Preserve accessible required/error behavior using
supported markup; do not invent props. Do not copy fixture props into live generation.

## 5. Reader/Writer report

`report` accepts `{url?, audience?}`; `report_sample` describes the synthetic
injection sample at `web/public/samples/vendor-news.html`.
Stages are `reader_fetch`, `reader_summarize`, `handoff`, `writer_search`,
`writer_generate`, followed by `report.done`.

Reader and Writer are separate handlers and IAM roles. Reader fetches external
content and attempts the internal tool for the denial demonstration; the Reader
role lacks that invocation grant. Writer consumes structured summary data and
internal search results, without fetching the original URL. The orchestrator uses
`READER_FN`, `WRITER_FN`, `INTERNAL_TOOL_FN` and `STREAM_TABLE`.
Writer streams through DynamoDB; the orchestrator reports actual
`delivery=stream|partial_fallback|single_event` and relay errors.
IAM-denial or streaming claims require observed results, not role names alone.

## 6. Internal plane and MyData

`onprem/service.py` retains `/health`, `/s2/prepare`, `/s2/finalize`,
`/audit/recent`, and `/vector/search`. Vector requests carry
`{query, queryEmbedding}` and return hits plus timing. Bridge operation names
are defined in `bridge/handler.py`.

With `DATA_BACKEND=rds`, audit text and reidentification maps are stored in RDS;
local development uses files. `/audit/recent` returns metadata/lengths rather than
raw audit text. Authenticated S2 stage events can contain lookup values and the
final reidentified answer; the cloud trace is not a copy of those events.

Current S2 free text must pass `api/common/privacy.py` and the private EKS
processor before cloud Guardrails. Known structured fields are tokenized by the
internal producer, then scanned independently. The explanation route is separate
from `privacyModel`; `gemma` does not replace private Qwen processing.
Preserve `privacy_input`, `privacy_payload`, `s2_privacy_models`,
`schema-and-independent-scan`, `structured-tokenization`, `modelInvoked`,
receipt field names and Korean runtime strings. Privacy verification failure
blocks S2, with no shared cache fallback. See
[privacy infrastructure](../infra/README-privacy.md).

## 7. Validation commands

From `platform/`, with dependencies/browser assets installed as in CI:

```bash
bash agents/prepare_context.sh
python3 -m pytest tests/ -q
(cd gates && npm test)
(cd react-kit && npm test)
(cd web && npx tsc --noEmit && npm run build && node --test test/*.test.cjs)
```

Use focused suites while changing a module; a documentation audit does not prove
deployment or all live model routes. Preserve exact fixtures and runtime-consumed
skill semantics when translating explanatory text.

## 8. Audit notes

The 2026-09-13 code audit found these contradictions. They remain explicit so a
reviewer does not turn an older description into a requirement for unrelated code:

| Earlier claim | Current code and implication |
| --- | --- |
| All agents are Harness; four specs | `api/admin_handler.py` prefers Runtime/Strands when configured; `agentcore/agent_specs.py` has five specs. Custom creation still uses Harness. Some source comments retain older wording. |
| Every generation path regenerates once | F5 and `design_loop/loop.py` do; `studio/loop.py` permits 20 rounds and `workspace/http.py` permits five. |
| Gemma substitutes for private PII processing | S2 requires the private EKS detector for free text. Its Gemma route is explanation only. |
| Raw question goes to input Guardrails first | `api/handlers/s2.py` now sanitizes it through EKS first. Its opening source docstring still describes the older order. |
| All scenario failures replay cache | S2 explicitly sets `cache=False`. |
| CloudFront is the only public endpoint; all VPCs have no NAT | The browser directly uses WebSocket API Gateway. The isolated plane and reused EKS VPC are separate topologies. |
| Loading, shared seeding and reset must be IAM-admin-only | Neptune loading follows the administrative path. Explicit authenticated `reset` and `registry_seed` routes are removed. Other legacy bootstrap/list behavior still needs separate review; route removal does not certify every Registry write. |
| F5 gates verify production React/pixels | `gates/a11y.js` uses stubs/jsdom and `gates/visual.js` uses structure hashes. Real kit/browser/release checks are under `workspace/` and `react-kit/`. |
| `Badge danger` / `Alert kind` always work | These are fixture props; `registry/seed.py` uses `critical` / `severity`. The actual approved schema wins. |
| Deployment and teardown target the same stack | `deploy.sh` defaults to `BankPlatformCore`; `teardown.sh --all` targets `BankPlatform`. Privacy contexts are not forwarded by `deploy.sh`. |

These notes document behavior and integration risks; they do not waive applicable
security or output requirements. Source/runtime corrections require a separately
scoped code change.

## Canonical ontology extension

`/studio-api/ontology` reuses the workspace JWT and project-membership boundary.
Its versioned schema, candidate publication, review, source-analysis and impact
interfaces are defined in [`workspace/ONTOLOGY_CONTRACT.md`](../workspace/ONTOLOGY_CONTRACT.md).
The data foundation is distinct from AgentCore execution readiness. Existing
bank Gateway/Harness contracts are retained; the planned ontology Gateway is
a separate target and permission boundary.

The required execution extension is specified in
[`workspace/AGENTCORE_CONTRACT.md`](../workspace/AGENTCORE_CONTRACT.md).
Its required new durable writer is `workspace/execution_ledger.py`; the authenticated API,
dispatcher, trusted Lambda facade and reconciler have distinct transition roles.
Runtime uses signed capabilities and scoped tools rather than direct ledger/
canonical-store writes. Interpreter, Browser and Memory have their own bounded
responsibilities and evidence. These are implementation requirements, not
installed endpoints or an alternate approval system.

Extend existing run/round/release/Git interfaces with exact context and observed
service receipts. The [design](ARCHITECTURE.md) maps the integration sequence;
the [acceptance cases](ONTOLOGY_AGENTCORE_VALIDATION.md) define its verification.
The default backend and graph mode remain legacy until their applicable gates.

## Source admission (`source-admission/1`)

Private intake (`intake/*`) owns source admission and de-identified derivatives;
the binding details are in
[`workspace/AGENTCORE_CONTRACT.md`](../workspace/AGENTCORE_CONTRACT.md) (`source-admission/1`).
Offline code and tests exist; no route or function is deployed by this record.

| Method / path | Input / output |
|---|---|
| GET `/studio-api/intake/reviews` | JWT access token, `X-Workspace-Project`, and a current IAM-administered reviewer grant → `{reviews: [{id, revision, source: {sourceKind, sourceId, revision}, title, dataClass, artifactKind, pageCount, inspection: {pages, chars, pii, identifiers, blocking}, derivativePreview, expiresAt}]}`; only `pending-review` decisions the actor may review (grant **and** current source access); the preview is derivative text only |
| POST `/studio-api/intake/reviews/{decisionId}` | `{approve: boolean, reason}` (closed fields; the reason is not stored) → `{decision: {id, revision, status, expiresAt}}`; `403 review-grant-required` / `source-access-required`, `409 decision-not-pending` / `policy-changed` / `conflict` |

These routes confer no administration: no workspace/project API creates, edits or
activates a policy, provenance registration, reviewer grant or resolver profile.
In-app `platform-operators`/`admin` groups confer nothing.

Storage kinds: `adm_policy`, `adm_provenance`, `adm_grant`, `adm_resolver` and
`adm_audit` live in the owner partition `intake:deployment`; `adm_decision` lives
in the project partition `project:<id>`. Derivative pages, inspection receipts,
code-collection index/files/mapping, prompt text and image/vision derivatives are
private blobs under `Storage.key_for(owner, "adm_decision", <id>, ...)`.
The Worker task `intake-image` (job target `adm_decision.decisionId`) produces
image decisions atomically with its job completion.

The IAM-only administration Lambda (`intake/admin_handler.py`) also owns the
organization `design_publish`/`policy_publish` capabilities used by shared
publications: `grant_capability` (record `{id, actor, name, expiresAt}`) and
`revoke_capability` (`{id, expectedRevision}`). Kind `capability` lives in
`intake:deployment` with an `adm_audit` event per write. No workspace route
creates or activates a capability; project ownership confers none.

## Shared publications

Shared-publication authority and the `published-asset` source adapter follow
[`workspace/AGENTCORE_CONTRACT.md`](../workspace/AGENTCORE_CONTRACT.md)
"Shared-publication authority". Offline code and tests exist
(`workspace/publications.py`, `tests/test_publications.py`); no route is deployed by this record.
All routes require a JWT access token and `X-Workspace-Project`; a publication the
caller cannot use returns the same `404 not-found` as a missing one.

| Method / path | Input / output |
|---|---|
| POST `/studio-api/publications` | Origin project; `{kind: "design"\|"policy", nodeIds, revisionBindings: {nodeId: {revision, contentHash}}}` (closed fields). Design: owner/designer; policy: owner/planner. Every node must be a current, readable, `approved` origin node at the bound revision whose sources are publishable (`asset`, `document-revision`, `product-guideline`, `package`, no `allowedRoles`) → `201 {publication: {id, originProject, kind, revision, nodes, hash, status: "proposed", recallNeeded}}`. A changed binding of the same node set is a new revision; `409 publication-binding-stale` / `publication-restricted-source` / `publication-withdrawn`, `422 publication-node-kind` |
| POST `/studio-api/publications/{id}/approve` | Origin project; `{}`. Requires a current `capability` for the actor (`design_publish` or `policy_publish`) **and** origin source authority (source-owner role that can still read every bound node) → `{publication: {..., status: "published", approvedBy, capability: {id, revision}}}`. The capability's version, status and expiry are rechecked by the storage transaction guard immediately before submission (`workbench/service.check_source_deadlines`); `403 publication-capability-required` / `publication-authority-required` |
| POST `/studio-api/publications/{id}/grants` | Destination project owner; `{roles}` → `201 {grant: {id, publicationId, publicationRevision, publicationHash, originProject, destinationProject, roles, revision, status}, reference}` where `reference` is the `published-asset` source reference. A new publication revision needs a new grant; changed roles bump the grant revision |
| POST `/studio-api/publications/{id}/withdraw` | Origin owner or the approving publisher; `{}` → `{publication: {..., status: "withdrawn", recallNeeded: boolean}}`. Destination IDs stay internal recall metadata |
| GET `/studio-api/publications` | `?view=origin` (default): this project's publications; `?view=granted`: this project's grants. Paged with `limit` (1–100) and opaque `cursor` |
| GET `/studio-api/publications/{id}` | Origin members, or destination members whose role an active grant names |
| GET `/studio-api/publications/{id}/impact` | Origin members → `{publicationId, dependents: [{projectId, nodeIds}], coverage: {complete: false, unknown: ["restricted-or-unmapped"]}}`; only destinations where the caller is a current member able to read the dependents, no hidden IDs or counts |

Storage kinds: `publication` in the owner partition `publication:deployment`
(revision history and immutable per-revision snapshot blobs under
`Storage.key_for("publication:deployment", "publication", <id>, "revisions/<n>.json")`);
`pub_grant` in the destination partition `project:<id>`; `capability` in
`intake:deployment`.
