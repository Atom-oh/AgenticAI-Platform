# Dedicated ontology Runtime adapters

This module implements the source-analysis execution path for implementation
unit B in `platform/docs/ONTOLOGY_AGENTCORE_PLAN.md`. It does not enable the
production backend or complete the generation/release cutover.

## Execution and authority

The authenticated workspace API creates a durable analysis artifact/job with
the actor, project, source revisions, resolver, authorization expiry and selected
backend. The ordinary Worker claims the job before invoking `RuntimeAnalyzer`.
The adapter calls a dedicated IAM execution-authority Lambda.

`Authority` rechecks project membership, source access, the selected tool archive
and exact source classification. It creates a bounded execution ledger and a
KMS-signed capability for one actor/project/attempt/Runtime session. A request ID
or an execution ID alone is insufficient authorization. A dispatch marker
prevents an implicit second Runtime execution for an uncertain first attempt.

Runtime receives the capability over its IAM invocation. Fixed workflow code
adds authorization to Gateway calls; model-facing schemas omit that envelope.
The separate Gateway target validates the signature, active signing-key record,
ledger binding, allowed operation, deadlines, current membership, source
classification and source revisions on every tool call. Source and execution
writes use the project and source version fences.
Each protected call also binds the original artifact and running workspace job.
Cancellation, terminal artifacts and admission-time membership changes reject
further work. Coupled writes check those job/artifact versions, and tool commits
retain the capability's shorter expiry. Execution and operation records retain
their bounded DynamoDB TTL.

The source-analysis workflow obtains context/source data through Gateway, reads
scoped structured Memory events, runs the pinned analyzer in Code Interpreter,
signs its result with a distinct evidence key, records continuity hashes and
finishes the ledger. Authority verifies that result before returning it to the
workspace publisher. No model is called by this source-analysis workflow.

## Identity and credential permissions

Runtime uses IAM inbound authentication. Its automatically managed workload
identity is recorded as deployment evidence. AWS does not allow that managed
identity to request a token through the manual Identity token APIs.

The stack therefore registers a separate machine workload for the dedicated
Gateway. Runtime's role can request that workload's token and broker the
configured OAuth client credential through Identity. The workload ARN is fixed
in deployment configuration and signed execution claims; arguments cannot select
another identity or credential provider.

`GetResourceOauth2Token` also requires `secretsmanager:GetSecretValue` on the
provider's backing secret. A deployment custom resource reads only the
provider's secret ARN using `GetOauth2CredentialProvider`. Runtime receives
permission for that exact ARN. It has no general Secrets Manager listing or
reading permission. Tokens and secret values are not written to artifacts,
Memory, model prompts, browser pages or logs.

Runtime can invoke the configured Interpreter, Browser and Memory resources and
sign evidence. It cannot directly read the workspace source bucket/table or
invoke Lambda tools. The Gateway role invokes only the separate ontology target.
The target cannot sign capabilities/evidence, call bank model APIs, invoke the
private plane/bridge or reset/write the shared Registry. The bank IAM Gateway
and its customer-profile tools are separate resources.

## Input, tooling and receipts

Project read access is distinct from AgentCore admission. A project owner must
record `synthetic`, `public` or `internal-non-sensitive` classification against
an exact current source reference. Admission records have their own version
fences. Changing source bytes/revision requires another classification.

Code Interpreter's role reads only the pinned S3 tool-archive object version.
The adapter verifies archive/code/lock hashes and the observed architecture/
Node major before using the trusted tool commands. Source modules, package
hooks, arbitrary build configuration and model-authored commands are not run.
Actual interpreter/session/tool/input hashes are retained in execution receipts.

The Browser adapter uses authenticated automation against the configured
isolated VPC browser. It delegates static-bundle fulfillment and checks to the
existing verifier. Browser credentials never enter page resources. The module
also provides compiler/browser adapters; source-analysis success alone is not
compile, browser, generation, release or customer-workflow acceptance.

Memory has no extraction strategies. It stores bounded structured continuity
events and hashes under derived project/actor/session scopes. It does not grant
source access or replace source/approval revalidation.

## Validation

Use Python 3.12, the workspace requirements and the pinned infra dependencies:

```bash
PYTHONPATH=platform python -m pytest \
  platform/tests/test_agentcore_capability.py \
  platform/tests/test_agentcore_identity.py \
  platform/tests/test_agentcore_authorization.py -q
cd platform/infra
npm ci
node --test test/ontology-stack.test.cjs
npx tsc --noEmit
```

Prepare Docker contexts using `prepare_context.py --output <empty-build-dir>
--axe <installed-axe-core-dir>`. It copies only the listed public module/tool
files, refuses source symlinks and records their hashes. Customer archives,
credential files, private operation receipts and unrelated worktrees are not
Docker build inputs.

`infra/bin/ontology.ts` accepts the operator-owned `ontologyConfigFile` context.
Supply the isolated network, immutable archive descriptor and public code
context. Omitting workspace storage selects retained synthetic test stores.
Verify the target AWS account and role before deploying.

Keep live receipts outside Git. Verify actual Runtime/Identity/Gateway/Lambda/
Memory/Interpreter execution and negative cases; provisioning a READY resource
does not establish acceptance. Capture compiler and isolated-browser evidence
separately. Integration with the latest foundation, concurrency/cost admission,
versioned cohort activation, generation/release wiring, full negative acceptance
and rollback remain cutover gates. Do not enable the production default merely
because a synthetic source analysis passed.
