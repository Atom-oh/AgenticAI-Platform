# Ontology AgentCore completion validation

Date: 2026-09-22. Updated: 2026-09-23 after Kiro document review.
Status: acceptance specification; implementation acceptance execution not started.
All case and gate results start as `NOT_RUN`. This document defines how to
assess the completed implementation; it does not certify the current code or
deployment.

## Scope and authority

Validate the complete workflow in [the implementation plan](ONTOLOGY_AGENTCORE_PLAN.md):
private intake, canonical ontology, dependency analysis, AgentCore execution,
React generation, verification, human review, handoff and staged rollout.
Use [SPEC §7-2](../../SPEC.md), the
[execution contract](../workspace/AGENTCORE_CONTRACT.md), the
[ontology implementation contract](../workspace/ONTOLOGY_CONTRACT.md), and the
[React contract](../workspace/REACT_CONTRACT.md) as the acceptance authority.
The dated plan supplies sequencing, not an exemption from those contracts.
The subordinate [architecture](ARCHITECTURE.md) assigns delivery ownership.

The unit of acceptance is an exact implementation and deployment revision,
configuration, source snapshot and tool profile. A local test, READY resource,
mock service, screenshot, successful merge or configured model name alone
cannot establish an operating AgentCore workflow.

Publishing-package readiness and frontend acceptance are separate outcomes.
Customer API/authentication integration and customer deployment are outside
this publishing acceptance; report them separately as unresolved unless
independently verified and authorized. They must not be represented as complete.

## How to run and record the assessment

1. Copy the case/gate tables and result template into a private dated validation
   record. The `Result` columns here are template initial values, not a public
   execution ledger. Name an executor and independent reviewer for each gate.
2. Freeze the scope, candidate HEAD, contract/blob hashes, fixture oracle,
   deployment configuration and enabled models. Inspect the current code and CI;
   do not carry forward historical test totals.
3. Execute the offline cases and Phase 0 probes. Save both positive and negative
   evidence. Stop dependent integration when its gate fails or is unmeasured.
4. Execute the vertical scenario and failure matrix in private staging, then the
   limited synthetic production cohort. Use actual authenticated service calls
   for every row marked `L`.
5. Review the observations against the expected results, record defects, repair
   and apply the evidence-freshness rules below before rerunning cases. Recheck
   shared invariants and required CI after fixes.
6. Complete rollback validation and latest-HEAD review. Determine each completion
   level separately; retain every unimplemented, blocked or untested item.

`O` means reproducible offline tests/source inspection. `L` means authenticated
live service/deployment evidence. `U` means observed UI and downloaded bytes.
`O+L`, for example, requires both; one does not substitute for the other.
The full-plan ledger contains 57 required cases. A case passes only when all
its listed assertions and required evidence modes pass. An offline-only
assessment uses the fixed level sets below and records `O` results separately;
it does not promote an `O+L` case to overall `PASS`.

| Result | Meaning |
|---|---|
| `NOT_RUN` | No valid execution evidence for this candidate and scope |
| `PASS` | Expected positive and negative outcomes observed, evidence reviewed |
| `FAIL` | Observed behavior violates an acceptance assertion |
| `BLOCKED` | Missing implementation, dependency, access, model or service prevents execution |
| `STALE` | Earlier evidence no longer matches the candidate, authority, inputs or profile |

Aggregate required evidence modes using `FAIL > STALE > BLOCKED > NOT_RUN`;
`PASS` requires every required mode to pass. For example, O=FAIL/L=BLOCKED
is overall FAIL, and O=PASS/L=NOT_RUN is overall NOT_RUN. Retain the individual
results and unresolved defects; an untested fix does not erase an observed
failure. A mode absent from a case's Evidence column is labelled `not-required`
and excluded only from that case's mode aggregation.

There is no partial `PASS`. Record partial work in observations. Missing,
skipped, truncated or failed review/CI is not passing evidence. GEN-02 has
separate positive invocation and negative unavailable-ID subchecks, defined
below. A requested model with no verified live invocation keeps its positive
subcheck `BLOCKED` and GEN-02 non-PASS even if rejection behavior passes.
Models are subchecks of GEN-02, not extra numbered cases. Always report both
full-plan `P/57` and supported/blocked requested-model counts. A narrower
approved cohort retains these full-plan gaps; no silent model substitution.

For any evaluated scope, let `N` be its predeclared required cases and `P` its
cases with current `PASS` evidence. Report completion as `P / N × 100`, plus
counts of every other status. Also report observation coverage as
`(PASS + FAIL) / N × 100`. A high percentage cannot override a failed gate.
Do not average offline and live results into a deployment claim.
For the fixed offline level only, use mode results: `P_O` counts its 23 cases
whose O mode passes, so report `P_O/23` and mode-level status counts.
Integrated and full-plan metrics use overall case results across all required
modes. An O=PASS/L=NOT_RUN case contributes to `P_O`, never to full-plan P.

After any change or push, mark earlier PASS evidence `STALE` until its relation
to the final candidate is established. Rerun all L/U portions used for the
decision against the final deployed revision, configuration and verification
criteria; record its image digest and mapping to the final candidate HEAD.
Do not relabel old live receipts as new executions. An unchanged O result may
be carried forward only with an independent, per-case review of unchanged
tested-code, test, dependency, fixture, contract and configuration hashes.
Record the original run HEAD, final HEAD, compared hashes and reviewer; any
changed or unknown dependency requires rerun. All counted PASS results must
have this explicit final-candidate binding, and all required CI/reviews must
cover the final HEAD. Revocation/freshness checks still apply at decision time.

## Baseline and evidence locations

The document-preparation baseline is commit
`9991203b6b0fee6aed2e5d9d0810676972b9f846`. This is an inspection reference,
not the implementation revision that a future assessor must accept. The table
describes that initial preparation baseline, not subsequent working-tree edits.
The plan's `8b9268b` is an ancestor of `9991203b`, which is an ancestor of the
review-preparation HEAD `e097cee8b8a62dbc1d675e112edd401a8072fb46` (ancestry checked
locally on 2026-09-23). Re-derive the table at assessment time.

| Area | Existing inspection entry points | Acceptance limitation |
|---|---|---|
| Canonical data and source authority | `workspace/ontology_schema.py`, `ontology_store.py`, `ontology_sources.py`, `ontology_product.py` | Presence of code does not pass the data, concurrency or access cases |
| Parsing and ingestion | `source-analyzer/analyze.cjs`, `workspace/ontology_analysis.py`, `ontology_jobs.py` | At the preparation baseline, only exact `local_analyze` with explicit offline opt-in was admitted; cloud-shaped receipt fields do not install a trusted cloud adapter |
| Impact and workbench projection | `workspace/ontology_impact.py`, `ontology_workbench.py`, `workbench/knowledge.py` | Test canonical mode explicitly; legacy/bank graph results are not canonical results |
| Regression entry points | `tests/test_ontology_*.py`, `source-analyzer/test/analyze.test.cjs` | This preparation did not execute the acceptance matrix; existing regression tests do not replace case evidence |
| React approval and handoff | `workspace/react_generation.py`, `react_runtime.py`, `react_quality.py`, `releases.py`, `git_service.py`, `git_export.py` | Existing React behavior must be verified again with the new context and backend |
| AgentCore, sharing and cutover | Execution contract and the eventual reviewed adapters/IaC | Still require distinct implementation, live receipts and admission gates; do not reuse unrelated Harness evidence |

