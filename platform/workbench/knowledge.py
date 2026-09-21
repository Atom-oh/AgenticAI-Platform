"""Bounded ETL and exact cosine search over private, verified artifact projections."""
from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import Counter
from urllib.parse import urlsplit

from workbench.collectors import collect, connection, profile_hash
from workbench.service import (
    MAX_SOURCES, ROLES, Service, _hash, _id, _json, fail, fields, paginate, text,
)

MAX_DOCUMENTS = 100
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_NODES = 500
MAX_EDGES = 1000
LABELS = frozenset({"Product", "Flow", "Guideline", "Component", "Icon", "Screen",
                    "API", "Test", "Skill", "Role", "Document", "Rule"})
DEPENDENCIES = frozenset({"DEPENDS_ON", "IMPLEMENTS", "USES", "REFERENCES", "VALIDATES",
                          "CONFORMS_TO", "REQUIRES"})
EDGE_TYPES = DEPENDENCIES | {"OWNS"}
BACKEND = {"vector": "private-artifact-exact-cosine", "graph": "private-artifact-typed-graph",
           "embedding": "local-hashing-256-v1"}


class HashingEmbedding:
    """Signed, normalized word/character hashing; no learned or remote model."""
    name = "local-hashing-256-v1"
    dimensions = 256

    def embed(self, texts):
        vectors = []
        for value in texts:
            terms = re.findall(r"\w+", value.casefold())
            tokens = terms + [word[i:i + 2] for word in terms for i in range(len(word) - 1)]
            vector = [0.0] * self.dimensions
            for token, count in Counter(tokens).items():
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % self.dimensions] += (
                    (1 if digest[4] & 1 else -1) * (1 + math.log(count)))
            norm = math.sqrt(sum(x * x for x in vector))
            vectors.append([round(x / norm, 10) if norm else 0.0 for x in vector])
        return vectors


def embed(ctx, texts, expected=None):
    adapter = getattr(ctx.host, "workbench_embeddings", None) or HashingEmbedding()
    name, dimensions = getattr(adapter, "name", None), getattr(adapter, "dimensions", None)
    if (not isinstance(name, str) or len(name) > 100 or type(dimensions) is not int
            or not 1 <= dimensions <= 4096 or expected is not None and name != expected):
        fail(503, "embedding-not-configured", "해당 인덱스의 임베딩 어댑터가 구성되지 않았습니다.")
    if not isinstance(adapter, HashingEmbedding):
        if getattr(adapter, "approved", False) is not True:
            fail(503, "embedding-not-configured", "승인된 임베딩 어댑터가 필요합니다.")
        ctx.gate("input", {"texts": texts}, "workbench-embedding")
    vectors = adapter.embed(texts)
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        fail(422, "embedding-invalid", "임베딩 결과 수가 일치하지 않습니다.")
    for vector in vectors:
        if (not isinstance(vector, list) or len(vector) != dimensions
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)):
            fail(422, "embedding-invalid", "유한한 숫자 벡터가 필요합니다.")
    if not isinstance(adapter, HashingEmbedding):
        ctx.gate("output", {"vectors": vectors}, "workbench-embedding")
    return vectors, name


def register_source(ctx, body):
    fields(body, {"requestId", "name", "kind", "connectionId", "scope", "description"})
    ctx.fresh()
    kind = body.get("kind")
    if kind not in {"snapshot", "confluence", "git"}:
        fail(400, "invalid-source", "지원하지 않는 소스 종류입니다.")
    name = text(body.get("name"), "source name", 180)
    description = text(body.get("description", ""), "description", 2000, True)
    connection_id, fingerprint = None, None
    if kind != "snapshot":
        ctx.fresh(operator=True)
        if body.get("scope") not in (None, {}):
            fail(400, "invalid-source", "외부 수집 범위는 서버 연결 프로필에서만 지정합니다.")
        connection_id = _id(body.get("connectionId"))
        profile = connection(ctx.host, connection_id, ctx.project_id, kind)
        fingerprint = profile_hash(profile)
        scope = {"type": "server-connection", "connectionId": connection_id}
    else:
        if body.get("connectionId") or body.get("scope") not in (None, {}):
            fail(400, "invalid-source", "스냅샷은 서버가 지정한 프로젝트 범위로만 등록됩니다.")
        scope = {"type": "project-snapshot", "projectId": ctx.project_id}
    identifier = ctx.identity("wb_source", body.get("requestId"))
    previous = ctx.existing("wb_source", identifier, body)
    if previous:
        return previous
    items, more = ctx.bounded_list("wb_source", MAX_SOURCES)
    if len(items) >= MAX_SOURCES or more:
        fail(422, "source-limit", "프로젝트 소스 수 제한을 초과했습니다.")
    return ctx.create("wb_source", body, {
        "name": name, "description": description, "kind": kind, "scope": scope,
        "connectionId": connection_id, "connectionHash": fingerprint, "sourceVersion": 0,
        "permissionVersion": 0, "access": {}, "accessExpiresAt": 0, "status": "registered",
        "backend": BACKEND, "provenance": "local-authoring" if kind == "snapshot" else "configured-connector"})


