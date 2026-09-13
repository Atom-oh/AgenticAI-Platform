# Ontology workbench implementation plan

Status: local implementation and validation complete; Draft PR handoff only.
Core AI review coverage remains pending after a safety-flagged review error.
No merge or runtime deployment is authorized for this handoff.
Private source PDF/transcripts remain outside the repository.

**Goal:** Implement role-oriented workspaces, change impact and worklists,
versioned Skill Creator, batch knowledge projections/MCP serving, synthetic pension
counselling and evidence-based internal reports, plus a Korean explanation page
and an approximately four-minute rendered video.

**Architecture:** Extend the existing authenticated workspace API, private storage,
worker and real React workspace. Use additive `workbench` modules and clearly
label configurable integrations and local projection/model adapters.

**Contract:** `platform/workbench/CONTRACT.md`.

## Tasks

1. [x] Add backend workbench services and focused failing tests: scope/CAS, generation
   publication, tombstones/ACL age, impact paths, task evidence, immutable Skills,
   operator authorization, MCP dispatch and idempotency.
2. [x] Add Korean workbench screens against the frozen HTTP contract. Test cross-project
   state reset, actual mutation feedback, artifact links, operator denial and mobile
   navigation.
3. [x] Integrate workspace routing/worker/storage packaging and role-aware navigation.
   Keep global operator rights separate from project ownership; guard affected
   administrative WebSocket actions server-side.
4. [x] Add deterministic synthetic pension scenarios, assumptions/calculations,
   fact-grounded answers/feedback and evidence-linked report generation/approval.
5. [x] Add exact approved Skill package resolution and bounded execution, plus
   approved collector/client adapters. Keep declared dependency provenance explicit;
   automatic React-run Skill attachment and customer-code extraction are not claimed.
6. [x] Run focused and full Python/web/build/infra checks; repair valid findings. Exercise
   the implemented flows with synthetic fixtures, preserving real integration limits.
7. [x] Build the Korean explanation page and HyperFrames general-video composition.
   Capture actual validated UI, use synthetic/public assets only, render a roughly
   240-second video with Korean narration/captions and validate media playback.
8. [ ] Hand off a Draft PR and retain the remaining review gates. Content review
   passed; the independent core AI review did not complete and must not be retried
   or reassigned through an alternate route. Exact-HEAD review and commit CI are
   still required. The user's current instruction prohibits merge and runtime
   deployment.

## Validation checkpoint

Full local results: Python **1,238 passed + 119 subtests**, web **110 passed**,
React **24 passed**, gates **12 passed**, CDK synthesis/template check and both
application/documentation builds passed. Content review: **88.5/90, PASS**,
zero Critical findings and one nonblocking typography warning.

The final MP4 is 240 seconds at 1080p/24 fps with audio. A WebM fallback supports
the installed Chromium; three responsive page sizes, 15 role selections and
24 chapter seeks passed without JS errors or 404s. See
`platform/workbench/VALIDATION.md` for evidence scope and integration limits.

## Acceptance boundaries

- No raw customer material in public source, video or external reviewer payloads.
- No fake Confluence/Git/model/Neptune connectivity or simulated successful ETL.
- Missing runtime integrations stay visible; real snapshot indexing and application
  workflows still function with controlled synthetic data.
- Source/permission revisions bind knowledge publication and consumers.
- Current membership, operator capability and exact approval hashes are enforced
  in backend services, including direct API access.
- The rendered video explains what this release actually implements and demonstrates.
