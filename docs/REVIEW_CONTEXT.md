# Review context

Baseline: merged MyData implementation through `acad38c` (PR #4), 2026-09-13.
This file maps requirements to evidence; it does not waive defects or certify deployment.
That baseline dates the original discrepancy audit. Later ontology/workbench
amendments below and the architecture's 2026-09-23 baseline are separately scoped.

## Read the applicable contract

| Document | Meaning |
|---|---|
| `AGENTS.md` | Shared agent workflow and review rules |
| `SPEC.md` | Active bank-platform requirements, including explicit scoped amendments |
| `platform/docs/ARCHITECTURE.md` | Cross-module implementation design, authority boundaries and staged ownership under SPEC §7-2 |
| `platform/docs/ONTOLOGY_AGENTCORE_VALIDATION.md` | Acceptance cases and evidence rules; documentation review is separate from implementation results |
| `platform/workspace/ONTOLOGY_CONTRACT.md` | Implemented canonical data/API boundaries and explicitly gated source-analysis migration |
| `platform/docs/ONTOLOGY_AGENTCORE_PLAN.md` | Dated ontology/AgentCore sequence and historical review outcomes; does not override active contracts |
| `platform/README.md` | Implementation map, commands, and dated operational evidence |
| `platform/docs/CONTRACTS.md` | API/module integration |
| `platform/workspace/REACT_CONTRACT.md` | React generation, approval, release, and export |
| `platform/workspace/AGENTCORE_CONTRACT.md` | SPEC §7-2 execution, authorization, capability and rollout requirements; not a deployment claim |
| `platform/workspace/CONTRACT.md` | Imported-file/HTML workspace validation |
| `platform/workbench/CONTRACT.md`, `platform/workbench/README.md` | Project-scoped knowledge/impact/skills and synthetic pension/report workflow; SPEC §17 |
| `platform/infra/README-privacy.md` | MyData relay/gateway configuration and operational prerequisites |
| `docs/14-demo/mydata-privacy.md` | Korean MyData user instructions and verification limits |
| `demo/*/README.md`, `demo/SECURITY-GOVERNANCE.md` | Separate demo implementations and governance |
| `docs/decisions/` | Explicit repository decisions, with status and scope |
| Dated `plans/`, `specs/`, `.superpowers/` records | Historical feature context; follow their current-contract pointers |
| `docs/00-intro/` through `docs/13-appendix/` | Educational reference, not a deployment inventory or blanket acceptance criteria |

Use source and tests to verify behavior. When code violates an applicable requirement,
report the violation. When documents disagree, identify their scopes and dates and
report the documentation conflict instead of choosing whichever statement is strictest.

## Resolved sources of false positives

| Earlier statement | Current interpretation and evidence |
|---|---|
| Gemma is the MyData privacy processor | S2 calls `common.privacy.process` before INPUT Guardrails. The EKS gateway detects entities; code validates and replaces them. Claude/Gemma select the separate explanation route. See `platform/api/handlers/s2.py`, `platform/privacy/gateway/engine.py`. |
| Every structured payload needs a second sLLM pass | The trusted producer tokenizes typed fields; independent rules and Guardrails inspect the prepared payload. `privacy_payload` reports `modelInvoked: false`. Reclassification must not corrupt amounts or rates. |
| Anonymization is unimplemented, or is legally complete | Rule-based tokenization and EKS free-text removal are implemented. Reversible trusted-plane identity restoration is distinct from irreversible free-text removal. No legal/full-anonymization claim follows. |
| Every request must stream unverified tokens within five seconds or use a cache | Those were demo UX goals. S2 buffers explanation output for verification and disables shared-cache writes/replay. Privacy failure blocks; it must not become a cached success. |
| Everything is already inside one deployed VPC | The target is a private data boundary. `BankPlatform`, `BankPlatformPlane`, and optional `BankPlatformPrivacy` are distinct IaC stacks. The privacy stack reuses an existing EKS VPC; code merge does not prove relay/network deployment. |
| Every occurrence of `onprem`, `idc_vllm` or `Two-Plane` is forbidden | The presentation rule rejects misleading user-facing topology/residency claims. Existing module names, event values, and historical records are compatibility identifiers, not claims of physical deployment. |
| GitLab is mandatory for this repository's CI | GitLab is the proposed customer's source/CI system. This repository uses GitHub Actions. The React export implementation supports configured GitHub/GitLab targets; customer credentials and targets are not implied. |
| All generated screens use the same gate | Legacy Registry/stub checks and imported HTML checks are scoped separately from real React compilation/browser verification and release approval. |
| Every ontology is persisted in Neptune | The bank impact graph has local/Neptune backends. Published workspace product guidelines persist project-scoped JSON nodes/edges; this is not Neptune integration. |
| Optional NER or SageMaker tuning is running | Private NER is reported as unconfigured unless actually connected. Training preparation/export does not mean a job ran or an artifact was promoted. |
| Earlier source-region/profile claims prove current AWS availability | Model IDs and adapters describe repository configuration. Query the target account and current official documentation before asserting availability or routing behavior. |

## Requirements that remain gaps

Shared Registry reset/seeding must remain IAM-admin-only. Explicit authenticated
`reset` and `registry_seed` routes have been removed in the workbench change. Legacy
list/bootstrap behavior still needs separate review; route removal does not certify
all legacy Registry writes. Likewise,
component guidance is required to map to `SKILL`, while current Registry records use
`CUSTOM/COMPONENT` and the portal marks the deviation. These are not exemptions.

Engine handlers and the Strands scenario runtime have different boundary-check entry
points (`engine/gate.py` and `agents/boundary_gate.py`). Check their actual enforcement
against the same requirement; module identity alone proves neither compliance nor bypass.

The PR #4 HTTP gateway additionally checks actual TCP peers against verified NLB
addresses before accepting model-bound input. `/health` remains a limited probe;
this is not permission for anonymous model requests or proof of strict CNI isolation.
Shared-cluster maintenance authorization is recorded in the scoped runbook; rollout
and model cold-start verification remain separate from merging prerequisite code.

S2 currently sends answer text after OUTPUT Guardrails and before private numeric/
Semantic finalization. Report this ordering accurately; the later check does not
prove that every incorrect number was suppressed before user-visible delivery.

## Review evidence and severity

For each finding, identify the changed line, reachable caller or affected document,
applicable contract, and concrete consequence. Distinguish a new regression from an
existing gap, historical requirement, proposed feature, or dated deployment claim.
An existing security defect remains reportable; scope is not a blanket dismissal.

Do not require every guidebook pattern in every demo. Do not infer a bypass merely
because privacy preprocessing has its own private endpoint: trace what enters
Bedrock and where verification occurs. Conversely, a document calling a component
"private" does not establish network isolation; inspect IaC and deployment evidence.

Record reviewer identity, base and HEAD SHA, changed-file coverage, and unresolved
findings. Revalidate after each push. A missing Kiro/bot response or failed review
is an incomplete review, even when CI is green.

## Publication and language

English development documents in `docs/` are intentionally public build inputs;
there is no confidentiality or exclusion rule implied by their audience. Korean
technical chapters, demo instructions, homepage and navigation keep their language.
See ADR-001 for this distinction. A reviewer must not infer a missing exclusion
requirement solely from the directory or language.

## Automation in this repository

`.github/workflows/platform-ci.yml` runs Python, web, React kit, gates, and infra
checks for its configured path filters. `.github/workflows/deploy-docs.yml` builds
and publishes Pages on pushes to `main`. There is no checked-in `pr-review.yml`
or repository-local Kiro panel configuration at the baseline above.

An externally invoked PR reviewer must receive `AGENTS.md`, this file, and the relevant
contracts at the reviewed HEAD, either by reading them or as explicit prompt context. Do not claim a local document change installs,
repairs, or successfully runs an external review service. Verify that service's
actual result separately.

The installed external panel inspected during this reconciliation invokes Kiro in an
isolated directory with no trusted tools and an embedded diff. That mode cannot follow
repository file pointers. Include the applicable context text in the caller-owned lens
prompt; a steering file alone is insufficient. See [review invocation](pr-review.md).

## Workbench scope amendment

The ontology extension chooses workspace project `ontology` records as the
canonical authority when `PROJECT_ONTOLOGY_MODE=canonical` is explicitly enabled.
Existing per-product guideline projections retain their IDs/hashes; workbench
typed-graph/impact consumers become a projection with explicit legacy import.
The bank local/Neptune graph remains a separate reference source. This is not
three interchangeable stores or a claim that all sources have migrated.
Default execution and graph mode remain legacy until the cutover gates.
Source-analysis/offline tests and configured readiness are not live AgentCore
Runtime/Interpreter/Browser/Memory/Identity/Gateway evidence. The new planned
Gateway/Lambda must not inherit the bank target's private-plane capabilities.

The 2026-09-23 user instruction adopts the cross-module design in
`platform/docs/ARCHITECTURE.md` before actual implementation. ADR-003 records
that scoped decision. The completed Kiro acceptance-document reviews corrected
verification criteria; they do not prove implementation of the dedicated
ledger, execution authority, Runtime or other service adapters.
Review new implementation against its named contract/case IDs and current code.
Offline protocol work and disposable capability probes may precede activation;
the existing A/B/C reviews, source boundaries and service gates remain mandatory.

SPEC §17 applies only to the new `/studio-api/workbench` module. Its private
feature-hash vector and typed-graph artifacts are an explicit initial backend,
not a claim that OpenSearch/Neptune are deployed for this module. MCP registration
publishes internal tool declarations, not an automatically provisioned Gateway.
The synthetic pension workflow is separate from legacy S2; trace its own
privacy, numeric substitution and output validation path before reusing an S2
finding. The Skill Creator governs exact private content; it does not retroactively
change every legacy Registry component record into a Skill.