Paths in this table are relative to `platform/`. Start from these files, then
record the exact test names and deployed entry points actually used. Read
[review context](../../docs/REVIEW_CONTEXT.md) before interpreting differences.

Each case result must include case ID, expected result, observed result,
status, UTC timestamp, executor/reviewer, candidate HEAD and dirty-tree state,
fixture/input hashes, exact command or API request shape, test selection,
exit/status codes and private evidence references with SHA-256 hashes.
Include limits, measured usage, negative outcomes and associated defect IDs.
Sanitize request shapes; never record authorization header values.

Live evidence additionally fixes account, region, effective assumed role,
resource IDs/versions, deployed image digest, configuration revision, actual
model/provider/profile IDs, API/SDK versions, execution/attempt/stage IDs and
service/session/request IDs. Bind source, context, kit, tool archive, bundle,
verifier profile and receipt-chain hashes. Distinguish signed trusted-adapter
observations from AWS execution attestation; the contract requires the former
and does not claim the latter.
Also retain execution-profile revision/hash and the exact consumed
source-admission decision IDs/revisions and admitted artifact hashes in the
immutable input manifest, attempt, transfer and completion evidence. Broker
logs alone do not satisfy this binding.

Customer archives, extracted customer content, credentials, capability values,
raw prompts and private production receipts stay outside Git. Commit only
synthetic fixtures and sanitized result summaries. Store full permitted
evidence in access-controlled private storage; verify that a reviewer can read
it without widening the source audience.

## Acceptance fixtures and independent oracle

Create a versioned synthetic collection with immutable file bytes and a manually
reviewed expected-result manifest. Do not derive expected edges or hashes from
the implementation output being tested. Record exact source locations and
positive, negative and unresolved expectations.

| Fixture | Required contents and expected distinction |
|---|---|
| Projects and roles | Projects A/B; owner, planner, designer and developer in A; a distinct actor in B; nonmember; source-restricted member; separately administered design/policy publishers |
| Business sources | One synthetic product, published rule R1/R2, procedure with forward/back/cancel and conditional transitions, exact document/guideline revisions |
| Design graph | All eight levels; raw image/Icon at Foundation; React icon wrapper at Atom; explicit Molecule, Organism, Pattern, PageTemplate, two Screens and Procedure mappings |
| Pattern and identity controls | Two independently reviewed Screen usages for Pattern approval; one-usage rejection; duplicate external IDs in different namespaces; ambiguous legacy Component and visually similar template candidates |
| Code collection | Real TS/TSX/JS/JSX exports, re-exports, import/JSX bindings, CSS/SCSS literal assets, HTML, configured JSON reference fields and approved aliases/package metadata |
| Analyzer execution canaries | Synthetic top-level marker writes/process launches, package lifecycle hooks, SCSS evaluation, inline HTML scripts, executable resolver/build configuration and package/URL refs requiring network lookup; a trusted independent control proves the harness can observe markers/processes/egress |
| Two-screen dependency | Both screens depend on the shared image through declared design edges and actual code references; include code-file nodes and explicit Procedure → Screen / Product → Procedure `USES` |
| Unknown and non-impact controls | Nonliteral dynamic import, missing module, shadowed JSX name, parse error, import cycle, missing generated-source map, inaccessible source, unrelated Screen/Team, `NEXT`/`PART_OF`-only links |
| Image revisions | Original I1 and changed I2, rename with/without reviewed lineage, normalized orientation/alpha, valid region and invalid/oversized/active-SVG cases |
| Handoff baseline | Exact approved React baseline, reviewed file allowance, required behavior/a11y assertions, local Korean fonts and optional explicit visual reference |
| Sharing and failure variants | Exact-revision grant A → B, upstream deny, grant withdrawal, membership remove/re-add, source expiry/tombstone, tampered archive/receipt/chunk, duplicate/cancelled/crashed execution |
| Scale | Boundary-sized and one-over-limit parser, graph, source-authority, history, payload and execution fixtures; split collections with cross-unit references |

At minimum, the oracle contains the two expected image-to-screen witness paths,
their source-code paths/locations, the applicable rule refs and the explicit
unknown reference. It excludes unrelated screens and does not propagate
`OWNED_BY`, `DERIVED_FROM`, `NEXT` or `PART_OF` as dependency edges.
`DERIVED_FROM`/source indexes may identify the initial change seeds.
An image change requires rule revalidation where applicable, not a rule edit.
The oracle requires readable exact applicable rule refs labelled `recheck`,
with evidence of their relationship to affected entities. It does not mandate
a collection algorithm. These refs are not rules changed by the image or extra
reverse propagation edges. Inspected nodes/edges and returned recheck evidence
consume the same invocation's applicable traversal and response budgets;
source/authority limits also apply.

## Requirement cases

### Canonical ontology and publication

Authority: plan “Canonical ontology”; execution contract `platform-ontology/1`;
ontology contract “Authority and storage”, “Source authority” and “APIs”.

| ID | Exercise and required outcome | Evidence | Result |
|---|---|---|---|
| ONT-01 | Run with `PROJECT_ONTOLOGY_MODE=canonical`: publish product/design/code partitions, preserve product IDs/hashes, and verify workbench projection rejects competing legacy writes. Separately verify unset/default and explicit `legacy` preserve legacy behavior; caller fields cannot choose graph authority. Workspace `ontology`/`project-current` is canonical; bank local/Neptune stays separate. | O+L | NOT_RUN |
| ONT-02 | Round-trip all eight levels, aliases and original IDs. Foundation/Icon and Atom wrapper remain distinct; Procedure is workflow. Reject collisions/ambiguous matches; preserve unclassified legacy records without claiming classification. | O | NOT_RUN |
| ONT-03 | Exercise every edge type and direction. `COMPOSES` strictly descends Screen–Atom with permitted level skipping; Foundation uses `USES`. Validate many-to-many implementation/membership and conditional/back-loop `NEXT`. Invalid or same-level composition remains a candidate, never approved. | O | NOT_RUN |
| ONT-04 | Inspect exact revisions, hashes, scope, source refs/locations, provenance, review state and tombstones. Declared/parser/model evidence cannot self-approve; reject forged parser provenance and unknown fields/adapters. Reserved `published-asset`, `ux-contract`, `run-round` kinds return unavailable until their authority adapters exist, never implied success. | O+L | NOT_RUN |
| ONT-05 | Execute exact-revision human review/approval by permitted roles. Deny direct candidate/agent/service approval and wrong-role business/code/Team approval. Product/policy approval needs approved/published business sources; deny manual edits of managed product partitions. Pattern needs two reviewed usages; changed/rejected/expired sources invalidate its basis. Unchanged review preserves incident relations. | O+L | NOT_RUN |
| ONT-06 | Edit/delete/reintroduce a node or edge. Reintroduction advances its existing identity's revision rather than restarting at one; historical evidence is not rewritten and tombstones immediately deny new use. Preserve unchanged valid Pattern proofs on replacement; changed basis returns it to candidate. | O | NOT_RUN |
| ONT-07 | Test immutable-write/authority races on both paths. Legacy analysis commits job/artifact/partition/receipt/request-marker together with no trailing terminal write. New `ontology.publish` stages only; `execution.finish` commits all or none. Title/comment control preserves authority epoch and permits caller-checked retry without analyzer replay. | O+L | NOT_RUN |
| ONT-08 | Owner-only workbench import preserves namespace/provenance, candidate state and source/index fences. Incomplete/conflicting snapshots cannot replace prior imports; unsupported shapes stay explicit. Split selections retain stable partitions and crossing-scope unknowns. Compare identities, permissions and paths before atomic cutover; rollback never restores revoked access. | O+L | NOT_RUN |
| ONT-09 | Exercise authorized pagination, hidden nodes, historical snapshots and source reads. API cursors bind actor/role/project/generation and authority epochs, expire within five minutes and fail after remove/re-add membership. Tool cursors additionally bind resource selection under `ontology-tools/1` (AUTH-09). No hidden IDs/names/counts leak or historical ACL bypass. | O+L | NOT_RUN |
| ONT-10 | Test limits/hot buckets. With 12 non-source ops, 88 source checks may commit; 89 checks within 45 bindings reject as `execution-completion-scope`, no publication or omitted fence. Admission never trims inputs; only a requester-authorized new smaller request/ID creates its own frozen manifest/hash. Finish cannot shrink scope. | O | NOT_RUN |

