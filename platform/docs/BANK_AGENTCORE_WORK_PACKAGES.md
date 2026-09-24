# Existing bank AgentCore work packages

Date: 2026-09-22. Authority: the user's WP1–WP8 instructions.
Baseline: `8b9268be34546f67fd762cb9a39f7e7ab5f63c53`.
Status: WP1–WP3 implementation in progress; no deployment accepted by this document.

## Scope and order

Keep the existing bank IAM Gateway and Runtime in their current Tier 0/1
scenario scope, including the existing design-flow compatibility entry point.
They are separate from the §7-2 ontology extension. Prioritize security
boundaries, unused service/permission cleanup, actual AgentCore feature use,
then §7-2 expansion.

Memory, Identity and Observability must either be used with current evidence
or have their unused permissions removed. Resource names and installed
permissions alone do not establish use.

Each package needs separate offline (`O`) and live (`L`) evidence. A merged,
undeployed package is incomplete. Retain exact commits, image digests,
configuration, effective account/role, fixture hashes and observations.
Never put prompts, response bodies, credentials or original identities into
logs or review artifacts.

## PR 1: security — WP1, WP2, WP3

### WP1: authoritative, actor-bound sessions

- `agents/app.py` uses only the SDK Runtime context session ID. Payload
  `sessionId` is ignored; only `meta.ignoredPayloadSessionId=true` reports it.
  The LRU key is the context ID.
- Overlapping turns for one Runtime session fail with `409/session_busy`
  before constructing another agent. Completion, cancellation and stream closure
  release the session claim after cleanup.
  Design cancellation stops further generation/judging calls and joins the
  admitted worker before release. Failed Gateway cleanup quarantines the
  affected session; a new conversation is required. Active claims are released
  even on cleanup failure. Quarantine retains at most 20 IDs; overflow refuses
  further work until the Runtime process is recycled.
- `agentcore/runtime.py` sends `runtimeSessionId` in the API request and no
  payload `sessionId`, including through additional payload fields.
- `api/ws_handler.py` retains the Cognito-verified `sub`; it verifies the
  configured pool/client and token expiry and does not persist the access token.
- `api/handlers/agents.py` derives the Runtime ID from the verified subject,
  agent/version and client conversation ID. Responses retain the client ID so
  subsequent turns do not hash a previously derived Runtime ID again.

O: payload override rejection, missing Runtime context, actor separation,
same-actor continuity, foreign-pool/client rejection and expiry tests.
L: two invited Cognito users use the same client conversation string without
sharing history. Verify the deployed Runtime context behavior.

### WP2: control-plane IAM separation

WsFn receives only the listed Harness/Gateway/Registry read operations and
`InvokeHarness` scoped to the same-account, same-region `harness/bank_*` ARN
prefix, plus the existing exact bank Runtime invocation grant needed by Strands.
AgentCore create/update/delete/approval actions and Harness
`iam:PassRole` belong to AdminFn. Remove the three unused workload-token
permissions from the bank Runtime role.
Remove the Harness role's wildcard Memory data permissions and its obsolete
S3 Skill-prefix grant. Historical Skill bucket objects remain retained. New Harness
configurations explicitly set `memory.disabled`; omission does not prove the
service has disabled managed Memory. The deployed legacy Harness's Registry
version is deprecated; retain its historical Memory without execution-role
access. Do not delete its events or resource.
The user's explicit AdminFn allocation includes CreateMemory, GetMemory and
CreateWorkloadIdentity. These are administrative creation capabilities, not a
claim that bank Runtime Memory or outbound Identity is active. Runtime use and
dedicated resource grants still require WP5 and its independent live evidence.

