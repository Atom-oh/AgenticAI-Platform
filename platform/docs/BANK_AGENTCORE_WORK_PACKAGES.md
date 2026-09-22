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
prefix. AgentCore create/update/delete/approval actions and Harness
`iam:PassRole` belong to AdminFn. Remove the three unused workload-token
permissions from the bank Runtime role.
The user's explicit AdminFn allocation includes CreateMemory, GetMemory and
CreateWorkloadIdentity. These are administrative creation capabilities, not a
claim that bank Runtime Memory or outbound Identity is active. Runtime use and
dedicated resource grants still require WP5 and its independent live evidence.

User agent creation saves a local pending specification. Agent transitions
save `AGENT_ADMIN_REQUEST` records. The generic Registry route cannot bypass
this request path for Agent records. WsFn cannot invoke AdminFn.

The IAM-only `apply_agent_request` Admin operation validates the exact
requested record, provisions the approved Harness, applies its Registry
transition and mirrors it. Changed specifications and conflicting existing
Harness configurations are rejected. Long-term Harness memory is not enabled
by these requests without its separate privacy/extraction review.
For an unapproved orphan, the IAM-only `reconcile_agent_request` operation
requires `expectedHarnessHash` from `agentcore.administration.harness_fingerprint`.
It refuses any active approved consumer and rechecks the request before updating
the existing Harness to the exact requested configuration. It never deletes it.

O: synthesized policy checks prove no AgentCore control actions or PassRole on
WsFn; tests prove no control call on user routes and cover administrative
failure/retry. L: a request signed by the deployed WsFn role receives
AccessDenied for DeleteHarness. Use a nonexistent synthetic target so an
unexpected grant cannot delete a real resource.

### WP3: closed tool access and bounded errors

Remove production Gateway DEBUG exception output. Empty or wildcard
`allowedTools` do not grant all tools. In the Strands Runtime, missing configured tools produce
`toolsMissing`, a failure before model invocation, and a visible UI badge.
Harness creation requires a configured Gateway and explicit tools; its managed
execution reports bounded service failures and does not provide the Runtime's
client-side `toolsMissing` preflight. Do not claim that unimplemented equivalence.
Runtime/Harness upstream exception bodies are not returned to the user.
Harness input/system text receives the same independent rule inspection
before a managed model request.

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