ONT-07 live write-failure injection uses a preconfigured denial on disposable
test object keys/prefixes, plus controlled concurrent publication/authority
updates. Capture storage failure and unchanged authoritative pointers; restore
test policy afterward. Do not alter shared production-role policy for this
probe. ONT-10 measures application prechecks offline; that alone does not prove
live atomicity or cloud-service limit enforcement.

ONT-09's API remove/re-add control is a derived revocation test of the ontology
contract's monotonic `authorityRevision`: old in-flight authorization must not
be restored by restoring membership contents. Tool cursor selection/version
bindings are explicit in execution contract `ontology-tools/1`.

Optional bank-reference import has no installed v1 authority adapter in this
baseline. Record it as unavailable, separately from legacy workbench import.
If a deployment claims that optional integration, extend ONT-01's subchecks
before testing: allowed exact-version import, current source access, lineage
and revocation must pass through a reviewed, schema-version-mapped authority
adapter. Keeping the bank graph separate never proves import.

### Intake, source analysis and impact

Authority: plan “Source and code ingestion”; execution contract
`source-analysis/1` and Phase 0 data admission; ontology implementation contract.

| ID | Exercise and required outcome | Evidence | Result |
|---|---|---|---|
| SRC-01 | Upload HTML/images/source and published business evidence through bounded/resumable intake. Preserve original bytes, page/file identity, hash, source status and immutable revisions separately from platform review. Private uploads do not become shared or approved. Analysis failure leaves originals stored and analysis unavailable/failed. | O+L+U | NOT_RUN |
| SRC-02 | Run the pinned parser on the code collection and execution canaries. Match dependency locations/hashes; harness observations must show no source-triggered marker writes, child processes, hooks/configuration, SCSS evaluation or active HTML. Validate the observer with a separate trusted control; unsupported behavior remains explicit without inventing parser result fields. | O+L | NOT_RUN |
| SRC-03 | Resolve only through the full collection manifest and approved versioned profile. Reject escape, basename/extension/case/Unicode ambiguity and unsupported aliases/packages. Preserve dynamic/runtime/transform/map/parse unknowns. Canary network refs stay unresolved; observed attempted external access must be denied, with no successful network/package fetch. | O+L | NOT_RUN |
| SRC-04 | Process split units with continuation and cross-unit references. Report inspected files/bytes and truncation at limits; finishing one unit or resolving all literals never certifies repository/runtime completeness. Reject altered input/resolver/result hashes. | O+L | NOT_RUN |
| SRC-05 | Normalize admissible PNG/JPEG/sanitized SVG privately. Preserve original and derivative hashes, dimensions, source location, EXIF transform, sRGB/alpha/profile and bounded region coordinates. Reject animation, external SVG resources, unsupported profiles and decompression/parse/size failures. Similarity stays candidate pending human review. | O+L | NOT_RUN |
| SRC-06 | Enforce `source-admission/1`: current policy/source/artifact/inspection and required reviewer bindings; exact registered fixture/public hashes or eligible human review. Source access alone cannot admit data. Deny project-owner/member/workload policy or grant writes, caller class labels, sensitive/unclassified inputs and missing required privacy evidence. | O+L | NOT_RUN |
| SRC-07 | Submit/reopen analysis; test duplicates, interruption and expiry/revocation with exact source/generation fences. Omitted `expectedGeneration` pins current; explicit values including `null` must match. AgentCore analysis uses Runtime/new ledger; legacy analysis remains offline-only. Reuse accepted jobs for reads without paid replay; reject missing adapters and local substitution. | O+L+U | NOT_RUN |
| IMP-01 | Change I1 to I2 using exact old source/region/lineage and a frozen manifest. Return both screen witness paths, actual code references, procedure/product dependencies and applicable rules. Exclude unrelated nodes; retain before/after refs without silently editing rules. | O+L+U | NOT_RUN |
| IMP-02 | Separately change a rule, code/kit, procedure transition and permission/tombstone. Follow the contract-specific seeds/reverse edges. `NEXT`/`PART_OF` alone produce no confirmed dependency; source provenance seeds are not propagation edges; ownership only routes responsibility. | O+L | NOT_RUN |
| IMP-03 | Exercise cycles, converging paths and candidate/rejected mappings. Deduplicate scoped revisions/edges, retain the complete evidence union and witness paths, exclude rejected mappings, and separate observed/approved-declared/potential results. Candidate work requests investigation. | O | NOT_RUN |
| IMP-04 | Hit each traversal/history/response limit and inaccessible boundary. Report every limiting reason and `restricted-or-unmapped`; never show “no impact” or complete coverage. Old-source snapshots remain historical candidates with current ACL checks; no mixing generations. | O+L+U | NOT_RUN |
| IMP-05 | Create role-specific work items, then change source/audience/manifest before starting/completing them. Reject stale evidence and excessive completion references. Cross-project routing uses the IAM-only worker and grants without revealing the destination to an unauthorized origin. | O+L+U | NOT_RUN |

### Authorization, sharing and service isolation

Authority: plan “Identity and tool authority”; execution contract
`execution-capability/1`, `ontology-tools/1` and shared-publication authority.

