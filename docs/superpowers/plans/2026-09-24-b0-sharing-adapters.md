# B0 Sharing — Source Authority Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the reserved source-kind authority adapters that the design slice needs:
- **`run-round`**: generated React rounds as ontology and discussion sources
- **`ux-contract`**: approved UX contracts as sources

Also install the shared-publication authority, including the **`published-asset`** kind, so that C can exit at the architecture's integrated level. Until an adapter is installed, its kind keeps failing closed (`workspace/ontology_sources.py:173-175`).

**Architecture:** Adapters are added to `Sources.resolve`/`authorize`, next to the existing `document-revision` and `product-guideline` handlers. Each adapter resolves the exact record in the current project, rechecks current membership and audience, and compares revision and hash. Stale, withdrawn and superseded records return explicit reasons.

Shared publication follows `AGENTCORE_CONTRACT.md` "Shared-publication authority" (L87-128):
- the source owner proposes
- a designated publisher, holding the IAM-administered `design_publish`/`policy_publish` capability, approves
- the destination owner accepts a grant
- effective access is the intersection of membership, grant and upstream audience
- withdrawal is prospective

**Tech Stack:** Python 3.12, pytest, the existing `Sources`, `Collaboration`, `Storage` and `intake/admin_handler.py` IAM entry, which is extended for capability grants.

**Roadmap:** unit 3. It supports Codex #17 and #21 in C.

**Owning contracts:**
- `workspace/AGENTCORE_CONTRACT.md`: source references (L30-37), "Shared-publication authority" (L87-128) and `publishing-handoff/1` (L404-457)
- `workspace/ONTOLOGY_CONTRACT.md:140-142` (reserved kinds)
- `workspace/REACT_CONTRACT.md`

**Acceptance cases:** these run in O-mode plus API checks; L/U evidence stays with C.
- AUTH-07/08
- ONT-04/09 (source kinds, pagination)
- IMP-05
- HAND-02–05 (approval, copy, release, export roles): the source-binding parts only. The design-manifest parts are in C.

## Global Constraints

- The roadmap constraints apply.
- An adapter never substitutes an id lookup for authority. An inaccessible record returns the same error as a missing one, so no existence is disclosed.
- Rounds are immutable. A round source is current only while its run is not archived and its project membership still grants read.
- Publisher capabilities are written only through the IAM-only entry point. Project owners cannot self-grant them.

## File Structure

| Path | Responsibility |
|---|---|
| `platform/workspace/ontology_sources.py` (modify) | `run-round`, `ux-contract`, `published-asset` resolution in `resolve`/`authorize` |
| `platform/workspace/publications.py` (create) | Publication proposal, approval, grant, withdrawal and effective-access checks |
| `platform/intake/admin_handler.py` (modify) | `grant_capability` / `revoke_capability` ops (`design_publish`, `policy_publish`) |
| `platform/workspace/storage.py` (modify) | Kinds `publication`, `pub_grant`, `capability` |
| `platform/workspace/http.py` (modify) | `/publications` routes (propose, approve, accept, withdraw, list) |
| `platform/tests/test_sources_adapters.py`, `platform/tests/test_publications.py` | Tests |

---

### Task H1: `run-round` adapter

**Interfaces:**

The reference shape is `{sourceKind: "run-round", sourceId: <runId>, revision: "<round number>", sha256: <round sourceHash>, audienceRevision: "current-project-members-v1", location?: {round, path?}}`.

