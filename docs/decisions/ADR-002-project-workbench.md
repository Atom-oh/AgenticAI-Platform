# ADR-002: Add a project workbench with explicit evidence boundaries

- Date: 2026-09-13
- Status: Accepted for the initial workbench implementation
- Scope: `/studio-api/workbench`, its UI and explanation artifacts
- Authority: User-requested implementation and SPEC §17

## Context

Planning, UX assets, React delivery, knowledge, reusable Skills and internal
reports need shared project context and traceable evidence. No customer Confluence
target is available; the user requested connection preparation documentation only.
Existing legacy scenarios and React release contracts must remain distinguishable.

## Options

1. Extend the existing private Workspace API, worker, storage and project fences.
   This reuses established access/approval paths and supports bounded local tests.
2. Provision a separate platform and require managed search/graph services and
   customer connectors before validating any workflow. This adds operational and
   identity integration that cannot be verified against the supplied inputs.

## Decision

Use the existing workspace boundary. Start with real numeric feature-hash vectors
and typed graph artifacts in private storage, with explicit backend identity,
source permissions, versioned joint publication and incomplete-coverage reporting.
This decision does not replace the platform's other graph/vector backends.

Store actual Skill content and resolve exact approved versions. Execute model
proposal, behavior evaluation and bounded Skill use asynchronously through the
existing model boundary. Keep synthetic pension calculations server-owned,
including displayed financial labels and units. Pin direct and transitive report
evidence. Separate operator capability from project roles.

The current interface is defined in
[the implementation contract](https://github.com/Atom-oh/AgenticAI-Platform/blob/main/platform/workbench/CONTRACT.md).
ADR-001's language, authority and latest-HEAD review rules remain in effect.

## Consequences

The implemented workflows can be verified with synthetic project data. Feature
hashing is not semantic embedding, context-only Skill execution is not arbitrary
tool execution, and an internal MCP declaration is not Gateway provisioning.
Confluence collection requires a future reviewed target and refresh policy;
there is no automatic scheduler or live customer connectivity claim.

Preserve those distinctions in code, UI, docs, reviews and the Korean video.
Revisit storage adapters and collector operations when real scope, volume and
permission requirements are supplied.