| ID | Exercise and required outcome | Evidence | Result |
|---|---|---|---|
| AUTH-01 | Admit jobs through verified Cognito identity, current membership and operation scope. Ignore/reject body/model actor/owner/project overrides. Source, compilation dispatch, review, copy/download, release/export and idempotent replay all recheck current access. Login remains invitation-only. | O+L | NOT_RUN |
| AUTH-02 | Trace Identity workload identity → brokered dedicated OAuth machine credential → JWT Gateway → dedicated Gateway IAM execution role → scoped Lambda. Validate issuer/client/scope/audience and actual credential consumption. An unused token call, assumed Gateway workload token, shared bank role, anonymous or silent IAM-only path fails. | O+L | NOT_RUN |
| AUTH-03 | Verify signed capability issuer/audience/actor/project/execution/attempt/session/workload/operations/resources/deadlines. Swap capability A into B, alter claims, spoof Gateway/target/tool context and use an execution ID alone: all deny. Validate configured context IDs; do not assume a service session field exists. | O+L | NOT_RUN |
| AUTH-04 | Issue capabilities only through the IAM-only dispatcher after exact attempt/session/fence allocation; deny API-side/pre-allocation/Runtime minting. Test separate keys, registered algorithms, rotation/revocation/expiry. Token URLs cannot select keys or cached keys bypass revocation; renewal requires a new authorized attempt. | O+L | NOT_RUN |
| AUTH-05 | Validate declared wire `_executionAuthorization: {capability, operationId}` and filtered model schema. Reject model-supplied reserved/unknown fields. Canary credentials/envelopes never appear in prompts, Memory, logs/spans/exporter sinks, job results, ZIPs or pages. Model discovery excludes trusted publish/stage/finish tools. | O+L | NOT_RUN |
| AUTH-06 | Inspect IAM/import/network/encryption/TLS boundaries and make forbidden synthetic calls. New Runtime/Lambda/Gateway cannot invoke plane/bridge/customer-profile or Registry writes/reset; Runtime has no unrestricted canonical-store/direct-ledger writes; Interpreter/Browser have no model/data credentials. Compare pre/post hashes of bank Gateway resource identity, authorizer, targets and IAM configuration: this extension must not change them. | O+L | NOT_RUN |
| AUTH-07 | Publish an exact revision with origin authority, designated publisher and destination-owner acceptance; reuse succeeds only for allowed roles. Wrong revision, expired/withdrawn grant, explicit/upstream deny and owner self-granted organization capability fail. A newer publication does not inherit an older grant. | O+L | NOT_RUN |
| AUTH-08 | Withdraw access during generation/transfer and after approval/download/export. Fence attempts, quarantine all derivatives from supplied-input lineage, invalidate readiness and deny new access. Mark late output non-current; record cleanup and recall-needed work without claiming delivered ZIP/Git bytes were recalled. Rollback cannot undo revocation. | O+L+U | NOT_RUN |
| AUTH-09 | Exceed tool page/source/response/call/text budgets; tamper with cursors and artifact handles. Reject arbitrary URLs/bucket keys/endpoints/roles; errors expose no protected metadata or upstream bodies. Binary transfer limits remain separate from model context limits. | O+L | NOT_RUN |

### Runtime, compiler, Browser and Memory

Authority: plan execution architecture, compiler/browser and Memory sections;
execution contract `platform-execution/1`, atomic evidence and Phase 0 gates.

| ID | Exercise and required outcome | Evidence | Result |
|---|---|---|---|
| RUN-01 | Trace profile-bound stages, one current attempt and operation conflicts. O tests bind/import the full legacy writer inventory, including claim/16-minute expiry/failure/cancel, `_mark_failed` and analysis-read repair. Reserved/malformed jobs and marked/new-linked artifacts reject mutation; unknown linkage reports to the new reconciler with no completion change. | O+L | NOT_RUN |
| RUN-02 | Exercise heartbeat/lease loss, Runtime crash before/after evidence, cancellation, expiry and replacement attempts. Recovery uses the same writer and bounded window. Only a current authorized attempt with full verified receipts may reconcile success; late output cannot alter graph, Memory, approval or release. | O+L | NOT_RUN |
| RUN-03 | Lose the model response after recorded call intent. Report unknown potentially billed outcome/recovery-required; do not automatically reissue it. Explicit authorized retry acknowledges uncertainty and fences the previous attempt. Enforce per-stage budget reservation, retry/round/concurrency/cost limits. | O+L | NOT_RUN |
| RUN-04 | Validate signed chains, registered keys/profiles, current attempts, service/task/nonce and object hashes, plus consumed admission decision IDs/revisions/artifact hashes from immutable input. Missing/mismatched bindings, broker-log-only claims, replay or model pass flags fail. Production rejects test verifiers/unregistered keys. | O+L | NOT_RUN |
| RUN-05 | Transfer bounded files through scoped handles/file APIs. Bind every transfer receipt to its attempt and exact admission decision/revision/artifact hash. Verify offsets, total size, chunks and object hash; reject corruption, expiry or wrong bindings. Revocation/cancellation stops transfer and queues cleanup. | O+L | NOT_RUN |
| INT-01 | Actual Interpreter fetches the exact S3 archive version within Phase 0 limits and verifies hash/platform/native esbuild compatibility. Record OS/architecture/managed Node and pinned parser/compiler/kit/dependency hashes. No runtime package install; deny source-store and broad S3 access. | O+L | NOT_RUN |
| INT-02 | Analyze and compile allowed source with fixed trusted operations. Invalid types/policy/source and changed tool archive fail. Two clean builds from identical inputs/profile produce identical source/bundle and deterministic ZIP hashes; reject unsafe paths and imported build settings/hooks. | O+L | NOT_RUN |
| BROW-01 | Actual authenticated AgentCore Browser fulfills only exact immutable bundle resources in the isolated no-egress VPC. Check HTTP/WebSocket/external font/image denial, local Korean fonts and absent page credentials. Preserve intercepted/blocked request evidence and actual browser/profile/viewport. | O+L | NOT_RUN |
| BROW-02 | Run independent required state/navigation/validation/a11y assertions on that bundle, save real screenshots and failures. Session start, static HTML, fake postMessage results or missing checks cannot pass. Browser/profile drift makes old approval historical and requires revalidation. | O+L+U | NOT_RUN |
| MEM-01 | Follow the plan's “Memory and model boundary” choice: a dedicated resource with no long-term extraction strategies, not Harness SEMANTIC Memory. Write bounded structured decision/ref events without prompts/source documents/credentials; prove no unmeasured automatic extraction/model path. | O+L | NOT_RUN |
| MEM-02 | A later Runtime execution retrieves a prior decision only for the same HMAC-derived organization/project/actor namespace using the bounded recent-session index. Other actors/projects deny. Revalidate each source/approval revision; stale or revoked Memory cannot override current ontology or authorize access. | O+L | NOT_RUN |
| MEM-03 | Exercise TTL, revocation deletion, failed cleanup/retry and audit completion. Application retrieval blocks immediately despite deletion delay; retention cannot increase without operator policy approval. Non-content tombstones never provide a historical-content bypass. | O+L | NOT_RUN |

### Generation, collaboration and handoff

Authority: plan “Generation and handoff”; execution contract
`publishing-handoff/1`; React contract and SPEC §7-1.

