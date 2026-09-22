"""Trusted source-analysis adapter and source-bound canonical projection."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources, asset_reference

ANALYZER_ROOT = Path(__file__).resolve().parents[1] / "source-analyzer"
KINDS = {".ts": "code", ".tsx": "code", ".js": "code", ".jsx": "code",
         ".css": "style", ".scss": "style", ".json": "json",
         ".html": "html", ".htm": "html",
         ".png": "asset", ".jpg": "asset", ".jpeg": "asset", ".svg": "asset",
         ".woff": "asset", ".woff2": "asset"}


def source_input(ctx, items, resolver=None):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        fail(422, "ontology-analysis-limit", "분석 단위는 파일 1~100개로 구성하세요.")
    reader, files, bindings = Sources(ctx), [], {}
    total, folded = 0, set()
    for item in items:
        schema._fields(item, {"assetId", "path"})
        schema._identifier(item["assetId"])
        file = item["path"]
        if isinstance(file, str):
            try:
                file.encode("utf-8")
            except UnicodeError:
                fail(400, "ontology-analysis-path", "파일 경로는 올바른 UTF-8 문자여야 합니다.")
        if (not isinstance(file, str) or len(file) > 500 or len(file.encode()) > 1024
                or file != unicodedata.normalize("NFC", file) or file.startswith("/")
                or any(c in "\\:?#" or ord(c) < 32 or ord(c) == 127 for c in file)
                or any(part in {"", ".", ".."} for part in file.split("/")) or file.lower() in folded):
            fail(400, "ontology-analysis-path", "파일 경로가 올바르지 않거나 중복됩니다.")
        folded.add(file.lower())
        kind = KINDS.get(Path(file).suffix.lower())
        if kind is None:
            fail(422, "ontology-analysis-format", "지원되는 코드·스타일·이미지 원본을 선택하세요.")
        record = ctx.get("asset", item["assetId"])
        if Path(record["name"]).suffix.lower() != Path(file).suffix.lower():
            fail(400, "ontology-analysis-path", "원본 확장자를 다른 실행 유형으로 바꿀 수 없습니다.")
        ref = asset_reference(record)
        value = reader.resolve(ref, text=kind != "asset")
        entry = {"path": file, "kind": kind, "sha256": record["sha256"]}
        if kind != "asset":
            entry["text"] = value["text"]
            if len(value["text"].encode()) > 102400:
                fail(422, "ontology-analysis-limit", "각 분석 파일의 텍스트는 100 KiB 이하여야 합니다.")
            total += len(value["text"].encode())
        files.append(entry)
        bindings[file] = {"ref": ref, "record": record, "kind": kind}
    if total > 2097152:
        fail(422, "ontology-analysis-limit", "분석 단위의 텍스트는 총 2 MiB 이하여야 합니다.")
    reader.recheck()
    return {"schemaVersion": 1, "files": files, "resolver": resolver or {
        "aliases": {}, "packages": {}, "jsonAssetFields": []}}, bindings


def local_analyze(payload):
    """Explicit offline development adapter; never selected as a cloud fallback."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("The offline analyzer requires the pinned Node toolchain")
    raw = json.dumps(payload, ensure_ascii=False).encode()
    if len(raw) > 4_000_000:
        raise ValueError("Analysis input exceeds the transport limit")
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LD_LIBRARY_PATH") if key in os.environ}
    with tempfile.TemporaryDirectory(prefix="ontology-analysis-") as directory:
        request, output = Path(directory) / "input.json", Path(directory) / "output.json"
        request.write_bytes(raw)
        environment["TMPDIR"] = directory
        result = subprocess.run([node, "--max-old-space-size=384", str(ANALYZER_ROOT / "analyze.cjs"),
                                 str(request), str(output)], cwd=ANALYZER_ROOT, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
        if result.returncode or not output.is_file() or output.stat().st_size > 4_000_000:
            raise ValueError("Offline source analysis did not produce valid evidence")
        value = json.loads(output.read_bytes())
    return {"analysis": value, "execution": {"backend": "local-offline", "sourceExecuted": False,
        "inputHash": schema.digest(payload),
        "analyzerCodeHash": hashlib.sha256((ANALYZER_ROOT / "analyze.cjs").read_bytes()).hexdigest(),
        "dependencyLockHash": hashlib.sha256((ANALYZER_ROOT / "package-lock.json").read_bytes()).hexdigest()}}


local_analyze.backend = "local-offline"


def validate_execution(value):
    schema._fields(value, {"backend", "sourceExecuted", "inputHash"}, {"analyzerCodeHash", "dependencyLockHash", "toolArchiveHash",
        "interpreterId", "sessionId", "requestId", "nodeVersion", "architecture", "region", "elapsedMs"})
    if value["backend"] not in {"local-offline", "agentcore-code-interpreter"} or value["sourceExecuted"] is not False:
        fail(503, "ontology-analysis-incomplete", "입력 소스를 실행하지 않은 분석 근거가 필요합니다.")
    for key in ("inputHash", "analyzerCodeHash", "dependencyLockHash", "toolArchiveHash"):
        if key in value:
            schema._hash(value[key])
    if value["backend"] == "agentcore-code-interpreter":
        for key in ("interpreterId", "sessionId", "nodeVersion", "architecture", "region"):
            schema._text(value.get(key), 256)
        schema._hash(value.get("toolArchiveHash"))
    if "elapsedMs" in value and (type(value["elapsedMs"]) is not int or value["elapsedMs"] < 0):
        fail(503, "ontology-analysis-incomplete", "분석 실행 시간이 올바르지 않습니다.")
    return value


def validate_analysis(payload, value):
    schema._fields(value, {"schemaVersion", "analyzer", "inputHash", "resolverHash", "references",
                           "exports", "unresolved", "diagnostics", "coverage", "hash"})
    expected = [{key: file[key] for key in ("path", "sha256")} for file in payload["files"]]
    expected.sort(key=lambda file: file["path"])
    if (value["schemaVersion"] != 1 or value["inputHash"] != schema.digest(expected)
            or value["resolverHash"] != schema.digest(payload["resolver"])
            or value["hash"] != schema.digest({k: v for k, v in value.items() if k != "hash"})
            or value["analyzer"] != {"name": "platform-source-analyzer", "version": "1.0.0", "typescript": "5.6.3"}):
        fail(409, "ontology-analysis-integrity", "분석 결과가 선택한 원본·분석기와 일치하지 않습니다.")
    for key in ("references", "exports", "unresolved", "diagnostics"):
        if not isinstance(value[key], list) or len(value[key]) > 4000:
            fail(422, "ontology-analysis-limit", "분석 결과의 항목 수가 제한을 초과했습니다.")
    files = {file["path"]: file for file in payload["files"]}
    for item in value["references"]:
        if (not isinstance(item, dict) or item.get("path") not in files
                or item.get("sourceHash") != files[item["path"]]["sha256"]
                or not isinstance(item.get("resolution"), dict)):
            fail(409, "ontology-analysis-integrity", "분석 참조의 원본을 확인하지 못했습니다.")
        resolved = item["resolution"]
        if resolved.get("status") == "resolved-local" and (
                resolved.get("targetPath") not in files
                or resolved.get("targetHash") != files[resolved["targetPath"]]["sha256"]):
            fail(409, "ontology-analysis-integrity", "분석 대상 파일의 해시가 일치하지 않습니다.")
    return value


def project_analysis(ctx, name, payload, bindings, analysis):
    value = validate_analysis(payload, analysis)
    nodes, edges, file_ids, components = {}, {}, {}, {}
    owner_scope = {"kind": "project", "projectId": ctx.project_id}

    def add_node(identifier, kind, title, refs, props, subtype=None):
        row = {"id": identifier, "scope": owner_scope, "type": kind, "title": title[:300], "revision": 1,
               "sourceRefs": refs, "provenance": "parser-extracted", "reviewState": "candidate",
               "tombstone": False, "properties": props}
        if subtype:
            row["subtype"] = subtype
        nodes[identifier] = schema.seal(row)
        return identifier

    def add_edge(src, dst, kind, refs, properties=None):
        identifier = schema.identity("relation", name, src, dst, kind, properties or {})
        edges[identifier] = schema.seal({"id": identifier, "type": kind, "src": {"id": src, "revision": 1},
            "dst": {"id": dst, "revision": 1}, "sourceRefs": refs, "provenance": "parser-extracted",
            "reviewState": "candidate", "tombstone": False, "properties": properties or {}})

    for file in payload["files"]:
        ref, record = bindings[file["path"]]["ref"], bindings[file["path"]]["record"]
        identifier = schema.identity("file", name, file["path"])
        file_ids[file["path"]] = identifier
        if file["kind"] == "asset":
            add_node(identifier, "Foundation", file["path"], [ref], {"path": file["path"]}, "graphic")
        else:
            add_node(identifier, "CodeFile", file["path"], [ref], {
                "path": file["path"], "language": Path(file["path"]).suffix[1:],
                "fileHash": file["sha256"], "collectionId": name})
    for item in value["exports"]:
        if item.get("path") not in bindings or not isinstance(item.get("name"), str):
            fail(409, "ontology-analysis-integrity", "내보내기 기호의 원본이 올바르지 않습니다.")
        ref = {**bindings[item["path"]]["ref"], "location": {
            "path": item["path"], "exportName": item["name"], "line": item["line"], "column": item["column"]}}
        symbol_id = add_node(schema.identity("symbol", name, item["path"], item["name"]), "CodeSymbol",
                 item["name"], [ref], {"path": item["path"], "exportName": item["name"], "fileId": file_ids[item["path"]]})
        add_edge(file_ids[item["path"]], symbol_id, "IMPLEMENTS", [ref])
    for item in value["references"]:
        resolution = item["resolution"]
        if resolution["status"] != "resolved-local":
            continue
        source, target = file_ids[item["path"]], file_ids[resolution["targetPath"]]
        reference = {**bindings[item["path"]]["ref"], "location": {
            "path": item["path"], "line": item["line"], "column": item["column"]}}
        properties = {"line": item["line"], "column": item["column"], "resolution": "literal-file-manifest"}
        if item.get("conditional"):
            properties["conditional"] = True
        add_edge(source, target, "IMPORTS" if "import" in item["kind"] else "REFERENCES", [reference], properties)
        if item["kind"] == "jsx-use" and bindings[resolution["targetPath"]]["kind"] == "code":
            key = resolution["targetPath"], item.get("symbol", "default")
            if key not in components:
                target_ref = {**bindings[key[0]]["ref"], "location": {"path": key[0], "exportName": key[1]}}
                components[key] = add_node(schema.identity("component", name, *key), "Component",
                    key[1] if key[1] != "default" else Path(key[0]).stem,
                    [target_ref], {"componentName": key[1], "sourcePath": key[0],
                                   "description": "JSX source reference; implementation and design level require review."})
                add_edge(target, components[key], "IMPLEMENTS", [target_ref])
                symbol_id = schema.identity("symbol", name, *key)
                if symbol_id in nodes:
                    add_edge(components[key], symbol_id, "USES", [target_ref])
            add_edge(source, components[key], "USES", [reference],
                     {"line": item["line"], "column": item["column"]})
    reasons = sorted({item["reason"] for item in value["unresolved"]} |
                     {"unreviewed-design-mappings", "outside-source-unit-not-certified"})
    if any(item["resolution"]["status"] == "approved-package" for item in value["references"]):
        reasons.append("package-dependencies-not-projected")
    if value["diagnostics"]:
        reasons.append("parser-errors")
    graph = {"schemaVersion": 1, "projectId": ctx.project_id, "nodes": list(nodes.values()), "edges": list(edges.values()),
             "coverage": {"complete": False, "scope": "static-source-unit", "truncated": value["coverage"]["truncated"],
                          "unknown": reasons}}
    if (len(nodes) > schema.MAX_NODES or len(edges) > schema.MAX_EDGES
            or len(json.dumps(graph, ensure_ascii=False, separators=(",", ":")).encode()) > 4_000_000):
        fail(422, "ontology-analysis-scope", "분석 결과가 게시 한도를 초과했습니다. 소스 파일 묶음을 나누어 다시 분석하세요.")
    return schema.validate_graph(graph)
