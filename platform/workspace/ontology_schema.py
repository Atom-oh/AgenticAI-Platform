"""Closed, deterministic contracts for the canonical project ontology.

These validators confer no authority. The publishing service resolves every
source and performs membership, review-role and transactional version checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata

SCHEMA_VERSION = 1
LEVELS = ("Foundation", "Atom", "Molecule", "Organism", "Pattern",
          "PageTemplate", "Screen", "Procedure")
NODE_TYPES = frozenset((*LEVELS, "Component", "Product", "PolicyRule", "CodeFile",
                        "CodeSymbol", "Asset", "Test", "Team", "API", "Document", "Skill"))
SOURCE_KINDS = frozenset({"asset", "product-guideline", "workbench-document",
                         "document-revision", "package", "published-asset",
                         "ux-contract", "run-round"})
PROVENANCE = frozenset({"parser-extracted", "declared", "model-inferred", "verified-build"})
REVIEW_STATES = frozenset({"candidate", "reviewed", "approved", "rejected", "deprecated"})
DEPENDENCIES = frozenset({"COMPOSES", "USES", "IMPLEMENTS", "IMPORTS", "REFERENCES", "GOVERNED_BY"})
EDGE_TYPES = DEPENDENCIES | {"PART_OF", "NEXT", "DERIVED_FROM", "OWNED_BY"}
FOUNDATION_SUBTYPES = frozenset({"color", "typography", "spacing", "grid", "elevation",
                               "icon", "graphic", "motion"})
MAX_NODES, MAX_EDGES, MAX_REFS = 500, 1000, 20
DESIGN_PROPERTIES = {"componentName", "packageName", "sourcePath", "description",
                     "slots", "usageIds", "legacyType", "legacyId", "pageId", "procedureId", "uxModel"}
PROPERTIES = {
    **{kind: DESIGN_PROPERTIES for kind in LEVELS[1:]},
    "Component": DESIGN_PROPERTIES,
    "Foundation": {"name", "path", "mime", "token", "width", "height", "normalizedImageHash", "uxModel"},
    "CodeFile": {"path", "language", "collectionId", "fileHash", "analyzerHash"},
    "CodeSymbol": {"path", "exportName", "fileId", "kind"},
    "Asset": {"path", "mime", "bytes", "width", "height", "normalizedImageHash", "classification"},
    "Product": {"productId", "guidelineId", "description"},
    "PolicyRule": {"ruleId", "statement", "required", "guidelineId", "severity", "citation", "extraction", "appliesWhen"},
    "Test": {"testId", "kind", "status", "sourceHash", "receiptId"},
    "Team": {"teamId", "role"},
    "API": {"apiId", "method", "route", "description"},
    "Document": {"documentId", "description"},
    "Skill": {"skillId", "description"},
}
PROPERTIES["Pattern"] = DESIGN_PROPERTIES | {"usageBindings"}
# Screen code is generated and procedure semantics live in NEXT edges: neither carries a uxModel.
PROPERTIES["Screen"] = PROPERTIES["Screen"] - {"uxModel"}
PROPERTIES["Procedure"] = PROPERTIES["Procedure"] - {"uxModel"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _json_value(value, depth=0):
    if depth > 14:
        raise ValueError("Ontology metadata nesting exceeds the contract")
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= 2**53 - 1:
        return
    if isinstance(value, str):
        value.encode("utf-8")  # Reject lone surrogates instead of changing bytes.
        return
    if isinstance(value, list) and len(value) <= 1000:
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 1000 and all(isinstance(k, str) for k in value):
        for key, item in value.items():
            key.encode("utf-8")
            _json_value(item, depth + 1)
        return
    raise ValueError("Ontology metadata must use bounded canonical JSON values")


def canonical(value):
    _json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identity(namespace, *parts):
    if not _ID.fullmatch(namespace) or len(namespace) > 32:
        raise ValueError("Invalid ontology namespace")
    return namespace + "-" + digest(list(parts))[:48]


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Ontology fields do not match the declared schema")


def _text(value, maximum, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or not value.strip() and not empty
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)):
        raise ValueError("Invalid ontology text")
    value.encode("utf-8")
    return value


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Invalid ontology identifier")
    return value


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("Invalid ontology hash")
    return value


def _revision(value):
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        raise ValueError("Ontology revisions must be positive integers")
    return value


def source_ref(value):
    _fields(value, {"sourceKind", "sourceId", "revision", "sha256", "audienceRevision"}, {"location", "allowedRoles"})
    if value["sourceKind"] not in SOURCE_KINDS:
        raise ValueError("Unknown ontology source kind")
    _identifier(value["sourceId"])
    _text(value["revision"], 128)
    _text(value["audienceRevision"], 128)
    _hash(value["sha256"])
    if "allowedRoles" in value and (not isinstance(value["allowedRoles"], list) or not value["allowedRoles"]
            or any(role not in {"owner", "planner", "designer", "developer"} for role in value["allowedRoles"])):
        raise ValueError("Invalid bound source audience")
    location = value.get("location")
    if "location" in value:
        _fields(location, set(), {"path", "originalPath", "exportName", "line", "column", "page", "round", "region", "documentId"})
        if "documentId" in location and value["sourceKind"] != "workbench-document":
            raise ValueError("A location documentId applies only to workbench documents")
        if "path" in location:
            file = _text(location["path"], 500)
            if (file != unicodedata.normalize("NFC", file) or file.startswith("/")
                    or "\\" in file or any(part in {"", ".", ".."} for part in file.split("/"))):
                raise ValueError("Invalid source location path")
        for key in ("exportName", "documentId"):
            if key in location:
                _text(location[key], 160)
        if "originalPath" in location:
            _text(location["originalPath"], 500)
        for key in ("line", "column", "page", "round"):
            if key in location and (type(location[key]) is not int or location[key] < (0 if key == "column" else 1)):
                raise ValueError("Invalid source position")
        if "region" in location:
            region = location["region"]
            _fields(region, {"left", "top", "width", "height", "normalizedImageHash"})
            _hash(region["normalizedImageHash"])
            for key in ("left", "top", "width", "height"):
                if type(region[key]) is not int or not 0 <= region[key] <= 8192:
                    raise ValueError("Invalid image region")
            if not region["width"] or not region["height"]:
                raise ValueError("Image region must have positive dimensions")
    return copy.deepcopy(value)


def _refs(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_REFS:
        raise ValueError("Exact source references are required")
    refs = [source_ref(value) for value in values]
    if len({digest(ref) for ref in refs}) != len(refs):
        raise ValueError("Duplicate source reference")
    return refs


def scope(value):
    if not isinstance(value, dict):
        raise ValueError("A canonical scope is required")
    key = "projectId" if value.get("kind") == "project" else "publicationId"
    _fields(value, {"kind", key})
    if value["kind"] not in {"project", "published"}:
        raise ValueError("Invalid ontology scope")
    _identifier(value[key])
    return copy.deepcopy(value)


def seal(value):
    result = copy.deepcopy(value)
    result.pop("contentHash", None)
    result["contentHash"] = digest(result)
    return result


def validate_node(value):
    _fields(value, {"id", "scope", "type", "title", "revision", "contentHash", "sourceRefs",
                    "provenance", "reviewState", "tombstone"}, {"subtype", "properties", "aliases"})
    _identifier(value["id"])
    scope(value["scope"])
    if value["type"] not in NODE_TYPES:
        raise ValueError("Unknown ontology node type")
    if value["type"] == "Foundation" and value.get("subtype") not in FOUNDATION_SUBTYPES:
        raise ValueError("Foundation subtype is required")
    if "subtype" in value:
        _text(value["subtype"], 80)
    _text(value["title"], 300)
    _revision(value["revision"])
    _refs(value["sourceRefs"])
    if value["provenance"] not in PROVENANCE or value["reviewState"] not in REVIEW_STATES or type(value["tombstone"]) is not bool:
        raise ValueError("Invalid ontology provenance or lifecycle")
    properties = value.get("properties", {})
    if not isinstance(properties, dict) or len(canonical(properties)) > 16000:
        raise ValueError("Ontology properties exceed the contract")
    if properties.keys() - PROPERTIES[value["type"]]:
        raise ValueError("Unknown typed node properties")
    required_properties = {
        "CodeFile": {"path", "language"}, "CodeSymbol": {"path", "exportName", "fileId"},
        "Asset": {"path", "mime", "bytes"}, "Product": {"productId"},
        "PolicyRule": {"ruleId"}, "Team": {"teamId"}, "Test": {"testId"},
        "API": {"apiId"}, "Document": {"documentId"}, "Skill": {"skillId"},
    }
    if required_properties.get(value["type"], set()) - properties.keys():
        raise ValueError("Required typed node properties are missing")
    for key in required_properties.get(value["type"], set()) - {"bytes"}:
        _text(properties[key], 500)
    if "bytes" in properties and (type(properties["bytes"]) is not int or properties["bytes"] < 0):
        raise ValueError("Invalid asset size")
    for key in ("width", "height"):
        if key in properties and (type(properties[key]) is not int or not 1 <= properties[key] <= 8192):
            raise ValueError("Invalid image dimension")
    for key in ("usageIds", "slots"):
        if key in properties and (not isinstance(properties[key], list) or len(properties[key]) > 100
                                  or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in properties[key])):
            raise ValueError("Invalid design references")
    if "required" in properties and type(properties["required"]) is not bool:
        raise ValueError("Invalid policy requirement flag")
    if "usageBindings" in properties:
        bindings = properties["usageBindings"]
        if not isinstance(bindings, list) or len(bindings) > 20:
            raise ValueError("Invalid pattern approval bindings")
        for binding in bindings:
            _fields(binding, {"id", "revision", "contentHash"})
            _identifier(binding["id"])
            _revision(binding["revision"])
            _hash(binding["contentHash"])
    if value["type"] == "Pattern" and value["reviewState"] == "approved" and not value["tombstone"]:
        bindings = properties.get("usageBindings", [])
        identifiers = {binding["id"] for binding in bindings}
        if len(identifiers) < 2 or len(identifiers) != len(bindings) or identifiers != set(properties.get("usageIds", [])):
            raise ValueError("Approved pattern usages require exact revision/hash bindings")
    hashes = {"fileHash", "analyzerHash", "normalizedImageHash", "sourceHash"}
    for key in properties.keys() & hashes:
        _hash(properties[key])
    for key in properties.keys() - hashes - {"bytes", "width", "height", "usageIds", "slots", "required",
                                             "usageBindings", "uxModel", "citation", "extraction"}:
        _text(properties[key], 8000)
    aliases = value.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 20:
        raise ValueError("Invalid namespace mappings")
    for alias in aliases:
        _fields(alias, {"namespace", "value"})
        _text(alias["namespace"], 80)
        _text(alias["value"], 500)
    if "uxModel" in properties:
        from workspace.ontology_ux import validate_ux_model
        validate_ux_model(properties["uxModel"], value["type"])
    if value["type"] == "PolicyRule":
        if "severity" in properties and properties["severity"] not in {"critical", "major", "minor"}:
            raise ValueError("Invalid policy severity")
        if "citation" in properties:
            _fields(properties["citation"], {"sourceKind", "page", "quote", "derivativeHash"}, {"region", "normalizedImageHash"})
            if "region" in properties["citation"]:      # diagram transcription lineage (review round 9, AC2)
                region = properties["citation"]["region"]
                _fields(region, {"left", "top", "width", "height"})
                if any(type(region[k]) is not int or not 0 <= region[k] <= 8192 for k in region) or not region["width"] or not region["height"]:
                    raise ValueError("Invalid citation region")
                _hash(properties["citation"]["normalizedImageHash"])
            if properties["citation"]["sourceKind"] not in {"document-revision", "product-guideline"}:
                raise ValueError("Policy citations require a business source")
            if type(properties["citation"]["page"]) is not int or properties["citation"]["page"] < 1:
                raise ValueError("Invalid citation page")
            _text(properties["citation"]["quote"], 2000)
            _hash(properties["citation"]["derivativeHash"])
        if "appliesWhen" in properties:
            from workspace.ontology_ux import parse_when
            parse_when(properties["appliesWhen"])
        if "extraction" in properties:
            _fields(properties["extraction"], {"method", "model", "promptVersion", "admissionId"})
            if properties["extraction"]["method"] != "model":
                raise ValueError("Invalid extraction method")
            for key in ("model", "promptVersion", "admissionId"):
                _text(properties["extraction"][key], 200)
    expected = {k: v for k, v in value.items() if k != "contentHash"}
    if _hash(value["contentHash"]) != digest(expected):
        raise ValueError("Node content hash mismatch")
    return copy.deepcopy(value)


def validate_edge(value):
    _fields(value, {"id", "type", "src", "dst", "sourceRefs", "provenance",
                    "reviewState", "tombstone", "contentHash"}, {"properties"})
    _identifier(value["id"])
    if value["type"] not in EDGE_TYPES:
        raise ValueError("Unknown ontology edge type")
    for end in ("src", "dst"):
        _fields(value[end], {"id", "revision"})
        _identifier(value[end]["id"])
        _revision(value[end]["revision"])
    _refs(value["sourceRefs"])
    if value["provenance"] not in PROVENANCE or value["reviewState"] not in REVIEW_STATES or type(value["tombstone"]) is not bool:
        raise ValueError("Invalid ontology edge lifecycle")
    properties = value.get("properties", {})
    if (not isinstance(properties, dict) or properties.keys() - {"conditionId", "condition", "retains", "line", "column",
                                                                "symbol", "resolution", "conditional"}
            or len(canonical(properties)) > 8000):
        raise ValueError("Edge metadata exceeds the contract")
    for key in properties.keys() & {"line", "column"}:
        if type(properties[key]) is not int or properties[key] < (0 if key == "column" else 1):
            raise ValueError("Invalid edge source position")
    if "conditional" in properties and type(properties["conditional"]) is not bool:
        raise ValueError("Invalid conditional dependency")
    for key in properties.keys() - {"line", "column", "conditional"}:
        _text(properties[key], 4000)
    if _hash(value["contentHash"]) != digest({k: v for k, v in value.items() if k != "contentHash"}):
        raise ValueError("Edge content hash mismatch")
    return copy.deepcopy(value)


def validate_graph(value, *, external_nodes=(), diagnostic=False):
    _fields(value, {"schemaVersion", "projectId", "nodes", "edges"}, {"coverage"})
    if value["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError("Unsupported ontology schema")
    _identifier(value["projectId"])
    if len(canonical(value)) > 4_000_000:
        raise ValueError("Ontology snapshot exceeds the byte budget")
    if not isinstance(value["nodes"], list) or len(value["nodes"]) > MAX_NODES or not isinstance(value["edges"], list) or len(value["edges"]) > MAX_EDGES:
        raise ValueError("Ontology graph exceeds the bounded snapshot")
    nodes, owned = {}, set()
    for raw in [*value["nodes"], *external_nodes]:
        node = validate_node(raw)
        if node["id"] in nodes:
            raise ValueError("Conflicting canonical node identity")
        if node["scope"]["kind"] == "project" and node["scope"]["projectId"] != value["projectId"]:
            raise ValueError("A project graph cannot import private nodes from another project")
        nodes[node["id"]] = node
    owned.update(raw["id"] for raw in value["nodes"])
    edges, seen = [], set()
    for raw in value["edges"]:
        edge = validate_edge(raw)
        if edge["id"] in seen:
            raise ValueError("Duplicate edge identity")
        seen.add(edge["id"])
        for end in ("src", "dst"):
            node = nodes.get(edge[end]["id"])
            if node is None or not diagnostic and node["revision"] != edge[end]["revision"]:
                raise ValueError("Edge endpoint revision is missing")
        source, target = nodes[edge["src"]["id"]], nodes[edge["dst"]["id"]]
        if edge["type"] in DEPENDENCIES and "Team" in {source["type"], target["type"]}:
            raise ValueError("Team is an assignment endpoint, not a dependency")
        if edge["type"] == "COMPOSES":
            visual = LEVELS[1:7]  # Foundation dependencies use USES; Procedure is workflow.
            if source["type"] not in visual or target["type"] not in visual or visual.index(source["type"]) <= visual.index(target["type"]):
                raise ValueError("Composition must target a strictly lower visual level")
        if edge["type"] == "OWNED_BY" and target["type"] != "Team":
            raise ValueError("Ownership must terminate at a Team")
        if edge["type"] == "GOVERNED_BY" and target["type"] != "PolicyRule":
            raise ValueError("Policy dependencies require a PolicyRule target")
        if edge["type"] == "NEXT" and (source["type"] != "Screen" or target["type"] != "Screen"):
            raise ValueError("Executable transitions require Screen endpoints")
        edges.append(edge)
    for node in nodes.values():
        if (not diagnostic and node["id"] in owned and node["type"] == "Pattern"
                and node["reviewState"] == "approved" and not node["tombstone"]):
            usages = node.get("properties", {}).get("usageIds", [])
            if not isinstance(usages, list) or len(set(usages)) < 2 or any(
                    value not in nodes or nodes[value]["type"] != "Screen"
                    or nodes[value]["reviewState"] not in {"reviewed", "approved"} for value in usages):
                raise ValueError("Approved patterns need independently reviewed screen usages")
            for binding in node["properties"]["usageBindings"]:
                screen = nodes[binding["id"]]
                if screen["revision"] != binding["revision"] or screen["contentHash"] != binding["contentHash"]:
                    raise ValueError("Approved pattern usage binding is stale")
                if not any(edge["type"] == "REFERENCES" and not edge["tombstone"]
                           and edge["reviewState"] == "approved" and edge["src"]["id"] == node["id"]
                           and edge["dst"]["id"] == screen["id"] for edge in edges):
                    raise ValueError("Approved pattern usage evidence relationship is required")
    coverage = copy.deepcopy(value.get("coverage", {"complete": False, "unknown": ["unmapped-dependencies"],
                                                   "scope": "bounded-project-partition", "truncated": False}))
    _fields(coverage, {"complete", "unknown", "scope", "truncated"})
    if (type(coverage["complete"]) is not bool or type(coverage["truncated"]) is not bool
            or not isinstance(coverage["unknown"], list) or len(coverage["unknown"]) > 100
            or any(not isinstance(reason, str) or len(reason) > 200 for reason in coverage["unknown"])):
        raise ValueError("Invalid coverage evidence")
    _text(coverage["scope"], 100)
    if coverage["complete"] and (coverage["unknown"] or coverage["truncated"]):
        raise ValueError("Incomplete evidence cannot claim complete coverage")
    return {"schemaVersion": SCHEMA_VERSION, "projectId": value["projectId"],
            "nodes": sorted((node for key, node in nodes.items() if key in owned), key=lambda n: n["id"]),
            "edges": sorted(edges, key=lambda e: e["id"]),
            "coverage": coverage}
