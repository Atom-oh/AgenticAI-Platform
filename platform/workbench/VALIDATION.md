# Workbench validation and draft handoff

Date: 2026-09-13. Scope: the ontology workbench change based on `dbb0af1`.
These are local results, not deployment, customer-service availability or
security-certification claims.

## Integration update — 2026-09-14

The user requested review, merge and production deployment of PR #13. This
supersedes the earlier draft-only handoff instruction; review and CI requirements
still apply. GitHub had no submitted reviews, issue comments or inline comments
when checked. The incomplete core AI review has not been treated as approval.

Merged `main` at `75b3ba9` into the feature branch, preserving the internal
document library, its source-bound S1 workflow and unchanged-fence transaction
retries. Both document and workbench storage kinds, worker tasks and deployment
packages remain present. The role navigation exposes the document library and
S1 while retaining the selected project; the navigation regression was
reproduced before the fix.

The integrated tree passed 1,507 Python tests plus 119 subtests, 156 web tests,
web/documentation builds, CDK synthesis, the workspace template checker and
the eight privacy-delta cases plus full privacy integration check. These are
local results; the new commit's CI and review are separate gates.

Production was inspected read-only: the existing stack was `UPDATE_COMPLETE`.
Any eventual deployment must preserve its configured private MyData relay.
No production mutation or Confluence connection was performed.

## Recorded finding remediation — 2026-09-14

A partial local core-review report was recovered. It contains three reproduced
Major findings, but its coverage tables are unfinished and its review attempt
ended with the previously recorded safety error. Recovering its findings does
not turn that attempt into a completed review or a latest-HEAD approval.

The reported cases were retained as ordinary repository regression tests and
fixed:

- Snapshot replacement requires the current project owner or a source creator
  who retains access. Evidence now binds its validated historical audience;
  new public revisions do not disclose older restricted derivatives. Skill
  deprecation also checks read authorization before returning its record.
- Impact reads recheck membership, source access, change identity and the active
  manifest after blob I/O, rejecting a changed result before returning it.
- Skill numeric extraction handles adjacent Korean units and normalized Unicode
  digits, preserves signs, and excludes validated citation identifiers.

The original six recorded cases reproduced five failures and one passing
control before remediation. Eleven focused regressions now pass, including
authorized source refresh and preservation of historical readership. All 89
Workbench tests and the full 1,518-test Python suite plus 119 subtests passed.
No blocked review was retried, reassigned or substituted with these tests.

Compatibility: bindings without the newly pinned audience fail closed.
Reindex permitted source material and recreate affected derivatives from
currently authorized content; do not reconstruct historical permissions from
a newer, broader source policy.

The separate parent-scope checkpoint identified the same historical-audience
obligation for reports with direct knowledge evidence. That branch now
authorizes its persisted evidence before current-document lookup, covering
document reads, lists, overview counts and idempotent replay. A private-to-public
revision regression also verifies that the original reader retains access and
new readers can create reports from the new public content.

Following the user's renewed request, the original core-review channel was
asked to review `cf37503` under unchanged safeguards. It again returned the
safety error. It was not rerouted to another channel. Neither the interrupted
parent checkpoint nor these regression fixes closes the required complete
latest-HEAD review.

## Current local checks

| Check | Result |
|---|---|
| Full platform Python suite | 1,238 passed; 119 subtests passed |
| Full web suite | 110 passed |
| React kit | 24 passed |
| Gates | 12 passed |
| Web source build | Passed |
| VitePress build | Passed |
| Documentation lockfile install | Isolated offline `npm ci` passed; original dependency links preserved |
| CDK synthesis | Passed with lookups disabled and synthetic account `000000000000` |
| Workspace template checker | Passed; no checker issues |
| HyperFrames 0.8.36 | 17 samples; zero reported errors/warnings; 41/41 text contrast checks passed |
| Rendered Tour page | Desktop/tablet/mobile passed; 15 role selections and 24 chapter seeks |
| Browser media checks | Actual playback advanced; Korean captions loaded; no JS errors or HTTP 404s |

