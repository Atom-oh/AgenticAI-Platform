# Design execution delivery

Implementation design and candidate register, 2026-10-11. Owning authorities: SPEC §7-2,
AGENTCORE_CONTRACT `platform-execution/1`, `source-admission/1` and
`publishing-handoff/1`, ONTOLOGY_CONTRACT, and REACT_CONTRACT.
This document records implemented candidate interfaces and remaining gates; it is not acceptance.

## Application and data

Add a project-scoped, versioned `design` record and authenticated `/designs`
routes. The existing canvas remains available. A procedure design adds a
persistent path through admitted inputs, cited PRD, flow approval, generated
variants, verification, exact UX approval and developer handoff.

Every stage binds the design revision, current canonical procedure snapshot,
all source/admission versions, component catalog and selected model. Private
originals and original-to-normalized mappings never enter Runtime. Derived
ontology context is inspected independently before transfer; every referenced
source must have current admission and audience authority. Prompt instructions
use the existing reviewed prompt-text admission.

Human actions are separate from execution: planner/owner confirms the exact
cited PRD; designer/owner confirms the flow and approves a verified bundle;
developer/owner records frontend acceptance and performs an authorized export.
A model/tool cannot create these decisions. Any changed source, graph,
criterion, composition or evidence revision invalidates the dependent decision.

The frozen flow derives the real React assertion contract using
`design_loop.contract`. It cannot be weakened by generation or repair. Layout
variants and edits retain complete lineage. Required financial strings come
from admitted cited spans and deterministic bindings.

## Single execution writer

The application uses the existing B0 ledger for `design.extract`, `design.generate`,
`design.edit`, and `design.release`. The ledger also defines `design.compose`; its
deterministic application dispatch is still unimplemented and is not an enabled route.
Design artifacts are marked and created with their reserved job. Only the ledger
publishes a completed stage, its design pointer and associated run/release
records in one conditional transaction. Human approval uses the existing
module-checked human writer. Legacy workers cannot claim or complete these jobs.

`ExecutionSources` resolves all supported admitted artifact types and prior
outputs with current lineage and expiry. The shared capability binds the exact
operation; a source-analysis token never authorizes a design job. Runtime uses
the fixed machine Gateway envelope, bounded transfers, heartbeat and the same
receipt verifier. Known conditional contention may retry only the same logical
operation; unknown paid outcomes never authorize implicit invocation replay.

Runtime model calls pass through `engine/gate.py` after residual inspection.
Each model call has an intent, reservation and measured outcome. Every model
selection remains exact; an unavailable requested model blocks its path.
Interpreter compiles the frozen project with the pinned archive. Browser checks
the same bundle, including a second large-text pass. Verification combines the
reviewer, tester and coverage roles; missing evidence cannot approve a result.
Releases rebuild approved source without generation and repeat the exact-bundle
checks before download/Git export. Release success requires no final verification
issues, matching the ledger terminal rule; findings cannot become release success.
Full findings stay in hash-checked private objects. Signed receipt results contain
bounded summaries/references under 8 KiB, including the large-text Browser call
and session. A finding-rich generation can complete as inspectable needs-changes.

Partial edits also compare isolated-world Browser checkpoint boxes with the exact
base bundle. Every element outside the Registry-defined subtree must remain
within one pixel. Missing/oversized/duplicate checkpoint evidence blocks approval;
structural stability alone is insufficient. Completion repeats this comparison.

## Delivery gates and evidence

Cases: ONT-01/04/05/07/09/10, SRC-05/06/07, AUTH-01–09, INT-01–02,
BROW-01–02, GEN-01–05, MEM-01–03, RUN-01–05, HAND-01–05 and ROLL-01–03.
The 57-case acceptance template and its fixed denominators remain unchanged.

The private dated inventory records observed stack names, account and temporary
role. Those observations do not amend SPEC topology or prove a current deployment
of this candidate. Inventory and live validation must retain their exact resource
and revision bindings.

