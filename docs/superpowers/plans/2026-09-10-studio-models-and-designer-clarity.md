# Historical plan: Studio model selection and verification labels

- Date: 2026-09-10; intake correction: 2026-09-11
- Scope: Legacy Studio and agent-builder model selection
- Status: Implemented feature record; availability must be rechecked before live use
- Current requirements: `SPEC.md` §4 and §7-1; `platform/engine/model_catalog.py`

## Decisions

Use one server allowlist for generation, refinement, review and agent building.
Pass the selected model per request; never mutate the process-wide default or silently
substitute a rejected model. Record the actual invocation ID and result. The historical
catalog addition included Astra and Fable alongside the configured Claude choices;
this record does not certify today's account access.

Distinguish static drafts, structural review and executable verification. Prefer a
passing round over a higher-scoring failing round. Missing or indeterminate reviewer
items do not count as automatic passes. Preserve existing drafts, approvals and parent
relationships without promoting them to a newer validation contract.

The 2026-09-11 correction requires externally supplied files through controlled intake;
Studio does not connect directly to Figma or fetch missing external resources. Do not
claim nonexistent internal MCP tools or customer component integrations.

## Verification scope

Tests cover model propagation, allowlist rejection, concurrent request isolation,
privacy gating, reviewer state and round selection; browser checks cover loading,
errors and selected-model evidence. Model-selection completion did not include the
later private intake system, customer React package, or full visual/runtime validator.
Deployment of API/worker/frontend and live inference were separate checks.
