# B0 Intake — Source Admission and De-identified Derivatives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `source-admission/1` so that no customer document text reaches a model unless it passes these steps:
1. Private intake inspects it.
2. Private intake normalizes it into a separately hashed **admitted derivative**.
3. The derivative is admitted under a versioned policy by an authorized reviewer, or by a trusted provenance registration.

The design PoC's PRD, guideline-rule and analysis stages read only these derivatives (Codex #2).

**Architecture:** A new private package `platform/intake/` owns four closed record schemas:
- policy
- provenance registration
- reviewer grant
- admission decision

The platform security operator administers them through an **IAM-only Lambda entry point**. It has no API Gateway route, and in-app groups confer nothing.

Inspection reads the approved document-revision projection through the existing `documents.library.Library` and the source authority in `workspace.ontology_sources.Sources`. It then scans the text in two ways:
- PII rules (`api/common/pii.scan_rules`)
- a private customer-identifier deny-list

An `identifier-normalization` profile produces derivative pages with deny-listed identifiers replaced by neutral aliases. A residual scan must be clean.

Inputs with PII need privacy-service redaction. Their contract integration is not reviewed yet (`AGENTCORE_CONTRACT.md:726-733`), so they are **blocked**, not silently passed. Decisions are rechecked at attempt admission and at every transfer (B0 ledger `admissions`).

**Tech Stack:** Python 3.12, the existing `Storage`/`FakeTable`, `documents.library`, `api/common/pii.py` and Pillow (already a dependency of `workspace/browser.py`).

**Roadmap:** unit 2. It fixes Codex #2 together with the engine (E6/E7) and C (C2).

**Owners:**
- private-intake maintainer: records, inspection, derivatives
- security-policy owner: policy and grant administration
- privacy/infra owner: the future redaction adapter; it is explicitly unavailable in this unit

**Owning contracts:**
- `platform/workspace/AGENTCORE_CONTRACT.md` `source-admission/1` (L583-605) and Phase 0 admission text (L639-658, L704, L726-733)
- `platform/documents/CONTRACT.md` (a derivative consumer of approved revisions)
- `platform/docs/CONTRACTS.md`

**Acceptance cases:** SRC-01, SRC-05 and SRC-06; AUTH-01 and AUTH-08. These are O-mode and API negatives. G0-ADMISSION live evidence is B1's.

## Global Constraints

