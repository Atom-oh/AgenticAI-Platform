"""Screen registry: persistent UI page ids, short page keys and bounded test ids (engine plan, E12/E13/E14/E17).

One mapping serves every consumer (review round 32, AZ1): E12 codegen, E13 contract derivation, E14 edit subtree
exclusion and the Browser boxes and checkpoints all call `Registry`; no module builds a target string itself.

- `page_id(screen)`: the Screen's published `pageId`, unchanged when it matches the kit pattern (AX1); otherwise
  `p-` + 12 hex characters of `digest(screen)`. Collisions get a numeric suffix.
- `page_key(screen)`: a persisted ordinal `k<n>` (<= 6 characters) used for every derived id, so derived ids stay
  short even for 64-character published page ids.
- Test ids: `k<p>-cta` (primary action), `k<p>-f<n>` (controlled inputs), `k<p>-n<n>` (other nodes) and
  `k<p>-f<n>-o<j>` (choice options). Ordinals are allocated in the order the screen's shown assets and their binding
  fields first appear, never from field names or node ids (AU1), and persisted with the registry.

Customer convention (E17; R-01, R-03; Codex #14): `Registry.assign(screen)` allocates the next `<area>-<num>` screen
id and keeps it across regenerations (persisted with the registry state); `Convention.path` maps a screen id, surface
and state to the customer's relative `.tsx` path and refuses anything that could escape or is not the convention's
shape; `layout` pairs every approved page with its convention path.
"""
from __future__ import annotations

import copy
import json
import posixpath
import re
import unicodedata
from pathlib import Path

from workspace.ontology_schema import digest
from workspace.ontology_ux import STATES

from .composition import walk
from .route import shown_assets

PAGE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
MAX_KEY = 99999
# The adapter-owned prop that carries a controlled input's value, and its dataBinding field.
VALUE_FIELD = {"controlled-text": "value", "controlled-choice": "value", "controlled-bool": "checked"}


def _adapter(k, asset_id):
    return ((k.assets.get(asset_id) or {}).get("code") or {}).get("adapter")


def field_key(k, asset_id, occurrence=1):
    """Stable field key of a controlled input: its value binding path, else the asset id; plus the occurrence."""
    adapter = _adapter(k, asset_id)
    field = VALUE_FIELD.get(adapter)
    path = next((b["path"] for b in (k.assets.get(asset_id) or {}).get("bindings", []) if b["field"] == field), None)
    key = path or f"asset:{asset_id}"
    return key if occurrence == 1 else f"{key}#{occurrence}"


def node_key(asset_id, occurrence=1):
    return f"{asset_id}#{occurrence}"