| ID | Exercise and required outcome | Evidence | Result |
|---|---|---|---|
| GEN-01 | Exercise new design, reuse and scoped modification. Bind exact selected design/source/procedure/state/rule/kit/baseline refs and ontology context fingerprint to the run. Relevant changes invalidate reuse, approval and release; Memory/model output cannot replace authoritative context. | O+L | NOT_RUN |
| GEN-02 | Positive: for every requested Sonnet, Opus and GPT-5.6 Sol selection from the plan's “Memory and model boundary”, require actual provider/profile/route and boundary invocation receipts. Negative: unverified IDs return unavailable without guessed aliases/Kiro-ID inference/substitution. Any blocked requested invocation keeps GEN-02 non-PASS. Invoke only through Runtime `engine/gate.py`, never Interpreter/Browser or legacy bank tools; retain call-path metadata. | O+L | NOT_RUN |
| GEN-03 | Inspect/measure outgoing payloads; raw identifiers/uninspected inputs block. Preserve exact financial values/independent checks. Source/prompt/tool-content canaries must be absent from Runtime and Interpreter/Browser session logs, spans and exporter sinks. Enforce Tier 2 exclusion including optional insights/Evaluations/Policy; S2 regression stays in ROLL-01. | O+L | NOT_RUN |
| GEN-04 | Generate only within the designer/owner-reviewed file allowance and 24-file/128 KiB policy. Display real added/modified/deleted files with before/after hashes. Reject changes to unrelated source, kit/styles/packages/build settings; rename is delete/add absent reviewed lineage. | O+L+U | NOT_RUN |
| GEN-05 | Test creative and guided modes, independent rounds/history, fixed mandatory criteria and bounded repair. Preserve every failed round and stop at budget/deadline; missing required behavior, image baseline or evidence cannot become pass/approved. Intentional visual variation records explicit acceptance and actual comparison. | O+L+U | NOT_RUN |
| HAND-01 | Display actual backend, queued/running/failed/needs-changes/reviewable/approved/releasing/ready, unknown/truncated and stale states. Review rendered behavior, source files, dependency impact and anchored cross-role discussion; reopening/project switching cannot mix jobs or permissions. | O+L+U | NOT_RUN |
| HAND-02 | Approve only the exact source/kit/context/rule/bundle/evidence revisions under current human UX authority, with concurrent version fences. Agents and stale criteria fail. Publishing approval, developer frontend acceptance and unresolved API/auth integration remain distinct records; frontend rejection removes effective delivery readiness. | O+L+U | NOT_RUN |
| HAND-03 | Copy/download exact allowed bytes for the appropriate artifact state and current role. Draft/failed source is explicitly unverified, not a ready release. Verify chunk ranges/size/final ZIP hash; partial bytes fail. Revoked content is denied, not silently redacted under its original hash. | O+L+U | NOT_RUN |
| HAND-04 | Designer/developer/owner can prepare a release; unauthorized roles cannot. Rebuild the approved source without a model call, repeat required checks and compare the approved start screenshot within 2%. Bind identical content/toolchain evidence, retain private source/dist/manifests and record scope limits: start-screen agreement does not certify every state or original-design fidelity. | O+L+U | NOT_RUN |
| HAND-05 | Developer/owner exports to a registered synthetic Git target/feature branch; designer, planner, nonmember and agent/service identity attempts deny. Verify expected base and actual bytes/SHA; test retry, branch/base conflict, timeout after commit and withdrawal. No main/force writes or invented success; retain observed SHA after later status failure. | O+L | NOT_RUN |

### Rollout and compatibility

Authority: plan migration register, capability gates and implementation sequence;
execution contract “Vertical acceptance case and rollout”.

| ID | Exercise and required outcome | Evidence | Result |
|---|---|---|---|
| ROLL-01 | Record baseline hashes/schema versions; test product IDs, source ACLs, React handoff, workbench projection and unchanged bank Gateway configuration (AUTH-06). Separately run S2/MyData regressions for private preprocessing, exact calculations, independent checks and shared-cache prohibition. Migration is staged/atomic; legacy metadata cannot gain new approval. | O+L | NOT_RUN |
| ROLL-02 | Keep Lambda execution and `PROJECT_ONTOLOGY_MODE=legacy` defaults until gate approval; freeze operator-selected backend at admission. Enable only the named cohort; display receipts and `Lambda · isolated legacy verifier` or `AgentCore Runtime · Code Interpreter · Browser`. Missing services block; no per-call fallback, caller-selected graph mode or in-flight backend migration. | O+L+U | NOT_RUN |
| ROLL-03 | Pause new work/probes and drain/quarantine new-schema jobs/artifacts before guard rollback; never expose them to unguarded writers. Generation/verification may select verified legacy Lambda; source analysis blocks until a verified backend returns, never offline substitution. Preserve revocation/readiness fences. | O+L | NOT_RUN |
| ROLL-04 | Review PR A/B/C independently: A must not claim AgentCore execution, B must retain production defaults, C cannot bypass A/B. Verify latest-HEAD AI/inline review, required CI/protections and HEAD/base/predecessors; fix Critical/Major issues and re-review. Publication, limited cohort and general admission require separate evidence/decisions. | O+L | NOT_RUN |

## Limits to verify at and beyond the boundary

These are the acceptance defaults from the owning contracts at preparation.
Record actual configuration and source contract revision in each run. A changed
limit needs a reviewed contract/configuration revision, not a quiet test edit.
Use an exact-limit and one-over-limit fixture for each independent bound; for
time/expiry limits, use controlled clocks offline and observed deadlines live.

`E` denotes the execution contract, `O` the ontology implementation contract
and `R` the React contract linked above. Section names identify the source of
each bound; verify those sections at the assessment revision.

