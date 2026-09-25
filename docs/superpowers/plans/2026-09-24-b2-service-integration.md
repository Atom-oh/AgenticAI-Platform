# B2 — AgentCore Service Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Steps marked **live** follow the roadmap live-step rule.

**Goal:** Integrate the transports and capabilities that passed in B1 into production-shaped but **default-off** service adapters. This covers:
- a dedicated ontology execution Runtime that runs the design engine stages
- the separate ontology Gateway with scoped Lambda MCP tools (`ontology-tools/1`)
- the Code Interpreter compile adapter using the pinned tool archive
- the Browser exact-bundle verifier adapter
- Memory references
- the IAM-only dispatcher and reconciler
- scoped IaC and metadata-only telemetry

The production default remains the Lambda backend (ROLL-02). C admits the named cohort.

**Architecture:**

The **Runtime** (`platform/execution_runtime/`) is a new container. It is separate from the scenario `AgentsRuntime`:
- It receives only the `platform-execution/1` invocation fields.
- It holds the capability in memory.
- It calls Gateway tools with the reserved `_executionAuthorization` envelope inserted by trusted code.
- It runs `design_loop` stages.
- It invokes models only through the shipped `engine/gate.py` boundary inside the container.
- It records call intent through the ledger tool before every model, Interpreter or Browser call.

The **Lambda MCP target** (`platform/execution_tools/handler.py`) verifies:
- the capability, with `KmsVerifier`
- the current attempt, through the ledger `tool()` facade
- membership, audience and operation scope

Only then does it serve these tools: `ontology.context`, `ontology.source`, `ontology.impact`, `ontology.publish`, `execution.*`, `admission.pages` and `artifact.*`.

**Interpreter** runs the fixed `compile.cjs` and `analyze.cjs` operations from the archive. **Browser** runs the existing assertion DSL against the exact bundle through request interception.

**Tech Stack:**
- Python 3.12
- `bedrock-agentcore==1.22.0` (Runtime app, Code Interpreter and Browser tool clients)
- Playwright over the AgentCore Browser automation channel (CDP)
- CDK L1 `aws-bedrockagentcore`
- KMS

**Roadmap:** unit 6. Depends on B1 PASS for each gate whose capability is integrated. A capability whose gate failed stays unavailable and is not integrated.

**Owning contracts:** `AGENTCORE_CONTRACT.md` `execution-capability/1`, `ontology-tools/1`, Memory lifecycle, "Atomic publication and reproducible evidence" and `platform-execution/1`; `docs/CONTRACTS.md`; `REACT_CONTRACT.md`.

**Acceptance cases:** AUTH-02–06, AUTH-09; GEN-02/03; INT-*; BROW-*; MEM-01–03; RUN-04/05 L portions; ROLL-01 (bank Gateway unchanged).

**Exit:**
- a reviewed B-chain PR
- G0 evidence referenced
- IAM negative tests
- defaults retained (`DESIGN_BACKEND=lambda`, and no cohort admitted)

## Global Constraints