- The roadmap global constraints apply.
- No workspace/project API, model tool, workload identity or in-app operator group can create, edit or activate a policy, provenance registration or reviewer grant.
- Admission decisions never contain raw originals, deny-list terms or credentials. Derivative text lives only in private blobs, under keys from `Storage.key_for`.
- The private deny-list comes from private configuration, never from Git or a request body. The deployment's SSM SecureString or a private S3 key is named by an env var.
- Unknown fields and unknown versions fail closed. Every record has an immutable `revision`, a `hash`, a `status` and an `expiresAt`.
- PII redaction is `unavailable`. An input whose inspection finds PII is `blocked: redaction-required`.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/intake/__init__.py` | Package marker (private intake) |
| `platform/intake/records.py` | Closed schemas + validation for the four record types |
| `platform/intake/admin_handler.py` | IAM-only Lambda entry: policy, provenance and grant administration |
| `platform/intake/inspect.py` | Source resolution + inspection receipt |
| `platform/intake/derivative.py` | `identifier-normalization` profile, residual scan, derivative pages |
| `platform/intake/images.py` | PNG/JPEG normalization (SRC-05) |
| `platform/intake/admission.py` | `decide`, `verify`, `pages_for` (derivative read for execution) |
| `platform/intake/review.py` | Reviewer decision flow (current grant + current source access) |
| `platform/intake/audit.py` | Metadata-only transfer/admission audit export (§7.2) |
| `platform/workspace/storage.py` (modify) | Kinds `adm_policy`, `adm_provenance`, `adm_grant`, `adm_decision`, `adm_audit` |
| `platform/workspace/http.py` (modify) | `/intake/reviews` routes (reviewer only, JWT + current grant) |
| `platform/infra/lib/intake.ts` (create) | IntakeAdminFn construct (synthesized, deployed in B1) |
| `platform/workspace/check_infra.py` (modify) | Assert IntakeAdminFn has no API route and no Function URL |
| `platform/tests/test_intake_*.py` | Tests |

---

### Task I1: Record schemas and storage kinds

**Files:**
- Create: `platform/intake/__init__.py`, `platform/intake/records.py`
- Modify: `platform/workspace/storage.py:20-24`
- Test: `platform/tests/test_intake_records.py`

**Interfaces:**

The partition owner for administration records is `INTAKE_OWNER = "intake:deployment"`. Decisions live in the project partition, `project:<id>`.

| Record | Fields (all required unless noted) |
|---|---|
| `adm_policy` | `id`, `revision` (int ≥ 1), `scope: {deployment: str, projectIds: [id] \| "*"}`, `dataClasses: subset of {synthetic, public, internal-non-sensitive}`, `profiles: {inspection: "inspect-1", normalization: "identifier-normalization-1"}`, `requiresReviewer: {internal-non-sensitive: true}`, `trustedProvenance: {public: true, synthetic: true}`, `promptText: "review"` (v1 accepts only `"review"`), `status: draft\|active\|retired`, `expiresAt`, `hash` |
| `adm_provenance` | `id`, `revision`, `policyId`, `policyRevision`, `kind: fixture\|public-reference`, `reference: {sourceKind, sourceId, revision, sha256}`, `publicUrl?` (https only, for `public-reference`), `scope`, `status`, `expiresAt`, `hash` |
| `adm_grant` | `id`, `revision`, `actor` (verified Cognito `sub`), `policyId`, `scope: {projectIds}`, `operations: ["review-internal"]`, `status: active\|revoked`, `expiresAt`, `hash` |
| `adm_decision` | `id`, `revision`, `projectId`, `source: {sourceKind, sourceId, revision, sha256, audienceRevision}`, `artifact: {kind: "document-pages"\|"image"\|"code-collection"\|"prompt-text", key, sha256, pages?, files?, resolver?}`, `derivation: {profile, originalHash, derivativeHash}`, `policy: {id, revision, hash}`, `provenance?: {id, revision}`, `inspection: {receiptKey, hash}`, `review?: {actor, grantId, grantRevision, at}`, `dataClass`, `status: pending-review\|admitted\|rejected\|blocked\|revoked\|expired`, `blocking?: [reason]`, `expiresAt`, `hash` |
| `adm_audit` | `id`, `op`, `kind`, `recordId`, `revision`, `operator`, `at`. Metadata only, append-only; one record per admin operation |

Functions:
- `validate(kind, record) -> dict`
- `seal(kind, record) -> dict`: sets `hash = schema.digest(record without hash)`
- `is_current(record, now) -> bool`: true when `status` is `active` or `admitted` and `now < expiresAt`

- [ ] **Step 1: Write the failing tests.** Cover:
  - a valid record of each kind passes
  - an unknown field fails
  - a wrong `revision` type fails
  - an `http:` public URL fails
  - `dataClasses` containing `sensitive` fails
  - a decision without `derivation` fails
  - a hash mismatch fails
- [ ] **Step 2: Run** `python3 -m pytest tests/test_intake_records.py -q` → FAIL.
- [ ] **Step 3: Implement `records.py`** with `_fields` from `workspace.ontology_schema`, one validator function per kind, `seal` and `is_current`. Add the five kinds (`adm_policy`, `adm_provenance`, `adm_grant`, `adm_decision`, `adm_audit`) to `KINDS`.
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Commit** `git commit -m "feat(intake): closed source-admission/1 record schemas"`

### Task I2: IAM-only administration entry point

**Files:**
- Create: `platform/intake/admin_handler.py`
- Test: `platform/tests/test_intake_admin.py`

**Interfaces:**
- `handler(event, context) -> dict`. Operations:
  - `put_policy`, `activate_policy`, `retire_policy`
  - `register_provenance`, `revoke_provenance`
  - `grant_reviewer`, `revoke_grant`
- The event is `{op, record | id, expectedRevision, operator}`. `operator` is an audit label only; authority is the IAM invoke.
- Refusals, returned as `{"error": "forbidden-transport"}` with no write:
  - any event carrying `requestContext`, `headers`, `routeKey` or `rawPath`, which means API Gateway or a Function URL shape
  - a `context` without `invoked_function_arn`
- Writes use `Storage.put(INTAKE_OWNER, kind, sealed, expected_version)`. Every activation or revocation writes an `adm_audit` metadata event (`{op, kind, id, revision, at}`) in the same `put_many`.
- A policy change marks dependent **current** decisions as needing reverification. Nothing is rewritten: `admission.verify` compares `policy.revision`, so it happens implicitly.

- [ ] **Step 1: Tests:**
  - A Lambda-invoke-shaped event writes the policy.
  - An API Gateway v2-shaped event is refused.
  - An in-app operator claim in the body (`"groups": ["platform-operators"]`) confers nothing.
  - A stale `expectedRevision` → `conflict`.
  - Revoking a grant writes status `revoked` with a new revision.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(intake): IAM-only administration of admission policy, provenance and grants"`

### Task I3: Source resolution and inspection receipt (SRC-01, SRC-06)

**Files:**
- Create: `platform/intake/inspect.py`
- Test: `platform/tests/test_intake_inspect.py`

**Interfaces:**
- `resolve(host, scope, source_ref) -> {"pages": [{page, text}], "originalHash"}`. It accepts `document-revision` (and `product-guideline`, `asset` for code and images).
  - `document-revision`: `workspace.ontology_sources.Sources(ctx).resolve(ref, text=False)` for **current** authority. `authorize()` is not used, because it also admits historical metadata (`ontology_sources.py:177`); see review round 2, N10. Then `documents.library.Library(host, scope).projection(document, revision)` (L246). The revision must be `approved`, and its `sha256` must equal `ref.sha256`. The **document's** `aclVersion` must equal `ref.audienceRevision`: the ACL authority is on the document record, not the revision (`ontology_sources.py:154-164`; review round 5, X9). `ref.revision` is the revision record id, for example `<documentId>--r000001`. The paragraphs are grouped by `page`.
  - Reserved kinds (`published-asset`, `ux-contract`, `run-round`) keep failing `503 ontology-source-adapter-unavailable` until B0 sharing installs their adapters.
