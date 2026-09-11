# React Design Workspace Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the disjoint kit, collaboration and host integration tasks. Each owner must read the shared interface contract.

**Goal:** Deliver a shared planning/design/development workspace whose approved outputs are actual tested React projects, deployable static bundles and traceable Git feature-branch exports.

**Architecture:** Reuse private intake and bounded browser verification. Add a pinned real React kit and trusted compiler, project-scoped guideline ontology, generation batches, immutable release records and a configured Git transport. One sidebar joins planning revisions, UX pages, evidence and actual commits.

**Tech Stack:** React 18.3.1, TypeScript 5.6.3, esbuild 0.25.12, Playwright 1.62.0, Python, existing JWT HTTP API/private S3/DynamoDB/isolated browser Lambda.

**Spec:** `docs/superpowers/specs/2026-09-11-react-design-workspace.md`; exact interfaces: `platform/workspace/REACT_CONTRACT.md`.

## Global Constraints

- Real React code is the component authority; no stub success or MD-as-implementation.
- Keep personal and shared project scopes separate. Check canonical membership on every project request.
- Pin component/source/ontology/contract/bundle hashes; changed criteria cannot reuse old release approval.
- Creative mode preserves hard rules; guided mode contains one baseline plus 2~5 variations.
- Build settings, dependencies and tests are trusted templates. Never run uploaded package scripts/configuration.
- Runtime compilation/rendering has no AWS data credentials and no outside network access.
- Figma/source URLs remain metadata; no direct fetch, external image/font completion or runtime npm downloads.
- Public platform source publication is explicitly authorized. Customer/design output publication still requires a configured destination and authorized export action.
- Preserve unrelated user edits, existing data, old prototypes and approval history.

## Task 1 — actual pinned React component kit (UI worker)

Files: `platform/react-kit/ui/*`, `catalog.json`, `manifest.cjs`, `test/ui*.test.cjs`.

- [ ] Implement the exact 15-component typed interface in `REACT_CONTRACT.md`, including controlled inputs, focus/error/disabled behavior and fixed tokens.
- [ ] Verify real rendering and interactions; prove caller CSS/raw HTML/unsupported props cannot redefine components.
- [ ] Generate the catalog lock from actual source bytes, not descriptions.

```js
assert.equal(catalog().version, '1.0.0');
assert.match(catalog().hash, /^[a-f0-9]{64}$/);
// Browser fixture: Input onChange updates state; Checkbox controls Button disabled;
// root markers and computed primary color come from the real kit.
```

Run: `cd platform/react-kit && node --test test/ui*.test.cjs`.
Commit only after host package setup and cross-check of imports/types.

## Task 2 — trusted React project compiler (host)

Files: `platform/react-kit/package.json`, `package-lock.json`, `compile.cjs`, `policy.cjs`,
`project.cjs`, `test/compiler*.test.cjs`, trusted export scripts/tests.

- [ ] Add failing fixtures for component-source mutation, unknown import, raw controls/style/HTML, path traversal, wrong props and invalid TSX.
- [ ] Implement `buildProject` with AST policy, real TS types, production bundle and deterministic source/dist ZIPs.
- [ ] Ship an executable test project: actual UI kit, pinned lock, build/check scripts and rule-based browser tests.
- [ ] Rebuild the exported project and compare bundle hash; source/kit changes must fail.

```js
const out = await buildProject({ files: goodFiles, assets: {}, expectedCatalogHash: catalog().hash, contract });
assert.equal(out.ok, true);
assert.notEqual(out.sourceHash, out.bundleHash);
assert.equal((await buildProject({ files: wrongProps, assets: {}, expectedCatalogHash: catalog().hash, contract })).ok, false);
assert.equal((await buildProject({ files: goodFiles, assets: {}, expectedCatalogHash: '0'.repeat(64), contract })).ok, false);
```

Run: `cd platform/react-kit && npm ci && node --test test/compiler*.test.cjs`.

## Task 3 — shared project, planning and persistent ontology (backend worker)

Files: `platform/workspace/collaboration.py`, `storage.py` transaction extension,
`tests/test_workspace_collaboration.py`, transactional storage tests.

- [ ] Add two-user tests: membership, role enforcement, revocation, actor/scope separation and cross-project 404.
- [ ] Implement `Collaboration` exact interface and atomic `Storage.put_many`.
- [ ] Publish structured guidelines into immutable typed ontology + private guide asset, then atomically switch the product's published pointer.
- [ ] Read the saved projection; link discussions and affected runs to exact revisions; retries must be idempotent.

```python
scope = collaboration.resolve_scope("designer-sub", project_id)
assert scope["owner"] == "project:" + project_id
assert scope["actor"] == "designer-sub"
# Publish v1, create a run pinned to v1, publish v2:
assert collaboration.is_current(scope, run_v1) is False
# Stale membership index + removed canonical member must still reject access.
```

