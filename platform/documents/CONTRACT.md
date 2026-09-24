# Internal document library and source-bound S1

This contract implements the user's choice of an internal document library.
Existing document names in the shared demonstration ontology are not original
files. Unlinked records must remain explicitly unlinked.

## Authority and storage

- Reuse the committed workspace HTTP API, Cognito access-token `sub`, canonical
  project membership, private S3 bucket and DynamoDB records. Document-library
  authorization and storage remain independent of workbench knowledge indexes.
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
- Document and own-analysis listings paginate authorized matches, with one
  authorized lookahead. A cursor resumes after the last returned match; hidden
  records never supply a public continuation key. A bounded scan that cannot
  complete returns `list-scan-limit` without rows or cursor. Document lookahead
  participates in the final permission fence.
- Canonical `/documents/config` authorization is independent of project-discovery
indexes. Missing or unavailable discovery cannot veto an otherwise authorized
  personal collection or shared link; scope mismatch and actual authorization
  failures still block private content.
- A readable shared source/analysis does not imply access to its requester's
  raw job. On a raw-job 403/404, the UI refreshes the authorized resource instead;
  denial from that resource still clears private state. Unlinked registration
  has an explicit action independent of optional reference discovery.

## Library records

`Document` fields: `id`, `version` (metadata CAS), `title`, `kind`, `graphRef`
(optional existing Regulation/Document node ID), `createdBy`, `projectId`,
`readRoles`, `aclVersion`, `status` (`active|archived`), `latestRevisionId`,
`approvedRevisionId`, `createdAt`, `updatedAt`, `provenance`
(`uploaded|synthetic_sample|intake-transcription`).

`Revision` fields: `id`, `documentId`, `version` (metadata CAS), `revision`
(file import ordinal), `name`, `size`, `sha256` (original bytes), `versionLabel`,
`effectiveDate` (optional supplied date), `createdBy`, `status`
(`uploading|processing|draft|in_review|approved|rejected|failed`), `parseStatus`,
`textHash`, `paragraphCount`, `pages`, `warnings`, timestamps and review metadata,
and the server-owned optional `transcriptionOf` described below.
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
| GET `/documents/samples` | `{schemaVersion:1,notice,samples:[{graphRef,title,kind,name,versionLabel,sha256,sections:[{title,text}]}]}`; template preview only, no creation or model calls |
| POST `/documents/samples` | `{requestId}` → private synthetic example documents; **draft only**, never automatic approval |

The preview and installer use the same server-owned definitions and original-byte
serialization. Catalog rows describe templates, not installed or approved private
documents. Any current collection member can preview; only the owner can install.
The reader shows a template explanation only for synthetic provenance with matching
reference, kind, filename and original SHA-256. An unmatched historical/uploaded
source retains its own title and literal paragraphs without substituting catalog
text. Selected evidence repeats the document title, kind and source version.

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

### Private intake derivatives (`source-admission/1`)

Private intake (`intake/*`) reads approved revision projections through
`Library.document`/`revision(..., approved=True)`/`projection` after
`Sources.resolve` (current authority, never the historical `authorize`) to build
identifier-normalized derivatives. The library remains the source authority:
derivatives, inspection receipts and admission decisions are intake records
(`adm_decision` and its private blobs), not library records, and they never
change a document, revision, binding or approval. Unpaginated TXT/MD/HTML
projections are grouped into deterministic logical pages of at most 4 000
characters without splitting a paragraph; citations use those page numbers.

### Transcription revisions (`source-admission/1`, I6b)

`documents.library.publish_transcription(host, scope, decision)` is a trusted
server adapter callable only from `intake.review` (any other caller gets
`PermissionError`). It turns an admitted, reviewer-validated
`diagram-transcription` admission decision into an MD document of kind
`guide-transcription` (`provenance: "intake-transcription"`, `readRoles` copied
from the source audience; a project-wide image asset gives all four roles) with
one revision in status `in_review`. That revision carries the **server-owned**
field `transcriptionOf = {sourceRef, decisionId, decisionRevision, transcription,
visionSha256, normalizedImageHash, region}`, where `sourceRef` is the original
image asset reference, `decisionId` its admitted image decision and
`transcription = {decisionId, decisionRevision, artifactHash}` the
reviewer-validated transcription decision. Both admissions are bound. The public
document API never accepts caller-supplied `provenance` or `transcriptionOf`.

Library approval stays separate: a planner/owner approves the revision through
the existing review route; the intake grant confers no library review.

