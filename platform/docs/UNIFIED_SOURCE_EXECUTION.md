# Unified source execution

Status: implemented source path, offline validation only, 2026-10-09. This design
implements the source-analysis slice of `platform-execution/1`; it is subordinate
to SPEC §7-2, [AGENTCORE_CONTRACT](../workspace/AGENTCORE_CONTRACT.md) and
[ONTOLOGY_CONTRACT](../workspace/ONTOLOGY_CONTRACT.md). It does not activate a
cohort, certify service gates or implement the later design-stage application UI.

## Application boundary

The project-authorized ontology API adds:

| Route | Input / result |
|---|---|
| `POST /ontology/executions` | `{requestId,name,collectionDecisionId,expectedGeneration?}` → bounded job and analysis-artifact metadata |
| `GET /ontology/executions/:jobId` | Current member and source-authorized job status |
| `POST /ontology/executions/:jobId/cancel` | Requester or current project owner cancels the job and linked artifact together |
| `POST /ontology/executions/:jobId/retry` | `{acknowledgeUnknownOutcome:boolean}`; the ledger enforces retry eligibility, original authorization/deadline and acknowledgement of uncertain paid work |
| Existing `GET /ontology/analyses/:artifactId` | Authorized completed analysis, coverage and execution evidence |

The input must be a real admitted `code-collection`. The server resolves its
policy, provenance/reviewer grants, source revision and IAM-owned resolver. It
reads and verifies the normalized index and file payload through intake. The
immutable manifest contains the normalized analyzer payload, exact admission
references, tool configuration, source predicates and audience bindings. The
private original-to-normalized mapping and user-supplied partition name do not
cross into Runtime.

One transaction admits an `agentcore-execution` job, its actor-scoped request
marker, quota/due records and marked `wb_artifact`. The artifact descriptor is
part of `inputHash`. A failed queue send leaves a durable queued job; repeating
the same application request can requeue it. Replays reauthorize sources.

The older files-based `POST /ontology/analyses` and `ontology_jobs.process`
remain separate, explicitly offline paths. Configuring a cloud analyzer does
not permit that legacy worker to execute these jobs. The existing analysis
canvas is preserved; it has not been converted into the new design workflow.

## Authority and transport

`ExecutionSources` reconstructs the admitted actor's current scope from the
job, not Runtime arguments. Every admission resolution returns all policy,
provenance, reviewer, resolver, decision and source predicates plus their
minimum expiry. Prior run-round/release reads additionally verify exact archive
bytes and current upstream lineage. The ledger carries the entire predicate
set into protected transactions; pure clock expiry remains a final guard.

The API receives only `sqs:SendMessage` for the configured dispatch queue. A
versioned IAM Lambda consumes one message at a time, allocates a random attempt,
signs its capability and commits its digest/claims plus dispatch intent before
one Runtime invocation. SDK invocation retries are disabled. Duplicate messages
cannot allocate a dispatched/running/terminal job. An uncertain outcome enters
bounded recovery and never authorizes an automatic second Runtime call.

Capabilities use a separate KMS key and closed JWT claims under issuer
`platform-execution/1`, audience `platform-execution-lambda/1`. They bind actor,
project, job, attempt, fence, Runtime session, workload, operation, manifest hash,
profile hash, backend revision and authorization expiry. Lifetime is at most
15 minutes and bounded by key, job and actor expiry. Only the digest and claims
are stored. Gateway key/binding versions and current membership join actual
conditional writes, including receipt staging and publication.

The dedicated Gateway exposes one machine-only `execution` tool with
`{action,arguments,_executionAuthorization:{capability,operationId}}`.
`ExecutionTools.ACTIONS` is the closed argument allowlist. Job/owner/attempt
identifiers come only from the verified capability. The tool is excluded from
model-visible discovery; this source workflow invokes no model. The previous
`ac_execution`/`ac_operation` adapters remain compatibility test code, and are
not the dedicated Runtime/application entrypoints.