| Area | Required bound or behavior | Cases | Contract section |
|---|---|---|---|
| Parser unit | 100 text files; 2 MiB total; 100 KiB/text file; 60 seconds | SRC-02–04 | E: source-analysis/1 |
| Image | 20 MiB; 20 megapixels; maximum 8,192 pixels per dimension; in-bounds normalized integer regions | SRC-05 | E: Phase 0 / Resolved implementation choices |
| Ontology storage | 500 nodes/1,000 edges per partition; 1,000 partitions; 4,000,000 serialized bytes per partition/index object; 256 index buckets | ONT-07, ONT-10 | O: Authority and storage |
| Publication authority | 50 distinct exact source bindings; 90 authority records; complete DynamoDB transaction ≤100 operations; location-only annotations share a binding | ONT-07, ONT-10 | O: Authority and storage |
| Analysis completion transaction | Stage-only `ontology.publish`; one `execution.finish` commit for graph/job/artifact/request marker and inline receipt pointers. Reserve ≥10 non-source operations; source-check budget `min(90,100-max(10,nonSourceOperationCount))`; enforce total ≤100 | ONT-07/10, RUN-04 | E: ontology-tools/1; O: Authority and storage |
| Selected legacy import | Optional `nodeIds` selects 1–20 nodes into a stable separate partition; test empty and 21-node rejection | ONT-08 | O: APIs |
| Existing analysis-job recovery | 16-minute stale-job reconciliation in the existing worker; distinct from the new Runtime's 14-minute execution deadline | SRC-07 | O: Analyzer and execution staging |
| Snapshot read | 2,500 authority records with final version/expiry recheck; cursors ≤5 minutes | ONT-09–10 | O: Authority and storage; Source authority |
| Impact | 500 visited nodes; 1,000 edges; 12 dependency hops; 50 actionable items; 20 history snapshots shared within traversal budget | IMP-03–05 | E: source-analysis/1; O: Authority and storage |
| Impact payload | 3,500,000 aggregate evidence bytes; oversized response fails `ontology-impact-scope`; task completion ≤50 evidence refs; metadata record limits independently apply | IMP-04–05 | O: Authority and storage |
| Tools/model retrieval | Context page ≤50 nodes/100 edges; source ≤256 KiB; response ≤512 KiB; job ≤60 calls and 4 MiB text; smaller model-context bound still applies | AUTH-09 | E: ontology-tools/1 |
| Trusted binary transfer | 256 KiB chunks; ≤512 chunks/128 MiB per job; complete object hash; not model-visible retrieval | RUN-05 | E: Phase 0 / Resolved implementation choices |
| Capability | Execution/attempt IDs ≥128 random bits; validity ≤15 minutes and no later than user authorization/execution deadline; no in-session renewal | AUTH-03–04 | E: execution-capability/1 and Identity topology |
| Execution | Initial deadline 14 minutes; heartbeat 30 seconds; lease 90 seconds; recovery ≤5 minutes without extending deadline; idempotent read/chunk retries at most twice | RUN-01–03 | E: platform-execution/1 |
| Completion submissions | One submission plus ≤2 proven-contention retries within deadline; facade rechecks actor/source/deadline/attempt before each, with no hidden SDK retries. Exhaustion → fenced `completion-contention`; unknown transport outcome reconciles durable markers before bounded recovery | ONT-07/10, RUN-01–03 | E: ontology-tools/1 |
| Budget/concurrency | One execution/actor, two/project; daily cost gate, explicit per-execution tokens and service-spend alarm; reserve each repair round plus cleanup | RUN-03, GEN-05 | E: Phase 0 |
| Tool archive/compile | Archive ≤64 MiB compressed/256 MiB expanded; exact-version fetch/hash ≤120 seconds; compatible trusted compile ≤60 seconds | INT-01–02 | E: Phase 0 capability gate table |
| Browser | Required authenticated behavior/a11y/font probe ≤120 seconds under the verified profile | BROW-01–02 | E: Phase 0 capability gate table |
| Memory | At most 20 recent same-actor sessions; initial event TTL 30 days, configurable downward; increase requires policy approval | MEM-02–03 | E: Memory retention; Phase 0 |
| React/handoff | ≤24 generated files/128 KiB; creative one candidate; guided baseline plus 2–5 variants; configured 1–5 rounds subject to remaining budget; release image tolerance 0.02 | GEN-04–05, HAND-04 | E: publishing-handoff/1; R: Compiler result and isolation; Runs, batches and release; SPEC §12 |

## Phase 0 capability gate record

All gates require a named owner/reviewer, private receipt index, configured
limits, fixture hashes, measured results, negative outcomes and stop/go decision.
Run synthetic disposable probes in a verified deployment account/region.
Before a transfer probe, the private-intake owner verifies exact server-registered
synthetic fixture hashes, probe-scope policy and private inspection under
`source-admission/1`. A probe cannot use customer originals or caller-declared
synthetic labels. Complete customer-intake APIs are not a prerequisite to this
minimal protected fixture path.
Bind gate results to adapter, archive, browser/verifier, service/API version and
configuration hashes. Any change affecting the probe makes its PASS `STALE`;
rerun before dependent integration. Apply the case freshness rules to reused
gate evidence. The ontology gate owner also obtains intake/admission-policy
review for SRC-05/06.
Gate-to-case mappings group the existing acceptance obligations. Each mapped
case retains its owning contract's operation permissions and limits.

| Gate | Owner role | Measured pass condition | Cases | Failure decision | Result |
|---|---|---|---|---|---|
| G0-ONTOLOGY | Ontology maintainer | All 22 O-mode cases pass the oracle; any blocked SRC-05/06 keeps the gate non-PASS | O portions of ONT-01–10, SRC-01–07, IMP-01–05 (22 cases) | Correct implementation before canonical projection; 20/22 is not PASS | NOT_RUN |
| G0-ADMISSION | Private-intake maintainer; security operator owns IAM policy/provenance/grant approval | Exact decision/revision/artifact binding through attempts/transfers/completion, policy/inspection/normalization, reviewer grant and administrative-write denials | SRC-01 hashes; SRC-05/06 O+L; AUTH-01 access; AUTH-08 withdrawal; RUN-04/05 decision binding | Block missing evidence; privacy/infra owner verifies required redaction integration | NOT_RUN |
| G0-INTERPRETER | Platform runtime maintainer | Exact archive/platform bounds, valid/invalid compile, identical rebuilds, denied source-bucket access and canary exclusion from session logs/spans/exporters | INT-01–02, RUN-05, GEN-03 | Stop/revise archive/runtime/logging; no relabelled local compiler | NOT_RUN |
| G0-BROWSER | Verifier maintainer | Authenticated exact-bundle/no-egress checks, blocked HTTP/WebSocket, behavior/a11y/fonts within bounds and canary exclusion from session logs/spans/exporters | BROW-01–02, GEN-03 | Resolve network/profile/logging constraints; no public-network fallback | NOT_RUN |
| G0-IDENTITY | Platform identity maintainer | Actual Identity/provider/Gateway/Lambda chain; envelope filtering/canary exclusion; swapped/expired/revoked rejection | AUTH-01–06, AUTH-08–09 | Prove selected topology; no decorative Identity or anonymous Gateway | NOT_RUN |
| G0-RUNTIME | Platform runtime maintainer | Deployed B0 guard revisions recorded before shared-table probes; one current attempt; crash/cancel fences; profile and consumed admission-decision bindings verified; metadata-only telemetry | RUN-01–05, AUTH-05, GEN-03 | Fix guards/protocol/timing/telemetry before integration; unverified sinks block | NOT_RUN |
| G0-MEMORY | Ontology maintainer | No extraction, later-session reuse, actor/project isolation, stale exclusion, immediate revocation denial and deletion | MEM-01–03 | Block selected-backend admission until isolation/retention passes | NOT_RUN |
| G0-MODELS | Model boundary maintainer | Required model/profile calls and boundary, account/region/route, quotas/timing/budget/spend alarm evidence | GEN-02–03, RUN-03 | Mark missing requested model unavailable/BLOCKED; no substitution. A scoped cohort cannot pass the full requested-model gate | NOT_RUN |

Unpassed gates block the dependent integration. Ontology fixtures gate
classification/impact; private admission gates source transfers;
Identity/Runtime gate every protected execution;
Interpreter/Browser gate generation/handoff; Memory gates reuse; all applicable
gates precede production admission. Phase 0 success does not replace the later
application-level cases.

## Vertical acceptance procedure

Run with fresh synthetic execution IDs. Save a checkpoint receipt after each
step, keeping one trace from original input through the delivered source.

