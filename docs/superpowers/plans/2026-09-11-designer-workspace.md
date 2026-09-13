# Historical plan: private intake and HTML workspace

- Date: 2026-09-11
- Scope: Imported-file storage, HTML prototype checks, frozen rules and designer UI
- Status: Foundation record, expanded by the same-day React workspace requirements
- Current contracts: `platform/workspace/CONTRACT.md`, `platform/workspace/REACT_CONTRACT.md`
- Current product requirements: `SPEC.md` §7-1

## Implemented foundation

| Area | Contract |
|---|---|
| Authenticated intake/storage | Private originals, bounded multipart upload, hash verification, idempotency, async finalize, owner/project authorization, authenticated downloads |
| Parsing | Local PNG/JPEG/PDF/SVG/HTML/text previews; honest partial/unsupported state; FIG original storage only |
| Browser checks | Actual behavior/accessibility/network/console evidence; optional initial-screen pixel comparison, not all-state coverage |
| Rules and orchestration | Approved immutable criteria, selected private assets, allowed models, same-rule repair, exact-round approval, limits and failure evidence |
| Designer UI | Upload/progress/errors, source previews, editable rule cards, selected model/round, evidence and approval |
| Infrastructure | Private retained storage, authenticated API, restricted browser execution and no public prototype publication |

Imported skills are task data, not executable commands or permission grants. Do not
execute uploaded scripts/settings or fetch missing Figma/CDN content. Criteria changes
invalidate approval; missing evidence never passes.

## Historical checkpoint reconciliation

The early checkpoint reported backend intake, nine synthetic formats and hash-matched
downloads, plus a Fable proposal with 8 rules and 7 unresolved items. A draft proposal
was not approved executable evidence. Renderer repair and frontend publication were
still pending at that checkpoint.

Later entries in the same original plan recorded repaired-browser success/failure
checks, approval and generation, but left earlier pending prose unchanged. The later
React release record and `SPEC.md` §16 capture subsequent publication. Do not interpret
either checkpoint as proof of today's deployment or as a permanent prohibition on
features completed later.

## Verification pointers

`platform/tests/test_workspace_*.py`, `platform/web/test/workspace*.test.cjs`,
`platform/workspace/`, and dated operational records establish current evidence.
Customer component packages/APIs, FIG interpretation and public customer-artifact
publication were never included in these completion claims.
