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
1. `cd infra && pip install -r requirements.txt && cdk deploy` → writes `config/stack.json`
2. `aws secretsmanager put-secret-value --secret-id hana/figma-token --secret-string '<PAT>'` (operator; PAT never committed)
3. `python scripts/deploy_gateway.py` → Gateway MCP URL into `config/stack.json`
4. `python scripts/deploy_runtime.py` → builds/pushes harness image, creates Runtime
5. `python scripts/sync_skills.py` → seed skills to registry bucket
6. `aws lambda invoke --function-name hana-figma-sync --payload '{"file_key":"<from config/figma.json>"}' out.json`
7. `python scripts/verify_e2e.py` → full pipeline check
