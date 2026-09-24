# Ontology AgentCore execution contract v1

SPEC §7-2 adopts this contract for the staged ontology extension. These are
requirements, not a deployment inventory or evidence that every stage is complete.
The current data/API implementation is scoped by [ONTOLOGY_CONTRACT.md](ONTOLOGY_CONTRACT.md);
source, tests and current deployment receipts establish actual behavior.
An absent requirement remains a gap. Production admission requires the applicable
gates below. The dated implementation plan records sequencing and review history;
changing that plan does not change this contract.

The [cross-module architecture](../docs/ARCHITECTURE.md) assigns implementation
ownership and delivery order for this contract. Its offline protocol unit
precedes service activation; the capability gates and A/B/C boundaries below
continue to govern integration and admission.

### platform-ontology/1

`Foundation` is one level; `Icon` is its resource subtype. The other seven
levels are `Atom`, `Molecule`, `Organism`, `Pattern`, `PageTemplate`, `Screen`
and `Procedure`. Procedure is a workflow level, not a larger visual component.
Existing `Component` IDs remain compatibility aliases with explicit level
mappings. Unmapped components remain unclassified.

A node contains `id`, `scope`, `type`, `subtype?`, `revision`, `contentHash`,
`sourceRefs`, `provenance`, `reviewState` and `tombstone`. Scope is either
`{kind:project, projectId}` or `{kind:published, publicationId}`.
An edge contains `id`, `type`, exact source/target node IDs and revisions,
`sourceRefs`, `provenance`, `reviewState` and `tombstone`.

A source reference contains `sourceKind`, `sourceId`, `revision`, `sha256`,
`audienceRevision` and a location when applicable: document page, source
path/export/line, or image region. Implemented source-kind identifiers are
`asset`, `document-revision`, `product-guideline`, `workbench-document` and
`package`. Imported code revisions use `asset` with an exact import revision,
byte hash and file location. `published-asset`, `ux-contract` and `run-round`
are reserved for their separately installed authority adapters and fail closed
until those adapters exist.
IDs are stable within a scope; revision hashes identify immutable content.
Original IDs live in namespace mappings. Ambiguity cannot silently select
the first match.

| Edge | Direction and constraint |
|---|---|
| `COMPOSES` | Strictly lower visual levels from Screen through Atom; level skipping is permitted. Foundation dependencies use `USES`, never `COMPOSES`. |
| `USES` | Any design level, Product, Procedure or code → referenced resource. Atom → Foundation/Icon, Procedure → Screen and Product → Procedure are valid dependencies. |
| `IMPLEMENTS` | Code file/export → design element or Screen; many-to-many is permitted. |
| `IMPORTS`, `REFERENCES` | Code → module/resource. Record cycles and visit them once; these are not visual composition. |
| `GOVERNED_BY` | Product/Screen/Procedure/design usage → exact rule revision. |
| `PART_OF` | Screen → Procedure, Procedure → Product; many-to-many membership. |
| `NEXT` | Screen → Screen within a Procedure, with condition ID and retained-state specification. Loops/back navigation are permitted. |
| `DERIVED_FROM` | Resource/relation → source evidence; provenance, not impact propagation. |
| `OWNED_BY` | Resource → responsible team; terminal assignment metadata, not a dependency. |

Dependency edges run from dependent to prerequisite; impact walks the reverse.
Procedure order alone does not establish a code/policy dependency.
Level-invalid extracted edges remain candidate findings with source locations,
and cannot become approved composition.

| Level | Classification and existing-vocabulary mapping |
|---|---|
| Foundation | Non-interactive color/typography/spacing/grid/elevation/icon/graphic/motion resources. Existing token/icon records map by reviewed subtype; raw SVG is Foundation/Icon. |
| Atom | Small reusable control/display unit without a business sequence. Existing Component may map here only after review; a React Icon wrapper is an Atom. |
| Molecule | Small composition serving one local interaction, such as labelled input/help/error. Existing SM `molecules` are source candidates with their SM ID/path retained. |
| Organism | Bounded task section composed of smaller units. Existing SM `organisms` are source candidates, not automatically approved implementations. |
| Pattern | Reusable interaction/composition contract with at least two independently reviewed usages. Existing graph/Registry Pattern IDs remain aliases. |
| PageTemplate | Product-independent slot/region contract. Do not create one merely because two screenshots look similar. |
| Screen | Concrete route/state identity. Existing Screen/ScreenMeta IDs map explicitly and retain their separate specification/implementation meaning. |
| Procedure | Ordered/conditional screen interaction including back/cancel behavior; existing Procedure/Flow vocabulary maps by reviewed semantics. |

Ambiguous classifications and same-level visual nesting are quarantined until
the owner chooses a lower-level decomposition or justified non-composition
`USES` relation. Model confidence does not approve a mapping. Preserve old IDs,
record exact source→canonical mappings and reject collisions; unclassified
legacy records remain readable without claiming a validated eight-level mapping.

Provenance values are `parser-extracted`, `declared`, `model-inferred` and
`verified-build`; these are evidence methods, not approval states.
The review lifecycle is `candidate → reviewed → approved → deprecated`, with
rejection and supersession recorded. Only an authorized human advances design
or policy approval. A structurally confirmed import may still refer to a
candidate design element.

Schema migration requires explicit version mapping, collision checks and
compatibility tests. Tombstoning creates a new revision and immediately
excludes the resource from new use. Historical evidence is not rewritten.

### Shared-publication authority

Cross-team work in one project uses current membership and source audiences.
Cross-project reuse requires an exact-revision publication and grant.

The source owner proposes publication. A designated design-system publisher
approves design publications; the responsible policy owner approves policies.
A destination project owner accepts a grant naming publication revisions and
permitted roles. These are explicit application capabilities, never permissions
inferred from an agent/service identity.

Effective access intersects current membership, publication grant and upstream
source policy. Grants cannot widen a restricted upstream audience. Organization
publication requires a separately authorized source-policy change; ordinary
uploads or project ownership do not create it. A new revision needs a new grant
binding or explicit renewal. Withdrawal immediately invalidates downstream reuse.

Impact crosses shared boundaries only through authorized references. Return
only authorized dependents and evidence; do not disclose hidden IDs, names or
exact counts. Coverage says `restricted-or-unmapped`, never “no impact.”
Internal routing may create work in the affected project's authorized worklist
without exposing that project to the initiating caller.

Project roles remain the authoritative owner/planner/designer/developer roles.
Organization-level `design_publish` and `policy_publish` capabilities are
separate grants assigned through IAM-only administration; project owners cannot
self-grant them. Origin source authority is also required. Destination acceptance
requires destination ownership. Explicit deny, upstream restriction, expiry and
revocation override allows.

Withdrawal is prospective enforcement, not recall of delivered bytes. At the
next access/stage boundary, fence affected attempts, quarantine derivatives,
invalidate effective approval/readiness and deny new retrieval/download/export.
Stop active services best-effort; already-sent model input cannot be unsent.
Late output is non-current. Preserve historical approval metadata marked stale
or withdrawn. Derivative lineage includes every supplied source/model input;
an LLM claim that a source was unused cannot remove it.

Downloaded ZIPs and exported Git commits cannot be remotely recalled. Record
distribution/recall-needed events and scoped remediation work; do not force
rewrite Git history or claim remote deletion. Current access remains blocked
even if asynchronous deletion or recipient remediation is incomplete.

### source-analysis/1

Static analysis runs in Code Interpreter using the pinned **TypeScript compiler
API parser** in a trusted Node tool that returns bounded JSON observations.
Python regular expressions do not replace TS/TSX parsing.
Local execution is an offline test adapter. The analysis unit is a collection
root plus an explicit file manifest and resolver configuration, not a basename.