Generic MCP/SKILL/CUSTOM drafts can be created and submitted by invited users.
Approval, rejection and deprecation require the IAM Admin operations
`inspect_registry_record` and `transition_registry_record`, with the exact
`expectedHash` returned by inspection. The generic Admin operation rejects AGENT
and internal-request records. The user UI displays administrator-required states
and cannot perform the former one-click approval/deprecation demo.
Generic IAM decisions also synchronize their typed AgentCore mirrors. A local
commit followed by a mirror failure reports `applied=true, completed=false`;
reinspect the current record and retry that exact hash to finish synchronization
without duplicating the decision audit.
Path-backed Skills have no approved inline AgentCore descriptor: their response
reports `UNSUPPORTED` and `retryable=false`, rather than promising that an identical
retry will synchronize them. Their local lifecycle decision remains explicit.
Generic decisions serialize status changes and mirroring with a DynamoDB lease;
completion rechecks the authoritative revision. Agent requests and IAM seeding
share a per-agent lease before changing their shared Harness.
Mirror creation and descriptor updates wait for the service's asynchronous work
to finish. New approval/rejection decisions submit DRAFT records for manual review
before applying the decision. AdminFn reads only the configured Registry's approval
configuration; an unready Registry is retryable, while auto-approval is unsupported
for this flow.
The remote name binds the local name and immutable version. The service's
`recordVersion` is a separate optimistic-lock revision, initialized to `1` and
checked during updates. Legacy active mirrors are retired before clean replacements.
Deprecated service records are immutable audit archives: their inactive status is
verified and retained, with `archived=true` and an explicit `metadataCurrent` result.
Archive retention does not claim that historical descriptor content was rewritten.
The remote service permits deprecation from any state, including DRAFT; this is
separate from the platform's local DRAFT-to-approval lifecycle.
An active typed legacy record without a verifiable local identity blocks
synchronization with `RECONCILIATION_REQUIRED`; a new mirror cannot hide that
unresolved record or make the decision report completion. An IAM curator must
inspect and retire the legacy record before retrying the exact local decision.
Consumer discovery re-reads GSI candidates from the authoritative table; a stale
APPROVED index image cannot return a deprecated or removed record. New approvals
can still await GSI propagation, which is a completeness delay, not permission
to reuse revoked content.

User agent creation saves a local pending specification. Agent transitions
save `AGENT_ADMIN_REQUEST` records. The generic Registry route cannot bypass
this request path for Agent records. WsFn cannot invoke AdminFn.
The generic transition transaction also binds the exact record type, subtype,
payload and state revision inspected by its authorization check; a concurrent
creation or reclassification cannot turn it into an Agent approval.

Request records, payloads and audit entries are excluded from user Registry
listing, get, search, version and consumer APIs. Their namespace is reserved.
Agent discovery omits operational system prompts and embedded Skill content.
Agent audit responses retain transition evidence and hashes rather than private
request reasons. Record creation/status changes and their audit inserts commit
in one DynamoDB transaction; audit failure cannot leave an approval behind.
The IAM-only `apply_agent_request` Admin operation validates the exact
requested record, provisions the approved Harness, applies its Registry
transition and mirrors it. Changed specifications and conflicting existing
Harness configurations are rejected. Long-term Harness memory is not enabled
by these requests without its separate privacy/extraction review.
Custom Harness Skill selections must be APPROVED; server-derived bindings fix
the Registry revision and bundled Markdown hash. Approval rechecks those
bindings and embeds the exact inspected text into the Harness system prompt,
without a mutable S3 Skill prefix. All Harness creation paths, including IAM
seeding, require explicit bindings; even an empty selection uses `[]`. Invocation
rechecks the approval bindings. Creation explicitly selects `converse_stream` to
match the managed service protocol returned by GetHarness. Existing Agent mirror
descriptors are replaced with metadata and verified before status synchronization;
completion is verified again afterward. Remote names use the complete original
name hash. Legacy CUSTOM records are adopted only when their embedded original
name/version match; ambiguous legacy typed records are not silently adopted.
Descriptor errors never downgrade MCP/SKILL to CUSTOM or truncate approved text.
Every Harness invocation, including legacy approved custom records, also checks
the actual READY service configuration against the reviewed fields. A missing
binding, wildcard tool, changed limit, or enabled Memory blocks invocation until
IAM reconciliation. Runtime scenario records retain their existing dispatch.
Missing Runtime Skill files also block execution before a model call.
If local approval succeeds but the metadata mirror fails, the Admin response
reports `applied=true, completed=false`, the actual local state and mirror
status. The request stays pending until a successful retry.
For an unapproved orphan, the IAM-only `reconcile_agent_request` operation
requires `expectedHarnessHash` from the IAM `inspect_agent_request` operation.
It refuses any active approved consumer and rechecks the request before updating
the existing Harness to the exact requested configuration, including iteration,
token and timeout limits. Unexpected execution extensions require separate IAM
reconciliation. It never deletes a Harness. IAM seeding compares the latest immutable Registry
version even when its runtime label is unchanged. Specification drift creates a
new version. Service drift leaves it pending and returns an administration request
for the existing inspect/reconcile flow. Skill bindings are rechecked after service
creation and immediately before approval; old approved consumers must be deprecated
before their shared Harness can be changed.
Seeding stages the pending record and request before provisioning, rejects a
generic record occupying a built-in Agent name, and retires superseded approved
versions. Runtime approvals include the complete specification/Skill source hash;
the invocation forwards it and the container rejects a different bundled source.

