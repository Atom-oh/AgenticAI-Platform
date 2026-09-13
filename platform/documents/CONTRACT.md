# Internal document library and source-bound S1

This contract implements the user's choice of an internal document library.
Existing document names in the shared demonstration ontology are not original
files. Unlinked records must remain explicitly unlinked.

## Authority and storage

- Reuse the committed workspace HTTP API, Cognito access-token `sub`, canonical
  project membership, private S3 bucket and DynamoDB records. Do not depend on
  the separate unmerged ontology-workbench branch.
- `X-Workspace-Project` selects an existing project; absence means the caller's
  personal collection. A request never supplies a storage owner or S3 key.
- Roles are `owner`, `planner`, `designer`, `developer`. Document `readRoles`
  must include `owner`; default is all four. Owner can manage permissions.
  Owner/planner can edit and review; the creator can edit while still a member
  with read access. Reading, searching, chunk downloads, worker input and
  analysis-result retrieval all recheck current document access.
- Use storage kinds `document`, `docrevision`, `docbinding`, `docaudit`,
  `docanalysis`, `docdecision`. Source blobs must never be stored as ordinary
  workspace `asset` records, whose project-wide read path would bypass a
  document's narrower permissions.
- Content is immutable; metadata uses CAS. Project membership and document
  authority participate in transactional fences. Revocation may not publish a
  worker result after the final authorization check.
- A commit uses the same authority version that authorized its operation.
  Silently refreshing a downgraded role and fencing that newer version is not
  authorization. A changed project/document snapshot requires reauthorization
  of the operation or a conflict. Model completion uses a new read-authorized
  snapshot of every source, followed by its transactional fences.
- Records and source bytes remain private. No static website upload, public
  source URL, arbitrary URL fetch, model-generated URL, shared event-cache replay
  or browser local storage is permitted for document content.

## Library records

`Document` fields: `id`, `version` (metadata CAS), `title`, `kind`, `graphRef`
(optional existing Regulation/Document node ID), `createdBy`, `projectId`,
`readRoles`, `aclVersion`, `status` (`active|archived`), `latestRevisionId`,
`approvedRevisionId`, `createdAt`, `updatedAt`, `provenance`
(`uploaded|synthetic_sample`).

`Revision` fields: `id`, `documentId`, `version` (metadata CAS), `revision`
(file import ordinal), `name`, `size`, `sha256` (original bytes), `versionLabel`,
`effectiveDate` (optional supplied date), `createdBy`, `status`
(`uploading|processing|draft|in_review|approved|rejected|failed`), `parseStatus`,
`textHash`, `paragraphCount`, `pages`, `warnings`, timestamps and review metadata.
Private `parts`, blob keys and request fingerprints are not returned to clients.
Approving a revision never mutates its content. New content needs a new revision.
Approval cannot replace a newer already-approved import ordinal with an older
one. Historical source links remain on their original revision; a new approved
revision is reported as stale for an earlier analysis decision.

`Paragraph`: `{id: "p000001", text, page: number|null, sha256}`. Paragraph text is
the immutable extraction projection, not necessarily a byte-identical PDF text
stream. Preserve the separate original hash and extraction hash.
`textHash` is SHA-256 of the complete canonical UTF-8 JSON projection
(`sort_keys=True`, `ensure_ascii=False`, separators `(",", ":")`, no NaN).
The projection contains `schemaVersion`, `paragraphs`, `pages`, `parseStatus`
and `warnings`; it does not contain its own hash.

One active approved binding per `graphRef` in a collection. Store its pointer
under `docbinding` using `binding_id(graphRef)`; replacing an approved revision
of the same document updates it atomically. A different document cannot silently
take that binding. Archiving its document deactivates the binding.
Binding fields are `id`, `graphRef`, `documentId`, `revisionId`, `sha256`,
`textHash`, `status`, plus storage CAS/timestamps.

