# Canonical project ontology: implementation contract v1

This module implements the offline/data foundation of
[the AgentCore plan](../docs/ONTOLOGY_AGENTCORE_PLAN.md). It does not establish
AgentCore service readiness. The production defaults remain the existing
execution backend and `PROJECT_ONTOLOGY_MODE=legacy` until the separate cutover.

## Authority and storage

`workspace/ontology_schema.py` is the closed validator and canonical serializer.
The workspace `ontology` record kind is authoritative. `project-current` contains
the project manifest and immutable partition/index hashes. Source partitions,
node/source/adjacency/edge index shards and their hashes are written before a
single DynamoDB conditional publication transaction.

`Ontology` uses the existing `Service`/`Collaboration` project-membership fence.
Publication adds exact source-record checks; concurrent source, project or
manifest changes cancel the transaction. Failed immutable writes cannot advance
the manifest. Per-partition limits are 500 nodes/1,000 edges, with up to 1,000
partitions and bounded sharded indexes. Each immutable S3 partition/index object is
limited to 4,000,000 serialized bytes. Index keys are assigned to 256 hash buckets;
a key's entries stay in one bucket. Concentrated source references or adjacency
can exhaust a bucket before the partition-count limit. Such publication fails
without advancing the manifest and requires index-capacity expansion; splitting
the same hot key across source partitions does not expand that bucket.
The first reached limit governs publication.
There is no global 500-node storage claim.
Read pages and dependency closures remain bounded and disclose incomplete scope.
Source verification admits at most 90 distinct authority records; publication
also checks the complete DynamoDB transaction against its 100-operation limit.
Oversized operations fail with a split-scope error before publication.
Each publication accepts at most 50 distinct source authority bindings: source
kind/ID, exact revision/hash and audience, plus a workbench document ID where
applicable. Location-only annotations within that same source share a binding
and do not consume additional source-authority slots. Read snapshots
use a separate 2,500-record verification budget and perform their final version
recheck once, rather than inheriting the atomic-write limit.

Canonical IDs are allocated within a partition namespace. Input-local IDs are
retained as aliases, not trusted as another partition's ownership. Node revision
identifies the source/mapping revision. Review and approval retain that revision
and change the full node hash, audit record and manifest generation, so routine
review does not break incident relations. Mapping edits and deprecation advance
the revision; changed relations require republication for current context, while
impact reports stale witnesses.
Removed nodes/edges remain tombstones within the bounded partition; reintroduction
advances the existing identity's revision rather than restarting at one.
Pattern approval stores exact usage-screen revision/hash bindings and explicit
evidence relationships. Current reads and context reuse require those screens
and their source audiences to remain current; a later rejection, revision or
source change invalidates that approval basis.
Partition replacement preserves server-owned usage proofs for an unchanged,
still-valid approved Pattern, even when the caller omits those proof edges.
Changing a usage basis returns the Pattern to candidate and retires its proofs.
Existing product-guideline
projection IDs and hashes are preserved; canonical publication adds a project
manifest in the same product-publication transaction.

`COMPOSES` spans strictly descending visual levels from Screen to Atom.
Foundation resources use `USES`, including Atom → Foundation/Icon.
`NEXT`, `PART_OF`, provenance and ownership are not substitute dependency edges.
`ontology_impact.py` performs reverse traversal with source-revision seeds,
witness paths, candidate/approved-declared/observed evidence, and inaccessible
boundary handling. It never certifies dependencies outside the inspected snapshot.
Historical impact retains source-to-node indexes and stale endpoint witnesses.
When publication retires a source binding, its old complete manifest is retained
as an immutable snapshot with the affected seed IDs. Old-source impact uses those
snapshots rather than attributing the old source to replacement nodes. Every
snapshot read applies current source access checks; results name their snapshot
generation and remain historical candidates. Current and historical views share
the 500-node/1,000-edge inspection and 50-item budgets, with at most 20 history
snapshots per request and explicit truncation.
Repeated impact evidence has a 3,500,000-byte aggregate response budget.
Overflow fails with `ontology-impact-scope` and a split-scope instruction;
evidence is never silently discarded. Caller-provided task completion evidence
is limited to 50 references regardless of graph authority.
Change, task and report records also check the smaller metadata storage limit
before publication, with space reserved for subsequent approval metadata.
Rejected mappings remain visible for review but cannot supply dependency closure,
generation context or impact traversal. Starting or completing a work item
requires current source and impact evidence; historical diagnostics alone do not
permit these transitions.
Archived readable sources remain diagnostic candidates. Revoked source metadata
is hidden; an opaque changed seed can expose only independently readable dependent
nodes/evidence and reports a restricted boundary. Context/reuse/approval still
require current sources.

