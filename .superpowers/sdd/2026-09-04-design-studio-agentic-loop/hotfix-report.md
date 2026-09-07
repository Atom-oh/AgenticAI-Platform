# Design-studio agentic loop hotfix — 2026-09-07

Commit: `44f4937026cf18e7a403e6520e16960a3db32658` on `main`
(`platform: studio: scrub identifiers from model-bound feedback (gate-safe regenerate/refine/review); lenient reviewer JSON fallback`)

## Part A — gate-safe model-bound content

- `platform/studio/sanitize.py` (new): `scrub_result(text) -> (str, int)` and `scrub(text) -> str`.
  Imports `RULES` directly from `platform/api/common/pii.py:20-29` (same rule table
  `scan_rules` uses) and, for each `(kind, pattern)`, replaces every regex match with a
  same-shape mask — digit chars → `*`, separators/other chars kept — via
  `re.Pattern.subn` (`studio/sanitize.py:19-32`). No second copy of the regex text.
- `platform/studio/loop.py`:
  - `loop.py:12` imports `sanitize`.
  - `loop.py:143-149` (in `run()`): before `prompts.build_user_prompt(...)`, scrubs
    `prev_html` and (in refine mode) `refine["elementHtml"]` into local `_safe` copies;
    the unscrubbed `prev_html` variable is left untouched for the later
    `prev_html = html or prev_html` carry-forward and for `_review_round`'s
    (non-model-bound) `skeleton_diff` comparison. `generate` stage event now carries
    `scrubbed=<n>` (`loop.py:149`).
  - `loop.py:82-84` (in `_review_round`): scrubs `review.dom_digest(doc)` before
    `prompts.build_review_prompt(...)`; `_review_round` returns `scrubbed` in its dict
    (`loop.py:92`), and the `review` stage event carries `scrubbed=rv.get("scrubbed", 0)`
    (`loop.py:174-176`).
  - Published HTML (`publish(...)`) and the `items`/`history` payload are never scrubbed —
    only the two model-bound prompt-construction points above.
- `platform/skills/studio-draft-html.md`: added a rule (after the sample-data line) that
  sample identifiers (계좌번호·전화번호·주민번호·카드번호) must be rendered masked
  (`110-***-******`, `010-****-****`), never as full digit strings.
- Tests:
  - `platform/tests/test_studio_sanitize.py` (new, 3 tests): masking shape/Korean-text
    preservation, no-op + count 0 on clean text, and `pii.scan_rules(scrub(x)) == []` on
    a multi-identifier string (account/phone/card/RRN).
  - `platform/tests/test_studio_loop.py`: added `BAD_HTML_WITH_ACCOUNT` fixture plus
    `test_regenerate_prompt_is_gate_safe` (round-2 generate prompt lacks the raw account
    number, contains the masked form) and `test_review_digest_is_scrubbed` (captured
    reviewer prompt lacks the raw account number). Also added `sys.path.insert(0, ROOT/"api")`
    at the top of the file (needed once `loop.py` transitively imports `common.pii`).

## Part B — lenient reviewer JSON

- `platform/studio/review.py`:
  - New `_lenient_parse(text, item_ids)` (`review.py:272-291`): per-item regex recovery —
    anchors on `"id":"<id>"`, takes the text window up to the next `"id":"` (or end),
    and inside that window regex-matches `"verdict":"(pass|fail)"` plus non-greedy
    `"evidence":"(.*?)",` / `"fix":"(.*?)"[,}]`. Missing pieces just leave that item `None`.
  - `parse_review` (`review.py:294-322`): unchanged strict path is tried first; now falls
    back to `_lenient_parse` both when `json.loads` raises `JSONDecodeError` and when the
    parsed object has no (or empty) `"items"`. Error string in both fallback cases is
    `f"부분 파싱: {len(recovered)}/{len(item_ids)}건 복구"`. The existing strict-JSON tests
    (`test_parse_review_strict_json`) are untouched and still pass — the "maybe" verdict
    case still goes through the strict path since it *does* have a non-empty `items` list.
- `platform/studio/prompts.py`: `REVIEW_SYSTEM` (`prompts.py:114-118`) gained the line
  `evidence·fix 문자열 안에는 큰따옴표(")와 줄바꿈을 쓰지 말고 「」 를 쓴다.`
- Tests: `platform/tests/test_studio_review.py` added
  `test_parse_review_lenient_recovers_items` — item 2's `evidence` has an unescaped `"`
  that breaks strict `json.loads`; asserts items 1 and 3 are recovered (`verdict == "pass"`)
  and `err.startswith("부분 파싱")`.

## RED/GREEN evidence

- Part A RED: `ImportError: cannot import name 'sanitize' from 'studio'` (before
  `studio/sanitize.py` existed); then `test_regenerate_prompt_is_gate_safe` /
  `test_review_digest_is_scrubbed` failed with the raw `"110-234-567890"` string present in
  the captured prompt, before the `loop.py` scrub points were wired in.
- Part A GREEN: `pytest tests/test_studio_sanitize.py -q` → 3 passed;
  `pytest tests/test_studio_loop.py -q` → 17 passed (includes both new tests).
