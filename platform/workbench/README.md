# Project workbench

The workbench extends the existing Workspace API, storage, worker and React
application. It is separate from the legacy WebSocket scenarios and Registry.
See [CONTRACT.md](CONTRACT.md) for request and evidence schemas and
[the Korean walkthrough](../../docs/14-demo/ontology-workbench.md) for usage.

## Implemented paths

| Path | Responsibility |
|---|---|
| `api.py`, `service.py` | Verified identity, current project membership, operator checks, CAS writes, HTTP and stateless MCP |
| `collectors.py`, `knowledge.py` | Bounded approved source collection, immutable snapshots, permissions, numeric vectors, typed graph and generation publication |
| `impact.py` | Directed dependency traversal, evidence paths and role-specific work items |
| `skills.py` | Content packages, validation, approval, deprecation and exact version/hash resolution |
| `business.py` | Synthetic pension facts, guarded explanations, feedback and evidence-backed Markdown reports |
| `worker.py` | Asynchronous authorization/source rechecks and failure reporting |
| `fixtures.py` | Explicit fictional, project-local examples; no shared Registry reset |
| `web/src/workbench/` | Planning, design handoff, developer tasks, knowledge, Skill Creator, pension, reports and operator pages |

The initial knowledge backend stores real numeric feature-hash vectors and typed
adjacency data in the workspace private artifact store. It is **not** OpenSearch,
Neptune or a semantic embedding model. Coverage and backend identity are returned
with retrieval and impact results. This scoped baseline does not replace the
bank platform's other graph/vector adapters.

Source revisions, permission versions, storage record versions and published
index generations are distinct. Publication requires both projections. Current
source permission/tombstone checks remain authoritative when reading an older
generation; rollback cannot restore revoked access. Declared relations remain
declared evidence. A missing mapping is unknown, not proof of no impact.

## External source configuration

`workbenchConnections` CDK context contains server-owned profiles keyed by
connection ID. API requests select an ID; they cannot supply arbitrary endpoints
or credentials. Each profile binds an approved HTTPS source, explicit project
IDs, roles, collection scope and a Secrets Manager reference. The workspace
worker receives only exact configured secret read permissions.

The user requested preparation documentation only for Confluence; no real target
is to be connected. See [CONFLUENCE.md](CONFLUENCE.md). The console shows connection
readiness separately from successful ETL. A reachable service account alone
does not authorize project members to read everything it can collect. Do not
deploy an internal source profile until network reachability and effective ACL
mapping have been verified in that environment.

The authenticated MCP endpoint is `/studio-api/workbench/mcp` with the same
Bearer token and `X-Workspace-Project` header as the UI. It exposes bounded
knowledge/evidence/impact/approved-skill reads. Registering a tool declaration
does not provision an AgentCore Gateway target, create network access or grant
permissions. Existing AgentCore runtime/CLI and builder integration remains in
`agentcore/`, `agents/` and `api/handlers/agents.py`.

## Pension and reporting boundaries

Pension fixtures are synthetic. The versioned Decimal calculator owns monetary
values. Assumptions, rounding, excluded factors and fact hashes travel with the
result. The baseline is a deterministic explanation. A model answer is buffered,
selects known metric IDs. The server renders each selected metric's label,
amount and KRW unit; model-authored surrounding financial claims are not
displayed. Independent output inspection still precedes publication.

Free-text model questions require the private MyData processor. Server-authored
synthetic questions have a distinct receipt; they do not silently enable arbitrary
free text. Worker identity, expiry and the current fact hash are checked again.
Changing assumptions makes earlier answers stale. Feedback binds a real answer.
Preset feedback is server-authored. Free-text feedback requires private
processing; unavailable processing prevents persistence of the raw comment.

Reports are immutable private Markdown templates with source references and an
exact content hash. Missing evidence prevents approval. Source permissions are
rechecked on document reads, and source versions are rechecked on approval.
These are internal review drafts, not automated credit decisions, regulatory
interpretations or verified customer financial reports.

## Verification

Run from the repository root with the platform Python dependencies installed:

```bash
python -m pytest platform/tests/test_workbench_*.py -q
```

Run the complete suites and build commands in `.github/workflows/platform-ci.yml`
before integration. `platform/tests/workbench_preview.py` is a loopback-only
synthetic rehearsal utility using the real API and in-memory test storage.
It is excluded from deployment images and must never be used as a production
authentication path. The capture script uses an explicit synthetic login double;
application API requests are exercised end to end.
