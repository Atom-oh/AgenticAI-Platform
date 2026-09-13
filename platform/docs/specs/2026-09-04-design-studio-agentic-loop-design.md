# Historical design: product-spec process generation

**Historical record, 2026-09-04; implementation notes dated 2026-09-05.**
This file preserves the original process-loop intent. It is not the current
React workspace specification or a blanket requirement for Studio changes.
The earlier duplicate is [the root design record](../../../docs/superpowers/specs/2026-09-04-design-studio-agentic-loop-design.md).

Current authority: [root instructions](../../../AGENTS.md) →
[review context](../../../docs/REVIEW_CONTEXT.md) + [SPEC.md](../../../SPEC.md) →
[platform contracts](../CONTRACTS.md) and
[React workspace contract](../../workspace/REACT_CONTRACT.md).
See [ADR-001](../../../docs/decisions/ADR-001-documentation-authority.md).

## Original intent and requirements

Replace one-shot generation with product specification → PRD → multi-step flow →
review/test → report. Product-specific conditions add checklist items and, when
input evidence is needed, an evidence-entry step. Keep a stable review basis
through the bounded repair loop. The original proposal shared a Python engine
between the platform and the separate UI/UX demo; shared code did not imply one
runtime, storage boundary or deployment.

The asset model retained product conditions/rates/notices, UX molecules/organisms/
templates, base checklists, and derived requirements. Distinguish `source: base`
from `source: derived`; never invent financial values. A human reviews the result.
Customer GitLab delivery, Figma synchronization and real customer component mapping
were targets outside this original implementation, not prerequisites for all demos.

## Current process-loop code map (audited 2026-09-13)

Paths in this table are relative to `platform/`.

| Source | Actual behavior |
| --- | --- |
| `design_loop/models.py` | Dictionary validation; ProductSpec requires `productName`, `productType`, `category`, `baseRate`. Evidence is `{type: input|auto|none, label?, format?}`, not the earlier prose value `입력`. |
| `design_loop/prd.py` | Deterministic PRD skeleton from the spec and SM model; evidence-input conditions add steps. |
| `design_loop/checklist.py` | Merge applicable base sets and requirements derived from the spec. |
| `design_loop/generate.py` | Build prompts and parse `<<<STEP id="…" title="…">>>` / `<<<END>>>` and `<<<FLOW>>>` transition blocks. The output is not a single flow-JSON document. |
| `design_loop/rules.py`, `review.py` | Rule/LLM checks; unknown evidence is `incomplete`, not pass. |
| `design_loop/loop.py` | `run(spec, sm, checklists, deps, emit=None, assets=None, prd=None, output_type="design")`; at most one regeneration, plus one parse retry per attempt. Remaining failures/incomplete items appear in `openItems`. |
| `api/handlers/design.py` | `design_catalog`, `design_preview`, `design_runs`, `design_run`, `design_review`, `design_flow`; default local gate-backed execution. Runtime requires both `DESIGN_USE_RUNTIME=1` and `AGENTS_RUNTIME_ARN`. |
| `agents/app.py`, `agents/design_deps.py` | `design_flow_agent` runs the shared loop; it is separate from ordinary Strands chat. |
| `registry/seed.py`, `seed/design/` | Approved design seeds; handler labels file fallback as `seed-fallback`. |
| `web/src/studio/ProcessStudio.tsx` | Process-generation UI and review evidence. |

`deps.generate(system, user, on_token)` is required; `llm_judge` and `test_gate`
are optional. The engine emits `stage` and `token` dictionaries; the API translates
them to `design.stage`, `design.token`, then `design.done`. Results retain `prd`,
`checklist`, `flow`, `report`, `attempts`, `regenerated`, and `ok`.

Missing spec fields return `code:"spec-incomplete"` and `missing[]`; exhausted
parsing returns `code:"parse-failed"`. Remaining checks do not become approved
because attempts were exhausted. The process path's default test dependency does
not run browser axe; its badge reports this limitation. `design-runs/*` uses the
legacy web-bucket/CloudFront artifact path, not private workspace release storage.

## Historical evidence and later scope

The 2026-09-05 notes reported 13 offline process-loop tests and a platform rehearsal.
Those totals and the separate demo's deployment state are historical claims,
not fresh results from this audit. The original proposed `lookup_design_asset`
Gateway tool and handler action names are not current API contracts; use the
sources above.

Later `studio/loop.py` added HTML draft/refine behavior with up to 20 rounds.
The React workspace now has real component compilation, one to five rounds,
private approval/release evidence and configured Git export. Do not apply the
original one-regeneration, no-refine or no-React scope to those later workflows.
Customer Figma/package/Git readiness still requires explicit evidence.

## Relevant checks

From `platform/` with test dependencies installed:

```bash
python3 -m pytest tests/test_design_loop.py tests/test_design_handler.py -q
```

Check deterministic evidence-step insertion, derived checklist provenance,
missing-step/text detection, bounded regeneration and event translation.
Live multi-step rendering and each deployment require separate verification.