| Step | Action | Observable acceptance |
|---|---|---|
| V-01 | Owner creates A; planner publishes product/rule R1; authorized users register HTML, I1 and the two-screen code collection | Immutable hashes and private source locations; current role/audience; no upload-as-approval |
| V-02 | Analyze actual source and review the eight-level mappings | Parser locations match the independent oracle; both Pattern usages reviewed; candidate/unmapped states remain visible |
| V-03 | Select operator-configured `PROJECT_ONTOLOGY_MODE=canonical`; separately check unset/default and explicit legacy | Same canonical authority/generation and preserved product IDs; legacy behavior preserved, caller overrides denied, bank graph separate |
| V-04 | Propose I1 → I2 and open the change worklist | Both screen/code paths, Procedure/Product dependencies, exact rule refs and deliberate dynamic unknown; no unrelated impact or rule rewrite |
| V-05 | Designer approves selected scope, required behavior and file allowance; chooses an available requested model | Frozen context/source/kit/rule hashes and actual model, unresolved out-of-scope coverage explicitly retained |
| V-06 | Submit durable generation whose operator cohort setting selects AgentCore | Actual Runtime and Identity/Gateway/Lambda receipts; admission and model-bound checks; no local execution substituted |
| V-07 | Compile allowed changes and inspect the same bundle | Actual Interpreter/Browser sessions; pinned tool archive; types/policy/build/behavior/a11y pass; any requested visual comparison recorded honestly |
| V-08 | Start another authorized runtime session and retrieve the prior decision | Scoped structured Memory references, revalidated source/approval revisions; no source text or implicit cross-actor recall |
| V-09 | Designer/developer review behavior, source diff, impact and discussion; copy/download candidate | Exact source bytes and hashes, failed rounds inspectable, correct project/state/access labels |
| V-10 | Designer approves publishing revision; release role rebuilds and retests | Exact approved criteria/evidence; no regeneration; deterministic content, approved-start comparison ≤2%; private source/dist manifests |
| V-11 | Developer records separate frontend acceptance and exports to a configured synthetic Git target | Actual feature-branch commit/source bytes, idempotence and base binding; unresolved integration remains explicit |
| V-12 | Publish an exact revision A → B with valid capabilities/grant; then withdraw it and rerun affected stages | Authorized reuse first succeeds; subsequent context/Memory/generation/download/release/export deny or become non-current; private B metadata stays hidden |

Repeat V-05–V-11 for each model/backend/profile combination claimed supported.
For GEN-02, record a positive invocation subresult for every requested model
and a separate negative unavailable-ID result. One missing requested invocation
keeps GEN-02 non-PASS; passing the negative does not fill that gap. For example,
two supported models and one blocked model report `2/3 supported`, GEN-02
BLOCKED (unless another failure/stale result takes precedence), and at most
`56/57` full-plan cases passed. Record unavailable combinations as `BLOCKED`.
Frontend acceptance is independent of the export sequence: test authorized
export both before acceptance and after acceptance; explicit frontend rejection
still removes effective delivery readiness. Local Git tests establish adapter
behavior; actual configured remote export is required for a live export claim.
Neither a customer connection nor export of customer content to this repository
is authorized by this procedure.

For each failure injection below, start from a passing control and change only
the named condition. Observe both the API/UI result and ledger/artifact state;
an error message with a committed success pointer is a failure.

| Injection | Required blocking observation | Cases |
|---|---|---|
| Private B ID, actor/owner override, swapped capability | Denied without protected metadata or side effects | AUTH-01, AUTH-03, AUTH-07 |
| Membership/grant remove/re-add, source expiry or tombstone during work | Old authority stays fenced; no ready/approved/current output or later retrieval | ONT-07, ONT-09, AUTH-08 |
| R1 → R2, kit/context change or modified generated-file allowance | Earlier evidence/approval becomes stale; fresh authorized validation required | GEN-01, GEN-04, HAND-02 |
| Modified archive/native binary or incompatible managed Node | Compilation/service gate fails before trusted success | INT-01–02 |
| Malformed, missing, forged, wrong-session/nonce or wrong-hash Browser receipt | Completion and human approval cannot pass; retain sanitized failure evidence | RUN-04, BROW-02 |
| Browser external HTTP/WebSocket/font request | Blocked at browser/network boundary; no external fallback | BROW-01 |
| Duplicate dispatch, lost lease, Runtime crash, unknown model outcome | One current attempt, bounded recovery, no automatic paid replay or fabricated success | RUN-01–03 |
| Cancelled/expired/superseded attempt returns late | Diagnostic-only result; no graph/Memory/approval/release publication | RUN-02, AUTH-08 |
| Any selected AgentCore service unavailable | Affected work blocks/fails with its actual backend; rollback of source analysis blocks new analyses, never admits a local/offline adapter | SRC-07, ROLL-02–03 |
| Partial/corrupt/out-of-order download or transfer | Final integrity fails; no complete ZIP or successful source handoff claim | RUN-05, HAND-03 |
| Changed Git base/occupied branch/timeout after observed commit | Conflict or reconciled actual commit; no force write or invented URL | HAND-05 |
| Graph/parser/history/work-item limit or hidden dependency | Explicit incomplete/restricted coverage; no full-impact claim or unauthorized counts | SRC-04, IMP-04–05 |
| Completion source checks exceed reserved budget | `execution-completion-scope`; no publication transaction, omitted source fence or changed frozen scope; no graph/artifact/receipt publication | ONT-07/10, RUN-04 |
| Admission request exceeds budget | Request rejected unchanged; no server trimming or partially scoped execution; only the requester's new smaller authorized request/ID may be accepted | ONT-10 |
| Third completion retry, expired authority or changed attempt | No further submission; fenced contention failure or existing cancelled/expired/superseded state preserved; no SDK bypass | ONT-07, RUN-01–03 |
| Completion transport outcome unknown | Read durable job/artifact/request markers first; no assumed failure/success or blind replay; otherwise bounded `recovery_required` | ONT-07, RUN-02–03 |

## Execution commands and prerequisites

These commands are instructions for a future assessor, not recorded results.
Use the dependency/browser prerequisites in
[Platform CI](../../.github/workflows/platform-ci.yml),
[the platform README](../README.md), and
[the source-analyzer README](../source-analyzer/README.md).
Use the selected checkout's Python environment with `python3`; install the
pinned Node analyzer before Python integration tests.

From repository root, the focused offline baseline is:

```bash
git status --short
git rev-parse HEAD
npm --prefix platform/source-analyzer ci --ignore-scripts
npm --prefix platform/source-analyzer test
python3 -m pytest platform/tests/test_ontology_*.py -q
```

For completed cross-module implementation, run all required CI jobs and the
relevant full Python, React kit, gates, web and IaC checks defined in that
revision's workflow. Capture individual job results; absent/skipped jobs do not
pass. Document-only changes require:

```bash
npm ci
NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build
git diff --check
```

Before any live operation targeting the samples account, verify the required
temporary identity:

```bash
aws sts get-caller-identity --profile samples-atomoh
```

Require account `061525506239` and ARN prefix
`arn:aws:sts::061525506239:assumed-role/atomoh/`. The `samples-atomoh` profile
assumes `arn:aws:iam::061525506239:role/atomoh` from the current EC2 role through
`credential_source = Ec2InstanceMetadata`. If verification fails, stop samples
operations. Specify this profile on every CLI/SDK call; never use default,
static `samples`, `samples-ec2` or `awsops` credentials as a fallback or change
the global default.

The live runner and resource IDs must come from the reviewed implementation's
runbook. The samples identity rule above is a user-supplied workspace rule,
not an inference about where every rollout stage runs. Before each stage,
record its named account, region, cohort, approved data policy and disposable
or retained resource set. Check the configured region against actual service
resource/receipt regions; record and approve any cross-region provider route.
Account identity alone cannot establish region or residency. Phase 0, private
staging and synthetic production have separate environment records, even if
they share an account. Record the runner's exact entry point and allowed test
resources in the result. A missing runner, deployment or service receipt is
`BLOCKED`. Verify current service/model support in the target account and
official service documentation when executing; this document freezes no
external availability claim.

