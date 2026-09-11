# Designer Workspace Implementation Plan

Historical intake/HTML foundation plan. The final deliverable was expanded by the
2026-09-11 customer meeting; follow `2026-09-11-react-design-workspace.md` for the
actual React component, collaboration, release and Git handoff requirements.

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

## Checkpoint — 2026-09-11, release incomplete

Checked implementation items mean source and local/offline evidence exist.
They do **not** mean the new frontend is published or the AWS browser renderer
has passed. Live facts below are reported by the integration owner:

- Backend deployed; nine synthetic file formats were imported and their
  authenticated original downloads matched their hashes.
- Live Fable proposal succeeded with **8 rules and 7 unresolved items**.
  This is a draft, not contract approval or a passing executable verification.
- UI build/offline flow passed; staged new UI with the real API showed no
  horizontal overflow at FHD, QHD and 3440 width.
- **AWS browser renderer remains under Carson's repair. No live executable
  verification pass has been confirmed. NEW FRONTEND NOT PUBLISHED.**
- **Public GitHub PR payload publication was explicitly authorized by the user
  on 2026-09-11.** After runtime verification, the parent handles push, AI review,
  Critical/Major fixes and merge when only Minor findings remain. Publication
  and merge are not complete. The integration owner updates final release facts.

## Global constraints

- No Figma connection in network3 or Studio; no external resource completion.
- Never put imported originals or executable prototypes on public S3/CloudFront paths.
- FIG originals are stored, with interpretation explicitly unsupported.
- Imported skills are reference text, not server commands or agent permissions.
- Contract changes invalidate approval; incomplete evidence never passes.
- Preserve all existing drafts, approvals, resource logical IDs and unrelated edits.
- Public PR payload publication is now explicitly authorized; the parent owns
  push/review/fixes/conditional merge after runtime verification.
- Deployment is explicitly requested; validate each concrete AWS change before applying.

## Task 1 — private intake HTTP and storage

Files: `workspace/storage.py`, `workspace/http.py`, `tests/test_workspace_http.py`,
`tests/test_workspace_storage.py`. Implement the HTTP/Storage contract verbatim.

- [x] Local regression tests: cross-owner access returns404; absent JWT401; over-limit413;
  missing/mismatched/changed part409; duplicate identical part is idempotent.
- [x] Implement conditional metadata, two-MiB binary upload, async finalize jobs,
  authenticated bounded downloads, revision-preserving archive, contract/run API.
- [x] Verify full hash and exact selected artifacts locally; no active same-origin MIME.
  Nine-format live import/hash-download results are tracked separately in Task 5.

## Task 2 — parser and real browser checks

Files: `workspace/intake.py`, `workspace/browser.py`, `workspace/browser_handler.py`,
`tests/test_workspace_intake.py`, `tests/test_workspace_browser.py`.

- [x] Local fixtures: valid PNG/JPEG/PDF/SVG/HTML/text, corrupted images,
  SVG entities/scripts, external HTML resources, FIG archive, image pixel limit.
- [x] Implement local previews/text with honest partial/unsupported states.
- [x] Local actual-browser fixtures: amount propagation, required checkbox blocking, back
  navigation, deliberate broken flow, external fetch, accessibility violation.
- [x] Compare actual pixels of the initial screen when a reference is supplied,
  not an HTML hash. Do not claim every transition state was compared.
- [ ] Complete AWS browser renderer repair and rerun deployed success/failure cases.

## Task 3 — designer UI

Files: `web/src/workspace/*`, `web/src/studio/Studio.tsx`.

- [x] File select/drop, progress, retry/error, grouped roles, previews, original
  download, version/interpretation state; do not require JSON or plugin setup.
- [x] Guide proposal -> editable Korean rule cards -> explicit version approval.
- [x] Model selection, generation/three variants, round
  evidence, precise corrective instruction, exact-round approval/download.
- [x] Mocked UI: partial upload, failure, delayed polling, multiple report jobs,
  unsaved contract edits, narrow/FHD/QHD/wide layout.
- [x] Original HTML inspection without AI rewriting; advanced CSS bindings,
  `expectStyle`, public artifact flags, OCR/context warnings, one-based pages and
  import revisions separate from internal database revisions.
- [x] Native accessible verification-loop graph bound to selected run/round,
  matching evidence and exact approval; failure/incomplete returns to the same rules.
  Eight dedicated state tests plus the offline browser flow cover unknown phases,
  mismatching evidence, unapproved reports, missing/nonempty network or console
  evidence and narrow/FHD/QHD/wide wrapping. The frontend regression set passes 28/28.
- [ ] Publish the new frontend after renderer and final release verification.

## Task 4 — frozen contracts and AI orchestration

Host files: `workspace/rules.py`, `workspace/worker.py`, `engine/gate.py`,
workspace worker tests and gate tests.

- [x] Local tests reject unknown DSL, empty assertions, invalid targets, unresolved
  requirements, source-quote mismatch, edited approved contract.
- [x] Build bounded prompts from selected private assets, preserve imported
  design reference, apply selected skills as task data, use allowed model IDs.
- [x] Freeze hashes/versions; execute local actual-browser checks; repair only artifact;
  prioritize passed rounds, persist failure/incomplete evidence.
- [x] Local failure-path tests cover duplicate invocation, execution limits,
  inference errors, stale approval,
  foreign owner references and no invented successful verdicts.
- [ ] Confirm the same orchestration through the repaired AWS renderer.

## Task 5 — infrastructure, review and release

Host files: `infra/lib/workspace.ts`, `infra/lib/stack.ts`, workspace container
packaging/deployment, README and implementation review.

- [x] Container/runtime and infrastructure implementation with pinned tools; browser minimal environment and
  isolated VPC/no egress, no S3 or Bedrock permissions.
  Local non-root/read-only/network-none checks and the deployed AWS browser passed.
- [x] Private encrypted retained bucket/table; authorized HTTP route through
  CloudFront; workload-scoped IAM; no new public artifact endpoints.
- [x] Deploy backend intake/proposal resources (integration-owner report).
- [x] Live nine-format synthetic-file intake and authenticated original-download hash checks.
- [x] Live Fable rule proposal: 8 rules and 7 unresolved requirements, not approved.
- [x] Staged new UI connected to the real API: no overflow at FHD/QHD/3440.
- [x] Close foundation synth/diff and integration review; resolve renderer failures and confirmed Critical/Major findings.
- [ ] Deploy reviewed renderer fixes and new frontend; preserve existing data and resources.
- [x] Live proposal edit/approval, Astra/Fable generation and actual good/broken
  browser verification, exact-round repair and approval.
- [x] Obtain explicit user authorization for the public GitHub PR payload.
- [ ] Parent push and PR AI review after runtime verification; resolve
  Critical/Major findings and merge when only Minor findings remain.

Evidence: `platform/tests/test_workspace_*.py`, `platform/web/test/workspace*.test.cjs`,
the frontend build, prior local integration/container results and the explicitly
scoped live results above. Real bank React packages/APIs and FIG interpretation
remain outside any completion claim.
