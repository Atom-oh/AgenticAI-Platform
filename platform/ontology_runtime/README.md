# Dedicated ontology Runtime adapters

The source-only application path now uses `workspace.execution_ledger.Ledger`
from admission through Runtime and atomic ontology publication. It remains off
in the main stack unless explicitly configured. No live service gate or full
application cutover is claimed. See the [unified source design](../docs/UNIFIED_SOURCE_EXECUTION.md)
for API fields, protocol, persistence, recovery and validation scope.

## Execution and authority

`execution_entrypoints.py` constructs single-attempt storage, the registered
KMS receipt verifier and actual admission/prior adapters. The authenticated API
creates a reserved execution and marked analysis artifact and sends only their
opaque reference to SQS. The IAM dispatcher allocates one attempt, signs its
bounded capability and records dispatch intent before a single Runtime call.
The versioned dispatcher and Runtime endpoint fix the selected deployment.
Uncertain calls enter bounded recovery, never automatic paid replay.

`execution_protocol.py` binds capability claims to the live B0 job and checks
the authoritative capability key, Gateway binding, membership and expiry.
`execution_tools.py` exposes one machine-only `execution` Gateway tool; it
never accepts caller-selected owner/job/attempt identifiers. Model discovery
excludes it. Capability/receipt-key versions join actual transactions.

`execution_workflow.py` claims once, heartbeats, transfers the normalized
manifest through hash/ETag-bound chunks and invokes the fixed Interpreter.
It records the observed session and signed context/analyze evidence, then
asks the ledger to finish. `SourcePublication` stages graph/index blobs and
combines graph, marker, artifact and terminal execution writes atomically.
The watchdog discovers due jobs; the IAM reconciler can complete existing
valid evidence without rerunning Interpreter. The source workflow calls no model.

The previous `authority.py`, `tools.py`, `workflow.py`, `dispatch.py` and `ac_*`
record protocol remain isolated compatibility adapters. They are not the new
Runtime/stack entrypoints. The files-based legacy ontology worker remains
explicitly offline and cannot execute a new reserved job.

## Identity and data boundaries

Runtime uses IAM inbound authentication and a dedicated Identity M2M workload
for the separate JWT Gateway. Its role can broker only that provider's machine
credential and read the provider's exact managed-secret ARN. The execution
capability supplies the separate project/actor/attempt authorization. Tokens
and secret values never enter logs, Memory, model prompts or stored artifacts.

Runtime has no workspace S3/DynamoDB or Lambda invocation grant. Interpreter
reads only its exact tool-archive version. The Gateway Lambda can stage
workspace outputs and publication, with explicit IAM Denies on intake
administration and key-registry partitions. The API can send to the exact queue
but cannot sign capabilities or invoke Runtime. Bank Gateway, private plane,
MyData and Registry capabilities are outside these roles.

Only real admitted code-collections feed the new path. Intake normalizes source
identifiers, paths and resolver/package names together, retains the private
mapping, and rechecks policy/provenance/reviewer/source/resolver authority.
Runtime receives only the admitted normalized payload. Graph file locations
refer to that admitted collection, not the original ZIP namespace.

The fixed Interpreter checks archive, parser, lock, architecture and Node major
before executing trusted analyzer code. Submitted source, package hooks and
arbitrary build commands are never executed. Browser/compiler adapters remain
available for later design integration; source success does not prove those gates.
Structured Memory stores hashes and keyed project/actor references, with no
extraction strategy. It cannot authorize a source or substitute for current checks.

## Validation

Use Python 3.12, the workspace requirements, `cryptography==50.0.1` for the
independent test RSA oracle, and the pinned infra dependencies:

```bash
PYTHONPATH=platform python -m pytest \
  platform/tests/test_execution_integration.py \
  platform/tests/test_execution_ledger.py \
  platform/tests/test_agentcore_capability.py \
  platform/tests/test_agentcore_identity.py \
  platform/tests/test_agentcore_authorization.py \
  platform/tests/test_agentcore_workflow.py \
  platform/tests/test_agentcore_adapters.py -q
cd platform/infra
npm ci
node --test test/ontology-stack.test.cjs
npx tsc --noEmit
```

Prepare Docker contexts using `prepare_context.py --output <empty-build-dir>
--axe <installed-axe-core-dir>`. It copies only the listed public module/tool
files, rejects symlinks in every input path component, and records code,
axe/license, Dockerfile and dependency-lock hashes. The image verifies those
copied bytes during its build. Its base digest and complete Python 3.12/arm64
wheel hashes are pinned. Customer archives,
credential files, private operation receipts and unrelated worktrees are not
Docker build inputs.

`infra/bin/ontology.ts` accepts the operator-owned `ontologyConfigFile` context.
Supply the isolated network, immutable archive descriptor and public code
context. Omitting workspace storage selects retained synthetic test stores.
When `workspaceTableName` reuses the main stack's table and intake is
configured, supply `intakeDeployment` (config property or the shared
`intakeDeployment` context) equal to the main stack's `INTAKE_DEPLOYMENT`
(its `intakeDeployment` context, default the main stack name); synthesis fails
otherwise rather than defaulting to this stack's own name. A disagreeing
property and context also fail.
Verify the target AWS account and role before deploying.
CloudFormation provisions the fixed capability/evidence/Gateway key-registry
records through a dedicated bootstrap Lambda. Only that IAM administrative role
can write the registry partition. It refuses key replacement or reactivation;
those require an explicit rotation procedure. Retained Authority versions and
Runtime endpoints let admitted jobs retain their configured version.

Keep live receipts outside Git. Verify actual Runtime/Identity/Gateway/Lambda/
Memory/Interpreter execution and negative cases; provisioning a READY resource
does not establish acceptance. Capture compiler and isolated-browser evidence
separately. Integration with the latest foundation, concurrency/cost admission,
versioned cohort activation, generation/release wiring, full negative acceptance
and rollback remain cutover gates. Do not enable the production default merely
because a synthetic source analysis passed.