| Input | Supported observation / boundary |
|---|---|
| TS/TSX/JS/JSX | Parse imports, re-exports, exported symbols, JSX usage and literal asset references. Parse errors block complete coverage. |
| Relative modules | Resolve against the collection manifest; reject path escape and ambiguous extension/case/Unicode matches. |
| Aliases/packages | Use an approved versioned alias/workspace/package-exports map; never execute configuration or invent an unsupplied SDK. |
| CSS/SCSS | Parse literal imports and `url()` references with locations. SCSS evaluation, interpolation and loader execution remain unresolved. |
| HTML | Parse static structure and literal resource references with source locations. Inline scripts/styles, active behavior and unconfigured URL bases remain unresolved; do not execute or fetch them. |
| JSON | Parse approved manifest reference fields; arbitrary strings are not assumed to be paths. |
| Generated files | Record generator/tool version and source mapping; missing mappings stay unknown. |
| Dynamic imports/URLs and framework transforms | Preserve unresolved references; no automatic network/package lookup. |

Initial budgets: 100 text files and 2 MiB per unit, 100 KiB per text file,
60 seconds of parser execution. Partition larger corpora with a collection
manifest and continuation cursor. Cross-unit resolution uses that manifest;
a completed unit is not a completed repository analysis.

The image comparison unit is an original asset revision or an explicitly
located region. Return byte hash, dimensions, format, coordinates and source
location. Exact byte matches are structural evidence. Visual/semantic similarity
is a candidate with method/model/version recorded; no numeric threshold
automatically approves a relation in version 1. A reviewer confirms the canonical
target and meaning.

Impact uses breadth-first reverse traversal, deduplicates scoped node/revision
and edge IDs, and returns witness paths. Limits are 500 visited nodes, 1,000 edges,
12 dependency hops and 50 actionable work items per invocation. Report all
limiting reasons. Missing collections, dynamic references, parse errors,
inaccessible boundaries and unreviewed mappings prevent a complete-impact claim.
Limits change only through a reviewed contract/configuration revision.

A change set fixes its ID/scope/kind, old exact source reference, new reference
or tombstone, lineage ID, requested scope and base manifest generation.
Cross-ID replacement/rename requires a reviewed lineage mapping.
Analyze one authorized snapshot: first seed canonical nodes whose `sourceRefs`
match the old source revision/region or lineage. This reverse provenance-index
lookup is the seed step; `DERIVED_FROM` itself is not a propagation edge.

| Change | Propagation |
|---|---|
| Image/token/design/code/kit | Reverse `USES`, `COMPOSES`, `IMPORTS`, `REFERENCES`, `IMPLEMENTS` |
| Rule/product condition | Reverse `GOVERNED_BY`, then structural dependencies above |
| Procedure transition | Seed Procedure and changed Screen endpoints, then structural dependencies; other member screens without a proven edge are potential revalidation candidates |
| Permission/grant/tombstone | Invalidate descendants through persisted lineage; filter the reported impact by current authorization |

Procedure→Screen and Product→Procedure `USES` edges are necessary to claim
those dependency paths; `PART_OF` or screen order alone cannot establish them.
Traverse candidate edges separately and label paths containing them potential.
Distinguish observed structural, approved declared, candidate and unknown
evidence. Candidate tasks request investigation, not a confirmed code change.
Changed manifest/source/audience fences make analysis stale before mutation
or approval; never merge edges from different generations into one result.

Code analysis is a durable ingestion job under the same concurrency/budget
policy. Original storage may complete while analysis waits. Interpreter failure
leaves the original stored but analysis failed/unavailable, not indexed/approved.
Report measured usage per job instead of assuming a fixed upload cost.

### execution-capability/1 and Identity topology

The authenticated API admits a job after checking user identity, membership and
operation scope. The dispatcher obtains a short-lived signed execution capability
from the platform authorization service. Use a dedicated asymmetric signing key:
only the IAM-only dispatcher requests capability signing for an allocated
attempt; the user-facing API admits/cancels jobs but never requests signing.
Runtime/Gateway/tools can
verify but cannot mint capabilities.

Required claims are issuer, audience, verified actor reference, project ID,
execution ID, attempt ID, Runtime session ID, workload identity, permitted
operations/resource references, authorization expiry, issue time, expiry and
unique capability ID. Audience is the platform Lambda MCP boundary. Expiry is
no later than user authorization expiry or execution deadline and never more
than 15 minutes after issue. Renewal requires fresh backend authorization.

Deliver the capability in the authenticated Runtime invocation. Store only its
digest and validation claims in the job ledger, not the bearer value.
Trusted Runtime code inserts it in a reserved MCP authorization envelope.
The Gateway wire schema explicitly declares required
`_executionAuthorization: {capability, operationId}`. Runtime creates a separate
model-visible schema that removes the reserved property and required entry,
rejects any model-supplied reserved field, and inserts the capability itself
immediately before the Gateway call. Gateway therefore validates a declared
wire field; Lambda verifies its signature and ledger binding rather than a
caller-supplied trusted marker. Raw discovery may expose the envelope's schema,
never a token value. Model-visible discovery, prompts, Memory, logs and artifacts
contain neither the envelope values nor credentials.
Phase 0 must prove this schema filtering and canary-token log exclusion.

Gateway validates the workload credential. Lambda independently validates the
capability signature, issuer, audience, expiry, workload binding and current
attempt against the ledger, then rechecks membership, resource audience and
operation scope. Capability A cannot access execution/resource B even with the
same machine credential. An execution ID is not authorization.

Generate execution/attempt IDs with at least 128 bits of randomness and bind
`runtimeSessionId` in both the signed capability and the ledger. Validate Gateway,
target and tool identifiers supplied in Lambda context against configuration.
Do not assume a `bedrockAgentCoreSessionId` field is available or equal to the
Runtime session ID: the documented Lambda context does not guarantee it.
If a supported service session field is observed in Phase 0, it is supplementary
binding evidence, not a replacement for the signed capability. Expiry,
cancellation, revocation and attempt replacement reject further protected work.

Runtime has no unrestricted canonical source/ontology-store access; data
operations use scoped tools. Mutations carry a deterministic operation ID and
request digest. Identical retry returns the durable result; the same ID with
different content is a conflict. Cancelled, expired, revoked or superseded
attempts cannot mutate state. Read retry still requires current access.
Operators do not gain a general application source-read bypass.

The selected credential topology is:

```text
Verified application actor → Runtime workload identity
  → Identity workload token → configured OAuth2 machine-credential provider
  → Gateway JWT authorizer → Gateway IAM execution role → Lambda MCP
```

Use a dedicated machine client/issuer and Gateway audience/scope, separate from
interactive application login. The provider credential authorizes the workload;
the execution capability restricts project/user/operation. Do not assume the
Identity workload token itself is a Gateway bearer token.
Phase 0 must prove the broker/provider/Gateway chain and swapped-capability
rejection. Unsupported topology blocks integration until plan revision/review;
do not substitute unauthenticated access, an unused token-mint call or a silent
IAM-only path.

Select a dedicated Cognito machine app client with client-credentials and a
dedicated tool scope as the OAuth issuer profile. Identity brokers that client
credential; Gateway validates its configured issuer, allowed machine client and
scope, plus audience when present in the verified token profile. This applies
only to the new ontology Gateway, never the existing IAM bank Gateway.

