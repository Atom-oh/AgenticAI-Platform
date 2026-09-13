# Platform v4 — historical control-room plan

This file preserves the v4/v5/v6 decisions for **`demo/builder-harness/`**, originally organized around ten platform capabilities. It is not the current main banking-platform plan and is not an instruction to rerun deployments or commits. Root `SPEC.md` section numbers identify main-platform requirements; this historical version number is unrelated to SPEC §7-1's React workspace.

## Original design and current mapping

The intended control-room path was browser PKCE/Cognito → CloudFront origin verification → API Gateway JWT authorizer → site Lambda → DynamoDB configuration and shared AgentCore Harness. AgentCore Registry, Gateway, and S3 skills provided separate governance/tool/publishing integrations.

| Historical workstream | Checked-in implementation | Qualification |
|---|---|---|
| Identity and team self-service | `site/static/app.js`, `site/lambda_function.py` principal/admin/ownership handling | Deployed auth configuration needs verification; team ownership is not universal read/use isolation |
| Central MCP | `gateway/tools_function.py`, `gateway/target.json` | Separate Gateway tools; generated-agent chat denies tools |
| Agent Registry | `registry_register`, `registry_sync_execute`, status operations in the site Lambda | Local approval can precede best-effort synchronization |
| Agent skills | S3 publishing, Registry registration, and `skills_block` | Published text is injected into the prompt; not a demonstrated S3 per-invocation skill mount |
| AI-ready ontology | Entity/relation CRUD and `ontology_context` | Control-room data model, not the main Neptune graph |
| AI wiki | Markdown CRUD/history and Gateway lookup | Shared demo knowledge, not complete document entitlements |
| Coverage graph | Control-room SPA graph | UI of stored relationships, not proof of authorization coverage |
| Workflow builder | Async workflow execution/history with four-step/four-loop caps | Bounded demo DSL; no general orchestration guarantee |
| Frontend | Bundled `site/static/` served by Lambda | Separate SPA from the main React app |
| Governance/evaluation | Background smoke evaluation, admin actions, audit, usage, budgets | Gaps remain in evaluation depth, sync, atomic budgets, and entitlement coverage |

The earlier checklist marked v4 implementation complete and reported 12/12 E2E checks. That was a historical result, not a current test run or production-readiness certification. `site/e2e.sh` is a live, mutating demo script; inspect its credential handling and target before use.

## v5/v6 decisions retained

- The control room models a fictional securities-company employee platform with wiki/data sources, domain ontology, agents, and a briefing workflow.
- `crawler/crawler_function.py` collects news headline metadata into a fixed demo data source. The six-hour EventBridge schedule was a deployment note, not infrastructure independently established by this Markdown.
- Creation became asynchronous: `EVALUATING` → smoke result → local approval/pending → Registry synchronization, to move long work outside the request path.
- `seed_employees.py` supplies synthetic employee records. Personal HR prompt context uses the server-derived principal, not a caller-selected employee identity.
- Model/usage figures and Korean product copy were demo choices, not a guarantee of current service availability, pricing, or customer deployment.

## Historical deployment identifiers

Recorded resources: CloudFront `E3ETCXKSTRXQAT`, API `00l4tzkyqi`, Lambda `agentic-book-demo-site`, table `agentic-book-demo-registry`, Harness `AgenticBookBuilderDemo-6R0pXEwrY1`, and Registry `b2hOSZL4eOhDXAyk` in `us-east-1`. These identify the old control-room deployment; inspect current configuration before acting on them.

The site package contains `lambda_function.py` and `static/`. Configuration, IAM policies, Gateway payloads, seed scripts, and crawler source remain under `demo/builder-harness/`. Retrieve deployment credentials privately; never copy a password/token into this plan or use an old destructive seed command as a default.

See [control-room README](./builder-harness/README.md) for implementation scope and [security/governance](./SECURITY-GOVERNANCE.md) for requirements and remaining gaps. This history grants no policy exception and does not establish current deployment readiness.
