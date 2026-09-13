# Historical hotfix report: legacy Studio model-bound content

- Date: 2026-09-07
- Commit: `44f4937026cf18e7a403e6520e16960a3db32658`
- Scope: Legacy `platform/studio/` HTML generation/review
- Status: Historical evidence; read current source and `SPEC.md` for active requirements
- Full original report: `git show 5a14725:.superpowers/sdd/2026-09-04-design-studio-agentic-loop/hotfix-report.md`

## Recorded changes

Added `studio/sanitize.py` using the existing PII rule table, preserving masked shapes
and reporting replacement counts. Generation/refinement context and reviewer DOM
digests were scrubbed before model prompts. Runtime skill guidance required masked
synthetic identifiers. These changes did not transform all stored/published HTML.
Later commits extended scrubbing to final assembled prompts, including asset/few-shot
content; do not treat this original two-call-site description as today's full coverage.

Added best-effort recovery of per-item reviewer verdicts after invalid JSON. Missing
items remained indeterminate. Strict parsing stayed first; evidence/fix parsing was
not equivalent to a fully tolerant JSON parser.

## Recorded verification

Failure-before/fix-after cases covered regeneration prompts, review digests, masking
and malformed reviewer output. The historical full run reported 434 tests passing.
That count and code line references were observations of this commit, not current
validation or approval for later changes.

## Limits

The report identified partial-JSON recovery ambiguity and potential overlapping-rule
masking behavior. Keep these as historical review concerns; inspect current tests and
source before claiming they remain defects or were resolved. Current MyData entity
validation is a separate subsystem and must not inherit legacy regex assumptions.