The capability issuer is a new IAM-only `execution-authority` backend module.
It derives claims from an admitted job and current authorization, never arbitrary
Runtime arguments. A managed asymmetric KMS key signs capabilities; only the
dispatcher role may request signing after attempt allocation. Runtime evidence uses a separate key
and cannot mint capabilities. A versioned key registry fixes algorithm, key ID,
purpose, public-key fingerprint, validity and active/retired/revoked state.
Verifiers obtain keys only from configured KMS/registry entries, never token URLs.
Rotation stops old-key signing and retains verification through token expiry.
Authoritative key-revocation state is checked at protected operations; a cached
public key is not cached authorization. Compromised keys invalidate current
attempt/evidence readiness. Routine retired keys retain historical verification.
V1 has no in-session capability renewal: require a freshly authorized attempt.

### ontology-tools/1

Schemas reject unknown fields. Business arguments below exclude the reserved
authorization envelope; the model sees only authorized read tools.

| Tool | Input / output | Scope |
|---|---|---|
| `ontology.context` | Selected IDs/cursor → nodes, edges, source refs, context hash | Read; ≤50 nodes/100 edges per page |
| `ontology.source` | Exact source ref/range → bytes or text, hash and range metadata | Read; ≤256 KiB, no arbitrary URL |
| `ontology.impact` | Change ID/expected graph revision → witness paths, coverage and existing authorized work-item refs | Model-visible read-only analysis; no work-item creation |
| `ontology.publish` | Candidate artifact ref/hash/expected generation → immutable staged publication-plan ref/hash; no current manifest update | Trusted ingestion staging only; not model-visible |
| `execution.stage` | Attempt/stage/operation ID/receipt ref → transition | Trusted Runtime control only |
| `execution.finish` | Final manifest/staged publication-plan refs and hashes/expected ledger version → one coupled completion | Trusted Runtime; independent receipt and transaction validation |

Responses are ≤512 KiB. Cursors bind actor, project, resource selection, graph
generation and audience revisions. Authority/generation changes invalidate them.
A job has at most 60 tool calls and 4 MiB retrieved text, also subject to existing
smaller model-context limits. Errors disclose no unauthorized resource details
or upstream response bodies. Models cannot select endpoints, credential providers,
accounts or roles.

For source-analysis executions, `ontology.publish` stages immutable objects and
a bounded publication plan; it cannot advance current graph/artifact/job state.
`execution.finish` is the only publication/completion operation. The ledger
prepares conditional terminal-job and related execution writes/checks without
committing independently. The ontology coordinator combines them with its
manifest/request-marker and artifact writes and current source/authority checks
in one `Storage.put_many`/`TransactWriteItems` call. This extends the existing
trusted `_completion_writes` composition point; it does not introduce a second
writer. The validated receipt pointer is stored in the terminal job/artifact
records. A failed transaction advances none of those pointers or states.
Human mapping/publication APIs retain their own existing transactions.
Work-item creation/routing is performed only by the authorized IAM-only routing
worker, not by a model-visible `ontology.impact` call.

The trusted Lambda completion facade submits the coupled transaction once per
attempted completion. Proven transaction contention permits at most two retries
within the same execution deadline, with fresh actor/source/deadline/attempt
checks before each submission and no SDK-level hidden retry. Changed predicates
are not refreshed away. Exhausted proven contention fails completion with
`completion-contention` through a fenced metadata failure transition; a
cancelled/expired/superseded state is preserved. Unknown transport outcome first
reconciles durable job/artifact/request markers and otherwise enters bounded
`recovery_required`; it is not presumed to be a failed or successful commit.

Authority-only changes increment membership/grant/audience versions even when
graph content does not change. Cursors bind those versions independently from
manifest generation. Thus revocation does not wait for content reindexing.

### Memory retention and authorization lifecycle

Memory actor namespaces are keyed pseudonyms derived from organization, project
and verified actor using a managed HMAC key; session ID is execution ID.
No implicit cross-project/actor retrieval is permitted. Events contain schema
version, approved decision kind, resource/version refs, outcome and timestamps.
No free-form source documents, prompts, credentials or identity originals.

Initial event TTL is 30 days, configurable downward. Longer retention requires
operator policy approval. Revocation immediately blocks application retrieval
and queues deletion of affected Memory events/sensitive derivatives. Audit
deletion retries/completion. A tombstone may retain non-content audit hashes
under the configured policy; there is no historical-content bypass in this
version. Cleanup failure does not restore access.

At ingestion, context retrieval, compilation dispatch, review, copy/download,
release and Git export, check current membership and effective source audience.
Generation, approval, release and export additionally check exact relevant
source/kit/rule/graph-context/evidence revisions. Membership/source revocation,
tombstoning, grant withdrawal and relevant dependency/rule changes invalidate
current readiness. Keep historical approval metadata without allowing stale
or inaccessible content to be reused/downloaded. Reproducibility does not
override revocation; audit metadata and source access are separate permissions.

### Atomic publication and reproducible evidence

Write immutable source/graph/evidence objects before publishing a pointer.
Publish a manifest generation in one compare-and-swap transaction that checks
current authorization, source audience/revision fences and previous manifest
version. Failed publication leaves unreferenced staging objects for cleanup.
Readers never combine halves of separate generations.

Source/bundle hashes are SHA-256 over canonical UTF-8 JSON of sorted
`{path, sha256}` entries, hashing actual file bytes. Reject unsafe/ambiguous
paths and retain original paths as provenance. ZIP entries have sorted paths,
fixed timestamps/modes, no host paths or generated timestamps. Record ZIP byte
hash separately. Receipt timestamps are outside deterministic content hashes.

Receipts contain schema version; execution/attempt/stage IDs; actor/project
references; source/context hashes; model ID; compiler/tool-archive/runtime profile;
Browser/verifier profile; service/session identifiers; result hashes/status;
timings/usage; and previous-receipt hash. Trusted Runtime evidence uses a signing
key distinct from the capability key. Model output cannot sign or supply a
passed receipt. Completion checks signature, current attempt, required stages,
private object existence and hashes before success.

Record actual Node/compiler/browser versions. A managed browser/profile change
makes prior approval historical and requires verification under the new profile.
Phase 0 proves deterministic source-to-bundle rebuilding under the chosen
profile; service names and package versions alone do not prove reproducibility.

The trust basis is `trusted-adapter-observation`, not AWS cryptographic execution
attestation. The trusted computing base comprises the reviewed Runtime adapter,
pinned parser/compiler, independent Browser verifier and completion validator.
For each operation, capture the authenticated service/session identity,
operation nonce, exact input hashes, request/task IDs, observed status/exit code
and bounded raw-result hashes. Validate task/session association using service
status/read APIs where supplied. Reject malformed, wrong-session, wrong-nonce,
expired-attempt or mismatched-hash results. Model-authored pass flags are not
adapter observations.

Browser uses request interception over its authenticated automation channel to
fulfill the immutable bundle bytes; no general private web server or external
fetch fallback. Capture bundle manifest/hash, actual browser/profile, viewport,
independent assertion/a11y results, screenshots and blocked requests. Required
fonts are approved local bundle resources or part of the verified browser
profile. Page-authored postMessage results are not verifier evidence.
Completion validates the signed observation chain, allowlisted adapter/tool
profile, current key/attempt and object hashes. Inadequate service evidence
produces incomplete, never a claimed service-native attestation.

### publishing-handoff/1

Extend existing scoped runs, rounds, blobs, releases and registered Git export
APIs; do not create an unrelated handoff store. Responses identify schema version.
The manifest contains project/work item, base revision, model, ontology context
hash/coverage, component/toolchain versions, source/bundle hashes, added/modified/
deleted files with before/after hashes, mappings, evidence refs, publishing
approval, frontend acceptance and unresolved API/authentication integration.
Treat rename as delete/add unless an exact reviewed mapping proves continuity.