- The roadmap constraints apply.
- The existing `ToolsGateway`/`ToolsTarget`, the `AgentsRuntime` and `agentcore/gateway_tools.py` are not modified or reused. ROLL-01 compares their synthesized template sections before and after this change.
- Runtime, Interpreter and Browser have no plane/bridge invocation, no Registry write or reset, and no customer-profile lookup. Runtime has no direct DynamoDB or S3 data permission.
- Model-visible tool schemas exclude `_executionAuthorization`. Any model-supplied reserved field is rejected.
- Telemetry is allowlisted metadata only. Unverified sinks block activation (`ARCHITECTURE.md:206-215`).
- No per-call fallback. A missing service returns an explicit `unavailable` stage failure.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/execution_runtime/app.py` | Runtime entrypoint and stage orchestration for operations `design.extract`, `design.generate`, `design.edit`, `source.analyze` |
| `platform/execution_runtime/tools.py` | Gateway MCP client, envelope insertion and model-visible schema filtering |
| `platform/execution_runtime/interpreter.py` | Code Interpreter adapter: archive staging, fixed commands, observation receipts |
| `platform/execution_runtime/browser.py` | Browser adapter: CDP connection, interception, `workspace.browser`-compatible report |
| `platform/execution_runtime/memory.py` | Structured Memory events and revalidated retrieval |
| `platform/execution_runtime/Dockerfile`, `prepare_context.sh` | Image with `design_loop/`, `design_schema/` (packaged copies of `ontology_schema.py`, `ontology_ux.py`, `rules.py`, `react_quality.py`), `engine/` boundary modules |
| `platform/execution_tools/handler.py` | Lambda MCP target: capability verification and tool implementations |
| `platform/execution_tools/tool_schema.json` | `ontology-tools/1` wire schema including the reserved envelope |
| `platform/execution_tools/dispatcher.py` | IAM-only dispatcher: allocate, issue capability, invoke Runtime |
| `platform/execution_tools/reconciler.py` | IAM-only watchdog/reconciler (scheduled) |
| `platform/infra/lib/execution.ts` | `OntologyExecution` construct (flag `ontologyExecution=true`, default false) |
| `platform/workspace/check_infra.py` (modify) | `audit_execution_template` |
| `platform/tests/test_execution_runtime_*.py`, `platform/tests/test_execution_tools.py` | Offline tests with fake services |

---

### Task S1: Runtime image with packaged shared modules (Codex #19)

**Interfaces:**
- `prepare_context.sh` copies these into `_ctx/`:
  - `design_loop/`
  - `engine/{gate.py, model_catalog.py, llm*.py}`, plus the modules `engine/gate.py` imports (enumerate them with `python3 -c "import ast…"` in the script and fail on anything outside `engine/` or `api/common/pii.py`)
  - `api/common/pii.py`
  - `design_schema/` built from `workspace/ontology_schema.py`, `workspace/ontology_ux.py`, `workspace/rules.py` and `workspace/react_quality.py`

  The `design_schema` modules are copied **unmodified** into `_ctx/workspace/`, with an empty `__init__.py`, so `from workspace.ontology_ux import …` resolves.
  - **Import closure (review round 2, #19).** The script computes the transitive import closure of the seed modules with `ast`, including function-level imports. For example, `rules.state_coverage_issues` imports `workspace.change_requests` inside the function (`rules.py:34`).
  - The script copies every `workspace.*` module in that closure. It fails when the closure reaches a module outside the reviewed pure allowlist, which is `ontology_schema`, `ontology_ux`, `rules`, `react_quality`, `change_requests`, `guidelines`, `react_artifacts` and `browser_core`. `browser_core` is added in S5 and needs `playwright` (pinned `1.62.0`, the version in `react-kit/package.json`) in the Runtime `requirements.txt`, but no browser binaries, because the Browser is remote (review round 4, #19). The image test asserts `_ctx/workspace/` equals exactly this allowlist's closure, not a fixed count. `rules.py:80` imports `guidelines`, and `change_requests.py:139` imports `react_artifacts`. Both are standard-library only (review round 3, #19). It also fails on any module that imports `boto3`/`botocore` at any level.
  - The allowlist lives in `prepare_context.sh`. Changing it needs review.
  - **axe (review round 9, AC3).** The verifier loads `axe.min.js` from `AXE_PATH` (`browser.py:257,411`). `prepare_context.sh` runs the existing integrity-checked `python -m workspace.fetch_axe gates/package-lock.json _ctx/axe` (`fetch_axe.py`), which also brings its `LICENSE`, and the Dockerfile sets `AXE_PATH=/app/axe/axe.min.js`. `fetch_axe` verifies the lockfile integrity of the **npm tarball** before extracting (`fetch_axe.py:13-26`). It is extended to write `axe/manifest.json = {version, tarballIntegrity, members: {"axe.min.js": sha256, "LICENSE": sha256}}` from the extracted members. The image test compares the packaged `axe.min.js` with that recorded member digest, **not** with the tarball integrity (review round 10, AD4). The S5 staging run asserts `accessibility.status == "pass"` or `"fail"`, **never** `"incomplete"`, on the seed bundle.
  - Data resources: `design_loop/resources/` (the checklist base sets and the manifest with its hash) is copied with `design_loop`. The image test runs `python -c "from design_loop.verify_graph import design_checklist; ..."` against the seed PRD **inside the built image, without repository mounts**, and asserts a non-empty checklist whose resource hash equals the manifest (review round 6, Z6).
- The image test builds it and runs `docker run --rm <image> python -c "import design_loop.flow, design_loop.gui, design_loop.react_project, design_loop.verify_graph, engine.gate, workspace.ontology_ux, workspace.react_quality; from workspace.rules import state_coverage_issues; state_coverage_issues({'rules': []}); print('ok')"`. The call exercises the function-level import, so a missing module fails at build time and not during verification. It also runs one offline stage-dispatch smoke test: `python -m execution_runtime.selftest`, which runs the engine seed chain with fake deps.

- [ ] **Step 1:** Write `tests/test_execution_runtime_image.py`. It is marked `docker`, and CI skips it unless Docker is available. It asserts the import set and that `_ctx/workspace/` contains exactly those four modules.
- [ ] **Step 2–4:** Fail → implement the Dockerfile (`python:3.12-slim` pinned by digest, non-root uid 10001), `prepare_context.sh` and `selftest.py` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): runtime image with packaged design and boundary modules"`

### Task S2: Stage orchestration and model boundary (GEN-02, GEN-03; Codex #23)

**Interfaces:**
- `invoke(payload, context)` validates the invocation fields exactly:
  - `{schemaVersion: 1, executionId, attemptId, runtimeSessionId, capability, manifest: {ref, hash}, model, deadlineAt, backendConfigRevision}`
  - Unknown fields → error, with no tool call.

  The run order is:
  1. `execution.claim`
  2. the operation's stages from `OPERATIONS` (B0)
  3. `execution.finish` or `execution.fail`