Use the existing isolated probe resources or a separately scoped disposable
stack before touching shared data. Record exact deployment/code/archive hashes,
API identities, timings, signed observations and negative results. Verify deployed
legacy writer guards before shared-table controls. Main activation requires the
applicable service gates, a limited synthetic cohort and rollback evidence.
No source tests, READY resource or old receipt passes a live gate.

Complete code/tests, latest-HEAD Kiro review and required CI before merge.
Reconcile any real service failures, repeat affected tests and live probes,
then record actual acceptance and cleanup. Preserve missing/blocked model or
service results; do not claim full completion by narrowing the case set.

## Implemented interfaces (candidate; validation in progress)

`workspace/design_api.py` owns the authenticated `/designs` prefix and its final
response authority fence. Private object keys are never public design summaries.
Lists use actor/role/project-epoch-bound opaque five-minute cursors. Every delivered
variant retains all consumed admissions, including later edit instructions.

| Route | Behavior |
|---|---|
| `GET /designs/config` | Reports only this project's configured cohort and exact model choices |
| `GET /designs/inputs` | Current-authorized admitted documents/instructions; bounded pages |
| `POST /designs/admissions` | Requests reviewed internal-non-sensitive use of an approved library document |
| `POST /designs/instructions` | Uses private prompt-text normalization and the existing reviewer workflow |
| `GET/POST /designs` | Lists or creates a source-bound design with a selected approved procedure and model |
| `GET /designs/:id` | Current design, cited PRD, frozen flow and its assertion contract |
| `POST /designs/:id/confirm-prd` | Planner/owner confirms exact cited values; changed financial spans fail |
| `POST /designs/:id/approve-flow` | Designer/owner approves the exact flow/contract hash |
| `POST /designs/:id/extract`, `generate`, `edit`, `release` | Creates the corresponding reserved execution and marked artifact |
| `POST /designs/:id/approve` | Designer/owner approves the exact verified source/bundle pair |
| `POST /designs/:id/cancel` | Stops the active ledger execution; no legacy worker fallback |
| `GET /designs/:id/artifacts/:artifactId` | Authorized preview/element targets or hash-checked chunks |
| `GET /designs/:id/releases/:releaseId` | Authorized release metadata or verified source/bundle/handoff chunks |
| `POST /designs/:id/releases/:releaseId/accept` | Developer/owner records exact frontend package acceptance |

Registered Git export continues through the existing `/releases/:id/git`
authority and delivery checks. Design releases additionally require the matching
frontend acceptance. A missing registered repository/credential remains blocked.
Legacy generation/approval/release mutation routes refuse design-owned records.
Existing read/download/Git routes authorize the same canonical design criteria:
`projectId`, `designId`, `designSnapshotHash` and `catalogHash`, plus the exact
human-approved flow/contract, current procedure/source admissions and their
expiry. Product-guideline contracts retain their complete existing criteria.
Every outgoing Git request rechecks the retained source authority and the original
request authorization deadline persisted on the export. A refreshed JWT does not
renew a queued export; its logical retry identity excludes the deadline. Failed
candidate content remains limited to owner/designer/developer on every chunk;
missing and inaccessible singular design reads return the same 404.

## Runtime and publication

The capability retains the single `operation` bound in the job. Design operations
are enabled only by an explicit `design: {projects, models}` deployment setting.
The Runtime keeps no workspace datastore grant. Only its configured exact model
profiles receive invocation rights. Private residual inspection runs in the
Gateway Lambda before each model invocation; `engine/gate.py` independently
measures and checks the actual outgoing payload. SDK model calls use one attempt.
Input/output token usage and SDK request IDs are retained as metadata, not prompts.

Verified canonical IDs and cryptographic hashes can incidentally resemble phone
or identity numbers. `design_loop/references.py` replaces only those verified
structural references with reversible opaque prompt aliases. It never changes
human text or financial values. The full actual aliased prompt still passes both
private residual inspection and the model boundary. Replies restore those exact
references before citation/composition validation.