def canonicalize(documents, provenance="declared"):
    if not isinstance(documents, list) or len(documents) > MAX_DOCUMENTS:
        fail(422, "snapshot-limit", "스냅샷 문서는 최대 100개입니다.")
    if len(_json(documents)) > MAX_SNAPSHOT_BYTES:
        fail(422, "snapshot-limit", "스냅샷이 크기 제한을 초과했습니다.")
    result, seen, node_ids, node_count, edge_count = [], set(), set(), 0, 0
    for item in documents:
        fields(item, {"id", "title", "content", "revision", "kind", "sourceUrl",
                      "allowedRoles", "entities", "relations"})
        identifier = _id(item.get("id"))
        if identifier in seen:
            fail(400, "duplicate-document", "중복된 문서 ID입니다.")
        seen.add(identifier)
        content = text(item.get("content"), "document content", 100_000, True)
        if len(content.encode()) > 100_000:
            fail(422, "snapshot-limit", "문서 크기 제한을 초과했습니다.")
        title = text(item.get("title"), "document title", 500)
        revision = text(str(item.get("revision", "")), "source revision", 128)
        roles = item.get("allowedRoles", sorted(ROLES))
        if (not isinstance(roles, list) or len(roles) > 4
                or any(not isinstance(x, str) or x not in ROLES for x in roles)):
            fail(400, "invalid-acl", "문서 접근 역할이 올바르지 않습니다.")
        source_url = text(item.get("sourceUrl", ""), "source URL", 2000, True)
        if source_url:
            parsed = urlsplit(source_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                fail(400, "invalid-source-url", "자격 증명 없는 HTTPS 출처만 사용할 수 있습니다.")
        nodes, edges = item.get("entities", []), item.get("relations", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            fail(400, "invalid-graph", "엔터티와 관계 목록이 필요합니다.")
        canonical_nodes = []
        for node in nodes:
            fields(node, {"id", "label", "title", "version", "role", "provenance"})
            node_id, label = _id(node.get("id")), node.get("label")
            if label not in LABELS or node_id in node_ids:
                fail(400, "invalid-graph", "엔터티 타입 또는 고유 ID가 올바르지 않습니다.")
            node_ids.add(node_id)
            role = node.get("role")
            if role is not None and role not in ROLES:
                fail(400, "invalid-graph", "담당 역할이 올바르지 않습니다.")
            canonical_nodes.append({"id": node_id, "label": label,
                "title": text(node.get("title"), "node title", 300),
                "version": text(str(node.get("version", revision)), "node version", 128),
                "role": role, "provenance": provenance})
        canonical_edges = []
        for edge in edges:
            fields(edge, {"src", "rel", "dst", "provenance"})
            if edge.get("rel") not in EDGE_TYPES:
                fail(400, "invalid-graph", "지원하지 않는 의존 관계 타입입니다.")
            canonical_edges.append({"src": _id(edge.get("src")), "rel": edge["rel"],
                                    "dst": _id(edge.get("dst")), "provenance": provenance})
        node_count += len(nodes)
        edge_count += len(edges)
        if node_count > MAX_NODES or edge_count > MAX_EDGES:
            fail(422, "graph-limit", "그래프 크기 제한을 초과했습니다.")
        result.append({"id": identifier, "title": title, "content": content, "revision": revision,
                       "kind": text(item.get("kind", "document"), "document kind", 80),
                       "sourceUrl": source_url, "allowedRoles": sorted(set(roles)),
                       "contentHash": hashlib.sha256(content.encode()).hexdigest(),
                       "entities": canonical_nodes, "relations": canonical_edges})
    for doc in result:
        if any(e["src"] not in node_ids or e["dst"] not in node_ids for e in doc["relations"]):
            fail(400, "invalid-graph", "관계의 양 끝 엔터티가 스냅샷에 있어야 합니다.")
    return result


def access_ledger(source, docs):
    access = {identifier: {**entry, "tombstone": True} for identifier, entry in source.get("access", {}).items()}
    # Bound historical tombstones. Missing entries also deny access independently.
    access = dict(sorted(access.items())[-MAX_DOCUMENTS:])
    for doc in docs:
        access[doc["id"]] = {"revision": doc["revision"], "contentHash": doc["contentHash"],
                             "allowedRoles": doc["allowedRoles"], "tombstone": False}
    return access


def start_batch(ctx, source_id, body, dispatch=True):
    fields(body, {"requestId", "documents"})
    ctx.fresh()
    source = ctx.get("wb_source", source_id)
    if source["kind"] == "snapshot" and ctx.scope["role"] != "owner":
        # Project membership alone cannot replace another author's source or
        # turn an unreadable historical document into newly readable metadata.
        if (source.get("createdBy") != ctx.actor
                or any(ctx.scope["role"] not in entry.get("allowedRoles", [])
                       for entry in source.get("access", {}).values())):
            fail(403, "source-write-forbidden", "이 원본을 교체할 권한이 없습니다.")
    if source["kind"] != "snapshot":
        ctx.fresh(operator=True)
        profile = connection(ctx.host, source["connectionId"], ctx.project_id, source["kind"])
        if profile_hash(profile) != source["connectionHash"]:
            fail(409, "connection-changed", "연결 범위가 변경되어 재등록이 필요합니다.")
        if "documents" in body:
            fail(400, "invalid-source", "외부 연결은 승인된 서버 수집기로만 실행됩니다.")
    auth = ctx.authorization()
    identifier = ctx.identity("wb_batch", body.get("requestId"))
    fingerprint = {**body, "sourceId": source_id}
    previous = ctx.existing("wb_batch", identifier, fingerprint)
    if previous:
        job = ctx.storage.get(ctx.owner, "job", previous["jobId"])
        if not job:
            job = ctx.queue_job(previous["jobId"], previous["jobInput"], previous["requestHash"]) if dispatch else None
        elif dispatch and job["status"] == "queued":
            ctx.host._invoke(ctx.owner, job)
        return {"batch": previous, "job": job}
    if dispatch:
        ctx.host._worker_ready()
    docs = canonicalize(body.get("documents", []), source.get("graphProvenance", "declared")) if source["kind"] == "snapshot" else []
    raw_key, raw_hash = ctx.put_json("wb_batch", identifier, "raw.json", body.get("documents", []))
    canonical_key, canonical_hash = ctx.put_json("wb_batch", identifier, "canonical.json", docs)
    version, permissions = source["sourceVersion"] + 1, source["permissionVersion"] + 1
    job_id = "wbjob-" + _hash([identifier, "index"])[:40]
    updated = {**source, "sourceVersion": version, "permissionVersion": permissions,
               "access": access_ledger(source, docs), "status": "indexing",
               "accessExpiresAt": ctx.storage.clock() + (86_400_000 if source["kind"] == "snapshot" else 300_000)}
    job_input = {**auth, "operation": "index", "batchId": identifier, "targetId": identifier,
                 "sourceVersions": {source_id: {"sourceVersion": version, "permissionVersion": permissions}},
                 "inputHash": canonical_hash, "connectionHash": source.get("connectionHash")}
    batch = {"id": identifier, "projectId": ctx.project_id, "createdBy": ctx.actor, "sourceId": source_id,
             "requestHash": _hash(fingerprint), "status": "queued", "jobId": job_id, "jobInput": job_input,
             "sourceVersion": version, "permissionVersion": permissions,
             "rawKey": raw_key, "rawHash": raw_hash, "canonicalKey": canonical_key,
             "canonicalHash": canonical_hash, "documentCount": len(docs),
             "complete": source["kind"] == "snapshot", "extraction": "explicit-snapshot"}
    saved = ctx.commit([ctx.write("wb_source", updated, source["version"]), ctx.write("wb_batch", batch)])[1]
    job = ctx.queue_job(job_id, job_input, saved["requestHash"]) if dispatch else {
        "id": job_id, "task": "workbench", "input": job_input, "status": "running"}
    return {"batch": saved, "job": job}


def source_current(ctx, source, binding=None):
    if source.get("status") not in {"indexing", "ready"} or source.get("accessExpiresAt", 0) <= ctx.storage.clock():
        return False
    if binding and any(source.get(k) != binding.get(k) for k in ("sourceVersion", "permissionVersion")):
        return False
    if source["kind"] != "snapshot":
        try:
            profile = connection(ctx.host, source["connectionId"], ctx.project_id, source["kind"])
            return profile_hash(profile) == source["connectionHash"]
        except Exception:
            return False
    return True


def evidence(source, document, generation):
    return {"sourceId": source["id"], "documentId": document["id"],
            "revision": document["revision"], "contentHash": document["contentHash"],
            "sourceVersion": source["sourceVersion"], "permissionVersion": source["permissionVersion"],
            "generation": generation, "accessExpiresAt": source["accessExpiresAt"],
            "allowedRoles": list(document["allowedRoles"])}


def visible(ctx, source, ref):
    if not source_current(ctx, source, ref):
        return False
    access = source.get("access", {}).get(ref.get("documentId"), {})
    return (access.get("tombstone") is False and ctx.scope["role"] in access.get("allowedRoles", [])
            and ref.get("allowedRoles") == access.get("allowedRoles")
            and all(access.get(k) == ref.get(k) for k in ("revision", "contentHash")))


def verify_refs(ctx, refs, *, authority="legacy"):
    if not isinstance(refs, list) or len(refs) > (30000 if authority == "canonical" else 50):
        fail(400, "invalid-evidence", "근거 목록이 올바르지 않습니다.")
    checks = []
    if authority == "canonical":
        from workspace.ontology_sources import Sources
        reader = Sources(ctx, max_sources=90)
        checks = reader.verify(refs)
        reader.recheck()
        return checks
    if authority != "legacy":
        fail(400, "invalid-authority", "근거 저장소가 올바르지 않습니다.")
    for ref in refs:
        if not isinstance(ref, dict):
            fail(400, "invalid-evidence", "정확한 소스 버전 근거가 필요합니다.")
        if "sourceKind" in ref:
            fail(400, "invalid-evidence", "기존 지식 자료에는 기존 소스 근거가 필요합니다.")
        source = ctx.get("wb_source", ref.get("sourceId"))
        if (not visible(ctx, source, ref) or not isinstance(ref.get("generation"), str)
                or ref.get("accessExpiresAt") != source["accessExpiresAt"]):
            fail(409, "stale-evidence", "근거 권한 또는 소스 버전이 변경되었습니다.")
        manifest = ctx.storage.get(ctx.owner, "wb_index", "current") or {}
        projection = manifest.get("sources", {}).get(source["id"], {})
        if projection.get("generation") != ref["generation"]:
            fail(409, "stale-evidence", "근거 인덱스 버전이 변경되었습니다.")
        checks.append(ctx.check("wb_source", source))
    return checks


def authorize_refs(ctx, refs, *, authority="legacy"):
    """Require both the bound historical audience and the current source audience.

    This does not assert freshness of historical evidence and is never sufficient
    for approval, execution, task completion, or publication.
    """
    if not isinstance(refs, list) or len(refs) > (30000 if authority == "canonical" else 50):
        fail(400, "invalid-evidence", "근거 목록이 올바르지 않습니다.")
    if authority == "canonical":
        from workspace.ontology_sources import Sources, authority_identity
        reader = Sources(ctx, max_sources=90)
        unique = {authority_identity(ref): ref for ref in refs}
        if len(unique) > reader.max_sources:
            fail(422, "ontology-source-limit", "서로 다른 원본 근거 수 제한을 초과했습니다. 범위를 나누세요.")
        for ref in unique.values():
            reader.authorize(ref)
        return reader.recheck()
    if authority != "legacy":
        fail(400, "invalid-authority", "근거 저장소가 올바르지 않습니다.")
    for ref in refs:
        if not isinstance(ref, dict):
            fail(400, "invalid-evidence", "문서 근거가 필요합니다.")
        if "sourceKind" in ref:
            fail(400, "invalid-evidence", "기존 지식 자료에는 기존 소스 근거가 필요합니다.")
        source = ctx.get("wb_source", ref.get("sourceId"))
        access = source.get("access", {}).get(ref.get("documentId"), {})
        if (not source_current(ctx, source) or access.get("tombstone") is not False
                or ctx.scope["role"] not in access.get("allowedRoles", [])
                or not isinstance(ref.get("allowedRoles"), list)
                or ctx.scope["role"] not in ref["allowedRoles"]):
            fail(403, "source-forbidden", "이 근거 자료를 읽을 현재 권한이 없습니다.")


def publish_batch(ctx, batch_id, pinned):
    ctx.fresh()
    batch = ctx.get("wb_batch", batch_id)
    source = ctx.get("wb_source", batch["sourceId"])
    if not source_current(ctx, source, pinned["sourceVersions"][source["id"]]):
        fail(409, "source-changed", "수집 소스 버전 또는 접근 권한이 변경되었습니다.")
    if batch["canonicalHash"] != pinned["inputHash"]:
        fail(409, "input-changed", "작업 입력 해시가 일치하지 않습니다.")
    if batch["status"] == "completed":
        return {"batchId": batch_id, "generation": batch["generation"]}
    if source["kind"] != "snapshot":
        ctx.fresh(operator=True)
        profile = connection(ctx.host, source["connectionId"], ctx.project_id, source["kind"])
        if profile_hash(profile) != pinned["connectionHash"]:
            fail(409, "connection-changed", "연결 범위가 변경되었습니다.")
        collector = getattr(ctx.host, "workbench_collector", None)
        observed_at = ctx.storage.clock()
        collected = collector(profile=copy.deepcopy(profile)) if callable(collector) else collect(
            profile, token_provider=getattr(ctx.host, "workbench_token_provider", None),
            effective_acl_resolver=getattr(ctx.host, "workbench_effective_acl", None))
        if not isinstance(collected, dict) or type(collected.get("complete")) is not bool:
            fail(422, "collector-invalid", "수집 상태를 확인하지 못했습니다.")
        docs = canonicalize(collected.get("documents"))
        # Connector snapshots must explicitly carry permissions, with no wider
        # role set than the server's approved connection.
        for raw, doc in zip(collected["documents"], docs):
            if "allowedRoles" not in raw or set(doc["allowedRoles"]) - set(profile["allowedRoles"]):
                fail(422, "collector-invalid", "수집 문서 접근 권한이 확인되지 않았습니다.")
        raw_key, raw_hash = ctx.put_json("wb_batch", batch_id, "collected-raw.json", collected["documents"])
        key, digest = ctx.put_json("wb_batch", batch_id, "collected-canonical.json", docs)
        ctx.fresh(operator=True)
        source = ctx.commit([ctx.write("wb_source", {
            **source, "access": access_ledger(source, docs), "accessExpiresAt": observed_at + 300_000,
        }, source["version"])])[0]
        batch = {**batch, "canonicalKey": key, "canonicalHash": digest, "rawKey": raw_key, "rawHash": raw_hash,
                 "complete": collected["complete"], "extraction": collected.get("collector", "injected-approved-collector")}
    else:
        docs = ctx.read_json(batch["canonicalKey"], batch["canonicalHash"])
    generation = _hash({"sourceId": source["id"], "sourceVersion": source["sourceVersion"],
                        "canonicalHash": batch["canonicalHash"], "permissionVersion": source["permissionVersion"]})
    vectors, embedding_name = embed(ctx, [doc["title"] + "\n" + doc["content"] for doc in docs])
    rows, nodes, edges = [], [], []
    for doc, vector in zip(docs, vectors):
        ref = evidence(source, doc, generation)
        document_id = "d-" + _hash([source["id"], doc["id"]])[:40]
        rows.append({"id": document_id, "sourceDocumentId": doc["id"], "title": doc["title"],
                     "kind": doc["kind"], "vector": vector, "sourceRef": ref})
        nodes.extend({**node, "sourceRef": ref} for node in doc["entities"])
        edges.extend({**edge, "sourceRef": ref} for edge in doc["relations"])
    prefix = generation + "/"
    vector_key, vector_hash = ctx.put_json("wb_index", source["id"], prefix + "vectors.json",
                                         {"embedding": embedding_name, "rows": rows})
    graph_key, graph_hash = ctx.put_json("wb_index", source["id"], prefix + "graph.json",
                                       {"nodes": nodes, "edges": edges})
    # Read both back before publishing a manifest; a partial write is unreachable.
    ctx.read_json(vector_key, vector_hash)
    ctx.read_json(graph_key, graph_hash)
    ctx.fresh(operator=source["kind"] != "snapshot")
    current_source = ctx.get("wb_source", source["id"])
    if current_source["version"] != source["version"] or not source_current(ctx, current_source, source):
        fail(409, "source-changed", "게시 전에 소스 또는 접근 권한이 변경되었습니다.")
    manifest = ctx.storage.get(ctx.owner, "wb_index", "current")
    sources = dict((manifest or {}).get("sources", {}))
    sources[source["id"]] = {
        "generation": generation, "sourceVersion": source["sourceVersion"],
        "permissionVersion": source["permissionVersion"], "accessExpiresAt": source["accessExpiresAt"],
        "canonicalKey": batch["canonicalKey"], "canonicalHash": batch["canonicalHash"],
        "vectorKey": vector_key, "vectorHash": vector_hash, "graphKey": graph_key, "graphHash": graph_hash,
        "documentCount": len(docs), "nodeCount": len(nodes), "edgeCount": len(edges),
        "complete": batch["complete"], "embedding": embedding_name}
    publication = {**(manifest or {}), "id": "current", "projectId": ctx.project_id, "sources": sources}
    publication["generation"] = _hash({"sources": sources, "declared": publication.get("declared")})
    manifest_key, manifest_hash = ctx.put_json("wb_index", "current", publication["generation"] + "/manifest.json",
                                              {"generation": publication["generation"], "sources": sources,
                                               "declared": publication.get("declared")})
    publication.update(manifestKey=manifest_key, manifestHash=manifest_hash)
    completed = {**batch, "status": "completed", "generation": generation,
                 "documentCount": len(docs), "vectorCount": len(rows), "nodeCount": len(nodes),
                 "edgeCount": len(edges), "backend": {**BACKEND, "embedding": embedding_name}}
    completed["counts"] = {"documents": len(docs), "vectors": len(rows), "nodes": len(nodes), "edges": len(edges)}
    ctx.commit([ctx.write("wb_index", publication, manifest["version"] if manifest else None),
                ctx.write("wb_source", {**source, "status": "ready"}, source["version"]),
                ctx.write("wb_batch", completed, batch["version"])])
    return {"batchId": batch_id, "generation": generation, "documentCount": len(docs)}


def projections(ctx):
    ctx.fresh()
    manifest = ctx.storage.get(ctx.owner, "wb_index", "current") or {}
    active, missing = [], []
    all_sources, truncated = ctx.bounded_list("wb_source", MAX_SOURCES)
    for source in all_sources:
        binding = manifest.get("sources", {}).get(source["id"])
        if not binding or not source_current(ctx, source, binding):
            missing.append(source["id"])
            continue
        active.append((source, binding))
    coverage = {"complete": not missing and not truncated and bool(active) and all(b["complete"] for _, b in active),
                "indexedSources": len(active), "unavailableSources": len(missing),
                "scope": "bounded-registered-sources", "truncated": truncated}
    return manifest, active, coverage


def search(ctx, query):
    q = text(query.get("q", ""), "query", 2000, True)
    kind = text(query.get("kind", ""), "kind", 80, True)
    manifest, active, coverage = projections(ctx)
    observed_role = ctx.scope["role"]
    items, embeddings, query_vectors = [], set(), {}
    for source, binding in active:
        vectors = ctx.read_json(binding["vectorKey"], binding["vectorHash"])
        embedding_name = vectors["embedding"]
        embeddings.add(embedding_name)
        if q and embedding_name not in query_vectors:
            query_vectors[embedding_name] = embed(ctx, [q], expected=embedding_name)[0][0]
        for row in vectors["rows"]:
            if not visible(ctx, source, row["sourceRef"]) or kind and row["kind"] != kind:
                continue
            score = 0.0
            if q:
                query_vector, vector = query_vectors[embedding_name], row["vector"]
                if len(query_vector) != len(vector):
                    fail(409, "artifact-invalid", "벡터 차원이 일치하지 않습니다.")
                norm = math.sqrt(sum(x*x for x in vector) * sum(x*x for x in query_vector))
                score = sum(x*y for x, y in zip(vector, query_vector)) / norm if norm else 0.0
            items.append({k: v for k, v in row.items() if k != "vector"} | {"score": round(score, 8)})
    items.sort(key=lambda x: (-x["score"], x["id"]) if q else (x["id"],))
    result = paginate(items, query, [ctx.owner, ctx.actor, ctx.scope["role"], manifest.get("generation"),
                                    [(s["id"], s["version"]) for s, _ in active], q, kind])
    result.update(generation=manifest.get("generation"), coverage=coverage,
                  backend={**BACKEND, "embedding": next(iter(embeddings)) if len(embeddings) == 1 else
                           sorted(embeddings) if embeddings else BACKEND["embedding"]})
    ctx.fresh()
    if ctx.scope["role"] != observed_role:
        fail(409, "authority-changed", "조회 중 프로젝트 역할이 변경되었습니다.")
    recheck_sources(ctx, active)
    return result


def read_document(ctx, identifier):
    _id(identifier)
    manifest, active, _ = projections(ctx)
    for source, binding in active:
        canonical = ctx.read_json(binding["canonicalKey"], binding["canonicalHash"])
        for document in canonical:
            if "d-" + _hash([source["id"], document["id"]])[:40] != identifier:
                continue
            ref = evidence(source, document, binding["generation"])
            if not visible(ctx, source, ref):
                fail(404, "not-found", "읽을 수 있는 자료가 없습니다.")
            ctx.fresh()
            verify_refs(ctx, [ref])
            return {"document": {**document, "id": identifier, "sourceDocumentId": document["id"]},
                    "evidence": ref, "generation": manifest["generation"]}
    fail(404, "not-found", "읽을 수 있는 자료가 없습니다.")


def graph(ctx, target_id=None, *, historical=False):
    if getattr(ctx.host, "ontology_mode", "legacy") == "canonical":
        from workspace.ontology_workbench import graph as canonical_graph
        return canonical_graph(ctx, target_id, historical=historical)
    return legacy_graph(ctx, target_id)


def legacy_graph(ctx, target_id=None, *, selected_ids=None):
    """Explicit migration/reference reader; not the canonical-mode write authority."""
    manifest, active, coverage = projections(ctx)
    observed_role = ctx.scope["role"]
    nodes, edges, conflicts = {}, [], set()
    parts = []
    for source, binding in active:
        parts.append((source, ctx.read_json(binding["graphKey"], binding["graphHash"])))
    declared = manifest.get("declared")
    if declared:
        try:
            verify_refs(ctx, [declared["sourceRef"]])
            parts.append((ctx.get("wb_source", declared["sourceRef"]["sourceId"]),
                          ctx.read_json(declared["graphKey"], declared["graphHash"])))
        except Exception:
            coverage["complete"] = False
    for source, part in parts:
        for node in part["nodes"]:
            if not visible(ctx, source, node["sourceRef"]):
                continue
            if node["id"] in nodes and nodes[node["id"]] != node:
                conflicts.add(node["id"])
            nodes[node["id"]] = node
        edges.extend(edge for edge in part["edges"] if visible(ctx, source, edge["sourceRef"]))
    nodes = {k: v for k, v in nodes.items() if k not in conflicts}
    edges = [edge for edge in edges if edge["src"] in nodes and edge["dst"] in nodes]
    edges = list({_hash(edge): edge for edge in edges}.values())
    scoped_boundary = False
    if selected_ids is not None:
        selected = set(selected_ids)
        if selected - nodes.keys():
            fail(409, "legacy-selection-unavailable", "선택한 기존 노드의 현재 원본을 확인하지 못했습니다.")
        scoped_boundary = any((edge["src"] in selected) != (edge["dst"] in selected) for edge in edges)
        nodes = {key: node for key, node in nodes.items() if key in selected}
        edges = [edge for edge in edges if edge["src"] in nodes and edge["dst"] in nodes]
    if target_id:
        _id(target_id)
        selected = {target_id}
        for edge in edges:
            if target_id in (edge["src"], edge["dst"]):
                selected.update((edge["src"], edge["dst"]))
        nodes = {k: v for k, v in nodes.items() if k in selected}
        edges = [edge for edge in edges if edge["src"] in nodes and edge["dst"] in nodes]
    truncated = len(nodes) > MAX_NODES or len(edges) > MAX_EDGES
    nodes = dict(sorted(nodes.items())[:MAX_NODES])
    edges = sorted((edge for edge in edges if edge["src"] in nodes and edge["dst"] in nodes),
                   key=lambda edge: (edge["src"], edge["rel"], edge["dst"]))[:MAX_EDGES]
    coverage = {**coverage, "complete": False,
                "unknown": ["unmapped-dependencies"] + (["outside-selected-legacy-scope"] if scoped_boundary else []),
                "conflictingNodeIds": len(conflicts), "truncated": truncated,
                "scope": "declared-or-connector-extracted-dependencies"}
    ctx.fresh()
    if ctx.scope["role"] != observed_role:
        fail(409, "authority-changed", "조회 중 프로젝트 역할이 변경되었습니다.")
    recheck_sources(ctx, active)
    return {"nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(edges, key=lambda e: (e["src"], e["rel"], e["dst"])),
            "generation": manifest.get("generation"), "coverage": coverage, "backend": BACKEND["graph"]}


def declare_graph(ctx, body):
    if getattr(ctx.host, "ontology_mode", "legacy") == "canonical":
        fail(409, "canonical-ontology-required", "관계 변경은 프로젝트 온톨로지 검토 API를 사용하세요.")
    fields(body, {"requestId", "nodes", "edges", "sourceRef"})
    ctx.fresh()
    ref = body.get("sourceRef")
    checks = verify_refs(ctx, [ref])
    docs = canonicalize([{"id": "mapping", "title": "선언된 의존 관계", "revision": "1", "content": "",
                          "entities": body.get("nodes"), "relations": body.get("edges")}])
    nodes = [{**node, "sourceRef": ref} for node in docs[0]["entities"]]
    edges = [{**edge, "sourceRef": ref} for edge in docs[0]["relations"]]
    artifact = ctx.identity("wb_index", body.get("requestId"))
    request_hash = _hash(body)
    previous = ctx.storage.get(ctx.owner, "wb_index", artifact)
    if previous:
        if previous.get("requestHash") != request_hash:
            fail(409, "request-changed", "같은 요청 ID에 다른 관계가 지정되었습니다.")
        return {"generation": previous["generation"], "coverage": {"complete": False, "provenance": "declared"}}
    key, digest = ctx.put_json("wb_index", artifact, "graph.json", {"nodes": nodes, "edges": edges})
    current = ctx.storage.get(ctx.owner, "wb_index", "current") or {}
    declared = {"sourceRef": ref, "graphKey": key, "graphHash": digest, "provenance": "declared"}
    generation = _hash({"sources": current.get("sources", {}), "declared": declared})
    record = {**current, "id": "current", "projectId": ctx.project_id, "declared": declared, "generation": generation}
    manifest_key, manifest_hash = ctx.put_json("wb_index", "current", generation + "/manifest.json",
        {"generation": generation, "sources": current.get("sources", {}), "declared": declared})
    record.update(manifestKey=manifest_key, manifestHash=manifest_hash)
    marker = {"id": artifact, "projectId": ctx.project_id, "requestHash": request_hash, "generation": generation}
    ctx.commit([ctx.write("wb_index", record, current.get("version")), ctx.write("wb_index", marker)], checks)
    return {"generation": generation, "coverage": {"complete": False, "provenance": "declared"}}


def recheck_sources(ctx, active):
    """Catch permission/source changes that occurred while reading private blobs."""
    for previous, binding in active:
        current = ctx.get("wb_source", previous["id"])
        if current["version"] != previous["version"] or not source_current(ctx, current, binding):
            fail(409, "source-changed", "조회 중 소스 또는 접근 권한이 변경되었습니다.")