Limits: originals 20 MiB, upload chunks 2 MiB, 200 documents per collection,
100 revisions per document, PDF 200 pages, extracted text 400,000 characters,
each paragraph at most 2,000 characters, source responses at most 50 paragraphs.
Supported originals: PDF, HTML/HTM, Markdown/MD and UTF-8 TXT. No external
resources or uploaded programs are executed. Empty, encrypted, damaged, scanned
or truncated text is reported; incomplete extraction cannot be approved for AI
use. Unsupported files are rejected before upload registration.

## HTTP interface

All paths below are under `/studio-api`; responses use private/no-store headers.
Identifiers are server generated; create operations require an idempotent
`requestId`, and updates require the displayed `version`.

| Method / path | Input / output |
|---|---|
| GET `/documents/config` | `roles`, `maxFileBytes`, `chunkBytes`, `extensions`, `role`, `actorId`, `projectId` |
| GET `/documents/references` | shared ontology `references: [{id,label,title,version?,effectiveDate?}]`, actual `backend` and demonstration/source note |
| GET `/documents?q=&cursor=` | authorized `documents`, optional `cursor`; q searches title/reference, not an unconfigured vector index |
| POST `/documents` | `{requestId,title,kind,graphRef?,readRoles?,name,size,sha256,versionLabel?,effectiveDate?}` → 201 `{document,revision,chunkBytes}` |
| GET `/documents/{id}` | `{document,revisions:[Revision],capabilities:{read,edit,review,manage}}`; current access required |
| POST `/documents/{id}/revisions` | `{requestId,version,name,size,sha256,versionLabel?,effectiveDate?}` → 201 `{document,revision,chunkBytes}` |
| PUT `/documents/{id}/revisions/{rid}/parts/{index}` | raw bytes; immutable, identical retry allowed → `{revision,index,sha256}` |
| POST `/documents/{id}/revisions/{rid}/complete` | `{}` → 202 `{document,revision,job}`; queue `document-finalize` |
| GET `/documents/{id}/revisions/{rid}?paragraph=&cursor=` | `{document,revision,paragraphs,cursor?,totalParagraphs}`; optional paragraph selects its surrounding slice |
| GET `/documents/{id}/revisions/{rid}/blob?kind=original&offset=` | original chunks using existing `readPrivateBlob` protocol; attachment only, never execute HTML |
| POST `/documents/{id}/revisions/{rid}/submit` | `{version}` → `{document,revision}` |
| POST `/documents/{id}/revisions/{rid}/review` | `{version,decision:"approved"|"rejected",note}` → `{document,revision}` |
| PUT `/documents/{id}/permissions` | `{version,readRoles}` → `{document}` |
| POST `/documents/{id}/archive` | `{version}` → `{document}`; soft archive, no S3 deletion |
| GET `/documents/{id}/activity?cursor=` | authorized append-only `events`, optional `cursor` |
| POST `/documents/samples` | `{requestId}` → private synthetic example documents; **draft only**, never automatic approval |

Regular uploads cannot claim `synthetic_sample` provenance. The sample installer
is server-owned and labels every source as synthetic. No real bank material is
committed or sent to external reviewers.
Sample creation requires current collection-owner authority inside its write
fence. A sample document cannot receive an ordinary replacement revision; users
create a separate uploaded document instead. Historical sample provenance is
never relabelled.

## Backend integration interface

`documents.library.Library(host, scope)` consumes `host.storage` and
`host.collaboration`. It exposes:

```python
library.fresh() -> dict                         # canonical current scope
library.document(document_id, action="read") -> dict
library.revision(document, revision_id, approved=False) -> dict
library.projection(document, revision) -> dict  # verified textHash, paragraphs
library.binding(graph_ref) -> tuple[dict, dict] | None
library.checks(documents=()) -> list[dict]        # project/current-document CAS checks
library.commit(writes, documents=()) -> list[dict]
```

