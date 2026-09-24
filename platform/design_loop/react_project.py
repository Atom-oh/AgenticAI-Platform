"""Compositions + flow -> react-kit project files, and the local compile runner (engine plan, Task E12; R-01, R-02;
Codex #13, #15).

One rendering path: the emitted source *is* the preview, the Browser-verified bundle, the approved source and the
release rebuild input. The project stays inside `react-kit/policy.cjs` by construction: `src/App.tsx`,
`src/pages/<slug>.tsx` (one per screen and approved state) and `src/logic/*.ts`, at most 24 files and 128 KiB.

Every literal is emitted as a JSON expression attribute (`prop={"..."}`), so quotes and backslashes can never break
JSX; `pageId` is the one literal JSX string attribute, validated first against the kit pattern. Callback props are
emitted only by the trusted adapters. Every node gets its test id from `convention.Registry` (AZ1).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .composition import walk
from .convention import PAGE_ID, VALUE_FIELD, Registry
from .local_runner import run_node

KIT = Path(__file__).resolve().parents[1] / "react-kit"
KIT_IMPORT = "@studio/approved-ui"
MAX_FILES, MAX_BYTES = 24, 128 * 1024
LOGIC = ("src/logic/cases.ts", "src/logic/data.ts", "src/logic/flow.ts", "src/logic/types.ts")
# Kit props whose TypeScript type is a numeric literal union (react-kit/ui/types.ts: Gap, columns). Ontology enum
# values are strings, so these are emitted as numbers; anything else stays a string and the types gate decides.
KIT_NUMERIC = {"Stack": {"gap"}, "Grid": {"gap", "columns"}, "Inline": {"gap"}}
# Case-fixture form values per controlled adapter; the contract (E13) fills the same values.
# Choice fixtures pick the second option: a controlled <select> whose state never updates still shows its first option.
FIXTURE = {"controlled-text": "10000", "controlled-bool": True, "controlled-choice": "o2"}
FINISHED_TEST_ID = "flow-finished"
CASE_SELECT = "case-select"


def _js(value):
    return json.dumps(value, ensure_ascii=False)


def pascal(slug):
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"-+", slug) if part) + "Page"


def _code(k, asset_id):
    code = (k.assets.get(asset_id) or {}).get("code")
    if not code:
        raise ValueError(f"asset {asset_id} has no code layer")
    if code.get("importPath") != KIT_IMPORT:
        raise ValueError(f"asset {asset_id} imports an unsupported package")
    return code


def _literal(export, name, spec, value):
    if spec and spec["type"] == "enum" and name in KIT_NUMERIC.get(export, ()) and re.fullmatch(r"[0-9]+", str(value)):
        return str(int(value))
    return _js(value)


class _Page:
    """Emission state for one page."""

    def __init__(self, k, registry, screen, composition):
        self.k, self.screen, self.c = k, screen, composition
        self.ids = registry.test_ids(screen, composition)
        self.registry = registry
        self.components, self.helpers, self.required, self.choices = set(), set(), [], {}

    def node(self, node, depth):
        k, pad = self.k, "  " * depth
        code = _code(k, node["asset"])
        export, specs, adapter = code["exportName"], code.get("props", {}), code.get("adapter")
        self.components.add(export)
        test_id = self.ids["byNode"][node["id"]]
        attrs = [f"testId={{{_js(test_id)}}}"]
        props, bind = node.get("props") or {}, node.get("bind") or {}
        text_child = code.get("childrenProp") == "children"
        for name in sorted(props):
            if name == "children" and text_child:
                continue
            value = props[name]
            if adapter == "controlled-choice" and name == "options" and isinstance(value, list):
                mapping = {f"o{j}": item["value"] for j, item in enumerate(value, 1)}
                self.choices[test_id] = mapping
                value = [{"value": f"o{j}", "label": item["label"]} for j, item in enumerate(value, 1)]
            attrs.append(f"{name}={{{_literal(export, name, specs.get(name), value)}}}")
        for name in sorted(bind):
            if name == "children" and text_child:
                continue
            attrs.append(f"{name}={{data[{_js(bind[name])}]}}")
        if adapter in ("controlled-text", "controlled-choice"):
            self.helpers.add("fieldText")
            attrs += [f"value={{fieldText(form, {_js(test_id)})}}", f"onChange={{v => setField({_js(test_id)}, v)}}"]
        elif adapter == "controlled-bool":
            self.helpers.add("fieldFlag")
            attrs += [f"checked={{fieldFlag(form, {_js(test_id)})}}", f"onChange={{v => setField({_js(test_id)}, v)}}"]
        if adapter in VALUE_FIELD and self._required(node):
            self.required.append((test_id, node.get("visibleWhen")))
        click = (node.get("on") or {}).get("click")
        if adapter == "action" and click is not None:
            attrs.append(f"onClick={{() => go({_js(click)})}}")
            if node["id"] == self.ids.get("ctaNode") and export == "Button":
                attrs.append("disabled={!ready}")
        children = []
        if text_child and "children" in props:
            children.append(f"{pad}  {{{_js(props['children'])}}}")
        if text_child and "children" in bind:
            children.append(f"{pad}  {{data[{_js(bind['children'])}]}}")
        for child in node.get("children") or []:
            children.append(self.wrapped(child, depth + 1))
        head = f"<{export} {' '.join(attrs)}"
        element = f"{head}>\n" + "\n".join(children) + f"\n{pad}</{export}>" if children else f"{head} />"
        return element

    def wrapped(self, node, depth):
        pad = "  " * depth
        element = self.node(node, depth)
        spec = node.get("visibleWhen")
        if spec is None:
            return pad + element
        self.helpers.add("visible")
        guard = f"visible({{ when: {_js(spec['when'])}, negate: {'true' if spec['negate'] else 'false'} }}, caseState)"
        return f"{pad}{{{guard} && (\n{pad}  {element}\n{pad})}}"

    def _required(self, node):
        asset = self.k.assets.get(node["asset"]) or {}
        field = VALUE_FIELD.get(((asset.get("code") or {}).get("adapter")))
        return any(b["field"] == field and b["required"] for b in asset.get("bindings", []))


def _page_source(k, registry, flow, screen, state, composition, meta):
    template = k.templates.get(composition["templateId"])
    if template is None or (template.get("code") or {}).get("exportName") != "Screen" \
            or template["code"].get("importPath") != KIT_IMPORT:
        raise ValueError("page templates must render the kit Screen")
    page_id = registry.state_page_id(screen, state)
    if not PAGE_ID.fullmatch(page_id):
        raise ValueError("invalid pageId")
    page = _Page(k, registry, screen, composition)
    body = [page.wrapped(node, 4) for name in template["slots"] for node in composition["slots"].get(name, [])]
    guard = []
    if page.required:
        page.helpers.add("filled")
        terms = []
        for test_id, spec in page.required:
            term = f"filled(form, {_js(test_id)})"
            if spec is not None:
                page.helpers.add("visible")
                term = (f"(!visible({{ when: {_js(spec['when'])}, negate: {'true' if spec['negate'] else 'false'} }}, "
                        f"caseState) || {term})")
            terms.append(term)
        guard = [f"  const ready = {' && '.join(terms)};"]
    elif page.ids.get("ctaNode"):
        guard = ["  const ready = true;"]
    components = sorted(page.components | {"Screen", "Stack"})
    lines = [f"import {{ {', '.join(components)} }} from '{KIT_IMPORT}';",
             "import type { PageProps } from '../logic/types';"]
    if page.helpers:
        lines.append(f"import {{ {', '.join(sorted(page.helpers))} }} from '../logic/flow';")
    lines += ["", f"export const meta = {{ {', '.join(f'{key}: {_js(meta[key])}' for key in META_KEYS)} }} as const;", "",
              f"export default function {pascal(page_id)}({{ data, form, setField, go, caseState }}: PageProps) {{",
              *guard,
              "  return (",
              f'    <Screen pageId="{page_id}" testId={{{_js(page_id)}}} title={{{_js(k.screens[screen]["title"])}}} '
              f'width={{"mobile"}}>',
              "      <Stack>", *body, "      </Stack>", "    </Screen>", "  );", "}", ""]
    return "\n".join(lines), page.choices


META_KEYS = ("sid", "type", "dver", "status", "level1", "level2", "level3")


def _meta(k, registry, flow, screen, state, meta):
    given = (meta or {}).get((screen, state))
    if given is not None:
        if set(given) != set(META_KEYS) or not all(isinstance(given[key], str) for key in META_KEYS):
            raise ValueError("page meta needs sid, type, dver, status and three levels")
        return given
    procedure = k.procedures.get(flow["procedureId"], {}).get("title", "")
    return {"sid": registry.page_id(screen), "type": "page", "dver": "1", "status": "draft",
            "level1": procedure, "level2": k.screens[screen]["title"], "level3": state}


def _forms(k, registry, screens):
    """Case-fixture form values for every controlled input of the project, keyed by its field test id."""
    form = {}
    for (screen, _), composition in screens.items():
        ids = registry.test_ids(screen, composition)
        for node_id, key in ids["fieldOf"].items():
            adapter = ((k.assets.get(next(n["asset"] for _, n, _ in walk(composition) if n["id"] == node_id)) or {})
                       .get("code") or {}).get("adapter")
            form.setdefault(ids["byNode"][node_id], FIXTURE[adapter])
    return form


def _ts_type(value):
    if isinstance(value, list):
        keys = sorted({key for item in value for key in item}) if value else ["label", "value"]
        return "{ " + "; ".join(f"{_js(key)}: string" for key in keys) + " }[]"
    return "string"


def project(flow, screens, k, prd_bindings, *, cases, registry=None, meta=None):
    """`screens` maps `(screenId, state)` to its composition. Returns `{path: source}`."""
    registry = (registry or Registry(k)).register_flow(flow)
    for s in flow["screens"]:
        if (s, "default") not in screens:
            raise ValueError(f"missing default composition for {s}")
    order = {s: i for i, s in enumerate(flow["screens"])}
    keys = sorted(screens, key=lambda key: (order.get(key[0], len(order)), key[1] != "default", key[1]))
    if any(key[0] not in order for key in keys):
        raise ValueError("composition for a screen outside the flow")
    if 1 + len(keys) + len(LOGIC) > MAX_FILES:
        raise ValueError("project-file-limit")
    if not cases:
        raise ValueError("at least one case is required")
    files, pages, choices, names = {}, [], {}, set()
    for screen, state in keys:
        composition = screens[(screen, state)]
        if composition.get("screenId") != screen or composition.get("state") != state:
            raise ValueError("composition does not match its (screen, state) key")
        slug = registry.state_page_id(screen, state)
        name = pascal(slug)
        if name in names:
            raise ValueError("page-name-collision")
        names.add(name)
        source, page_choices = _page_source(k, registry, flow, screen, state, composition,
                                            _meta(k, registry, flow, screen, state, meta))
        files[f"src/pages/{slug}.tsx"] = source
        choices.update(page_choices)
        pages.append((screen, state, slug, name))
    form = _forms(k, registry, screens)
    files["src/logic/types.ts"] = "\n".join([
        "import type { Data } from './data';", "import type { CaseState } from './flow';", "",
        "export type Form = Record<string, string | boolean>;",
        "export type PageProps = {", "  data: Data;", "  form: Form;",
        "  setField: (name: string, value: string | boolean) => void;", "  go: (action: string) => void;",
        "  caseState: CaseState;", "};", ""])
    data_type = "; ".join(f"{_js(path)}: {_ts_type(value)}" for path, value in sorted(prd_bindings.items()))
    files["src/logic/data.ts"] = "\n".join([
        f"export type Data = {{ {data_type} }};", "",
        f"export const data: Data = {_js(dict(sorted(prd_bindings.items())))};", "",
        "// Choice option ordinals (o1, o2, ...) -> business values; labels are unchanged.",
        f"export const choices: Record<string, Record<string, string>> = {_js(choices)};", ""])
    transitions = [{"id": t["id"], "src": t["src"], "dst": t["dst"], "when": t.get("when"),
                    "navigation": t.get("navigation") or "forward"} for t in flow["transitions"]]
    files["src/logic/flow.ts"] = FLOW_TS.replace("__START__", _js(flow["entry"])) \
        .replace("__TERMINALS__", _js(list(flow["terminals"]))).replace("__TRANSITIONS__", _js(transitions))
    entries = []
    for i, _ in enumerate(cases):
        for screen, state, _, _ in pages:
            for empty in (False, True):
                value = f"{i}:{screen}:{state}" + (":empty" if empty else "")
                label = f"케이스 {i + 1} · {k.screens[screen]['title']} · {state}" + (" · 빈 입력" if empty else "")
                entries.append({"value": value, "label": label, "caseIndex": i, "screen": screen, "state": state,
                                "empty": empty})
    fixtures = [{"conditions": dict(case), "form": form} for case in cases]
    files["src/logic/cases.ts"] = "\n".join([
        "import type { CaseState } from './flow';", "import type { Form } from './types';", "",
        "export type CaseFixture = { conditions: CaseState; form: Form };",
        "export type Entry = { value: string; label: string; caseIndex: number; screen: string; state: string; "
        "empty: boolean };", "",
        f"export const cases: CaseFixture[] = {_js(fixtures)};", "",
        f"export const entries: Entry[] = {_js(entries)};", ""])
    views = [f'      {{view === {_js(screen + "|" + state)} && (\n'
             f"        <{name} data={{data}} form={{form}} setField={{setField}} go={{go}} caseState={{caseState}} />\n"
             f"      )}}" for screen, state, _, name in pages]
    files["src/App.tsx"] = "\n".join([
        "import { useState } from 'react';", f"import {{ Alert, Select, Stack }} from '{KIT_IMPORT}';",
        *[f"import {name} from './pages/{slug}';" for _, _, slug, name in pages],
        "import { cases, entries } from './logic/cases';", "import { data } from './logic/data';",
        "import { next, start } from './logic/flow';", "import type { Form } from './logic/types';", "",
        "export default function App() {",
        "  const [entry, setEntry] = useState<string>(entries[0].value);",
        "  const [screen, setScreen] = useState<string>(start);",
        "  const [state, setState] = useState<string>('default');",
        "  const [caseIndex, setCaseIndex] = useState<number>(0);",
        "  const [form, setForm] = useState<Form>(cases[0].form);",
        "  const [finished, setFinished] = useState<boolean>(false);",
        "  const caseState = cases[caseIndex].conditions;",
        "  const setField = (name: string, value: string | boolean) => setForm(prior => ({ ...prior, [name]: value }));",
        "  const go = (action: string) => {",
        "    if (action === 'finish') {", "      setFinished(true);", "      return;", "    }",
        "    const target = next(screen, action, caseState);",
        "    if (target !== null) {", "      setScreen(target);", "      setState('default');",
        "      setFinished(false);", "    }", "  };",
        "  const choose = (value: string) => {",
        "    const chosen = entries.find(item => item.value === value);",
        "    if (!chosen) return;",
        "    setEntry(value);", "    setCaseIndex(chosen.caseIndex);", "    setScreen(chosen.screen);",
        "    setState(chosen.state);", "    setForm(chosen.empty ? {} : cases[chosen.caseIndex].form);",
        "    setFinished(false);", "  };",
        "  const view = screen + '|' + state;",
        "  return (",
        "    <Stack>",
        f"      <Select testId={{{_js(CASE_SELECT)}}} label={{\"검증용 케이스\"}} value={{entry}} onChange={{choose}}",
        "        options={entries.map(item => ({ value: item.value, label: item.label }))} />",
        *views,
        f"      {{finished && <Alert testId={{{_js(FINISHED_TEST_ID)}}} tone={{\"success\"}} "
        f"message={{\"절차를 완료했습니다.\"}} />}}",
        "    </Stack>", "  );", "}", ""])
    total = sum(len(v.encode("utf-8")) for v in files.values())
    if len(files) > MAX_FILES:
        raise ValueError("project-file-limit")
    if total > MAX_BYTES:
        raise ValueError("project-size-limit")
    return dict(sorted(files.items()))


FLOW_TS = """import type { Form } from './types';

