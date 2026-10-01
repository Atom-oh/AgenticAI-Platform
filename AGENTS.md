# Repository guidance

This repository contains a bank platform, two separate demos, and a VitePress
guidebook. Read only the documents relevant to the changed paths.

## Authority and scope

1. Follow the user's current instructions.
2. Use this file for repository workflow and [review context](docs/REVIEW_CONTEXT.md)
   for document scope, implementation evidence, and known discrepancies.
3. [SPEC.md](SPEC.md) defines bank-platform requirements. Its explicit amendments
   replace earlier statements only for the named feature.
4. Module contracts define interfaces; source, tests, and IaC establish what is
   implemented. A difference from an active requirement is a gap, not an implied
   exemption.
5. Dated plans and design records explain history. Guidebook chapters explain
   options. Neither creates a requirement for every product or future PR.

For bank-platform implementation, [the architecture design](platform/docs/ARCHITECTURE.md)
connects SPEC and module contracts to the staged delivery units. Read it before
changing ontology execution, shared authority or cross-module workflow.
It is subordinate to SPEC and owning contracts. Report a disagreement as a
documentation conflict and reconcile the owning authority rather than silently
using design prose to add or waive a requirement.

Do not infer deployment, service availability, or compliance from checked-in code,
old test counts, screenshots, or a successful merge.

## Change map

| Changed area | Read next |
|---|---|
| `platform/` architecture, deployment, shared services | `SPEC.md`, `platform/docs/ARCHITECTURE.md`, `platform/README.md` |
| Canonical ontology and AgentCore execution | `platform/docs/ARCHITECTURE.md`, `platform/workspace/ONTOLOGY_CONTRACT.md`, `platform/workspace/AGENTCORE_CONTRACT.md`, `platform/docs/ONTOLOGY_AGENTCORE_VALIDATION.md` |
| API events and module integration | `platform/docs/CONTRACTS.md` |
| React workspace and releases | `platform/workspace/REACT_CONTRACT.md` |
| Internal document library and source-bound S1 | `platform/documents/CONTRACT.md` |
| Role workbench, knowledge ETL, impact, Skill Creator, pension/report workspace | `platform/workbench/CONTRACT.md`, `platform/workbench/README.md` |
| Imported HTML validation | `platform/workspace/CONTRACT.md` |
| MyData privacy | `platform/infra/README-privacy.md`, `docs/14-demo/mydata-privacy.md` |
| `demo/uiux-studio/` | its `README.md`, `demo/SECURITY-GOVERNANCE.md` |
| `demo/builder-harness/` | its `README.md`, `demo/SECURITY-GOVERNANCE.md` |
| Guidebook content | `docs/13-appendix/vitepress-conventions.md` |
| Documentation decisions | `docs/decisions/README.md` |
| PR-review inputs and invocation | `docs/REVIEW_CONTEXT.md`, `docs/pr-review.md` |

## Invariants

- Require boundary inspection and measurement before bank model calls. Engine handlers
  use `platform/engine/gate.py`; the Strands scenario runtime uses its pre-model
  hook in `platform/agents/boundary_gate.py`. Verify each entry point against the
  same privacy obligations; a hook alone does not prove complete enforcement. MyData additionally removes free-text identifiers
  through the private privacy service before Bedrock calls; do not send raw
  questions to Guardrails.
- Preserve deterministic financial values, private identity mappings, independent
  outgoing-payload checks, and failure blocking. S2 does not replay or write the
  shared event cache. Never log raw prompts or entity originals.
- Keep project membership checks and approval tied to the exact source, component,
  guideline, rule, bundle, and evidence revisions. Imported HTML approval is not
  React release approval.
- Keep Cognito invitation-only. Neptune loading, shared-data seeding and Registry
  resets must use IAM-only administrative entry points. Explicit WebSocket `reset`
  and `registry_seed` routes are removed; inspect other legacy bootstrap paths separately.
  Do not add credentials to source, docs, logs, or review artifacts.
- Keep source-admission policy/provenance/reviewer grants and organization
  `design_publish`/`policy_publish` capabilities behind IAM-only administrative
  writes. Project ownership and in-app operator groups do not grant that authority.
- Display actual backends and demo substitutions. Do not describe a local graph,
  platform component package, or optional integration as a verified customer service.

## Validation

For implementation work, name the owning contract, affected callers, acceptance
case IDs and applicable service gates before editing. Keep requirements, current
implementation, offline tests and live evidence distinct. A completed document
review is not implementation completion. Update the design register and owning
contract when interfaces, authority or delivery status change.

Use the commands and prerequisites in each module README. Existing CI is defined
in `.github/workflows/platform-ci.yml`; do not treat absent or skipped jobs as
passing. For documentation:

```bash
npm ci
NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build
git diff --check
```

Run the relevant platform checks when editing runtime-consumed skill Markdown.
Preserve exact API identifiers, test fixtures, and Korean product UI strings when
they are part of a contract. PR-review and development documentation uses concise
English; public guidebook chapters and demo instructions remain Korean.

## Collaboration and PR completion

- Inspect `git status` before editing. Preserve others' changes and stage explicit
  files. Coordinate shared-file edits and allow only one deployment owner.
- Review the latest PR HEAD and inline comments. Verify each finding against the
  affected code and the applicable requirement; report evidence and impact.
- Fix valid Critical/Major findings, run relevant tests and required CI, commit,
  push, and obtain a review of the new HEAD. Missing, failed, truncated, or
  incomplete review coverage is not approval.
- Under the user's standing authorization, merge once the latest HEAD has completed
  AI review, no unresolved Critical/Major findings, and all required CI and branch
  protections are satisfied. Recheck HEAD, target branch, and predecessor PRs
  immediately before merging. Minor/Info findings alone do not block this process.
- Do not disable checks or invent exemptions to obtain approval. A review-only or
  no-merge instruction overrides the standing workflow.
- Report changes, validation, PR URL, and merge result. Pages publishes from `main`;
  complete the required content review before a change that triggers publication.
