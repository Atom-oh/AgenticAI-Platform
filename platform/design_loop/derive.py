"""Derive reusable composition candidates from statically analyzed screen code (REQUIREMENTS O-03)."""
from __future__ import annotations

import copy
import re
from collections import defaultdict

from workspace import ontology_schema as schema

RESOLVED = {"approved-package", "resolved-local"}
_PACKAGE = re.compile(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+(?:/[a-zA-Z0-9._/-]+)?\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


# --- Resolver authority (review round 8, AB2) ---------------------------------------------------------------

def resolver_profile(profile, registry):
    """Validate a server-configured resolver profile `{id, revision, hash, aliases, packages}`.

    Every package entry must equal the platform package registry entry (name, version, sha256), and `hash`
    must be the canonical digest of the other fields. The profile is administered IAM-only; this check confers
    no authority by itself."""
    schema._fields(profile, {"id", "revision", "hash", "aliases", "packages"})
    schema._identifier(profile["id"])
    schema._text(profile["revision"], 128)
    body = {key: profile[key] for key in ("id", "revision", "aliases", "packages")}
    if not isinstance(profile["hash"], str) or profile["hash"] != schema.digest(body):
        raise ValueError("Resolver profile hash does not bind its contents")
    aliases, packages = profile["aliases"], profile["packages"]
    if not isinstance(aliases, dict) or len(aliases) > 30 or not isinstance(packages, dict) or len(packages) > 30:
        raise ValueError("Invalid resolver profile")
    for alias, target in aliases.items():
        schema._text(alias, 150)
        schema._text(target, 500)
    if not isinstance(registry, dict):
        raise ValueError("A platform package registry is required")
    for name, spec in packages.items():
        schema._fields(spec, {"version", "sha256"})
        if (not _PACKAGE.fullmatch(name) or not isinstance(spec["version"], str) or not spec["version"]
                or not isinstance(spec["sha256"], str) or not _HASH.fullmatch(spec["sha256"])):
            raise ValueError("Approved package identity is required")
        registered = registry.get(name)
        if not isinstance(registered, dict) or {k: registered.get(k) for k in ("version", "sha256")} != spec:
            raise ValueError("Resolver package is not the registered platform package")
    return copy.deepcopy(profile)


def analysis_request(caller, profile):
    """Build the analyzer request. The resolver comes only from the frozen profile; a caller resolver is rejected."""
    schema._fields(caller, {"files"})
    files = caller["files"]
    if not isinstance(files, list):
        raise ValueError("Analysis files are required")
    for item in files:
        schema._fields(item, {"path", "kind", "sha256"}, {"text"})
    return {"schemaVersion": 1, "files": copy.deepcopy(files),
            "resolver": {"aliases": copy.deepcopy(profile["aliases"]), "packages": copy.deepcopy(profile["packages"]),
                         "jsonAssetFields": []}}


def _counted(res, profile):
    status = res.get("status")
    if status == "resolved-local":
        return bool(res.get("targetPath"))
    if status == "approved-package":
        spec = (profile or {}).get("packages", {}).get(res.get("package"))
        return spec is not None and spec == {"version": res.get("version"), "sha256": res.get("sha256")}
    return False


def _uses(analysis, profile, resolved):
    for ref in analysis.get("references", []):
        if ref.get("kind") != "jsx-use":
            continue
        res = ref.get("resolution") or {}
        if _counted(res, profile) is resolved:
            yield ref, res


def jsx_sequences(analysis, *, profile=None):
    """Resolved JSX uses per file in source order. `approved-package` counts only for a frozen-profile package."""
    per = defaultdict(list)
    for ref, res in _uses(analysis, profile, True):
        origin = res.get("package") or res.get("targetPath")
        per[ref["path"]].append({"identity": f"{origin}#{ref.get('symbol')}", "symbol": ref.get("symbol"),
                                 "localName": ref.get("localName"), "line": ref["line"], "column": ref["column"]})
    return {p: sorted(v, key=lambda u: (u["line"], u["column"])) for p, v in per.items()}


def unresolved_uses(analysis, *, profile=None):
    """JSX uses that do not count as resolved, reported separately from the sequences."""
    return [{"path": ref.get("path"), "symbol": ref.get("symbol"), "line": ref.get("line"), "column": ref.get("column"),
             "status": res.get("status"), "reason": res.get("reason") or "not-in-resolver-profile"}
            for ref, res in _uses(analysis, profile, False)]


def mine(sequences, *, min_len=2, max_len=5, min_support=2, exclude=frozenset()):
    usage = defaultdict(set)
    for path, seq in sequences.items():
        ids = [u["identity"] for u in seq]
        for n in range(min_len, max_len + 1):
            for i in range(len(ids) - n + 1):
                gram = tuple(ids[i:i + n])
                if not set(gram) & exclude:
                    usage[gram].add(path)
    kept = {g: u for g, u in usage.items() if len(u) >= min_support}
    maximal = {g: u for g, u in kept.items()
               if not any(len(o) > len(g) and kept[o] == u and any(o[i:i + len(g)] == g for i in range(len(o) - len(g) + 1))
                          for o in kept)}
    out = [{"id": "cand-" + schema.digest(list(g))[:24], "level": "Molecule" if len(g) <= 3 else "Organism",
            "identities": list(g), "support": len(u), "usage": sorted(u), "score": len(u) * len(g)}
           for g, u in maximal.items()]
    return sorted(out, key=lambda c: (-c["score"], c["identities"]))


# --- PageTemplate reverse-derivation (O-03; review round 19, AM1) --------------------------------------------

MAX_TEMPLATE_SLOTS = 12          # the uxModel slot limit (Task E3)


class Mined(list):
    """Template candidates plus the mining coverage (capacity notes); still a plain list of candidates."""

    def __init__(self, items=(), coverage=None):
        super().__init__(items)
        self.coverage = coverage or {"unknown": [], "notes": []}


def _identity(ref):
    res = ref.get("resolution") or {}
    return f"{res.get('package') or res.get('targetPath')}#{ref.get('symbol')}"


def page_structures(analysis, *, profile=None):
    """`{path: {root, regions}}` for files whose single root JSX element resolves; regions are its direct children.

    A direct child that does not count as resolved stays `None` (never guessed). Truncated files, files without
    analyzer nesting evidence and files with several root JSX elements are not structures (fail closed)."""
    truncated = set((analysis.get("coverage") or {}).get("truncatedFiles", []))
    per = defaultdict(list)
    for ref in analysis.get("references", []):
        if ref.get("kind") == "jsx-use":
            per[ref["path"]].append(ref)
    out = {}
    for path, uses in per.items():
        if path in truncated or any(type(u.get("depth")) is not int or "parent" not in u for u in uses):
            continue
        roots = [i for i, u in enumerate(uses) if u["depth"] == 0]
        if len(roots) != 1 or not _counted(uses[roots[0]].get("resolution") or {}, profile):
            continue
        children = sorted((u for u in uses if u["parent"] == roots[0] and u["depth"] == 1),
                          key=lambda u: (u["line"], u["column"]))
        out[path] = {"root": _identity(uses[roots[0]]),
                     "regions": [_identity(u) if _counted(u.get("resolution") or {}, profile) else None for u in children]}
    return dict(sorted(out.items()))


def mine_templates(structures, *, min_support=2):
    """Group structures by `(root, len(regions))`, aligning regions position by position."""
    groups = defaultdict(list)
    for path, value in structures.items():
        if value["regions"]:
            groups[(value["root"], len(value["regions"]))].append(path)
    out, coverage = [], {"unknown": [], "notes": []}
    for (root, count), paths in sorted(groups.items()):
        if len(paths) < min_support:
            continue
        if count > MAX_TEMPLATE_SLOTS:
            if "template-capacity" not in coverage["unknown"]:
                coverage["unknown"].append("template-capacity")
            coverage["notes"].append({"reason": "template-capacity", "regions": count, "files": len(paths)})
            continue
        regions = [sorted({structures[p]["regions"][i] for p in paths if structures[p]["regions"][i] is not None})
                   for i in range(count)]
        unresolved = [i + 1 for i in range(count) if any(structures[p]["regions"][i] is None for p in paths)]
        out.append({"id": "tpl-" + schema.digest([root, regions])[:24], "level": "PageTemplate", "root": root,
                    "regions": regions, "unresolvedPositions": unresolved,
                    "identities": sorted({i for position in regions for i in position}),
                    "support": len(paths), "usage": sorted(paths), "score": len(paths) * count})
    return Mined(sorted(out, key=lambda c: (-c["score"], c["id"])), coverage)


def resolve_identities(identities, k, *, local=None):
    """Map analyzer identities to ontology node ids: an existing asset whose `uxModel.layers.code`
    `{importPath, exportName}` matches exactly, or a same-run mined candidate id given in `local`.
    Zero or several matches leave the identity unmapped; nothing is guessed (review round 20, AN1)."""
    index = defaultdict(set)
    for asset_id, asset in (getattr(k, "assets", None) or {}).items():
        code = asset.get("code") or {}
        if isinstance(code.get("importPath"), str) and isinstance(code.get("exportName"), str) \
                and isinstance(asset_id, str) and schema._ID.fullmatch(asset_id):
            index[(code["importPath"], code["exportName"])].add(asset_id)
    local = local or {}
    out = {}
    for identity in sorted(i for i in identities if isinstance(i, str)):
        if "#" not in identity:
            continue
        origin, symbol = identity.rsplit("#", 1)
        found = set(index.get((origin, symbol), set()))
        if identity in local and isinstance(local[identity], str) and schema._ID.fullmatch(local[identity]):
            found.add(local[identity])
        if len(found) == 1:
            out[identity] = found.pop()
    return out


def _template_node(c, *, scope, source_ref, identities, screens):
    from workspace.ontology_ux import validate_ux_model
    notes, unmapped, slots = [], 0, {}
    for position, alternatives in enumerate(c["regions"], 1):
        allowed = sorted({identities[i] for i in alternatives if i in identities})
        unmapped += sum(1 for i in alternatives if i not in identities) + (position in c.get("unresolvedPositions", []))
        if allowed:
            slots[f"slot{position}"] = {"required": True, "allowed": allowed}
        else:
            notes.append(f"unmapped region at position {position}")
    if not slots:
        return None, []
    origin, symbol = c["root"].rsplit("#", 1)
    ux = {"slots": slots}
    code = {"importPath": origin, "exportName": symbol, "childrenProp": None,
            "props": {name: {"type": "node", "required": True} for name in slots}}
    try:
        ux = validate_ux_model({**ux, "layers": {"code": code}}, "PageTemplate")
    except ValueError:
        notes.append("root code layer not representable")
    validate_ux_model(ux, "PageTemplate")
    registry = screens or {}
    usages = [registry[p] for p in c["usage"] if p in registry]
    usage_ids = [u if isinstance(u, str) else u["id"] for u in usages]
    unmapped_files = [p for p in c["usage"] if p not in registry]
    text = f"반복 {c['support']}회: " + ", ".join(c["usage"][:20]) + f"; unmapped: {unmapped}"
    if notes:
        text += "; " + "; ".join(notes)
    if unmapped_files:
        text += "; unmapped files: " + ", ".join(unmapped_files[:20])
    refs = [{**source_ref, "location": {"path": p, "exportName": symbol}} for p in c["usage"][:schema.MAX_REFS]]
    node = schema.seal({"id": c["id"], "scope": scope, "type": "PageTemplate",
                        "title": f"{symbol} 템플릿 · 영역 {len(c['regions'])}개"[:300], "revision": 1,
                        "sourceRefs": refs, "provenance": "parser-extracted", "reviewState": "candidate",
                        "tombstone": False,
                        "properties": {"uxModel": ux, "description": text[:8000],
                                       **({"usageIds": sorted(set(usage_ids))[:100]} if usage_ids else {})}})
    edges = []
    for usage in usages:
        screen = {"id": usage, "revision": 1} if isinstance(usage, str) else {"id": usage["id"], "revision": usage["revision"]}
        edges.append(schema.seal({"id": "use-" + schema.digest([screen["id"], c["id"]])[:24], "type": "COMPOSES",
                                  "src": screen, "dst": {"id": c["id"], "revision": 1}, "sourceRefs": [source_ref],
                                  "provenance": "parser-extracted", "reviewState": "candidate", "tombstone": False}))
    return node, edges


def candidate_graph(candidates, *, project_id, source_ref, analysis_coverage=None, unresolved=(), identities=None,
                    screens=None, mining_coverage=None):
    """Candidate nodes for mined compositions. PageTemplate candidates need `identities` from
    `resolve_identities`; `screens` maps analyzed file paths to Screen node ids (the collection's screen registry)."""
    scope = {"kind": "project", "projectId": project_id}
    nodes, edges, unknown = [], [], ["runtime-composition-not-observed"]
    for c in list(candidates)[:200]:
        if c["level"] == "PageTemplate":
            node, uses = _template_node(c, scope=scope, source_ref=source_ref, identities=identities or {},
                                        screens=screens)
            if node is None:
                if "unmapped-template-structure" not in unknown:
                    unknown.append("unmapped-template-structure")
                continue
            nodes.append(node)
            edges += uses
            continue
        nodes.append(schema.seal({"id": c["id"], "scope": scope, "type": c["level"],
                                  "title": " + ".join(i.split("#")[1] for i in c["identities"])[:300], "revision": 1,
                                  "sourceRefs": [source_ref], "provenance": "parser-extracted", "reviewState": "candidate",
                                  "tombstone": False,
                                  "properties": {"description": f"반복 {c['support']}회: " + ", ".join(c["usage"][:20])[:7900]}}))
    cov = analysis_coverage or {}
    mining = mining_coverage or getattr(candidates, "coverage", None) or {}
    if cov.get("unresolvedObservations") or unresolved:
        unknown.append("unresolved-references")
    unknown += [reason for reason in mining.get("unknown", []) if reason not in unknown]
    result = {"nodes": nodes, "edges": edges,
              "coverage": {"complete": False, "scope": "observed-static-references", "truncated": bool(cov.get("truncated")),
                           "unknown": unknown}}
    if mining.get("notes"):
        result["notes"] = list(mining["notes"])
    return result