class Registry:
    def __init__(self, k, state=None, convention=None):
        self.k = k
        self.state = copy.deepcopy(state) if state else {"schemaVersion": 1, "pages": {}}
        self.state.setdefault("sids", {})
        if not isinstance(self.state.get("pages"), dict) or not isinstance(self.state["sids"], dict):
            raise ValueError("Invalid registry state")
        self.convention = convention

    def to_json(self):
        return copy.deepcopy(self.state)

    # ---- pages ------------------------------------------------------------------------------------------------
    def _taken_ids(self):
        return {entry["pageId"] for entry in self.state["pages"].values()}

    def _entry(self, screen):
        pages = self.state["pages"]
        if screen in pages:
            return pages[screen]
        if screen not in self.k.screens:
            raise KeyError(screen)
        published = self.k.screens[screen].get("pageId")
        base = published if isinstance(published, str) and PAGE_ID.fullmatch(published) else "p-" + digest(screen)[:12]
        taken, candidate, n = self._taken_ids(), base, 1
        while candidate in taken or re.fullmatch(r"k[0-9]+--.+", candidate):
            n += 1
            suffix = f"-{n}"
            candidate = base[:64 - len(suffix)] + suffix
        ordinals = [int(e["key"][1:]) for e in pages.values()]
        ordinal = max(ordinals, default=0) + 1
        if ordinal > MAX_KEY:
            raise ValueError("registry-capacity")
        entry = pages[screen] = {"key": f"k{ordinal}", "pageId": candidate, "fields": {}, "nodes": {}}
        for asset_id in shown_assets(self.k, screen):      # deterministic pre-allocation, whoever calls first
            self._node(entry, node_key(asset_id))
            if _adapter(self.k, asset_id) in VALUE_FIELD:
                self._field(entry, field_key(self.k, asset_id))
        return entry

    def register_flow(self, flow):
        """Allocate every flow screen in flow order, so keys never depend on which consumer calls first."""
        for screen in flow["screens"]:
            self._entry(screen)
        return self

    def page_id(self, screen):
        return self._entry(screen)["pageId"]

    def page_key(self, screen):
        return self._entry(screen)["key"]

    def state_page_id(self, screen, state="default"):
        """The pageId (and page file slug) of one (screen, state) page."""
        if state == "default":
            return self.page_id(screen)
        return f"{self.page_key(screen)}--{state}"

    # ---- customer screen ids (E17) ----------------------------------------------------------------------------
    def assign(self, screen):
        """The screen's convention id: allocated once as the next number of the area, then stable."""
        sids = self.state["sids"]
        if screen in sids:
            return sids[screen]
        if self.convention is None:
            raise ValueError("convention-required")
        if screen not in self.k.screens:
            raise KeyError(screen)
        used = {self.convention.number(sid) for sid in sids.values() if self.convention.area_of(sid) == self.convention.area}
        sid = self.convention.sid(max(used, default=0) + 1)
        sids[screen] = sid
        return sid

    def sid_for(self, screen):
        if screen not in self.state["sids"]:
            raise KeyError(screen)
        return self.state["sids"][screen]

    # ---- test ids ---------------------------------------------------------------------------------------------
    @staticmethod
    def _field(entry, key):
        fields = entry["fields"]
        if key not in fields:
            fields[key] = 1 + max(fields.values(), default=0)
        return fields[key]

    @staticmethod
    def _node(entry, key):
        nodes = entry["nodes"]
        if key not in nodes:
            nodes[key] = 1 + max(nodes.values(), default=0)
        return nodes[key]

    def cta_id(self, screen):
        return f"{self.page_key(screen)}-cta"

    def field_id(self, screen, key):
        entry = self._entry(screen)
        return f"{entry['key']}-f{self._field(entry, key)}"

    def node_id(self, screen, key):
        entry = self._entry(screen)
        return f"{entry['key']}-n{self._node(entry, key)}"

    def option_id(self, screen, key, j):
        return f"{self.field_id(screen, key)}-o{j}"

    def test_ids(self, screen, composition=None):
        """The screen's target map. With a composition, `byNode` maps each node id to the test id codegen emits,
        and `fieldOf` maps controlled nodes to their field key (the App form-state key is the field test id)."""
        entry = self._entry(screen)
        out = {"page": entry["pageId"], "key": entry["key"], "cta": self.cta_id(screen),
               "fields": {key: f"{entry['key']}-f{n}" for key, n in entry["fields"].items()},
               "nodes": {key: f"{entry['key']}-n{n}" for key, n in entry["nodes"].items()}}
        if composition is None:
            return out
        by_node, field_of, seen, cta = {}, {}, {}, None
        for _, node, _ in walk(composition):
            asset_id = node["asset"]
            seen[asset_id] = seen.get(asset_id, 0) + 1
            occurrence = seen[asset_id]
            adapter = _adapter(self.k, asset_id)
            click = (node.get("on") or {}).get("click")
            if adapter in VALUE_FIELD:
                key = field_key(self.k, asset_id, occurrence)
                field_of[node["id"]] = key
                by_node[node["id"]] = self.field_id(screen, key)
            elif cta is None and adapter == "action" and click in ("next", "finish"):
                cta = node["id"]
                by_node[node["id"]] = self.cta_id(screen)
            else:
                by_node[node["id"]] = self.node_id(screen, node_key(asset_id, occurrence))
        out.update(byNode=by_node, fieldOf=field_of, ctaNode=cta,
                   fields={key: f"{entry['key']}-f{n}" for key, n in entry["fields"].items()},
                   nodes={key: f"{entry['key']}-n{n}" for key, n in entry["nodes"].items()})
        return out


# ---- customer convention (E17) -----------------------------------------------------------------------------------

CONVENTION_KEYS = frozenset({"schemaVersion", "screenIdPattern", "area", "pathTemplate", "statePathTemplate",
                             "surfaceSuffix", "typeCodes", "meta"})