- Prompt inputs (review round 3, F2): the Runtime **never** receives the deny-list. Normalization happens on the private side before transfer:
  - `admission.pages` returns admitted derivatives.
  - `ontology.context` returns, for model use, only two kinds of content (review round 5, #2/F2):
    - **structural fields**. Every string in them is still passed through private normalization, because they are user-authored too (review round 12, AF1):
      - canonical node ids (`node-<hash>`), levels and kit prop names/types are sent as-is; they are trusted schema or catalog symbols
      - binding paths must match the fixed binding catalog `ontology_ux.BINDING_PATH`, enforced at E3 validation and rechecked here
      - **user-defined identifiers** (slot names, condition ids, binding `field` names, local aliases) are replaced by **opaque tokens** (`slot-1`, `cond-1`, `field-1`) through a per-execution mapping. The mapping is held **in the Lambda**, and the Lambda reverse-maps model outputs that refer to those tokens before validation.

      A test puts canaries into a binding path (rejected at validation), a slot name and a condition id. It asserts that neither the serialized `ontology.context` response nor the model payload received by the fake gate contains any canary. Structural fields also include **Enum values** are sent only when every value appears in the trusted kit catalog's declared values for that component prop: `react-kit/catalog.json` `props` / `variationAxes`, pinned by `catalogHash` (review round 8, AB3). Any other enum value is user-authored content. It is treated as free text: sent only with an exact admission binding, otherwise replaced by an opaque token (`enum-1`, `enum-2`, …). Test: a canary inside an enum value never appears in the serialized `ontology.context` response.
    - **free text** (titles, intents, rule statements) **only when the exact text is bound to an admission** (review round 6, #2/F2):
      - (a) the node carries `extraction.admissionId`, and that admitted derivative contains the text verbatim, after whitespace normalization
      - (b) the text entered through a reviewed `prompt-text` decision whose normalized-text sha256 equals `sha256(text)`. Asset proposals (C5) and manual node edits made through the design API create such decisions.

      A referenced source alone is not enough.

    Any other free text is replaced by the node id. The response lists the consumed decision ids, and the Runtime adds them to the stage receipt `inputs` and the job `admissions` check. No unadmitted text reaches a model.
  - User edit instructions arrive as `prompt-text` admission decisions (B0 intake I6a).

  In the Runtime, `deps["normalize"]` is `engine.gate`'s identifier rule check run as **verify-only**. A hit → `execution.fail(code="normalization-blocked")`, because nothing should remain at that point. The gate still performs its own independent refusal.
- A heartbeat thread calls `execution.heartbeat` every 30 s for the whole invocation, including blocking model, Interpreter and Browser calls. Heartbeat failure cancels the in-flight service call best-effort and stops the stage (B1 N8).
- Model transport is single-attempt (review round 8, AB1). The Runtime sets `BEDROCK_READ_TIMEOUT` (default 120, within the 30–600 range `engine/llm.py:136-144` accepts). That makes the Bedrock adapter use `retries={"total_max_attempts": 1}`, so the SDK never silently re-issues a potentially billed call. A test asserts that the constructed client config has `total_max_attempts == 1`, and that a stubbed read timeout produces one wire attempt and an `unknown` call outcome.
- Model calls: `deps = engine.gate.design_deps(route="bedrock", model_id=payload.model, trace_id=executionId)`, wrapped so that every call:
  1. runs `execution.intent` first (`stage`, `kind: "model"`, and `min_remaining_ms` from the profile)
  2. then `execution.outcome` with usage metadata

  `engine.gate.GateRefused` → `execution.fail(code="gate-refused")`, with the refused types only. `design_deps` is used, not `agents/design_deps.make_deps`, whose scan raises a plain `RuntimeError` before the gate (`agents/design_deps.py:47`).
- Model selection: `payload.model` must be in `engine/model_catalog.MODEL_IDS` **and** in the B1-verified model set recorded in the backend configuration revision. Otherwise → `execution.fail(code="model-unavailable")`, with no substitution.
- Each stage emits a signed observation receipt through the trusted observer (`execution.stage`). The Runtime has no signing key; see S3.

- [ ] **Step 1: Tests** (fake Gateway client and fake `engine.gate`):
  - a happy-path stage order
  - an intent recorded before each model call
  - `GateRefused` → fail with `gate-refused`, asserting on the `GateRefused` class raised by the fake `engine.gate.stream`
  - an unknown model → `model-unavailable`, with zero model calls
  - an unknown invocation field → rejected before claim
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): runtime stage orchestration through the measured model boundary"`

### Task S3: Lambda MCP target and envelope filtering (AUTH-02–05, AUTH-09)

**Interfaces:**
- `tool_schema.json` declares each tool from `ontology-tools/1` plus the following, all requiring `_executionAuthorization: {capability, operationId}`:
  - `admission.pages`: `{decisionId, cursor?}` → one bounded batch of derivative pages (≤ 400 000 bytes) plus `cursor`, via B0 intake `pages_for`. The Runtime pages through all batches and verifies the concatenated `derivativeHash` (review round 17, AK1). Model-context limits still apply to what E6/E7 put into a prompt: pages are sent to the model in prompt-sized groups, and each group is separately boundary-checked.
  - `artifact.openManifest`: `{}` → handle for the job's frozen manifest (B0 ledger `open_manifest`)
  - `artifact.openPrior`: `{ref}` → handle for a manifest-listed prior artifact (B0 ledger `open_prior`)
  - `artifact.openInput` / `artifact.openOutput` / `artifact.readChunk` / `artifact.writeChunk` / `artifact.closeOutput`: the B0 ledger T7 handle operations (review round 8, AA3). All are trusted-call only.
  - `artifact.put`: `{stage, name, size, sha256}` → a write handle, with 256 KiB chunk transfer through `execution.transfer`
  - `artifact.get`: handle → bytes
  - `execution.claim`, `heartbeat`, `intent`, `outcome`, `stage`, `transfer`, `finish` and `fail`
- `handler(event, context)`:
  1. Validate the Gateway/target/tool identifiers in `context` against configuration.
  2. Verify the capability with `KmsVerifier` (B1).
  3. Load the job through the ledger, and require `attemptId`, `fence`, `sessionId` and the operation in its `operations`.
  4. Recheck project membership and source audience through the `Sources`-backed read paths.
  5. Dispatch. Responses are ≤ 512 KiB, and the error bodies are generic.
- Budgets per job: 60 **model-visible** tool calls (`ontology.context`/`source`/`impact`) and 4 MiB of retrieved text, per `ontology-tools/1`. Trusted control calls (`execution.*`, `artifact.*`, `admission.*`) are not counted against the 60. They are bounded by the ledger's own ceilings: `maxCallsByOperation` for model intents, 512 transfer chunks, and the op-entry bound (review round 9, Z2).
- **Observation signing:** a separate `ObserverFn` holds the observation key. Runtime sends raw service results (session IDs, task IDs, exit codes, output hashes). `ObserverFn` re-reads the service status APIs where they exist, validates the association and signs the receipt. Runtime never signs.
- Runtime-side tool authority (review round 2, N21):
  - `tools.MODEL_TOOLS = frozenset({"ontology.context", "ontology.source", "ontology.impact"})` is an explicit read-only allowlist.
  - `tools.model_catalog(schema)` returns only those tools, each with the reserved property and its `required` entry removed.
  - `tools.model_call(name, args)`:
    - rejects any name outside `MODEL_TOOLS` before the capability is inserted, with `model-tool-denied`
    - rejects `_executionAuthorization` in the args
    - then inserts the envelope
  - `tools.trusted_call(name, args)` is used by stage code only, never passed to a model, and may call control/write tools (`execution.*`, `artifact.*`, `admission.pages`, `ontology.publish`).
  - The design operations use plain completions (`deps["generate"]`) without model-directed tool calling, so `MODEL_TOOLS` is defense in depth. Tests assert that a model-originated `execution.finish` or `artifact.put` is refused.

- [ ] **Step 1: Tests:**
  - A capability for execution A used on execution B → denied.
  - An expired capability → denied.
  - A revoked kid → denied.
  - A spoofed `context` target id → denied.
  - A model-supplied `_executionAuthorization` → rejected by `tools.call`.
  - A budget overrun → `budget-exhausted`.
  - A response over 512 KiB → an error with no partial body.
  - Canary tokens do not appear in the log records captured by `caplog`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): scoped ontology Lambda MCP target with capability verification"`

