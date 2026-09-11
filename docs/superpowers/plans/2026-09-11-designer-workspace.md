# Designer Workspace Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for disjoint
> implementation tasks and independent review after integration.

**Goal:** Deploy private multi-format intake and an approved-guide-to-executable-
prototype workflow with browser evidence and repair loops.

**Architecture:** A Cognito-authenticated HTTP API uses the existing CloudFront
origin. Originals and derived artifacts live in private storage. A Bedrock worker
generates against frozen user-approved contracts; a separate network-isolated
browser Lambda verifies behavior and visuals without AWS data permissions.

**Tech Stack:** React/TypeScript, Python, S3/DynamoDB, API Gateway JWT, CDK,
Playwright Chromium, Pillow/PDFium, Bedrock through the existing gate.

**Spec:** `docs/14-demo/studio-file-intake.md`; implementation interfaces:
`platform/workspace/CONTRACT.md`.

## Global constraints

- No Figma connection in network3 or Studio; no external resource completion.
- Never put imported originals or executable prototypes on public S3/CloudFront paths.
- FIG originals are stored, with interpretation explicitly unsupported.
- Imported skills are reference text, not server commands or agent permissions.
- Contract changes invalidate approval; incomplete evidence never passes.
- Preserve all existing drafts, approvals, resource logical IDs and unrelated edits.
- Public git export was rejected previously; do not bypass that approval boundary.
- Deployment is explicitly requested; validate each concrete AWS change before applying.

## Task 1 — private intake HTTP and storage

Files: `workspace/storage.py`, `workspace/http.py`, `tests/test_workspace_http.py`,
`tests/test_workspace_storage.py`. Implement the HTTP/Storage contract verbatim.

- [ ] Failing tests: cross-owner access returns404; absent JWT401; over-limit413;
  missing/mismatched/changed part409; duplicate identical part is idempotent.
- [ ] Implement conditional metadata, two-MiB binary upload, async finalize jobs,
  authenticated bounded downloads, revision-preserving archive, contract/run API.
- [ ] Verify full hash and exact selected artifacts; no active same-origin MIME.

## Task 2 — parser and real browser checks

Files: `workspace/intake.py`, `workspace/browser.py`, `workspace/browser_handler.py`,
`tests/test_workspace_intake.py`, `tests/test_workspace_browser.py`.

- [ ] Failing fixtures: valid PNG/JPEG/PDF/SVG/HTML/text, corrupted images,
  SVG entities/scripts, external HTML resources, FIG archive, image pixel limit.
- [ ] Implement local previews/text with honest partial/unsupported states.
- [ ] Browser fixtures: amount propagation, required checkbox blocking, back
  navigation, deliberate broken flow, external fetch, accessibility violation.
- [ ] Compare actual pixels when a reference is supplied, not an HTML hash.

## Task 3 — designer UI

Files: `web/src/workspace/*`, `web/src/studio/Studio.tsx`.

- [ ] File select/drop, progress, retry/error, grouped roles, previews, original
  download, version/interpretation state; do not require JSON or plugin setup.
- [ ] Guide proposal -> editable Korean rule cards -> explicit version approval.
- [ ] Model selection, generation/three variants, verification graph, round
  evidence, precise corrective instruction, exact-round approval/download.
- [ ] Mocked UI: partial upload, failure, delayed polling, multiple report jobs,
  unsaved contract edits, narrow/FHD/QHD/wide layout.

## Task 4 — frozen contracts and AI orchestration

Host files: `workspace/rules.py`, `workspace/worker.py`, `engine/gate.py`,
workspace worker tests and gate tests.

- [ ] Test rejected unknown DSL, empty assertions, invalid targets, unresolved
  requirements, source-quote mismatch, edited approved contract.
- [ ] Build bounded prompts from selected private assets, preserve imported
  design reference, apply selected skills as task data, use allowed model IDs.
- [ ] Freeze hashes/versions; execute real browser checks; repair only artifact;
  prioritize passed rounds, persist failure/incomplete evidence.
- [ ] Test duplicate async invocation, time cap, inference error, stale approval,
  foreign owner references and no invented successful verdicts.

## Task 5 — infrastructure, review and release

Host files: `infra/lib/workspace.ts`, `infra/lib/stack.ts`, workspace container
packaging/deployment, README and implementation review.

- [ ] Container runtime with pinned tools; browser minimal environment and
  isolated VPC/no egress, no S3 or Bedrock permissions.
- [ ] Private encrypted retained bucket/table; authorized HTTP route through
  CloudFront; workload-scoped IAM; no new public artifact endpoints.
- [ ] Synth/diff, security review, unit/integration/browser tests; fix Critical/Major.
- [ ] Deploy only reviewed resource changes; no seed reset or bucket deletion.
- [ ] Live synthetic-file upload, private download, rule proposal/edit/approval,
  Astra/Fable generation and actual good/broken flow verification.
- [ ] PR AI review and conditional merge when public publication is authorized;
  report any remaining approval boundary without claiming merge success.
