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
For AgentCore source-analysis completion, reserve at least ten transaction
operations for non-source work: manifest/request marker, terminal ledger job
with inline receipt pointer, artifact completion, required quota/operation
records and control fences. The planner computes the actual complete operation
set; its effective source-check budget is
`min(90, 100 - max(10, nonSourceOperationCount))`. The existing 50-source-binding
limit still applies. Extra execution writes reduce source capacity rather than
raising the transaction limit; preflight rejects oversized scope before paid
execution and rechecks the final operation count before commit.
Admission never trims the requested inputs: reject an over-budget request
unchanged with `execution-completion-scope` and its applicable limits. Only the
requester may submit a new smaller authorized request with a new request ID.
The frozen manifest and canonical input hash derive solely from that accepted
request; rejected requests do not create a partially scoped execution.
If required fences exceed that budget, return `execution-completion-scope`
before submitting the completion transaction. Never omit a condition or shrink
the frozen input scope at finish; a smaller scope needs a newly authorized
request. Graph/artifact/receipt publication remains unchanged. Only a separate
fenced failure/expiry transition may record execution failure metadata.
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

## uxModel property (v1)

`workspace/ontology_ux.py` validates the bounded `properties.uxModel` design
property. It is allowed on `Atom`, `Molecule`, `Organism`, `Pattern`,
`PageTemplate` and `Component`. `Foundation` may carry it restricted to
`layers` and `changeReason`, so every asset level carries the three O-01 layers
(`layers.code` token import, `layers.gui` swatch/icon snapshot,
`layers.wireframe` block ids); Foundation `token`/`name` stay the value source.
`Screen` and `Procedure` reject `uxModel`: screen code is generated and
procedure semantics live in `NEXT` edges.

| Key | Allowed on | Contents |
|---|---|---|
| `intent` | any level | text, at most 500 characters |
| `conditions` | any level | at most 50 `{id, when, effect: include\|exclude\|state, target, state?}`; `target` is a node id; `state` is required iff `effect == "state"`; `when` is `term (" & " term)*` with `term = "!"? "cond:" id` |
| `dataBindings` | any level | at most 50 `{field, source: product\|session\|static, path, required}`; `path` must be in the fixed binding catalog (`ontology_ux.BINDING_PATH`) |
| `requiredStates` | any level | unique subset of `default, empty, error, ineligible, loading, done, zero, many` |
| `stateProps` | any level | `{state: {prop: literal}}`, keys limited to declared `requiredStates` |
| `slots` | PageTemplate only | 1–12 `{slot: {required, allowed: [nodeId]}}`; an empty `allowed` is an error, never "unrestricted" |
| `layers.wireframe` | any level | `{blocks: [id]}` |
| `layers.gui` | any level | `{snapshotHash: sha256, width, height}` (1–4096) |
| `layers.code` | any level | `{importPath, exportName, props: {name: {type, required, values?, item?}}, childrenProp?, adapter?}`; prop types `string, number, boolean, enum, node, list, callback`; `callback` requires a trusted adapter (`controlled-text, controlled-bool, controlled-choice, action`); template slots must be `node` props of the same name |
| `changeReason` | any level | text, at most 500 characters |

Unknown keys fail closed. `ontology_ux.references(value)` enumerates condition
targets and slot `allowed` ids. `publish_candidate` applies
`ontology_ux.rewrite` to `properties.uxModel` with the same partition
`identities` map it uses for `usageIds`/`slots`; ids outside that map are left
unchanged. These references are properties, not edges, so closure does not
follow them.

`PolicyRule` properties also accept `severity` (`critical|major|minor`),
`citation` `{sourceKind: document-revision|product-guideline, page >= 1, quote,
derivativeHash, region?, normalizedImageHash?}`, `extraction` `{method: model,
model, promptVersion, admissionId}` and `appliesWhen` (the `when` grammar;
absent means the rule applies to every case). They keep model provenance even
though the node `provenance` is set by the publishing producer.

Compatible v1 change: canonical JSON nesting is limited to depth 14 (was 8) so
a graph or partition envelope can carry `uxModel.layers.code.props.<name>.values`.
Canonical bytes do not depend on the limit, so existing hashes are unchanged.

## Procedure snapshot (design view)