### Task S4: Code Interpreter compile and analyze adapter (INT-*; Codex #13, #15)

**Interfaces:**
- `interpreter.compile(files, *, catalog_hash, contract, archive: {bucket, key, version, sha256}) -> build`:
  1. Start a session on the custom interpreter.
  2. Fetch and verify the archive with a fixed command: `aws s3api get-object` under the Interpreter execution role, then `sha256sum -c`, then extract to `/opt/tools`.
  3. Write `in.json` with `writeFiles`. The generated source is JSON data, never a command.
  4. Run exactly `node /opt/tools/react-kit/compile.cjs in.json out.json`.
  5. Read `out.json`.
  6. Stop the session.

  It returns the `compile.cjs` output shape (`ok`, `gates`, `sourceHash`, `bundleHash`, `files`, …). It also returns an observation with the session id, archive hash, Node version, architecture, exit code and output hash.
- `interpreter.analyze(request)` does the same with `source-analyzer/analyze.cjs`.
- File limits are **per operation** (review round 31, AY1):
  - `compile`: ≤ 4.5 MB and at most 24 source files and 128 KiB, the react-kit policy
  - `analyze`: at most 100 files, 100 KiB per file and 2 MiB decoded total (`analyze.cjs:10`, `ontology_analysis.py:26`), **and** a serialized request of ≤ 3 900 000 bytes, below the CLI's 4 000 000-byte envelope (`analyze.cjs:466`). The supported profile is the **intersection** of both bounds (review round 33, AZ3). Collections within the decoded limit but over the serialized one are split into analysis units by B0 intake I6a. S8 records this intersection in `AGENTCORE_CONTRACT.md` `source-analysis/1`, so the plans and the contract state one profile.

  Tests: an admitted 25-file collection completes analysis, 100 files are accepted and 101 are rejected; 24 files compile and 25 are rejected. Paths are validated by the kit policy **inside** the compiler; the adapter never interpolates paths into commands.
- The engine's `design_loop.react_project.compile_local` and this adapter share the request/response shape. C selects the adapter from the frozen backend.

- [ ] **Step 1: Tests** (fake code-interpreter client recording commands):
  - the command list is exactly the fixed sequence
  - an archive hash mismatch → failure before compile
  - no source path appears in any command string
  - `out.json` is parsed, and a malformed output → `incomplete`
- [ ] **Step 2 (live, cohort-staging account):** Run the engine seed project through the adapter. The `bundleHash` must equal the local `compile_local` result for the same files. Record this in the private assessment (INT reproducibility).
- [ ] **Step 3: Commit** `git commit -m "feat(execution): code interpreter compile and analyze adapter"`

### Task S5: Browser verifier adapter with layout boxes (BROW-*; Codex #11, #12)

