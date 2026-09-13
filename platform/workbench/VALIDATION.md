# Workbench validation and draft handoff

Date: 2026-09-13. Scope: the ontology workbench change based on `dbb0af1`.
These are local results, not deployment, customer-service availability or
security-certification claims.

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

The independent core AI review **did not complete**: its attempt ended with a
safety/high-risk-cyber error. That coverage remains pending. It was not retried,
reassigned, substituted with the content review, or treated as approval.

Parent/business review findings were checked locally after their fixes.
The planning shortcut now retains product context while requiring an actual
graph target; ordinary UI tests and the synthetic API capture verify this flow.
These checks do not close the missing core AI review.

The authorized handoff is a **Draft PR to `main`**. Commit CI and an exact-HEAD
review remain external gates. **Do not merge or deploy the runtime** in this
handoff. Do not retry or route around the blocked core review.

No real Confluence target was supplied or configured. `CONFLUENCE.md` is
preparation documentation. Local feature hashing/private artifact indexes,
context-only Skill execution and internal MCP declarations remain explicitly
distinguished from managed search, arbitrary tool execution and Gateway
provisioning.