META_KEYS = ("sid", "type", "dver", "status", "level1", "level2", "level3")
SURFACES = ("page", "bottom-sheet", "full-popup", "layer", "tab")
_PATH = re.compile(r"[A-Za-z0-9_./-]+\.tsx\Z")
MAX_PATH_BYTES, MAX_NUM = 200, 9999


class Convention:
    def __init__(self, data):
        if not isinstance(data, dict) or set(data) != CONVENTION_KEYS or data["schemaVersion"] != 1:
            raise ValueError("Invalid convention")
        try:
            self.pattern = re.compile(data["screenIdPattern"])
        except (re.error, TypeError) as error:
            raise ValueError("Invalid convention screenIdPattern") from error
        if {"area", "num"} - set(self.pattern.groupindex):
            raise ValueError("screenIdPattern needs the area and num groups")
        if (not isinstance(data["area"], str) or not all(isinstance(data[key], str) for key in
                                                        ("pathTemplate", "statePathTemplate"))
                or not isinstance(data["surfaceSuffix"], dict) or set(data["surfaceSuffix"]) != set(SURFACES)
                or not isinstance(data["typeCodes"], dict) or set(data["typeCodes"]) != set(SURFACES)
                or not all(isinstance(v, str) for v in (*data["surfaceSuffix"].values(), *data["typeCodes"].values()))
                or list(data["meta"]) != list(META_KEYS)):
            raise ValueError("Invalid convention")
        self.data = copy.deepcopy(data)
        self.area = data["area"]
        if self.pattern.fullmatch(f"{self.area}-0001") is None:
            raise ValueError("The convention area does not produce valid screen ids")

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def sid(self, num):
        if type(num) is not int or not 1 <= num <= MAX_NUM:
            raise ValueError("screen-id-capacity")
        sid = f"{self.area}-{num:04d}"
        if self.pattern.fullmatch(sid) is None:
            raise ValueError("screen id does not match the convention")
        return sid

    def _match(self, sid):
        m = self.pattern.fullmatch(sid) if isinstance(sid, str) else None
        if m is None:
            raise ValueError("screen id does not match the convention pattern")
        return m

    def area_of(self, sid):
        return self._match(sid)["area"]

    def number(self, sid):
        return int(self._match(sid)["num"])

    def path(self, sid, surface, state="default"):
        m = self._match(sid)
        if surface not in self.data["surfaceSuffix"]:
            raise ValueError("Unknown surface")
        if state not in STATES:
            raise ValueError("Unknown state")
        template = self.data["pathTemplate"] if state == "default" else self.data["statePathTemplate"]
        try:
            raw = template.format(area=m["area"], screenId=sid, suffix=self.data["surfaceSuffix"][surface], state=state)
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError("Invalid convention path template") from error
        normalized = posixpath.normpath(unicodedata.normalize("NFC", raw))
        parts = normalized.split("/")
        if raw.startswith("/") or normalized.startswith("/") or ".." in parts or normalized in ("", "."):
            raise ValueError("convention path escapes the project")
        if (normalized != unicodedata.normalize("NFC", normalized) or len(normalized.encode("utf-8")) > MAX_PATH_BYTES
                or not _PATH.fullmatch(normalized)):
            raise ValueError("convention path shape")
        return normalized

    def meta(self, k, registry, flow, screen, state, composition, *, dver="1", status="draft"):
        """Page meta emitted by E12 as `export const meta = {...} as const;` (compiled, tested, approved)."""
        procedure = k.procedures.get(flow["procedureId"], {}).get("title", "")
        return {"sid": registry.sid_for(screen), "type": self.data["typeCodes"][composition.get("surface", "page")],
                "dver": str(dver), "status": str(status), "level1": procedure,
                "level2": k.screens[screen]["title"], "level3": state}


def layout(convention, registry, approved):
    """`approved` maps `(screen, state)` to the selected composition. -> `[{source, target, sid, state, screen}]`."""
    out, targets = [], set()
    for (screen, state), composition in sorted(approved.items()):
        try:
            sid = registry.sid_for(screen)
        except KeyError as error:
            raise ValueError("screen-id-unassigned") from error
        target = convention.path(sid, composition.get("surface", "page"), state)
        if target in targets:
            raise ValueError("duplicate-handoff-path")
        targets.add(target)
        out.append({"source": f"project/src/pages/{registry.state_page_id(screen, state)}.tsx", "target": target,
                    "sid": sid, "state": state, "screen": screen})
    return out