`Library.revision()` (and therefore `projection()`, paragraph reads, every
download chunk and review approval) rechecks `transcriptionOf` lineage for any
revision that carries it through `intake.admission.verify` of the transcription
decision, which recursively verifies its image decision, and of the image
decision itself. A revoked or changed upstream (either reviewer grant, either
decision, the policy or the image source) returns `409 source-upstream-revoked`;
a revision without the `transcription` binding fails closed. The records that
check read (the image asset, both `adm_decision` records and their
`adm_policy`/`adm_provenance`/`adm_grant` records) are kept in `Library._upstream` as `{owner, kind, id, version}` in
their actual owner partitions and join `checks()`, so `commit()` and
`assert_current()` fence upstream authority atomically with the library write.
`Sources.resolve` propagates the same observations with
`Sources._remember_owned(owner, kind, record)`.

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
| GET `/impact-analyses?cursor=` | the caller's currently authorized `analyses`, optional cursor |
| GET `/impact-analyses/{id}` | `{analysis,result?}`; reauthorize every source before returning any private result |
| POST `/impact-analyses/{id}/decisions` | `{version,nodeId,decision:"change_required"|"unaffected"|"needs_review",note}` → `{analysis,decisions}` |

Worker task `document-analysis` pins original/extraction hashes and approved
revision IDs before model input. Select the regulation explicitly; reuse the
actual GraphStore traversal for candidate assets, recording its backend. The
shared demonstration ontology is not a verified customer graph.
Private analysis uses `impact_of_regulation_id` with the selected node ID and
checks the returned identity. Optional or duplicated display codes may not
select a different regulation; the legacy code-based entry point remains separate.
The list is the caller's own request history. A teammate may open a shared
analysis URL once source bindings exist and every current source-access check
passes. Before binding, only its requester can read the analysis; raw job
records are also requester-only. The generic workspace API has no public job
retry route.
Stale document work and already-failed jobs reconcile their matching pending target to terminal
failure in one version/authority-fenced transaction. Reads and same-request
reposts expose that failure; they do not automatically rerun timed-out work.
An existing non-timeout failure keeps its original job error/category; it is not
relabelled as a timeout when repairing an interrupted target update.
Workers also publish ordinary document failures through this authority-fenced
job/target transaction. If current authority or storage no longer permits that
transaction, no unfenced fallback updates the private target; authorized later
reads reconcile stale work.
Exact request replays resolve their stored identity and frozen model choice
before new-request graph admission. Removing a regulation does not erase an
already recorded terminal outcome.
Completed/approved targets and fresh heartbeats are preserved. An expired,
missing job fails its pending target under a transactional absence check,
without inventing another job record.

At most 20 approved, authorized source documents and 36,000 context characters
enter a model call. Record excluded/unlinked/truncated coverage explicitly.
Required regulation source absent: return `needs_sources` with relationship
candidates and no model call; offer document registration. Exclude missing,
unapproved, inaccessible, malformed and changed sources from prompts. An access
failure never falls back to another user's cache or another document version.
Coverage includes the required regulation and each displayed document candidate
in `sourceResolution`: `selected`, `unavailable`, or `not_checked`.
Unavailable originals share the opaque reason `approved_source_unavailable`;
the result must not reveal denied document IDs, titles, existence or detailed
errors. The library provides details only after its own access checks.
Skipped candidates use `regulation_source_required` or `source_limit`.
`unavailableSources` includes an unavailable required regulation;
`uncheckedSources` counts candidates whose original was not inspected.

Neptune retains bounded queries. When any node or path query reaches its cap,
`ImpactResult.traversal_limit_reached` is true and the result records
`graphTraversalLimited: true`, `graphCountsExact: false`. Counts then describe
only fetched candidates; the UI must warn that the whole impact scope is
unconfirmed. This conservative indicator does not assert an exact omitted total.

The result contains `regulation`, `counts`, `candidates`, `sources`, `evidence`,
`findings`, `summary`, `coverage`, `verification`, `model`, `decisions`.
Prose reference checks apply bounded compatibility/encoding normalization before
matching evidence aliases and platform/supplied node-ID namespaces. Structured
IDs remain exact and original source quotes are never rewritten. This is reference
linkage checking, not semantic classification of arbitrary natural-language terms.
Evidence is `{id:"E1",documentId,revisionId,paragraphId,title,revision,
versionLabel,originalSha256,textHash,quote,page,provenance}`. The UI constructs
same-app links from these fields. Models never supply URLs or storage paths.

Model output is strict JSON `{summary,findings:[{nodeId,reason,citationIds}]}`;
the model is instructed to use `"review"` placeholders for its prose fields.
Reject unknown node/citation IDs and findings without citations. Source integrity
and citation existence are checked separately from a human's content judgment.
The regulation is context only; finding/decision targets come from the candidate
groups. After validation, retain only node IDs and citation IDs. **Never persist
or display the model's summary or reason strings**, regardless of wording.
`materialize_answer` constructs both fields from fixed server review templates
and an integer candidate count; arbitrary model URLs or approval/verification
declarations have no path into those fields. This replaces heuristic expression
filtering with controlled output construction rather than weakening the gate.
Record `verification.outputPolicy: "controlled"` and explain that AI proposes
target/evidence associations while the service supplies review guidance.
The associations still require human content review; existence is not semantic proof.
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
