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


def candidate_graph(candidates, *, project_id, source_ref, analysis_coverage=None, unresolved=()):
    scope = {"kind": "project", "projectId": project_id}
    nodes = [schema.seal({"id": c["id"], "scope": scope, "type": c["level"],
                          "title": " + ".join(i.split("#")[1] for i in c["identities"])[:300], "revision": 1,
                          "sourceRefs": [source_ref], "provenance": "parser-extracted", "reviewState": "candidate",
                          "tombstone": False,
                          "properties": {"description": f"반복 {c['support']}회: " + ", ".join(c["usage"][:20])[:7900]}})
             for c in candidates[:200]]
    cov = analysis_coverage or {}
    return {"nodes": nodes, "edges": [],
            "coverage": {"complete": False, "scope": "observed-static-references", "truncated": bool(cov.get("truncated")),
                         "unknown": ["runtime-composition-not-observed"] +
                                    (["unresolved-references"] if cov.get("unresolvedObservations") or unresolved else [])}}
