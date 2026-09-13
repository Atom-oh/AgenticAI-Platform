# Historical plan: legacy HTML Studio loop

- Date: 2026-09-04
- Scope: `platform/studio/`, legacy HTML drafts
- Status: Historical implementation record; not the current React release plan
- Current requirements: `SPEC.md` §7-1 and `platform/workspace/REACT_CONTRACT.md`
- Original detailed plan: `git show 5a14725:docs/superpowers/plans/2026-09-04-design-studio-agentic-loop.md`

## Purpose and architecture

Replace the platform's thin companion-Studio proxy with a native product-spec loop:
ontology -> DesignSpec -> HTML -> deterministic/model review -> bounded repair.
`studio_run` stores a job and asynchronously invokes `StudioLoopFn`; the worker sends
`studio.stage`, `studio.token`, and `studio.done`. Jobs, rounds and drafts use the
Studio table; legacy demo HTML uses the web bucket. This historical storage decision
does not authorize putting private imported workspace artifacts there.

## Work packages and evidence

| Work package | Implementation / tests |
|---|---|
| Deposit-product seed, isolated random stream | `platform/seed/generate.py`, `platform/tests/test_studio_seed.py` |
| Category checklist and ontology-derived DesignSpec | `platform/studio/spec.py`, `platform/studio/checklists/` |
| Text/DOM/model review, scoring and structural stability | `platform/studio/review.py` |
| Gate-routed generation/review prompts and skill inputs | `platform/studio/prompts.py`, `platform/skills/studio-*.md` |
| Bounded generation/repair, injected test dependencies | `platform/studio/loop.py` |
| Job/round/draft persistence | `platform/studio/store.py` |
| WebSocket actions and companion asset proxy | `platform/api/handlers/studio.py`, `platform/api/common/studio_proxy.py` |
| Worker invocation, publication and traces | `platform/studio/worker_handler.py` |
| Table/function grants and deployment packaging | `platform/infra/lib/stack.ts`, `platform/deploy.sh` |
| Playground/gallery/assets/checklist/history UI | `platform/web/src/studio/` |

## Historical constraints and amendments

- Preserve existing seed output and embedding-cache compatibility while adding the
  deposit examples with an independent random stream.
- Route inference through the bank gate; log lengths/hashes/scores, not prompts or HTML.
- Parse missing/invalid reviewer items as indeterminate. Never turn them into passes.
- Legacy HTML limits were 1–20 rounds (default 3), score 50–100 (default 85), and a
  780-second worker cap. They do not replace the newer workspace's 1–5-round contract.
- The early plan selected highest score and treated the generation model as read-only.
  The 2026-09-10 change added request-specific model selection and passed-first round
  selection, with indeterminate results excluded from automatic pass.
- The stdlib-only packaging instruction applied to this legacy Lambda module; it is
  not a repository-wide ban on dependencies used by containerized workspace services.
- Original implementation snippets are retained in Git history. Read current source
  instead of replaying those snippets or their session-specific scratch paths.

## Validation scope

Offline tests covered seed stability/coverage, conditional flow rules, DOM/text
checks, malformed reviewer JSON, scoring, repair limits, time caps, persistence and
handler/worker contracts. Type/build and live deployment checks were separate steps.
Use current module READMEs and dated operational records for actual completion.
