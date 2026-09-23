# Agentic AI Platform

Banking scenarios, asset governance, and a collaborative React design workspace.
This document describes repository code as audited on 2026-09-13; it does not
attest to the current AWS deployment.

Read [root instructions](../AGENTS.md), then [review context](../docs/REVIEW_CONTEXT.md)
and [SPEC.md](../SPEC.md), then the applicable module contract:
[platform](docs/CONTRACTS.md), [workspace](workspace/CONTRACT.md), or
[React workspace](workspace/REACT_CONTRACT.md). SPEC section numbers remain stable.
Guidebooks explain usage; dated plans and deployment reports are evidence for
their stated scope, not blanket requirements for every module.

## Current architecture

| Path | Current implementation |
| --- | --- |
| Web and API | CloudFront serves the SPA from a private S3 origin. The browser also connects directly to the authenticated API Gateway WebSocket endpoint. Workspace HTTP traffic uses `/studio-api/*`. |
| Internal data plane | `infra/lib/plane-stack.ts` defines an isolated VPC without NAT: bridge, internal ALB/ECS service, RDS, Neptune, and Writer. `onprem/` is the retained source-directory name. |
| Private MyData processor | Optional `BankPlatformPrivacy` resources reuse a separate existing EKS VPC. An IAM-only Lambda relay reaches an internal NLB and CPU privacy gateway, which invokes a private model service. This VPC's routes are independent of the isolated data plane. |
| Generation | `engine/gate.py` checks outbound text before the platform LLM adapters. Strands uses its own `agents/boundary_gate.py` hook. Model choices come from `engine/model_catalog.py`; configured IDs do not prove service availability. |
| Workspace execution | Private S3/DynamoDB hold inputs, approvals and artifacts. A worker generates source; a separate isolated browser Lambda compiles the pinned React kit and verifies its actual bundle. |
| Role workbench | Project planning, impact worklists, private knowledge projections, Skill content governance and synthetic pension/report flows extend the Workspace API. Read `workbench/README.md`; external source readiness is explicit. |
| Internal documents and S1 | `documents/` stores private immutable originals, extracted paragraphs and revision-bound review. S1 uses accessible approved sources and the existing demonstration graph; evidence links open the exact source revision/paragraph. Missing sources and human review remain explicit. See [contract](documents/CONTRACT.md) and [user guide](../docs/14-demo/internal-documents.md). |

CloudFront is the web entry point, not the only public endpoint: WebSocket API
Gateway is directly reachable. Private workspace artifacts differ from legacy
Studio/design artifacts served through CloudFront. Inspect
`infra/lib/stack.ts`, `infra/lib/workspace.ts`, and the relevant artifact module
before making an isolation or publication claim.

### MyData: privacy processing and explanation are separate

`api/handlers/s2.py` implements this sequence:

1. Validate the question and model selection. Send free text to
   `api/common/privacy.py` → `privacy/relay/handler.py` → EKS gateway.
2. Invoke the selected private detector, redact validated identifier spans with
   per-request tokens, and validate the receipt. The default configured detector
   is `qwen` / `Qwen/Qwen3-8B`; private `gemma4` and `deepseek` entries are disabled
   until configured. `/models` checks model identity; `/health` alone is insufficient.
3. Run input Guardrails on sanitized text. Resolve semantic definitions, then
   use `onprem/service.py` for exact lookup and deterministic rate/limit calculations.
4. Tokenize known structured fields in the internal service. Run the independent
   strict PII scan on the resulting payload. This step reports
   `processor="schema-and-independent-scan"`, `method="structured-tokenization"`,
   and `modelInvoked=false`; it does not send that payload through Qwen again.
5. Generate the explanation through `engine/gate.py`, buffer it, and apply output
   Guardrails before emitting answer text. Internal finalization restores known
   tokens and reports numeric violations; semantic checking compares the answer's
   `전월실적` value with the calculation engine.

`privacyModel` selects the private detector; `route` selects the explanation
adapter (`claude` or `gemma`). The Gemma adapter defaults to `google.gemma-4-31b`
at bedrock-mantle in `us-west-2`. It is not a substitute for the EKS privacy step.
S2 requires `MYDATA_PRIVACY_FUNCTION_ARN`; missing configuration, invalid evidence,
residual identifiers or verification failure blocks the request. There is no
rule-only privacy fallback or shared response-cache replay: S2 calls
`costguard.guarded(..., cache=False)`.

The gateway currently reports `independentNer="not-configured"`. Rule checks and
strict downstream verification exist; a second private NER model is not thereby
implemented. `modelRevision="unverified"` is an explicit unknown. See
[privacy infrastructure](infra/README-privacy.md) and
[optional training preparation](privacy/training/README.md).

### Agent and design paths

