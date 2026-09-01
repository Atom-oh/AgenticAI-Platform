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
3. `aws secretsmanager put-secret-value --secret-id hana/figma-token --secret-string '<PAT>'` (operator; PAT never committed; once)
4. `python scripts/deploy_gateway.py` → Gateway MCP URL into `config/stack.json` (updates target to 8 tools)
5. `python scripts/sync_skills.py` → seed skills to registry bucket
6. `python scripts/deploy_gallery.py` → uploads `gallery/` to the drafts bucket (CloudFront origin) and invalidates the CloudFront cache
7. `python scripts/deploy_runtime.py` → builds/pushes harness image, creates Runtime (also refills the dispatcher's `RUNTIME_ARN`)
8. `aws lambda invoke --function-name hana-figma-sync --payload '{"file_key":"<from config/figma.json>"}' out.json`
9. `python scripts/verify_e2e.py` → full pipeline check

WARNING: any bare `cdk deploy` resets the dispatcher's `RUNTIME_ARN` environment
variable back to `""`. After any `cdk deploy`, rerun `scripts/deploy_runtime.py`
(or step 7 above) to refill it — otherwise generation jobs will fail.

Per-invoke cost note: each `InvokeAgentRuntime` call runs 3 full HTML draft generations
plus up to 2 approved patterns injected into the prompt as few-shot references — budget
model spend accordingly.

PoC limitation (documented): the API is unauthenticated in this PoC, which now
includes writes (asset registration) — production must put Cognito in front.

## Deployed endpoints (PoC, 2026-08-31)

- Gallery (CloudFront): https://d4zwmnh2s47e9.cloudfront.net/
- Shared design-asset MCP (AgentCore Gateway): https://hana-design-assets-gw-kwzg6g7rhz.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp (Cognito M2M JWT)
- Harness runtime: arn:aws:bedrock-agentcore:ap-northeast-2:180294183052:runtime/hana_design_harness-2G8fU3CCa4
- Figma seed file: https://www.figma.com/design/LsM27cpiDij9PSQfAsTAys

E2E verified: figma-sync (6 components) → gateway MCP (6 tools) → harness (3 axis
variants per brief) → gallery; feedback approve → approved-patterns → few-shot
uptake confirmed on a second invoke. Reminder: revoke the temporary Figma PAT
after the PoC (`aws secretsmanager delete-secret --secret-id hana/figma-token`).

## Phase 2 (2026-08-31) — asset platform

Live at the same gallery URL: 자산 탭 (7종 자산 등록 + 버전 히스토리) · 생성 탭 (자산 선택형
비동기 생성). E2E verified: web-registered purple palette + workflow asset → selective
generation → drafts render with the registered palette (#7d2882); history v1 recorded.

## Phase 3 (2026-09-01) — commercial-grade designer platform

- **디자이너 로그인 (AgentCore Identity 경로의 Cognito)**: 관리자 발급 계정(`scripts/create_designer.py`),
  SPA 로그인 → `x-hana-auth` 토큰. 모든 쓰기 API는 로그인 필수 (무인증 401).
- **Bedrock 전 모델**: `GET /api/models`가 활성 inference profile 전체(27+)를 노출, 생성 시 모델 선택.
- **에이전트 공유**: 자산 타입 `agent` — 시스템 프롬프트·모델·자산·스킬 프리셋을 조직에 공유,
  생성 탭에서 선택 실행.
- **AgentCore Memory** (`hana_design_memory`): 생성/승인/반려가 디자이너별 이벤트로 기록되고,
  다음 생성에서 시맨틱 추출된 취향이 프롬프트로 회수됩니다 (추출은 비동기 — 수 분 소요).
- 잡 목록 영속화(`GET /api/jobs`) + 토큰 사용량/모델 표시.
- 배포 추가 단계: `scripts/deploy_memory.py` (cdk deploy 후, deploy_runtime 전) ·
  `scripts/create_designer.py <id> <pw>`.