Runtime claims the attempt once, runs a 30-second heartbeat, reads the manifest
through 256 KiB hash/ETag-bound chunks and invokes only the fixed Interpreter
analyzer. It persists call intent before invocation and records the observed
Interpreter session. Structured Memory continuity contains hashes and scoped
references only, with extraction disabled; it never supplies authority.
Context, analysis and final manifest outputs use ledger-owned immutable handles.

`ReceiptSigner` signs SHA-256 of `platform-execution/1:receipt\n` followed by
finite canonical JSON excluding `signature`, using KMS RSASSA-PKCS1-v1_5/SHA-256
with `MessageType=DIGEST`. Receipt times are milliseconds; JWT and key registry
times are seconds. `KmsVerifier` is a registered verify-only class. It verifies
key purpose/state/window, KMS status, public-key fingerprint and signature.
Receipt-key version checks and expiry bounds join stage/completion transactions.

## Coupled publication and recovery

`SourcePublication` is the installed trusted source completion adapter. It
rechecks the original normalized payload, admissions, tool archive/parser/lock,
observed Interpreter session and result coverage. Graph references use
`admitted-code` (decision ID/revision/index hash, project audience), with
normalized file locations. Such a reference never identifies an original ZIP
path. Current content reads verify the decision and resolver; historical
metadata authorization does not return file bytes or bypass current grants.

`Ontology.publish_candidate(_stage=True)` writes immutable graph/index blobs
and returns conditional writes without publishing a current manifest. The
ledger combines those writes, the ontology request marker and completed
artifact with its terminal job/attempt/receipt/quota/due writes in one
transaction. Source obligations are frozen at admission and cannot be trimmed.
The existing 100-operation limit, declared non-source reserve, bounded proven
contention retries and unknown-outcome rules apply. Any failed predicate leaves
all public completion state unchanged. Unreferenced immutable blobs are not
proof of publication.

Cancellation, failure, expiry and recovery update the marked artifact through
the same ledger writer. The scheduled watchdog discovers due jobs and settles
expiry/cleanup. The IAM-only reconciler accepts only an owner/job reference and
can finish from already retained signed stage receipts and output manifests;
it cannot rerun Interpreter or invent missing evidence. Incomplete evidence
remains incomplete until timeout/failure or an explicitly authorized retry.

## Deployment and validation

`OntologyStack` packages the new Runtime, execution tool, SQS dispatcher,
watchdog and reconciler. Tools can write the workspace execution/publication
records; explicit Denies protect intake administration and the key registry.
Runtime has no workspace S3/DynamoDB access. Interpreter can read only its exact
archive version. The API has no capability-signing or Runtime invocation grant.
Existing bank Gateway, MyData and Registry paths remain outside this stack.

`SOURCE_EXECUTION_CONFIGURATION` is deployment-owned and contains protocol,
revision, capability/evidence IDs, evidence ARN, workload identity and the
immutable Interpreter configuration. Only the dispatcher receives
`SOURCE_EXECUTION_RUNTIME` with Runtime ARN and versioned qualifier. Keeping
Runtime identifiers out of the tool environment avoids a CloudFormation
Runtime → Gateway → Tools → Runtime dependency cycle.

The main stack is unchanged by default. Its optional `sourceExecution` context
requires `{queueArn,configuration}` and configured intake; the queue must be in
the target account/region. Use the dedicated stack outputs and the main intake
deployment scope. Configure `cacheTableName` to share the intended daily budget;
a standalone synthetic stack otherwise provisions an isolated budget table.
No live deployment or opt-in was performed for this source change.

Focused O-mode evidence is in `test_execution_integration.py` (real RSA oracle,
real intake and conditional storage, substituted AWS transports), existing
execution/source/intake regression tests, and `infra/test/ontology-stack.test.cjs`.
Cases supported in part: ONT-04/07/09/10, SRC-02/03/04/06/07, AUTH-01–09,
RUN-01–05 and ROLL-01. The acceptance document's O/L/U obligations and all G0
service gates remain independent. In particular, offline signatures, a
successful synthetic chain and a passing synth do not establish AWS operation,
network isolation, customer admission, cost measurements or full UX acceptance.
