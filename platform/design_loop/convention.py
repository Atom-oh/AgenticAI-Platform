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

The customer convention and screen-ID assignment (`assign`, `sid_for`, `Convention.path`) are added in E17.
"""
from __future__ import annotations

import copy
import re

from workspace.ontology_schema import digest

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
    def __init__(self, k, state=None):
        self.k = k
        self.state = copy.deepcopy(state) if state else {"schemaVersion": 1, "pages": {}}
        if not isinstance(self.state.get("pages"), dict):
            raise ValueError("Invalid registry state")

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
