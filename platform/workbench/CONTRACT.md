# Workbench implementation contract

Implementation scope approved by the user on 2026-09-13. This contract defines
new application APIs; it does not claim live Confluence or customer Git connectivity.
Use synthetic fixtures, not the private source PDF or meeting transcript.

## Integration

HTTP prefix: `/studio-api/workbench`. Reuse the workspace JWT authorizer and
`X-Workspace-Project`. Parent integration calls:

```python
workbench.api.route(api, scope, claims, method, parts, body, query)
# api: WorkspaceAPI; parts omit "workbench"
# return (status_code, JSON object)
```

The module validates current scope/role and every mutation. No caller-supplied
owner or operator flag is trusted. Project mutations use the Collaboration CAS
fence. Operator capability comes from verified JWT groups `platform-operators`
or `admin`; a project owner alone is not an operator. Jobs retain actor, project,
source versions and authorization expiry; workers recheck current membership and
source state before processing/publication.

Parent integration adds Storage kinds:
`wb_source`, `wb_batch`, `wb_index`, `wb_change`, `wb_task`, `wb_skill`,
`wb_artifact`, `wb_pension`, `wb_report`, `wb_tool`.

Async task: existing workspace job `task="workbench"`; `input` includes
`operation`, target ID, actor/project and pinned input/version/hash. API creates
the job through `_new_job` and dispatches `_invoke`; worker calls
`workbench.worker.process(worker, owner, job)` and records the result through its
existing completion/failure machinery.

Errors use CollaborationError/HTTPError or bounded validation messages, never raw
SDK errors. Lists return `{items: [...], cursor?}`. Existing workspace projects,
products, uploads, React runs and releases retain their original APIs.

## API surface

| Method/path | Request | Response |
|---|---|---|
| GET `/overview` | — | `{actorId,role,operator,capabilities,stats,tasks,recentArtifacts,readiness}` |
| POST `/examples` | `{requestId}` | `{productId,sourceId,description}`; private/project synthetic content only, no global Registry reset or network credentials |
| GET `/sources` | `cursor?` | `{items,cursor?}` |
| POST `/sources` | `{requestId,name,kind,connectionId?,scope?,description?}` | `{source}`; operator for external sources, server-defined snapshot/example scope for local authoring |
| GET `/sources/:id` | — | `{source}` |
| POST `/sources/:id/batches` | `{requestId,documents?}` | `202 {batch,job}`; bounded snapshot input or configured collector |
| GET `/batches` | — | `{items,cursor?}` |
| GET `/batches/:id` | — | `{batch}` |
| GET `/knowledge` | `q?,kind?,cursor?` | `{items,generation,coverage,backend,cursor?}` |
| GET `/knowledge/:id` | — | `{document,evidence,generation}` |
| GET `/dependencies` | `targetId?` | `{nodes,edges,coverage,generation}` |
| POST `/dependencies` | `{requestId,nodes,edges,sourceRef}` | `{generation,coverage}`; declared mappings retain declared provenance |
| GET `/changes` | — | `{items,cursor?}` |
| POST `/changes` | `{requestId,title,targetId,changeType,before,after,reason,productId?}` | `{change}` |
| POST `/changes/:id/analyze` | `{version}` | `{change,impact,tasks}`; deterministic typed traversal, source evidence and unknown coverage |
| GET `/tasks` | `role?,status?` | `{items,cursor?}` |
| PUT `/tasks/:id` | `{version,status,assigneeSub?,evidenceRefs?}` | `{task}`; evidence required for completion |
| GET `/skills` | — | `{items,cursor?}` |
| POST `/skills/propose` | `{requestId,goal,domain,sourceIds?,toolNames?,model?}` | `202 {skill,job}`; actual gated model-assisted drafting, unavailable is explicit |
| POST `/skills` | `{requestId,name,title,description,instructions,sourceRefs?,toolNames?,examples?}` | `{skill}`; DRAFT, private content hash |
| PUT `/skills/:id` | `{version,...editable fields}` | `{skill}`; edit invalidates verification/approval |
| POST `/skills/:id/validate` | `{version}` | `{skill,validation}` for package-only checks, or `202 {skill,job}` for actual asynchronous behavior evaluation |
| POST `/skills/:id/approve` | `{version,contentHash}` | `{skill}`; current role and all required evidence |
| POST `/skills/:id/deprecate` | `{version,reason}` | `{skill}` |
| GET `/skills/:id/package` | — | `{skill,files,contentHash}`; exact immutable Markdown/references |
| POST `/skills/:id/execute` | `{requestId,version,contentHash,input}` | `202 {artifact,job}`; exact approved content, bounded context-only execution, no arbitrary tool calls |
| GET `/skills/:id/executions/:artifactId` | — | `{artifact,result}`; current approval/source checks before serving saved output |
| GET `/tools` | — | `{items,endpoint,operator}` |
| POST `/tools` | `{requestId,name,description,toolNames}` | `{tool}`; operator, only supported internal operations |
| POST `/mcp` | MCP JSON-RPC | initialize/tools/list/tools/call for bounded knowledge/evidence/impact/approved-skill operations |

