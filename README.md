# Agentic AI Platform

A bank-platform demo, two companion demos, and an engineering guidebook.
The demos share governance concepts but have separate implementations and deployment
boundaries. Checked-in code is not evidence that every integration is live.

| Area | Source | Current reference |
|---|---|---|
| Bank platform: impact analysis, MyData, Registry, React workspace | `platform/` | [Requirements](SPEC.md), [implementation and operations](platform/README.md) |
| Designer UI/UX demo | `demo/uiux-studio/` | [Studio README](demo/uiux-studio/README.md) |
| Agent control room | `demo/builder-harness/` | [Control-room README](demo/builder-harness/README.md) |
| VitePress engineering guidebook | `docs/` | [Guidebook home](docs/index.md) |

Read [AGENTS.md](AGENTS.md) for development and PR completion, and
[review context](docs/REVIEW_CONTEXT.md) for document authority and known historical
conflicts. [Documentation decisions](docs/decisions/README.md) record scoped changes.

## Development

```bash
npm ci
npm run docs:dev
NODE_OPTIONS=--max-old-space-size=8192 npm run docs:build
```

Platform setup, test, deployment, and cleanup commands live in
[platform/README.md](platform/README.md). GitHub Actions runs the checked-in platform
checks and publishes the guidebook on pushes to `main`. The customer's proposed
GitLab workflow is distinct from this repository's CI.

## Access and evidence

See [demo access](docs/14-demo/index.md) and
[companion-demo governance](demo/SECURITY-GOVERNANCE.md). Cognito accounts are
invitation-only. Retrieve credentials through the authorized secret-management
workflow; do not copy them into source, documentation, or review artifacts.

The bank impact graph supports local and Neptune backends. The React workspace uses
its own project-scoped guideline ontology and real pinned component package. MyData
uses a private EKS privacy processor before the separate Bedrock explanation route.
See the module references for implementation status and dated verification limits.
