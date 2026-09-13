# Confluence connection preparation

Status: **documentation only; no real integration is requested or configured**.
The user confirmed on 2026-09-13 that no target installation is available.
Keep `workbenchConnections` empty and validate with project-local synthetic snapshots.

Confluence is a batch knowledge source. Agents use published internal retrieval
and evidence tools; they do not fetch arbitrary wiki URLs during conversations.

## Inputs for a future connection

| Input | Purpose |
|---|---|
| Installed edition/version and HTTPS base URL | Verify REST collector compatibility |
| Selected Space and document scope | Avoid unrelated collection |
| Service identity and exact Secrets Manager ARN | Resolve credentials without storing their values in source/configuration |
| Network route, DNS and TLS trust | Reach the authorized collection environment |
| Project IDs and permitted roles | Bind an explicit application audience |
| Effective Space/page/ancestor permissions | Prevent service-account access from becoming user access |
| Update/deletion and ACL refresh policy | Define freshness, revocation, retry and ownership |
| Volume and attachment policy | Size batches or use an internal snapshot exporter |

Operator capability uses verified `platform-operators` or `admin` groups.
Project ownership does not grant it. Follow the organization's identity process
when assigning group membership.

## Placeholder context

This example is **not a deployable customer configuration**:

```json
{
  "workbenchConnections": {
    "internal-wiki": {
      "kind": "confluence",
      "label": "Internal knowledge",
      "baseUrl": "https://wiki.example.invalid/confluence",
      "spaceKey": "EXAMPLE",
      "approved": true,
      "allowedProjectIds": ["p-example"],
      "allowedRoles": ["owner", "planner", "designer", "developer"],
      "secretRef": "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:example/wiki-token-EXAMPLE",
      "maxPages": 4
    }
  }
}
```

CDK grants the worker reads of exact referenced secrets. The API receives profile
metadata, without secret-read permission. No token value belongs in this JSON.
Set approval for a real profile only after source ownership, audience and network
access have been reviewed; this example grants none of those approvals.

## Collection and authorization

The adapter fetches current pages through `/rest/api/content`, including body,
version and ancestor metadata. It uses bounded offsets, constructs paths locally
and refuses redirects; upstream continuation URLs are not trusted.

The default policy checks page and ancestor read restrictions. Restricted or
ambiguous pages are denied. A future trusted effective-ACL resolver may translate
enterprise identity into explicit application roles. Page content cannot supply
that authorization. Include Space access in the reviewed profile audience.

Canonical documents feed numeric feature-hash vectors and typed graph artifacts.
Both must be verified before joint publication. This baseline does not imply
OpenSearch, Neptune or semantic embedding integration.

External ACL observations expire after five minutes in the current implementation.
This fail-closed limit does **not** mean a scheduler is deployed. A future target
needs a refresh cadence or approved internal batch exporter matching the agreed
policy. Stale sources become unreadable.

## Future operator rehearsal

1. Provision the reviewed profile, credential and network route.
2. Use synthetic pages first; verify TLS and effective permissions.
3. Register the configured connection ID in Knowledge Sources and start a batch.
4. Inspect processed, denied, failed and truncated counts, both projections and
   the published generation.
5. Verify each application role sees only its permitted content and relations.
6. Revoke page access, add an ancestor restriction and delete a test page.
   Search, impact, Skill and report reads must stop exposing those records.
7. Test partial collection, credential expiry and ACL observation expiry.
8. Register internal read tools after verification. AgentCore Gateway provisioning,
   if needed, remains a separate step.

## Limits and recovery

- No attachment/OCR collection is claimed by this adapter; workspace file intake
  handles its own supported formats.
- An incomplete scan is not proof that missing pages were deleted.
- Rollback cannot restore access: current permissions and tombstones remain
  independent of index generations.
- Preserve failed-batch evidence and correct the cause before retrying. Do not
  bypass permission checks or convert failures into successful empty indexes.

Sources: [collector](collectors.py), [index lifecycle](knowledge.py),
[API contract](CONTRACT.md), [infrastructure](../infra/lib/workspace.ts).
