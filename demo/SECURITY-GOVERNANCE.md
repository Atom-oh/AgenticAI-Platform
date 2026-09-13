# Demo security and governance

Scope: the main banking application (`platform/`), the builder control room (`demo/builder-harness/`), and the original UI/UX Studio (`demo/uiux-studio/`). They have different trust boundaries. This code-based account, updated 2026-09-13, is not a live security audit or a policy exception.

Root `SPEC.md` §§3-2, 4-3, 7, 7-1, 11, and 12 remain the stable main-platform requirements. Historical plans and demo implementation gaps do not relax authentication, least privilege, private data handling, approval, or truthful evidence requirements.

The previous **2026-09-03 credential exception covered only the synthetic demo access page** (SPEC §12 item 9). It was not permission to publish credentials elsewhere. This reconciliation removes existing published password prose and uses retrieval guidance; it neither expands that exception nor changes the scoped policy. Main-demo credentials are retrieved from the approved Secrets Manager entry `bank-platform/demo-user` by an authorized operator.

## 1. Security requirements

- Use administrator-issued identities; do not enable self-registration as a workaround.
- Verify identity and authorization at each protected operation. Ownership metadata or a frontend control is not authorization by itself.
- Protect model-bound data with the required privacy/boundary checks. Failure, missing evidence, and unavailable services are not successful checks.
- Keep secrets out of source, Markdown, command transcripts, and rendered output. Retrieve them through approved credential/secret channels.
- Keep S3 Block Public Access enabled; require the intended authenticated/origin-controlled entry paths. Do not add anonymous Function URLs or bypass paths.
- Scope IAM actions/resources and service trust. Unconditioned wildcard permissions, public exposure, or plaintext secret configuration remain issues to remediate, not accepted demo exceptions.
- Preserve versioned approval and audit evidence. Missing synchronization, failed audit writes, and partial validation must remain visible.

## 2. Main banking platform

| Control or boundary | Code evidence and limits |
|---|---|
| WebSocket identity | `platform/api/ws_handler.py` uses Cognito `GetUser` at `$connect`; later messages use the stored connection. This is not per-message token revalidation |
| Public ingress | CloudFront serves static web assets; WebSocket is a separate authenticated endpoint. Do not claim CloudFront is the only network entry point |
| Private data service | `platform/infra/lib/plane-stack.ts`: isolated ECS/RDS, Neptune, bridge, and OpenSearch Serverless VPC access; configuration/readiness must be verified |
| PR3 MyData | `platform/api/handlers/s2.py`, `api/common/privacy.py`, `privacy/relay/handler.py`: IAM relay to internal NLB/EKS; private model detection, structured tokenization, strict payload checks; no S2 shared-cache read/write |
| Processor isolation | `platform/infra/lib/privacy-stack.ts` and `privacy/deploy/`: separate existing-cluster integration, restricted CPU gateway and explicit network policy. HTTP and unverified model revisions are not TLS or model-certification claims |
| Local Registry | `platform/registry/`: versioned lifecycle, audit records, and approved-only consumer API. AgentCore mirror status and CloudTrail evidence are separate |
| React workspace | `platform/workspace/`: current project membership/action checks, frozen criteria, specific-round approval, private releases, and configured Git targets |

Remaining concerns include the WebSocket connection-lifetime model and authorization coverage for legacy Registry actions: `platform/api/handlers/registry.py` receives the connection identity but does not itself enforce a separate approver role. Do not transfer the control room's admin gate to this handler in prose.

S2 emits guarded output before internal numeric finalization; discrepancies are returned afterward. That is not a completed numeric suppression gate. The private processor currently reports no independent private NER configuration; later Bedrock Guardrails inspection is a separate check. Effective cluster network policy, deployed model identity, historical cache cleanup, and internal audit retention need operational evidence.

SPEC §3-2 requires boundary enforcement and restricted raw-prompt logging. This review does not prove universal compliance across direct SDK, AgentCore, legacy proxies, and all logging paths. Raw questions transit the API/relay and lookup values reach the authenticated UI; private ledger/model placement is not proof that raw data never leaves those private services.

## 3. Builder control room

`demo/builder-harness/site/lambda_function.py` implements the following:

| Area | Implemented behavior | Limit |
|---|---|---|
| Entry and identity | Checks the origin-verification header and reads API Gateway JWT-authorizer claims | Deployed authorizer/origin settings must be checked separately |
| Admin/ownership | `platform-admins` gates operations; `can_manage` allows owner, same team, or admin for guarded mutations | List/read/chat and linked-resource paths are not uniformly team-scoped; no complete tenant-isolation claim |
| Agent execution | DynamoDB configs share one Harness with prompt overrides | Creating a catalog config does not create a dedicated runtime |
| Approval | Background smoke evaluation; Tier 1 + pass sets local `APPROVED`, others remain pending | Probe only checks a minimal response; caller supplies risk tier |
| Registry | Registers/submits records and mirrors status to AgentCore | Synchronization is best effort after local state changes; remote failure does not revoke local approval |
| Audit | Application audit entries carry an actor; Registry operations can be correlated with CloudTrail | Audit writes are best effort. AWS API principal is the Lambda role, not delegated human IAM identity |
| Tool exposure | Generated-agent chat/evaluation/workflow calls request the deny-tools list | Do not generalize this to every builder invocation or claim Gateway tools execute in all chats |
| Usage/budget | Token counters and a pre-call budget check, default 50,000 tokens | Check and usage update are not an atomic reservation; concurrent calls can exceed the budget |
| Memory | Actor IDs include agent/user-derived strings | Sanitization/truncation is not collision-proof identity isolation |
| Personal HR | `emp_block` loads one `EMP` record by verified principal-derived identity for opted-in agents | Synthetic demo scoping, not production HR authorization or a no-model-egress guarantee |

Catalog limits are 30 agents, 15 data sources, and 15 skills; messages are capped at 2,000 characters and workflows at four steps/four iterations. These are implemented caps, not a complete abuse-prevention system. Hard-coded cost estimates are not current billing evidence.

The control room's PKCE frontend uses transient `sessionStorage` state during login; authentication/session behavior must be described per product. Approval-role IAM separation, stronger evaluation/risk classification, atomic budgets, comprehensive entitlement checks, and reliable audit/sync remain work. Cedar-based policy was a target, not an implemented gate demonstrated here.

## 4. Original UI/UX Studio

`demo/uiux-studio/infra/stack.py` sets Cognito self-registration off and uses an `AWS_IAM` Function URL behind CloudFront OAC. `feedback/auth.py` validates the custom `x-hana-auth` access token with Cognito. Normal POST asset/generation/feedback routes require a designer identity.

These facts do not establish full protection:

- `feedback/handler.py` retains a POST compatibility branch without `rawPath` that invokes feedback before the user check. Reachability must be assessed for its callers; it contradicts an unconditional “all writes authenticate” claim.
- Asset/history/content/job/model reads are not user-scoped, and the generated gallery is served publicly through CloudFront. S3 Block Public Access does not make those CloudFront artifacts private.
- `harness/publish.py` stores generated HTML directly; the original gallery uses same-origin previews. This is not the main workspace's restricted preview and browser-verification boundary.
- `infra/stack.py` includes unconditioned wildcard IAM grants. Runtime deployment also handles M2M credentials in configuration. These are security gaps to assess and remediate, not exemptions from least privilege or secret handling.
- Feedback changes status/copies an approved pattern without enforcing the main workspace's immutable criteria and checks. Manifest updates and memory recording are not transactional approval evidence.

Figma ingestion and remote-font instructions belong to this older external-service PoC. They do not authorize external fetches in the main financial-network workspace. Memory/few-shot references are context reuse, not model training or guaranteed learning.

## 5. Access and historical resources

Get current account assignments and credentials from the demo administrator through the approved credential channel. Use the configured Cognito recovery/admin reset procedure when needed. Never publish a shared password or copy access/refresh tokens into documentation.

| Recorded surface | Historical endpoint identifier | Product |
|---|---|---|
| Main banking app | `agent.atomai.click`; earlier `d15n7n9ypt87h8.cloudfront.net` | `platform/` |
| Control room | `d1twhttjtzqewp.cloudfront.net` | `demo/builder-harness/` |
| Original Studio | `d4zwmnh2s47e9.cloudfront.net` | `demo/uiux-studio/` |

The 2026-09-02 surface-registration notes and later deployment reports are historical evidence. Do not infer current `APPROVED` status, uptime, matching accounts, or a unified authorization policy from these URLs. Old `Nexus`/`Hana` resource names remain identifiers for separate implementations.