The publishing lifecycle is
`draft → validating → needs_changes|reviewable → approved → releasing → ready`.
Failed/indeterminate required checks cannot reach approved.
Frontend acceptance is a separate version-bound record.
Planners own published product/rule guidance; designers own UX approval.
Designers and developers may prepare releases, preserving SPEC §7-1.
Developers may export approved releases; project owners hold all these capabilities.
Agents and service identities cannot approve business/UX content.

An authorized designer/owner approves the generated-file allowance with the
criteria. Retain the current 24-file/128 KiB generated-source limit and approved
path policy. Reject changes outside that allowance; do not replace unrelated
source, packages, build settings or trusted styles.

UI states distinguish queued/running/failed, needs-changes, unknown/truncated
coverage, stale evidence, reviewable, approved, releasing and ready. Failed rounds
remain inspectable. Copy/download preserves verified bytes after current access
checks; deny unauthorized delivery rather than silently redacting code while
keeping its hash. Verify ranges, total size and final hash for chunked downloads;
partial bytes are not a complete ZIP.

Concurrent review uses expected source/contract/record versions. Git export uses
a registered repository/feature branch and expected base commit. Identical retry
returns the verified commit; occupied branches/changed bases are conflicts.
Timeouts or guessed commit URLs are not success.

| Artifact/state | Copy/download | Approval/release/export gate |
|---|---|---|
| Trusted component reference | Current authorized readers may obtain exact package bytes | Package provenance is not a generated-screen approval |
| Draft/failed candidate | Authorized designer/developer/owner may inspect/export as explicitly unverified source | No ready release or approved Git export |
| Reviewable candidate | Current authorized project readers; required checks pass and selected-scope blockers are resolved | Designer/owner may approve exact publishing source; agents cannot |
| Approved publishing source | Current authorized readers | Authorized release role starts deterministic rebuild/retest |
| Ready publishing package | Current authorized readers obtain verified package | Developer/owner may export to registered feature branch with data-audience checks |
| Frontend accepted | Exact source/package acceptance by developer/owner is recorded separately | Acceptance does not authorize deployment |
| Customer/production deployment | Outside publishing readiness | Requires separate frontend acceptance, integration/security tests and authorized deployment decision; unresolved API/auth integration blocks this classification |
| Stale/withdrawn/quarantined | Deny if source access was revoked; otherwise historical/diagnostic access only, clearly labelled | No current approval, ready release or export |

Frontend acceptance is not required merely to create a ready **publishing
package**. Unresolved external API/auth integration remains explicit and blocks
an integrated/deployed claim, not source handoff. A frontend rejection creates
needs-changes and removes effective delivery readiness for that revision while
preserving its history. Unknown coverage outside the approved scope remains
visible; an unresolved required screen, behavior or source binding cannot be
waived by renaming it optional.

### platform-execution/1

The durable ledger is the existing workspace DynamoDB job store. A shared
`workspace/execution_ledger.py` module is the single logical transition writer,
using conditional versions and `TransactWriteItems` for coupled updates.
The authenticated API calls it for admission/cancellation; the IAM-only
dispatcher calls it for attempt allocation; the new Lambda MCP facade calls it
for capability-authorized Runtime stages; an IAM-only watchdog/reconciler calls
it for bounded recovery transitions. Caller kind is fixed by the trusted handler,
never read from model/user arguments. Runtime has no direct ledger write IAM
permission. Backend roles retain only their reviewed datastore/control rights.
Transport acceptance is not success.

New execution jobs use the immutable discriminator
`task="agentcore-execution", executionSchemaVersion=1`. A reserved task or any
presence of `executionSchemaVersion`, including unknown/malformed values, must
never be treated as a legacy job. New dispatch/reconciliation accepts only its
exact supported discriminator and otherwise rejects without mutation.
B0 implements both sides of this exclusion in actual legacy claim/expiry paths
and new-ledger paths, with conditional version/discriminator fences. Its offline
tests must import the real legacy code. Before any probe creates new records
in a shared table, verify that the deployed legacy guard is installed.
B0's review inventories every legacy job-status writer, including failure,
cancellation, repair and helper paths, and applies the same conditional
exclusion wherever a new job could otherwise be mutated.
The inventory also covers every linked artifact/receipt completion writer,
including `_mark_failed` and analysis-read reconciliation. New artifacts carry
the immutable execution discriminator and exact execution/job linkage.
Legacy code must refuse mutation if the artifact is marked for new execution
or its linked job has a reserved/new discriminator. A missing job never makes
a marked artifact legacy. Mismatched/unknown linkage is reported for the
IAM-only new reconciler; legacy code does not fail or complete that artifact.

One platform-runtime deployment owner installs the reviewed guard change and
records worker/API revision or image hashes and RUN-01 live guard/legacy-control
results in the B1/G0-RUNTIME stop/go record. Before that, probes use an isolated
disposable table or do not create new-schema jobs. Guard rollback first stops
new-schema admission/probes and drains or quarantines those jobs; it must not
expose them to an older unguarded writer.
After matching deployed revisions to the reviewed guard, the deployment owner
may create one inert synthetic control record for the live refusal/legacy
control check, quarantine if needed and clean it up. General shared-table probes
remain blocked until that narrowly scoped installation check passes.

Source analysis using AgentCore is a new-ledger Runtime execution, not a cloud
analyzer injected into legacy `ontology_jobs.process`. B0 retires the obsolete
cloud-factory comment as documentation-only cleanup. Existing legacy jobs drain
unchanged; C wires the already guarded dispatch/read/reconciliation paths into
the application and verifies them live.

Invocation fields are schema version, execution/attempt IDs, Runtime session ID,
signed capability, immutable input-manifest ref/hash, selected model, absolute
deadline and backend-config revision. Queue messages contain no raw source
documents or reusable credentials.
The backend configuration references a versioned execution profile containing
the deadline, heartbeat, lease and recovery limits. Invocation/ledger/evidence
records retain that profile's revision/hash. Initial values below remain the
defaults until a reviewed profile revision passes the capability probes.
Attempts and transfer/completion receipts bind the exact consumed
source-admission decision IDs/revisions and artifact hashes; revalidation
cannot rely only on broker logs.

| State | Transition owner / condition |
|---|---|
| `queued` | API admission after authorization/idempotency |
| `dispatched` | Dispatcher atomically allocates attempt and lease |
| `running` | Runtime claims the matching attempt through the ledger tool |
| `succeeded` / `needs_changes` | Completion validates stage receipts and result manifest |
| `failed` / `cancelled` / `expired` | Actual failure, explicit cancellation, deadline or authority expiry |
| `recovery_required` | Watchdog observes lost lease or unknown external-call outcome |

A logical request key plus canonical input hash controls admission. Attempts
have distinct IDs and fencing numbers. Superseded attempts cannot publish current
evidence or alter terminal results. Initial deadline is 14 minutes, heartbeat
30 seconds and lease 90 seconds, subject to Phase 0 proving service compatibility.
Each stage reserves remaining budget; do not start a round that cannot finish.
Earlier authorization expiry shortens the deadline.
The immutable input manifest enumerates the exact admission-decision
IDs/revisions and admitted artifact hashes consumed by the attempt.

Retry idempotent reads/transfer chunks at most twice within budget. Record model
call intent and budget reservation before invocation. Unknown model outcomes
enter recovery-required; do not automatically repeat a potentially billed call
or claim exactly-once model execution. Explicit retry creates a fenced attempt.
Idempotent graph/Git side effects reconcile their durable result first.

Cancellation fences the attempt, blocks new model/tool dispatch and best-effort
stops Interpreter/Browser sessions. Late receipts are non-current diagnostics.
A crash retains verified partial evidence and last stage; the watchdog cannot
fabricate success. Correlate execution, attempt, capability digest, model call
and service sessions using allowlisted metadata logs, never source text/tokens.

