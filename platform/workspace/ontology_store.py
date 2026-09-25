"""Canonical project ontology partitions and immutable lookup indexes.

The existing workspace `ontology` kind is the write authority. Workbench and
generation consumers use these snapshots; they do not write competing graphs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
from collections import deque

from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources, authority_identity

CURRENT = "project-current"
MAX_PARTITIONS = 1000
MAX_INDEX_BYTES = 4_000_000


def _bucket(identifier):
    return hashlib.sha256(identifier.encode()).hexdigest()[:2]


def source_identity(ref):
    return authority_identity(ref)


def _references(graph):
    return list({schema.digest(ref): ref for item in [*graph["nodes"], *graph["edges"]]
                 for ref in item["sourceRefs"]}.values())


def _mapping(value):
    result = copy.deepcopy(value)
    for field in ("revision", "contentHash", "reviewState"):
        result.pop(field, None)
    for field in ("usageBindings",):
        result.get("properties", {}).pop(field, None)
    return result


def retain_tombstones(previous, graph):
    """Keep removed identities and dependency witnesses without making them usable."""
    result = copy.deepcopy(graph)
    ids = {node["id"] for node in graph["nodes"]}
    for node in previous.get("nodes", []):
        if node["id"] not in ids:
            result["nodes"].append(node if node["tombstone"] else schema.seal({
                **node, "revision": node["revision"] + 1, "tombstone": True, "reviewState": "deprecated"}))
    ids = {edge["id"] for edge in graph["edges"]}
    for edge in previous.get("edges", []):
        if edge["id"] not in ids:
            result["edges"].append(edge if edge["tombstone"] else schema.seal({
                **edge, "tombstone": True, "reviewState": "deprecated"}))
    if len(result["nodes"]) > schema.MAX_NODES or len(result["edges"]) > schema.MAX_EDGES:
        fail(422, "ontology-history-capacity", "삭제 이력을 포함한 파티션 한도를 초과했습니다. 새 매핑 단위로 분리하세요.")
    return result


class Ontology:
    def __init__(self, context):
        self.ctx, self.storage = context, context.storage
        self.sources = Sources(context, max_records=2500)
        self._parts = {}
        self._indexes = {}
        self._historical_stale = False
        self._visibility = {}
        self._snapshot = None
        self._anchor = None
        self._last_inspection = (0, 0)

    def current(self):
        self.ctx.fresh()
        if self._snapshot is not None:
            return self._snapshot
        row = self.storage.get(self.ctx.owner, "ontology", CURRENT)
        if row is not None and (row.get("projectId") != self.ctx.project_id or row.get("schemaVersion") != 1):
            fail(409, "ontology-manifest-invalid", "프로젝트 온톨로지 기준을 확인하지 못했습니다.")
        return row

    def _key(self, identifier, digest):
        return self.storage.key_for(self.ctx.owner, "ontology", identifier, digest + ".json")

    def _read_json(self, identifier, digest):
        schema._hash(digest)
        key = self._key(identifier, digest)
        info = self.storage.blob_info(key)
        if info["size"] > MAX_INDEX_BYTES or info["sha256"] != digest:
            fail(409, "ontology-integrity", "온톨로지 파일의 크기 또는 해시가 다릅니다.")
        raw = self.storage.get_blob(key, length=MAX_INDEX_BYTES)
        if len(raw) != info["size"] or hashlib.sha256(raw).hexdigest() != digest:
            fail(409, "ontology-integrity", "온톨로지 파일을 검증하지 못했습니다.")
        try:
            value = json.loads(raw)
            if schema.canonical(value) != raw:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            fail(409, "ontology-integrity", "온톨로지 직렬화 형식이 다릅니다.")
        return value

    def _put_json(self, identifier, value):
        raw = schema.canonical(value)
        if len(raw) > MAX_INDEX_BYTES:
            if identifier.startswith("index-"):
                fail(422, "ontology-index-capacity", "연결 인덱스의 저장 한도를 초과했습니다. 운영자에게 인덱스 용량 확장을 요청하세요.")
            fail(422, "ontology-capacity", "온톨로지 파티션을 더 작은 단위로 나누세요.")
        digest = hashlib.sha256(raw).hexdigest()
        self.storage.put_blob_once(self._key(identifier, digest), raw, "application/json")
        return digest

    def _part(self, manifest, partition):
        key = (partition, manifest.get("partitions", {}).get(partition))
        if not key[1]:
            return None
        if key not in self._parts:
            value = self._read_json(partition, key[1])
            if value.get("partitionId") != partition or value.get("graph", {}).get("projectId") != self.ctx.project_id:
                fail(409, "ontology-integrity", "온톨로지 파티션 범위가 다릅니다.")
            if len(value["graph"]["nodes"]) > schema.MAX_NODES or len(value["graph"]["edges"]) > schema.MAX_EDGES:
                fail(409, "ontology-integrity", "온톨로지 파티션의 저장 한도가 올바르지 않습니다.")
            for node in value["graph"]["nodes"]:
                schema.validate_node(node)
            for edge in value["graph"]["edges"]:
                schema.validate_edge(edge)
            self._parts[key] = value
        return self._parts[key]

    def _index(self, manifest, kind, bucket):
        digest = manifest.get("indexes", {}).get(kind, {}).get(bucket)
        if not digest:
            return {}
        key = (kind, bucket, digest)
        if key not in self._indexes:
            value = self._read_json(f"index-{kind}-{bucket}", digest)
            if not isinstance(value, dict):
                fail(409, "ontology-integrity", "온톨로지 인덱스 형식이 다릅니다.")
            self._indexes[key] = value
        return self._indexes[key]

    def _node(self, manifest, identifier):
        location = self._index(manifest, "nodes", _bucket(identifier)).get(identifier)
        if not location:
            return None
        part = self._part(manifest, location["partition"])
        node = next((n for n in part["graph"]["nodes"] if n["id"] == identifier), None) if part else None
        if not node or node["revision"] != location["revision"] or node["contentHash"] != location["contentHash"]:
            fail(409, "ontology-integrity", "온톨로지 노드 인덱스가 원본과 다릅니다.")
        return node

    def _visible(self, refs, *, historical=False):
        from workspace.collaboration import CollaborationError
        key = historical, tuple(sorted(authority_identity(ref) for ref in refs))
        if key in self._visibility:
            return self._visibility[key]
        try:
            if historical:
                for ref in refs:
                    self.sources.authorize(ref)
                    try:
                        self.sources.resolve(ref)
                    except CollaborationError as error:
                        if error.status not in (403, 404, 409):
                            raise
                        self._historical_stale = True
            else:
                self.sources.verify(refs, recheck=False)
            self._visibility[key] = True
            return True
        except CollaborationError as error:
            if error.status in (403, 404, 409):
                self._visibility[key] = False
                return False
            raise

    def _recheck(self, manifest):
        self.sources.recheck()
        current = self.storage.get(self.ctx.owner, "ontology", CURRENT)
        expected = self._anchor if self._snapshot is not None else manifest
        if (current or {}).get("generation") != (expected or {}).get("generation"):
            fail(409, "ontology-changed", "조회 중 온톨로지가 변경되었습니다. 다시 조회하세요.")

    def _visible_node(self, manifest, node, *, historical=False):
        if not node or not self._visible(node["sourceRefs"], historical=historical):
            return False
        if node["type"] == "Pattern" and node["reviewState"] == "approved":
            bindings = node.get("properties", {}).get("usageBindings", [])
            if len({binding["id"] for binding in bindings}) < 2:
                return False
            for binding in bindings:
                screen = self._node(manifest, binding["id"])
                if (not screen or screen["type"] != "Screen" or screen["tombstone"]
                        or screen["revision"] != binding["revision"] or screen["contentHash"] != binding["contentHash"]
                        or screen["reviewState"] not in {"reviewed", "approved"}
                        or not self._visible(screen["sourceRefs"], historical=historical)):
                    return False
        return True

    def authorize_publication(self, name, *, origin="manual"):
        """Check the destination before any paid analysis or other side effect."""
        if self._snapshot is not None:
            fail(409, "ontology-historical-read-only", "과거 스냅샷은 조회 전용입니다.")
        self.sources.max_records = 90
        self.ctx.fresh({"owner", "planner", "designer", "developer"})
        schema._identifier(name)
        if origin not in {"manual", "workbench-import"}:
            raise ValueError("Unknown ontology publication origin")
        if name.startswith("legacy-"):
            if origin != "workbench-import":
                fail(403, "ontology-managed-partition", "기존 지식 가져오기 파티션은 전용 가져오기 경로로만 변경하세요.")
            self.ctx.fresh({"owner"})
        if name.startswith(("product-", "system-", "publication-")):
            fail(403, "ontology-managed-partition", "게시 원본 파티션은 해당 원본의 관리 API에서 변경하세요.")
        current = self.current()
        prior = self._part(current, schema.identity("partition", name)) if current else None
        if prior and prior["createdBy"] != self.ctx.actor and self.ctx.scope["role"] != "owner":
            fail(403, "ontology-owner-required", "기존 매핑 작성자 또는 프로젝트 관리자가 수정할 수 있습니다.")
        return current, prior

    def publish_candidate(self, name, graph, *, expected_generation, request_id, additional_checks=(),
                          _producer="declared", _completion_writes=None, _origin="manual", _replacement_complete=True):
        current, prior = self.authorize_publication(name, origin=_origin)
        schema._identifier(request_id)
        if _producer not in {"declared", "parser-extracted"}:
            raise ValueError("Unsupported trusted ontology producer")
        partition = schema.identity("partition", name)
        request_hash = schema.digest({"name": name, "graph": graph, "expectedGeneration": expected_generation,
                                      "producer": _producer,
                                      **({"origin": _origin, "checks": list(additional_checks)} if _origin != "manual" else {})})
        marker_id = schema.identity("ontology-request", self.ctx.actor, _producer, request_id)
        marker = self.storage.get(self.ctx.owner, "ontology", marker_id)
        if marker:
            if marker.get("requestHash") != request_hash:
                fail(409, "request-changed", "같은 요청 ID의 내용이 달라졌습니다.")
            if "sourceRefs" not in marker:
                fail(409, "ontology-request-evidence", "이전 게시 요청의 원본 근거를 재확인할 수 없습니다. 현재 원본으로 새 요청을 만드세요.")
            self.sources.verify(marker["sourceRefs"])
            for check in additional_checks:
                observed = self.storage.get(check["owner"], check["kind"], check["id"])
                if (observed or {}).get("version") != check["version"]:
                    fail(409, "ontology-source-changed", "가져오기 기준이 조회 중 변경되었습니다.")
            self._recheck(current)
            return {"generation": marker["generation"], "partitionId": partition,
                    "historical": (current or {}).get("generation") != marker["generation"],
                    "identities": marker["identities"]}
        if (current or {}).get("generation") != expected_generation:
            fail(409, "ontology-changed", "온톨로지 기준이 변경되었습니다.")
        schema._fields(graph, {"schemaVersion", "projectId", "nodes", "edges"}, {"coverage"})
        if not isinstance(graph["nodes"], list) or not isinstance(graph["edges"], list):
            fail(400, "ontology-input", "노드와 관계 목록이 필요합니다.")
        if graph["projectId"] != self.ctx.project_id:
            fail(403, "ontology-project-mismatch", "현재 프로젝트에만 매핑을 등록할 수 있습니다.")
        normalized = copy.deepcopy(graph)
        old_nodes = {n["id"]: n for n in prior["graph"]["nodes"]} if prior else {}
        identities = {}
        for value in normalized["nodes"]:
            schema.validate_node(value)
            if value["scope"] != {"kind": "project", "projectId": self.ctx.project_id}:
                fail(403, "ontology-publication-required", "공유 자산은 승인된 게시 경로로만 등록할 수 있습니다.")
            schema._identifier(value.get("id"))
            identities[value["id"]] = value["id"] if value["id"] in old_nodes else schema.identity("node", partition, value["id"])
        for i, value in enumerate(normalized["nodes"]):
            original_id, identifier = value["id"], identities[value["id"]]
            old = old_nodes.get(identifier)
            if old and old["type"] != value["type"]:
                fail(409, "ontology-type-change", "타입 변경은 새 노드와 명시적 이전 매핑으로 등록하세요.")
            location = self._index(current or {}, "nodes", _bucket(identifier)).get(identifier)
            if location and location["partition"] != partition:
                fail(409, "ontology-identity-conflict", "다른 파티션이 소유한 노드 ID입니다.")
            properties = copy.deepcopy(value.get("properties", {}))
            if value["type"] == "Pattern":
                properties.pop("usageBindings", None)
            for field in ("usageIds", "slots"):
                if field in properties:
                    properties[field] = [identities.get(item, item) for item in properties[field]]
            if "fileId" in properties:
                properties["fileId"] = identities.get(properties["fileId"], properties["fileId"])
            if "entryScreenId" in properties:
                properties["entryScreenId"] = identities.get(properties["entryScreenId"], properties["entryScreenId"])
            if "uxModel" in properties:
                from workspace.ontology_ux import rewrite
                properties["uxModel"] = rewrite(properties["uxModel"], identities)
            aliases = list({schema.digest(alias): alias for alias in [
                *(old.get("aliases", []) if old else []), *value.get("aliases", [])]}.values())
            if not old:
                aliases = [*aliases, {"namespace": "partition-local", "value": original_id}]
            candidate = {**value, "id": identifier, "properties": properties, "aliases": aliases,
                         "provenance": _producer, "reviewState": "candidate"}
            normalized["nodes"][i] = (copy.deepcopy(old) if old and _mapping(old) == _mapping(candidate) else
                schema.seal({**candidate, "revision": old["revision"] + 1 if old else 1}))
        own = {n["id"]: n for n in normalized["nodes"]}
        old_edges = {edge["id"]: edge for edge in prior["graph"]["edges"]} if prior else {}
        externals = {}
        managed, retired_proofs = {}, set()
        for index, candidate in enumerate(normalized["nodes"]):
            if candidate["type"] != "Pattern":
                continue
            original = old_nodes.get(candidate["id"], {})
            proofs = {key: edge for key, edge in old_edges.items()
                      if edge["src"]["id"] == candidate["id"] and edge["type"] == "REFERENCES"
                      and key == schema.identity("pattern-usage", candidate["id"], edge["dst"]["id"])}
            bindings = candidate.get("properties", {}).get("usageBindings", [])
            valid = candidate["reviewState"] == "approved" and len(bindings) >= 2
            screens = []
            for binding in bindings:
                screen = own.get(binding["id"]) or self._node(current or {}, binding["id"])
                proof = proofs.get(schema.identity("pattern-usage", candidate["id"], binding["id"]))
                if (not screen or screen["tombstone"] or screen["reviewState"] not in {"reviewed", "approved"}
                        or any(screen[key] != binding[key] for key in ("revision", "contentHash"))
                        or not proof or proof["tombstone"] or proof["reviewState"] != "approved"
                        or not self._visible(screen["sourceRefs"])):
                    valid = False
                elif screen["id"] not in own:
                    screens.append(screen)
            if valid:
                managed.update(proofs)
                externals.update({screen["id"]: screen for screen in screens})
            elif original.get("reviewState") == "approved":
                retired_proofs.update(proofs)
                props = {key: item for key, item in candidate.get("properties", {}).items() if key != "usageBindings"}
                normalized["nodes"][index] = schema.seal({**candidate, "reviewState": "candidate", "properties": props})
                own[candidate["id"]] = normalized["nodes"][index]
        normalized["edges"] = [edge for edge in normalized["edges"] if edge["id"] not in retired_proofs]
        for i, raw in enumerate(normalized["edges"]):
            schema.validate_edge(raw)
            value = copy.deepcopy(raw)
            for end in ("src", "dst"):
                identifier = identities.get(value[end]["id"], value[end]["id"])
                value[end]["id"] = identifier
                if identifier in old_nodes and identifier not in own:
                    fail(409, "ontology-removed-endpoint", "이번 교체에서 삭제하는 노드를 활성 관계의 대상으로 사용할 수 없습니다.")
                target = own.get(identifier) or self._node(current or {}, identifier)
                if not target or target["tombstone"] or not self._visible_node(current or {}, target):
                    fail(409, "ontology-reference-unavailable", "관계 대상의 현재 원본을 확인할 수 없습니다.")
                if identifier not in own:
                    externals[identifier] = target
                value[end]["revision"] = target["revision"]
            identifier = value["id"] if value["id"] in old_edges else schema.identity("edge", partition, value["id"])
            previous = old_edges.get(identifier)
            if previous and (previous["type"] != value["type"] or any(
                    previous[end]["id"] != value[end]["id"] for end in ("src", "dst"))):
                fail(409, "ontology-edge-identity", "관계 종류나 대상을 변경할 때는 새 관계 ID를 사용하세요. 기존 관계는 이력으로 보존됩니다.")
            if identifier in managed:
                proof = managed[identifier]
                if any(value.get(key) != proof.get(key) for key in ("type", "src", "dst", "sourceRefs", "properties")):
                    fail(409, "ontology-managed-evidence", "승인된 사용 근거는 해당 원본 검토를 통해 변경하세요.")
                normalized["edges"][i] = copy.deepcopy(proof)
            else:
                normalized["edges"][i] = schema.seal({**value, "id": identifier, "provenance": _producer, "reviewState": "candidate"})
        supplied = {edge["id"] for edge in normalized["edges"]}
        normalized["edges"].extend(copy.deepcopy(edge) for key, edge in managed.items() if key not in supplied)
        if prior and not _replacement_complete:
            for kind in ("nodes", "edges"):
                preserved = {item["id"] for item in normalized[kind] if not item["tombstone"]}
                previous = {item["id"] for item in prior["graph"][kind] if not item["tombstone"]}
                if not previous <= preserved:
                    fail(422, "legacy-import-incomplete", "변환하지 못한 기존 항목이 있어 이전 매핑을 제거할 수 없습니다.")
        normalized = schema.validate_graph(normalized, external_nodes=list(externals.values()))
        normalized["coverage"] = {**normalized["coverage"], "complete": False,
            "scope": "static-source-unit" if _producer == "parser-extracted" else "declared-project-partition",
            "unknown": sorted(set(normalized["coverage"]["unknown"]) | {"unreviewed-design-mappings"})}
        refs = _references(normalized) + [ref for node in externals.values() for ref in node["sourceRefs"]]
        checks = [*self.sources.verify(list({schema.digest(ref): ref for ref in refs}.values())), *additional_checks]
        normalized = retain_tombstones(prior["graph"] if prior else {}, normalized)
        part = {"partitionId": partition, "name": name,
                "createdBy": prior["createdBy"] if prior and _origin == "manual" else self.ctx.actor,
                "updatedBy": self.ctx.actor, "graph": normalized, "origin": _origin}
        part_hash = self._put_json(partition, part)
        updated = copy.deepcopy(current or {"id": CURRENT, "schemaVersion": 1, "projectId": self.ctx.project_id,
                                            "partitions": {}, "indexes": {}})
        updated["partitions"][partition] = part_hash
        if len(updated["partitions"]) > MAX_PARTITIONS:
            fail(422, "ontology-capacity", "프로젝트 파티션 수 제한을 초과했습니다.")
        self._replace_indexes(updated, current or {}, partition, prior["graph"] if prior else {"nodes": [], "edges": []}, normalized)
        updated["generation"] = schema.digest({"partitions": updated["partitions"], "indexes": updated["indexes"]})
        marker = {"id": marker_id, "projectId": self.ctx.project_id, "requestHash": request_hash,
                  "generation": updated["generation"], "partitionId": partition, "identities": identities,
                  "sourceRefs": list({authority_identity(ref): ref for ref in refs}.values())}
        completion = (_completion_writes(copy.deepcopy(marker)) if _completion_writes else [])
        self.sources.recheck()
        self.ctx.commit([self.ctx.write("ontology", updated, current["version"] if current else None),
                         self.ctx.write("ontology", marker), *completion], checks)
        return {"generation": updated["generation"], "partitionId": partition,
                "nodes": len(normalized["nodes"]), "edges": len(normalized["edges"]), "identities": identities,
                "coverage": normalized.get("coverage", {"complete": False})}

    def _replace_indexes(self, updated, current, partition, old, new):
        changes = {}

        def bucket(kind, identifier):
            key = kind, _bucket(identifier)
            if key not in changes:
                changes[key] = copy.deepcopy(self._index(current, *key))
            return changes[key]

        # Record the complete immutable manifest before retiring a source
        # binding. IDs alone cannot recover its old node/edge revisions.
        retired = {}
        for collection in ("nodes", "edges"):
            replacements = {item["id"]: item for item in new[collection]}
            for item in old[collection]:
                if item["tombstone"]:
                    continue
                replacement = replacements.get(item["id"])
                active_refs = ({source_identity(ref) for ref in replacement["sourceRefs"]}
                               if replacement and not replacement["tombstone"] else set())
                for ref in item["sourceRefs"]:
                    key = source_identity(ref)
                    if key not in active_refs:
                        seed = item["id"] if collection == "nodes" else item["src"]["id"]
                        retired.setdefault(key, set()).add(seed)
        if retired and current.get("generation"):
            snapshot = self._put_json("project-snapshot", current)
            for key, seeds in retired.items():
                history = bucket("source-history", key).setdefault(key, [])
                existing = next((entry for entry in history if entry["hash"] == snapshot), None)
                if existing:
                    existing["nodeIds"] = sorted(set(existing["nodeIds"]) | seeds)
                else:
                    history.append({"hash": snapshot, "generation": current["generation"], "nodeIds": sorted(seeds)})

        for node in old["nodes"]:
            bucket("nodes", node["id"]).pop(node["id"], None)
            for ref in node["sourceRefs"]:
                key = source_identity(ref)
                values = bucket("sources", key).get(key, [])
                bucket("sources", key)[key] = [value for value in values if value != node["id"]]
                history = bucket("historical-sources", key).setdefault(key, [])
                if node["id"] not in history:
                    history.append(node["id"])
                    history.sort()
        for edge in old["edges"]:
            bucket("edges", edge["id"]).pop(edge["id"], None)
            for ref in edge["sourceRefs"]:
                key = source_identity(ref)
                values = bucket("edge-sources", key).get(key, [])
                bucket("edge-sources", key)[key] = [value for value in values if value != edge["id"]]
                history = bucket("historical-sources", key).setdefault(key, [])
                if edge["src"]["id"] not in history:
                    history.append(edge["src"]["id"])
                    history.sort()
            for end in ("src", "dst"):
                key = edge[end]["id"]
                values = bucket("adjacency", key).get(key, [])
                bucket("adjacency", key)[key] = [value for value in values if value != edge["id"]]
        for node in new["nodes"]:
            bucket("nodes", node["id"])[node["id"]] = {
                "partition": partition, "revision": node["revision"], "contentHash": node["contentHash"]}
            for ref in node["sourceRefs"]:
                key = source_identity(ref)
                values = bucket("sources", key).setdefault(key, [])
                if node["id"] not in values:
                    values.append(node["id"])
                    values.sort()
        for edge in new["edges"]:
            existing = bucket("edges", edge["id"]).get(edge["id"])
            if existing and existing["partition"] != partition:
                fail(409, "ontology-edge-conflict", "다른 파티션이 소유한 관계 ID입니다.")
            bucket("edges", edge["id"])[edge["id"]] = {"partition": partition, "contentHash": edge["contentHash"]}
            for ref in edge["sourceRefs"]:
                key = source_identity(ref)
                values = bucket("edge-sources", key).setdefault(key, [])
                if edge["id"] not in values:
                    values.append(edge["id"])
                    values.sort()
            for end in ("src", "dst"):
                key = edge[end]["id"]
                values = bucket("adjacency", key).setdefault(key, [])
                if edge["id"] not in values:
                    values.append(edge["id"])
                    values.sort()
        for (kind, prefix), values in changes.items():
            updated.setdefault("indexes", {}).setdefault(kind, {})[prefix] = self._put_json(
                f"index-{kind}-{prefix}", {k: v for k, v in values.items() if v})

    def read(self, node_ids=None, *, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            fail(400, "ontology-limit", "한 페이지는 1~100개 노드로 조회하세요.")
        current = self.current()
        if not current:
            return {"nodes": [], "edges": [], "generation": None,
                    "coverage": {"complete": False, "unknown": ["not-indexed"]}, "backend": "workspace-project-ontology"}
        position = {"bucket": 0, "offset": 0}
        fingerprint = schema.digest([list(self.sources.authority),
                                     current["generation"], node_ids, limit])
        if cursor:
            schema._identifier(cursor)
            saved = self.storage.get(self.ctx.owner, "ontology_cursor", cursor)
            if not saved or saved.get("fingerprint") != fingerprint or saved.get("expiresAt", 0) <= self.storage.clock():
                fail(409, "ontology-cursor-stale", "조회 범위 또는 권한이 변경되었습니다.")
            position = saved["position"]
        selected, scanned, more = [], 0, False
        if node_ids is not None:
            if not isinstance(node_ids, list) or len(node_ids) > 50 or cursor:
                fail(400, "ontology-selection", "조회할 노드 ID를 확인하세요.")
            for identifier in node_ids:
                schema._identifier(identifier)
                node = self._node(current, identifier)
                if node and not node["tombstone"] and self._visible_node(current, node):
                    selected.append(node)
        else:
            prefixes = [f"{i:02x}" for i in range(256)]
            while position["bucket"] < len(prefixes) and len(selected) < limit and scanned < 250:
                names = sorted(self._index(current, "nodes", prefixes[position["bucket"]]))
                if position["offset"] >= len(names):
                    position = {"bucket": position["bucket"] + 1, "offset": 0}
                    continue
                identifier = names[position["offset"]]
                position["offset"] += 1
                scanned += 1
                node = self._node(current, identifier)
                if node and not node["tombstone"] and self._visible_node(current, node):
                    selected.append(node)
            more = position["bucket"] < len(prefixes)
        selected = list({node["id"]: node for node in selected}.values())
        identifiers = {node["id"] for node in selected}
        revisions = {node["id"]: node["revision"] for node in selected}
        edges = {}
        stale_edges = False
        for identifier in identifiers:
            for edge_id in self._index(current, "adjacency", _bucket(identifier)).get(identifier, []):
                location = self._index(current, "edges", _bucket(edge_id)).get(edge_id)
                if not location:
                    fail(409, "ontology-integrity", "관계 인덱스가 누락되었습니다.")
                part = self._part(current, location["partition"])
                edge = next((e for e in part["graph"]["edges"] if e["id"] == edge_id), None)
                if not edge or edge["contentHash"] != location["contentHash"]:
                    fail(409, "ontology-integrity", "관계 인덱스의 원본이 다릅니다.")
                if (edge["src"]["id"] in identifiers and edge["dst"]["id"] in identifiers and not edge["tombstone"]
                        and self._visible(edge["sourceRefs"])):
                    if any(revisions[edge[end]["id"]] != edge[end]["revision"] for end in ("src", "dst")):
                        stale_edges = True
                    else:
                        edges[edge_id] = edge
        self._recheck(current)
        next_cursor = None
        if more:
            next_cursor = "cursor-" + secrets.token_hex(24)
            self.ctx.commit([self.ctx.write("ontology_cursor", {
                "id": next_cursor, "projectId": self.ctx.project_id, "fingerprint": fingerprint,
                "position": position, "expiresAt": self.storage.clock() + 300000,
                "ttl": self.storage.clock() // 1000 + 300})])
            self._recheck(current)
        return {"schemaVersion": 1, "nodes": selected, "edges": list(edges.values()), "generation": current["generation"],
                "cursor": next_cursor, "backend": "workspace-project-ontology",
                "coverage": {"complete": False, "unknown": ["outside-page-or-inaccessible"] +
                             (["stale-endpoint-revisions"] if stale_edges else []), "truncated": more}}

    def closure(self, node_ids, *, direction="dependencies", max_nodes=50, max_edges=1000, historical=False):
        if (direction not in {"dependencies", "dependents", "both"} or not 1 <= max_nodes <= schema.MAX_NODES
                or not 1 <= max_edges <= schema.MAX_EDGES):
            fail(400, "ontology-scope", "지원되는 관계 조회 범위가 필요합니다.")
        if not isinstance(node_ids, list) or not 1 <= len(node_ids) <= 20:
            fail(400, "ontology-selection", "시작 노드는 1~20개로 선택하세요.")
        for identifier in node_ids:
            schema._identifier(identifier)
        current = self.current()
        if not current:
            fail(409, "ontology-not-indexed", "프로젝트 온톨로지가 아직 등록되지 않았습니다.")
        pending = deque((identifier, 0) for identifier in sorted(set(node_ids)))
        nodes, edges, unknown = {}, {}, {"outside-snapshot-not-certified"}
        impact_seeds = set(node_ids)
        visited, inspected_edges = set(), set()
        while pending:
            identifier, depth = pending.popleft()
            if identifier in visited:
                continue
            if len(visited) >= max_nodes:
                unknown.add("truncated")
                break
            visited.add(identifier)
            node = self._node(current, identifier)
            if node and node["reviewState"] == "rejected":
                unknown.add("rejected-mapping")
                continue
            readable = node and (historical or not node["tombstone"]) and self._visible_node(current, node, historical=historical)
            opaque_seed = historical and identifier in node_ids and not readable
            if not readable and not opaque_seed:
                unknown.add("unmapped-or-inaccessible")
                continue
            if readable:
                nodes[identifier] = node
            else:
                unknown.add("restricted-source-boundary")
            adjacent = self._index(current, "adjacency", _bucket(identifier)).get(identifier, [])
            if depth >= 12:
                if adjacent:
                    unknown.add("truncated")
                continue
            for edge_id in adjacent:
                if edge_id not in inspected_edges:
                    if len(inspected_edges) >= max_edges:
                        unknown.add("truncated")
                        break
                    inspected_edges.add(edge_id)
                loc = self._index(current, "edges", _bucket(edge_id)).get(edge_id)
                if not loc:
                    fail(409, "ontology-integrity", "온톨로지 관계 인덱스가 누락되었습니다.")
                part = self._part(current, loc["partition"])
                edge = next((item for item in part["graph"]["edges"] if item["id"] == edge_id), None)
                if not edge or edge["contentHash"] != loc["contentHash"]:
                    fail(409, "ontology-integrity", "관계 원본과 인덱스가 일치하지 않습니다.")
                if (edge["type"] not in schema.DEPENDENCIES
                        or not historical and (edge["tombstone"] or edge["reviewState"] in {"rejected", "deprecated"})
                        or edge["reviewState"] == "rejected"):
                    continue
                if direction == "dependencies" and edge["src"]["id"] != identifier or (
                        direction == "dependents" and edge["dst"]["id"] != identifier):
                    continue
                other = edge["dst"]["id"] if edge["src"]["id"] == identifier else edge["src"]["id"]
                target = self._node(current, other)
                if (not target or target["reviewState"] == "rejected" or not historical and target["tombstone"]
                        or not self._visible_node(current, target, historical=historical)
                        or not self._visible(edge["sourceRefs"], historical=historical)):
                    unknown.add("unmapped-or-inaccessible")
                    continue
                versions = {identifier: node["revision"] if node else None, other: target["revision"]}
                if any(versions[edge[end]["id"]] != edge[end]["revision"] for end in ("src", "dst")):
                    unknown.add("stale-endpoint-revisions")
                    if not historical:
                        continue
                if not opaque_seed:
                    edges[edge_id] = edge
                else:
                    impact_seeds.add(other)
                pending.append((other, depth + 1))
        edges = {key: edge for key, edge in edges.items() if edge["src"]["id"] in nodes and edge["dst"]["id"] in nodes}
        self._recheck(current)
        if self._historical_stale:
            unknown.add("historical-source-revisions")
        result = {"schemaVersion": 1, "projectId": self.ctx.project_id, "generation": current["generation"],
                "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
                "edges": sorted(edges.values(), key=lambda edge: edge["id"]),
                "coverage": {"complete": False, "scope": "authorized-dependency-closure",
                             "truncated": "truncated" in unknown, "unknown": sorted(unknown)}}
        self._last_inspection = len(visited), len(inspected_edges)
        if historical:
            result["impactSeeds"] = sorted(impact_seeds & nodes.keys())
        return result

    def procedure_snapshot(self, procedure_id, *, max_screens=20, max_nodes=300, max_edges=1000):
        """Authorized view of one Procedure: member Screens, NEXT transitions, design dependencies and the
        property-only uxModel references. It is a read view, never approval (engine plan, Task E4)."""
        from workspace.ontology_ux import references
        schema._identifier(procedure_id)
        if (type(max_screens) is not int or not 1 <= max_screens <= 20 or type(max_nodes) is not int
                or not 1 <= max_nodes <= schema.MAX_NODES or type(max_edges) is not int
                or not 1 <= max_edges <= schema.MAX_EDGES):
            fail(400, "ontology-scope", "지원되는 절차 조회 범위가 필요합니다.")
        current = self.current()
        if not current:
            fail(409, "ontology-not-indexed", "프로젝트 온톨로지가 아직 등록되지 않았습니다.")
        procedure = self._node(current, procedure_id)
        if (not procedure or procedure["type"] != "Procedure" or procedure["tombstone"]
                or not self._visible_node(current, procedure)):
            fail(404, "not-found", "조회할 수 있는 절차가 없습니다.")
        unknown, members, flow_edges = set(), {}, {}

        def edges_of(identifier):
            for edge_id in self._index(current, "adjacency", _bucket(identifier)).get(identifier, []):
                loc = self._index(current, "edges", _bucket(edge_id)).get(edge_id)
                if not loc:
                    fail(409, "ontology-integrity", "온톨로지 관계 인덱스가 누락되었습니다.")
                edge = next((e for e in self._part(current, loc["partition"])["graph"]["edges"] if e["id"] == edge_id), None)
                if not edge or edge["contentHash"] != loc["contentHash"]:
                    fail(409, "ontology-integrity", "관계 원본과 인덱스가 일치하지 않습니다.")
                if edge["tombstone"] or edge["reviewState"] in {"rejected", "deprecated"}:
                    continue
                if not self._visible(edge["sourceRefs"]):
                    unknown.add("unmapped-or-inaccessible")
                    continue
                yield edge

        for edge in edges_of(procedure_id):
            if edge["type"] != "PART_OF" or edge["dst"]["id"] != procedure_id:
                continue
            screen = self._node(current, edge["src"]["id"])
            if screen and screen["type"] == "Screen" and (screen["tombstone"] or screen["reviewState"] in {"rejected", "deprecated"}):
                unknown.add("retired-or-rejected-mapping")     # a required member never silently disappears
                continue
            if (not screen or screen["type"] != "Screen" or not self._visible_node(current, screen)):
                unknown.add("unmapped-or-inaccessible")
                continue
            if screen["revision"] != edge["src"]["revision"] or procedure["revision"] != edge["dst"]["revision"]:
                unknown.add("stale-endpoint-revisions")
                continue
            if screen["id"] in members:
                continue
            if len(members) >= max_screens:
                unknown.add("too-many-screens")
                break
            members[screen["id"]] = screen
            flow_edges[edge["id"]] = edge
        for identifier in list(members):
            for edge in edges_of(identifier):
                if (edge["type"] == "NEXT" and edge["src"]["id"] == identifier and edge["dst"]["id"] in members
                        and all(members[edge[end]["id"]]["revision"] == edge[end]["revision"] for end in ("src", "dst"))):
                    flow_edges[edge["id"]] = edge
        design = self.closure(sorted(members), direction="dependencies", max_nodes=max_nodes, max_edges=max_edges) \
            if members else {"generation": current["generation"], "nodes": [], "edges": [],
                             "coverage": {"unknown": [], "truncated": False}}
        nodes = {n["id"]: n for n in design["nodes"]}
        nodes.update(members)
        nodes[procedure_id] = procedure
        edges = {e["id"]: e for e in design["edges"]}
        edges.update(flow_edges)
        unknown |= set(design["coverage"]["unknown"])
        truncated = bool(design["coverage"]["truncated"])

        def merge(view):
            if view["generation"] != current["generation"]:
                fail(409, "ontology-changed", "조회 중 온톨로지가 변경되었습니다. 다시 조회하세요.")
            nodes.update({n["id"]: n for n in view["nodes"]})
            edges.update({e["id"]: e for e in view["edges"]})

        # The Procedure's own dependencies (e.g. GOVERNED_BY PolicyRule) are not reachable from a member Screen's
        # closure: the Procedure node itself was never a closure seed. Traverse them separately under the same
        # budgets (PR #30 review round 2, #2).
        remaining_nodes, remaining_edges = max_nodes - len(nodes), max_edges - len(edges)
        if remaining_nodes < 1 or remaining_edges < 1:
            truncated = True
        else:
            procedure_design = self.closure([procedure_id], direction="dependencies", max_nodes=remaining_nodes,
                                            max_edges=remaining_edges)
            merge(procedure_design)
            unknown |= set(procedure_design["coverage"]["unknown"])
            truncated = truncated or bool(procedure_design["coverage"]["truncated"])

        # uxModel slot alternatives and condition targets are properties, not edges (review round 11, AE1).
        requested = set()
        for _ in range(4):
            wanted = sorted({ref for node in nodes.values()
                             for ref in references(node.get("properties", {}).get("uxModel") or {})}
                            - nodes.keys() - requested)
            if not wanted or truncated:
                break
            requested.update(wanted)
            found = []
            for start in range(0, len(wanted), 50):
                batch = wanted[start:start + 50]
                page = self.read(batch)
                merge({**page, "nodes": [], "edges": []})
                readable = [n["id"] for n in page["nodes"]]
                if set(batch) - set(readable):
                    unknown.add("unmapped-or-inaccessible")
                found += readable
            for start in range(0, len(found), 20):                # closure accepts at most 20 seeds
                remaining_nodes, remaining_edges = max_nodes - len(nodes), max_edges - len(edges)
                if remaining_nodes < 1 or remaining_edges < 1:
                    truncated = True
                    break
                view = self.closure(found[start:start + 20], direction="dependencies",
                                    max_nodes=remaining_nodes, max_edges=remaining_edges)
                merge(view)
                unknown |= set(view["coverage"]["unknown"])
                truncated = truncated or bool(view["coverage"]["truncated"])
        else:
            if {ref for node in nodes.values() for ref in references(node.get("properties", {}).get("uxModel") or {})} - nodes.keys() - requested:
                truncated = True
        if len(nodes) > max_nodes or len(edges) > max_edges:
            truncated = True
        if "rejected-mapping" in unknown:
            unknown.add("retired-or-rejected-mapping")
        self._recheck(current)
        truncated = truncated or "too-many-screens" in unknown or "truncated" in unknown
        if truncated:
            unknown.add("truncated")
        return {"schemaVersion": 1, "projectId": self.ctx.project_id, "generation": current["generation"],
                "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
                "edges": sorted(edges.values(), key=lambda e: e["id"]),
                "coverage": {"complete": False, "scope": "authorized-procedure-snapshot",
                             "truncated": truncated, "unknown": sorted(unknown | {"outside-snapshot-not-certified"})}}

    def context(self, node_ids):
        graph = self.closure(node_ids, direction="both", max_nodes=50)
        if set(node_ids) - {node["id"] for node in graph["nodes"]}:
            fail(409, "ontology-context-unavailable", "선택한 온톨로지 근거를 모두 읽을 수 없습니다.")
        payload = {key: value for key, value in graph.items() if key != "generation"}
        if len(schema.canonical(payload)) > 100000:
            fail(422, "ontology-context-limit", "생성에 사용할 온톨로지 범위를 줄이세요.")
        return {**payload, "generation": graph["generation"], "hash": schema.digest(payload),
                "sourceRefs": _references(graph)}

    def source_nodes(self, reference, *, for_impact=False, include_history=True):
        ref = schema.source_ref(reference)
        current = self.current()
        if not current or not for_impact and not self._visible([ref]):
            return []
        key = source_identity(ref)
        values = list(self._index(current, "sources", _bucket(key)).get(key, []))
        if for_impact and include_history:
            values.extend(self._index(current, "historical-sources", _bucket(key)).get(key, []))
        for edge_id in self._index(current, "edge-sources", _bucket(key)).get(key, []):
            loc = self._index(current, "edges", _bucket(edge_id)).get(edge_id)
            part = self._part(current, loc["partition"]) if loc else None
            edge = next((item for item in part["graph"]["edges"] if item["id"] == edge_id), None) if part else None
            if edge and (for_impact and include_history or
                         not edge["tombstone"] and edge["reviewState"] not in {"rejected", "deprecated"}
                         and (for_impact or self._visible(edge["sourceRefs"]))):
                values.append(edge["src"]["id"])
        result = []
        if len(set(values)) > 500:
            fail(422, "ontology-impact-scope", "원본의 영향 시작점이 500개를 초과합니다. 노드를 선택해 범위를 나누세요.")
        for identifier in values:
            node = self._node(current, identifier)
            if (for_impact and (include_history or node and not node["tombstone"])
                    or node and not node["tombstone"] and self._visible_node(current, node)):
                result.append(identifier)
        self._recheck(current)
        return sorted(set(result))

    def source_history(self, reference, maximum=20):
        current = self.current()
        if not current:
            return [], False
        key = source_identity(reference)
        entries = self._index(current, "source-history", _bucket(key)).get(key, [])
        views = []
        for entry in entries[-maximum:]:
            snapshot = self._read_json("project-snapshot", entry["hash"])
            if snapshot.get("projectId") != self.ctx.project_id or snapshot.get("generation") != entry["generation"]:
                fail(409, "ontology-integrity", "과거 온톨로지 스냅샷의 범위가 다릅니다.")
            reader = Ontology(self.ctx)
            reader._snapshot, reader._anchor = snapshot, current
            reader.sources = self.sources
            views.append((reader, entry["nodeIds"], entry["hash"]))
        self._recheck(current)
        return views, len(entries) > maximum

    def review_node(self, identifier, *, expected_generation, revision, decision, reason, request_id):
        if self._snapshot is not None:
            fail(409, "ontology-historical-read-only", "과거 스냅샷은 조회 전용입니다.")
        self.sources.max_records = 90
        schema._identifier(identifier)
        schema._identifier(request_id)
        schema._revision(revision)
        schema._text(reason, 2000)
        if decision not in {"reviewed", "approved", "rejected", "deprecated"}:
            fail(400, "ontology-review-decision", "지원되는 검토 결정을 선택하세요.")
        current = self.current()
        audit_id = schema.identity("ontology-review", self.ctx.actor, request_id)
        request_hash = schema.digest([identifier, expected_generation, revision, decision, reason])
        existing = self.storage.get(self.ctx.owner, "ontology", audit_id)
        if existing:
            if existing.get("requestHash") != request_hash:
                fail(409, "request-changed", "같은 검토 요청 ID의 내용이 다릅니다.")
            self.sources.verify(existing["sourceRefs"])
            self._recheck(current)
            return {"review": existing, "generation": existing["generation"],
                    "historical": (current or {}).get("generation") != existing["generation"]}
        if not current or current["generation"] != expected_generation:
            fail(409, "ontology-changed", "검토한 온톨로지 기준이 변경되었습니다.")
        target = self._node(current, identifier)
        if not target or not self._visible(target["sourceRefs"]):
            fail(404, "not-found", "검토할 수 있는 온톨로지 노드가 없습니다.")
        if target["scope"] != {"kind": "project", "projectId": self.ctx.project_id}:
            fail(403, "ontology-origin-review", "공유 원본의 승인 주체에게 검토를 요청하세요.")
        roles = {"owner"}
        if target["type"] in {"Product", "PolicyRule", "Procedure"}:
            roles.add("planner")
        elif target["type"] in {"CodeFile", "CodeSymbol", "Test", "API", "Skill"}:
            roles.add("developer")
        elif target["type"] != "Team":
            roles.add("designer")
        self.ctx.fresh(roles)
        if target["revision"] != revision:
            fail(409, "ontology-node-changed", "노드 버전이 변경되었습니다.")
        if target["reviewState"] == "deprecated":
            fail(409, "ontology-deprecated", "폐기된 노드는 새 매핑 버전으로 다시 등록하세요.")
        if decision == "approved" and target["reviewState"] != "reviewed":
            fail(409, "ontology-review-required", "검토 완료 후 정확한 버전을 승인하세요.")
        if decision == "approved" and target["type"] in {"Product", "PolicyRule"} and not any(
                ref["sourceKind"] in {"product-guideline", "document-revision"} for ref in target["sourceRefs"]):
            fail(409, "ontology-business-authority", "상품·업무 규칙에는 게시 또는 승인된 업무 원본이 필요합니다.")
        location = self._index(current, "nodes", _bucket(identifier))[identifier]
        prior = self._part(current, location["partition"])
        if prior.get("kind") == "published-product":
            fail(409, "ontology-source-review", "게시된 상품 기준은 상품 기획·게시 API에서 수정하세요.")
        graph = copy.deepcopy(prior["graph"])
        next_revision = revision + 1 if decision == "deprecated" else revision
        updated_node = schema.seal({**target, "revision": next_revision, "reviewState": decision,
                                   "tombstone": decision == "deprecated"})
        usage_screens = []
        if target["type"] == "Pattern" and decision == "approved":
            usages = target.get("properties", {}).get("usageIds", [])
            if not 2 <= len(set(usages)) <= 20:
                fail(409, "ontology-pattern-usages", "서로 다른 검토 완료 화면 두 개 이상이 필요합니다.")
            for usage in sorted(set(usages)):
                screen = self._node(current, usage)
                if (not screen or screen["type"] != "Screen" or screen["tombstone"]
                        or screen["reviewState"] not in {"reviewed", "approved"}
                        or not self._visible(screen["sourceRefs"])):
                    fail(409, "ontology-pattern-usages", "패턴의 사용 화면 근거를 다시 확인하세요.")
                usage_screens.append(screen)
            bindings = [{key: screen[key] for key in ("id", "revision", "contentHash")} for screen in usage_screens]
            updated_node = schema.seal({**updated_node, "properties": {
                **updated_node.get("properties", {}), "usageBindings": bindings}})
            graph["edges"] = [schema.seal({**edge, "tombstone": True, "reviewState": "deprecated"})
                if edge["src"]["id"] == identifier and edge["type"] == "REFERENCES"
                and edge["id"] == schema.identity("pattern-usage", identifier, edge["dst"]["id"])
                and edge["dst"]["id"] not in usages else edge for edge in graph["edges"]]
            for screen in usage_screens:
                edge_id = schema.identity("pattern-usage", identifier, screen["id"])
                graph["edges"] = [edge for edge in graph["edges"] if edge["id"] != edge_id]
                graph["edges"].append(schema.seal({"id": edge_id, "type": "REFERENCES",
                    "src": {"id": identifier, "revision": next_revision},
                    "dst": {"id": screen["id"], "revision": screen["revision"]},
                    "sourceRefs": screen["sourceRefs"], "provenance": "declared",
                    "reviewState": "approved", "tombstone": False}))
        graph["nodes"] = [updated_node if node["id"] == identifier else node for node in graph["nodes"]]
        own = {node["id"]: node for node in graph["nodes"]}
        externals = {}
        for i, original in enumerate(graph["edges"]):
            edge = copy.deepcopy(original)
            if edge["tombstone"]:
                continue
            for end in ("src", "dst"):
                if edge[end]["id"] not in own:
                    external = self._node(current, edge[end]["id"])
                    if not external or not self._visible_node(current, external):
                        fail(409, "ontology-reference-unavailable", "활성 관계 대상의 현재 원본 권한을 확인하지 못했습니다.")
                    externals[external["id"]] = external
            if decision in {"rejected", "deprecated"} and identifier in (edge["src"]["id"], edge["dst"]["id"]):
                edge["reviewState"] = "candidate"
            graph["edges"][i] = schema.seal(edge)
        # Tombstoned witnesses can retain inaccessible historical endpoints.
        # Validate active relationships separately; history remains immutable.
        if len(graph["nodes"]) > schema.MAX_NODES or len(graph["edges"]) > schema.MAX_EDGES:
            fail(422, "ontology-capacity", "검토 근거를 포함한 파티션의 저장 한도를 초과했습니다. 검토 범위를 나누세요.")
        schema.validate_graph({**graph, "edges": [e for e in graph["edges"] if not e["tombstone"]]},
                              external_nodes=list(externals.values()), diagnostic=True)
        active = {"nodes": [n for n in graph["nodes"] if not n["tombstone"]],
                  "edges": [e for e in graph["edges"] if not e["tombstone"]]}
        checks = self.sources.verify(_references(active) + target["sourceRefs"]
                                     + [ref for node in externals.values() for ref in node["sourceRefs"]])
        part = {**prior, "graph": graph, "updatedBy": self.ctx.actor,
                "reviews": {**prior.get("reviews", {}), identifier: audit_id}}
        updated = copy.deepcopy(current)
        updated["partitions"][location["partition"]] = self._put_json(location["partition"], part)
        self._replace_indexes(updated, current, location["partition"], prior["graph"], graph)
        updated["generation"] = schema.digest({"partitions": updated["partitions"], "indexes": updated["indexes"]})
        audit = {"id": audit_id, "projectId": self.ctx.project_id, "kind": "ontology-review",
                 "actor": self.ctx.actor, "role": self.ctx.scope["role"], "nodeId": identifier,
                 "beforeRevision": revision, "afterRevision": next_revision,
                 "decision": decision, "reason": reason, "nodeHash": updated_node["contentHash"],
                 "sourceRefs": list({schema.digest(ref): ref for ref in [
                     *target["sourceRefs"], *(ref for screen in usage_screens for ref in screen["sourceRefs"])]}.values()),
                 "usageBindings": updated_node.get("properties", {}).get("usageBindings", []),
                 "generation": updated["generation"], "requestHash": request_hash}
        self.sources.recheck()
        self.ctx.commit([self.ctx.write("ontology", updated, current["version"]),
                         self.ctx.write("ontology", audit)], checks)
        return {"review": audit, "generation": updated["generation"], "node": updated_node}
