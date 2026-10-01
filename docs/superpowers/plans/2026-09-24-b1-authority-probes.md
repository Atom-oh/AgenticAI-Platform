# B1 — Execution Authority and Live Capability Probes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Steps marked **live** act on AWS and require the live-step rule in the roadmap.

**Goal:**
- Implement the durable execution authority: the capability issuer, KMS keys and key registry.
- Deploy the reviewed B0 writer guards.
- Run the Phase 0 capability gates as disposable probes: Runtime/ledger, Code Interpreter, Browser, Identity/Gateway, Memory, Models and private admission.
- Record the stop/go evidence for each gate and retire the probe resources.

**Architecture:** Nothing here becomes a production executor.

- **Issuer:** `workspace/execution_authority.py`, an IAM-only Lambda. It signs bounded capabilities with a dedicated asymmetric KMS key. A separate KMS key signs observation receipts.
- **Key registry:** a closed record set in the workspace table under the `execution:keys` partition. It supports rotation and revocation.
- **Verifier:** `KmsVerifier` is registered with the B0 ledger through `register_verifier`, the only way `Ledger.production` accepts a verifier.
- **Probe stack:** probes run in a **separate disposable CDK stack**, `BankPlatformProbes`. It has an isolated DynamoDB table and an isolated S3 bucket, and never touches the production workspace table. The one exception is the narrowly scoped deployed-guard control record in Task P2.
- **Receipts:** each probe script writes a private JSON receipt. The results go into a private dated assessment copied from `platform/docs/ONTOLOGY_AGENTCORE_VALIDATION.md`.

**Tech Stack:**
- Python 3.12 with boto3 and the `bedrock-agentcore` SDK already pinned in `agents/requirements.txt` (`bedrock-agentcore==1.22.0`)
- CDK `aws-cdk-lib/aws-bedrockagentcore` L1 constructs: `CfnRuntime`, `CfnCodeInterpreterCustom`, `CfnBrowserCustom`, `CfnMemory`, `CfnWorkloadIdentity`, `CfnGateway`, `CfnGatewayTarget`
- KMS

**Roadmap:** unit 5. It depends on B0 ledger (merged, with deployable guards) and B0 intake, which provides the probe-scope policy and admission decisions for the synthetic fixtures.

**Owners** (named in the private assessment before any gate runs, per `AGENTCORE_CONTRACT.md:734`):

| Owner | Gates or work |
|---|---|
| platform-runtime maintainer | Runtime/ledger, Interpreter, and the single deployment owner |
| verifier maintainer | Browser |
| platform-identity maintainer | Identity/Gateway, keys |
| ontology maintainer | Memory |
| model-boundary maintainer | Models |
| private-intake maintainer | Admission |
| platform security operator | Policy, provenance and grant writes |

**Owning contracts:**
- `AGENTCORE_CONTRACT.md` `execution-capability/1` and Identity topology (L195-283)
- `ontology-tools/1` (L284-332)
- Memory lifecycle (L333-356)
- Phase 0 gates (L607-742)

**Acceptance and gates:** G0-RUNTIME, G0-INTERPRETER, G0-BROWSER, G0-IDENTITY, G0-MEMORY, G0-MODELS and G0-ADMISSION. The L portions of RUN-01–05, AUTH-02–06 and AUTH-09, INT-*, BROW-*, MEM-01–03 and GEN-02/03 are recorded as probe evidence only. Probe permission is not full intake completion.

## Global Constraints

- The roadmap constraints apply, especially the **live-step rule**. For every live step:
  1. Run `aws sts get-caller-identity` and confirm that the account and ARN match the deployment target recorded in the private assessment.
  2. Confirm that you are the single deployment owner.
  3. Get explicit user confirmation.

  A mismatch stops the step. There is no fallback to another profile or account.