O: synthesized policy checks prove no AgentCore control actions or PassRole on
WsFn; tests prove no control call on user routes and cover administrative
failure/retry. L: a request signed by the deployed WsFn role receives
AccessDenied for DeleteHarness. Use a nonexistent synthetic target so an
unexpected grant cannot delete a real resource.

### WP3: closed tool access and bounded errors

Remove production Gateway DEBUG exception output. Empty or wildcard
`allowedTools` do not grant all tools. In the Strands Runtime, ambiguous bare names fail closed and missing configured tools produce
`toolsMissing`, a failure before model invocation, and a visible UI badge.
Harness creation requires a configured Gateway and explicit tools; its managed
execution reports bounded service failures and does not provide the Runtime's
client-side `toolsMissing` preflight. Do not claim that unimplemented equivalence.
Runtime/Harness upstream exception bodies are not returned to the user. Streamed
tool arguments expose size and identity metadata only. Requests exceeding the
handler/container message limits are rejected rather than truncated, and a stream
without its terminal result is incomplete and cannot replace session history.
Managed Harness turns record only opaque session/turn identifiers and phase in
the existing Registry table, outside discovery indexes. An unfinished or failed
turn stays non-reusable across WsFn instances; the response requests a new client
conversation. No prompt, response or managed history is stored in that record.
Unknown Harness execution fields are rejected; only explicit service metadata is
excluded from configuration comparisons.
Harness input/system text is inspected with rules and strict, coverage-verified
Guardrails before managed execution. Rule hits reject locally; every admitted
input has both detectors. Missing Guardrail configuration or failed coverage
blocks Harness approval/invocation. The Admin Lambda has the same exact Guardrail
and APAC-profile grants needed to inspect system/Skill text. The bank
Gateway Lambda independently measures and scans every outgoing tool result,
blocks residual identifiers or failed inspection, and returns bounded errors.
Every admitted Gateway payload requires both rules and the configured Guardrail's
independent sensitive-information check, with complete coverage. A rules hit
blocks locally without sending the identified content to Guardrails; it records
rules-only rejection evidence, never a completed Guardrail check. Missing
configuration, incomplete coverage, service errors and inputs/results above the
Gateway verifier's 20,000-character limit block the tool boundary. Inspection
sends the complete serialized payload in one request; it never truncates or
splits context. The shared S2 verifier retains its separate 4,000-character bound.
The Tools Lambda can apply only that Guardrail and its existing APAC profile
in the six documented destination Regions from Seoul. Passport-format variants
have an independent Guardrail regex. A new immutable policy version is selected by
its consumers, while the prior policy version remains available for rollback.
The Runtime pre-model hook independently verifies the complete collected text,
including system content, message history and tool specifications, before each
model call. Its explicit 100,000-character verification bound never truncates;
opaque media requires a separate boundary and is rejected. The Gateway/Harness
retain 20,000-character bounds and the shared default remains 4,000. Missing,
failed or incomplete independent verification blocks the call.
A top-level Guardrail policy intervention also blocks when no PII was detected;
the refusal retains zero detected PII rather than inventing an entity.
Gateway/Harness refuse all identifier classes, including EMAIL and KR_PASSPORT;
raw email customer references are not admitted through this Tier 0/1 path.
The existing sample lookup uses its configured synthetic default. Authenticated
customer MyData intake remains the separate S2 privacy workflow.
The design-flow compatibility adapter emits measured boundary events for successful
model attempts and local privacy refusals, retaining the refusal classification.
It uses the approved Runtime whenever that Runtime is configured. Bounded failure
metadata retains `blocked`, `stopReason=gate_refused`, and code `422`, including
policy denials with zero detected PII.
Generation fails with configuration status `503` when no Runtime is configured;
offline catalogue and PRD preview remain available without model calls.
Each text frame identifies its model-call sequence. Runtime/design relays require
the matching accepted boundary, and a tool result invalidates the previous call's
evidence. Design measurements are queued before that call's text and final result.
It also inspects arguments before executing a tool, including nested model
adapter calls inside tools.
Gate and document results are projected from their expected response schemas;
nested upstream diagnostic bodies are not returned. Gate failure verdicts,
counts, rule identifiers and compiler codes remain available. Accessibility
incomplete checks remain visible and prevent aggregate success; failed Registry
component approval also makes the aggregate false.
The managed Harness loop does not run the Strands pre-model hook; its input
trace must not be presented as inspection of the complete managed history.

