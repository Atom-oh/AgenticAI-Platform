# Historical design: ontology-driven HTML Studio loop

- Date: 2026-09-04
- Status: Approved then; retained as legacy-feature rationale
- Scope: `platform/studio/`, not the newer React workspace
- Current requirements: `SPEC.md` §7-1 and `platform/workspace/REACT_CONTRACT.md`

## Problem and selected design

The early platform UI proxied the companion Studio and lacked its richer canvas,
selection/refinement, output types, presets and asset history. One-shot HTML drafts
did not verify product conditions or repair failed flows. The chosen legacy design
built a DesignSpec from the bank ontology, generated one HTML draft, reviewed it, and
repaired failures in a bounded Lambda worker loop through the model gate.

Decisions at that date: ontology source rather than uploads; HTML structural/text
review rather than real React/pixel validation; platform-owned jobs/rounds/drafts;
companion asset proxy; defaults of three rounds and 85 score, bounded by 20 rounds
and 780 seconds. These do not constrain the later imported-file/React features.

## DesignSpec and seed rationale

`Product -HAS_CONDITION-> Condition` creates display requirements and conditional
input steps. Product screens/procedures define order; active policy rules provide
weighted disclosure/consent/validation/accessibility/security checks; UX terms and
approved patterns add text/structural checks. Category templates add common and
product-specific assertions. Each check retains source-node evidence.

The deposit examples contrast a preferential-membership flow with a control lacking
that step. Independent seed randomness preserves prior seed/embedding compatibility.
Actual fixture values and identifiers live in `platform/seed/generate.py`.

## Execution and review

`studio_run` invokes the worker; `studio.stage`, `studio.token`, and `studio.done`
report progress. The worker persists round evidence even if the socket disconnects.
Text/DOM checks and model-review JSON contribute weighted results; missing/invalid
reviewer items are indeterminate. Repair changes the requested scope; structural
stability is reported separately. Errors/time caps never become passes.

The early highest-score selection was later replaced by passed-first selection.
The initial read-only generation-model assumption was also replaced by per-request
allowlisted selection. Read current `platform/studio/` source for those behaviors.

## Storage and evidence limits

Legacy synthetic HTML drafts used the existing web bucket and Studio table. Private
workspace originals and React releases use their newer private-storage contracts.
Legacy structure/text/flow checks do not establish pixel equivalence, executable React
quality, customer approval or Git publication. Preserve parent/approval history without
promoting old approvals to a different artifact or validation contract.

Tests cover conditional flows, source mapping, DOM/text checks, reviewer failures,
scoring, repair/time limits, handlers and persistence. Deployment and actual model
execution are separate evidence in the module README and dated release records.
