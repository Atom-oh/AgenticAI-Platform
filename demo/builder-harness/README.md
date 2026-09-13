# Builder Harness and control-room demo

This directory contains two related examples: a requirement-discussion Harness configured by `create-harness.json`, and a separate employee control-room application in `site/`. It illustrates the guidebook's Part 11 builder/catalog concepts. It is not the main banking application in `platform/` or the original designer Studio in `demo/uiux-studio/`.

The recorded control-room URL is <https://d1twhttjtzqewp.cloudfront.net/>. Obtain current administrator-issued credentials privately; historical URLs and accounts do not establish current availability. See [security and governance](../SECURITY-GOVERNANCE.md).

## Harness concept demo

`create-harness.json` configures a managed AgentCore Harness to discuss requirements and propose an agent plan. The prompt does not deploy child agents. `invoke.py` calls the configured Harness and accepts an optional session ID to continue the conversation.

Run from this directory in an environment with the required AWS SDK and authorized credentials:

```bash
python3 invoke.py "Help me plan a contract-review assistant"
```

`trust-policy.json` and `execution-policy.json` retain the deployment inputs. The recorded Harness was `AgenticBookBuilderDemo-6R0pXEwrY1` in `ap-northeast-2`, using `apac.anthropic.claude-sonnet-4-20250514-v1:0`. These are repository/deployment identifiers, not current model availability or a Seoul-only inference claim.

## Control-room behavior

`site/lambda_function.py` serves bundled `site/static/` assets and handles the API. The intended web path is CloudFront → origin-verification header → API Gateway JWT authorizer → Lambda. The handler reads verified authorizer claims; admin actions and guarded mutations use role/ownership checks. Deployment authorizer/origin settings must still be verified.

| Feature | Actual behavior |
|---|---|
| Builder conversation | Discuss requirements, extract a proposed config, and register it after user review |
| Catalog execution | DynamoDB agent configs share one Harness with per-call system-prompt overrides; creation does not deploy a dedicated runtime |
| Data sources | Up to five selected text sources are inserted into the prompt; per-source limit is 20,000 characters |
| Skills | S3/Registry publishing plus prompt injection of selected stored skill text |
| Ontology/wiki | Stored entity/relation context, Markdown pages/history, and separate Gateway lookup tools |
| Workflow | Async bounded execution and stored run history |
| Personal HR | For opted-in agents, `emp_block` loads the requesting principal's synthetic employee record |

This differs from `platform/api/handlers/agents.py`, whose main-platform creation path manages actual Harness resources.

## Approval, usage, and limits

Creation starts at `EVALUATING`. A background probe sets local `APPROVED` only for Tier 1 with a passing smoke result; other results stay pending. The smoke check tests a minimal response length, not task correctness or comprehensive safety. Manual operations are admin-gated.

AgentCore Registry registration/submission/status updates are separate best-effort operations. Local DynamoDB approval can remain after a Registry failure. Do not call Registry the sole enforced source of truth. Application audit records identify the actor, while AWS API/CloudTrail calls use the site's Lambda role; both audit writes and synchronization can fail.

Generated-agent chat, evaluations, and workflow calls request a deny-tools list. Builder calls are a distinct path. Actor IDs include agent/user-derived strings, but sanitization/truncation is not collision-proof isolation. Team ownership checks do not cover every list/read/chat/linked-resource operation.

Token usage is accumulated after calls. The default 50,000-token budget is checked before a call, without atomic reservation. The code caps catalog size at 30 agents, 15 data sources, and 15 skills; messages at 2,000 characters; workflows at four steps/four iterations. These limits do not establish zero abuse or zero cost risk. Cost figures in code are estimates, not billing evidence.

## Source map

| Path | Purpose |
|---|---|
| `site/lambda_function.py`, `site/static/` | Control-room API and SPA |
| `gateway/` | Central MCP tools, schemas, and policy inputs |
| `crawler/` | News headline metadata collection |
| `seed.py`, `seed_employees.py` | Mutating synthetic setup scripts |
| `create-harness.json`, `invoke.py` | Concept Harness config and invocation |
| `site/distribution.json`, policy/trust JSON files | Historical deployment configuration inputs |
| `site/e2e.sh` | Live/mutating smoke script; not an offline unit-test suite |

Do not copy example credentials from legacy scripts into output. Obtain current credentials through the approved channel and inspect scripts before running them. This documentation update does not remediate credentials or other issues in code/configuration outside its scope.

## Historical lessons and operations

Earlier deployments encountered Harness trust validation requiring the managed runtime ARN, asynchronous deletion/name-reuse conflicts, missing memory permissions, API request timeouts, and model throttling. These are historical troubleshooting observations, not a guarantee that the same service behavior or resource state applies today.

An earlier control-room Function URL approach was replaced by API Gateway after invocation failures; a leftover anonymous URL was reported removed. This does not prohibit the separate Studio's IAM-authenticated OAC Function URL or authorize anonymous URLs anywhere.

The older v2/v3 notes saying the control room had no authentication are superseded by current JWT/role handling. Conversely, v4 “commercial-grade” wording did not prove complete security. Review [the historical v4 plan](../PLATFORM-V4-PLAN.md) and [remaining security gaps](../SECURITY-GOVERNANCE.md).

Deploy or tear down only after resolving the current resources and dependencies. Historical IDs, old successful checks, and earlier teardown commands are not authorization to delete today's deployment. No deployment, seed, or teardown command was run for this documentation reconciliation.