`documents.api.handle(host, scope, method, parts, event, query)` returns the full
workspace HTTP response for `documents` routes. `documents.intake.finalize(worker,
owner, job)` handles `document-finalize`. Its stored input contains trusted
`actorId`, `projectId`, `documentId`, `revisionId`; no request-provided owner.
`documents.library.authorize_job(host, scope, job)` protects generic job reads.
`DocumentError(status, code, message)` is a bounded, non-sensitive domain error.
Library code never imports a model client.

`Storage.list_page` gains optional `prefix=""`; encoded cursors must match the
owner, kind AND prefix. Revision/audit IDs start with `{documentId}--`, so
pagination does not scan another document's history.

## S1 analysis

The commercial S1 view uses `/impact-analyses`, not the legacy shared-cache
comparison stream. The legacy engine/API is not a private-document consumer.
Do not label ID existence as semantic verification.

| Method / path | Input / output |
|---|---|
| POST `/impact-analyses` | `{requestId,query,regulationRef,modelId?}` → 202 `{analysis,job}` |
| GET `/impact-analyses?cursor=` | currently authorized `analyses`, optional cursor |
| GET `/impact-analyses/{id}` | `{analysis,result?}`; reauthorize every source before returning any private result |
| POST `/impact-analyses/{id}/decisions` | `{version,nodeId,decision:"change_required"|"unaffected"|"needs_review",note}` → `{analysis,decisions}` |

Worker task `document-analysis` pins original/extraction hashes and approved
revision IDs before model input. Select the regulation explicitly; reuse the
actual GraphStore traversal for candidate assets, recording its backend. The
shared demonstration ontology is not a verified customer graph.

At most 20 approved, authorized source documents and 36,000 context characters
enter a model call. Record excluded/unlinked/truncated coverage explicitly.
Required regulation source absent: return `needs_sources` with relationship
candidates and no model call; offer document registration. Exclude missing,
unapproved, inaccessible, malformed and changed sources from prompts. An access
failure never falls back to another user's cache or another document version.

The result contains `regulation`, `counts`, `candidates`, `sources`, `evidence`,
`findings`, `summary`, `coverage`, `verification`, `model`, `decisions`.
Evidence is `{id:"E1",documentId,revisionId,paragraphId,title,revision,
versionLabel,originalSha256,textHash,quote,page,provenance}`. The UI constructs
same-app links from these fields. Models never supply URLs or storage paths.

Model output is strict JSON `{summary,findings:[{nodeId,reason,citationIds}]}`.
Reject unknown node/citation IDs and findings without citations. Source integrity
and citation existence are checked separately from a human's content judgment.
The result remains `needs_review`, never automatically "수정 확정" or "검증 통과".
Owner/planner decisions and their notes/actor/timestamp are separately audited.
No original text, query, output or credentials go into application logs.

The worker rechecks membership and every source before the model call, before
publication and on result reads. A failed authorization/integrity check blocks
publication. Use the existing Bedrock gate and usage budget; do not use the
shared S1 event cache or send private text to an external service.

## User interface

Add `#/documents` as "내부 문서함"; reuse project selection plus personal scope.
The stable source URL is
`#/documents?documentId=...&revisionId=...&paragraph=...&projectId=...&textHash=...`.
Omit projectId for personal scope. A missing/changed/forbidden version is not
silently replaced by the latest revision.

Support file upload progress/retry, revision history, original download,
paragraph reading/highlighting, review and role permissions. Uploaded HTML is
rendered only as extracted text. Clear private views and object URLs on scope
changes, access errors and unmount.

S1 shows the selected regulation, source readiness, actual candidate lists,
clickable evidence, coverage and human review state. Technical model/traversal
details are secondary. Empty, forbidden, extraction-failed, unlinked and stale
states are actionable and distinct. Never invent an original for a metadata-only
record or imply sample documents are genuine banking policies.
