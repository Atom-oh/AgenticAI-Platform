# Hana UI/UX Agentic AI Platform (PoC)

Org-wide agentic platform for UI/UX work: Figma ingestion → shared design-asset
MCP (AgentCore Gateway) → shared skill registry → design-draft harness
(AgentCore Runtime) → CloudFront gallery.

Spec: docs/specs/2026-08-31-uiux-agentic-platform-design.md

## Layout
- ingestion/  Figma sync Lambda + normalizer
- mcp/        Gateway Lambda tools + tool schemas
- skills/     Org skill registry seed content
- harness/    Strands agent container for AgentCore Runtime
- gallery/    Showcase gallery static frontend
- infra/      CDK app (Python)
- scripts/    Gateway/Runtime deploy, e2e verify, teardown

## Deploy (order matters)
1. `cd infra && pip install -r requirements.txt && cdk deploy` → writes CloudFormation outputs
2. `python scripts/write_config.py` → writes `config/stack.json` from the stack outputs
3. `aws secretsmanager put-secret-value --secret-id hana/figma-token --secret-string '<PAT>'` (operator; PAT never committed)
4. `python scripts/deploy_gateway.py` → Gateway MCP URL into `config/stack.json`
5. `python scripts/sync_skills.py` → seed skills to registry bucket
6. `python scripts/deploy_gallery.py` → uploads `gallery/` to the drafts bucket (CloudFront origin)
7. `python scripts/deploy_runtime.py` → builds/pushes harness image, creates Runtime
8. `aws lambda invoke --function-name hana-figma-sync --payload '{"file_key":"<from config/figma.json>"}' out.json`
9. `python scripts/verify_e2e.py` → full pipeline check

Per-invoke cost note: each `InvokeAgentRuntime` call runs 3 full HTML draft generations
plus up to 2 approved patterns injected into the prompt as few-shot references — budget
model spend accordingly.