The Runtime and model-boundary maintainers verify this exclusion in application
instrumentation, Runtime/service spans, exporters and configured destinations.
Synthetic source/prompt/tool-content and capability canaries must be absent from
those sinks. Missing capture controls or unverified destinations block activation.
SPEC §11-4's Tier 2 exclusion also applies to optional insights/Evaluations/Policy;
these checks do not require enabling optional observability products.
The same exclusion covers Interpreter/Browser service-session logs and
exporters, while authorized private compiler/browser evidence artifacts retain
their separately governed access. Verify the configured sinks in those service
gates as well as the Runtime gate.

All recovery transitions use the same ledger writer. The watchdog may request
`recovery_required`, failed, expired or cancelled, but cannot request generation
or fabricate completion. Reconciliation is an IAM-only backend operation:
recover signed, matching service/evidence records and recheck current
authorization. Only the still-current, unexpired attempt may reconcile to
succeeded/needs-changes, and only with the full completion contract.

The recovery window is at most five minutes and never extends the execution
deadline. Unresolved work becomes failed/expired with unknown-outcome metadata.
Explicit retry requires the original requester or project owner to have current
operation/source permission; acknowledge an unknown potentially billed outcome.
It creates a new fenced attempt, not an automatic model replay. Changed inputs
or criteria require a new approved request. Late results from superseded attempts
are quarantined diagnostics and cannot update current graph, Memory decisions,
approval or release. Retain metadata/evidence under the stated retention policy.

An injected test-key verifier requires explicit offline test opt-in. Production
construction rejects test verifiers, unregistered adapters and unregistered
keys; it requires the reviewed KMS/key-registry verifier. A callable's label or
caller-supplied test flag is not a trust basis.

#### Record fields (v1)

Status: the offline protocol is implemented in `workspace/execution_ledger.py`
(B0). It is not deployed. No API route, dispatcher, Lambda facade or IAM role
calls it yet; those transports are planned (C, B2). Offline tests are the only
callers.

Job record (`kind="job"`, id `exec-` + 128 random bits, written only with the
module-private ledger writer token):

| Field | Meaning |
|---|---|
| `task`, `executionSchemaVersion` | Immutable discriminator `agentcore-execution` / exact `int` 1 |
| `requestKey`, `inputHash` | Actor-scoped idempotency key; digest of actor, operation, model, manifest, admissions, backend revision, profile hash, authority, completion scope, `supersedes` |
| `actor`, `actorRole`, `projectId`, `authority` | Verified requester, role at admission and `{authorityRevision, membershipDigest}` from the current project record |
| `operation`, `model`, `backend`, `backendConfigRevision` | Closed operation map (`OPERATIONS`), selected model, `agentcore`, backend revision |
| `profile`, `profileBody` | `{id, revision, hash}` and the bounded profile body (`PROFILE_DEFAULT`) |
| `manifest`, `admissions` | Immutable input manifest `{ref, hash}`; consumed decisions `[{decisionId, revision, artifactHash}]` |
| `status`, `fence`, `attempt`, `attempts` | State, fencing number, current attempt, archived attempts (with `stages` receipt hashes and `late` diagnostics) |
| `calls`, `budget` | Per-call intents; `{calls, maxCalls, tokensReserved, tokensUsed, tokenBudget}` |
| `stages`, `nonces` | Verified receipts of the current attempt; nonces seen on the job |
| `handles`, `transfers`, `transferUsage`, `cleanup` | Transfer handles, closed outputs, `{chunks, bytes}`, fenced handles awaiting cleanup |
| `deadlineAt`, `authorizationExpiresAt`, `recoveryAt` | `min(now + deadlineMs, authorization expiry)`; JWT expiry; recovery start |
| `completionScope` | Frozen `{nonSourceOperations, sourceChecks, sourceBindings}` accepted at admission |
| `obligations` | Frozen `{sourceChecks: [{owner, kind, id, version}], sourceBindings: [...]}` taken from the validated input manifest |
| `ops` | `{operationId: {digest, version, status, value?}}`, bounded by `maxCalls + 80`, pruned at `allocate` |
| `result`, `error`, `unknownOutcome`, `supersedes`, `dueId` | Terminal result manifest, error code, unknown-outcome flag, superseded job, current due entry |

Attempt: `{id: "att-"+32 hex, fence, sessionId: "rt-"+40 hex, leaseExpiresAt,
heartbeatAt, startedAt, completionSubmissions?}`. Call: `{callId, stage, kind, status: intent|completed|
failed|unknown, at, attemptId, reserved, usage?, serviceSessionId?}`; usage
holds only `{inputTokens, outputTokens}`. `outcome` for an `interpreter` or
`browser` call requires `service_session_id`, the observed service session,
stored as `serviceSessionId`; a model call takes none. Stage: `{stage,
receiptHash, receiptRef, nonce, attemptId, status, service, result, outputs}`.
`receiptRef` is the immutable object
`job/{id}/receipts/{attemptId}/{receiptHash}.json` holding the signed receipt's
canonical bytes (they hash to `receiptHash`), written once at `stage` (and at
`reconcile` for the receipts it supplies). Transfer output: `{handleId, key, sha256, size,
stage, attemptId}` under `out/{attemptId}/{stage}/{name}` of the job. An input
handle (`source="input"`) is opened only when the resolved artifact hash equals
the frozen `admissions[*].artifactHash`; it records that `decisionId`,
`revision` and hash, and the server re-hashes the stored bytes against it. Every
`intent`, `open_*`, `read_chunk` (including a retried chunk index), `write_chunk`
and `close_output` rechecks the requester's current project authority (a
mismatch fails the job with `authority-changed`) and fences its mutation with
the project version check. Through the protected-operation guard below it also
revalidates every consumed admission and opened prior (a withdrawn one fails
the job with `authority-changed`); `read_chunk` additionally requires that an
input handle's admission still resolves to the same key and hash, otherwise
`transfer-invalid`. `read_chunk` and `write_chunk` are not `ops` entries; a supplied
`operation_id` (mandatory in production) is bound on the handle
(`chunkOps: {operationId: "index:chunkSha256"}`, at most `2 × chunks + 8`), and
reusing it for another index or chunk hash is `operation-changed`. Every returned
chunk is fenced after its bytes are read: a first read commits its usage with
the guard's predicates, and a pure retry (already read, same operation ID)
submits a check-only transaction (unchanged job version plus the guard's
predicates and `before_attempt` checks) that charges no usage; a failure
returns no bytes.

Receipts use schema v1. Required fields are `schemaVersion`, `executionId`,
`attemptId`, `fence`, `sessionId`, `stage`, `nonce`, `profileHash`,
`admissions`, `service`, `keyId` and `signature`. The optional fields
`operationId`, `inputs`, `outputs`, `objects`, `iat`/`exp`, `status`, `result`
and `previous` are validated when present. Any other field is rejected.

`service` is bound per stage (`STAGE_SERVICES`): `context` and `verify` →
`runtime`, `generate` → `model`, `compile` → `interpreter`, `browser` →
`browser`, `analyze` → `runtime` or `interpreter`. A `runtime` service carries
the attempt's `sessionId` and no `taskId`. Any other kind carries `taskId`, the
`callId` of a recorded call of the same attempt, stage and kind that no other
receipt of the attempt used. A `model` service carries the attempt's
`sessionId` and no `exitCode`; an `interpreter`/`browser` service carries the
call's recorded `serviceSessionId`, an integer `exitCode` and `profile`, which
must equal the pinned tool profile (`TOOL_PROFILES`, frozen in
`profileBody.toolProfiles`; admission refuses any other with `profile-invalid`). A receipt with
`status: ok` requires `exitCode` 0 (when present) and a `completed` call. The
`profileHash` must equal the job's profile hash. Any mismatch is
`receipt-invalid`.