## Source authority

`ontology_sources.py` resolves assets, current published product guidelines,
workbench documents, approved document-library revisions and the real platform
component package through their existing authorities.

It checks original byte identity, current source status, source audience and
project membership. Document-library ACL/revision checks remain in `Library`;
workbench expiry/generation checks remain in the workbench source adapter.
Authority is frozen during a read, and all observed record versions are rechecked.
Unknown source adapters fail closed; they are not generic ID lookups.
The frozen authority is actor, project, role, membership and project lifecycle;
unrelated collaboration writes do not revoke it. The commit still fences the
latest project record and every observed source. Bounded project-CAS retries
reuse already computed analysis; changed membership, sources or graph generation
fail instead of running the analyzer again.
Membership changes alter the project audience and invalidate in-flight source
authority, even when the requesting actor's own role is unchanged. Title/content
writes that preserve that audience do not invalidate it.
Storage maintains a monotonic `authorityRevision` for membership/role and project
lifecycle changes. Restoring old membership contents does not restore an earlier
in-flight authority epoch. Title/comment writes leave that epoch unchanged.
Queued analyses pin this authority digest at admission and compare it before
source analysis, in addition to the checks during publication.
Deadline-sensitive service commits submit a transaction once. Contention returns
to the authorized caller, which repeats source and actor deadline checks before
any retry; storage cannot silently resubmit that transaction after expiry.
Visibility checks are memoized by source authority within one request. The
final authority, source-version and expiry checks are always repeated before
returning data or committing a mutation.
Opaque pagination cursors use the separate `ontology_cursor` record kind,
with an application expiry and DynamoDB TTL of at most five minutes.

The v1 schema reserves publication, UX-contract and run-round reference kinds
for later adapters. Until those adapters are installed, reads fail unavailable.
This is explicit staging, not completed sharing or release integration.

## APIs

All endpoints use the existing `/studio-api` JWT authorizer and
`X-Workspace-Project` membership checks. Body fields never select actor or owner.
Current owner, planner, designer and developer members may read context/impact
and submit candidate mappings or source analysis. Replacing an existing
partition additionally requires its creator or the current project owner.
Review/approval uses the narrower type-specific roles below. Legacy import is
owner-only. Analyzer availability and source-authority checks precede dispatch.

| Endpoint | Behavior |
|---|---|
| `GET /ontology/schema` | Declared types, eight levels and analyzer configuration state |
| `GET /ontology?limit=&cursor=` | Authorized page; opaque actor/role/project/generation-bound cursor |
| `GET /ontology/nodes/:id` | Readable exact node; hidden/missing nodes are not disclosed |
| `POST /ontology/partitions` | `{requestId,name,graph,expectedGeneration}` creates/replaces a source-bound candidate partition |
| `POST /ontology/nodes/:id/review` | `{requestId,revision,expectedGeneration,decision,reason}` records an authorized human decision |
| `POST /ontology/context` | `{nodeIds}` returns a bounded, source-bound context fingerprint |
| `POST /ontology/impact` | `{changeId,kind,expectedGeneration,oldSource?,newSource?,nodeIds?}` returns authorized reverse paths |
| `POST /ontology/import-workbench` | Owner-only, explicit legacy migration with source and legacy-index fences; optional `nodeIds` selects 1–20 nodes into a stable separate partition |
| `POST /ontology/analyses` | `{requestId,name,files:[{assetId,path}],resolverProfileId?,expectedGeneration?}` queues source analysis |
| `GET /ontology/analyses` | Source-authorized, paginated accepted analysis metadata, including its original request/artifact/job IDs |
| `GET /ontology/analyses/:id` | Authorized analysis artifact, coverage and actual execution receipt |

Manual candidate writes cannot claim parser provenance or approval. Only the
trusted analysis path records parser-extracted provenance and initially sets
`reviewState=candidate`. Authorized human review advances it to `reviewed`, then
approval can advance that exact revision to `approved`.
Review roles are owner/planner for business nodes and procedures, owner/developer
for code/test nodes, owner/designer for design nodes, and owner for Team metadata.
Approval requires a reviewed exact revision. Product/policy approval additionally
requires approved/published business sources. Managed product partitions are
changed through product publication, not manual graph edits.

## Analyzer and execution staging

`platform/source-analyzer` pins TypeScript, PostCSS and HTML parsers. It parses
code and HTML without executing input modules, hooks or scripts. A virtual
TypeScript host prevents filesystem/module resolution outside the provided
manifest and preserves lexical import binding.