- Probes transfer only server-registered synthetic fixtures with exact hashes, under a versioned **probe-scope** admission policy (B0 intake). No customer material is used in any probe.
- The probe stack uses no NAT gateway, no public IP and no public listener. Browser sessions run in an isolated VPC with no internet egress.
- Receipts contain metadata only: account/region, service and API versions, resource IDs, hashes, timings and outcomes. They contain no source text, prompts or credentials, and they are stored outside Git.
- Every probe resource is torn down after its stop/go record. A teardown is complete only after a genuine not-found result, not while the resource is `DELETING`.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/workspace/execution_authority.py` | Capability claims, issuance, `KmsVerifier`, key registry records |
| `platform/workspace/storage.py` (modify) | Kind `exec_key` |
| `platform/infra/lib/probes.ts` | `BankPlatformProbes` disposable stack |
| `platform/infra/bin/*.ts` (modify) | Register the probe stack behind `-c probes=true` |
| `platform/probes/common.py` | Receipt writer, account check, fixture registry, canary helpers |
| `platform/probes/{runtime,interpreter,browser,identity,memory,models,admission}_probe.py` | One script per gate |
| `platform/probes/runtime_app/` | Probe Runtime container: `app.py` plus `engine/gate.py` and ledger tool client |
| `platform/probes/build_tool_archive.sh` | Platform-matched react-kit/source-analyzer archive with manifest and hash |
| `platform/tests/test_execution_authority.py` | Offline tests with a fake KMS client |

---

### Task P1: Execution authority and key registry (offline, then live)

**Interfaces:**

**Capability claims (`execution-capability/1`):**

```python
{"v": 1, "iss": "bank-platform-execution-authority", "aud": "ontology-gateway", "kid": key_id,
 "actor": actor, "project": project_id, "executionId": job_id, "attemptId": attempt_id, "fence": fence,
 "sessionId": runtime_session_id, "workload": workload_identity_name, "operations": [...], "resources": [...],
 "iat": now, "exp": min(job.authorizationExpiresAt, job.deadlineAt, now + 15 * 60_000), "nonce": hex}
```

- **Expiry and lease are independent (review round 2, N8).** `exp` is bounded by user authorization, the execution deadline and 15 minutes (`AGENTCORE_CONTRACT.md:208-210`). It is **not** bounded by the 90-second lease.
- The lease is checked on every protected call by the ledger (`_current`): a lost lease rejects the call even with an unexpired token.
- There is no in-session renewal (`AGENTCORE_CONTRACT.md:282`).
- The Runtime heartbeats every 30 s from a dedicated thread, including during blocking Interpreter, Browser and model calls. Its test asserts heartbeats continue across a simulated 150-second Interpreter call and that the capability stays valid.

- The token is compact JWS, ES256, signed through KMS `Sign` (`ECDSA_SHA_256`).

**Issuer:**
- `issue(ledger, owner, job_id, attempt)` is callable only from the IAM-only dispatcher Lambda.
- It re-reads the job through the ledger and requires the attempt to be current (id, fence, lease).
- It derives `operations`/`resources` from `OPERATIONS[job.operation]` and the job manifest, never from request arguments.

**`KmsVerifier`:**
- It verifies signatures with the cached public key of a **registered** key whose state permits **verification** (review round 5, Y7; `AGENTCORE_CONTRACT.md:278-281`). Signing and verification eligibility are separate:
  - `active`: may sign and verify.
  - `retired`: **may not sign**, but verifies tokens and receipts whose `iat` falls before `retiredAt`, until their `exp`. Observation receipts carry signed `iat`/`exp` (B0 ledger receipt schema v1). A receipt rotation test covers an observation key that retires mid-execution, where earlier receipts still verify at `finish`. It is also retained for historical receipt verification.
  - `revoked`: verifies nothing current. It is checked authoritatively on each protected operation, not from cache. Receipts signed with it are marked compromised, which invalidates readiness.
  - Tests cover:
    - rotation from active → retired, where an outstanding capability still verifies until `exp` and new issuance uses the new key
    - a retired key signing → refused
    - revocation → immediate rejection, even with a cached public key
- It checks `kid ∈ registry`, `aud`, `exp` and the claims-to-ledger match. The ledger enforces the claims match on use.

**Key records:**
- `exec_key {id: kid, purpose: "capability"|"observation", kmsKeyArn, algorithm: "ES256", status: active|retired|revoked, activatedAt, retiredAt?}`
- Two purposes, two distinct KMS keys. A capability key can never verify an observation receipt, and the reverse is also true.

**Registration:**
- `execution_ledger.register_verifier(KmsVerifier)` runs at module import.
- A test verifier stays offline-only (B0).

**Steps:**

- [ ] **Step 1: Offline tests** with a fake KMS client that signs with a local P-256 key (`cryptography`; add it to `workspace/requirements.txt` if absent, pinned):
  - issue/verify round trip
  - swapped capability, where A's token is used for B's execution → the ledger rejects it via the claims mismatch
  - altered claim → signature failure
  - expired → rejected
  - revoked `kid` → rejected
  - an observation key cannot verify a capability
  - a non-dispatcher caller cannot issue (`PermissionError`)
- [ ] **Step 2–4:** Fail → implement → pass. Commit: `git commit -m "feat(execution): KMS-signed execution capabilities and key registry"`
- [ ] **Step 5 (live, disposable):** Deploy the two KMS keys in `BankPlatformProbes` with key policies that meet all of these conditions:
  - only the probe issuer role may `Sign` with the capability key
  - only the probe observer role may `Sign` with the observation key
  - verifiers get `GetPublicKey` only

  Record the key ARNs in the private assessment.

### Task P2: Deployed B0 guard verification (RUN-01 live, G0-RUNTIME prerequisite)

- [ ] **Step 1 (live):** The deployment owner deploys the merged B0 revision of the workspace Worker and Api Lambdas through the normal `platform/deploy.sh` path, under the AGENTS.md single-owner rule. Record these revision hashes:
  - the image digest for the `DesignerWorkspace/Worker` DockerImageFunction
  - the code SHA-256 for `DesignerWorkspace/Api`
- [ ] **Step 2 (live):** Match the deployed revisions to the reviewed guard commit, using the image digest ↔ build provenance.
- [ ] **Step 3 (live; review rounds 2 and 3, N20).** Set up the probe project first. The deployment owner creates a normal authorized project named "probe control" through `POST /projects` with the owner's invited account. That account is created with `AdminCreateUser`, and its credentials are never written to files. Use the **returned** project id: the API generates it (`collaboration.py:356`). This gives the probe a real project record and membership for `project:<returned id>`.

  Seed two records through the IAM-only `ProbeIssuerFn` `seed_control` operation. It exists only in the probe stack. It writes with a **raw DynamoDB `PutItem`**, not through `Storage`, because `Storage._prepare` overwrites `updatedAt` with the current clock (`storage.py:140-141`) and a backdated timestamp would be lost. The raw item uses the same `pk`/`sk`/marshalling as `Storage._key`. A probe unit test asserts that the item read back through `Storage.get` has the backdated `updatedAt`.
  - **Reserved control:** `{task: "agentcore-execution", executionSchemaVersion: 1, status: "running", updatedAt: now - 17 min}`
  - **Legacy control:** `{task: "run", status: "running", updatedAt: now - 17 min}`

  Both are deliberately stale, so the 16-minute expiry path (`http.py:270`) performs its guarded write attempt. Then:
  1. Invoke the Worker for each record.
  2. Call `GET /jobs/<id>` for each.
  3. Check the expected outcomes:
     - the reserved control is unchanged (same version) and has an `exec_report` with reason `reserved-item` or `reserved-record`. The expiry write carries the stored markers in its item, so the chokepoint reports `reserved-item` (review round 4, N20)
     - the legacy control becomes `failed` with `job-timeout`, proving legacy behavior is intact
  4. Delete both records and the probe project, then record the outcomes.
- [ ] **Step 4:** Record the G0-RUNTIME prerequisite stop/go decision. General shared-table probes stay blocked unless this passes. All later probes use the probe stack's own table regardless.

### Task P3: Probe stack

**Interfaces:** `BankPlatformProbes` (CDK, `-c probes=true`) contains the following, all with metadata-only log configuration:

| Resource | Configuration |
|---|---|
| Disposable DynamoDB table | Same key schema as the workspace table; SSE; TTL `ttl` |
| Disposable S3 bucket (tool archive) | Versioned; block public access; bucket-owner-enforced |
| Disposable S3 bucket (synthetic fixtures) | Same as the tool-archive bucket |
| `CfnRuntime` | Probe container `platform/probes/runtime_app`, which includes `engine/gate.py`, `engine/model_catalog.py`, the boundary adapter and a ledger tool client. Network `PUBLIC` is allowed only if Phase 0 confirms Runtime requires it; otherwise VPC. Record the choice. |
| `CfnCodeInterpreterCustom` | Network mode `SANDBOX`. Execution role: `s3:GetObject` and `s3:GetObjectVersion` on the archive object ARN only, with the condition `StringEquals s3:VersionId = <pinned version id>`. A request that names a `VersionId` requires `GetObjectVersion` (review round 26, AT1). No source bucket, model or table permission. |
| `CfnBrowserCustom` | VPC mode in an isolated-subnet VPC (no NAT/IGW, security group egress closed except the required AgentCore endpoints through VPC endpoints). No execution role permissions beyond logs. |
| `CfnMemory` | No memory strategies (no long-term extraction); event expiry 30 days |
| `CfnWorkloadIdentity` | Dedicated to the probe Runtime |
| Dedicated Cognito M2M app client | Resource server `ontology-gateway`, scope `ontology-gateway/execute`, `generateSecret: true`. It lives on a **new user pool with `selfSignUpEnabled: false` and `AdminCreateUserConfig.AllowAdminCreateUserOnly: true`**, used only for machine clients. |
| OAuth2 credential provider | For the M2M client |
| `CfnGateway` | Authorizer `CUSTOM_JWT` with the dedicated pool issuer and audience; protocol MCP |
| `CfnGatewayTarget` | Lambda `ProbeToolsFn`: verifies the capability through `KmsVerifier` and calls the ledger `tool()` facade against the disposable table |
| IAM-only Lambdas | `ProbeIssuerFn` (dispatcher and issuer) and `ProbeReconcilerFn`, with no API routes |

- The existing bank `ToolsGateway`/`ToolsTarget` (`stack.ts:268-289`) is not referenced or modified (AUTH-06).
- `check_infra`-style assertions for the probe template are added to `workspace/check_infra.py` as `audit_probe_template`:
  - no NAT gateway or IGW
  - no public listener
  - no `*` principal
  - the Interpreter role limited to `GetObject`/`GetObjectVersion` on the archive object with the pinned `s3:VersionId` condition
  - the Browser role limited to logs
  - Memory with an empty `memoryStrategies`
  - Cognito self sign-up false
  - the Gateway authorizer is `CUSTOM_JWT`

- [ ] **Step 1:** Write the stack and `audit_probe_template` tests against a synthesized template (offline `cdk synth` as in CLAUDE.md, adding `-c probes=true`). Commit.
- [ ] **Step 2 (live):** Run `cdk deploy BankPlatformProbes -c probes=true`, and record the resource ARNs.

### Task P4: Tool archive for the measured Interpreter platform

- [ ] **Step 1 (live):** Run the Interpreter probe's `platform` stage: start a session, then read `uname -m`, the OS release and `node --version`. Record them.
- [ ] **Step 2:** Run `build_tool_archive.sh <arch> <node-major>`. It builds `react-kit` and `source-analyzer` with `npm ci --ignore-scripts` in a container matching the measured platform, so the correct native `esbuild` binary is included. It then writes `tool-archive.tar.zst` with `manifest.json`, which records:
  - each file's SHA-256
  - `catalogHash` (`react-kit/manifest.cjs`)
  - `package-lock` hashes

  Record the archive hash. The limits are 64 MiB compressed and 256 MiB expanded.
- [ ] **Step 3 (live):** Upload the archive to the tool-archive bucket at a new versioned key, and record the version id.

### Task P5: Gate probes (live)

Each script takes these arguments:

```
--account <expected> --region <r> --stack-outputs <json> --out <private receipt path>
```

It exits with a non-zero status on any failed measurable criterion. The criteria follow `AGENTCORE_CONTRACT.md:701-710`.

| Script | Measured criteria |
|---|---|
| `interpreter_probe.py` | - Fetch the exact-version archive and verify its hash within 120 s.<br>- Compile the synthetic react-kit project from the engine seed without package install in ≤ 60 s.<br>- Two identical rebuilds give equal `sourceHash`/`bundleHash`.<br>- An invalid source fails the `types` gate.<br>- An attempt to read the fixture bucket is denied.<br>- A source/token canary is absent from session logs and configured exporters. |
| `browser_probe.py` | - Authenticated automation connects; the exact bundle is served by request interception only.<br>- An external HTTP and a WebSocket attempt are blocked.<br>- The `workspace/rules.py` contract runs, with axe results, Korean text rendered with a font present and screenshots, in ≤ 120 s.<br>- Canaries are absent. |
| `identity_probe.py` | - A brokered credential is accepted for audience `ontology-gateway`.<br>- A current-attempt capability invokes `ProbeToolsFn`.<br>- Denied: a swapped capability, an expired one, a revoked `kid`, a missing credential and a spoofed tool context.<br>- Capability canaries are absent from Gateway, Identity and Lambda logs and spans. |
| `runtime_probe.py` | Against the disposable table:<br>- Legacy/new writer rejection.<br>- A duplicate dispatch results in one attempt.<br>- A Runtime crash leaves verified partial evidence.<br>- A cancellation fences a late publication.<br>- Profile timing is measured: heartbeat, lease, deadline.<br>- Metadata-only telemetry, with a prompt canary absent. |
| `memory_probe.py` | - Structured events are stored and retrieved with no extraction.<br>- Another actor's namespace is denied.<br>- A stale source reference is excluded after revalidation.<br>- Deletion is recorded. |
| `models_probe.py` | - For each requested model in `engine/model_catalog.py` `_MODELS` plus any user-requested model, resolve the provider ID and inference profile, run one boundary-routed synthetic call inside the probe Runtime through `engine/gate.py`, and record quota and timing.<br>- A model that cannot be verified is recorded `unavailable`. It is never substituted. |
| `admission_probe.py` | - The probe-scope policy, provenance and reviewer bindings are current.<br>- A source-readable but inadmissible synthetic input with PII is denied.<br>- A policy or grant write through the workspace API is denied.<br>- The admin write succeeds only through the IAM invoke. |

- [ ] **Step 1:** Write each script with a `--dry-run` mode that validates its inputs offline. Unit-test the receipt shape and the account-check refusal. Commit.
- [ ] **Step 2 (live):** Run each probe. Save the receipts privately. For each gate, record in the private assessment:
  - configured limits
  - fixture hashes
  - observed measurements
  - negative-case outcomes
  - stop/go decision
  - named reviewer
- [ ] **Step 3:** If any gate fails, record the failure decision from the contract table, for example "Stop; revise archive/runtime/logging strategy", and **do not** start B2 for the dependent capability.

### Task P6: Teardown and B1 exit record

- [ ] **Step 1 (live):** Run `cdk destroy BankPlatformProbes`. Poll every resource until it returns not-found. Check the Runtime, Interpreter, Browser, Memory, Gateway, Identity, user pool, buckets (emptied first) and table. KMS keys are scheduled for deletion with the 30-day window unless B2 adopts them; record the choice.
- [ ] **Step 2:** Complete the B1 stop/go record in the private assessment: one row per gate with PASS, FAIL or BLOCKED and its evidence reference. Update the `platform/docs/ARCHITECTURE.md` B1 row status with a date and "probe evidence recorded privately". Do not claim service readiness.
- [ ] **Step 3:** Open the B1 PR (code: authority, probe stack, scripts). It passes offline tests and CI, and its body lists each gate result by ID without private receipts.
