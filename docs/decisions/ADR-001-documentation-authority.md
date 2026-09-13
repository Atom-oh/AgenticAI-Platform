# ADR-001: Shared documentation authority

- Date: 2026-09-13
- Status: Accepted
- Scope: Repository documentation and PR review
- Authority: User-requested reconciliation and English documentation

## Context

Root guidance repeated older SPEC requirements while later feature records changed
MyData privacy and React releases. Historical plans, deployment reports, and general
guidebook chapters lacked clear authority boundaries. Reviewers could apply an old
Gemma substitution or HTML-only contract to the newer EKS/React implementation.

## Decision

Use one shared `AGENTS.md`, a thin `CLAUDE.md` entry point, and
`docs/REVIEW_CONTEXT.md` for scope and evidence. Keep active product requirements
in `SPEC.md` and interfaces in their module contracts. Apply amendments narrowly;
never treat implementation as automatic permission to weaken a requirement.

Write PR-review and development documentation in concise English. Keep public
guidebook chapters and demo instructions in Korean, as clarified by the user. Preserve
meaningful decision history, executable identifiers, fixtures, and application language requirements.
Mark historical records and link the current contract instead of repeating policy.
Keep deployment evidence dated and distinguish implementation, local validation,
live verification, and publication.

Review the actual latest HEAD with complete applicable coverage. Missing reviewers
and incomplete checks remain failures to complete the review process.

## Consequences

Agents load a small shared entry point and only relevant contracts. Documentation
changes that alter requirements must update the canonical document and affected
references together. This decision neither changes production behavior nor installs
an external PR-review service.
