# Ontology/AgentCore implementation register

Date: 2026-10-12. Scope: source/design execution follow-up to source integration of ontology branch `c234292` and main
`f5157c7`. This register is subordinate to SPEC, the [architecture](ARCHITECTURE.md)
and the owning contracts. It records implementation and gaps, not deployment.
Final review/CI evidence belongs to the exact integration PR HEAD.

## Implemented code and remaining boundaries

| Area | Concrete implementation and focused tests | Remaining requirement |
|---|---|---|
| Canonical ontology | `workspace/ontology_store.py`, `ontology_sources.py`, `ontology_ux.py`, `source-analyzer/`; `test_ontology_*` | Preserve old parser-edge/tombstone capacity limits; run the independent bounded oracle and current-source/revocation cases |
| B0 execution | `workspace/execution_ledger.py`, `workspace/storage.py`, legacy worker/expiry/repair guards; `test_execution_ledger.py`, `test_execution_guards.py` | The production verifier, real admission/prior adapters, queue dispatcher and atomic source publication now have a unified caller path; design-stage API/Runtime wiring is present; live probes remain |
| Private intake | `intake/{records,admin_handler,inspect,derivative,admission,review,collection,prompts,images,imaging,transcription,worker,audit}.py`; `test_intake_*`; `documents/library.py` | Configure and verify private policy/deny-list/reviewer administration, source bindings and any required redaction service; no customer-data admission from project ownership alone |
| Sharing and derivative reads | `workspace/publications.py`, `ontology_sources.py`, `http.py`, `batches.py`, `releases.py`, `git_service.py`; `test_publications.py`, `test_sources_adapters.py`, project React-flow tests | Deployed cross-project grant/withdrawal checks, source-bound UI acceptance and prospective cleanup/recall evidence |
| Dedicated service adapters | `ontology_runtime/{authority,authorization,capability,dispatch,tools,workflow,interpreter,browser,identity,memory}.py`; `test_agentcore_*`; `infra/lib/ontology-stack.ts` | Dedicated Runtime/IaC now select B0 through `execution_{entrypoints,protocol,tools,workflow}.py`; `ac_*` adapters remain isolated compatibility code. Service gates and production activation remain unverified |
| Offline design engine | `design_loop/{knowledge,derive,guide_rules,prd_extract,flow,composition,gui,edit,contract,coverage,verify_graph,react_project,handoff,benchmark}.py`; `test_design_*`, synthetic seed and golden fixtures | Persisted API/Runtime/UI candidate and selected-screen deterministic compose dispatch are present in design_api/design_execution/design_workflow/design_stages. Live vertical acceptance and service gates remain; legacy process generation is a distinct caller |
| Public-output controls | `scripts/check_public_identifiers.py`, `api/common/public_scan.py`, legacy public-writer hooks and CI; `test_public_identifiers.py`, `test_public_scan.py` | Configured deny-list coverage and reviewed media registry remain necessary. CI's inherited allow-missing mode warns when its secret is absent; that result is not evidence that customer identifiers were scanned. A configured runtime SSM deny-list must be readable and nonempty; failures block publication. Only an unset parameter name keeps runtime warning mode. Private intake retains its own blocking checks |
| Main/IaC compatibility | Existing bank security/privacy checks and dedicated intake/ontology constructs | Bind the reviewed main resource inventory to the actual integration base and inspect every changed resource; retain bank Gateway and MyData isolation |

## Required integration sequence

1. Integrate the independently developed source changes, preserve current main
   fixes, reconcile contract wording, and complete exact-HEAD review and CI.
2. Source path implemented in [unified execution](UNIFIED_SOURCE_EXECUTION.md); validate deployed B0's production verifier and execution profile to the dedicated
   authority/Runtime adapters. Replace the isolated adapter execution protocol
   in the application path; do not create another authoritative job writer.
3. Source path implemented; validate deployed staged ontology publication and one conditional completion
   transaction covering job, attempt, artifact, manifest, request marker and
   receipt pointers. Recheck all source/admission/profile/expiry fences; preserve
   the operation reserve and bounded contention/unknown-outcome handling.