Run: `cd platform && PYTHONPATH=. python -m pytest tests/test_workspace_collaboration.py tests/test_workspace_storage.py -q`.

## Task 4 — scoped HTTP and actual React runtime (host)

Files: `workspace/http.py`, new HTTP route helpers, `react_runtime.py`, `browser.py`, `browser_task.py`,
`Dockerfile`, `Dockerfile.dockerignore`, `infra/lib/workspace.ts`, tests.

- [ ] Wire project context and actor into existing resources without trusting request owner fields.
- [ ] Package pinned Node dependencies and kit into the image; keep compilation inside the credential-free child.
- [ ] Add `evaluate_bundle`: serve only exact local build files in memory, use the existing behavioral/a11y/image assertions.
- [ ] Prove failed build cannot return HTML fallback or functional pass; verify actual React state propagation and blocked network.
- [ ] Add narrowly scoped transactional/directory permissions required by project routes; synth/check before deployment.

```python
result = evaluate_react({"files": good_files, "contract": contract, "catalogHash": expected_hash})
assert result["build"]["gates"]["types"]["status"] == "pass"
assert result["report"]["functionalStatus"] == "pass"
bad = evaluate_react({"files": broken_state_files, "contract": contract, "catalogHash": expected_hash})
assert bad["report"]["passed"] is False
```

## Task 5 — frozen ontology generation and comparison batches (host)

Files: `workspace/worker.py`, `react_generation.py`, `batches.py`, `rules.py`, run/batch HTTP helpers and tests.

- [ ] Extend snapshots with catalog/product/guideline/ontology hashes; read published ontology as actual generation input.
- [ ] Generate only allowed React source files; retry against unchanged kit and rules.
- [ ] Creative creates one proposal; guided validates integer 2..5 and creates exactly one baseline + N variations.
- [ ] Persist per-run failures independently; support selecting/revising an exact run/round.
- [ ] Require mandatory notices/pages from ontology and distinguish exact image comparison from allowed variation review.

```python
assert len(make_batch(mode="guided", variation_count=2).run_ids) == 3
assert len(make_batch(mode="guided", variation_count=5).run_ids) == 6
# Reject 0,1,6, booleans and non-integers; preserve all hashes across repairs.
```

## Task 6 — verified release and Git export (host)

Files: `workspace/releases.py`, `git_export.py`, release HTTP/worker handlers and tests.

- [ ] Only an approved React round can create a release. Rebuild and retest exact approved source and verify source/kit/ontology/dist hashes.
- [ ] Store private source/dist/manifests/evidence and reject stale criteria or mismatched hashes.
- [ ] Implement registered Git connection transport, feature-branch-only writes, expected-base conflict handling and idempotent retries.
- [ ] Use local bare repositories/mock HTTP transports for branch/commit/conflict contract tests; never fabricate an external commit when unconfigured.

```python
release = prepare_release(approved_run, selected_round)
assert release["bundleHash"] == selected_round["bundleHash"]
# Modify source or publish a new product guideline: release/export must reject.
# Export twice: same commit; occupied unrelated branch: conflict, never force.
```

## Task 7 — project sidebar, modes and developer handoff (UI worker after kit)

Files: `web/src/workspace/*`, workspace tests.

- [ ] Add an immutable project-bound client context; remount scoped panels on project change and abort old polling/downloads.
- [ ] Add project/product selection, planner guideline editing/publication, ontology view, discussion threads and role-aware actions.
- [ ] Add creative/guided controls and 2..5 variation count; keep baseline visible with independent variant statuses.
- [ ] Show actual kit/build/browser/approval/release/Git states and page source links.
- [ ] Test two-user permissions, stale product revision, partial batch failure, page discussion anchors and real commit/unconfigured states.
- [ ] Verify responsive sidebar and preserve private nested preview navigation controls.

Run: `cd platform/web && npm run build && node --test test/*.test.cjs`.

## Task 8 — end-to-end review, release and PR (host)

- [ ] Build a synthetic product guideline, publish/read its ontology, and generate real React with Astra and Fable.
- [ ] Exercise creative, guided baseline+2 and baseline+5 boundaries; verify normal/broken/repaired flows.
- [ ] Approve/rebuild/download actual React source and dist, then serve dist locally and verify the same flow.
- [ ] Verify Git feature-branch export against the configured/test destination and display the actual result.
- [ ] Test guide change invalidation, project revocation and wrong artifact hashes.
- [ ] Run complete tests, independent code/security review, fix Critical/Major and check deployment diff.
- [ ] Deploy platform/backend without deleting existing files/data; verify the published UI with actual authenticated APIs.
- [ ] Publish PR to the authorized repository, collect AI review on the PR head, fix Critical/Major, and merge when only Minor remains.
- [ ] Update README/SPEC/designer guide to the actual React deliverable and report configured/unconfigured customer integrations accurately.
