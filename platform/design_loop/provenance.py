"""Displayed-literal provenance (engine plan, Task E10; global PRD-binding constraint; SPEC 12.4;
PR #30 review 7, #1 follow-up).

The engine no longer tries to recognise how a number is written. Seven review rounds showed that every grammar for
"a financial quantity in display text" misses another Korean form (split nodes, Hangul numerals, glued text, 영/삼
as numerals). Instead, every string a generated page shows literally must come from an approved source:

(a) **PRD binding.** The value is read from a binding path at render time (`bind`, `data[...]`); it is never a
    literal, so it is not checked here. `composition.validate` checks the path against the PRD catalog.
(b) **Approved knowledge copy.** An exact string of an *approved* node of the knowledge snapshot (`reviewState ==
    "approved"`; a node outside that state never contributes): a renderable asset's `title`, a string value
    of an asset's `stateProps` for a string-typed prop, or a Screen's `title`. These are the strings the deterministic
    `fill` and the `stateProps` overlay already use, and the ontology review approves them at an exact node revision.
    A number-typed literal is approved only when it is the node's own asset's `stateProps` value for that prop.
(c) **Engine template text.** The fixed strings `react_project` emits itself, enumerated in `ENGINE_TEXT`.

Anything else -- model-authored text, with or without a number -- is `unapproved-literal-text` (critical). The
match is exact: no trimming, no concatenation, no substring. Enum props are exempt because their value must be one
of the approved code layer's declared `values` (anything else is already a critical `prop-type`); booleans are not
displayed.

Reviewed copy that happens to contain a number is accepted: a human approved that exact text at that revision, so
the engine has not invented it (SPEC 12.4 forbids *inventing* amounts, rates or limits). The engine cannot alter,
split or join approved copy, so a model can no longer smuggle a value past the check by changing its spelling.
"""
from __future__ import annotations

CODE = "unapproved-literal-text"
MESSAGE = "displayed literal text must come from a PRD binding, approved knowledge copy or an engine template"

# ---- (c) engine template text: every visible string react_project emits outside the composition trees ----------
CASE_SELECT_LABEL = "검증용 케이스"            # App.tsx case selector label (verification harness)
FINISHED_MESSAGE = "절차를 완료했습니다."        # App.tsx completion alert on a terminal `finish`
CASE_PREFIX = "케이스 "                       # case entry label: CASE_PREFIX + index + " · " + screen title + " · " + state
CASE_SEPARATOR = " · "
CASE_EMPTY_SUFFIX = " · 빈 입력"              # the empty-form variant of a case entry
MISSING_CONDITION_ERROR = "케이스에 조건 값이 없습니다: "   # flow.ts error thrown for a malformed case fixture
GALLERY_TITLE = "자산 갤러리"                  # asset gallery heading (C5 layers.gui snapshot page)
GALLERY_SAMPLE = "샘플"                       # asset gallery sample value of a string list-item field
TEXT_FIXTURE = "10000"                        # case-fixture value typed into a controlled-text input (a user entry)

ENGINE_TEXT = {
    "case-select-label": CASE_SELECT_LABEL,
    "finished-message": FINISHED_MESSAGE,
    "case-prefix": CASE_PREFIX,
    "case-separator": CASE_SEPARATOR,
    "case-empty-suffix": CASE_EMPTY_SUFFIX,
    "missing-condition-error": MISSING_CONDITION_ERROR,
    "gallery-title": GALLERY_TITLE,
    "gallery-sample": GALLERY_SAMPLE,
    "text-fixture": TEXT_FIXTURE,
}


def _approved(entry):
    """Explicitly approved; a missing review state is not approval (fail closed)."""
    return isinstance(entry, dict) and entry.get("reviewState") == "approved"


def _specs(asset):
    code = (asset or {}).get("code") or {}
    specs = code.get("props")
    return specs if isinstance(specs, dict) else {}


def approved_copy(k):
    """(b): every exact string an approved node of `k` contributes as display copy."""
    texts = set()
    for asset in k.assets.values():
        if not _approved(asset):
            continue
        texts.add(asset.get("title"))
        specs = _specs(asset)
        for props in (asset.get("stateProps") or {}).values():
            for name, literal in (props or {}).items():
                if isinstance(literal, str) and (specs.get(name) or {}).get("type") == "string":
                    texts.add(literal)
    texts.update(s.get("title") for s in k.screens.values() if _approved(s))
    return frozenset(t for t in texts if isinstance(t, str) and t)


def screen_title_approved(k, screen_id):
    """The page heading react_project renders is the screen title: approved only for an approved Screen."""
    screen = k.screens.get(screen_id)
    return _approved(screen) and isinstance(screen.get("title"), str) and bool(screen["title"])


def _shown(value):
    """Every scalar a literal puts on screen (a numeric list field renders as text too); booleans are not shown."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (str, int, float)):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _shown(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _shown(item)


def _state_literals(asset, name):
    return [props[name] for props in ((asset or {}).get("stateProps") or {}).values()
            if isinstance(props, dict) and name in props]


def literal_approved(k, asset_id, name, literal, copy):
    """True if every scalar `literal` displays for prop `name` of a node of `asset_id` has an approved source."""
    asset = k.assets.get(asset_id)
    spec = _specs(asset).get(name)
    if isinstance(spec, dict) and spec.get("type") == "enum":
        return True                                    # a design token; a value outside `values` is `prop-type`
    own = [v for v in _state_literals(asset, name) if not isinstance(v, bool)] if _approved(asset) else []
    for value in _shown(literal):
        if isinstance(value, str):
            if value not in copy:
                return False
        elif not any(type(v) is type(value) and v == value for v in own):
            return False                               # a number is never copy; only its own reviewed stateProps
    return True