Receipts follow the operation's evidence graph (`STAGE_INPUTS`): each stage's
`inputs` must include an output of the latest receipt of its predecessor stage
(`generate` ← `context`, `compile` ← `generate` or `context`, `browser` ←
`compile`, `verify` ← `browser` or `generate`, `analyze` ← `context`), so stages
are staged in order. A `compile` receipt produces exactly one output with role
`bundle`, and a `browser` receipt must consume exactly that bundle. The
receipt hash is SHA-256 over sorted-key JSON.

Completion (`finish`, and `reconcile` through the same path) re-reads every
retained signed receipt of the attempt and re-verifies it with the current
verifier (signature and key), its hash, bindings and `previous` linkage; any
failure is `receipt-invalid`. It also re-reads every chain output of the current attempt, including the result manifest and every
object it lists, and requires the stored hash and size to match before any
terminal pointer is prepared; a missing or changed object is `receipt-invalid`.
Every `files`/`objects` entry of the result manifest must be an object with a
string `key` and a 64-hex `sha256` (and, if given, the recorded `size`) that
names a chain output of the attempt; a malformed or non-member entry is
`receipt-invalid`.
Completion is refused with `calls-unresolved` while any call of the attempt is
still `intent` (no recorded outcome): the attempt stays open, the watchdog moves
it to `recovery_required` with `unknownOutcome`, and `retry` marks such calls
`unknown`.
Admission validates the immutable input manifest: the object at
`manifest.ref` (owned by the project) must hash to `manifest.hash` and be a JSON
object whose `admissions` equals the admitted decisions exactly, whose
`sourceChecks` are distinct `{owner, kind, id, version}` predicates that hold now
(otherwise `authority-changed`), and whose distinct `sourceBindings`
(`{sourceKind, sourceId, revision, audience, documentId?}`) match
`completionScope` one for one; otherwise `manifest-invalid`. The source
predicates are also checks of the admission transaction and are frozen as
`obligations`. At completion the ledger rechecks and itself submits every frozen
source predicate (a changed source fails the job with `authority-changed`).
`stage_completion` returns `{writes, checks, sourceBindings, sourceChecks?}`:
`sourceBindings` must equal the frozen bindings exactly, a declared
`sourceChecks` must equal the frozen predicates exactly, and a staged check of
a frozen record at another version is refused, all with
`completion-obligations`. Non-source operations above
`completionScope.nonSourceOperations` return `execution-completion-scope`.
`source.analyze` completion (`finish` and `reconcile`) returns
`completion-unavailable` (`reason: source-staging-adapter`) until the ontology
staging adapter supplies its manifest, request-marker and artifact publication.
Protected operations (`intent`, `stage`, `open_*`, `read_chunk`,
`write_chunk`, `close_output`, `finish`, `reconcile`) share one guard. It
revalidates the requester's membership, every consumed admission (the
`input_resolver` must still return `{key, sha256, check}` with `sha256` equal to
the frozen `artifactHash`) and every opened prior (`prior_authority` must still
return a `check`), where `check` is the `{owner, kind, id, version}` predicate of
the granting record. A withdrawn source fails the job with `authority-changed`.
These authority predicates are part of the transaction, and its `before_attempt`
callable runs immediately before each wire submission: it rechecks the
deadline and authorization expiry (`deadline`), the attempt lease
(`stale-attempt`), for `intent` the remaining-time reservation
(`deadline-budget`) and, for a model call, the daily cost gate. For completion
the final temporal checks run after `stage_completion` returns: `now < deadlineAt` and the
authorization expiry (`deadline`), the attempt lease for `finish`
(`stale-attempt`), and for `reconcile` the recovery bound
`now < recoveryAt + recoveryWindowMs` (`recovery-window`), which `reconcile`
also checks on entry independently of the watchdog.
Proven transaction contention is retried at most `completionRetries` (2)
times with fresh checks. Each completion submission is first counted durably
in `attempt.completionSubmissions` (a ledger-only conditional write after all
local checks pass), so at most `completionRetries + 1` submissions exist per
attempt even when the failure settlement itself contends; once the count is
spent, `finish`/`reconcile` retries only the settlement and returns
`completion-contention`. On exhaustion the ledger persists a fenced
`failed` transition with error `completion-contention` (fence bumped, quota
released) for the still-current attempt, and returns `completion-contention`;
later submissions for that attempt get `stale-attempt`/`terminal`. A job that
concurrently became cancelled, expired or superseded keeps that state.
An unknown transport outcome re-reads the job and compares it with the
submitting attempt: a terminal record with the same attempt, result and
operation entry is the committed result. Otherwise, while the same attempt and
fence are still active, the ledger conditionally enters `recovery_required`
with `unknownOutcome=true` from the latest version (a concurrent heartbeat is
re-read, not treated as a lost race), and returns `unknown-outcome`.

| Facade (caller role) | Methods |
|---|---|
| `api()` (authenticated API) | `admit`, `cancel`, `retry`, `read` |
| `dispatcher()` (IAM-only dispatcher) | `allocate` |
| `tool()` (ontology Lambda facade) | `claim`, `heartbeat`, `intent`, `outcome`, `stage`, `finish`, `fail`, `open_manifest`, `open_prior`, `open_input`, `read_chunk`, `open_output`, `write_chunk`, `close_output` |
| `reconciler()` (IAM-only watchdog/reconciler) | `sweep`, `reconcile`, `resolve_orphan`, `run_due` |

`Ledger.production` requires a registered verifier type and single-attempt
storage (`Storage(single_attempt=True)`). It uses the fail-closed
`common.costguard` gate. `register_verifier` accepts only a class defined by,
and registered from, the reviewed verifier module `workspace.execution_verifier`
(B1). Independently of registry membership, production rejects offline verifier
types: any `execution_fakes` class, any class exposing `sign`, or one marked
`offline`. `Ledger.offline` requires pytest and the `execution_fakes` verifier,
and `TestKeyVerifier` is constructible only under pytest. The production operation IDs are mandatory. Offline
tests may omit them.

Error codes: `request-changed`, `admission-required`, `authority-changed`,
`concurrency-actor`, `concurrency-project`, `execution-completion-scope`,
`unknown-operation`, `not-an-execution`, `not-queued`, `already-claimed`,
`attempts-exhausted`, `stale-attempt`, `receipt-invalid`, `budget-exhausted`,
`deadline-budget`, `token-budget`, `model-not-allowed`, `call-invalid`,
`daily-budget`, `daily-budget-unavailable`, `transfer-invalid`,
`transfers-incomplete`, `stages-incomplete`, `status-inconsistent`,
`operation-changed`, `operation-budget`, `operation-id-required`, `conflict`,
`completion-contention`, `completion-obligations`, `completion-unavailable`,
`completion-invalid`, `calls-unresolved`, `profile-invalid`, `manifest-invalid`,
`unknown-outcome`, `terminal`, `deadline`, `recovery-window`, `retry-expired`,
`acknowledge-required`, `not-retryable`, `forbidden`.

Supporting storage kinds:

- `exec_request`: the actor-scoped request marker `{jobId, inputHash}`.
- `exec_quota`: `active` job lists. The per-actor list lives in partition
  `execution:quota` (at most 1 active job). The per-project list lives in the
  project partition (at most 2 active jobs).
