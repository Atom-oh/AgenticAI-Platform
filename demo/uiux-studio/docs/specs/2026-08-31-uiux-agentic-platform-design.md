# Original UI/UX Studio — historical PoC design

**Date: 2026-08-31.** The design was recorded as user-approved with spec review pending. It targeted the former `hana/uiux-platform/` project in account `180294183052`, Seoul. The migrated implementation is now `demo/uiux-studio/`. This record preserves decisions; [the current README](../../README.md), code, and `demo/SECURITY-GOVERNANCE.md` establish present scope.

This was an external-service HTML-design PoC. It is separate from root `SPEC.md` §7-1's main React workspace, which accepts approved files without a Figma connection and creates private verified React releases.

## Goal and chosen interface

Demonstrate shared design assets and organizational instructions: Figma ingestion → Gateway MCP → skill registry → AgentCore Runtime generation → CloudFront gallery. The selected gallery direction was a prompt-first showcase with large preview cards. The original canvas reference was <https://claude.ai/code/artifact/cf92ef19-6fb2-40b5-b001-6cdacddd64d5>; local design references remain in `design-canvas/`.

The chosen draft direction was a mobile single screen with card sections, a primary action, no fake device chrome, and 44-pixel minimum hit targets. Hana-themed green/ink/off-white tokens and Noto Sans KR were design inputs, not evidence of customer approval or compliance. Korean copy and bank-like values in samples are fixtures.

## Components and implementation boundary

| Component | Design decision | Checked-in result |
|---|---|---|
| Figma ingestion | Normalize source tokens/components into shared S3/DynamoDB assets | `ingestion/figma_sync.py` fetches a file; `normalizer.py` extracts tokens/metadata. Image export and a disabled daily schedule were deferred |
| Shared MCP | One Gateway with token/search/component/guideline/skill tools | Six initial tools, later extended by generic asset list/get; schemas in `mcp/tool_schemas.py` |
| Identity | Disable self-registration; admin-created accounts and M2M access | M2M plus a later human designer client exist. The initial “M2M-only” limit is historical |
| Skill registry | Versioned `SKILL.md` seeds in S3 | `scripts/sync_skills.py` and Gateway skill retrieval; not the nonexistent historical `sync-skills.sh` recipe |
| Harness | Strands in AgentCore Runtime, originally configured with `global.anthropic.claude-sonnet-5` | `harness/app.py`; model selection is configurable, availability unverified by this document |
| Variants | Three outputs, each changing density, emphasis, or flow under token guidance | Prompt-directed HTML generation; not an enforced visual regression guarantee |
| Gallery | CloudFront with private S3 origin | Public gallery artifacts through CloudFront; origin privacy is not private viewer access |
| Feedback | Approve/reject, copy approved HTML, reuse two recent patterns | Manifest/S3 operations in `feedback/handler.py` and `harness/publish.py`; not transactional React approval |

## Constraints and unresolved work

Keep temporary Figma credentials in the approved secret channel and revoke them after use; no credentials belong in this design record. The 2026-09-03 permission for a synthetic demo access page was limited to that page, not a general waiver for source, logs, or other documentation.

Cognito no-self-registration, authenticated protected writes, origin protection, least privilege, and trustworthy approval evidence remain requirements. Early unauthenticated feedback was an implementation limitation, not a security exception. Current normal POST routes authenticate, but the compatibility branch, public reads, same-origin generated HTML, wildcard IAM, and credential configuration still require attention.

The original out-of-scope items included Figma write-back, multiple-tenant isolation, private-network deployment, and a general approval backend. Later work added human login, web generation, assets/history, memory, and refinement; these must not be described as still entirely absent. Full React release verification and private customer-project artifacts belong to the main platform, not this gallery.

Embedding search over approved drafts, draft-to-asset graph relationships, external brief intake, and automatic skill-revision batches were proposed extensions. This record does not claim they were built. Error handling must be checked in source; planned retry/partial-success behavior is not an implementation guarantee.

## Historical verification and operations

The 2026-08-31 record reported Figma token/metadata sync, initial MCP calls, three generated drafts, and a later generation using approved-pattern context. It did not prove all file formats, all models, comprehensive accessibility, or production security. Earlier claims that a temporary credential was valid/revoked or a service was available need current operator evidence.

`tests/` covers normalizer, tools, publication, feedback, dispatch, and infrastructure assertions. `scripts/verify_e2e.py` exercises live services and creates artifacts. Deployment/teardown scripts and CDK removal policies are operational tools, not authority to recreate or delete historical resources. Current setup dependencies are in [the README](../../README.md); the [implementation plan](../plans/2026-08-31-uiux-agentic-platform.md) and [Phase 2 plan](../plans/2026-08-31-phase2-asset-platform.md) preserve the delivery sequence.
