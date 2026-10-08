# Ontology/AgentCore implementation register

Date: 2026-10-08. Scope: source integration of ontology branch `c234292` and main
`f5157c7`. This register is subordinate to SPEC, the [architecture](ARCHITECTURE.md)
and the owning contracts. It records implementation and gaps, not deployment.
Final review/CI evidence belongs to the exact integration PR HEAD.

## Implemented code and remaining boundaries

| Area | Concrete implementation and focused tests | Remaining requirement |
|---|---|---|
| Canonical ontology | `workspace/ontology_store.py`, `ontology_sources.py`, `ontology_ux.py`, `source-analyzer/`; `test_ontology_*` | Preserve old parser-edge/tombstone capacity limits; run the independent bounded oracle and current-source/revocation cases |
| B0 execution | `workspace/execution_ledger.py`, `workspace/storage.py`, legacy worker/expiry/repair guards; `test_execution_ledger.py`, `test_execution_guards.py` | Connect the production verifier, admitted-input/prior-handle adapters, dispatcher and atomic completion facade |
| Private intake | `intake/{records,admin_handler,inspect,derivative,admission,review,collection,prompts,images,imaging,transcription,worker,audit}.py`; `test_intake_*`; `documents/library.py` | Configure and verify private policy/deny-list/reviewer administration, source bindings and any required redaction service; no customer-data admission from project ownership alone |
| Sharing and derivative reads | `workspace/publications.py`, `ontology_sources.py`, `http.py`, `batches.py`, `releases.py`, `git_service.py`; `test_publications.py`, `test_sources_adapters.py`, project React-flow tests | Deployed cross-project grant/withdrawal checks, source-bound UI acceptance and prospective cleanup/recall evidence |
| Dedicated service adapters | `ontology_runtime/{authority,authorization,capability,dispatch,tools,workflow,interpreter,browser,identity,memory}.py`; `test_agentcore_*`; `infra/lib/ontology-stack.ts` | Current adapter transport uses `ac_execution`/`ac_operation`. It must be reconciled with B0's `agentcore-execution` protocol before production use. Service configuration and READY status are insufficient |
| Offline design engine | `design_loop/{knowledge,derive,guide_rules,prd_extract,flow,composition,gui,edit,contract,coverage,verify_graph,react_project,handoff,benchmark}.py`; `test_design_*`, synthetic seed and golden fixtures | No completed persisted application-stage API/dispatcher wiring. Legacy process generation remains a distinct caller |
| Public-output controls | `scripts/check_public_identifiers.py`, `api/common/public_scan.py`, legacy public-writer hooks and CI; `test_public_identifiers.py`, `test_public_scan.py` | Configured deny-list coverage and reviewed media registry remain necessary. CI's inherited allow-missing mode warns when its secret is absent; that result is not evidence that customer identifiers were scanned. Runtime publication and private intake keep their own blocking checks |
| Main/IaC compatibility | Existing bank security/privacy checks and dedicated intake/ontology constructs | Bind the reviewed main resource inventory to the actual integration base and inspect every changed resource; retain bank Gateway and MyData isolation |

## Required integration sequence

1. Integrate the independently developed source changes, preserve current main
   fixes, reconcile contract wording, and complete exact-HEAD review and CI.
2. Connect B0's production verifier and execution profile to the dedicated
   authority/Runtime adapters. Replace the isolated adapter execution protocol
   in the application path; do not create another authoritative job writer.
3. Implement staged ontology publication and one conditional completion
   transaction covering job, attempt, artifact, manifest, request marker and
   receipt pointers. Recheck all source/admission/profile/expiry fences; preserve
   the operation reserve and bounded contention/unknown-outcome handling.
4. Connect admitted-input/prior-handle resolution, fixed caller roles and the
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