4. Source path implemented; validate deployed admitted-input/prior-handle resolution, fixed caller roles and the
   dispatcher/watchdog/reconciler. A legacy workbench job cannot become a new
   execution merely because a cloud analyzer is configured.
5. Run B1 probes using reviewed deployed writer guards and the intended account,
   immutable code/profile/archive versions, IAM administration and private
   admission. Collect G0 evidence for Runtime, Identity/Gateway, Interpreter,
   Browser, Memory, models and admission. No fallback or guessed model alias.
6. Wire design stages and exact human decisions into persistent application
   records. Preserve the current canvas workflow and private source controls;
   retain the older HTML playground's separate approval semantics.
7. Exercise the synthetic vertical flow: intake → PRD confirmation → flow
   approval → generated variants → compile/browser verification → bounded edits
   → exact UX approval → rebuild/retest → authorized download/Git export.
   Add cancellation, expiry, withdrawal, stale approval and recovery negatives
   before a limited cohort and rollback test.

## Evidence and status rules

The [acceptance specification](ONTOLOGY_AGENTCORE_VALIDATION.md) remains the
57-case template with eight service gates. Do not rewrite its NOT_RUN entries
from aggregate test counts. A dated private assessment must bind actual
case/mode results to commit, configuration, deployment and receipt hashes.

The fixed offline level is 23 cases; integrated is 54; full-plan is 57.
A focused test named after a feature can support part of a case but cannot
stand in for its entire independent oracle or required O/L/U modes.

Integration status updates must state: current code, exact tests/review,
remaining caller wiring, configured/live evidence, and the next blocking gate.
No code merge or document review alone authorizes production activation.

## Source integration follow-up

`execution_source.py` creates one job/marked artifact and stages coupled graph
publication; `execution_sources.py` supplies actual intake/prior authority;
`execution_verifier.py` verifies domain-separated KMS receipts. The new Runtime
path uses bounded transfers, signed context/analyze receipts, a single dispatcher
invocation and IAM recovery. Source-only application status/cancel/retry routes
are implemented. Main opt-in remains absent by default. See the linked design
for exact contracts and tests. This advances steps 2–4 in code; it does not close
steps 5–7, service gates, independent acceptance or the complete design UI flow.

## Design delivery candidate (2026-10-11)

Review follow-up: compiler refusals retain private diagnostics and candidate
source without generating a bundle/approval. Stale procedure rows cannot poison
design listings. Design Git receipts use the same module-authorized writer at
completion as at admission. These changes require GEN-05/HAND-01–03 regressions;
their offline tests do not close any live service gate.

The candidate branch adds persistent design records, exact PRD/flow/UX/frontend
decisions, design operations through the B0 Runtime, compiler/Browser/large-text
verification, source-preserving release/handoff, recent-session Memory indexing
and the existing-canvas UI extension. See [delivery interfaces](DESIGN_EXECUTION_DELIVERY.md).
Validation is in progress; this is not a completed live/cutover claim.
The UI follow-up adds directed flow diagrams to both design editors, on-demand
side-by-side candidate previews, and single common-menu entries for tasks and
knowledge. Owning contract: REACT_CONTRACT (persistent procedure design).
`workspace-visual-review.test.cjs` covers branches, back/self edges, missing
targets, preview evidence mismatch/retry, scope changes and narrow layouts with
real Chromium and explicit API doubles. These are U-mode regressions for
GEN-05/HAND-01–03, not G0-BROWSER or live model/service acceptance.
AWS payload/destination approval, explicit synthetic Memory deletion authorization
and the registered Git target/credential are pending. The acceptance template
retains its fixed cases and unrun live modes.

## Selected-screen composition (2026-10-12 candidate)