- **Logical pages for unpaginated sources (review round 8, AB4).** TXT, Markdown and HTML projections have `pages: 0` and `paragraph.page: None` (`documents/intake.py:136-146`). `resolve` assigns stable logical pages instead:
  - paragraphs are grouped in order into logical pages of ≤ 4 000 characters, never splitting a paragraph
  - each logical page is numbered from 1 and records `{page, paragraphIds: [first, last], logical: true}`
  - the derivative keeps these numbers, and every citation (E6/E7, the contract `source.page`) uses them
  - the mapping is deterministic from the projection hash
  - tests take real admitted TXT, MD and HTML revisions through PRD extraction, citation verification, contract validation (`rules.py:154` accepts the positive page) and planner confirmation
- `inspect(pages, *, denylist) -> receipt` returns:
  - `profile: "inspect-1"`
  - `pages`, `chars`
  - `pii: [{type, count}]`, from `pii.scan_rules` per page; no samples stored
  - `identifiers: {count}`, deny-list hits counted only
  - `classificationSuggestion: synthetic|public|internal-non-sensitive|sensitive`
  - `blocking: [reason]`
  - `hash`

  Any PII hit → `blocking += ["redaction-required"]`, with suggestion `sensitive`.
- The receipt is written to a private blob `key_for(owner, "adm_decision", <decision id>, "inspection.json")` by `admission.decide`, not by `inspect`.

- [ ] **Step 1: Tests:**
  - An approved synthetic revision gives a clean receipt.
  - A draft revision → `source-not-approved`.
  - A phone-number page → `redaction-required`, and the receipt contains no digits of the number.
  - An actor without source access → the `Sources` refusal is propagated.
  - A reserved kind → 503.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(intake): source-bound inspection receipts without stored samples"`

### Task I4: Identifier-normalization derivative and residual scan

**Files:**
- Create: `platform/intake/derivative.py`
- Test: `platform/tests/test_intake_derivative.py`

**Interfaces:**
- `load_denylist() -> list[{term, alias}]` reads from the private config named by `INTAKE_DENYLIST_PARAM` (SSM) or `INTAKE_DENYLIST_KEY` (private S3). If both are missing → `DenylistUnavailable`, which blocks admission.
- `normalize(pages, denylist) -> {"pages": [{page, text}], "replacements": {aliasCount}, "derivativeHash"}`:
  - Longest-term-first, case-insensitive for ASCII, NFC-normalized replacement with the entry's alias. The default alias is `고객사 A` for org names and `[내부 링크]` for URLs.
  - Internal URLs: the patterns `https?://[^\s]*\.(?:internal|corp|local)\b[^\s]*` plus deny-listed hosts are replaced.
  - Numbers are untouched. Financial values stay verbatim (P-02).