export type CaseState = Record<string, boolean>;
export type VisibleSpec = { when: string; negate: boolean };
export type Transition = { id: string; src: string; dst: string; when: string | null; navigation: string };

export const start: string = __START__;
export const terminals: string[] = __TERMINALS__;
export const transitions: Transition[] = __TRANSITIONS__;

// The one `when` evaluator (workspace.ontology_ux.evaluate): every term needs a boolean in the case.
export function holds(when: string | null, caseState: CaseState): boolean {
  if (when === null) return true;
  let result = true;
  for (const raw of when.split('&')) {
    const term = raw.trim();
    const negated = term.startsWith('!');
    const name = term.slice(negated ? 6 : 5);
    const value = caseState[name];
    if (typeof value !== 'boolean') throw new Error('케이스에 조건 값이 없습니다: ' + name);
    result = result && value !== negated;
  }
  return result;
}

// Shared visibility rule: whole-expression negation (AJ1).
export function visible(spec: VisibleSpec, caseState: CaseState): boolean {
  return holds(spec.when, caseState) !== spec.negate;
}

// `next` selects the single forward transition whose `when` holds; an explicit id must leave this screen.
export function next(screen: string, action: string, caseState: CaseState): string | null {
  if (action === 'next') {
    const enabled = transitions.filter(t => t.navigation === 'forward' && t.src === screen && holds(t.when, caseState));
    return enabled.length === 1 ? enabled[0].dst : null;
  }
  const chosen = transitions.find(t => t.id === action && t.src === screen);
  return chosen ? chosen.dst : null;
}

