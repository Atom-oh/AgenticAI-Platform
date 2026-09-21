# Customer guideline packs

## Source corpus intake (2026-09-21)

PPTX, XLSX, TSX/TS/JSX/JS and SCSS are private reference inputs. Code-bearing TXT
is classified as source, preserving bounded metadata/imports without execution.
Direct originals remain downloadable; prepared JSON packs identify originals
as local-only. Original status never grants approval. Deleted/deprecated sources
are excluded from generation. XLSX retains worksheet XML/cell/row coordinates;
formulas are not evaluated and row groups become searchable pages.

Small ZIP imports retain the 50 MiB HTTP and 20-original limits. Prepare large
corpora locally using:

```bash
python -m workspace.prepare_sources /private/source-corpus.zip \
  --output-dir /private/prepared-sources
```

The tool bounds inventory to 30,000 entries/8 GiB expanded, processes each
supported original independently and writes content-addressed packs within
the existing 20-source, 1,200-page and 16 MiB limits. Matching outputs are reused
on restart. Protected, unsupported and unreadable originals appear in an explicit
local exclusion receipt. Truncated code cannot be selected as model context.
There is no arbitrary nested-ZIP recursion, code execution or network retrieval.
Server ZIP PDF extraction runs in a separate process without inherited AWS credential variables,
with 1.5 GiB address-space, 45-second CPU, 50-second wall-time and 16 MiB output
limits. PDF text is requested in bounded ranges. XLSX parsing bounds shared
strings, cell/row counts and cumulative expanded text before assembling pages.
PPTX text is accumulated in one bounded traversal per slide, with a cumulative
document budget. Server source intake has one 120-second parsing deadline,
shortened to reserve 30 seconds of remaining Lambda time for persistence.
Each PDF child receives only the remaining budget. Unprocessed members are
reported as exclusions, so a slow mixed ZIP can retain completed extracts.
Text/code classification and extraction inspect a bounded prefix; omitted text
is explicitly truncated and unavailable as generation evidence.

Small mixed ZIPs report excluded members and partial extraction, retaining the
original archive. An archive with no readable supported source is unsupported,
not complete. Unsafe archive paths remain a hard rejection.

Select `sources-*.json` in the workspace and use **준비한 파일 모두 보관**.
The queue supports 500 files, serial uploads, stop-after-current and per-file
retries with existing chunk checkpoints. Queue state is in memory, not restored
after reload. Model selection remains bounded to 20 assets/12 source pages.

Filter packs by filename or declared source ID, then search the selected pack's
pages. Metadata, platform approval and executable package readiness remain
separate. React examples do not install missing SDK/styles/routing/API contracts.

The Studio workspace and the UX asset portal share a private guideline library.
A designer searches source pages, selects the pages relevant to a screen, and
uses them in the existing rule proposal, approval and React generation flow.
This adds source navigation; it does not install a customer's component package.

## Prepare and use

Install the dependencies in `workspace/requirements.txt`. Run from `platform/`:

```bash
python -m workspace.prepare_guidelines /private/customer-guides.zip \
  --output /private/customer-guidelines.json
```

The ZIP may contain PDF and PPTX files. Preparation reads files locally, never
executes slide programs or follows external links, and does not call a model.
PDF pages retain physical order; PPTX slides follow presentation relationships
rather than lexical filenames. The output path must not already exist.
Keep the ZIP, output and any analysis private. Repository-local work belongs in
the ignored `.local/ux-guidelines/` directory, outside the web/public tree.

1. Choose the personal workspace or the intended project.
2. In **기준·자산 → 화면·그래픽 자료**, upload the JSON with purpose
   **업무·스타일 가이드**.
3. Open **기준·자산 → 고객 가이드**, also accessible from the UX asset portal's
   **프로젝트 UX 기준·자산** area.
4. Search across all prepared pages or choose an individual source. Review
   definitions, structure, states and application conditions. Select at most
   12 pages and 60,000 source characters for one rule proposal.
5. Define the goal and relevant states in **업무 정의**, then request rules in
   **흐름·상태 설계**. Review conditions, exceptions, source
   citations and unresolved conflicts before approving the exact rule version.
6. Generate and inspect in **시안·검수**, then open **개발 전달** for an exact
   approved-source rebuild, ZIP downloads and configured Git export.

Selection does not approve content. A saved contract retains its own page
selection even if the next proposal's selection changes. **현재 선택한 파일 적용**
explicitly updates the edited contract, requiring a new save and approval.

## Data and provenance

The uploaded JSON has `format:"ux-guidelines"`, `schemaVersion:1` and `sources`.
Each source declares `id,name,sha256,category,pageCount` and
`pages:[{page,text,truncated?}]`. Categories are `foundation`, `interaction`,
`graphics`, `content`, `writing`, or `general`. Local preparation suggests a
category from the filename; it does not determine authority or precedence.

The server validates the pack, recalculates every text hash, and stores the
normalized page index as a separate immutable private blob. Asset metadata
contains source summaries; the ordinary bounded analysis text does not duplicate
or truncate the page index. The uploaded JSON itself remains the immutable
original of this workspace asset.

The PDF/PPTX bytes remain local and are not included in the JSON. Their declared
SHA-256 identifies the original used by the preparation tool, but the server
cannot independently authenticate an original it has not received. UI labels
therefore retain `originalStatus:"local-only"` and `reviewStatus:"unreviewed"`.
Do not treat these extracts as approved internal-document-library originals.

Only selected pages reach model context, through the existing boundary gate.
No model or external service is called by search. An explicit rule citation must
match the selected asset, source ID, physical page and actual extracted quote.
Text hashes establish byte linkage, not semantic correctness or completeness.

## Interfaces and bounds

- `GET /assets/:id/guidelines?sourceId=&q=&cursor=0`: authorized private source
  summaries, eight full page extracts, total matching pages and next cursor.
  Current project membership and asset state apply on every request.
- `/contracts/propose` and editable contracts accept `guideRefs`:
  `{assetId,sourceId,page,sourceSha256,textSha256}[]`.
  References participate in request idempotence and the approved contract hash.
- Explicit rule sources for packs add `sourceId` and `page`. Quotes from another
  page, stale hashes, missing references, empty text and truncated pages fail.
- Runs snapshot the immutable pack index identity and retain the contract's
  selected pages. React/HTML generation and repairs reuse those frozen inputs.
- ZIP: 400MiB total, 20 entries, 256MiB per original; nested PPTX entries are
  separately bounded. Pack: 16MiB, 1,200 extracted pages, 3,000,000 text characters,
  at most 20,000 characters per page. Truncation is explicit and cannot be selected.

## Limits

Preparation extracts text, not images, diagrams, slide notes, animations or
complete layout semantics. A page with text may still omit decisive graphics.
Compare the extract with the local original before approving rules; this path
does not provide an original-image visual baseline. Regular image intake remains
available for an explicitly selected visual reference.

Filename, cover and revision-history versions can disagree. The model is
instructed to honor explicitly supplied precedence and report unresolved
conflicts; this is not a deterministic conflict detector or coverage guarantee.
Unselected sources are not reviewed. Package labels or component names in a guide
do not establish implementation in the pinned React kit.

Offline verification uses synthetic PDF/PPTX, private storage/API doubles, exact
source checks and browser interactions. Customer-model runs, service deployment,
customer package integration and publication require separate evidence.