Resolution rules:
- The run exists in `ctx.owner` and `run.projectId == ctx.project_id`, and the run is not archived.
- The round with `number == int(revision)` exists, and `round.sourceHash == sha256`.
- For React rounds, the round's `outputType == "react"`.
- The actor has project `read`.
- **State-dependent content permission** (PR #26 review, `AGENTCORE_CONTRACT.md:440-447`). Before any source text or `release_source` bytes are returned, the round's state is checked: a draft/failed/needs-changes round is readable only by designer/developer/owner (`edit_design`-class roles); a reviewable, approved or released round by any current project reader. A planner resolving a draft/failed round → the same `404 not-found`. Metadata-only `resolve` (no text) follows the same rule. Tests: planner denied for draft and failed rounds, allowed for a reviewable round; designer allowed for a failed round.
- **Upstream lineage (review round 2, N10).** A round's source is a derivative of its inputs, so these must still be current:
  - the run's `contract` sources: the approved contract through the `ux-contract` adapter
  - the round's `designManifestInput.admissions`: each admission decision through `intake.admission.verify(host, scope, decisionId)`
  - the guideline/product publication in the run's criteria: through `resolve_generation_context`, as `process_release` already does

  Any revoked or superseded upstream → `409 source-upstream-revoked`, and the round cannot be used as current evidence.
- Historical reads (PR #26 review): the `run-round` historical `authorize` also rechecks upstream **permission**. A superseded but still accessible upstream admits historical diagnostics; a revoked upstream (contract audience, admission `grant-revoked`/`decision-not-current`, publication withdrawal) denies the historical read with the same not-found error, so `ontology_store` historical paths cannot expose round-derived content after upstream revocation (`ONTOLOGY_CONTRACT.md:102-105`, `AGENTCORE_CONTRACT.md:348-355`). Test: revoke an upstream admission → historical read of a round-derived node is denied; supersede it while still readable → historical diagnostics remain.
- Current resolution always uses `Sources.resolve`. `authorize` admits historical metadata and is not used for reuse authority.

Failure codes:
- `404 not-found` covers both missing and inaccessible records.
- `409 source-changed` when the hash differs.

With `text=True`, the adapter returns the round's source file text through the existing blob read and hash check. `release_source` has the same constraints.

- [ ] **Step 1: Tests** (`tests/test_sources_adapters.py`, reusing the `wb` fixture from `tests/test_workbench_core.py:21` and the React run helpers from `tests/test_workspace_react_generation.py`):
  - a valid ref resolves
  - a wrong hash → `source-changed`
  - a round number that does not exist → 404
  - another project's run → 404, with no distinction from missing
  - after member removal → 403 `ontology-authority-changed`, from the existing `_fresh`
- [ ] **Step 2–4:** Fail → implement in `Sources.resolve` and `authorize` → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(ontology): install run-round source authority adapter"`

### Task H2: `ux-contract` adapter

**Interfaces:**

The reference shape is `{sourceKind: "ux-contract", sourceId: <contractId>, revision: str(contract.version), sha256: contract.approval.hash, audienceRevision: "current-project-members-v1"}`. This matches the stored approval fields: `status == "approved"` and `approval: {version, hash, actor, at}` (`workspace/http.py:807-813`).

Resolution rules:
- The contract's `status` is `approved`, `contract.version == int(revision)` and `contract.approval.hash == sha256`. The hash is also recomputed with `rules.contract_hash(normalized)` and must match.
- The contract's product and guideline criteria are still the current published ones, compared with the existing run-currency check in `collaboration.py` just before `_anchor`, at ~L590-609.

A superseded contract is readable historically with `historical: true` and cannot be used as current evidence (`409 source-superseded`).

- [ ] **Step 1: Tests:**
  - an approved contract resolves
  - a draft contract → 404
  - a contract whose product publication changed → `source-superseded`
  - a historical read is allowed with `historical=True`
- [ ] **Step 2–4:** Fail → implement → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(ontology): install ux-contract source authority adapter"`

### Task H3: Shared-publication authority and `published-asset`

**Interfaces:**
- `publications.propose(ctx, *, kind: "design"|"policy", node_ids, revision_bindings)`: owner or source owner only. It creates `publication {id, originProject, kind, nodes: [{id, revision, contentHash}], status: "proposed"}`.
- `approve(ctx, pub_id)`: the actor must hold a current `capability {actor, name: "design_publish"|"policy_publish", status: active}` **and** origin source authority → `status: "published"`.
- `grant(ctx_destination, pub_id, *, roles)`: destination owner only → `pub_grant {publicationId, publicationRevision, destinationProject, roles, status: active}`.
- `withdraw(ctx, pub_id)`: publisher or origin owner → `withdrawn`. Every grant becomes ineffective immediately.
- `published-asset` refs: `{sourceKind: "published-asset", sourceId: pubId, revision: str(pubRevision), sha256: publication hash, audienceRevision: str(grantRevision)}`. Resolution requires all of the following:
  - the publication is `published`
  - the grant is active for this destination and role
  - the upstream origin source ACL still allows it; restricted upstream beats grant
  - revisions match
- Withdrawal effects: listed in the `publication` record as `recallNeeded` metadata when derivatives exist. Fencing and quarantine of in-flight attempts is by the B0 ledger: C checks admission refs at each stage.
- Impact across projects returns only authorized dependents, with coverage `restricted-or-unmapped` (AUTH-07).

- [ ] **Step 1: Tests:**
  - A project owner without capability cannot approve.
  - A capability holder without origin source authority cannot approve.
  - A wrong-role destination read is denied.
  - Withdrawal immediately denies new resolution.
  - An upstream restriction overrides a grant.
  - A new publication revision requires a new grant.
  - The cross-project impact response hides the node IDs and exact counts of the unauthorized project.
- [ ] **Step 2–4:** Fail → implement `publications.py`, the storage kinds, the admin capability ops and the routes → pass.
- [ ] **Step 5: Commit** `git commit -m "feat(workspace): shared-publication authority and published-asset adapter"`

### Task H4: Contracts

- [ ] In `AGENTCORE_CONTRACT.md` L30-37 and `ONTOLOGY_CONTRACT.md:140-142`, move the three kinds from "reserved" to "installed". Record each adapter's resolution rules, error codes and `historical` behavior.
- [ ] In `docs/CONTRACTS.md`, add the `/publications` routes and the storage kinds.
- [ ] Run `python3 -m pytest tests/ -q` and `git diff --check`.
- [ ] Commit: `git commit -m "docs(ontology): record installed source authority adapters"`

## Verification before PR

```bash
cd platform && python3 -m pytest tests/test_sources_adapters.py tests/test_publications.py tests/ -q && git diff --check
```
