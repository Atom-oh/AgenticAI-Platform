# Bank platform specification

Current requirements, reconciled 2026-09-13 against merged code `acad38c` (including PR #4).
Section numbers remain stable for source references. Explicit React (§7-1) and
MyData (§4-3) requirements supersede older demo assumptions only in those features.
See `docs/REVIEW_CONTEXT.md` for authority; implementation is not proof of deployment.

## 1. Purpose

A 15-minute demonstration should establish three claims:

| Claim | Evidence |
|---|---|
| Vector retrieval alone cannot establish complete dependency impact | Run the same regulation question through Vector RAG and graph traversal |
| Sensitive model-bound data must pass the privacy boundary | Show actual inspected fields, model route, usage and pass/block evidence |
| Only approved, versioned assets are available to consumers | Deprecate an asset and show the changed consumer result |

### 1-1. Consistency audit

Before a material change, compare the applicable requirement with code and tests.
Classify it as implemented, partial, missing, or outside the specification. Record
unverified deployment separately. Do not add unsupported product claims.
The review-context table records resolved historical conflicts; it does not exempt
new or existing security defects.

## 2. Demonstration scenarios

| Scenario | Required behavior |
|---|---|
| S1: regulation impact, 4 minutes | Compare a normal hybrid/reranked vector baseline with graph traversal across regulation, rules, products, screens, components, departments and documents. Show the actual traversed path. Highest demo priority. |
| S2: MyData consultation, 4 minutes | Exact authorized lookup, deterministic rate/limit calculation, separately displayed raw values/calculation/explanation, actual outgoing payload and model/processor evidence. Apply §4-3. |
| S3: Registry governance, 3 minutes | Generate using APPROVED assets; show build/type/lint/accessibility/visual results and version changes after deprecation. Legacy metadata/stub gates do not certify real React components. Apply §7-1 for React releases. |
| S4: boundary view, 2 minutes | Show S1–S3 request history, private data categories, measured outgoing tokens/fields, model IDs and Guardrails decisions. |
| S5: failure handling, 2 minutes | Use actual Guardrails for disallowed investment solicitation; unsupported answers should report insufficient evidence. |

## 3. Architecture and privacy boundary

### 3-1. Target and implemented topology

The target keeps the ledger, indexes, graph, calculation, orchestration and raw audit
data inside the private data boundary, with inspected payloads reaching external
inference. Do not describe all current workloads as already deployed in one VPC.

The bank implementation has `BankPlatform` (web/API/orchestration),
`BankPlatformPlane` (isolated VPC data services), and optional `BankPlatformPrivacy`
(private relay into an existing EKS VPC). The latter is additive and does not create
VPC peering or a new GPU fleet. This topology is a documented difference from the
original single-VPC target, not proof that network enforcement is complete.

### 3-2. Mandatory behavior

- All bank model-bound payloads require boundary inspection and measurement. Engine
  handlers use `engine/gate.py`; the Strands scenario runtime uses the pre-model hook
  in `agents/boundary_gate.py`. Assess both against the same privacy obligations;
  neither an adapter nor a hook by itself establishes complete enforcement. Private
  EKS entity detection is separate preprocessing, not a Bedrock bypass.
- S2 removes free-text identifiers before any Bedrock Guardrails/model call, then
  independently inspects the complete prepared payload (§4-3).
- Measure actual boundary traffic. Do not report hardcoded zero leakage or simulated
  Guardrails as a successful live check.
- Read personal financial records through authorized exact lookup; do not replicate
  them into vector or graph indexes.
- Neptune loading, shared-data seeding and Registry resets require IAM-only admin
  access. The authenticated `reset` and `registry_seed(reset=true)` code paths remain
  an authorization gap; client-only display reset is a different operation.
- Keep raw prompts, identity mappings and sensitive audit content private. CloudWatch
  and public receipts receive permitted metadata, not raw prompts/entity originals.

### 3-3. Data placement and evidence

| Component | Contract / implementation |
|---|---|
| Synthetic ledger | Private RDS PostgreSQL; `onprem/` is a legacy source name |
| Vector index | OpenSearch Serverless is the selected design; verify actual backend and deployment |
| Bank regulation/UX graph | `GraphStore`, local development or Neptune demonstration backend |
| Published workspace guideline ontology | Project-scoped private JSON nodes/edges; not Neptune integration |
| Calculation and identity restoration | Trusted data service; deterministic values and private mapping |
| Free-text privacy detection | Dedicated private EKS CPU gateway using a configured existing vLLM model |
| Explanation inference | Configured Claude/Gemma adapter after applicable checks |
| Registry | Implemented Registry APIs; verify each integration rather than inferring AgentCore coverage |

## 4. Model routes

Model IDs below are repository configuration, not a current AWS availability claim.
Check the target account, configured catalog and live health before demonstration.

### 4-1. Claude explanation

The default configured generation profile is `global.anthropic.claude-sonnet-5`;
`global.anthropic.claude-opus-5` is another configured choice. The adapter uses
Bedrock Runtime Converse with IAM, sourced from `ap-northeast-2`. Global routing
must not be described as inference guaranteed to remain in Seoul.
The earlier blanket claim that APAC profiles are unavailable in Seoul was contradicted
by the recorded account check; it is not a project invariant.

### 4-2. Gemma explanation and legacy substitution

The Gemma adapter targets `google.gemma-4-31b` at the configured
`bedrock-mantle` endpoint in `us-west-2`, using its OpenAI-compatible client.
`LLMClient` separates adapters. Keep endpoint/API/auth behavior in `engine/llm.py`
and `engine/bedrock_token.py`; prefer a configured Secrets Manager key or the
implemented IAM-derived short-lived bearer token. Never hardcode credentials.

`LLM_ROUTE` selects the explanation adapter (`claude`, `gemma`, or configured
`idc_vllm`). The old demo used Gemma as a private-GPU substitution. In current S2,
Gemma is an explanation choice; it is not the EKS privacy processor.

### 4-3. MyData private privacy processing

This section incorporates the 2026-09-12 MyData requirements and supersedes S2's
older Gemma-as-privacy, no-transformation, raw-token-streaming and cached-fallback assumptions.

1. Authenticate and validate the request. Send the raw free-text question only to
   the configured private privacy service before INPUT Guardrails.
2. The EKS gateway asks the configured model for entity evidence. Deterministic code
   validates schema, spans, occurrences, identifier formats and overlaps, preserves
   amounts/rates/periods, and performs replacement. Malformed, truncated, ambiguous
   or unverifiable detections block the request.
3. The trusted data producer performs exact lookup, calculation and typed-field
   tokenization. Do not ask a second generative pass to reinterpret financial facts.
   Independently inspect the complete prepared payload with rules and Guardrails;
   evidence identifies this stage as `modelInvoked: false`.
4. Send only the checked payload through the explanation gate. Buffer explanation
   output until output verification. Keep financial validation and trusted-plane
   restoration; diagnostics must not re-expose content removed by Guardrails.
5. Do not read, write or replay S2 through the shared event cache. Missing configuration,
   failed verification or residual identifiers block rather than return cached success.
6. Receipts contain permitted model/revision, processor, method, counts, timing and
   inspection status. Do not return entity originals or raw questions. Newly detected
   free-text identifiers are removed; existing trusted-plane restoration is separate.
   Do not claim legal or complete anonymization.
7. Select privacy models from the server allowlist and actual health. Qwen is the
   reference EKS implementation; Gemma/DeepSeek privacy candidates are unconfigured
   until an endpoint and artifact revision are registered. Explanation and privacy
   selections remain distinct.
8. Private NER is optional and reported as unconfigured unless connected. Synthetic
   evaluation and SageMaker job preparation do not mean training or promotion ran.

Current S2 implementation sends `c.token` after OUTPUT Guardrails but before
private numeric/Semantic finalization. Do not claim numeric validation suppresses
every incorrect number before display; this is a remaining output-ordering gap,
not an exemption from deterministic-finance requirements.

The relay uses `MYDATA_PRIVACY_FUNCTION_ARN`, has no function URL, and accepts only
its supported operations against a fixed private endpoint. Reuse the existing GPU;
add only the CPU gateway, private target binding/load balancer and IAM relay.
See `platform/infra/README-privacy.md` for prerequisites and network checks.
The HTTP gateway also checks the actual TCP peer against verified NLB addresses in
`PRIVACY_ALLOWED_CLIENT_IPS` before reading model-bound input. `/health` is a
non-sensitive probe exception. Forwarded headers cannot override peer checks; this
mitigates the CNI standard-mode startup window without claiming strict-mode or host
isolation. Keep NLB client-IP preservation disabled for this contract. The current
gateway accepts exactly 2–4 distinct IPv4 peer addresses inside `10.0.0.0/8`;
other counts or address ranges require an explicit implementation/contract change.
The platform privacy client uses a Seoul Lambda ARN and accepts `query|payload`,
with an 8,192-byte UTF-8 text limit. The gateway caps the JSON body at 65,536 bytes
and, like the IAM relay, additionally supports internal `evaluation` requests.
The platform client does not expose that purpose as a selectable S2 operation. HTTP health, evaluation and model readiness are distinct paths.

## 5. Ontology

### 5-1. Lending-domain target

| Label | Target count | Key fields |
|---|---:|---|
| Regulation | 60 | code, title, article, effectiveDate, version, status |
| RegulationAmendment | 25 | amendmentId, date, summary, diffType |
| Product | 120 | productCode, name, category, launchDate, status |
| Condition | 800 | conditionId, type, operator, value, unit, priority |
| Department | 20 | deptCode, name, role |
| Document | 200 | docId, title, type, deptCode, updatedAt |
| Template | 12 | templateId, name, sections |
| Customer | 500 | pseudonymous customerId, segment, joinDate |
| Account | 1200 | token accountId, productCode, balance, openDate |
| Merchant | 150 | merchantId, name, mccCode, category |

Customer/account graph nodes are synthetic demonstration fixtures, not a copy of
customer ledger records. Counts are seed targets, not live measurements.

### 5-2. UX-domain target

| Label | Target count | Purpose |
|---|---:|---|
| Screen | 150 | Screen references |
| Component | 80 | Component library |
| Pattern | 40 | Pattern library |
| Procedure | 30 | Procedures |
| PolicyRule | 60 | Business/policy rules |
| UXTerm | 200 | UX dictionary |
| ScreenMeta | 150 | Screen metadata |

Components carry ID/name/version/approval/props schema/owner; screen metadata carries
screen number, purpose, entry condition and predecessor/successor screens. Original
aggregate targets were approximately 3,800 nodes and 11,000 relationships.

### 5-3. Relationships

`schema/ontology.cypher` and seed generation define the actual schema. Required paths:

```text
Regulation -APPLIES_TO-> Product
Regulation -AMENDED_BY-> RegulationAmendment
Regulation -SUPERSEDES-> Regulation
Product -HAS_CONDITION-> Condition -DERIVED_FROM-> Regulation
Condition -EXCLUDES-> Merchant
Condition -REQUIRES-> Condition
Product -OWNED_BY-> Department
Document -REFERENCES-> Regulation
Document -FOLLOWS-> Template
Document -OWNED_BY-> Department
Customer -HOLDS-> Account -OF_PRODUCT-> Product
Account -TRANSACTED_AT-> Merchant
Product -SOLD_VIA-> Screen -USES-> Component
Screen -FOLLOWS-> Pattern -COMPOSES-> Component
Procedure -INCLUDES-> Screen
Screen -OWNED_BY-> Department
PolicyRule -CONSTRAINS-> Screen
PolicyRule -DERIVED_FROM-> Regulation
Component -SUPERSEDED_BY-> Component
ScreenMeta -DESCRIBES-> Screen
UXTerm -USED_IN-> Screen
```

### 5-4. Required synthetic coverage

- At least three regulations each reach at least 4 products, 6 screens, 8 components,
  3 departments and 5 documents.
- At least three components each affect at least 12 screens, 4 patterns and 2 policy rules.
- Verify these constraints in tests; display query results rather than target counts.

### 5-5. S1 traversal

Traverse conditions derived from a regulation and their products; include screens
constrained by policy rules derived from the same regulation, product screens and
components, owning departments and referencing documents. Deduplicate results and
return traversal evidence. Use the actual graph implementation and coverage tests,
not a copied illustrative query as an alternative executable contract.

## 6. Semantic layer

`platform/semantic/metrics.yaml` and its loader define metrics separately from the
ontology. Preserve exact machine names and Korean aliases from that file. SQL uses
approved templates and parameters; the LLM does not invent metric definitions.

The Semantic Layer OFF demo deliberately omits a definition to expose possible
misinterpretation of the previous-month metric. Label this as an antipattern; compare
actual model values against deterministic values without guaranteeing a mismatch.
Diagnostics may inspect only the verified deliverable, never blocked original output.

## 7. Agent Registry

- Record types: `MCP`, `AGENT`, `SKILL`, `CUSTOM`.
- Lifecycle: `DRAFT -> PENDING_APPROVAL -> APPROVED -> DEPRECATED`, with rejection and
  revision paths implemented by the Registry contract.
- Preserve name/version uniqueness, approval audit events, and APPROVED-only consumer lookup.
- Normal Registry search combines keywords and embedding similarity. Report an
  unavailable/disabled embedding backend as a fallback, not the full hybrid target.
- Customer asset sources are version-controlled component guidance, screen specs,
  skills, agents and registered tools. Proposed GitLab/MCP/AgentCore integrations
  are not automatically implemented by creating a Registry record.
- Import externally provided assets through the organization's intake process.
  Studio must not fetch missing Figma/CDN resources or invent internal MCP contracts.

### Required asset mapping and implementation deviation

| Asset | Required record type | Source / integration scope |
|---|---|---|
| Component descriptions and usage guidance | `SKILL` | Version-controlled component guidance |
| Screen specifications (MDX/JSON) | `CUSTOM` | Version-controlled specifications |
| Registry and GitLab MCP servers | `MCP` | Registered internal services; deployment is separately verified |
| Bank publishing skills | `SKILL` | Versioned Markdown |
| Screen-generation agent | `AGENT` | Selected agent runtime; verify actual integration |
| draw.io MCP | `MCP` | External service proposal; not permission to fetch external Studio inputs |

The current Registry represents component contracts as `CUSTOM` with subtype
`COMPONENT`, while the requirement remains `SKILL`. `registry/seed.py` and the portal
mapping/tests explicitly mark this deviation. Do not remove that distinction without
an authorized change to the requirement. Registration is not proof of deployment.

### 7-1. React collaborative workspace

The final unit is real React source plus the static bundle built from it. Markdown
provides business guidance, not component implementation. `@studio/approved-ui` is
currently a platform baseline, not a supplied customer's approved package. Its 15
components are Screen, Stack, Grid, Inline, Panel, Text, Button, Input, Checkbox,
Select, RadioGroup, Alert, Stepper, Summary and AssetImage.

**Access and planning.** Separate private and project storage. Check current membership
on each project operation. Owners manage members; planners publish product guidance
and edit/approve rules; designers edit/approve rules, generate/refine/approve UX and
prepare releases; developers prepare releases and export to registered Git targets.
Members may access project assets and discussions within the implemented role contract.
Do not automatically share personal uploads.

Publishing guidance stores an immutable revision, private source asset and typed
Product/Condition/PolicyRule/Procedure/ScreenMeta nodes and edges. Generation reads
the persisted project ontology. Connect discussions and impacts to product/page/round;
changed guidance invalidates reuse of earlier approval under the latest criteria.

**Generation and verification.** `creative` creates one candidate; `guided` creates
one baseline plus 2–5 variants. Each has independent history and the same mandatory
business rules, component version and guidance revision. Pin source/type/token hashes.
AI writes permitted screen composition/state code, not the trusted package or compiler.
Use real TypeScript checks, production compilation and browser tests of that same bundle
in a restricted process without data credentials. Do not execute uploaded build settings
or download npm packages, external images or fonts during execution.

Failing or indeterminate checks require repair and revalidation against the same rules.
A requested image comparison and intentional variant acceptance are different results.
Do not label an absent image baseline as a visual pass.

**Approval and release.** Bind human approval to source/component/guideline/rule/bundle/
evidence revisions. Atomically verify current criteria when storing approval. Rebuild
and revalidate the same approved source without AI regeneration. Compare the approved
start screen with a maximum difference of 2%; this does not certify every state/page.
Keep source ZIP, dist ZIP/site files, manifests and evidence private. A ready bundle
does not authorize publication to a customer service or public website.

**Git export.** Use administrator-registered targets, base branches, allowed paths and
secret references. Export approved release source to a feature branch; never direct
main writes, force updates or uploaded-code execution. Verify idempotent commits and
refuse conflicts. Preserve an actual commit SHA if a later status lookup fails.
GitHub/GitLab adapters and local bare-repository tests exist; customer destinations
remain unconfigured unless separately supplied. Do not fabricate remote success.

**Input scope.** HTML/CSS/PNG/JPG/SVG are primary inputs; PDF/text are supported and FIG
is original-file storage only. Imported HTML verification remains prototype evidence,
not React release approval. No claim covers unsupplied financial APIs/authentication/
transactions or completed migration of all legacy galleries/drafts.
The detailed interface is `platform/workspace/REACT_CONTRACT.md`.

## 8. User interface

### 8-1. Surfaces

Provide login, dashboard, regulation comparison, graph explorer, MyData consultation,
Registry, generation, UX asset portal, Reader/Writer reports, boundary history,
Guardrails history and the collaborative React workspace.

### 8-2. UX asset portal

Categories: Foundation, Components, Patterns, Screens, Procedures, Policies, UX Writing.
Show asset ID/status/version/owner, version history and publication/sync actions.
Related counts and affected-screen navigation come from actual graph queries.

### 8-3. Boundary metrics

Show private data categories/counts, actual outgoing fields and token usage, actual
model ID, storage versus inference route, and Guardrails status. S2 shows privacy
processor evidence separately from explanation-model evidence.

### 8-4. Presentation

Use consistent private/external-inference colors and highlight traversed graph paths.
The demo application remains Korean, using Pretendard and a dark-first presentation.
English PR-review/development documentation does not change the Korean public
guidebook, demo instructions, or product UI language.
Avoid unsupported geographic-residency or physical-topology claims.

### 8-5. Demo controls

Provide authorized reset, scenario presets and progress indicators. The original
five-second first-token target is a UX goal, not permission to expose unverified S2
output. Cached-response fallback is permitted only in supported nonsensitive scenarios
with an explicit cache badge; S2 privacy failures and shared replay are excluded.

## 9. Synthetic data

Use fixed seeds, fictional products and fictional regulation content. Never copy real
customer records or private bank clauses. General ledger fixtures use pseudonymous or
token identifiers. The MyData privacy evaluation deliberately uses controlled synthetic
identifier-shaped text to test removal; it is not real customer data. Keep that scoped
exception distinct from production ledger generation. Verify ontology coverage (§5-4).

## 10. Technology and source systems

React/Vite/TypeScript frontend; API Gateway and Python Lambda; private Lambda/ECS data
services; RDS PostgreSQL ledger; selectable local/Neptune graph; selected OpenSearch
Serverless vector design; Bedrock explanation/Guardrails; Cognito; TypeScript CDK.
The optional privacy gateway reuses EKS/vLLM. Inspect package manifests for exact versions.

The customer proposal assumes existing GitLab and excludes CodeCommit/CodePipeline.
This repository uses GitHub Actions; customer assumptions do not forbid its actual CI.
SDK/AgentCore choices and deployment limits are recorded in §16.

### 10-1. Graph backend and cost

Use `GRAPH_BACKEND=local|neptune` through `GraphStore`. A customer demonstration claiming
Neptune must use Neptune and display the actual backend. Preserve deployment/teardown
instructions in `platform/README.md`; obtain current pricing before estimating costs.

## 11. Honest implementation labels

### 11-1. Privacy versus explanation

Label actual processors and endpoints. The historical Gemma-for-GPU substitution is
not the current S2 privacy path. A configured EKS Qwen detector and a Bedrock Gemma
explanation may coexist; neither proves an IDC Hybrid Nodes deployment.

### 11-2. Transformation scope

Rule-based structured tokenization and EKS free-text removal exist. Replace the old
“no transformation implemented” claim with the actual mechanism and validation status.
Do not imply a full ML anonymization/re-identification vault or legal certification.

### 11-3. Ledger placement

The demo uses a synthetic private RDS ledger, not a demonstrated Direct Connect link
to a customer's core banking system. Use accurate private-subnet language in the UI.
Legacy `onprem` source/event identifiers are not a requirement to rename APIs.

### 11-4. AgentCore scope

The selected governance design uses AgentCore for permitted nonsensitive workloads.
Do not route Tier 2 personal inference through AgentCore insights/Evaluations/Policy.
Label applicable screens Tier 0/1-only and verify current service routing/storage
before deployment; earlier broad availability claims are not permanent AWS facts.

## 12. Prohibited behavior

1. Bypassing the applicable model-bound gate or S2 preprocessing/inspection.
2. Hiding substitutions or reporting unconfigured integrations as live.
3. Vectorizing personal ledger data; document embeddings must respect data scope.
4. Inventing amounts, rates or limits instead of validating deterministic results.
5. Logging raw prompts/entity originals to CloudWatch.
6. Presenting mock Guardrails as real deployment evidence.
7. Intentionally weakening the Vector RAG comparison baseline.
8. Reporting hardcoded counts as measured results.
9. Hardcoding passwords/API keys in source, documentation or logs. A 2026-09-03
   permission covered only the synthetic demo access page; no credential value is
   required in maintained docs, and that historical exception is not a general waiver.
10. Using actual private bank products/clauses as synthetic fixtures.
11. Unbounded retries. Legacy scenario regeneration is at most once; workspace requests
    have 1–5 rounds and a time limit. Reaching a limit never means approval.
12. Storing personal data in browser storage.
13. Misleading UI claims of physical on-premises deployment, multiple privacy boundaries,
    in-region inference or guaranteed Seoul-only routing. Historical/code identifiers
    are not live topology assertions.

## 13. Implementation phases

Historical delivery sequence: (1) ontology/data/semantic layer; (2) GraphRAG and S1;
(3) private services, calculation, boundary and S2; (4) Registry/portal/generation;
(5) Reader/Writer reports and rehearsal. These are not a current unchecked backlog.
Use current source, tests and module status for completion; use dated plans for history.

## 14. Configuration decisions

Before configuring a new environment, resolve actual target account/region, model
access, integration scope, budget, credentials and rehearsal duration. Do not reopen
already accepted project choices merely because the original questionnaire listed them.
Do not infer authorization to modify unrelated shared-cluster resources.

## 15. Demonstration mapping

S1 demonstrates dependency-aware retrieval and AI-ready data; S2 demonstrates exact
calculation and privacy processing; S3 demonstrates approved assets; the portal
explains asset relationships; S4 explains measured inference boundaries; S5 shows
safety failure handling; Reader/Writer shows separate read and write permissions.

## 16. Implementation and verification record

This section preserves dated evidence, not a current deployment attestation.

- **2026-09-02 decisions:** Strands for the selected agent implementation; managed
  AgentCore Harness, Gateway and Registry for their permitted governance scope;
  OpenSearch Serverless replaces the earlier default pgvector proposal. These choices
  do not mean every bank handler executes in Harness or every integration is deployed.
- **2026-09-02 observations:** the account had configured global and APAC profiles;
  the Gemma adapter used the Mantle catalog/auth path rather than assuming the standard
  foundation-model catalog listed it. Verify current availability before use.
- **2026-09-11 React record:** real pinned components, shared guidance, source rebuilding,
  approval and private archives were implemented. The recorded check had 784 Python
  and 41 UI tests; backend UPDATE_COMPLETE and synthetic project/upload/model/release
  checks were reported. Two approval/Git-recording Major findings were fixed. The
  recorded model generation lacked a source-image baseline (`visual: not-run`), while
  release recomparison used the approved start screen and 2% threshold. Frontend
  publication was recorded separately; no customer React package or Git target was supplied.
- **2026-09-12 MyData, PR #3:** private gateway/relay, deterministic entity replacement,
  S2 ordering/cache restrictions and synthetic evaluation were merged. Latest reviewed
  feature HEAD was `b3d05e1`; merge commit is `5a14725`. Five CI jobs passed; the final
  independent review reported no remaining Critical/Major findings. A live synthetic
  model exercise is not proof of deployment of the new Lambda/NLB/network route.
  Shared-cluster prerequisites and network-policy enforcement still require operational
  verification. SageMaker training and candidate-model promotion were not established.

- **2026-09-13 MyData, PR #4:** merged TCP-peer admission checks for the NLB path,
  explicit subnet route-table metadata, and shared-cluster ListenerSet/CNI canary
  inputs. Shared maintenance was authorized; actual rollout, cold-start dependencies
  and policy-enforcement results remain separate verification. PR #5 artifact-pinning
  work was still open during this reconciliation and is not claimed as implemented
  by this baseline.

Keep detailed operational evidence in `platform/README.md`,
`platform/infra/README-privacy.md` and `docs/14-demo/`. Do not convert a historical test
count, a local Git test or a staging result into a claim about today's deployment.

## Amendment: internal documents and source-bound S1 (2026-09-13)

This scoped amendment follows the user's request for commercial-level source
traceability and their choice to start with an internal document library.
It replaces the primary S1 comparison-screen requirement only. Existing
Vector/Graph comparison engines remain legacy technical interfaces and are not
consumers of private library content.

- The primary S1 workflow selects a regulation, resolves currently accessible
  approved originals, records exact document/extraction versions and opens
  citations at their source paragraphs. Missing originals remain missing.
- Reuse canonical workspace JWT identity, personal/project membership and private
  storage. Document permissions apply to metadata, original/paragraph reads,
  search, model context, asynchronous publication and result reads.
- Do not use the shared S1 event cache for private analyses. Failures cannot
  replay another user's result or silently substitute a document version.
- Source hashes and reference-ID validation establish integrity and linkage,
  not semantic or legal correctness. Results remain impact candidates for
  human review; authorized owner/planner decisions are separately recorded.
- Original intake, extraction completeness, immutable revisions, approval,
  source-role changes and history follow
  `platform/documents/CONTRACT.md`. Incomplete text cannot be approved for AI use.
- Synthetic examples are explicitly labelled, server-owned and initially
  drafts. Normal uploads cannot claim that provenance or replace sample
  originals. No example is represented as a real banking policy.

This amendment defines required behavior; source code, tests, review and live
deployment evidence must establish its implementation separately.

## Appendix: access

Use the URLs and authorized account setup in `docs/14-demo/index.md`. Cognito remains
invitation-only. Obtain the bank demo credential from Secrets Manager
`bank-platform/demo-user`; never include its value in this specification.
