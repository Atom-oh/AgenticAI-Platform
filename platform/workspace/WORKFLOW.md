# UX workflow

## Change requests (2026-09-21)

The five stages support a versioned `changeRequest` inside the existing scoped
rule contract. Type, channel, requester, date, baseline notes, preserved behavior,
page/overlay/slot inventory, distinct ID namespaces and guarded transitions
survive proposal, approval and handoff. Mapping notes remain reviewer claims.

A request draft can have no rules and be saved before proposing tests.
Project drafts may be saved before a product is selected; shared proposal,
approval and generation still require current published product guidance.
Approval requires actual required assertions. Each declared screen/state needs
a `screenId`/`scenario` rule observing that screen. Each transition needs a
`transitionId` rule observing source, action and destination in order.
Conditions and retained values need semantic review and additional meaningful
assertions; these structural checks do not establish their correctness.

An optional baseline pins an approved React run, round and source hash in the
same storage scope. Only explicitly allowed generated-source paths may change;
other generated files remain byte-identical. This is file-level enforcement,
not preservation of every region inside an edited file. Imported customer
source cannot become an executable baseline through reference intake.

The release manifest adds the request, ID fields, references and actual
added/modified/deleted exported files with before/after hashes. Trusted build/
test metadata can also differ; the strict allowlist governs generated sources.
Customer integration and frontend acceptance remain explicitly unverified.
The existing pinned compiler and isolated browser remain authoritative.
The knowledge repository's mock adapter and coverage scores are not imported
as production generation or approval gates.

The Studio uses a five-stage workflow, from an explicit user goal to an approved
React handoff. The asset portal opens the same project-scoped criteria stage.
This adapts the supplied guidelines' separation of definition, structure, states
and application to the platform's design work. It does not claim that a banking
mobile-app guide prescribes this desktop platform's complete operating model.

## Work stages

| Stage | User task | Persisted result / boundary |
| --- | --- | --- |
| 업무 정의 | Select/publish product guidance, describe the user goal and completion condition, choose relevant states | Product publication is saved independently. UX intent is saved with its rule contract in stage 3. |
| 기준·자산 | Select exact guideline pages, supply visual/graphic references, inspect executable component capabilities | Private immutable imports and selected references; a guide is not an implementation package |
| 흐름·상태 설계 | Propose/edit rules, connect each required state to required assertions, resolve unknowns, approve the criteria | Versioned contract with source references and state requirements |
| 시안·검수 | Generate/compare React, inspect matching reports, repair against the same criteria, approve an exact round | Actual source/bundle/build/browser evidence and revision-bound UX approval |
| 개발 전달 | Select the exact React round, rebuild it, download source/report/bundle or export to a registered feature branch | Existing release/export gates; no implied API integration, PR/MR, merge or customer deployment |

Legacy static galleries and generators remain secondary tools. They do not
authorize React releases. The collaboration rail keeps the current project,
role, product revision, selected round, discussion and member management visible.
Planning and source delivery are primary stages rather than hidden sidebar tabs.

## State requirements

Contracts and proposal requests optionally carry `requiredStates`, a unique list
of `entry|input|consent|error|empty|loading|back|cancel|complete`. Rules optionally
carry a `scenario` from the same vocabulary.

The designer selects applicable states; no template automatically becomes a
customer-approved policy. Starter flows set an editable brief and state scope.
They never invent source quotations or attach a customer approval.

Saving an incomplete draft is allowed. Approval is rejected when a selected
state has no required rule with an assertion. A model cannot delete requested
states from the resulting proposal. Both fields participate in the contract
hash; edits clear approval. Empty/absent state scope preserves legacy hashes.
Generated reports cannot pass while required state coverage is incomplete.

State names establish declared coverage, not semantic correctness. The designer
still reviews the actions, expected results, source conditions and exceptions.
The results view uses matching round evidence for each required state's
connected rule results. Missing, ambiguous or unmatched evidence is indeterminate.
An unselected state is not a tested state.

## Navigation and state

`#/studio` accepts `projectId`, `productId`, `contractId`, `runId`, `round` and
`step=define|assets|design|review|handoff`. Existing `project` is accepted as a
project alias. The asset portal retains project/product context when browsing
reference components and opening project criteria.

These parameters are navigation intent, not authority. The workspace resolves
current project membership through the existing authenticated API. Invalid
identifiers cannot silently load another workspace. An unknown requested round
does not substitute a different approved round.

Stage navigation preserves the mounted definition/rule editor, product draft
and review state. Explicit product/project changes clear artifact selection;
unsaved rule changes require confirmation. Browser navigation inside the same
project restores the stage without serializing private content to browser
storage. Hard reloads retain only saved records; the definition screen says
when its unsaved intent will be saved.

Developer roles start at handoff unless an explicit link selects another stage.
Other roles start at definition. Stages remain inspectable; server authorization,
criteria freshness and exact approval still govern every mutation.

## Handoff

The handoff stage reuses `ReleasePanel` and the existing release/export APIs.
Showing a source round, seeing an approval record, rebuilding a ready release,
and committing a feature branch remain separate results. A finished step in the
navigation does not manufacture any of these states.

The source ZIP contains the frozen contract, including the selected states and
source references. The developer follows the included build/test instructions
and performs customer component/API/auth/routing integration and team review.
Changed code is not covered by the earlier Studio approval.

Customer tokens, executable components, graphics and source-image baselines
remain separate inputs. The private textual guideline pack does not supply
these assets or establish complete visual compliance.

## Validation

Run the existing workspace Python and web suites, plus
`tests/test_workspace_workflow.py` and `web/test/workspace-workflow.test.cjs`.
The guideline browser test covers definition, page selection, required-state
gaps, approval and preserved inputs. Project tests cover handoff, revoked access
and exact-round deep links. Captures use synthetic data only.