| Path | Runtime and limits |
| --- | --- |
| Scenario agents | `AdminFn seed_agents` prefers AgentCore Runtime/Strands when `AGENTS_RUNTIME_ARN` exists; otherwise it provisions Harness records. There are five specs, including `design_flow_agent`. |
| Custom agent builder | `api/handlers/agents.py` creates Harness agents. `agentcore/invoke.py` dispatches by record payload; `APPROVED` is required by the platform invocation handler. |
| Legacy F5 screen generation | `screengen/agent.py` reads approved Registry component schemas and three skills. At most one regeneration. Node gates use synthetic `@atom/ui` declarations and semantic stubs; visual evidence is an HTML structure snapshot. |
| Process generation | `design_loop/` derives a PRD and checklist, generates step HTML, then reviews it. At most one regeneration, plus one parse retry within each attempt. `api/handlers/design.py` runs locally through the gate unless `DESIGN_USE_RUNTIME=1` and a runtime ARN are set. |
| Legacy HTML Studio | `studio/loop.py` uses `studio-*.md` skills, DesignSpec checks and up to 20 rounds. This is a separate workflow from F5 and the React workspace. |
| React workspace | `workspace/` uses an approved rule contract and pinned `react-kit/`, with one to five rounds (default three). HTML verification remains available as a prototype path. |

AgentCore inference is a Tier 0/1 path. Scenario chat memory in the Strands
container is a process-local LRU of at most 20 sessions, not proof of managed
durable memory. See [agent runtime](agents/README.md).

## Collaborative React workspace

The deliverable is a real source project plus the static bundle built from it.
`react-kit/ui/` supplies 15 platform components, TypeScript types and tokens;
generated code imports `@studio/approved-ui`. This is a platform baseline, not a
claim that a customer's component package has been supplied.

- `workspace/collaboration.py` stores project membership, roles, product drafts,
  immutable published guidelines, ontology projections and comments.
- `workspace/http.py` derives personal/project scope from authenticated identity
  and checks current membership. Approval freezes exact criteria and artifact hashes.
- `workspace/batches.py` creates one creative run or a guided baseline plus two
  to five variations, with independent evidence and failures.
- `workspace/react_generation.py`, `react_runtime.py` and `browser*.py` generate,
  compile and verify the actual bundle. Missing build/browser evidence cannot pass.
- `workspace/releases.py` rebuilds approved source without an AI call, repeats
  browser checks, and compares with the approved screenshot at tolerance `0.02`.
  This is distinct from comparison with an original design image.
- `workspace/git_service.py` and `git_export.py` export verified source to configured
  Git destinations and feature branches. No destination is inferred from this
  repository; unconfigured export remains unavailable.

Files are untrusted data. Originals and hashes are retained; imports track
`importRevision` and `lineageId` separately from metadata `version`. FIG files
are retained as opaque archives. Imported HTML inspection does not authorize
a React release. Visual comparison without a selected reference is `not-run`;
variation acceptance preserves the measured comparison and requires explicit
review. Figma integration, customer packages and real financial APIs are not
implied by a passing platform demo.

The Studio and asset portal also share a private, page-searchable
[customer guideline library](workspace/GUIDELINES.md). Local PDF/PPTX preparation
creates a text-only JSON pack; the original documents stay local. Selected page
hashes and citations are fixed in the rule contract and reused by generation.
Guideline names do not imply that a customer's React package is installed.
The [UX workflow](workspace/WORKFLOW.md) opens a canvas with request/history on
the left and the preview/revision composer beside it. Definition, criteria/assets,
stateful design, verification and developer handoff remain accessible. Required
states must have required assertions before criteria approval; the existing
source, browser, release and permission checks remain authoritative.

## Source map

The 2026-09-21 UX change workflow adds saved change requests, screen/overlay/slot
scope, source ID mappings and guarded transitions. An approved React baseline
can be changed only within an explicit source-file allowlist, with actual file
deltas included in the private release manifest. See `workspace/WORKFLOW.md`.

PPTX/XLSX and React source are private reference inputs; large corpora use the
resumable `workspace.prepare_sources` tool and batched authenticated upload.
Imported status never becomes approval, and references do not supply a missing
customer SDK or establish frontend integration. See `workspace/GUIDELINES.md`.

The portal Registry mapping and Registry listing are read-only. MCP reference seeding uses the existing
IAM-only admin Lambda with `op: "seed_mcp_servers"`; opening the portal never
creates approved records. Queued HTML and React generation both recheck current
project membership and product criteria before reading source blobs or invoking models.

