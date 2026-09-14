# Versioned Portal components

**Goal:** Open a seeded component ID and inspect its exact React implementation,
interactive example, source files, props, and version changes.

**Architecture:** A project-owned reference library maps the 22 named seed
versions to separate TSX exports. The API and web build independently hash the
same implementation files and identity. Portal renders only an exact match.
The existing Studio kit remains a separate package.

**Scope:** `platform/component-library/`, Portal API/React UI, immutable renderer
build, deployment packaging, and regression tests. The 58 volume-test widgets
remain explicitly identified metadata placeholders. No Registry resets,
historical approval changes, customer package claims, or external integrations.

## Implementation

- [x] Add a failing Portal detail test for `CMP-Select-v2` / `2.0.0`, including
  mismatched version, unknown IDs, and preservation of approval metadata.
- [x] Implement each named version against the richer Registry seed prop
  contracts. Use separate exports and demonstrate meaningful version differences.
- [x] Build a static sandbox renderer and source manifest. Hash ordered
  `{id,version,package,exportName,files:[{path,sha256}]}` as compact UTF-8 JSON.
  API bindings and browser manifests must agree before rendering.
- [x] Show source text/download, usage, props, changes, and graph version links
  beside the exact-ID preview. Reject mismatched API/web deployments.
- [x] Check seed coverage, TSX compilation, interaction behavior, source/hash
  integrity, sandbox boundaries, unknown identities, and responsive layout.
- [ ] Update the deployment package and English development contract. Review
  the final HEAD, pass required CI and content review, then merge and deploy
  without changing shared seed data or live stack configuration.

## Validation

Run `python3 -m pytest tests/test_portal*.py -q` from `platform`, then
`npm run build` and `node --test test/portal-*.test.cjs
test/component-library.test.cjs` from `platform/web`. Check actual browser
interaction for all 22 IDs and version-specific Select search behavior.
Verify production API identity and rendered/source identity after deployment.
