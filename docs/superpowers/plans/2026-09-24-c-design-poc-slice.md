# C — Design PoC Vertical Slice (Application Cutover) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Steps marked **live** follow the roadmap live-step rule.

**Goal:** Deliver one persisted G1–G4 vertical slice for one synthetic product, inside the customer-approved Studio flow. The slice runs through these steps:
1. Admitted documents
2. Cited PRD, confirmed by a planner
3. Case-aware flow, approved by a designer
4. Layout and state variants, generated as React on the platform kit, compiled in Code Interpreter, verified in Browser and checked by the fail-closed verification graph
5. Targeted edits with stability proof
6. Designer approval of an exact manifest
7. Exact-source release rebuild
8. Convention handoff ZIP and registered Git export

Every model and tool step runs as an `agentcore-execution` job through the B0 ledger, the B2 Runtime, Gateway, Interpreter and Browser. The slice also covers G5 change impact (C-01) and G6 anchored discussion (W-01), both on the same records.

**Architecture:**

- **Design record.** A new `design` storage record per product links:
  - `productId`
  - `procedureId`
  - PRD revisions
  - flow approval
  - the screen registry
  - the selected variants and state variants
  - the execution jobs
  - the React `run` and its rounds
  - the approval manifest
- **Routes.** `/design/*` routes are mounted in `workspace/http.py`. Each route:
  - checks project membership and collaboration actions
  - freezes inputs into an immutable manifest blob
  - verifies admissions (B0 intake)
  - reads the backend selector at admission
  - admits a ledger job

  The B2 dispatcher then runs it.
- **React delivery** extends the existing run/round/release/Git export path (`publishing-handoff/1`: "do not create an unrelated handoff store"). A completed `design.generate` execution creates a `run` with an `outputType: "react"` round, whose sources and bundle hashes come from the Interpreter compile. Approval binds the design manifest to that round, and release reuses `process_release` (`workspace/releases.py:101-168`). The handoff ZIP is derived from the release `source.zip` bytes.
- **Web.** The existing Studio tabs keep their order and labels. A shared `StudioScope` provides project, product, model and output type to every tab. `ProcessStudio`, `Gallery`, `Playground` and `Assets` gain panels.
- **Legacy path.** The legacy `design_*` WebSocket path stays available and labelled.

**Tech Stack:** Python 3.12 workspace API, and the web app (React 18, esbuild, `node --test`). Every service dependency comes from B0–B2 and the engine.

**Roadmap:** unit 7. Its dependencies must all be merged and gated:
- B0 ledger, intake and sharing
- the engine
- B1 PASS for Runtime, Interpreter, Browser, Identity, Memory, Models and Admission
- B2

**Owning contracts:**
- `workspace/REACT_CONTRACT.md`: design manifest in run approval, handoff derivation
- `workspace/ONTOLOGY_CONTRACT.md`: model-inferred candidates via execution completion; design anchors
- `docs/CONTRACTS.md`: `/design/*` routes, errors, storage kind
- `workspace/AGENTCORE_CONTRACT.md`: operation stage lists
- `platform/docs/ARCHITECTURE.md`: the C row

**Acceptance cases:**
- GEN-01, GEN-04, GEN-05
- HAND-01 through HAND-05
- ONT-02, ONT-03, ONT-05
- IMP-01 (design subset)
- AUTH-01 and AUTH-08 (application paths)
- ROLL-02 and ROLL-03
- the vertical acceptance case (`AGENTCORE_CONTRACT.md:744-765`), design subset
- REQUIREMENTS §8 success criteria, measured

## Global Constraints

- The roadmap constraints apply.
- **Backend.** Read at admission from the B2 selector and frozen per job. If the selector is `lambda`, or the project is not in the cohort list (SSM `/bank-platform/design/cohort`), design execution routes return `503 design-backend-unavailable`. There is no legacy fallback for the new slice. The legacy Studio path is a separate, labelled feature.
- **Approvals.** The planner confirms the PRD (`publish` action). The designer or owner approves the flow and the screens (`approve`). The designer, developer or owner releases (`release`). The developer or owner exports (`export`). Agents approve nothing.
- **Recheck points.** These four actions recheck current authority, the admission decisions, the relevant ontology snapshot hash and the approval manifest hash:
  - dispatch
  - completion
  - release
  - export

  A stale input returns `409 approval-stale` with the changed component named.
