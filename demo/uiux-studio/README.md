# Original UI/UX Studio

This is the original designer PoC, recorded as migrated from the `uiux-platform/` project in the earlier account-test repository on 2026-09-02. Its external Figma ingestion, shared MCP assets, Strands Runtime, and HTML gallery are separate from the main React workspace in `platform/workspace/`.

Recorded gallery: <https://d4zwmnh2s47e9.cloudfront.net/>. Obtain current administrator-issued access through the approved credential channel; see [security/governance](../SECURITY-GOVERNANCE.md). A historical endpoint is not current deployment evidence.

## Current code map

| Directory | Implemented purpose |
|---|---|
| `ingestion/` | Figma file fetch, token normalization, and component metadata; no implemented image-export/scheduled-sync pipeline |
| `mcp/` | Eight tool schemas: tokens, asset search/component/guideline, skills, and generic asset list/get |
| `skills/` | Original prompt instruction seeds, published by `scripts/sync_skills.py` |
| `feedback/` | Asset/history/job/model API, feedback, and designer-token verification |
| `dispatch/` | Async job dispatcher to AgentCore Runtime |
| `harness/` | Strands HTML generation/refinement, publication, few-shot references, and optional memory |
| `gallery/` | Static gallery/playground frontend |
| `infra/`, `scripts/` | Python CDK plus deployment, seed, verification, and teardown tooling |
| `tests/` | Stubbed/local unit and infrastructure assertion tests |

Asset registration currently accepts `token`, `palette`, `icon-set`, `component`, `style-guide`, `skill`, `workflow`, and `agent`. “Seven types” describes the earlier Phase 2 baseline. `GET /api/models` lists active system inference profiles visible to the configured region; no fixed model count or all-model invocation guarantee follows.

## Generation and feedback

Ordinary generation prompts for three HTML variants organized by density, emphasis, and flow. Refinement prompts for a selected-element change and publishes a new draft with `parent_id`. Preserving unrelated markup is a prompt instruction, not an enforced DOM/pixel regression guarantee. Output types are prompt styles, not separate compilers.

The Runtime can also use `mode: "flow"`, packaging `platform/design_loop/` and its seed data through `scripts/deploy_runtime.py`. This is a separate process-design/report path, not the main React release contract.

Feedback updates the gallery manifest and copies approved HTML into `approved-patterns/`; later generation can load up to two references. Optional AgentCore Memory records/recalls designer context on a best-effort basis. Neither mechanism trains model weights or guarantees learning. Generated HTML is published through CloudFront; it is not a private customer-artifact workspace.

## Authentication and remaining concerns

The stack disables Cognito self-registration, supplies human SPA and M2M clients, and uses an `AWS_IAM` Function URL behind CloudFront OAC. Normal POST routes require a designer access token through `x-bank-auth`. Therefore the original “all APIs are unauthenticated” description is stale.

However, `feedback/handler.py` retains a no-`rawPath` compatibility POST before authentication; asset/job reads and gallery artifacts are not private per-user resources. Generated HTML/same-origin previews, unconditional wildcard IAM grants, and runtime credential configuration also need remediation/review. These are gaps, not policy exemptions. Do not call this implementation production-ready or assign it the main workspace's approval/browser guarantees.

## Deployment dependencies

These are code dependencies, not a command to deploy an unchanged historical configuration:

1. Review current account/region, policies, secret handling, and resources in `infra/`.
2. After an authorized infrastructure deployment, `scripts/write_config.py` records outputs in `config/stack.json`.
3. Provision the temporary Figma credential through the approved secret process; never paste it into Markdown or command transcripts.
4. `deploy_gateway.py` configures the eight tools; `sync_skills.py` publishes seed instructions; `deploy_gallery.py` publishes gallery files.
5. If using designer memory, run the authorized `deploy_memory.py` step before Runtime configuration.
6. `deploy_runtime.py` packages the Runtime and fills the dispatcher's `RUNTIME_ARN`.
7. Validate ingestion, generation, authentication, and feedback in the intended environment using approved synthetic inputs.

CDK declares an empty dispatcher `RUNTIME_ARN` and feedback `MEMORY_ID`. Redeployment can reset these script-populated settings; reapply the appropriate configuration and verify it. Gateway/Runtime setup and designer-account provisioning are separate steps. Current scripts need security review; this ordering does not bless their credential handling or wildcard grants.

Local tests run from this directory with `python3 -m pytest tests -q` in a provisioned test environment. `scripts/verify_e2e.py` contacts AWS and creates demo artifacts; it is not a read-only/offline check. Teardown scripts and CDK removal policies can delete resources/data; inspect current targets first.

## Historical design and evidence

- [Original design](./docs/specs/2026-08-31-uiux-agentic-platform-design.md)
- [Original implementation plan](./docs/plans/2026-08-31-uiux-agentic-platform.md)
- [Phase 2 asset plan](./docs/plans/2026-08-31-phase2-asset-platform.md)

The 2026-08-31 notes reported six ingested components, six initial MCP tools, three variants, and reuse of an approved pattern. Phase 2 notes reported a custom purple-palette/workflow run and version history. September notes reported login, model selection, memory, and refinement examples. These are bounded historical observations, not current deployment, full accessibility, or arbitrary-edit preservation evidence.

The main workspace instead accepts approved external files without a Figma connection, uses an actual fixed React kit, verifies builds/browser behavior, and stores releases privately. See `docs/14-demo/studio-designer-guide.md` and root SPEC §7-1 for that product.