`modelInspections` records the inspected hash and byte count, bounded by the
operation's call ceiling; total model text is at most 4 MiB per job. This has a
separate bounded idempotency map so it does not consume the existing intent/stage
operation reserve. A retry repeats every capability/membership/source predicate;
no project predicate is removed. Unknown outcomes never authorize a repeated
paid call. The UI requires acknowledgement before repeating uncertain work.

`DesignPublication` independently reconstructs generated files from the frozen
composition/PRD/registry, checks actual archive bytes and the compiler/Browser
receipt chain, and validates required tester/coverage evidence. It composes the
job, design pointer, artifact and associated immutable contract/run/release
records in one transaction. Identical record-version checks may be enforced by
the corresponding conditional Put; differing predicates still conflict.

Flow approval includes the derived React assertion contract. Materializing its
immutable contract record preserves that exact human decision; Runtime does not
create an approval. UX approval remains a later, separate exact source/bundle
decision. Release rebuilds approved sources without model generation, compares
the approved screenshot at 0.02, and repeats large-text verification. The handoff
ZIP keeps every approved source entry byte-identical under `project/` and carries
convention metadata and the verified path-application script. The default naming
convention is a platform package convention, not verified customer integration.

## Memory and operational limits

A completed execution may atomically publish a same-actor Memory index with
source/admission/profile/backend fingerprints. The recent-session query is
bounded to 20 and rechecks authority before and after reading structured events.
Revoked or changed source contexts are not reused. Existing three-day retention
and immediate read denial remain in force. Explicit cloud-event purge code and
its live deletion check are awaiting the separate authorization required by the
execution environment; no new DeleteEvent permission has been added.

Model proposal repair is bounded by the requested ceiling and the engine's
existing cap. Compiler/Browser failure cannot become a pass or relax the frozen
contract. Successful, failed and uncertain outcomes remain distinct in the
ledger; incomplete evidence cannot produce a release.

The candidate is being tested with real private-intake/Storage/CAS, RSA,
compiler, Browser and UI paths, while substituting AWS transport/model responses
in offline integration tests. AWS code/image egress approval and a registered Git
destination/credential are pending. No live gate or production activation is
claimed from these tests. Final review, CI and the private assessment must record
the exact candidate and remaining gate outcomes.

## Reviewed deployment constraints

Design publication S3 writes add only `design/*` and `release/*` under the existing
workspace prefix, for tools and reconciler. The watchdog has neither prefix;
`run/*` writes are unnecessary. Tools/watchdog/reconciler may update only
`usage#*` and `usage-charge#*` keys in the separate budget table, not workspace
records through UpdateItem. Design synthesis requires both a private deny-list
parameter and an intake deployment scope. No DeleteEvent grant is installed.

Changing the deployment cohort changes the backend revision: drain active work
first. Prior variants from another revision must be regenerated before edit or
release. Runtime resources are an explicit three-file allowlist; a manifest hash
is evidence of identity, not a privacy classification.

Design handoff displays the authorized export receipt in the same panel, including
queue/failure/unverified state and actual branch/base/commit SHA. Release-view
reads retain the export record version through the final response fence. Model
boundary artifacts retain field counts rather than prompt-derived field labels.

Design records retain only fixed-shape verification summaries (verdict, approval
flag, finding/critical counts and layout counts). Full findings, role evidence and
geometry remain in the hash-checked private artifact returned by artifact view.
Admission reserves 64 KiB of metadata headroom before dispatch; a design whose
history reaches that bound requires a new design rather than spending on a job
that cannot publish. Existing idempotent requests still return their durable result.

The common Git worker image also packages `design_loop` and its resources:
registered design exports reauthorize canonical design criteria in that worker.
API ZIP assembly already includes the package. Isolated packaging tests import
`workspace.design_stages` using only Docker COPY contents, so a checkout-local
module cannot hide a missing deployed dependency.