The web tests use Playwright's CI-default Chromium headless shell
151.0.7922.34. Full Chromium of the same version showed a nested-frame test
compatibility difference; no preview CSP was weakened. The documentation player
offers both H.264/AAC MP4 and VP9/Opus WebM because the installed full Chromium
does not decode H.264/AAC.

Existing build notices remain visible: large JavaScript chunks, Cedar syntax
highlighting fallback, and CDK deprecation/runtime notices. None was suppressed
to obtain a passing result.

## Required CI follow-up

The first Draft PR run passed Python, web, React and gates but found an outdated
privacy-integration assertion in the infrastructure check: it expected only the
WebSocket function and policy to change when the relay was enabled. The new
Workspace API also intentionally consumes that relay.

The checker now permits exactly the WebSocket and Workspace API function/policy
pairs, checks each exact relay ARN and invocation grant, and compares the entire
restored template with the disabled baseline. Unrelated changes remain rejected.
Eight focused regression cases and the complete offline privacy check passed
locally against PR base `29f39118017dc7473bf8fc206ef11ce49c6d37b7`.
This follow-up changes validation code and its CI invocation, not runtime
permissions or deployed resources. The updated commit's CI is a separate result.

## Content result

The installed `aws-content-plugin` 2.0.0 content-review procedure scored the
walkthrough **88.5/90, PASS**, with zero Critical findings and one nonblocking
secondary-typography warning. Its formal ten visual-testing points were exempt
because Playwright MCP could not initialize its expected Chrome binary.
Supplementary local Playwright tests nevertheless verified the actual rendered
page at 1440×1000, 768×1024 and 390×844.

This is a project content-quality result, not official AWS certification.
The full local report is `/tmp/workbench-content-review.md`. The rendered
VitePress build is retained locally; no Pages publication was performed.

| Artifact | Verified properties |
|---|---|
| `docs/public/media/ontology-workbench.mp4` | 240.0 seconds; 1920×1080; 24 fps; H.264 + AAC |
| `docs/public/media/ontology-workbench.webm` | 240.008 seconds; 1920×1080; 24 fps; VP9 + Opus fallback |
| `docs/public/media/ontology-workbench.vtt` | 42 ordered, bounded Korean caption cues |
| `docs/public/media/ontology-workbench-poster.jpg` | Extracted from the rendered video at approximately 15 seconds |

MP4 SHA-256:
`7907075d904b446766c8ad3ace755e3efdbd0e9a5af8448dd5db20329f10eaeb`.

All eight scenes and both alternate screenshot states were inspected. Captures
are synthetic sessions using the actual application API and test storage.
Customer PDFs, meeting transcripts, credentials and live Confluence material
were not included. Font licenses, local subsets, narration and captures remain
with the reproducible source; unused OTF copies and local media caches are not
part of the publication.

## Remaining review gate

The independent core AI review **did not complete**: its initial attempt ended
with a safety/high-risk-cyber error. No retry or reassignment occurred in that
initial handoff. The later request through the same original channel is recorded
above and also failed. Neither attempt nor the content review is approval.

Parent/business review findings were checked locally after their fixes.
The planning shortcut now retains product context while requiring an actual
graph target; ordinary UI tests and the synthetic API capture verify this flow.
These checks do not close the missing core AI review.

The original handoff was a **Draft PR to `main`**. The 2026-09-14 user request
authorizes integration once the gates pass. Commit CI and exact-HEAD review
remain required; do not route around the blocked core review or equate missing
coverage with approval.

No real Confluence target was supplied or configured. `CONFLUENCE.md` is
preparation documentation. Local feature hashing/private artifact indexes,
context-only Skill execution and internal MCP declarations remain explicitly
distinguished from managed search, arbitrary tool execution and Gateway
provisioning.