`Ontology.procedure_snapshot(procedure_id, *, max_screens=20, max_nodes=300,
max_edges=1000)` is a read view for design generation, not approval. It reads
the Procedure, collects member Screens through `PART_OF` edges whose `dst` is
the procedure, keeps `NEXT` edges whose endpoints are both members at their
exact revisions, and runs `closure(direction="dependencies")` over the members.
`uxModel` slot alternatives and condition targets are properties, not edges, so
after the closure it reads missing referenced ids with `read` in batches of at
most 50 and runs `closure` on them in batches of at most 20 seeds. All batches
share the aggregate node/edge budgets and must observe the same generation; it
repeats for at most four rounds. It applies the same visibility, exact-revision
and final `_recheck` rules as `closure`; tombstoned, rejected and deprecated
nodes and edges are excluded.

The result is `{schemaVersion, projectId, generation, nodes, edges, coverage}`
with scope `authorized-procedure-snapshot` and `complete: false`.
`coverage.unknown` is the union of the closure reasons and its own:
`too-many-screens`, `unmapped-or-inaccessible` (including an unreadable
`uxModel` reference), `stale-endpoint-revisions` and
`retired-or-rejected-mapping` (a member Screen or dependency that was rejected
or retired). `coverage.truncated` is true when any limit is hit.

Design views accept unreviewed edges between approved nodes; rejected/deprecated
edges are excluded. The ontology has no edge review step, so edge usability is:
not tombstoned, `reviewState` not `rejected`/`deprecated`, and both endpoints
live (approved, or reviewed/candidate when candidates are explicitly included)
at the exact revisions the edge names. `design_loop.knowledge.from_snapshot`
reports a non-live dependency as `unapproved-dependency` and a rejected or
retired one as `retired-or-rejected-mapping`; both block `Knowledge.complete`,
as do truncation, unresolved `uxModel` references and `ambiguous-entry`.

Schema amendment (v1 compatible): `Procedure` properties accept
`entryScreenId`, rewritten through the publication `identities` map like
`slots`. `NEXT` edge properties accept `navigation: forward|back|cancel`
(default `forward`). The entry is `entryScreenId` when present (it must be a
member); otherwise the unique member with no incoming forward edge. Zero or
several candidates yield `ambiguous-entry`. Back and cancel edges are user
actions and do not participate in entry detection or forward ordering.

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

The v1 schema reserves `published-asset`, `ux-contract` and `run-round` source
kinds for later authority adapters. Until those adapters are installed, reads fail unavailable.
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

For legacy import, omitted or `null` `nodeIds` uses the existing bounded import
without an explicit node selection. A non-null value must be an array of 1–20
entries; an empty or oversized array fails with `422 legacy-import-scope`.
The existing source-index, owner and source-access prerequisites still apply.

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
AgentCore source analysis is a separate deployment milestone using Runtime and
the new `execution_ledger.py` protocol. The legacy `ontology_jobs.process`
path remains offline-only; do not install a cloud analyzer in that legacy
writer. C dispatches newly admitted AgentCore analyses through the new ledger,
with source/artifact/graph publication retaining the same atomic fences.
For those jobs, `ontology.publish` stages only. `execution.finish` combines the
ledger's conditional completion writes with ontology/artifact/request-marker
publication using the trusted completion-write composition point and one
transaction. Receipt pointers are inline in the job/artifact records. No
independent graph publication or later terminal-state update is permitted.
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
and verifies its input/code/lock hashes. The B adapter PR provides the trusted
Runtime/Interpreter transport; C connects it through mutually exclusive new-ledger
routing. B0 first adds and tests conditional discriminator guards in the actual
legacy claim/16-minute expiry paths and new-ledger paths, before any shared-table
probe records. It retires obsolete cloud-factory comments in `ontology_jobs.py`.
C owns live application routing and repeats the already implemented exclusion
tests against the deployed paths. Source-analysis rollback blocks new production
analyses until a verified backend is restored; the offline adapter is not a
production rollback target.
A callable's backend label is insufficient authorization or execution evidence.

B0 also guards artifact-linked repair and read reconciliation, including
`_mark_failed`: new artifacts retain an immutable execution discriminator and
job/execution linkage. A marked artifact, or an artifact linked to a new/reserved
job, is never failed/completed by legacy repair, even if the job is missing or
uses a new state such as `dispatched`/`recovery_required`. Unknown linkage is
reported for the new reconciler without changing completion state.

## Workbench compatibility

The legacy-shaped change API expresses changes to selected current nodes.
Its free-text `before`/`after` fields are annotations, not source revision IDs.
Exact old/new source changes use `POST /ontology/impact`, which binds those
references and selects retained source snapshots. The workbench API does not
infer source revisions from free text.

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