- **Studio tabs.** Order and labels are unchanged, as `web/src/studio/Studio.tsx:13-19`:

  | Tab id | Label |
  |---|---|
  | `workspace` | UX 설계 작업실 |
  | `play` | UX 만들어보기 · 플레이그라운드 |
  | `process` | 프로세스·흐름 생성 |
  | `gallery` | 시안 갤러리 |
  | `assets` | 디자인 자산 |

  The `ProcessStudio` sequence 명세서 → PRD → 흐름 → 리뷰 is kept.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/workspace/design_api.py` | `/design/*` routes, records and admission |
| `platform/workspace/design_manifest.py` | Input manifests, approval manifest, freshness checks |
| `platform/workspace/design_completion.py` | Ledger completion writes: design record, run and round, ontology candidates |
| `platform/workspace/storage.py` (modify) | Kind `design` |
| `platform/workspace/http.py` (modify) | Mount `/design`; handoff blob download through the existing binary blob response |
| `platform/workspace/collaboration.py` (modify) | Design anchors in `_anchor` |
| `platform/workspace/ontology_store.py` (modify) | `model-inferred` producer allowed only with `_origin="execution"` and `_completion_writes` |
| `platform/workspace/releases.py` (modify) | Design-manifest freshness recheck in `process_release` |
| `platform/web/src/studio/StudioScope.tsx` (create) | Shared project, product, model and output-type context |
| `platform/web/src/studio/design/*.ts(x)` (create) | Design API client, models and panels |
| `platform/web/src/studio/{Studio,ProcessStudio,Gallery,Playground,Assets}.tsx` (modify) | Scope provider and panels; tabs unchanged |
| `platform/tests/test_design_api.py`, `platform/tests/test_design_completion.py`, `platform/tests/test_design_release.py` | API tests |
| `platform/web/test/studio-*.test.cjs` | Web tests |

---

### Task C1: Design record and read routes

**Interfaces:**

**Record** (kind `design`, id `design-` + 16 random hex characters; review round 11, AE3). One product may have several designs, one per procedure segment, for example when E13 returns `contract-capacity`. A `design_index` record `di-<productId>` holds `{designs: [{designId, procedureId, title}]}` and is updated in the same transaction as design creation. `(productId, procedureId)` is unique: a second create → `409 design-exists`. Each design has independent contracts, runs, approvals and handoffs.

```python
{"id", "projectId", "productId", "procedureId", "status": "active",
 "prd": {"revision": int, "hash", "status": "draft"|"confirmed", "issues", "jobId", "blobKey", "confirmedBy?", "changeReason?"},
 "flow": {"hash", "snapshotHash", "approved": {"by", "at", "hash"} | None},
 "registry": {screenNodeId: sid},
 "screens": {screenNodeId: {"variants": [{"id", "compositionHash", "blobKey"}], "selected": "a"|None,
                             "states": {state: {"compositionHash", "blobKey"}}, "verification": {...}, "edits": [...]}},
 "runId": None | str, "round": None | int,
 "approval": None | {"manifestHash", "by", "at"},
 "metrics": {...}, "jobs": [jobId]}
```

**Routes**, all with the header `X-Workspace-Project`:

| Method and path | Action | Result |
|---|---|---|
| `GET /design?productId=` | read | The **list** of the product's designs, from `design_index` |
| `GET /design/{id}` | read | One design record |
| `POST /design {productId, procedureId}` | edit_product | Creates the record. The product must exist and be published; the procedure must be readable through `Ontology.procedure_snapshot`. |
| `GET /design/{id}/flow?case=<json>` | read | Deterministic, no model: `build_flow`, `expected`, `flow_issues`, `mermaid`, cases and truncation. It uses the current snapshot and the confirmed PRD. |
| `POST /design/{id}/flow/approve {flowHash, contractVersion?}` | approve (designer/owner) + edit_rules | **Flow approval** (review round 2, N15). The server does the following in one request:<br>1. It recomputes the flow.<br>2. It requires `flowHash` to equal the recomputed hash, a confirmed PRD and no flow issues.<br>3. It derives the Browser contract from the PRD, flow, registry, published pages and criteria **only**, with no compositions (E13; review round 7, AA4). Every later compile, Browser run and repair uses this exact contract.<br>4. It creates or updates **one** workspace `contract` record through the existing contract path: validation by `rules.validate_contract`, then the `_approval_put` approval at `http.py:807-813`, so `approval: {version, hash, actor, at}` exists exactly as `_run_approve` requires (`http.py:1049-1055`).<br>5. It stores `flow.approved = {by, at, hash, contractId, contractVersion, contractHash}` on the design record.<br><br>E13 always yields exactly one contract. A `contract-capacity` finding blocks flow approval and asks the planner to split the procedure into separate designs (review round 3, F8). |
| `POST /design/{id}/mining {collectionDecisionId, requestId}` | upload | Admits the `source.analyze` ledger operation (B2 S4 `interpreter.analyze`). Its input is one admitted **code-collection** decision (B0 intake I6a), which carries collection-relative paths and the resolver, so basenames can collide safely (review round 3, F12). Completion publishes `parser-extracted` candidates (E5 `candidate_graph`) through the staged ontology publication. The existing offline `ontology_jobs.submit` path is not used and stays offline-gated (`ontology_jobs.py:43-47`). |
| `GET /design/{id}/screens` | read | Lists screens with their variants, states, verification summary and route/strategy (E9) |
| `GET /design/{id}/screens/{screenNodeId}` | read | Compositions, the React page source (from the round), the verification report and the edit history |

- [ ] **Step 1: Tests** (`tests/test_design_api.py`):
  - Build the `WorkspaceAPI` fixture per `tests/test_workspace_http.py:65-69`.
  - Seed a project with members by role, and a published product, following `tests/test_workspace_collaboration.py`.
  - Publish the engine seed ontology through `publish_candidate`, then review and approve it with the owner role.
  - Assert:
    - A non-member → 404.
    - A planner can create the record, and can create a second design for another procedure segment of the same product. The list returns both, and their approvals and handoffs stay independent.
    - A designer can read the flow, and it has 4 cases with no issues.
    - After tombstoning `evidence-auto` through `review_node(decision="deprecated")`, the flow reports `unreachable-screen` or `dead-end`.
    - Flow approval with a stale `flowHash` → 409. A planner approving → 403.
    - A successful approval creates approved `contract` records whose `approval.hash == rules.contract_hash(normalized)`.
    - `POST /design/{id}/mining` admits `source.analyze`, and never calls `ontology_jobs.submit`. Its completion publishes Molecule, Organism **and PageTemplate** candidates (E5, E5a) from a synthetic three-screen collection with a shared layout (review round 19, AM1).
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): design record and deterministic flow read routes"`

### Task C2: Document admission, PRD extraction and candidate publication (P-01–P-03, O-02–O-04; Codex #2, #7)

**Interfaces:**

**Admission and PRD routes:**

| Method and path | Action | Behavior |
|---|---|---|
| `POST /design/{id}/sources {sourceRef, dataClass, kind: "document"\|"image"\|"code-collection", root?, resolverProfileId?}` | upload | Calls `intake.admission.request`, or `request_collection` for `code-collection` (review round 4, F12). `code-collection` requires a server-configured `resolverProfileId`, and a raw `resolver` field → `400` (review round 9, AB2). Returns `{decisionId, status}`, where status is `admitted`, `pending-review` or `blocked` with reasons. |
| `POST /design/{id}/prds {decisionIds, model, requestId}` | edit_product | Admits `design.extract`. See the checks below. |
| `POST /design/{id}/prds/{revision}/confirm {reason}` | publish (planner/owner) | Confirms the PRD. See the checks below. |

For `POST /design/{id}/prds`:
- Each decision is checked with `intake.admission.verify`.
- The input manifest is `{designId, decisions: [admission_ref], model, promptVersion}`.
- It is written with `put_blob_once` under `key_for(owner, "design", id, f"inputs/{hash}.json")`. The signature is `key_for(owner, kind, id, suffix)`, per `storage.py:47` (Codex #16).
- The call is `ledger.api().admit(owner, request_key=requestId, operation="design.extract", model=…, manifest={ref, hash}, admissions=[…], backend_config_revision=<frozen>, authorization_expires_at=<verified JWT exp × 1000>, project_authority=<from Collaboration>)`. There is no `now + constant` expiry (review round 2, N3).
- The response is `202 {jobId}`.

For `POST /design/{id}/prds/{revision}/confirm`:
- An empty issue list is required; otherwise `409 prd-issues`.
- It sets `prd.status = "confirmed"` with the change reason. It never edits values.

**Runtime `design.extract` stages** (B2):
1. `context`: read `admission.pages` for each decision
2. `generate`: `prd_extract.extract_prd`, then `guide_rules.extract_rules` for guideline decisions
3. `verify`: re-run the citation checks in the Lambda tool before finish

**`design_completion.finish_extract`** builds the `completion_writes` for `execution.finish`:
- a PRD blob: `put_blob_once` before finish
- a design record update: `prd.revision + 1`, the hash, the issues and status `draft`
- PolicyRule candidates, published through `Ontology.publish_candidate(..., _producer="model-inferred", _origin="execution", _completion_writes=…)`, combined into the one transaction. This is the ledger's trusted composition point (`ontology-tools/1` L305-314).

**Ontology store change:**
- `publish_candidate` accepts `_producer` ∈ {`model-inferred`, `verified-build`} **only** when `_origin == "execution"` and `_stage=True` is used by the ledger coordinator. `parser-extracted` keeps its existing path plus the same staged path for `source.analyze`.
- Any other caller with `model-inferred` → `ValueError`.
- This preserves model provenance (Codex #7).

**Review queue:**
- PolicyRule candidates appear in the planner queue: `GET /design/{id}/candidates?type=PolicyRule`.
- Asset candidates from `source.analyze` mining (E5) appear in the designer queue.
- Review uses the existing `/ontology/nodes/{id}/review` route. Role authority is enforced by `review_node` (`ontology_store.py:720-728`).
- Roadmap decision D-1 records the requirement conflict on who confirms UX rules.

- [ ] **Step 1: Tests** (fake dispatcher; the completion is invoked directly with fake receipts through `execution_fakes`):
  - An admitted decision lets the extraction admit.
  - A `pending-review` decision → `409 admission-required`.
  - A revoked grant between admission and dispatch → the dispatcher's `verify` fails with `grant-revoked`, and the job fails without a model call.
  - The completion writes the PRD and the candidates in one transaction. Inject a stale record version, then assert that neither the PRD nor the candidates are written.
  - A planner confirms. A designer confirming → 403.
  - Confirming with issues → 409.
  - Publishing `model-inferred` outside execution → `ValueError`.
  - The outgoing payload of the fake `engine.gate` contains no deny-listed term: seed the original with one, and the derivative has only the alias. This is the Codex #2 assertion.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): admitted-document PRD extraction and execution-bound candidate publication"`

### Task C3: Screen generation, compile, verification and rounds (G-01–G-03, V-01–V-04, R-01, R-02; Codex #1, #11, #23)

**Interfaces:**

**Admission — `POST /design/{id}/screens {screenNodeIds?, variants: 1..5, model, requestId}`** (generate: designer/owner):
- It requires a confirmed PRD and an approved flow, with the flow hash equal to the current flow hash.
- It computes the manifest `{designId, prdHash, flowHash, snapshotHash, screens, strategyPerScreen (E9), variants, model, catalogHash, conventionHash}`.
- It computes the worst-case call plan (E16 judge budget) and the completion scope (B0 ledger Y5). It rejects the request with `call-budget` or `execution-completion-scope` before admission (review round 6, Z2).
- It admits `design.generate`.

**Runtime `design.generate` stages:**

| Stage | Work |
|---|---|
| `context` | `ontology.context` and the procedure snapshot through Gateway |
| `generate` | `gui.generate_screen` per the screen strategy, then `generate_states` |
| `compile` | `react_project.project`, then the Interpreter compile. This runs once for the whole project, with the selected (default a) variant per screen. Each variant set is compiled as a separate project candidate, up to `variants` candidates. |
| `browser` | `contract.derive`, then the Browser verify per candidate, with `boxes_for` all testIds |
| `verify` | `verify_graph.run`, with `regenerate` bound to `gui` in adapt/compose modes and bounded by the deadline budget (GEN-05) |

**`design_completion.finish_generate`:**
- It creates a **new `run` per execution** (review round 3, F9). Rounds are 1–5 in `_run_approve`, `_download` and `create_release` (`http.py:1046,1159`, `releases.py:65`), so one execution's candidate variants (≤ 5) or repair rounds (≤ 5) are one run's rounds. A later compose, edit or regeneration starts another run. The design record keeps `runs: [{runId, operation, jobId}]`, and approval names `(runId, round)`. A generate request with `variants` + repair budget > 5 is rejected at admission with `round-capacity`. The run has the **exact fields `_run_approve` and `process_release` read**:
  - `contractId`, `contractVersion`, `contractHash`, all from the flow approval
  - `contract`: the normalized approved snapshot
  - `status: "completed"|"needs_changes"`
  - `outputType: "react"`, `mode: "generate"`, `visualPolicy: "exact"`
  - `designId`: the marker that routes release through the ledger (C4)
  - the criteria keys at the **top level of the run**, not nested (review round 3, N15): `projectId`, `productId`, `guidelineId`, `guidelineAssetId`, `catalogHash`, `ontologyHash`. `criteria.resolve_generation_context` (`criteria.py:35-45`) and `rules.py:16-17` read them there.
- There are no pre-seeded approvals in tests.
- It appends one **round per candidate**. Each round includes `htmlKey`, the compiled `previewHtml` blob, and `artifactSha256` equal to that blob's sha256. It also includes `sourceArchiveSha256` and `distArchiveSha256`, as `react_generation.py:189-215` produces them and `_run_approve` checks them (`http.py:1059-1090`). The round fields follow `react_generation.py:208-215`: `number`, `passed`, `sourceHash`, `bundleHash`, `catalogHash`, `artifactSha256`, `reportKey`, `sourceKey`, `distKey`, `screenshotKey`, `pageSources`, `functionalStatus`, `visualStatus`, `blockingFindings`, `checks`. It adds `designManifestInput` and `verification: {verdict, approvable, rounds, escalated}`.
- The blobs are written with `put_blob_once` before finish.
- It updates `design.screens[*].variants` and `states` with composition hashes and keys, and sets `design.runId`.

**Result codes and HTTP mapping:**
- The failure codes are `gate-refused`, `model-unavailable`, `budget-exhausted`, `knowledge-incomplete` and `unavailable:<service>`. They surface on `GET /jobs/{id}`.
- `GET /design/{id}/screens` shows `blocked` for an unavailable tester or reviewer. It never shows `pass`.

- [ ] **Step 1: Tests:**
  - A structured seed screen → zero model calls in the `generate` stage.
  - A fake Interpreter returning `ok: false` → the round has `passed: false`, blocking findings, and verdict `fail` or `blocked`.
  - A missing Browser observation → verdict `blocked`, the round is not approvable, and no regeneration runs.
  - A `GateRefused` from the fake boundary → the job fails with `gate-refused`, and no round is written. This asserts the actual job error contract (Codex #23).
  - An admission with a stale flow hash → `409 approval-stale` naming `flow`.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): execution-backed screen generation producing verified react rounds"`

### Task C4: Selection, targeted edits, approval manifest, release and handoff (G-04, A-01, R-03; Codex #12, #16, #17)

**Interfaces:**

**Selection and edit routes:**

| Method and path | Action | Behavior |
|---|---|---|
| `POST /design/{id}/screens/{screenNodeId}/select {variant}` | approve | Records the selected variant per screen. It creates **no** approvable state by itself |
| `POST /design/{id}/selection/compose {requestId}` | approve | Admits `design.compose` (review round 2, N16). The execution:<br>1. compiles the **exact** set of selected variants plus every required state composition into one react-kit project (E12 keyed by `(screen, state)`)<br>2. verifies it in Browser against the approved contracts<br>3. runs `verify_graph`<br>4. appends **one new round**<br><br>Only that round can be approved. Its `pageSources` list every screen and state page, and the handoff exports only files present in that round's source. |
| `POST /design/{id}/screens/{screenNodeId}/edit {targetId, instruction, requestId}` | generate | Creates a persistent **edit request** `design.edits[requestId] = {targetId, instructionDecisionId, status}` (review round 5, Y1). The instruction is admitted as a `prompt-text` decision (B0 intake I6a), and the decision id is stored on the request, so retries reuse it. The response is `202 {editId, status: "pending-review"}`. A blocked instruction → `409 instruction-blocked`. |
| `POST /design/{id}/edits/{editId}/start {requestId}` | generate | Allowed only when the stored decision is `admitted`, rechecked with `verify`. It admits `design.edit`, with that decision in `admissions` and stages context → generate (`edit.edit`) → compile → browser (boxes) → verify. It is idempotent per `editId`: exactly one execution. The UI polls `GET /design/{id}/edits/{editId}` and calls `start` after review; there is no automatic re-submission. Test: request → pending (zero model calls) → review → start → one execution; a second `start` returns the same job |

The edit result is accepted only when all of these hold (the edit instruction is an admitted `prompt-text` decision; see the edit route):
- `stability.stable`
- `layout_stable(before, after, excluded_testids=subtree_testids(composition, targetId))`, the checkpoint maps from B2 S5
- the verification verdict is `pass`

Otherwise the round is `needs_changes` with `unstable-edit`, and the reason is `target-moved`, `target-missing`, `layout-moved` or `changes`. This is an asynchronous job result, not a synchronous 409 (Codex #23).

**Legacy entry points are closed for design records** (review round 4, X4). A contract or run created by the design API carries a trusted `designId`, written only by `design_api` with the human writer and never accepted from request bodies. The shared entry points enforce the design protocol:
- `POST /runs` (`http.py:867`, `_run_create`) rejects a contract with `designId` → `409 design-contract-requires-design-api`. Design executions are admitted only through `/design/*`.
- `POST /runs/{id}/approve` (`_run_approve`, `http.py:1044`) on a run with `designId` → `409 use-design-approval`. The design approval route calls a shared `_run_approve_core` after its own compose-round, verdict and manifest checks, so the generic URL cannot approve a non-approvable design round.
- `POST /runs/{id}/...` refinement and baseline routes reject design runs in the same way.
- Contract edits (`_contract_edit`, `http.py:735-760`) keep trusted markers. `designId` and `designSnapshotHash` are **not** in `_EDITABLE` (`http.py:30`). They are copied from the stored record on every edit, and a generic edit of a design contract → `409 use-design-api`. Only `design_api` rewrites design contracts, through a shared `_contract_put_core` that preserves the markers. The preservation of `designSnapshotHash` through create and edit is tested directly (review round 5, X2/X4).
- Tests call those legacy URLs directly with a design contract or run and assert the 409s. They also check that a legacy contract or run is unaffected.

**Approval — `POST /design/{id}/approve {manifestHash, round, requestId}`** (approve: designer/owner):
1. The server rebuilds the **approval manifest** (`design_manifest.approval`):

   ```python
   {"schemaVersion": 1, "designId", "productId", "procedureId",
    "prd": {"revision", "hash", "confirmedBy"}, "flow": {"hash", "approvedBy"},
    "snapshotHash", "ontologyGeneration",
    "screens": [{"screenNodeId", "sid", "variant", "compositionHash", "states": {state: hash}}],
    "run": {"id", "round", "sourceHash", "bundleHash", "catalogHash", "contractHash"},
    "verification": {"verdict": "pass", "resultHash", "receiptHashes": [...]},
    "conventionHash", "admissions": [admission_ref], "model", "backend": {"name", "configRevision"}}
   ```

2. It requires the client `manifestHash` to equal `schema.digest(manifest)`, the round to be the latest `design.compose` round, and that round's verification to be approvable. It also requires `manifest.screens[*]` (the selected variant and state composition hashes) to equal the compositions recorded in that round's `designManifestInput`. A round produced before a selection change is therefore never approvable.
3. It writes the run approval through the existing approval path. Extend `_run_approve` (`http.py:1044-1110`) with an optional `designManifestHash` that is included in `approvalHash`, plus the design record's `approval`.
4. On success it records metrics (`approvedWithoutEdit`, edits). It records a Memory `selected-variant` event through an `exec_outbox` record written in the same approval transaction, which the reconciler delivers (B2 S6; review round 33, BA1).

**Generation input excludes generated usage** (review round 14, AH2). `procedure_snapshot(..., exclude_partitions=("design-usage-",))`, used for every **generation, verification and freshness** read, skips edges owned by server-managed generated-usage partitions during traversal, coverage and budgets. Stale `run-round` sources in old usage edges therefore cannot add `unmapped-or-inaccessible` or block regeneration. The **impact** view (C5) reads without the exclusion. Test: approve → revise PRD and flow (contract superseded) → regenerate → approve → release, all with the B0-sharing adapters active.

**Snapshot fingerprint excludes generated usage** (review round 13, AG1). `design_manifest.snapshot_hash(snapshot)` digests the procedure snapshot **without** the edges in this design's own generated-usage partition (`design-usage-<designId>`, provenance `verified-build`). Those edges are outputs of approval, not generation inputs. Every other node and edge, including their revisions, stays in the hash. The usage partition is bound separately in the approval record as `usageHash`, and its freshness is checked at export, where a mismatch → re-publish the usage, not `approval-stale`. Test: approve (publishing usage edges) → release → handoff → Git export succeeds, and a genuine asset change afterward still yields `approval-stale:snapshot`.

**Browser profile binding** (review round 27, AU2; `AGENTCORE_CONTRACT.md:379-382`). Each Browser observation (B2 S5) carries `browser: {service, engine: {product, revision, userAgent}, profileId, profileHash}` (review round 28, AU2):
- `engine` is captured over the **authenticated CDP connection** with `Browser.getVersion`. It is the same channel the verifier already uses for isolated-world axe (`browser.py:35-57`), and the protocol exposes `product`/`revision` (`playwright-core/types/protocol.d.ts:2183`). The session-status API does not report the engine version, so it is not used for that.
- `profileId` is the session's `profileConfiguration.profileIdentifier`, when present.
- `profileHash` is `digest({engine, profileId, verifierCodeHash: sha256(browser_core.py), axeMemberSha256, viewport, fontManifestHash})`, computed by the trusted observer from verified configuration plus the observed engine.

The observer binds it to the session id in the signed receipt. Test: the engine revision changes while the resource and profile ids stay unchanged → the `profileHash` changes, and approval becomes stale. The approval manifest includes `browserProfile`, the approved round's observed value. `check_current` compares it with the **current** execution profile's verified Browser profile at approval, release and export. A mismatch → `409 approval-stale:browser-profile`: the approval becomes historical, and a new compose round verified under the current profile plus a new approval are required. `design.release`'s terminal rule also requires the rebuild's Browser observation profile to equal the approved one. Test: change only the Browser profile, with unchanged source and bundle hashes and a passing screenshot → release is refused as stale.

**Freshness** (`design_manifest.check_current(host, scope, design, manifest)`) runs **with the requesting actor's scope**. Source audiences are role-specific (`ontology_sources.py:55`, `documents/library.py:195`), so the checks are evaluated for the person downloading or approving (review round 4, F3). It recomputes and compares:
- the snapshot hash
- the PRD hash
- the flow hash
- the admission `verify` for each decision
- the convention and catalog hashes
- each composition hash

A mismatch → `ApprovalStale(component)`. It is called:
- at release creation
- inside `process_release`, before the rebuild and before publishing (`releases.py:101-168`; add the call next to the existing `resolve_generation_context` rechecks)
- at handoff download
- at Git export

**Release** (review round 2, N17): the `POST /releases {runId, round}` API shape is unchanged. For a run carrying `designId`, `create_release` does **not** create a legacy `release` worker job (`releases.py:95`). Instead:
1. It writes the `release` record (`status: "queued"`).
2. It admits the `design.release` ledger operation with the frozen backend.
3. The Runtime rebuilds the approved source through the Interpreter with no model call, and runs the Browser against the approved contract and the approved screenshot at 2% tolerance.
4. The ledger completion writes the release artifacts and `status: "ready"` through the staged coordinator.

The comparison logic is extracted from `process_release` into `releases.verify_rebuild(report, release, run, original)`. The legacy worker and the ledger completion both call it, so the gate is identical. Legacy runs, those without `designId`, keep the existing worker path.

**Handoff:**
- `POST /design/{id}/handoff {releaseId, requestId}` (export: developer/owner) requires a `ready` release.
  - It reads the release `source.zip` through the existing hash-checked `read_archive`.
  - It builds `design_loop.handoff.build` with the design registry and convention.
  - It stores the ZIP once with `put_blob_once(key_for(owner, "design", id, f"handoff/{releaseId}.zip"), …, "application/zip")`.
  - It records `{releaseId, key, sha256, size, by, at}` on the design record.
- The convention is the synthetic seed, or the tenant copy from private storage `key_for(owner, "design", id, "convention.json")`.
- `GET /design/{id}/handoff/{releaseId}/blob?offset=` (current project `read` plus current source/data-audience checks on every chunk, per `AGENTCORE_CONTRACT.md:446`: a ready package is available to current authorized readers; only Git export needs developer/owner `export`) serves the stored ZIP through the existing **chunked binary download** pattern (`http.py:1125-1137`, `_release_download`): base64 body plus `X-Total-Size`, `X-Chunk-Size` and `X-SHA256`, with the offset range checks.
  - The route is placed next to the other blob routes, so it never passes through `_json` (Codex #16).
  - It re-runs `check_current` before every chunk.
  - Each completed download records a distribution event.

**Frontend acceptance** (HAND-02; `AGENTCORE_CONTRACT.md:417,451-455`; review round 7, AA6):
- `POST /releases/{id}/frontend {decision: "accept"|"reject", sourceHash, bundleHash, reason, requestId}` (developer/owner only).
  - It writes a separate, version-bound `frontend_acceptance` record: kind `frontend`, id `fe-<releaseId>-<n>`, with fields `{releaseId, sourceHash, bundleHash, approvalHash, decision, reason, actor, at}`.
  - The hashes must equal the release's, or the request → `409 frontend-stale`.
- **Reject** sets the release's `effectiveReadiness: "needs-changes"`, with the rejection record id. Handoff, Git export and ready-package downloads then deny with `409 frontend-rejected`. History is kept, and a later accepted release, from a new approval and rebuild, restores readiness.
- **Accept** records acceptance only. It does **not** authorize deployment, and it is not required for a ready publishing package (`AGENTCORE_CONTRACT.md:451`).
- Tests:
  - a designer or planner → 403
  - a stale hash → 409
  - export after a reject → 409, and after a new accepted release → 200
  - the acceptance record is not created by release completion itself
- Storage kind `frontend` is added, and the web `HandoffPanel` shows the state.

**Git export** (review round 3, F13): the existing `/releases/{id}/git` path with registered feature branches is kept (HAND-05), with the expected base wired end to end.
- `POST /releases/{id}/git {connectionId, connectionHash, confirmPublic?, expectedBaseSha, requestId}` keeps the existing destination safeguards: `connectionHash` and `confirmPublic` for public or unknown visibility (`git_service.py:84-92`; review round 14, AH3). It adds a 40-hex `expectedBaseSha`.
- The base comes from a new authorized lookup, `GET /git-connections/{id}/head` (export: developer/owner). The server resolves the registered connection's base-branch HEAD through the configured exporter (`git_export` provider API, using the existing token provider), and returns `{branch, sha, connectionHash}`. No client-supplied URL is involved.
- **Credential access** (review round 15, AI3). The token provider reads the registered Git credential (`git_service.py:64-72`), and today only the Worker role may read those secret ARNs (`infra/lib/workspace.ts:195-198`). The workspace `Api` role therefore gets `secretsmanager:GetSecretValue` on **exactly the same registered `gitSecrets` ARN list**, with no wildcard. This is added next to the worker grant and asserted by `check_infra`. A test covers a successful lookup for a registered connection and denial for an unregistered connection id; the synth assertion covers the IAM side.
- `ReleasePanel.tsx` (`:116`) and the design `HandoffPanel` fetch it before export.
- `create_export` freezes it in the `gitexport` record and job input. `process_export` passes it as `expected_base_sha` to the exporter, which already supports it (`git_export.py:504`); today the call omits it (`git_service.py:158`).
- A changed base → `409 git-base-changed`, with no commit.
- Tests run through the API and worker chain with the fake exporter:
  - a client-shaped request including `connectionHash`/`confirmPublic` with base unchanged → committed
  - base changed between request and processing → conflict
  - an identical retry → the same commit
  - the head lookup denied for a designer

**Human approval transaction** (review round 14, AH1). The compose execution has already finished, so approval does not reopen it or use `Ledger._finish`. `design_api.approve` is the **human-authorized coordinator**. It runs a single `storage.put_many(..., retry_conflicts=False, _writer=storage._human_writer())` that combines:
- the run-approval writes, from `_run_approve_core(..., stage=True)`. This is the approval logic refactored to return writes and checks instead of committing, and the legacy `_run_approve` calls it with commit.
- the staged usage publication, from `Ontology.publish_candidate(name="design-usage-<designId>", _stage=True, _origin="design-approval", _producer="verified-build")`
- the design record's `approval`
- checks:
  - a version check on the terminal compose job (status `succeeded`, unchanged)
  - the project record, for current human authority
  - the admission decisions

**Transaction normalization** (review round 15, AI1). Several sources contribute writes and checks for the same record: the approval helper writes both run **and** project (`http.py:761`), and `run-round` resolution fences the run. `storage.put_many` rejects a write and a check on the same record (`storage.py:213`). The coordinator therefore normalizes by `(owner, kind, id)` before submitting, with the same algorithm as `workbench.service.Service.commit` (`service.py:161-181`), extracted into a shared `workspace.storage.merge_transaction(writes, checks)`:
- a check whose version equals the write's `expected_version` is absorbed into that write
- a differing version → `409 source-changed`
- duplicate identical checks are deduplicated
- the 100-operation limit is enforced after merging

The test runs the complete approval transaction with the B0-sharing `run-round` adapter active, shared admission observations and an expired execution lease.

It works after the execution lease has expired, because it requires no current attempt. Test: an actual finish, then approval after the lease expiry, persists approval and usage atomically. An injected stale check → neither is written.

**Generated usage enters the impact graph** (review round 12, AF3). When a compose round is approved, the approval transaction above publishes one `USES` edge per asset actually used by an approved composition, **including alternatives** that the Screen's declared `COMPOSES` edges do not name. Each edge is `Screen USES asset`, which the edge table permits for any design level (`AGENTCORE_CONTRACT.md:45`), with provenance `verified-build` and the round's `run-round` source ref. It is published through the staged coordinator, into a partition `design-usage-<designId>` that each approval replaces. The C5 impact route then reaches the Screen from a changed alternative asset through ordinary reverse traversal (`ontology_impact.py:79`). Test: approve a screen that uses an allowed alternative absent from its declared `COMPOSES`, change that alternative, and assert impact lists the Screen and the approved round.

**Screen code layer** (review round 4, #22). The `design.release` completion publishes, through the staged coordinator, one `CodeFile` candidate per released page with `{path, language: "tsx", fileHash}`, and an `IMPLEMENTS` edge from that CodeFile to the Screen. `IMPLEMENTS` is code→design, per `AGENTCORE_CONTRACT.md:46`, and the producer is `verified-build`. The Screen's GUI layer is the approved round's screenshot reference, and its wireframe is the composition slot structure. A developer reviews the CodeFile candidates (`ontology_store.py:727`).

**Rejection — `POST /design/{id}/reject {reason}`** (approve):
- It records metrics.
- It creates V-05 PolicyRule candidates through `verify_graph.rejection_candidates` and `publish_candidate(_producer="declared")`, with a `run-round` source ref (B0 sharing).

**Artifact reads re-authorize lineage** (review round 3, F3). Stored-key authorization is not enough for a design-linked run or release. Every read of one re-runs `design_manifest.check_current` in its lineage-only form: current admission `verify` for every decision, and the `Sources.resolve` of every upstream source. This applies to:
- `GET /runs/{id}/blob` (`http.py:1139`)
- `GET /releases/{id}/blob` (`http.py:1120`)
- `GET /design/{id}/handoff/{releaseId}/blob`

The check runs **on every chunk request**. A failure → `403 source-revoked`, and the record shows `stale/withdrawn` per the `publishing-handoff/1` state table. Legacy records keep their current checks.

- [ ] **Step 1: Tests** (`tests/test_design_release.py`):
  - After the upstream document's `readRoles` removes the designer, or its admission decision is revoked: the designer's `/runs/{id}/blob?kind=source`, `/releases/{id}/blob?kind=source` and the handoff blob → 403.
  - A request for chunk 2, after revocation between chunks 1 and 2 → 403.
  - Approval with a wrong manifest hash → 409.
  - Approval of a non-approvable round → `409 not-approvable`.
  - A planner approving → 403.
  - After approval, a convention change → release creation returns `409 approval-stale:convention`.
  - A PRD revision → `approval-stale:prd`.
  - An admission revocation → `approval-stale:admission`.
  - An ontology change to an asset used on an approved screen → `approval-stale:snapshot`.
  - An unrelated ontology change → still current. The snapshot hash covers only the procedure snapshot.
  - The handoff ZIP entries equal the release source bytes, and the manifest carries `approvalHash`, `sourceHash` and `bundleHash`.
  - A designer or planner who is a current reader downloads the ready handoff → 200 with the correct content type; a removed member → 404; a designer starting a Git export → 403, a developer → allowed.
  - An unstable edit gives a `needs_changes` round with `unstable-edit`.
  - **End-to-end without pre-seeding:** flow approve → generate (fake ledger completion with fake receipts) → select → `selection/compose` → approve through the real `_run_approve` → release.
  - Approving a round produced before a later `select` change → `409 approval-stale:selection`.
  - Approving an earlier generate-round instead of the compose round → `409 not-approvable`.
  - A design run's `POST /releases` admits `design.release` and makes **no** Worker Lambda invoke; assert `FakeLambda.calls == []`. A legacy run's release still invokes the Worker.
  - The handoff ZIP contains every state page present in the approved round, and nothing else.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): exact approval manifest, freshness rechecks and release-derived handoff"`

### Task C5: Anchored discussion, change impact, versions and assets (W-01, C-01, O-06–O-08, O-05; Codex #18, #21, #22)

**Interfaces:**

**Anchors** (`collaboration.py`):
- Add `designId`, `screenNodeId`, `flowTransitionId` and `ruleId` to `_ANCHORS`.
- In `_anchor`, a `designId` resolves the design record and **sets `productId` from the record**, which satisfies the existing product requirement.
- `screenNodeId` must be in `design.registry`.
- `flowTransitionId` must be a transition of the current flow.
- `ruleId` must be readable through `Ontology.read([ruleId])` as a current PolicyRule.
- `round`/`pageId` continue to require the run, which is `design.runId`.
- The anchor stores every revision it references (review round 2, #21):
  - `designRevision`, the record `version`
  - for `ruleId`: the rule node's `revision` and `contentHash`
  - for `flowTransitionId`: the edge id plus the `revision` of both endpoint Screens and the current ontology `generation`
  - for `screenNodeId`: the Screen node `revision`

  A later read recomputes these values and shows the comment as `stale`, without deleting it, when any has changed. That covers independent ontology edits that do not touch the design record.

**Impact** (C-01):
- `POST /design/{id}/impact {changeId, kind, nodeIds, oldSource?, newSource?}`, where `kind` comes from `ontology_impact.CHANGE_EDGES` (`ontology_impact.py:12`), delegates to the authorized handler: `ontology_api.route(host, scope, claims, "POST", ["impact"], body={"changeId", "kind", "nodeIds", "expectedGeneration": <current generation>, …}, query)`. These are exactly the fields accepted at `ontology_api.py:107-108`.
- The existing source-aware visibility and generation checks apply.
- The response adds the design-level view: which of this design's screens and approved rounds appear in the witness paths. `restricted-or-unmapped` coverage is shown verbatim. No `can_read=lambda …: True` shortcut exists (Codex #18).

**Versions** (O-06):
- **Node version history** (review round 2, #22). `Ontology.source_history` is source-keyed and misses edits and reviews that do not change a source. This task therefore adds an append-only per-node history index, owned by Unit A and co-reviewed.
  - `publish_candidate` and `review_node` each add `{revision, contentHash, reviewState, generation, actor, at, changeReason?}` to a bounded (100 entries) `node-history` index in the **same** commit.
  - The immutable node object for each revision is retained, keyed by `contentHash` like the partitions.
  - `Ontology.node_history(node_id, limit=50)` returns the visible entries.
  - `GET /design/assets/{nodeId}/versions` uses `node_history`.
- `GET /design/assets/{nodeId}/diff?from=&to=` reads both retained revisions and returns a structured property diff.
- **Three layers** (O-01). For each renderable asset:
  - the `layers.code` is the kit mapping
  - the `layers.gui` snapshot is produced by a `design.generate` execution in `assets` mode, which compiles `react_project.asset_gallery` and captures per-asset Browser screenshots, then proposes `layers.gui.snapshotHash` updates as candidates
  - `layers.wireframe.blocks` are derived from the composition slots

  The asset browser shows all three.
- Lifecycle mapping for the UI:

  | UI label | Review state |
  |---|---|
  | 초안 | candidate |
  | 검토 | reviewed |
  | 활성 | approved |
  | 폐기 예정 | deprecated |
  | 보관 | tombstoned history, read-only |

**Asset browsing and extension** (O-07, O-08):
- `GET /design/assets?level=&screen=` pages through `Ontology.read` with its cursor. The using screens are resolved from **both** declared `COMPOSES` edges and the generated `Screen USES asset` usage edges (C4). The dependents query is `Ontology.closure([asset], direction="dependents")`, filtered to Screens, and it includes the `design-usage-*` partitions for this read. Each usage returns its evidence kind, `declared` or `approved-round` with the `{runId, round}` example (review round 20, AN2). Test: an approved alternative with no declared `COMPOSES` edge is listed with its round example.
- `POST /design/assets/proposals` wraps `publish_candidate(_producer="declared")` for UX-team additions and edits: new assets, `uxModel` metadata, rules and checklist items. The next generation uses them after human review, with no code deployment.

**Benchmark on ontology change** (O-05):
- `POST /design/{id}/benchmark {requestId}` admits a `design.generate` execution in `benchmark` mode for the golden cases, which is not approvable.
- It stores `benchmark.score` and a `compare` against the last baseline on the design record.

- [ ] **Step 1: Tests:**
  - A screen-only comment (`{designId, screenNodeId}`) succeeds. This is the old failure case.
  - An unknown screen → 400.
  - A rule anchor on a deprecated rule → 400.
  - A comment becomes `stale` after a design record update.
  - The impact route returns the design screens for a change to `amount-input`, and hides nodes from an inaccessible source.
  - The versions list after a review and a deprecation.
  - The diff shows the `uxModel` change.
  - A proposal creates a candidate that is invisible to generation until approved.
  - The benchmark regression case from E18 through the API.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(design): anchored discussion, authorized impact, versions and asset proposals"`

### Task C6: Studio UI — shared scope and panels, legacy path preserved (U-04, W-01, O-07; Codex #20)

**Interfaces:**

**`StudioScope.tsx`:**
- It exports `StudioScopeProvider` and `useStudioScope()` with `{client, project, role, actorId, product, setProduct, model, setModel, outputType, setOutputType, backend}`.
- It reuses `createWorkspaceClient` (`web/src/workspace/client.ts:37-83`), and the project resolution and hash handling from `Workspace.tsx:66-129`, extracted into a shared hook `useProjectScope()`. `Workspace` also uses this hook, so its behavior is unchanged.
- `Studio.tsx` wraps all tabs in the provider. The tab buttons and `TOOLS` are unchanged.

**ProcessStudio:**
- With a project and product selected and `backend.design === "agentcore"` (from `GET /config`, extended with the frozen-at-read selector state for display), it runs the new path. The steps are:
  1. **명세서**: source admission status per document
  2. **PRD**: `PrdCitations`, where each field shows its quote and page, plus planner confirmation
  3. **흐름**: `CaseFlow`, with the case selector, the Mermaid path, flow issues and designer approval
  4. **리뷰**: `VerificationPanel`, with verdict, blocked roles and findings with rule citations; `ApprovalBar`
- Otherwise the existing WebSocket path runs unchanged, labelled `기존 데모 경로 (정적 시안)`.
- The model selection and output type come from the scope, shared with Playground.

**Gallery:**
- In new-path mode it groups rounds by design and screen, with `VariantCompare` (side-by-side compiled previews from the round `distKey` via the existing run blob route) and `StateStrip`.
- Otherwise it shows the legacy drafts.

**Playground:**
- In new-path mode, clicking a rendered node (by `testId`) opens "이 요소만 수정". It posts the edit and shows the stability result: `변경 영역 외 이동 없음` or the moved testIds.

**Assets:**
- The ontology asset browser with level and screen filters, versions and diff.
- The candidate queues: the planner rules queue and the designer assets queue, gated by role.
- The proposal form.

**Discussion:**
- `CommentsPanel`, on screen, flow node and rule anchors, reusing `/comments`.

**Recovery:**
- Pending jobs are listed from `design.jobs`, then resumed by polling `GET /jobs/{id}`. The UI never re-submits automatically.

- [ ] **Step 1: Tests:**
  - `web/test/studio-tabs.test.cjs`: `TOOLS` ids and labels are exactly the five pairs above, in order.
  - `studio-scope.test.cjs`: a selected project and product in Workspace are visible to ProcessStudio, Gallery and Playground.
  - `studio-process.test.cjs`: with a mocked client, the new-path step order is 명세서 → PRD → 흐름 → 리뷰, and blocked verification disables `승인`.
  - The legacy path still calls `design_catalog`, `design_flow`, `design_runs` and `design_review` with unchanged payloads.
  - In `platform/tests/test_design_handler.py`, the legacy WebSocket contract tests pass unchanged. They are run explicitly in this task.
  - Recovery: a reload with a running job id shows progress and makes no new POST.
- [ ] **Step 2:** Run `(cd platform/web && npx tsc --noEmit && npm run build && node --test test/*.test.cjs)` and `cd platform && python3 -m pytest tests/test_design_handler.py -q`. Expect PASS.
- [ ] **Step 3: Commit** `git commit -m "feat(studio): shared scope and design panels inside the existing studio tabs"`

### Task C7: Cohort vertical acceptance and success criteria (live)

- [ ] **Step 1 (live, staging):** Configure the cohort, a single synthetic project:
  - Set the SSM selector `agentcore` for that cohort only.
  - Register the synthetic public provenance for the seed documents (B0 intake admin, IAM).
- [ ] **Step 2 (live):** Run the positive path with the demo account created by `AdminCreateUser`. That account's credentials are never written to any file. The path:
  1. admit
  2. PRD
  3. confirm (planner)
  4. flow approve (designer)
  5. generate with 3 variants
  6. edit one element
  7. approve
  8. release
  9. handoff download (developer)
  10. Git export to the registered synthetic repository (developer)

  Capture the job receipts: Runtime session, Interpreter session, Browser session, Gateway and Lambda, Memory event and the model call metadata.
- [ ] **Step 3 (live):** Run the negative cases from the vertical acceptance case (`AGENTCORE_CONTRACT.md:755-760`), design subset. None may produce a current approved or ready result:
  - a private cross-project id
  - capability swapping
  - revoked membership
  - a stale rule
  - an altered compiler archive
  - malformed Browser evidence
  - a Runtime crash
  - duplicate dispatch
  - cancellation
  - a partial download
- [ ] **Step 4 (live):** Rollback demonstration:
  1. Set the selector to `lambda`.
  2. New design execution returns `503 design-backend-unavailable`.
  3. In-flight jobs reconcile or fail explicitly.
  4. The legacy Studio path still works.

  Record the result (ROLL-03).
- [ ] **Step 5:** Measure the REQUIREMENTS §8 indicators through `benchmark.success_metrics`, using the planted-defect set (E18 golden plus planted defects):
  - G3 detection rate ≥ 90% (target)
  - Critical violations in approved output = 0
  - out-of-scope changes from edits = 0
  - G4 type-check pass = 100%
  - G5 reverse-trace recall = 100%
  - G4 "component match with the real operating screens" is **not measurable** in the PoC: the platform kit substitutes for the absent customer package. Record it as an explicit gap and do not claim it.
- [ ] **Step 6:** Record all evidence in the private assessment. Update the `platform/docs/ARCHITECTURE.md` C row with the date and "cohort evidence recorded privately". Open PR C.

### Task C8: Contracts and docs

- [ ] Update `REACT_CONTRACT.md` with:
  - the design manifest in run approval
  - the freshness recheck points
  - the handoff derivation from the release source
  - that the approval of a static legacy draft is not React approval (unchanged)
- [ ] Update `ONTOLOGY_CONTRACT.md` with:
  - the `model-inferred` producer via execution only
  - the design anchors
  - the asset proposal route
- [ ] Update `docs/CONTRACTS.md` with the `/design/*` routes, error codes (`design-backend-unavailable`, `admission-required`, `prd-issues`, `approval-stale:<component>`, `not-approvable`), the storage kind `design` and the backend labels.
- [ ] Update `REQUIREMENTS.md` §6 with one line: the delivery order (AgentCore platform first, then this slice) and the plan series link.
- [ ] Update `platform/README.md` with the Studio design path and backend labels.
- [ ] Update `platform/deploy.sh` step 4 (review round 3, #3). Before `aws s3 sync dist`, run `python3 ../../scripts/check_public_identifiers.py --patterns-file "$PUBLIC_DENYLIST_FILE" --no-tree --site dist`. It fails closed when the file is missing, and a hit aborts the upload. The demo site is a public path (REQUIREMENTS §7.3).
- [ ] Run the docs build (repo root): `NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build`. Then run `git diff --check`, and the public identifier scan.
- [ ] Commit: `git commit -m "docs(design): record the design slice contracts and delivery order"`

## Verification before PR

```bash
cd platform
python3 -m pytest tests/test_design_*.py tests/test_design_handler.py tests/ -q
(cd web && npx tsc --noEmit && npm run build && node --test test/*.test.cjs)
git diff --check
python3 ../scripts/check_public_identifiers.py --patterns-file ~/.config/public-denylist.txt
```

The PR C body lists:
- acceptance case IDs with O/L/U status
- the gate references
- the cohort
- the explicit gaps: customer component package absent, privacy redaction adapter unavailable, requirement conflict D-1
