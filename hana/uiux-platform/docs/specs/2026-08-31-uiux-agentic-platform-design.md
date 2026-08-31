# Hana UI/UX Agentic AI Platform — PoC Design

Date: 2026-08-31
Status: Approved (user), pending spec review
Root: `hana/uiux-platform/` (other Hana projects may live as siblings under `hana/`)
Target: real AWS deployment · account 180294183052 · region ap-northeast-2 (Seoul)

## Goal

A deployable PoC demonstrating an org-wide Agentic AI Platform for UI/UX work at Hana Bank:

1. Ingest design assets from external SaaS (Figma)
2. Share org-managed design assets via a shared MCP endpoint
3. Share org-wide agent skill sets
4. Generate design drafts through an AgentCore-hosted harness

Verified preconditions: AgentCore Runtime available in ap-northeast-2 (an existing runtime already runs in this account); Claude models reachable via `global.*`/`apac.*` inference profiles; Figma PAT valid (temporary, test-only — stored in Secrets Manager, revoked after PoC).

## Frontend direction (chosen on design canvas)

Canvas: https://claude.ai/code/artifact/cf92ef19-6fb2-40b5-b001-6cdacddd64d5

- **Gallery: Option B — showcase style.** Prompt-first hero ("어떤 화면을 만들어 볼까요?"), generated drafts as large preview cards with status badges (승인됨/검토중/초안). Palette: Hana green #008485 / #00615f, warm off-white #fbfcfb, ink #17332f, Noto Sans KR.
- **Generated-draft default style: single-screen.** Card-sectioned single page (출금계좌 / 받는분 / 금액 / CTA), 44px+ hit targets, no fake device chrome. This style is codified in the `design-draft-html` skill.

## Components

### 1. Figma asset ingestion — `ingestion/`

- **Seed Figma file** created via Figma MCP in this session: Hana-toned design system (color/typography/spacing tokens + core banking components: button, card, account list item, amount input, chips, status badges) matching the approved canvas palette.
- **Lambda `figma-sync`** (Python 3.13, arm64):
  - Reads Figma PAT from Secrets Manager secret `hana/figma-token` (never in code or git).
  - Calls Figma REST API: file document + styles/variables → normalized design-token JSON; component nodes → metadata; `/v1/images` → PNG/SVG exports.
  - Writes token JSON and images to S3 bucket `hana-design-assets-<acct>`; upserts records into DynamoDB `hana-design-registry` (PK `asset_id`, attrs: `type` token|component|icon, `name`, `version`, `s3_key`, `figma_node_id`, `updated_at`).
  - Trigger: manual invoke for the PoC; EventBridge daily rule included but disabled.

### 2. Shared design-asset MCP — AgentCore Gateway, `mcp/`

- **AgentCore Gateway** (Seoul) with one Lambda target `design-asset-tools` exposing MCP tools:
  - `list_design_tokens`, `search_assets`, `get_component`, `get_brand_guideline`
  - `list_skills`, `get_skill` (reads the skill registry)
- **Inbound auth:** Cognito user pool — `selfSignUpEnabled: false` explicit, admin-created users only (org security policy) — plus an M2M app client (client-credentials) for agents and team members. Gateway JWT authorizer validates Cognito tokens.
- Org members connect from Claude Code / Kiro / any MCP client via the Gateway's streamable-HTTP URL + OAuth token.

### 3. Shared skill registry — `skills/` → S3

- S3 bucket `hana-skill-registry-<acct>`, layout `skills/<name>/<version>/SKILL.md` (+ resources).
- Seed skills:
  - `hana-design-system` — how to apply the tokens/components from the asset registry.
  - `design-draft-html` — rules for generating a draft screen. Encodes the approved direction: single-screen card-sectioned layout, Hana palette, Noto Sans KR, 44px hit targets, self-contained HTML, no fake device chrome.
  - `a11y-finance` — 금융권 웹접근성 checklist (KWCAG-informed).
- Consumption: agents fetch at runtime via Gateway `get_skill`; humans sync via `scripts/sync-skills.sh` (`aws s3 sync`).

### 4. Design draft harness — AgentCore Runtime, `harness/`

- **Strands Agents** Python agent, container image → AgentCore Runtime (Seoul). Model: `global.anthropic.claude-sonnet-5`.
- Flow: `InvokeAgentRuntime(brief)` → agent loads `design-draft-html` + `hana-design-system` skills → queries Gateway MCP for tokens/components → generates a self-contained HTML draft → uploads to S3 `hana-design-drafts-<acct>` and appends it to the gallery's `drafts.json` manifest → returns the CloudFront URL.
- **Gallery frontend (Option B showcase):** static site on the drafts S3 bucket behind **CloudFront (OAC)** — the only public entry point; no public compute (org policy). The gallery index lists drafts from a `drafts.json` manifest the harness updates.
- `scripts/invoke.py` demonstrates the end-to-end run.

## IaC & deployment

- **CDK (Python)** app in `infra/`: S3 ×3, DynamoDB, Lambda ×2, Cognito, CloudFront, Secrets Manager secret shell, IAM roles.
- **boto3 deploy scripts** in `scripts/` for AgentCore Gateway + Runtime (CDK support immature; mirrors current AgentCore practice): `deploy_gateway.py`, `deploy_runtime.py`, `teardown.py`.
- Secret value injected post-deploy by operator command (`aws secretsmanager put-secret-value`), never committed.

## Error handling

- `figma-sync`: Figma 429/5xx → bounded retry with backoff; partial failure writes what succeeded and reports skipped nodes in the response.
- MCP tools: unknown asset/skill → MCP error result with message, not exception; DynamoDB/S3 errors surface as tool errors.
- Harness: model/tool failure → structured error response from InvokeAgentRuntime; drafts are only registered after successful S3 upload.

## Testing

- pytest: token normalizer (Figma JSON → token JSON), MCP tool handlers (moto/stubbed AWS).
- Post-deploy `scripts/verify_e2e.py`: sync Figma → call MCP tools with a Cognito M2M token → invoke harness with a sample brief → assert a draft URL renders.

## Cost & teardown

All serverless/on-demand (Lambda, DynamoDB on-demand, S3, CloudFront, AgentCore consumption, Bedrock per-token). `scripts/teardown.py` + `cdk destroy` remove everything; Figma PAT revoked by user after PoC.

## Out of scope (YAGNI)

- Web UI for invoking the agent (PoC uses `invoke.py`; gallery is read-only static).
- Multi-tenant orgs, approval workflow backend (status badges are data-only), Figma write-back, VPC/private networking beyond defaults.