## Plan-to-case coverage

Use this mapping to check that a future edit does not drop a plan requirement.
More detailed obligations are traced in the owning-contract references above.

| Plan section / acceptance row | Verification coverage |
|---|---|
| Product objective; input registration; cross-team context | SRC-01, ONT-01, GEN-01, HAND-01–05, V-01–V-12 |
| Canonical ontology; eight-level composition; explicit/default graph mode | ONT-01–10, ROLL-02 (`PROJECT_ONTOLOGY_MODE`) |
| Optional bank reference import | ONT-01 conditional subchecks; unavailable without its own allowed exact-version authority adapter, distinct from workbench import |
| Source/code ingestion; code analysis | SRC-01–07 |
| Image change and reverse dependencies | SRC-05, IMP-01–05 |
| AgentCore execution architecture | RUN-01–05, INT-01–02, BROW-01–02, MEM-01–03 |
| Identity/tool authority; isolation and sharing | AUTH-01–09, ONT-09 |
| Compiler/browser boundary | INT-01–02, BROW-01–02, RUN-04–05 |
| Memory/model boundary; selected-model generation | MEM-01–03, GEN-01–03 |
| Generation and handoff | GEN-01–05, HAND-01–05 |
| Owning contracts; dependency/migration register | Baseline/blob record, ONT-08, ROLL-01 |
| Capability/rollout gates; implementation sequence | G0 gate record, ROLL-01–04 |
| Failure behavior | Failure injection matrix, RUN-02–05, ROLL-02–03 |
| Deployment acceptance | All live case portions, V-01–V-12, G0 gates, ROLL-01–04 |

## Completion decision and reusable result template

Assess these levels independently. At document preparation, every level is
`NOT_RUN` and there is no computed implementation-completion percentage.

| Level | Required decision evidence |
|---|---|
| Offline/data foundation complete | Fixed O portions of ONT-01–10, SRC-01–07, IMP-01–05 and ROLL-01: N=23. All pass the oracle/access/atomicity/compatibility assertions; no AgentCore execution claim |
| Phase 0 capability proven | Every applicable G0 gate passes with actual service probes, owners, measured limits and reviewed private receipts |
| Integrated publishing workflow complete | Fixed ONT/SRC/IMP/AUTH/RUN/INT/BROW/MEM/GEN/HAND cases and ROLL-01: N=54. All required modes and vertical/failure scenarios pass for the declared configuration, including GEN-02 requested-model subchecks |
| Declared-model AgentCore admission ready | All required cases/gates for the predeclared verified model set, limited synthetic cohort, live isolation/budget/rollback and latest-HEAD review/CI pass. Unavailable requested models may remain disabled under the scoped rule below; no other failed, stale or blocking requirement is waived |
| Customer deployment | Separate decision outside this plan's publishing acceptance; requires its own integration/security/acceptance and authorized deployment evidence |

Full-plan completion requires all numbered cases and G0 gates to pass with
current evidence and no omitted requested capability. If only a narrower level
passes, name it and list the remaining blockers. A Minor/Info issue does not
independently block merge, but a failed required acceptance assertion still
prevents declaring that acceptance level complete.

The fixed 23-case offline, 54-case integrated and 57-case full-plan sets cannot
be reduced after results are known. Record any exclusion by case ID and reason
as BLOCKED/NOT_RUN in the fixed set; do not declare that level complete.
A separately approved limited cohort may show an additional scoped metric but
must also show full-plan P/57, per-model results and every excluded capability.
Gates and optional integration subchecks are reported separately, not added to
the 57-case denominator. The initial `NOT_RUN` columns remain unchanged in this
specification; execution results belong in its private copied ledgers.

The 23-case offline level covers the complete workflow's offline obligations,
which are broader than the currently implemented ontology module. Missing
image-normalization or data-admission behavior remains BLOCKED, even when that
module's own tests pass.

The plan permits an unverified requested model to remain unavailable. An
admission decision may therefore cover a predeclared, verified enabled-model
set without claiming full requested-model completion. Keep a separate scoped
GEN-02/G0-MODELS record: every enabled model's invocation and every negative
selection check must pass; all other required cases/gates remain mandatory.
Only positive invocation subchecks for explicitly disabled unavailable models
are outside that admission scope. The full-plan ledger still records them
BLOCKED, GEN-02 non-PASS, full P/57 and supported/blocked model counts. Never
label this scoped admission as a complete implementation of all requested
models, or extend this exception to other missing service/security evidence.

Store the completed case/gate ledgers beside this summary in the private
assessment record. Copy the case IDs exactly and attach each observation.

```text
Assessment ID / UTC:
Executor / independent reviewer / gate owners:
Candidate HEAD / dirty-tree patch hash / base HEAD:
Per-case final-HEAD binding / O carry-forward hashes and independent reviewer:
Final deployment revision / fresh L-U run IDs / unchanged raw receipt identities:
Plan + SPEC + contract blob hashes / schema versions:
Declared acceptance level / projects / selected models / backend cohort:
Fixture manifest hash / reviewed expected-result manifest hash:
Account / region / verified role / data-admission policy revision:
Deployment image/config/resource versions / model routes:
Parser / kit / archive / Node / Browser / verifier profile hashes:
Execution profile revision/hash / admission decision IDs/revisions/artifact hashes:
Baseline compatibility and migration snapshot:

Case ledger:
  ID | O result | L result | U result | overall status
     | expected | observed | command/API/test | evidence ref + hash
     | timestamp | reviewer | defect ID
Gate ledger:
  gate | owner/reviewer | configured limits | measured results
       | positive/negative evidence | status | stop/go
Model/backend coverage:
  requested selection | actual provider/profile/route | invocation evidence
                      | supported / blocked | unresolved prerequisite
Vertical run IDs / failure-injection run IDs:
CI job results / skipped or missing checks:
AI review identity / reviewed HEAD / changed-file coverage / inline findings:
Defects: ID | severity | affected cases | reproduction | fix HEAD | retest

Fixed level / required cases N (offline 23, integrated 54, full plan 57):
PASS P / FAIL / BLOCKED / NOT_RUN / STALE:
Completion P/N:
Observation coverage (PASS + FAIL)/N:
Offline metric P_O/23 / O-mode status counts (not overall case PASS):
Full-plan overall PASS P/57 (required alongside any scoped metric):
Requested models supported/total / blocked selections / GEN-02 subresults:
Declared-model admission set / scoped GEN-02 and G0-MODELS records:
Excluded case IDs/capabilities / reasons (do not remove from fixed N):
Optional bank-reference integration: unavailable / selected and validated:
Phase 0 gates passed / required:
Offline level / capability level / integrated level / admission decision:
Unresolved full-plan cases and requested models:
Frontend acceptance / unresolved API-auth integration:
Rollback exercise / active-job drain / revocation verification:
Private evidence index + hash:
PR URLs / latest reviewed HEAD / merge result:
Deployment/admission decision and responsible operator:
```
