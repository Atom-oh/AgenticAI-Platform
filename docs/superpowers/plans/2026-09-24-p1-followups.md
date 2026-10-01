# P1/P2 Follow-ups After the Design Slice (Outline)

> This is an outline, not an executable plan. Each item becomes its own plan once C has exited and the owning decisions are made. Every item reuses the C records, ledger operations and gates. None of them adds a second execution or approval path.

| Requirement | Scope after C | Depends on | Notes |
|---|---|---|---|
| C-02 | Role-specific worklists generated from the C-01 impact result, with regeneration candidates flagged | C C5; `workbench` worklist writer (`workbench/CONTRACT.md`) | Uses the IAM-only routing worker for cross-project items (`AGENTCORE_CONTRACT.md:634-637`) |
| C-03 | Customer-facing change notice screens and notice drafts generated through the same generate → verify → approve flow | C C3/C4 | A notice is a design screen in a dedicated procedure. It goes through the same approval manifest |
| C-04 | Internal change notices (what, why, scope, owner) to affected roles | C-02 | Delivery channel is an open decision; there is no external send without an authorized endpoint |
| W-02 | Planning, design and development AI reviewers commenting on changes | C C5 anchors; B2 Runtime operation `design.review` | Comments carry `author: agent:<role>` and have no approval effect (`ontology_store.py:720-728` unchanged) |
| W-03 | Parallel drafts from an unconfirmed PRD, with partial regeneration on PRD change | C C2/C3; E8 flow diff | Draft rounds are explicitly unverified until the PRD is confirmed |
| W-04 | Decisions from discussion recorded as change reasons and versions | C C5 | Writes `uxModel.changeReason` through the proposal route |
| G-05 | Region selection on the rendered screen for partial edits, recorded as rounds | C C4, B2 S5 boxes | The UI selects a `testId` region, and the existing edit route runs |
| O-09 | Hybrid retrieval: semantic search over admitted derivatives plus graph traversal | B0 intake derivatives; model boundary embed (`engine/gate.py` `embed`) | Measure accuracy against the golden set (E18) |
| O-10 | Brand asset separation in derivation and assets | E5 `exclude`; asset `properties.classification` | A brand classification needs a schema amendment |
| O-11 | Operating effort measurement | E16 `Metrics` | Report per product and per asset |
| P-04 | Failure checklist accumulation feeding launch-process redesign | E16 `failure_checklist` | Export only, with no automated process change |
| R-04 | Design-code / FE boundary configuration | Q-04 decision | A convention field in E17 |
| R-05 (P2) | Git delivery beyond the existing registered export | Existing `git_service` | Already available for releases; the only extension needed is convention-layout export |
| G-06 (P2) | Segment recommendations, idea experiments, A/B variants | Out of PoC scope | — |
