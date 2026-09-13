# UI/UX Studio PoC — historical implementation plan

**Date: 2026-08-31. Status: historical design/implementation sequence.** The original path was `hana/uiux-platform/` in the account-test repository; it now lives at `demo/uiux-studio/`. This is not an active task list or an instruction to recreate resources, commit, or deploy. Use [the current README](../../README.md), [original design context](../specs/2026-08-31-uiux-agentic-platform-design.md), and `demo/SECURITY-GOVERNANCE.md` when reviewing this product.

## Goal and constraints retained

Demonstrate external Figma token/component ingestion → shared AgentCore Gateway MCP → shared skill registry → Strands on AgentCore Runtime → CloudFront HTML gallery. The recorded deployment target was account `180294183052`, Seoul (`ap-northeast-2`). The selected implementation used Python, Python CDK, AWS SDK calls, and local/stubbed tests.

Decisions were: explicit Cognito no-self-registration, administrator-created identities, private S3 origins through CloudFront OAC, privately provisioned temporary Figma credentials, and a three-variant single-screen design direction. Never infer Seoul-only inference from a regional resource or a `global.*` model identifier. The original default model ID was `global.anthropic.claude-sonnet-5`; current availability requires separate verification.

The historical no-public-compute intent does not certify every generated artifact as private or waive IAM/authentication issues. The later Studio uses an IAM Function URL with OAC. The main React workspace's private file intake and release rules are separate requirements under root SPEC §7-1.

## Task sequence and resulting evidence

| Original task | Purpose | Current evidence or limit |
|---|---|---|
| 1. Scaffold and normalizer | Normalize a Figma file into reusable tokens and component metadata | `ingestion/normalizer.py`, `tests/test_normalizer.py` |
| 2. Figma sync | Fetch the source file and persist normalized assets | `ingestion/figma_sync.py`, `tests/test_figma_sync.py`; no image export or scheduled-sync implementation |
| 3. MCP tools | Share token, component, brand, and skill queries | `mcp/asset_tools.py`, `mcp/tool_schemas.py`; six initial tools, eight after Phase 2 |
| 4. Skill seeds | Fixed design-system, HTML-output, and accessibility instructions | `skills/`, `scripts/sync_skills.py`; instructions are not executable test gates |
| 5. Infrastructure | Buckets, tables, Lambdas, Cognito, CloudFront, roles, secret shell | `infra/stack.py`, `tests/test_stack.py`; checked-in IaC is not deployment proof |
| 6. Gallery | Showcase cards and draft manifest | `gallery/index.html`; later extended beyond the initial read-only gallery |
| 7. Gateway/config | Configure the shared MCP endpoint and deployment outputs | `scripts/deploy_gateway.py`, `write_config.py` |
| 8. Runtime harness | Asset-grounded HTML with density/emphasis/flow variants | `harness/app.py`; variant/markup constraints are prompt instructions |
| 9. Runtime lifecycle | Package, deploy, invoke, and tear down the Runtime | `scripts/deploy_runtime.py`, `invoke.py`, `teardown.py`; mutating operational tools |
| 10. End-to-end exercise | Seed Figma, call MCP, generate, and inspect gallery output | `scripts/verify_e2e.py`; needs live authorized services and creates artifacts |
| 11. Feedback loop | Approve/reject and reuse approved patterns | `feedback/handler.py`, `harness/publish.py`; context reuse, not training or the main React approval contract |

The long original inline code/test/deployment recipes are superseded by these source files. They must not be applied over newer implementations.

## Design decisions preserved

- Tokens define the intended palette/type/spacing; component metadata supplies semantic usage guidance.
- The default output is a card-sectioned mobile screen with no fake device chrome and a 44-pixel minimum hit-target instruction.
- Three ordinary-generation variants each move a named axis: density, emphasis, or flow. Random recombination was rejected as too similar.
- Approval copies HTML into shared few-shot patterns; the harness reads up to two recent approved references.
- AgentCore API shapes, container architecture, authentication, and cleanup dependencies require verification in the target environment. Earlier CLI examples are not permanent API contracts.
- Temporary Figma credentials must be revoked through the approved provider procedure after use. Deleting a local/cloud secret record alone is not evidence of provider-token revocation.

## Historical observations and superseded assumptions

The 2026-08-31 notes reported token/metadata ingestion, six initial tools, three draft variants, and reuse of approved HTML on a later run. This is dated PoC evidence, not a current test pass or broad UX/security certification.

“Read-only gallery,” “M2M clients only,” and “unauthenticated writes” described early stages. Current code includes human designer login, async generation, assets/history, and playground/refinement. Public reads, the compatibility feedback branch before authentication, direct HTML publication, wildcard IAM, and credential configuration remain concerns; later login does not resolve all of them.

Image exports, a disabled daily ingestion schedule, Figma write-back, full tenant isolation, semantic draft search/graph expansion, and automated skill revision were not established by this plan. See [Phase 2 history](./2026-08-31-phase2-asset-platform.md) for the asset-platform extension.