| Directory | Responsibility |
| --- | --- |
| `api/`, `web/` | WebSocket handlers, common services and React/Vite/TypeScript SPA |
| `engine/`, `graph/`, `semantic/` | LLM boundary, GraphRAG, Vector RAG, metric definitions |
| `registry/`, `agentcore/`, `agents/` | Registry lifecycle, execution dispatch, Gateway tools, Strands image |
| `onprem/`, `bridge/` | Internal exact lookup, calculations, masking, audit storage and vector search |
| `report/` | Separate Reader/Writer handlers; structured handoff and DynamoDB streaming relay |
| `screengen/`, `gates/`, `skills/` | Legacy F5 generation and runtime prompt rules |
| `design_loop/`, `studio/` | Process and HTML Studio workflows |
| `workspace/`, `react-kit/` | Project workflow, actual React build, browser verification, release and export |
| `workbench/` | Knowledge ETL, versioned private projections, impact tasks, Skill Creator, synthetic pension and report workspace |
| `privacy/` | Private detector gateway, IAM relay, manifest renderer and offline training preparation |
| `infra/`, `seed/`, `schema/`, `tests/` | CDK, synthetic fixtures, ontology schema and tests |

## Local checks and operations

Run from `platform/`. Install the dependencies and browser assets required by
each suite as shown in [CI](../.github/workflows/platform-ci.yml); browser tests
need Chromium and axe. The following commands are available, not results of
this documentation audit:

```bash
python3 seed/generate.py
python3 seed/corpus.py
python3 -m pytest tests/ -q
(cd gates && npm ci && npm test)
(cd react-kit && npm ci --ignore-scripts && npm test)
(cd web && npm ci && npx tsc --noEmit && npm run build && node --test test/*.test.cjs)
python3 cli.py admin health
python3 cli.py admin seed_agents
python3 cli.py admin reset_demo
```

`seed/out/corpus.embeddings.json` is committed; other generated corpus files
are normally ignored. CI also synthesizes CDK and checks workspace/privacy
boundaries and secret-like patterns.

For an intended deployment, `deploy.sh` assembles Lambda packages, vendors
boto3/botocore, prepares agent/skill contexts, deploys the main stack, and normally
seeds data and uploads the web build:

```bash
bash deploy.sh --plane
GRAPH_BACKEND=neptune bash deploy.sh
GRAPH_BACKEND=neptune bash deploy.sh --no-web --no-seed
```

`MAIN_STACK` defaults to `BankPlatformCore` in this script; direct CDK defaults to
`BankPlatform`. `GRAPH_BACKEND` defaults to `local`. The script does not forward
privacy contexts: preserve the reviewed `mydataPrivacyFunctionArn` and current
plane/graph contexts when operating a privacy-enabled main stack. Use the
[privacy runbook](infra/README-privacy.md) for the optional stack and manifest.

Destructive cleanup commands exist as `bash teardown.sh` and
`bash teardown.sh --all`. The first destroys `BankPlatformPlane`; `--all`
currently targets `BankPlatform`, not the deployment script's default
`BankPlatformCore`, and neither removes `BankPlatformPrivacy`. Inspect the actual
stack selection before using them. Removing the plane does not automatically
reconfigure an already deployed API for local fallback.

## Visual design-asset portal

The `#/portal` view previews the Studio React kit and exact-ID reference
implementations for the 22 named seed component versions. Source files,
version changes, and interactive examples share verified source hashes.
Recorded UX steps and relationships use a locally bundled Mermaid renderer.
Generated volume-test widgets are explicit placeholders; other entries without
matching source remain unlinked. See the [preview contract and workflow](docs/PORTAL_PREVIEWS.md);
rendering alone is not customer approval or UX execution validation.

## Dated evidence and remaining limits

These are inherited reports, not fresh verification of the current commit:

| Date | Recorded scope |
| --- | --- |
| 2026-09-02 | Local Strands container smoke against real Gateway/Bedrock; see the agent README. |
| 2026-09-03 | S1/S2/S3/S5, Registry, boundary view and Reader/Writer live rehearsal. Reported Neptune 3,797 nodes/11,052 edges and AOSS 177 documents are historical counts. |
| 2026-09-05 / 2026-09-07 | Process generation and HTML Studio loop rehearsals. These do not verify React releases. |
| 2026-09-11, `7325c72` | Reported 784 Python and 41 UI tests; four Astra/Fable React runs passed in two rounds. Two selected approvals passed AWS rebuild/browser/release checks and authenticated ZIP hash checks. Original-image comparison was not run; release comparison used the approved screenshot. |
| 2026-09-12 | Existing EKS topology inspection recorded controller/policy prerequisites. See the privacy runbook; recheck before deployment. |

The earlier deployment report named `agent.atomai.click` and `BankPlatformCore`;
current availability and deployment revision require live evidence. Demo
credentials belong in the configured secret, not this README.

Known integration gaps: customer React package and Git connection are not bundled;
optional training has no trainer/image/promotion implementation; privacy deployment
readiness and model artifact revision need environment verification. The
[contract audit notes](docs/CONTRACTS.md#8-audit-notes) record source contradictions.
The [designer guide](../docs/14-demo/studio-designer-guide.md) and
[file-intake guide](../docs/14-demo/studio-file-intake.md) provide usage context.