- `exec_due`: due-time-ordered entries in partition `execution:due`, with fields
  `{type: job|report, dueAt, status: pending|done, targetOwner, ref}`. Entries
  are tombstoned, not deleted.
- `exec_report`: the metadata-only legacy refusal report
  `{kind, recordId, reason, count, firstAt, lastAt, dueId}`. It has no payload
  and no source text.

Legacy guard: `Storage._prepare` refuses any non-ledger write in these cases:

- The item or the stored record is reserved: its `task` is reserved, or
  `executionSchemaVersion` or `executionId` is present.
- A linked artifact's stored job is reserved or has an `exec-` id.
- A linkage is moved to or from such a job.
- An existing linked record changes status while its stored job is missing
  (`unknown-linkage`).

Non-ledger updates also carry
`attribute_not_exists(executionSchemaVersion) AND attribute_not_exists(executionId)`.
Every stored linked job the guard reads is also fenced in the same
transaction: a `ConditionCheck` that its version is unchanged (or that it is
still absent). A single `put` with such a fence is submitted as a one-put
transaction, so a job that becomes reserved after the guard's read aborts the
artifact write.
Human approval, release creation and Git export use a separate module-checked
token. That token applies only to kinds `run`, `release`, `gitexport` and
`design`, and never moves a status into `failed`, `needs_changes` or
`completed`. The IAM-only reconciler resolves orphan reports with
`orphan-legacy-job`. It uses one transaction with the artifact CAS, the job
absence check and the due tombstone.

Not yet implemented in B0:

- the staging mode of `publish_candidate`. `ontology_store.py` is owned by
  Unit A. `finish` accepts a `stage_completion` callable whose writes and
  checks commit with the terminal job. Until the staging adapter exists,
  `source.analyze` completion is refused with `completion-unavailable`.
- the admitted-input resolver and the run-round prior-authority adapter. They
  come from the B0 intake and sharing units. Because every protected operation
  revalidates the consumed admissions through the resolver, production refuses
  protected operations (and input/prior transfers) until they are wired.
- the 4 MiB model-visible text budget.

### source-admission/1

Private intake owns the following closed, versioned record schemas. Unknown
fields/versions fail closed. Records have immutable revision/hash, status and
expiry; policy/grant changes invalidate affected decisions before transfer.
Their administration belongs to an IAM-only private administrative entry point.
Project/workspace APIs, model tools and workload identities cannot create,
edit or activate policies, provenance registrations or reviewer grants.
The in-app `platform-operators`/`admin` group alone is not IAM authorization.
The same IAM-only write/current-actor record-verification pattern applies to
the `design_publish`/`policy_publish` capabilities used by shared publications.

| Record | Required bindings and authority |
|---|---|
| Admission policy | Policy ID/revision/hash, deployment/project scope, eligible data classes, inspection/normalization profiles, expiry and trusted provenance rules. Cannot override mandatory privacy/source-access checks. IAM-only administration. |
| Provenance registration | Policy revision, server-owned fixture registry entry or policy-approved public source reference, exact revision/byte hash and scope. Filenames, project labels and uploader claims are not registrations. IAM-only administration. |
| Reviewer grant | Grant ID/revision, verified actor reference, policy/scope, allowed review operation and expiry/revocation. Does not grant source-read access. IAM-only administration; private APIs verify current grants on review. |
| Admission decision | Project/source reference and audience revision, exact admitted artifact/hash, original-to-derivative linkage, policy/provenance refs, inspection receipt/hash, applicable human actor/grant revision, status and expiry. Produced only by private intake after current checks; no raw originals or credentials in the record. |

These are administrative/internal contracts, not newly public workspace routes.
The implementation must record the concrete private module/storage/entry-point
bindings before installation. Source admission reads revalidate current policy,
grant where required, source audience and the exact artifact/inspection hashes.

## Phase 0: mandatory capability gates

### Resolved implementation choices

The first stage includes disposable bootstrap harnesses/resources and offline
contract code; these are needed to run the gates. Production adapters/cutover
come afterward. The offline ontology gate uses fixed TS/TSX/HTML/CSS/image
fixtures with expected identities, import/JSX bindings, reverse paths, ambiguity,
authority filtering, truncation and atomic-publication failure outcomes.

Model invocation runs inside the new Runtime container through the shipped
`engine/gate.py` boundary and its existing checks. Its IAM role has only
allowlisted model/profile invocation and scoped budget-record permissions;
Interpreter/Browser have no model permission. Source and boundary receipts flow
through the same signed stage-evidence/ledger path. No model call occurs through
the legacy bank tools target.

Memory decisions are per actor within a project. An application index identifies
up to 20 recent execution sessions for that same actor namespace, within retention.
Runtime retrieves bounded structured events from those sessions and revalidates
their source references before reuse. Shared team decisions are published
canonical ontology records, not implicit cross-actor Memory access.

Human mapping mutations use the authenticated `/ontology/partitions` and
`/ontology/nodes/:id/review` APIs in `workspace/ONTOLOGY_CONTRACT.md`.
The same canonical source/manifest fences govern these writes; Runtime MCP
tools cannot call the human-approval path. Business-source projections are
changed by their owning product/document publication APIs. Cross-project
remediation is written by an IAM-only routing worker through the shared
worklist writer, using publication/usage records rather than caller-supplied
destination project IDs. It returns no hidden destination content to the origin.

AgentCore data admission permits only synthetic, public or explicitly reviewed
internal-non-sensitive snapshots under a versioned deployment policy.
Unclassified, restricted-sensitive and identity-original inputs are blocked.
Private intake performs format/PII inspection before an AgentCore transfer;
required redaction happens through the private privacy service outside AgentCore
and produces a separately hashed derivative. Original mappings remain private.
The model boundary independently checks the final outgoing model payload.
Source permission and non-sensitive classification are separate requirements.

Private intake owns inspection, image normalization and admission records.
A platform security operator administers the versioned policy and names its
eligible source-admission reviewers. For required human decisions, including
internal-non-sensitive review, a reviewer needs that policy grant and current
source access; project ownership or workload identity alone
does not grant admission-review authority or a source-read bypass. Bind each
decision to policy revision, applicable reviewer revision and exact
source/derivative hashes. Synthetic/public inputs may use policy-approved
trusted provenance checks; caller-supplied class labels are not such evidence.
Missing policy, inspection, required reviewer authority or privacy-service
redaction blocks admission and transfer.

Canonical source bytes reach Runtime only through scoped artifact handles issued
by the new source-broker Lambda after current access/admission checks.
Transfers use 256 KiB chunks with offset, total size, chunk hash and whole-object
hash. Trusted binary transfer has a separate limit of 512 chunks/128 MiB per job;
it is not model-visible retrieval and does not spend the model-context allowance.
Read/write handles bind execution, attempt, stage, resource and expiry; they
cannot name arbitrary bucket keys. Runtime stages only that attempt's data,
verifies complete hashes, passes bounded files to Interpreter, and supplies
verified bundle bytes to Browser interception. Cancellation/revocation fences
further transfer and queues staging cleanup. Observer signatures come from the
trusted adapter, with no credentials in transferred files.

Image analysis is private pre-admission normalization using the pinned image
parser profile. V1 accepts PNG/JPEG and sanitized non-active SVG, at most 20 MiB,
20 megapixels and 8,192 pixels on either dimension. Reject animation, unsupported
color profiles, external SVG resources and decompression/parse failures.
Record original hash, EXIF orientation transform, sRGB/alpha handling,
normalization profile and normalized pixel/image hashes. Regions use integer
top-left coordinates after normalization and must fit the normalized image.
Similarity candidates require a separate permitted model/tool receipt and
human review; no automatic confidence threshold creates an approved relation.
The original bytes are preserved; normalization is an identified derivative.

