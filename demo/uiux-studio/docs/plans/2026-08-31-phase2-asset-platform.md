# Phase 2 asset platform — historical plan

**Date: 2026-08-31. Status: historical.** This extended the original UI/UX Studio from a gallery into asset registration/history and selective async generation. It is not the main React workspace and is not a pending implementation checklist. See [current Studio behavior](../../README.md), [Phase 1 history](./2026-08-31-uiux-agentic-platform.md), and [original design](../specs/2026-08-31-uiux-agentic-platform-design.md).

## Decisions retained

The feedback Lambda became a routed API for assets, history, generation, jobs, and feedback. Generation enqueued a job and asynchronously invoked a dispatcher, which invoked AgentCore Runtime. Selected user assets were fetched through Gateway `get_asset`; the shared skill/pattern stores remained separate reference sources.

The original seven asset-type literals were `token`, `palette`, `icon-set`, `component`, `style-guide`, `skill`, and `workflow`. Current `feedback/assets_api.py` also accepts `agent`; this eighth type is a later addition, not a contradiction in the old milestone.

User content is versioned under `user-assets/<asset_id>/v<version>.<json|md>`, with a current registry pointer and history records. Registered skills are also published under their generated asset IDs in the skills bucket; they must not overwrite deployment-managed seed skills. Figma sync remains a separate pipeline. A Figma URL entered as metadata does not implement ingestion from that URL.

## Workstreams and evidence

| Historical task | Current source | Scope |
|---|---|---|
| P2-1: API router and history | `feedback/handler.py`, `feedback/assets_api.py`, `tests/test_assets_api.py` | Asset CRUD/history, async job creation, feedback |
| P2-2: Generic MCP tools and dispatcher | `mcp/{asset_tools,tool_schemas}.py`, `dispatch/handler.py`, `harness/app.py` | Added list/get asset tools; selected asset context; stored job results |
| P2-3: Infrastructure | `infra/stack.py`, `tests/test_stack.py` | History table, dispatcher, grants; dispatcher Runtime ARN filled by a deployment script |
| P2-4: Gallery/assets/generation UI | `gallery/index.html`, `design-canvas/` | Original three-tab direction; later playground/login functionality supersedes the session-only job assumption |
| P2-5: Deployment and E2E | `scripts/`, `tests/` | Separate authorized live verification, not established by a plan checkbox |

The intended UI showed asset purpose/scope/version/history, selected assets for a brief, and polled job status. A rollback control was deferred. The original “jobs exist only in the page session” assumption was superseded by persistent job-list API/UI support.

## Historical evidence and limitations

The 2026-08-31 report described registration of a purple palette (`#7d2882`) plus a workflow, generation using that selected palette, and a recorded first version. The color is a synthetic test fixture, not the default brand token. A matching color in HTML does not establish accessibility, full workflow correctness, or arbitrary asset fidelity. The plan's earlier 24-test baseline was historical, not the current suite size.

The initial open-write design was later replaced for normal POST routes by designer-token checks. Public reads and the compatibility feedback branch still require security assessment. Origin access control is not user authorization, asset scope labels are not tenant isolation, and historical open access is not a policy waiver.

Cognito no-self-registration, private credential provisioning, and origin protection remain constraints. Review `demo/SECURITY-GOVERNANCE.md` for current gaps. Do not copy old deployment/commit recipes over the current source or treat these milestones as the main workspace's file-intake and approval requirements.