The candidate connects the authenticated API, B0, Runtime, independent completion
and UI. Request and delivery authority are defined in the
[owning React interface](../workspace/REACT_CONTRACT.md#selected-screen-assembly-2026-10-12-candidate);
the [execution design](DESIGN_EXECUTION_DELIVERY.md#selected-screen-composition)
records its receipt chain. Review, exact-HEAD CI and live acceptance remain
separate evidence requirements.

## Condition-case flow viewer (2026-10-12 candidate)

`web/src/workspace/flowCases.ts` and `FlowDiagram.tsx` expose the frozen
condition-case paths in the existing design panel (REQUIREMENTS U-02,
HAND-01). The [owning React interface](../workspace/REACT_CONTRACT.md#persistent-procedure-design-2026-10-11-candidate)
defines the read-only forecast and its incomplete outcomes. Four grammar/path
tests and one local Chromium UI check cover the candidate at desktop and
375-pixel mobile viewports; independent review/CI and live UI acceptance remain required.

## Complete validation repair rounds (2026-10-12 candidate)

Implementation is in `ontology_runtime/design_workflow.py`,
`workspace/design_stages.py`, `design_execution.py` and the design panel.
`test_design_execution.py` exercises real local compiler/Browser feedback,
failed-round retention, source integrity, authority refusal and bounded call plans.
The
[owning execution contract](../workspace/AGENTCORE_CONTRACT.md#validation-repair-rounds-2026-10-12-candidate)
defines failed compile receipts and final-stage selection; the
[React interface](../workspace/REACT_CONTRACT.md#validation-repair-loop-2026-10-12-candidate)
defines the UI and role-dependent history projection. Exact-HEAD regression,
independent review/CI and deployment-bound live acceptance remain separate gates.

## Design evidence discussion (2026-10-12 candidate)

`workspace/design_discussions.py`, the design API and `DesignDiscussion.tsx`
connect persistent discussion to PRD, flow nodes, rules and actual generated
screens (REQUIREMENTS W-01). The
[owning React interface](../workspace/REACT_CONTRACT.md#discussion-on-design-evidence-2026-10-12-candidate)
defines target, permission and pagination bindings. `test_design_discussions.py`
and `design-discussion-ui.test.cjs` cover persistence, retry, source withdrawal,
concurrent membership changes, cursor isolation and current React evidence.
Independent review/CI and deployment-bound HAND-01/AUTH-01/08 acceptance remain
required.

## Human intervention feedback (2026-10-12 candidate)

`workspace/design_interventions.py`, design admission/completion, the B0
terminal writer and `DesignInterventions.tsx` connect V-05/P-04 to persisted
human actions and the existing metrics/proposal/checklist engines. The
[owning React interface](../workspace/REACT_CONTRACT.md#human-intervention-and-feedback-2026-10-12-candidate)
and [execution accounting](../workspace/AGENTCORE_CONTRACT.md#human-edit-accounting-2026-10-12-candidate)
define their boundaries. `test_design_interventions.py` drives actual local
compiler/Browser artifacts through admission, exact decisions and delivery
invalidation; its AWS/model transports remain doubles. Independent review/CI
and deployment-bound GEN/HAND/AUTH evidence remain required.
`test_design_intervention_edges.py` additionally covers older-run approval
invalidation, cancel/withdrawal and pre-dispatch limits;
`design-interventions-ui.test.cjs` covers retry and evidence mismatch.

## Synthetic ineligibility guidance (2026-10-12 candidate)

`seed/design_poc/ontology.json` adds a declared Text Atom for the ineligibility
reason and alternative action, the template's permitted body placement and the
Screen's composition edge (O-02/V-02). It remains candidate data until human
review; existing literal-provenance and PRD-binding rules are unchanged.
`test_design_ineligible_copy.py` verifies candidate blocking, in-process
publication and owner review on test storage, deterministic copy placement and
rendered page-root guidance.
GUI/provenance regressions remain distinct from actual model/service/customer
acceptance.
## Independent reference benchmark (2026-10-12 candidate)

`workspace/design_observations.py`, the Browser verifier and frozen design
contract collect real local page/journey observations. `design_benchmarks.py`
and `DesignReferencePanel.tsx` connect immutable human criteria, source-bound
regeneration and comparison (O-05). The
[owning reference interface](../workspace/REACT_CONTRACT.md#independent-reference-sets-and-actual-observations-2026-10-12-candidate)
and execution evidence contract define their boundaries. Local tests preserve
the independent golden oracle and report observed GUI defects rather than
weakening criteria. Review/CI and actual service/user acceptance remain required.