export function fieldText(form: Form, name: string): string {
  const value = form[name];
  return typeof value === 'string' ? value : '';
}

export function fieldFlag(form: Form, name: string): boolean {
  return form[name] === true;
}

export function filled(form: Form, name: string): boolean {
  const value = form[name];
  return value === true || (typeof value === 'string' && value.trim() !== '');
}
"""


# ---- asset gallery (type check of every seed mapping; C5 layers.gui snapshots) --------------------------------

def _sample(spec, asset, name, binding_values):
    """A sample of the *declared* type (a bound value only when it has that type), so a mapping that declares the
    wrong type reaches the kit's types gate."""
    bound = next((b["path"] for b in asset.get("bindings", []) if b["field"] == name), None)
    value = binding_values.get(bound) if bound is not None else None
    kind = spec["type"]
    if kind == "string":
        return value if isinstance(value, str) else asset["title"]
    if kind == "list" and isinstance(value, list):
        return value
    if kind == "enum":
        return spec["values"][0]
    if kind == "number":
        return 1
    if kind == "boolean":
        return False
    if kind == "list":
        return [{field: ("샘플" if t == "string" else 1) for field, t in spec["item"].items()}]
    return None


def asset_gallery(k, binding_values):
    """One page rendering each renderable asset once, with adapter wiring and sample values of each prop type."""
    components, elements, helpers = {"Screen", "Stack"}, [], set()
    for n, asset_id in enumerate(sorted(a for a, v in k.assets.items() if v.get("code")), 1):
        asset = k.assets[asset_id]
        code = _code(k, asset_id)
        export, specs, adapter = code["exportName"], code.get("props", {}), code.get("adapter")
        components.add(export)
        attrs = [f"testId={{{_js(f'g-n{n}')}}}"]
        key = f"g-f{n}"
        for name in sorted(specs):
            spec = specs[name]
            if spec["type"] in ("callback", "node") or name in (set(VALUE_FIELD.values()) if adapter in VALUE_FIELD
                                                                 else set()):
                continue
            value = _sample(spec, asset, name, binding_values)
            if value is not None:
                attrs.append(f"{name}={{{_literal(export, name, spec, value)}}}")
        if adapter in ("controlled-text", "controlled-choice"):
            helpers.add("text")
            attrs += [f"value={{text({_js(key)})}}", f"onChange={{v => setField({_js(key)}, v)}}"]
        elif adapter == "controlled-bool":
            attrs += [f"checked={{form[{_js(key)}] === true}}", f"onChange={{v => setField({_js(key)}, v)}}"]
        elif adapter == "action":
            attrs.append("onClick={() => setField('clicked', true)}")
        head = f"<{export} {' '.join(attrs)}"
        if code.get("childrenProp") == "children":
            elements.append(f"        {head}>{{{_js(asset['title'])}}}</{export}>")
        else:
            elements.append(f"        {head} />")
    page = "\n".join([
        "import { useState } from 'react';",
        f"import {{ {', '.join(sorted(components))} }} from '{KIT_IMPORT}';", "",
        "export default function GalleryPage() {",
        "  const [form, setForm] = useState<Record<string, string | boolean>>({});",
        "  const setField = (name: string, value: string | boolean) => setForm(prior => ({ ...prior, [name]: value }));",
        "  const text = (name: string): string => { const value = form[name]; return typeof value === 'string' ? value : ''; };",
        "  return (",
        '    <Screen pageId="gallery" testId={"gallery"} title={"자산 갤러리"} width={"mobile"}>',
        "      <Stack>", *elements, "      </Stack>", "    </Screen>", "  );", "}", ""])
    app = "\n".join(["import GalleryPage from './pages/gallery';", "",
                     "export default function App() {", "  return <GalleryPage />;", "}", ""])
    return {"src/App.tsx": app, "src/pages/gallery.tsx": page}


# ---- compile ----------------------------------------------------------------------------------------------------

def compile_request(files, *, catalog_hash, contract):
    request = {"files": dict(files), "assets": {}, "expectedCatalogHash": catalog_hash}
    if contract is not None:
        request["contract"] = contract
    return request


def compile_local(request):
    """Run `react-kit/compile.cjs` offline (B2 swaps the runner for Code Interpreter with the same shapes)."""
    return run_node(KIT / "compile.cjs", request, timeout=180)


def kit_catalog_hash():
    """The pinned kit catalog hash, computed by the kit's own manifest (codegen never hashes the kit itself)."""
    return run_node(KIT / "hash.cjs", {}, timeout=30)["hash"]