- Part B RED: `test_parse_review_lenient_recovers_items` failed —
  `err == "리뷰어 JSON 파싱 실패: Expecting ',' delimiter: line 1 column 124 (char 123)"`,
  not startswith `"부분 파싱"`.
- Part B GREEN: `pytest tests/test_studio_review.py -q` → 11 passed (strict-JSON test
  `test_parse_review_strict_json` unchanged and still passing).

## Full suite

```
python3 -m pytest tests/ -q
434 passed, 1 warning in 9.39s
```
(warning is the pre-existing boto3/Python-3.9 deprecation notice, unrelated to this change)

## Concerns

- `_lenient_parse`'s evidence/fix capture (`"evidence":"(.*?)",`) still breaks if the
  reviewer's stray quote appears *inside* the evidence text right before the field's own
  closing quote+comma in a way that produces a shorter, wrong match rather than no match —
  it is a best-effort regex, not a real JSON5/relaxed parser. Given the `REVIEW_SYSTEM`
  prompt now tells the model to use 「」 instead of `"`/newlines inside evidence/fix, this
  should become rare in practice, but a determined adversarial or degenerate reviewer
  response could still yield a wrong (not just missing) recovered value.
- The `scrub` mask is shape-preserving per matched span, but if two rule regexes in
  `RULES` (`common/pii.py`) ever produced overlapping matches on the *same* substring in a
  future rule addition, only the first rule in table order gets to mask it (subsequent
  rules see already-masked, non-digit text and won't match) — currently not an issue for
  the existing 8 rules since they're digit-based and masking removes the digits they key
  on, but worth remembering if a non-digit-based rule is ever added to `RULES`.
- Did not modify `platform/api/common/pii.py` itself — Part A intentionally reuses its
  `RULES` table as the single source of truth per the task's "do not copy regex text"
  instruction.

## Hotfix 2 — 2026-09-07

Live e2e round-1 still hit `GateRefused … KR_BANK_ACCOUNT [studio.generate]`. Root cause:
Hotfix 1 scrubbed `prev_html`, `refine["elementHtml"]`, and the review digest at their
*source* points, but a few-shot reference draft (an approved past HTML sample) gets
injected into the **system** prompt via `prompts.build_system_prompt(..., fewshot=...)`,
and that sample contained a real-looking account number (`110-234-567890`) that was never
routed through `sanitize`. `assets_text` (also folded into the system prompt) and the full
`build_review_prompt(...)` output (assembled from spec + items + digest, not just the
digest) were equally unscrubbed at their point of use.

Fix: scrub the **final** strings handed to the model, not just their individual sources —
covers every current and future contributor to those strings in one place.

- `platform/studio/loop.py`:
  - `run()`: after `system = prompts.build_system_prompt(...)`, added
    `system, n_sys = sanitize.scrub_result(system)`; the `assets` stage event now carries
    `scrubbedSystem=n_sys` (int).
  - `run()` per round: after `user = prompts.build_user_prompt(...)`, added
    `user, n_user = sanitize.scrub_result(user)`; the existing point-scrubs of
    `prev_html`/`refine["elementHtml"]` (`n_scrub`) are kept (harmless, still useful for the
    `regenerate` stage's failure list rendering) and the `generate` stage's `scrubbed`
    total becomes `n_scrub + n_user`.
  - `_review_round()`: scrubs the full string returned by `prompts.build_review_prompt(...)`
    before calling `review_generate`, in addition to the existing digest scrub; both counts
    are summed into the returned `scrubbed`.
- Tests: `platform/tests/test_studio_loop.py` added
  `test_fewshot_reference_is_scrubbed_from_system_prompt` — passes
  `fewshot=["<html><body>계좌 110-234-567890</body></html>"]` and
  `assets_text="문의 010-1234-5678"` into `loop.run`, captures the `system` string a fake
  `generate` receives, and asserts both raw identifiers are absent, the masked form
  (`***-***-******`) is present, and the `assets` stage event's `scrubbedSystem >= 2`.

### RED/GREEN evidence

- RED: `test_fewshot_reference_is_scrubbed_from_system_prompt` failed —
  `AssertionError: assert '110-234-567890' not in '...'` (raw account number present in the
  captured system prompt, before the `loop.py` scrub point existed).
- GREEN: `pytest tests/test_studio_loop.py -q` → 18 passed (includes the new test; the 4
  Hotfix-1 tests are untouched and still pass).

### Full suite

```
python3 -m pytest tests/ -q
435 passed, 1 warning in 9.26s
```
(warning is the pre-existing boto3/Python-3.9 deprecation notice, unrelated to this change)

### Concerns

- Scrubbing the assembled `system`/`user`/review-prompt strings (rather than only their
  individual sources) is the intentionally more robust fix, but it means `sanitize.scrub_result`
  now runs the full `RULES` table over much larger strings (skills text, spec JSON, full
  review prompt) every round — negligible at current sizes (single-digit ms for regex
  over a few KB) but worth remembering if `RULES` grows or prompt sizes grow substantially.
  - The stage event's `scrubbed`/`scrubbedSystem` counts are per-string regex substitution
  counts (`sanitize.scrub_result`'s second return value), not deduplicated by identifier
  identity — the same account number matched twice (once in `fewshot`, once independently in
  `assets_text`) would count as 2, which is correct for "how many replacements happened" but
  not "how many distinct identifiers were found."