The configured maximum repair rounds remains an upper bound. Before each round,
reserve model/compile/browser worst-case budgets plus cleanup against the actual
remaining deadline. Faster completed stages can permit more rounds; otherwise
stop with explicit budget-exhausted/needs-changes evidence. Never weaken checks
or claim all requested rounds ran. These timing assumptions are measured in
Phase 0 rather than treated as a service guarantee.

Minimal offline protocol/admission code and bounded spikes are the first
implementation work. Disposable probes transfer only server-registered synthetic
fixtures with exact hashes under a versioned probe-scope policy and private
format/PII inspection. They do not require complete customer-intake/sharing APIs,
but cannot bypass `source-admission/1` or its administration/access rules.
The private-intake owner validates this prerequisite before each transfer probe.
Use synthetic inputs
and disposable resources in an explicitly verified deployment account/region.
Plan statements are not passing receipts. Feature integration and production
cutover wait for every applicable gate.

| Gate | Measurable pass evidence | Failure decision |
|---|---|---|
| Offline ontology/analyzer | Fixed expected eight-level mappings, code/image references, witness paths and blocked/revoked/ambiguous/truncated cases all pass | Correct schema or implementation before enabling canonical projection |
| Private source admission | Current policy/provenance/reviewer/source bindings; private image normalization and inspection; required redaction derivative; source-readable but inadmissible inputs and unauthorized policy/grant writes denied | Stop transfers until the private-intake gate passes. Probe-scope synthetic approval cannot admit unreviewed source material. |
| Code Interpreter | Fetch exact-version S3 archive ≤64 MiB compressed/256 MiB expanded and verify hash within 120s; record OS/architecture/Node; compile with matching native dependencies in ≤60s without package install; deny source-bucket access; identical rebuilds; source/token canaries absent from session logs/exporters | Stop; revise archive/runtime/logging strategy and repeat. No relabelled local compiler. |
| Browser | Authenticated exact-bundle automation in no-egress VPC; blocked external HTTP/WebSocket; behavior/a11y/Korean fonts in ≤120s; actual profile/screenshots; source/token canaries absent from session logs/exporters | Stop; resolve service/network/profile/logging constraints. No public-network fallback. |
| Identity/Gateway | Brokered credential accepted by dedicated audience/scope; allocated-attempt capability invokes Lambda; swapped/expired/revoked requests deny; capability canaries absent from Gateway/Identity logs/spans/exporters | Stop until topology and credential exclusion are proven. No decorative Identity or anonymous Gateway. |
| Runtime/ledger | Legacy/new writers reject incompatible job discriminators before mutation; duplicates have one attempt; crash recovery/cancellation fence late publication; execution-profile timing and metadata-only telemetry verified | Fix protocol, deployed guards and telemetry before real job integration. |
| Memory | Structured scoped event storage/retrieval without extraction; another actor/project denied; stale references excluded; deletion recorded | Block selected-backend admission until isolation/retention pass. |
| Models/region/quotas | Resolve enabled provider IDs, boundary-routed synthetic calls, region/network compatibility and measured quotas/budgets | Mark requested model unavailable; no silent substitution. |

Record account/region, service/API versions, tool/profile hashes and timestamp in
private evidence. Initial concurrency is one execution per actor and two per
project. Retain the daily cost gate; require an explicit per-execution token
budget and service-spend alarm before production admission. No unbounded retry
or automatic concurrency expansion.

Use encrypted private stores, TLS and least-privilege keys/roles for capabilities
and evidence. Record/approve any cross-region provider route in deployment policy;
Runtime region alone is not a data-residency guarantee.

Gate owners are the platform-runtime maintainer (Runtime/Interpreter), verifier
maintainer (Browser), platform-identity maintainer (Identity/Gateway), ontology
maintainer (offline ontology/analyzer and Memory/source bindings), and
model-boundary maintainer (models).
The private-intake maintainer owns the source-admission gate; the platform
security operator owns its IAM-administered policy/provenance/grant approval.
The privacy/infra maintainer owns any source-document redaction adapter, caller
authorization and private network route. Existing MyData relay/gateway support
does not imply that source-document intake is covered by its current contract.
Until that separate integration is reviewed, only eligible synthetic/public
inputs with complete required inspection and no redaction dependency can pass;
inputs requiring unavailable redaction stay blocked.
The implementation record names the responsible reviewer before a gate runs.
Each gate saves its configured limits, fixture hashes, observed measurements,
negative-case outcomes and stop/go decision. Production integration cannot
proceed with an unowned or unmeasured gate.
Interpreter/Browser gates govern generation and handoff rows; Identity/Runtime
govern every protected execution; Memory governs reuse; ontology/code fixtures
govern classification and impact; private admission governs data transfers;
all gates govern deployment admission. The ontology owner coordinates the
SRC-05/06 offline portions with the private-intake owner.

## Vertical acceptance case and rollout

Use one synthetic product, one published rule, one shared image, a declared
eight-level mapping, two dependent screens and their real React/CSS sources.
Register exported HTML/image/source, review mappings, and propose an image
revision. Show both affected screen paths and exact code references plus a
deliberately dynamic unresolved reference. Generate the allowed delta, compile
in Code Interpreter, verify in Browser, record Memory references, review/copy/
download and rebuild the approved source. Capture actual Identity/Gateway/Lambda
and Runtime receipts.

Repeat with an authorized publication grant and then its revocation. Negative
cases cover private cross-project IDs, capability swapping, revoked membership,
stale rules, altered compiler archive, malformed browser evidence, Runtime crash,
duplicate dispatch, cancellation and partial download. None may produce a
current approved/ready result.

Roll out through offline contract fixtures, disposable Phase 0 spikes, private
staging acceptance, a limited synthetic production project, then broader
admission. Complete applicable code review/CI before production code publication.
Live acceptance and rollback evidence precede general admission; merge is not
that gate.

Backend selection is a versioned operator setting fixed at job admission.
Its initial default is **the current Lambda backend**. AgentCore is enabled only
for the explicit staged cohort after the gates pass. Keep the existing
PRIVATE_ISOLATED browser Lambda as the legacy backend during migration; label it
`Lambda · isolated legacy verifier`. Label the new path `AgentCore Runtime ·
Code Interpreter · Browser`, with actual receipts. Do not delete legacy resources
until active jobs drain and rollback verification is complete.
Drain existing jobs on their original backend; do not migrate an in-flight
attempt. Display actual backend/service evidence. Rollback pauses AgentCore
admission and deliberately selects a compatible previous backend for new work,
labelled legacy execution. Existing AgentCore jobs fail or reconcile explicitly;
no per-call automatic fallback.
For source analysis there is no production legacy analyzer: rollback blocks
new analyses until a verified backend is restored, never enabling the offline
test adapter.

Deliver at least three independently reviewed PRs: **A**, canonical ontology,
vocabulary mappings, analyzer and reverse-impact contracts with offline tests;
**B**, AgentCore adapters, dedicated IAM/Identity/Gateway/Memory/Browser/Interpreter
infrastructure and Phase 0 receipts; **C**, cohort cutover, actual job/UI wiring
and end-to-end acceptance. PR A does not claim AgentCore execution, PR B does not
enable the production default, and PR C does not bypass A/B reviews or gates.

The B chain contains independently reviewed offline execution protocol,
private admission/normalization, publication/source-authority adapters, durable
execution authority/key registry, disposable capability probes and service
adapter/IaC units. B0/B1/B2 in the architecture register subdivide these units;
they do not change A/B/C gate or review obligations. C cannot complete sharing,
source-analysis or handoff acceptance without the corresponding B authority
adapters and private-admission evidence.
