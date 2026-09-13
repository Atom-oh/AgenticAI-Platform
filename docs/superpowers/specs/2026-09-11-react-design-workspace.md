# Design record: collaborative React workspace

- Date: 2026-09-11
- Status: Accepted feature design, consolidated into `SPEC.md` §7-1
- Interface authority: `platform/workspace/REACT_CONTRACT.md`
- Scope: Real React generation, collaboration, approval, release and export

## Decisions

Use externally supplied assets through controlled intake; no direct Figma connection
or fetching of missing external content. Actual React code/types/tokens/version/hash
are the component authority. Markdown supplies business guidance. The platform's
`@studio/approved-ui` baseline is not an unsupplied customer's approved package.

AI composes screens/state in permitted source; trusted templates own settings, locks,
build scripts and component code. Reject arbitrary CSS/HTML, unapproved imports,
dynamic execution and DOM mutations that bypass the contract. Component changes
require their own versioned implementation and verification.

`creative` makes one candidate; `guided` makes one baseline plus 2–5 variants. All use
the same mandatory rules and pinned dependencies with independent histories. Separate
intentional visual differences from image-equivalence evidence. Missing/failed checks
cannot become approvals.

## Verification and collaboration

Run TypeScript AST/policy checks, actual component typechecking and production build,
then render the same bundle in a restricted browser with no data credentials or external
downloads. Test inputs, transitions, consent, values, guidance, style and accessibility.
An optional initial-image comparison does not cover all states. Repair source against
the same criteria and preserve source/dist/tests/manifests/evidence.

Separate personal and project storage; verify current membership and owner/planner/
designer/developer permissions on every operation. Publish immutable guideline source,
revision and typed Product/Condition/PolicyRule/Procedure/ScreenMeta nodes/edges.
Persist and reread the project ontology. Link discussions and impacts to product,
guideline, page and round; changed guidelines prevent reuse of stale approval.

## Approval and delivery

Bind human approval to exact source/component/guideline/rule/bundle/evidence revisions.
Rebuild the approved source without AI and verify matching artifacts plus the approved
start-screen comparison. Store source/dist/site/manifests privately. Do not publish to
public buckets just because a release is ready.

Git export uses registered target/base/path/secret references, feature branches,
verified source and idempotent commits. Refuse main writes, force updates and conflicts.
Preserve actual commit evidence if a later read fails. Without a configured target,
provide source downloads and an unconfigured state rather than a fabricated commit URL.

## Acceptance evidence

Actual React build/browser checks, policy-bypass rejection, generation-mode boundaries,
partial-failure repair, multi-user authorization, persisted guideline impacts,
stale-approval rejection, same-source releases and real Git conflict/idempotency tests.
Live integration and publication evidence remain separate from local tests. See the
same-date release record and `SPEC.md` §16 for historical results and exclusions.