Analysis records literal imports, re-exports, JSX references, CSS/HTML resources
and explicitly configured JSON reference fields. Aliases/package identities are
server-owned resolver profiles. Nonliteral dynamic targets, ambiguous paths,
unconfigured browser URL bases, inline HTML script/style behavior and unsupported
transforms stay unresolved. Input bytes, resolver profile and result are hashed.
Literal `import()`/CommonJS targets retain possible static dependencies with
`conditional-import` observations and unresolved runtime-loading semantics.
They do not certify runtime execution or promote a mapping to approval.
Static manifest coverage is distinct from runtime completeness.

`ontology_jobs.py` uses existing durable jobs and rechecks actor expiry, source
revisions and permissions before analysis and publication. A missing configured
analyzer blocks submission/execution. `local_analyze` is an explicit offline test
adapter and requires a separate test opt-in; production cannot silently use it.
The host must set `allow_offline_ontology_analysis=True` and supply the exact
`local_analyze` function; the default is disabled. An omitted analysis
`expectedGeneration` pins the current authorized manifest at admission, while an
explicit value (including `null`) must match it.
The trusted adapter identity and publication role/partition ownership are checked
before invocation. Terminal durable-job state, artifact completion, execution
receipt pointers, the partition and request marker commit atomically; an artifact
or job conflict publishes none of them. The worker does not append a separate
terminal-state write after this publication.
The AgentCore adapter is a separate deployment milestone.
Manual partitions always retain incomplete, declared coverage. Source deadlines
are rechecked immediately before submitting the version-fenced transaction;
source validity is never extended by publication. Reads, reuse and approval
independently reject expired sources, including expiry after that observation.
The existing `Worker.handle` atomically claims a queued durable job before
dispatching `ontology_jobs.process`; duplicate deliveries cannot enter the analyzer.
The existing 16-minute stale-job check also reconciles the linked workbench
artifact. Artifact reads reconcile interrupted dispatches with no job record.
An already-timeout-failed job retries its linked artifact repair on later reads,
including an interruption between the job and artifact transitions.
Recovery records failure and requires a new authorized request; it never repeats
paid execution under an old authorization. Retrying an unexpired dispatch request
can finish creating its missing job.
The UI records an accepted artifact in its project-scoped URL before waiting.
Returning to that URL or selecting a saved analysis resumes reads/polling of the
existing job. Project changes clear the URL selection; the authorized analysis
list remains available for recovery without another POST.
This foundation accepts only the exact offline adapter with explicit test opt-in
and verifies its input/code/lock hashes. Cloud adapter installation belongs to
the separate adapter PR; a callable's backend label is insufficient.

## Workbench compatibility

In `legacy` mode, existing workbench graph behavior remains. In `canonical` mode,
the workbench graph is a projection of the workspace manifest and retains
`canonicalType`/`canonicalRelation` plus exact source references.
Legacy dependency mutation is rejected rather than creating a competing graph.
Explicit import preserves the original workbench source/index and creates
candidate canonical mappings. Current-source changes invalidate stale imported
references; source reindex/import must complete before they become usable again.

Canonical-mode impact records name their graph authority, use that manifest
for freshness and preserve all source references. Existing bank local/Neptune
graph and Gateway are not merged or reconfigured by this module.
Each impact item keeps a representative display path and the union of node/edge
evidence across converging paths in the inspected result subgraph. Report and task
checks retain that complete evidence union; truncation remains explicit.
Legacy evidence APIs cannot choose canonical resolution through caller fields.
Impact receipts bind the requested change kind, selection and old/new source
references through hashes. New sources require current authority even when no
dependent nodes are found. Historical opaque source metadata stays hidden.
Incomplete or conflicting legacy snapshots cannot replace a prior import.
Owners can split imports with explicit node selections; each selection has its
own stable partition and reports dependencies crossing the selected scope.
Canonical impacts/tasks/reports carry their server-selected authority through
read and completion. Import combines parallel relationship evidence, preserving
all references in bounded chunks, and reports unsupported legacy shapes.

## Verification

```bash
cd platform/source-analyzer
npm ci --ignore-scripts
npm test
cd ../..
python -m pytest platform/tests/test_ontology_*.py -q
```

Tests exercise actual parsing, shadowed names, unresolved/dynamic references,
source hashes, namespace isolation, atomic publication failures, revocation,
human review, current product publication, opaque cursors and workbench projection.
Offline analyzer tests run real Node code with no inherited AWS credentials.
CI installs the pinned analyzer before Python integration tests and runs its
own Node boundary suite. AgentCore, sharing, generation/release context binding
and deployment acceptance remain separate required implementation work.