- `residual(pages, denylist) -> {"identifiers": n, "pii": [...]}`. Admission requires `identifiers == 0 and not pii`.
- `normalize_text(text, denylist) -> text` applies the same profile to a single prompt string, such as an ontology title or a user edit instruction. It then runs `residual`. Any residual identifier or PII hit raises `NormalizationBlocked(types)`, which reports counts only. B2 binds it as `deps["normalize"]` (review round 2, #2). Tests check replacement, a blocked phone number and byte-identical financial values.
- `derivativeHash = sha256(canonical([{page, text}]))`.

- [ ] **Step 1: Tests:**
  - A deny-listed name is replaced, and `연 2.0%` / `12개월` are byte-identical before and after.
  - The residual count is zero.
  - An overlapping term (a longer name containing a shorter one) is replaced as the longer one.
  - A missing deny-list config → `DenylistUnavailable`.
  - The derivative is deterministic, so the same hash is produced twice.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(intake): identifier-normalization derivative with residual scan"`

### Task I5: Image normalization (SRC-05)

**Files:**
- Create: `platform/intake/images.py`
- Test: `platform/tests/test_intake_images.py`

**Interfaces:**
- `normalize_image(data: bytes) -> {"bytes", "sha256", "originalSha256", "width", "height", "mode", "exifTransposed": bool, "iccProfile": "srgb"|"none"}` follows the v1 image profile (`AGENTCORE_CONTRACT.md:672-681`; review round 2, N19):
  - It accepts PNG and JPEG, ≤ 20 MiB, **≤ 20 megapixels** and ≤ 8192 px per side.
  - It rejects animation and decompression-bomb inputs (Pillow `MAX_IMAGE_PIXELS` set to 20 000 000, with `DecompressionBombWarning` treated as an error).
  - An embedded ICC profile that is neither sRGB nor convertible without loss (`ImageCms` to sRGB) → `unsupported-color-profile`.
  - It applies the EXIF transpose, converts to sRGB RGBA, strips metadata and re-encodes PNG. The original and derivative hashes are both kept.
- **Sanitized SVG** is part of the v1 contract profile, but this unit does not implement it. SVG inputs → `blocked: svg-sanitizer-unavailable`. SRC-05 is therefore recorded as **BLOCKED (partial)**, not PASS, until a reviewed sanitizer lands or the owning contract is amended.
- `region_ok(region, image) -> bool` requires integer top-left coordinates and `left + width <= image.width` and `top + height <= image.height`, measured on the normalized image.

- [ ] **Step 1: Tests:**
  - A rotated JPEG is transposed.
  - EXIF is removed.
  - An animated PNG is rejected.
  - Oversized dimensions are rejected.
  - A 5000×5000 image (25 MP, both sides under 8192 px) is rejected.
  - An SVG → `svg-sanitizer-unavailable`.
  - A non-sRGB ICC profile → converted to sRGB, or rejected.
  - An out-of-bounds region is rejected.
  - The hashes differ, and both are reported.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(intake): private image normalization with original and derivative hashes"`

### Task I6: Admission decision, verification and derivative reads (SRC-06, AUTH-01, AUTH-08)

**Files:**
- Create: `platform/intake/admission.py`, `platform/intake/review.py`
- Modify: `platform/workspace/http.py` (routes `GET /intake/reviews`, `POST /intake/reviews/{id}`)
- Test: `platform/tests/test_intake_admission.py`

**Interfaces:**
- `request(host, scope, source_ref, *, data_class) -> pending` is called by the design API in C with a verified actor. Steps:
  1. resolve and inspect
  2. derive a normalized derivative
  3. write the private blobs (derivative pages and inspection receipt)
  4. choose a path:
     - `synthetic`/`public`: requires a current `adm_provenance` whose `reference.sha256 == source.sha256` and which is in scope → `decide` immediately
     - `internal-non-sensitive`: creates `adm_decision` with `status: "pending-review"`
     - otherwise → `blocked`
- `review.decide(host, scope, decision_id, *, approve: bool, reason)` enforces the reviewer path:
  - the actor must hold a current `adm_grant` covering the project with `review-internal`
  - the actor must **also** have current source access, via `Sources.resolve` (current authority, never the historical `authorize`; review round 3, N10)
  - on approval it writes `review: {actor, grantId, grantRevision, at}` and `status: "admitted"`
  - the grant never confers source access on its own
- `decide(...)` checks that the inspection has no blocking reasons, the residual is clean, the policy is active and in scope, and the data class is allowed. It then seals the decision with an expiry: `min(policy.expiresAt, provenance/grant expiresAt, now + 30 days)`.
- `verify(host, scope, decision_id) -> decision` rechecks, raising `AdmissionError(code)`. Source checks use `Sources.resolve` (current authority), never `authorize`:

  | Code | Check |
  |---|---|
  | `decision-not-current` | The decision is current |
  | `policy-changed` | Policy revision and hash still active and equal |
  | `grant-revoked` | Provenance/grant still current, where applicable |
  | `source-changed` | Source audience and revision still equal, via `Sources.resolve` |
  | `artifact-changed` | Derivative blob hash equals `derivation.derivativeHash` |
  | `inspection-changed` | Inspection blob hash equals `inspection.hash` |

- **Bounded delivery (review round 17, AK1).** The library supports 400 000 text characters (`library.py:23`), so a complete derivative can exceed the 512 KiB tool response. `pages_for` therefore returns **pages in bounded batches**: `pages_for(host, scope, decision_id, *, cursor=None, max_bytes=400_000) -> {pages, cursor, total, derivativeHash}`.
  - The cursor binds `(decisionId, decisionRevision, derivativeHash, actor, nextPage)` and expires in 5 minutes.
  - Each batch reruns `verify`, so a revocation between batches stops delivery.
  - The concatenation of all batches is hash-checked against `derivativeHash` by the consumer before use.
  - `admission.pages` (B2 S3) exposes the cursor.
  - Tests use a 180 000-character Korean document (> 512 KiB as JSON): extraction → batched transfer → E7 consumption succeeds with every response ≤ 512 KiB, and a revocation between batches → `409`, with no further pages.
- `pages_for(host, scope, decision_id) -> [{admissionId, derivativeHash, sourceRef, page, text}]` is the **only** function that returns text for model use. It has the batched signature above. It calls `verify(host, scope, decision_id)` first, and both take the same context-bearing arguments (review round 2, N9).
- `admission_ref(decision) -> {"decisionId", "revision", "artifactHash"}` feeds the ledger's `admissions` list.
- Routes: `/intake/reviews` lists `pending-review` decisions the actor may review, showing source title, page count, inspection summary and derivative preview. The preview is derivative text only, never the original. `POST` approves or rejects. The route requires JWT plus a current grant; it confers no admin rights.

- [ ] **Step 1: Tests:**

  **Happy paths**
  - A public source with registered provenance is admitted.
  - An internal document is `pending-review`. A reviewer with a grant and source access admits it.

  **Denied or blocked**
  - A reviewer with a grant but no source access → denied.
  - A project owner without a grant → denied.
  - PII → `blocked`.
  - A missing deny-list → `blocked`.

  **Revocation and change**
  - Policy retirement after admission → `verify` raises `policy-changed`.
  - Grant revocation → `grant-revoked`.
  - A tampered derivative blob → `artifact-changed`.
  - A source ACL change, via `documents` `_permissions` bumping `aclVersion` → `source-changed`.

  **Full chain**, with no pre-seeded records: admin `put_policy` + `activate_policy` + `grant_reviewer` through `admin_handler` writes `adm_audit` records, then `request` → `pending-review` → `review.decide` → `admitted` → `pages_for`. Every closed schema accepts each record written along the way.
  - A wrong `sha256` or `revision` in the source ref is rejected by `resolve`, even though `authorize` would accept historical metadata.

  **Derivative reads**
  - `pages_for` never returns original text. Assert that the deny-listed term is absent and the alias is present.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(intake): admission decisions with current-authority verification and derivative-only reads"`

### Task I6a: Code-collection and prompt-text admission (review round 3, F12 and F2)

**Interfaces:**

**Code collection.** `request_collection(host, scope, source_ref, *, root, resolver_profile_id) -> pending`:
- The resolver comes from a server-configured profile (review round 8, AB2). Profiles `{id, revision, hash, aliases, packages}` are IAM-administered, and their packages are verified against the platform package registry. The decision freezes the profile id, revision and hash. A caller-supplied `resolver` map is rejected.
- The source is an `asset` import revision: a ZIP of a repository subset, whose import revision and byte hash come from the existing asset intake.
- The derivative is stored as two private objects (review round 4, F12):
  - a collection index `{files: [{path, kind: code|style|json|html|asset, sha256, size}], resolver: {aliases, packages, jsonAssetFields}}`
  - the normalized file bytes

  The analyzer request is **built from them** in the `source.analyze` context stage as `{schemaVersion: 1, files: [{path, kind, sha256, text}], resolver}`. Asset entries omit `text`, and `size` is not included, matching the analyzer's closed fields (`analyze.cjs:30-40`).
  - It keeps each file's **collection-relative path**. Basenames collide, and relative imports need the full path.
  - It passes the analyzer's `safePath` rules (`source-analyzer/analyze.cjs:20-24`) and its limits of 100 files, 100 KiB per file and 2 MiB in total.
  - **Serialized envelope** (review round 32, AZ3). The analyzer CLI rejects serialized requests above 4 000 000 bytes (`analyze.cjs:466`), and JSON escaping can expand valid source beyond 2 MiB, for example backslash-heavy code. Admission therefore computes the exact analyzer request as the `source.analyze` context stage will build it, with `json.dumps(..., ensure_ascii=False)` giving the same bytes the Interpreter writes. It requires ≤ 3 900 000 bytes, with headroom. Larger requests → `collection-too-large` with the measured size, and the caller splits the collection. Test: 24 backslash-heavy TypeScript files with 2 073 600 source bytes are rejected at admission. An at-limit escaped collection passes through the real CLI in the Interpreter adapter test.
  - It **also** passes the existing ingestion adapter's stricter validation, reused from `workspace/ontology_analysis.py:35-45`, including the ≤ 500-character path limit that ontology source locations require (`ontology_schema.py:135`). The check runs at request time and again **after normalization**, since renamed segments change the length, before any paid execution (review round 24, AR2). Test: 500- and 501-character paths. The 500-character path mines and publishes. The 501-character path is rejected at admission with `path-too-long`, so no execution is started.
- Identifier normalization is applied **consistently** to file text, and to path segments that contain deny-listed terms, with one alias per term. Imports therefore still resolve. The mapping from original path to derivative path stays in the private decision blob and is never transferred.
- The resolver is part of the decision hash. A resolver change is a new decision.
- **Derivative resolver** (review round 22, AP2). Normalization may rename path segments, and the analyzer expands alias targets literally and requires the result to exist (`analyze.cjs:127-146`). The transferred resolver is therefore **derived**: every alias target and every path-bearing field is rewritten with the same path mapping applied to the files.
  - **Package names** (review round 23, AQ1). A deny-listed package scope, for example `@tenant-example/ui`, is renamed through a private, server-derived **package mapping**, for example to `@neutral-1/ui`. The mapping is applied **together** to:
    - every import specifier in the files
    - every `package.json` dependency key
    - the derivative resolver's `packages` keys

    The derivative key keeps the authoritative entry's `version` and `sha256` unchanged. Registry verification (E5) runs on the **authoritative** names, privately and before transfer. `source.analyze` checks, through the private mapping, that each derivative package key maps back to an authoritative profile key with identical version and hash.
  - The decision binds both hashes: `resolverProfile: {id, revision, hash}`, the authoritative server profile, and `derivativeResolverHash`.
  - `source.analyze` checks that re-applying the private mapping to the profile yields the derivative resolver.
  - Test: a collection importing a deny-listed package scope is normalized, and the real analyzer returns `approved-package` for the renamed import. Mining yields its candidates. A mismatched derivative key → the decision is rejected.
  - Test: a collection whose aliased directory is renamed by normalization (for example to `src/neutral-parts/`) still resolves `@parts/Header` as `resolved-local` through the real analyzer, and mining yields the expected candidates.
- Admission follows the same reviewer and provenance rules as documents.
- The execution reads the collection through input handles (B0 ledger T7). The derivative collection is exactly the analyzer request `files` array.

**Prompt text.** `admit_prompt_text(host, scope, text, *, purpose: "edit-instruction"|"proposal-note") -> decision`:
- It runs synchronously in the API. It applies `normalize_text` with the private deny-list, then the residual scan.
- It stores the **immutable** instruction as a private blob `key_for(owner, "adm_decision", <id>, "prompt.json")`, which holds the original and normalized text plus the author. Its intake source identity is `{sourceKind: "prompt-text", sourceId: <instructionId>, revision: "1", sha256: <normalized sha>, audienceRevision: <project authorityRevision>}`. This is an intake-only source kind resolved by intake from that blob. It is not an ontology `SOURCE_KINDS` value, so no ontology schema change is needed.
- The decision follows the normal `internal-non-sensitive` path: **`pending-review`, admitted by a reviewer holding a current grant** (review round 4, X3). Authorship does not establish classification (`AGENTCORE_CONTRACT.md:639-658`).
- The policy schema adds `promptText: "review"`, and `"auto"` is **rejected** in v1. Any automatic admission of actor-authored instructions needs an explicit owning-contract amendment approved by the platform security operator. This is roadmap decision D-5.
- A hit that normalization cannot remove → `blocked`.
- The Runtime receives only the admitted derivative, through `admission.pages`. The raw deny-list never leaves the private side, and the former `admission.policy` idea is removed.

**Tests:**
- Two files with the same basename in different folders keep distinct paths, and the analyzer resolves a relative import between them.
- An alias entry resolves.
- A deny-listed package scope is normalized consistently in the import and in `package.json`, so resolution still succeeds.
- A resolver change → a new decision.
- An edit instruction containing a deny-listed name is `pending-review` with the alias applied. A reviewer admits it, and `promptText: "auto"` in a policy is rejected by I1 validation.
- A phone number → `blocked`.

### Task I6b: Diagram and table transcription for guidelines (O-02; review round 8, AB5)

O-02 requires knowledge from text, **tables and diagrams**. The library marks visible images and PDF graphics as untranscribed (`documents/intake.py:51`, `documents/_pdf.py:59`), and a revision with incomplete extraction cannot be approved (`documents/api.py:213`). Guideline diagrams therefore take a separate, source-bound path.

**Interfaces:**
- `request_diagram(host, scope, source_ref, *, page, region) -> pending`. The source is an image asset, normalized through I5, or a rendered PDF page region. `region` uses the `normalizedImageHash` coordinates.
- A new execution operation `intake.transcribe` goes through the B0 ledger, with stages context → generate → verify. It sends the normalized, admitted image derivative to a vision-capable model through `engine.gate.generate_with_images` (`gate.py:338`), whose boundary checks apply. The derivative must first pass admission itself (reviewer grant, `internal-non-sensitive`).
- The output is a transcription derivative `{page, region, text, tables: [[cell]], kind: "diagram"|"table"}`.
- A **reviewer** validates the transcription against the image in the review UI (`/intake/reviews`, side by side).
- **Business source (review rounds 9 and 10, AC2, AD1, AD2).** A validated transcription becomes a library document revision through a **trusted server adapter**, not the public document API. The HTTP API rejects caller-supplied `provenance` (`documents/api.py:24-37`) and is not changed.
  - **Publish.** `documents.library.publish_transcription(host, scope, decision)` is a new library function, callable only from `intake.review`. It creates an MD document of kind `guide-transcription` with `readRoles` copied from the source audience, and a revision in status `in_review`. The revision carries a **server-owned** field `transcriptionOf = {sourceRef (original image asset ref), decisionId, decisionRevision, visionSha256, normalizedImageHash, region}`. `documents/CONTRACT.md` is amended with this field and the adapter's caller restriction.
  - **Library approval stays separate.** A planner/owner approves the revision through the existing library review route (`library.py:195` capabilities), independently of the intake reviewer's transcription validation. The intake grant does not confer library review.
  - **Library fences** (review round 12, AD2). An accessor that validates `transcriptionOf` lineage records its upstream observations in `Library._upstream`, a list of `{owner, kind, id, version}` for **every record `verify` read**: the original asset record, the image `adm_decision`, and the `adm_policy`, `adm_provenance` and `adm_grant` records it depends on (review round 13, AD2). Retiring the policy or revoking the grant just before commit therefore also aborts the transaction, and the test covers that case.
  - **Ontology propagation** (review rounds 14 and 15, AD2). `Sources.resolve`, for a `document-revision` with `transcriptionOf`, and `intake.admission.verify` both call a new `Sources._remember_owned(owner, kind, record)` for **every** upstream record they read. It builds the check `{owner, kind, id, version}` with the record's **actual** owner partition: `intake:deployment` for policy, provenance and grant, and the project partition for the decision and the asset. It does not go through `ctx.check`, which always assigns the current project owner (`workbench/service.py:190-191`). A test covers normal transcription publication with a globally stored policy, which succeeds, before the revocation race tests. The original round-14 wording follows: they call `Sources._remember(kind, record)` for **every** upstream record they read: the asset, the decision, the policy, the provenance and the grant (`ontology_sources.py:64-73`). Those records then enter `Sources.observed`, which `publish_candidate` commits as checks (`ontology_store.py:360`). The records are deduplicated by key, count against `max_records`, and have their expiry rechecked at `recheck()`. The race test is extended to ontology publication: retiring the policy immediately before the publish transaction → `409`, with nothing published. `Library.checks()` (`library.py:295-311`) appends them as `put_many` version checks, so `commit()` and `assert_current()` (`:313-330`) fence upstream authority atomically with the library write. Test: revoke the original between projection validation and the approval commit, and assert `409` with the revision still `in_review`.
  - **Library read paths** (review round 11, AD2). The library's own shared accessors `Library.revision()`, `projection()` and `read_blob()` (`library.py:212-246`) check `transcriptionOf` lineage for any revision that carries it. Library reads that bypass the ontology resolver therefore still enforce upstream revocation: paragraph reads (`documents/api.py:285`), downloads of every chunk (`:314`) and review approval all go through these accessors. A revoked upstream → `409 source-upstream-revoked`. Tests cover paragraph read, each download chunk and approval after revocation.
  - **Upstream enforcement.** `Sources.resolve` for a `document-revision` whose revision has `transcriptionOf` additionally resolves `transcriptionOf.sourceRef` with **current** authority and calls `intake.admission.verify(host, scope, decisionId)`. Either failure → `409 source-upstream-revoked`. The same check runs in admission `verify`, in transfer (`open_input`) and in the publication fences, because they all go through `resolve`/`verify`.
  - **Citation.** The rule's citation keeps the original image lineage through the optional `citation.region` and `citation.normalizedImageHash` (E3 closed schema). As a `document-revision`, the source satisfies `review_node`'s business-authority check (`ontology_store.py:740-742`).
  - **Tests** go through the real API with no pre-seeded approval:
    - `publish_transcription` from outside `intake.review` → `PermissionError`
    - a designer's library review → 403, and a planner's → approved
    - after approval, revoking the original image asset's access, or the image decision, makes the transcription's `resolve` return `source-upstream-revoked`, and blocks a new admission and an in-flight transfer
- E6 consumes it like any other admitted page, so rules cite `page` and `region`.
- Tests: a synthetic, non-sensitive guideline diagram fixture (a PNG flowchart saying "자격 미충족 시 사유 화면") runs this chain through the actual `Sources.resolve` and `review_node`:
  1. admission
  2. vision derivative (real gate, fake transport)
  3. transcription
  4. reviewer validation
  5. library revision approval
  6. rule extraction
  7. publication
  8. planner approval

  An unreviewed transcription cannot reach E6.

### Task I7: Metadata-only audit export (§7.2)

**Files:**
- Create: `platform/intake/audit.py`
- Test: `platform/tests/test_intake_audit.py`

**Interfaces:**
- `export(storage, owner, *, since, until) -> {"decisions": [...], "transfers": [...]}`:
  - A decision entry is `{decisionId, revision, dataClass, policy, reviewer?, derivativeHash, at}`.
  - A transfer entry comes from the ledger jobs' `transfers` and `calls`: `{executionId, attemptId, decisionId, artifactHash, stage, model, at}`.
  - It contains no text, no deny-list terms and no prompts.

  The output is used for the 혁신금융 application record.

- [ ] **Step 1: Tests:**
  - The export of a seeded admitted decision plus a ledger job contains the IDs and hashes.
  - It contains no page text: assert that no seeded text substring occurs in the JSON.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(intake): metadata-only admission and transfer audit export"`

### Task I8a: Lambda packaging for the new modules (review round 3, F14)

The workspace API, AdminFn and tool Lambdas deploy from `api-dist`. `deploy.sh:38-47` assembles it from a fixed module list that does not include `intake`.
- [ ] Add `intake` (and later `execution_tools`) to that list.
- [ ] Image normalization (I5) needs Pillow, which `api-dist` lacks. It runs in the workspace **Worker** Docker image (`workspace/Dockerfile`, which already ships Pillow). Images are workspace **assets**, not library documents: the library accepts only PDF/HTML/MD/TXT (`documents/library.py:29`). The wiring is explicit (review round 5, Y4):
  - A new worker task `intake-image` is added to `Worker.handle`'s dispatch (`worker.py:253-270`), next to `finalize`, with the job target `("adm_decision", "decisionId")` added to `_JOB_TARGETS`.
  - Its producer is `intake.admission.request` for `kind: "image"`. It creates the `pending` decision and queues the job through `WorkspaceAPI._new_job`/`_invoke`.
  - The job reads the stored asset original, runs `normalize_image`, writes the derivative blob and completes the decision with a version-CAS `put_many` together with the job.
  - **Vision derivative (review round 9, AC1).** The job also builds the exact input that `engine.gate.generate_with_images` accepts (`gate.py:338-360`):
    - `{format: "png"|"jpeg", bytes ≤ 3 000 000, ocrStatus, ocrText}`
    - the normalized PNG is downscaled or re-encoded until it fits, with the transform recorded
    - OCR comes from the Worker's existing private `self.ocr` (`worker.py:151,222,398`), producing `ocrStatus: "complete"` and `ocrText`
    - the residual PII and deny-list scan runs over `ocrText`, and a hit → `blocked`
    - the decision binds `visionSha256`, `ocrSha256` and the transform
    - an incomplete OCR status → `blocked: ocr-incomplete`
  - Tests call the **real** `generate_with_images` with a fake Bedrock adapter over the produced derivative, and it succeeds. Missing OCR → `GateUnsupported`, and an oversize source is downscaled under 3 MB. Only the Worker, a trusted server extraction path, produces OCR attestations.
- **Runtime delivery (review round 10, AD3).** The Runtime receives the vision derivative as a bounded **descriptor**: `admission.pages` returns `{format, ocrStatus, ocrText, visionSha256, size}` for image decisions, with no bytes. The bytes come through the B0 ledger T7 admission-bound input handle (`open_input` + 256 KiB `read_chunk`). Each chunk is rechecked, and the assembled bytes must equal `visionSha256` before `generate_with_images`. Test: the full Gateway → chunk assembly → real gate path with a 788 KB PNG, where every response is ≤ 512 KiB, and a revocation between chunks stops the transfer.
  - Failure uses the existing `_update` failure path for the job, and marks the decision `blocked`.
  - Atomic completion (review round 6, Z7): `intake-image` joins the atomic-completion set in `Worker.handle` (`worker.py:276-280`), next to `document-*` and ontology analysis. After the handler, the worker **verifies** the committed `completed` job instead of writing it again.
  - The exception path first re-reads the job. If it is already `completed`, it returns completed, as the ontology-analysis branch does (`worker.py:285-290`), and never overwrites a committed outcome.
  - Test: inject a failure after the atomic commit, and assert job `completed` and decision `admitted`.
  - Tests run request → job → worker `handle` → decision `admitted`/`pending-review` with a real PNG fixture. An unsupported JPEG profile → `blocked`. **Update `workspace/Dockerfile`'s copy list (`:21`) to include `intake/`** (review round 4, F14).
- [ ] **Worker deny-list access** (review round 28, AV1). The `intake-image` task runs the residual scan in the Worker. `infra/lib/workspace.ts` therefore adds `INTAKE_DENYLIST_PARAM` to the Worker environment (`:158`), and `ssm:GetParameter` on exactly that parameter ARN (plus `kms:Decrypt` for its key when it is a SecureString with a CMK) to the Worker role. `check_infra` asserts it. Tests under the Worker principal's permissions: clean image admission succeeds, a missing configuration → `blocked: denylist-unavailable`, and a denied read → `blocked`.
- [ ] Extend `test_packaging.py` to build the Worker image context, or statically parse the Dockerfile `COPY` lines when Docker is unavailable. Assert that `intake/` is present, and that `python -c "import intake.images, intake.admission"` succeeds in the image. The Docker check is marked `docker`.
- [ ] Add `platform/tests/test_packaging.py`:
  1. Run the `api-dist` assembly part of `deploy.sh` into a temp directory, using a new `deploy.sh --assemble-only <dir>` flag that performs step 1 only.
  2. Import `workspace.http`, `intake.admin_handler` and `intake.admission` from that directory in a subprocess, with `PYTHONPATH` limited to it.
  3. Assert that a handler smoke call returns without an `ImportError`.
- [ ] Commit: `git commit -m "build: package intake modules into the api artifact and smoke-test imports"`

### Task I8: IntakeAdminFn construct (synthesized, not deployed here)

**Files:**
- Create: `platform/infra/lib/intake.ts`
- Modify: `platform/infra/lib/stack.ts` (instantiate behind the context flag `intakeAdmin=true`, default false)
- Modify: `platform/workspace/check_infra.py`
- Test: extend `check_infra` tests (`platform/tests/test_check_infra.py` if present; otherwise create it)

**Interfaces:**
- `IntakeAdminFn` is a Python 3.12 Lambda with handler `intake.admin_handler.handler`.
  - IAM: read and write only on the workspace table items under the `intake:deployment` partition, via a `dynamodb:LeadingKeys` condition on `owner#<sha256("intake:deployment")>`, and `ssm:GetParameter` on the deny-list parameter.
  - No API route, no Function URL and no resource policy for `apigateway.amazonaws.com`.
- New `check_infra` assertions:
  - no `AWS::Lambda::Permission` with `Principal: apigateway.amazonaws.com` targets IntakeAdminFn
  - no `AWS::Lambda::Url` for it
  - its role has no `lambda:InvokeFunction` on other functions
- The offline CDK check follows `CLAUDE.md`: `cdk synth -c planeDeployed=false -c intakeAdmin=true` must pass `check_infra.py`.

- [ ] **Step 1: Tests:** synthesize with the flag, then assert the three conditions. Also check that a hand-edited template with an API permission fails the check.
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(infra): IAM-only intake administration function (flagged, synth-checked)"`

### Task I9: Owning contracts

- [ ] In `AGENTCORE_CONTRACT.md` `source-admission/1`, record the concrete bindings required by L602-605:
  - module `platform/intake/*`
  - storage kinds and partitions
  - IAM-only entry point `IntakeAdminFn`
  - the reviewer routes `/intake/reviews`, JWT plus current grant
  - the `identifier-normalization-1` profile
  - the explicit `redaction-required → blocked` rule until the privacy adapter is reviewed
- [ ] In `documents/CONTRACT.md`, add that intake reads approved revision projections to build derivatives. The library remains the source authority, and derivatives are not library records.
- [ ] In `docs/CONTRACTS.md`, add the `/intake/reviews` routes and the storage kinds.
- [ ] Run `git diff --check`.
- [ ] Commit: `git commit -m "docs(intake): record source-admission/1 bindings"`

## Verification before PR

```bash
cd platform
python3 -m pytest tests/test_intake_*.py -q
python3 -m pytest tests/ -q
(cd infra && node -e "require('fs').writeFileSync('cdk.context.json', JSON.stringify({'availability-zones:account=000000000000:region=ap-northeast-2':['ap-northeast-2a','ap-northeast-2b']}))" \
  && CDK_DEFAULT_ACCOUNT=000000000000 npx cdk synth BankPlatform --quiet -c planeDeployed=false -c intakeAdmin=true \
  && python3 ../workspace/check_infra.py cdk.out/BankPlatform.template.json)
git diff --check
```

The PR body lists the SRC-01/05/06 and AUTH-01/08 O-mode results. It states that G0-ADMISSION live evidence and the privacy redaction adapter remain outstanding.