O: empty/wildcard allowlist rejection, missing-tool behavior and error-body
exclusion tests. L: an authenticated invalid-tool request contains no Lambda
stack trace or upstream body. Confirm the actual Gateway configuration.

## PR 2: actual platform features — WP5, WP6

### WP5: short-term AgentCore Memory

Create a dedicated bank Memory with 30-day event retention and no extraction
strategies. Grant only its CreateEvent, ListEvents, GetEvent, DeleteEvent and
ListSessions operations. No RetrieveMemoryRecords grant until reviewed
long-term strategies are enabled.

Replace process-local LRU history with the SDK's Strands Memory session
manager after verifying the installed SDK interface. Use a managed-HMAC actor
pseudonym supplied by WsFn and WP1's Runtime session ID. Verify pre-model
refusal ordering and delete any already-written refused-turn event.
SEMANTIC/USER_PREFERENCE strategies remain outside this package.

O: session-manager injection and refused-event cleanup.
L: continuity after a microVM restart, no cross-actor retrieval, and no
refused turn in ListEvents. Add a service-spend alarm.

### WP6: content-free observability

Pin ADOT, use its instrumentation launcher, and verify the current Strands/
ADOT content-capture controls before enabling them. Keep prompt/response
capture disabled. Retain only actually used logs/X-Ray permissions.
Propagate the OTEL trace ID into stage evidence for UI/CloudWatch correlation.

L: a current Runtime trace appears in CloudWatch GenAI Observability, and
sampled span attributes contain no prompt or response originals.

## PR 3: measured network change — WP4

Measure Seoul availability and private DNS/routing for the actual Runtime,
Gateway, Bedrock, ECR, Logs, STS and S3 endpoints. Verify the current
CloudFormation/SDK VPC shape rather than copying an assumed property layout.
Reuse the existing isolated subnets when the required paths work.

L: external HTTP fails from the Runtime microVM while Bedrock and Gateway
succeed; record cold-start measurements and the tested profile.
`demo/uiux-studio` remains PUBLIC and explicitly labelled demo-only.

No temporary NAT route is approved by this plan. If Gateway PrivateLink is
unavailable, prepare a concrete bounded-egress alternative and request the
user's decision. Do not assume an AWS-managed prefix list exists.

## PR 4: cleanup and current evidence — WP7, WP8

### WP7: Registry configuration and residency

Inject Registry ID/region only through stack configuration. Remove fallback
IDs/regions from Python; an unconfigured mirror stays disabled and is shown
as such. Verify Seoul Agent Registry availability.

If another region remains necessary, record the named residency approver and
decision before enabling that path. Restrict mirror payloads to reviewed
metadata rather than full CUSTOM records. The residency approver and any new
cross-region authorization remain undecided.
AWS also documents migration from the legacy `bedrock-agentcore` Registry APIs
to `agent-registry`, with a 2026-09-17 support cutoff for the old namespace.
WP7 must migrate SDK calls, IAM actions and resource ARNs. A successful compatibility
probe of the old namespace is dated evidence, not a claim of continued support.

### WP8: current documentation

Replace historical smoke claims with tests of the accepted commit: UTC date,
image digest, actual tool counts and token usage. Update CONTRACTS.md's
before/after inventory. Preserve AGENTCORE_CONTRACT.md's §7-2 requirements;
add only a reference beside its bank-IAM-Gateway statement after WP2/WP3 are
actually applied.

## Evidence ledger

| Package | Offline | Live | PR/deployment |
|---|---|---|---|
| WP1 | IN_PROGRESS | NOT_RUN | NOT_DEPLOYED |
| WP2 | IN_PROGRESS | NOT_RUN | NOT_DEPLOYED |
| WP3 | IN_PROGRESS | NOT_RUN | NOT_DEPLOYED |
| WP4 | NOT_RUN | NOT_RUN | NOT_DEPLOYED |
| WP5 | NOT_RUN | NOT_RUN | NOT_DEPLOYED |
| WP6 | NOT_RUN | NOT_RUN | NOT_DEPLOYED |
| WP7 | NOT_RUN | NOT_RUN | NOT_DEPLOYED |
| WP8 | NOT_RUN | NOT_RUN | NOT_DEPLOYED |