For POST `/examples`, use a small versioned fictional product/flow/guide/component/
icon dependency fixture. Mark relations as synthetic fixture evidence. Do not
claim customer-code extraction or a live external source. Repeated request IDs
are idempotent and never reset shared state.

## Knowledge ETL

Sources: `snapshot`, `confluence`, `git`. Real source connections are selected from
server-owned configuration; do not fetch arbitrary request URLs or accept secret
values in source records. Unconfigured connections fail explicitly. A collector
can run in the approved internal network and supply bounded snapshots.

Documents: `{id,title,content,revision,kind?,sourceUrl?,allowedRoles?,entities?,relations?}`.
Record immutable raw/canonical snapshots, content hashes, permission/source
versions, extraction state and actual counts. Publish vector and graph projections
together through a versioned manifest only after both writes/verification succeed.
Permission/tombstone checks remain independent of rollback.

Initial private artifact indexes are actual numeric-vector and typed-graph
projections with explicit backend/embedding labels. Provide a deterministic local
embedding adapter for offline tests and an injectable approved model adapter.
Never label hashing/local projections as Bedrock embeddings, OpenSearch or Neptune.
Bound size, page all results and expose incomplete coverage.

## Change and task objects

Nodes: `{id,label,title,version,role?,sourceRef?,provenance}`.
Edges: `{src,rel,dst,sourceRef?,provenance}`.
Use dependency edge types; ownership is terminal assignment metadata. An impact
item includes target, role, reason, witness path, source revision and confidence
class (`confirmed`, `candidate`, `unknown`), not invented probability scores.

Work items: `{id,changeId,impactHash,targetId,title,role,status,assigneeSub,
evidenceRefs,version}`. Do not replace existing conservative guideline staleness
checks with incomplete fine-grained indexing.

## Skills

Preserve Registry terminology but enforce project-specific governance over actual
content. Every new Skill has version/content hash/source bindings, package
validation, explicit behavior-evaluation status and approval evidence. Executable
helpers are not enabled by Markdown. Names/tool declarations do not grant access.
Resolve exact approved bytes at consumption; reject deprecated/stale/mismatched
versions. Model-assisted drafting/evaluation uses an injected, gated model adapter,
with `not-run` when unavailable; no simulated model success.

Default runtime adapters use the existing workspace model boundary and cost guard.
Model proposal, behavior evaluation and approved execution run in the asynchronous
worker. Behavior execution omits expected answers, compares actual responses with
exact expected text and adds server-owned unsupported-tool/missing-evidence cases.
These bounded checks are not a broad quality certification. New generated
instructions are English; user-facing titles/descriptions and business output
remain Korean. A context-only execution does not run declared tools.

## Parent-owned business endpoints

`workbench.business.route(api,scope,claims,method,parts,body,query)` handles:

- GET `/pension/personas` -> `{personas}` (synthetic fixtures).
- POST `/pension/sessions` `{requestId,personaId,assumptions?}` -> `{session}`.
- GET `/pension/sessions/:id` -> `{session}`.
- POST `/pension/sessions/:id/calculate` `{version,assumptions}` -> `{session}`.
- POST `/pension/sessions/:id/ask` `{version,question,topic,mode?}` -> `{answer,session}`
  or `202 {job,session}`; deterministic fact-grounded baseline is labelled, actual
  model requests are queued and independently validated before publication.
- POST `/pension/sessions/:id/feedback` `{rating,answerId,commentCode?,comment?}` -> `{feedback}`.
  Preset codes are server-owned; free text requires private processing before
  persistence. Missing processing is an explicit failure.
- GET `/reports` -> `{items}`.
- POST `/reports` `{requestId,type,title,changeId?,sessionId?}` -> `{report}`.
- POST `/reports/:id/approve` `{version,contentHash}` -> `{report}`.
- GET `/reports/:id/document` -> `{markdown,contentHash,status}`.

Report types: `change-impact`, `pension-evaluation`, `management`, `underwriting`,
`regulation`; missing sources are unresolved, never fabricated. No production
customer recommendation or private model-training claim.

Model pension responses select metric IDs. The server owns displayed labels,
amounts, signs and KRW units; surrounding model-authored financial claims are
not displayed. Change reports pin impact hashes/generations and transitive
source bindings and include tasks only from that impact version. Those bindings
govern replay, metadata visibility, document reads and approval.

## Frontend

New `web/src/workbench/Workbench.tsx` exports a default component with
`view` and optional `onNavigate(view)` props. It owns project/context selection
for the new pages and reuses `createWorkspaceClient`.

Views: `planning`, `changes`, `deliverables`, `components`, `development`,
`knowledge`, `skills`, `pension`, `reports`, `sources`, `batches`, `tools`, `operations`.
Existing real Studio remains the screen-generation destination. Links include
opaque project/product/change/target context. Navigation is not authorization.

All new business copy is Korean. Render useful loading/empty/error/not-configured
states. Show actual provenance and readiness. Filter multiple artifact types rather
than adding a menu for every scenario. No made-up live counts or static-success UI.