**Interfaces:**
- `browser.evaluate(bundle_files, contract, *, reference_png=None, visual_tolerance, expected_hash, boxes_for: [testId] = ())`:
  - It starts a custom Browser session and connects Playwright over the session's authenticated automation (CDP) endpoint.
  - It uses the same interception and assertion logic as `workspace/browser.py::evaluate_html`. Refactor that logic into `workspace/browser_core.py`, a pure function that takes a Playwright `BrowserContext`, so the Lambda and the AgentCore paths share it. `workspace/browser.py` keeps its public functions and delegates to `browser_core`.
  - It returns the same report keys, plus `renderer: "agentcore-browser"`, the session observation and **per-checkpoint** layout boxes, `boxes: {"<ruleId>:<stepIndex>": {testId: [x, y, w, h]}}`. Boxes are measured at every `expectVisible` of a screen root, so every screen and state on a case path is covered, not only the initial load (review round 2, #12).
  - **Accessibility at every checkpoint** (review round 25, AS1). The existing verifier runs axe only initially and after each complete rule (`browser.py:341,374`). A packed rule could therefore show a conditional region and hide it again without inspection. `browser_core` runs the isolated-world axe check (`browser.py:35-78`) at **every** checkpoint, **before** the next navigation or case change:
    - every `expectVisible` of a page root
    - every `expectVisible … true` of a conditional node
    - every state page

    Each result is stored in `checkpoints[*].accessibility`. The report's `accessibility.status` is `fail` if any checkpoint fails, and `incomplete` if any checkpoint could not be inspected, including when the 120-second budget is exhausted. Both propagate to `verify_graph` as tester failures and `blocked` respectively.
    - Tests: a transient defect shown only while a conditional region is visible (on → off) is caught, while a clean control passes. These run through `evaluate_bundle` with Playwright in CI, and the Lambda path shares `browser_core`.
  - At the same checkpoints it records `checkpoints: {"<ruleId>:<step>": {case, page, state, visibleText, truncated}}`, capturing visible text **before** navigation. `visibleText` is capped at 16 000 characters with a `truncated` flag. E18's benchmark scores these checkpoints (review round 5, Y6).
  - `edit.layout_stable` compares the same checkpoint keys between the before and after runs.
  - A testId that is present before and missing after → unstable, `layout-missing`.
  - A checkpoint missing from either run → `blocked`, never assumed stable.
  - Large text: a second pass at 200% root font size reports `largeText: {status, overflow: [testId]}`.
- Network: the session runs in the B1-verified VPC mode, and every request is aborted except the intercepted bundle origin.

- [ ] **Step 1: Tests:**
  - After the `browser_core` refactor, `tests/test_workspace_browser.py` passes unchanged (Lambda path).
  - A fake CDP context reports `boxes`.
  - An external request is recorded in `networkRequests` and blocked.
  - A missing session → the tester is `unavailable`, which gives `blocked` in `verify_graph` rather than pass.
- [ ] **Step 2 (live, staging):** Run the engine seed contract. The required checks pass, axe results are present and Korean text renders. Record the profile, timings and the blocked-request test.
- [ ] **Step 3: Commit** `git commit -m "feat(execution): agentcore browser verifier sharing the assertion core"`

### Task S6: Memory references (MEM-01–03)

**Interfaces:**
- **Session index and bindings** (review round 33, BA2). `ListEvents` and `DeleteEvent` require `sessionId`, which is the execution id. The tool Lambda therefore keeps a bounded index record `mem_index` per namespace (storage kind added): `{namespace, sessions: [{sessionId, at}]}`, holding the most recent 20 within retention, as the contract requires (`AGENTCORE_CONTRACT.md:624-627`).
  - Each recorded event appends `{sessionId, eventId}` to a per-execution binding list `mem_events` (`exec-<id>`).
  - **The Runtime reaches the index through trusted tools** (review round 39, BG1), because it has no DynamoDB access. The tool Lambda adds two trusted-call tools, capability-scoped like the others. Both derive the namespace **server-side** from the capability's actor and project with the HMAC key, and never accept it from the caller:
    - `memory.sessions {}` → `{actorId, sessions: [{sessionId, at}]}`, up to 20 sessions. `actorId` is the server-derived namespace that `ListEvents`/`CreateEvent` require. The Runtime uses the returned value and never computes it (review round 40, BH1).
    - The namespace producer is `ExecutionToolsFn`. It computes `HMAC(org|project|actor)` with a dedicated KMS HMAC key through `kms:GenerateMac`, granted only to `ExecutionToolsFn` and `ReconcilerFn` and added to the S7 IAM matrix. The Runtime has no access to the key.
    - Test: a chain with **no pre-seeded namespace**: the Runtime calls `memory.sessions` → `CreateEvent(actorId, sessionId)` → `memory.bindEvent`. A second execution then calls `memory.sessions` → `ListEvents(actorId, sessionId)`. Stubber validation passes.
    - `memory.bindEvent {eventId}` → records `{sessionId: <current execution>, eventId}` in `mem_events` and updates `mem_index`
  - `execution_runtime/memory.py` calls `memory.sessions`, then Memory `ListEvents` directly with the returned session ids. After each `CreateEvent` it calls `memory.bindEvent`.
  - Test: Runtime → Gateway → index → Memory, with a fake Runtime whose table access is denied. A second execution recalls the first one's event. A request naming another namespace is impossible by schema, and is rejected if forged.
  - `memory.recent(namespace)` reads the index and calls `ListEvents(memoryId, actorId=namespace, sessionId=…)` per session.
  - Deletion queues `{namespace, sessionId, eventId}` triples.
  - Tests use a botocore `Stubber` that validates request shapes: two executions, recall across both, and revocation deletes each bound event.
- **Approval-time events go through an outbox** (review round 33, BA1). The human approval (C4) runs in the Workspace API after the execution has finished, so it has no Memory access. It writes an `exec_outbox` record `{namespace, sessionId: <compose execution id>, event, eventKey}` **in the same approval transaction**. `ReconcilerFn`, which holds the Memory grants, delivers it **idempotently** (review round 34, BB2):
1. It claims the outbox record with a conditional version write, `status: queued → delivering` with the claimer id and a lease. Concurrent deliveries lose the condition and stop.
2. It calls `CreateEvent` with a **stable `clientToken`** = the first 64 hex characters of `sha256(eventKey)`, so the service deduplicates retries. The SDK otherwise generates a fresh token per call (`botocore/handlers.py:286`).
3. It records the returned `{sessionId, eventId}` in the outbox and in `mem_events`, then sets `delivered`.
4. A crash after `CreateEvent` succeeded is re-delivered with the same token, which returns the same event, and reconciled.

Tests: two concurrent deliverers → exactly one `CreateEvent` path completes. A crash after `CreateEvent` → the retry uses the same `clientToken`, and exactly one event exists. A Stubber asserts the token. Test: approve → outbox → reconciler delivery → `memory.recent` returns the `selected-variant` event.
- `memory.record(namespace, execution_id, event)`:
  - `namespace = HMAC(key, org|project|actor)`
  - `event` is `{schemaVersion, kind: "selected-variant"|"rejected-reason"|"accepted-edit", refs: [{kind, id, revision, hash}], outcome, at}`
- `memory.recent(namespace, *, limit=20) -> events`
- `memory.revalidate(events, check) -> events`. It drops any event whose refs fail current `Sources`/ontology checks.
- There are no extraction strategies, and events have a 30-day expiry. On revocation, deletion is queued through the reconciler.

- [ ] **Step 1–4:**
  - **Complete recovery under the reconciler principal** (review round 20, AM2). A `source.analyze` execution whose lease was lost after the analyze receipt is reconciled by `ReconcilerFn`, using a prefix-restricted fake S3 that enforces the IAM row exactly. The reconciliation re-validates the sources, stages and publishes the ontology partition, and completes the job. The test fails if any prefix is removed.
  - **Deletion runs under the reconciler principal** (review round 19, AM2). A revocation enqueues `{namespace, eventIds}`. `ReconcilerFn` performs the deletion and records completion or a retry, and a deletion request alone does not count as done. The test asserts the fake Memory client received `DeleteEvent` from the reconciler path, and that recovery `reconcile` reads outputs successfully under the reconciler's grants in the staging IAM test.
  - A test that another namespace returns nothing.
  - A test that a stale revision is dropped.
  - A test that deletion is recorded.
  - Then implement and pass.
- [ ] **Step 5: Commit** `git commit -m "feat(execution): scoped structured memory with revalidation"`

### Task S7: Dispatcher, reconciler and IaC (default off)

**Interfaces:**
- `dispatcher.handler(event)` is IAM-only and is triggered by a DynamoDB stream filter on `task = agentcore-execution, status = queued`, or by a direct IAM invoke from the API. It runs:
  1. `ledger.dispatcher().allocate`
  2. `execution_authority.issue`
  3. `bedrock-agentcore:InvokeAgentRuntime` with the invocation fields
- `reconciler.handler` runs every minute on EventBridge Scheduler. It discovers work through a **durable due-work index** (review round 35, BC1):
  - Kind `exec_due` lives in the fixed partition `execution:due`. Each record is `{id, targetOwner, kind: "job"|"outbox"|"deletion"|"report", ref, dueAt, status: "pending"|"done"}`. `report` entries come from legacy refusals and are handled by `resolve_orphan` (B0 ledger; review round 37, BE1). The target partition is in **`targetOwner`**, because `Storage._prepare` strips `owner` (`storage.py:138-139`; review round 36, BD1).
  - **Due-time ordered ids** (BD2): `id = "d" + zero-padded 13-digit dueAt + "-" + digest({targetOwner, kind, ref})[:24]`. The hash includes the target partition and kind, because artifact ids are project-local and two projects can report the same local id at the same due time (review round 41, BI1). Test: identical local ids and due times in two projects both get indexed and recovered by one scheduler event. so the sort-key order `list_page` returns (`storage.py:248-290`) is due-time order.
  - Changing `dueAt` writes a **new** entry and marks the old entry `status: "done"`, both as version-conditioned writes in the same `put_many`. `put_many` supports only puts and checks (`storage.py:187-224`), so removal is a tombstone and never a delete.
  - At a terminal state the entry is marked `done`. `Storage._prepare` sets a `ttl` of 24 hours on `exec_due` records whose `status` is `done`, extending the existing TTL rule for `job` at `storage.py:148-149`, so tombstones expire.
  - Each index write counts toward the ledger's `completion_scope` non-source operations (B0 Y5).
  - The reconciler reads with `list_page("execution:due", "exec_due", prefix="d")` from the start, and continues with the returned cursor **until the first entry whose `dueAt > now`**. It skips `done` entries, is bounded to 500 due entries per invocation, and resumes at the next tick. Because entries are ordered by due time, anything due is always ahead of future work.
  - Tests go through the actual `Storage` lifecycle: create → reschedule (new entry, old one done) → terminal (done, ttl set). Discovery tests use 150 future entries plus later-arriving expired entries: every due entry is processed within one invocation, including those beyond the first 100 records.
  - Test: a scheduler event with **no supplied ids** discovers an expired job in project A and an outbox record in project B, handles both, and releases the job's quota.
- The `OntologyExecution` construct, behind `-c ontologyExecution=true` (default false), contains:

  | Resource | Configuration |
  |---|---|
  | Dedicated Runtime | Its own role: model invoke only for the verified allowlist, the Gateway invoke, workload token actions. No DynamoDB or S3. |
  | Workload identity | Dedicated |
  | Cognito machine user pool and client | `selfSignUpEnabled: false` |
  | OAuth2 credential provider | For the machine client |
  | Ontology Gateway | `CUSTOM_JWT` authorizer |
  | Lambda MCP target | `ExecutionToolsFn`. See the IAM matrix below |
  | Keys | `ObserverFn`, and the KMS keys adopted from B1 or newly created |
  | Interpreter | Custom Interpreter with network `SANDBOX` and archive-only S3 access |
  | Browser | Custom Browser, VPC isolated |
  | Memory | No strategies |
  | Execution Lambdas | `DispatcherFn` and `ReconcilerFn` |

- **IAM matrix (review round 2, N18).** Workspace owner partitions are hashed (`storage.py:117-119`), so DynamoDB `LeadingKeys` cannot scope per project. Callers that must read arbitrary project partitions get item-level table actions, and the application authorization in the facade is the project boundary, as with the existing `Api` Lambda. Every caller without such a need gets none.

  | Caller | DynamoDB (workspace table) | S3 (private bucket) | Other |
  |---|---|---|---|
  | Runtime | none | none | `bedrock:InvokeModel*` on B1-verified model/profile ARNs; `bedrock-agentcore:InvokeGateway` on the ontology Gateway; `GetWorkloadAccessToken*`; `bedrock-agentcore:GetResourceOauth2Token` on **exactly** the ontology Gateway's OAuth2 credential provider and its token vault ARN, needed for the Identity → OAuth provider → JWT Gateway chain (`AGENTCORE_CONTRACT.md:248-263`), with no SigV4 substitute (review round 24, AR1). The staging test acquires a credential and invokes the Gateway under the exact Runtime policy, and a request for another provider is denied; `StartCodeInterpreterSession`/`InvokeCodeInterpreter`/`StopCodeInterpreterSession` on the custom Interpreter ARN; `StartBrowserSession`/`ConnectBrowserAutomationStream`/`StopBrowserSession` on the custom Browser ARN; `CreateEvent`/`ListEvents`/`DeleteEvent` on the Memory ARN; logs |
  | ExecutionToolsFn (tools + staged completion coordinator) | `GetItem`/`PutItem`/`Query`/`TransactWriteItems`/`ConditionCheckItem` on the table | `GetObject` on `workspace/*/{design,ontology,job,run,release,adm_decision,docrevision,asset}/*`. `PutObject` on `workspace/*/job/*/out/*`, `workspace/*/ontology/*` (the immutable staged partitions and indexes written before the manifest pointer, `ontology_store.py:104-112,365`), `workspace/*/run/*` (round artifacts), `workspace/*/release/*` (rebuilt `source.zip`, `dist.zip`, `site/*`, manifest and report, `releases.py:137-160`) and `workspace/*/design/*`. `DeleteObject` only on `workspace/*/job/*/out/*` (review round 3, N18) | KMS `GetPublicKey` on both keys; `ssm:GetParameter` on the deny-list parameter; costguard table access as the existing worker |
  | Runtime → ObserverFn, ExecutionToolsFn → ObserverFn | — | — | `lambda:InvokeFunction` on `ObserverFn`, granted to the Runtime role and to `ExecutionToolsFn`, which forwards stage results for signing (review round 5, N18) |
  | ObserverFn | `GetItem` (key registry, job) | `GetObject` on `workspace/*/job/*/out/*` | KMS `Sign` on the observation key; Interpreter/Browser session status reads |
  | DispatcherFn | Same item actions as ExecutionToolsFn | `GetObject`/`HeadObject` on `workspace/*/{adm_decision,asset,docrevision,design,job}/*` and `workspace/*/ontology/*`. These are **read-only**, for admission `verify` (source `resolve` issues `HeadObject`, `ontology_sources.py:104`; derivative and inspection hashes are read through `storage.py:366`) and for manifest hash checks before allocation and issuance (review round 21, AO1). Test: a complete valid dispatch, and a revoked-admission dispatch, which is refused before issuance, both run under a fake S3 that enforces exactly this row | KMS `Sign` on the capability key; `InvokeAgentRuntime` on the ontology Runtime |
  | ReconcilerFn | Same item actions | `GetObject`/`HeadObject` on `workspace/*/job/*/out/*`, the job manifest keys and manifest-listed prior prefixes (`workspace/*/{run,release,adm_decision,design}/*`), needed for full receipt, output and manifest validation during `reconcile` (`storage.py:354-366`). Also `GetObject` on `workspace/*/asset/*` and `workspace/*/docrevision/*`, for current source validation (`ontology_sources.py:104`), and `GetObject`/`PutObject` on `workspace/*/ontology/*`, for ontology reads and the staged partition and index objects the recovery completion writes (`ontology_store.py:87-112`; review round 20, AM2). `DeleteObject` on `workspace/*/job/*/out/*` | Session stop actions; KMS `GetPublicKey` on the observation key (receipt verification); `bedrock-agentcore:ListEvents`/`DeleteEvent`/`CreateEvent` on the dedicated ontology Memory ARN only, for the revocation deletion queue and the approval outbox of S6 (review rounds 19 and 33, AM2 and BA1) |
  | Workspace `Api` Lambda (existing, extended) | Existing grants (`infra/lib/workspace.ts:234`) | Existing grants, plus `PutObject` on `workspace/*/adm_decision/*` (derivatives and prompt blobs) | `ssm:GetParameter` on `/bank-platform/design/backend`, `/bank-platform/design/cohort` and the deny-list parameter; `lambda:InvokeFunction` on `DispatcherFn` only (review round 4, N18); `secretsmanager:GetSecretValue` on the registered Git secret ARNs only, for the HEAD lookup (C4; review round 15, AI3) |
  | Interpreter role | none | `s3:GetObject` and `s3:GetObjectVersion` on the archive object ARN only, with the condition `StringEquals s3:VersionId = <pinned version id>`. A request that names a `VersionId` requires `GetObjectVersion` (review round 26, AT1) | none |
  | Browser role | none | none | logs only |

  The action names are verified against the service authorization reference during B1 and recorded. Every row gets a **positive** staging test (the required operation succeeds) and a **negative** test (an adjacent forbidden action is denied).
- **Service-spend alarm** (N7): an AWS Budgets cost budget with an actual-spend alarm scoped to the Bedrock and AgentCore services, notifying the operator topic. Cohort admission in C7 requires it to exist.
- The backend selector is the SSM parameter `/bank-platform/design/backend`, with values `lambda` or `agentcore` and default `lambda`. It is read **at admission** by C and frozen into the job's `backendConfigRevision`.
- `audit_execution_template` asserts all of the following:
  - no NAT gateway or IGW
  - no public listener or Function URL
  - no `*` resource without a condition
  - the Runtime role has no `dynamodb:*` or `s3:*`
  - the Interpreter role has `s3:GetObject`/`s3:GetObjectVersion` only on the archive object, conditioned on the pinned `s3:VersionId`. The staging test fetches the configured version successfully, and fetching another version or the fixture/source bucket is denied under the actual Interpreter principal
  - the Browser role has logs only
  - Memory strategies are empty
  - the Gateway authorizer is `CUSTOM_JWT`
  - the bank `ToolsGateway` template section is byte-equal to its pre-change snapshot (ROLL-01)

- [ ] **Step 1:** Write the construct and the audit tests, and run an offline synth with the flag on and off. With the flag off, the template is unchanged versus `main` except for the absent resources. Commit.
- [ ] **Step 2 (live, staging):** Deploy with the flag on and the backend `lambda`. Verify:
  - an IAM negative: the Runtime role cannot `GetItem` on the workspace table
  - the bank Gateway configuration is unchanged
  - telemetry sinks match the B1-verified destinations

  Record the results.
- [ ] **Step 3: Commit** and open the B2 PR. The body lists the gate references and states that the default is `lambda`.

### Task S8: Contracts

- [ ] **Contract amendment for the tool-call budget** (review round 10, Z2): amend `ontology-tools/1` (`AGENTCORE_CONTRACT.md:300-301`) to state:
  - "A job has at most 60 **model-visible** tool calls and 4 MiB retrieved text."
  - Trusted Runtime control calls (`execution.*`, `artifact.*`, `admission.*`) are bounded instead by the execution profile's `maxCallsByOperation`, transfer-chunk and operation-entry ceilings (B0 ledger).

  The amendment is co-reviewed by the platform-runtime and ontology maintainers **before** S3 relies on it. Until it merges, S3 counts every call against 60, and `design.generate` preflight rejects plans exceeding it.
- [ ] In `AGENTCORE_CONTRACT.md`, record the concrete bindings:
  - Runtime name
  - the tool list including `admission.pages`/`artifact.*`
  - the envelope schema
  - the observer role
  - backend selector semantics
  - the telemetry allowlist fields
- [ ] In `docs/CONTRACTS.md`, add the backend labels `Lambda · isolated legacy verifier` and `AgentCore Runtime · Code Interpreter · Browser`, and the `unavailable` error codes.
- [ ] Commit: `git commit -m "docs(execution): record B2 service bindings"`

## Verification before PR

```bash
cd platform
python3 -m pytest tests/test_execution_runtime_*.py tests/test_execution_tools.py tests/test_workspace_browser.py tests/ -q
(cd infra && CDK_DEFAULT_ACCOUNT=000000000000 npx cdk synth BankPlatform --quiet -c planeDeployed=false -c ontologyExecution=true \
  && python3 ../workspace/check_infra.py cdk.out/BankPlatform.template.json)
git diff --check
```
