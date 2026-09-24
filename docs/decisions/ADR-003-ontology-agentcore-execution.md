# ADR-003: Integrate ontology work through a dedicated AgentCore execution path

- Date: 2026-09-23
- Status: Accepted for implementation sequencing; service activation remains gated
- Scope: SPEC §7-2 and its integration with existing platform modules
- Authority: User-requested implementation and design consolidation before coding

## Context

The canonical ontology foundation and the AgentCore execution requirements are
defined in separate module contracts. Kiro review of the acceptance document
closed eight Major issues and subsequent documentation clarifications. That
review did not implement or verify the operating services.

The platform needs one design entry point that makes ownership, permissions,
existing behavior, remaining implementation and admission gates visible before
runtime code changes.

## Decision

Use `platform/docs/ARCHITECTURE.md` as the cross-module implementation design.
Keep requirements in SPEC and schemas/roles in their owning contracts.
Use `platform/docs/ONTOLOGY_AGENTCORE_VALIDATION.md` for case evidence and
completion decisions; the dated plan preserves sequencing and review history.

The workspace project ontology is canonical in explicitly enabled canonical
mode. Workbench projects it; the bank reference graph remains separate.
This extends ADR-002 only for that canonical projection. Legacy workbench
behavior and the other ADR-002 business/knowledge requirements remain.

Use a separate ontology Runtime/Gateway/Lambda/Identity/Memory/Interpreter/
Browser path. Preserve the existing bank Gateway configuration and private
MyData boundary. Reuse project/source authorization and exact React approval,
release and Git export records.

Implement the offline execution protocol and the separately owned private
admission/publication adapters, then owned service probes, proven adapters/IaC
and the application cutover. Runtime telemetry must exclude source/prompt/token
content under observed canary checks. Keep the independently reviewed
A/B/C stages and legacy defaults. Actual service evidence is required before
activation and production admission. A verified enabled-model set may be
admitted with explicit unavailable-model gaps; it is not full requested-model
completion.

Each B sub-unit receives independent review. Durable execution authority and
its key registry are separate from disposable probe resources. Legacy jobs keep
their existing writer and drain there. From C, newly admitted AgentCore
executions use only the new ledger; two writers cannot own the same job or
its execution-managed completion artifacts.

## Consequences

Root agent guidance and platform indexes must lead implementation work to this
design, the relevant contracts and acceptance cases. Interface or authority
changes update the owning contract together with code.

Review/CI completion, offline tests, live capability probes, staged acceptance
and customer deployment remain distinct decisions. No document change creates
service readiness, widens source access or authorizes implicit local fallback.
