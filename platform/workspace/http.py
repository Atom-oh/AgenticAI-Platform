"""Private HTTP API for uploads, approved contracts, and verified run artifacts.

API Gateway's JWT authorizer supplies identity. No owner, S3 key, approval, or
worker status supplied by a request body is trusted. Uploaded programs are data.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import uuid
from urllib.parse import quote

from workspace.storage import Conflict, Storage, key_for

MAX_FILE_BYTES = 50 * 1024 * 1024
CHUNK_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
STALE_JOB_MS = 16 * 60 * 1000
EXTENSIONS = ("html", "htm", "png", "jpg", "jpeg", "svg", "pdf", "fig", "md", "markdown", "txt", "json", "css")
PURPOSES = frozenset({"reference", "component", "token", "skill", "guide", "prototype", "archive"})
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_EDITABLE = ("schemaVersion", "title", "brief", "assetIds", "viewport", "rules", "unresolved", "bindings")
_BASE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class HTTPError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise HTTPError(400, "invalid-input", f"{label} must be an integer between {low} and {high}")
    return value


def _text(value, label, maximum, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise HTTPError(400, "invalid-input", f"Invalid {label}")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise HTTPError(400, "invalid-input", f"Invalid {label}") from error
    return value


def _public(value):
    """Do not publish storage addresses or the private upload manifest."""
    if isinstance(value, dict):
        visible = {key: _public(item) for key, item in value.items()
                   if key not in ("pk", "sk", "owner", "parts", "key", "requestHash")
                   and not key.endswith("Key")}
        for kind in ("html", "screenshot", "diff", "report"):
            if kind + "Key" in value:
                visible["has" + kind.capitalize()] = bool(value[kind + "Key"])
        return visible
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _json(status, payload):
    return {"statusCode": status, "headers": {**_BASE_HEADERS, "Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(_public(payload), ensure_ascii=False, allow_nan=False), "isBase64Encoded": False}


def _body(event, binary=False):
    body = event.get("body") or ""
    maximum = CHUNK_BYTES if binary else MAX_JSON_BYTES
    if not isinstance(body, (str, bytes)):
        raise HTTPError(400, "invalid-body", "Invalid request body")
    if len(body) > (4 * ((maximum + 2) // 3) if event.get("isBase64Encoded") else maximum):
        raise HTTPError(413, "too-large", "Request body exceeds the size limit")
    try:
        if event.get("isBase64Encoded"):
            data = base64.b64decode(body, validate=True)
        else:
            data = body.encode("utf-8") if isinstance(body, str) else body
        if len(data) > maximum:
            raise HTTPError(413, "too-large", "Request body exceeds the size limit")
        if binary:
            return data
        value = json.loads(data.decode("utf-8")) if data else {}
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise HTTPError(400, "invalid-body", "Invalid request body") from error


class WorkspaceAPI:
    def __init__(self, storage=None, lambda_client=None, worker_fn=None, rules=None):
        self.storage = storage if storage is not None else Storage()
        self.lambda_client = lambda_client
        self.worker_fn = worker_fn if worker_fn is not None else os.environ.get("WORKSPACE_WORKER_FN", "")
        self._rules = rules

    def rules(self):
        if self._rules is None:
            from workspace import rules
            self._rules = rules
        return self._rules

    def handle(self, event, context=None):
        try:
            context_data = event.get("requestContext") or {}
            authorizer = context_data.get("authorizer")
            jwt = authorizer.get("jwt") if isinstance(authorizer, dict) else None
            claims = jwt.get("claims") if isinstance(jwt, dict) else None
            claims = claims if isinstance(claims, dict) else {}
            owner = claims.get("sub")
            if (not isinstance(owner, str) or not owner.strip() or len(owner) > 256
                    or claims.get("token_use") != "access"):
                raise HTTPError(401, "unauthorized", "An authorized access token is required")
            method = (context_data.get("http") or {}).get("method", event.get("httpMethod", "")).upper()
            path = event.get("rawPath") or event.get("path") or ""
            if path == "/studio-api":
                path = "/"
            elif path.startswith("/studio-api/"):
                path = path[len("/studio-api"):]
            if not path.startswith("/") or "//" in path:
                raise HTTPError(404, "not-found", "Resource not found")
            segments = path.strip("/").split("/")
            query = event.get("queryStringParameters") or {}
            return self._route(owner, method, segments, event, query)
        except HTTPError as error:
            return _json(error.status, {"error": error.message, "code": error.code})
        except Conflict:
            return _json(409, {"error": "The resource has changed; reload and retry.", "code": "conflict"})
        except FileNotFoundError:
            return _json(404, {"error": "The requested private artifact is not available.", "code": "not-found"})
        except ValueError:
            return _json(400, {"error": "Invalid request or resource data.", "code": "invalid-input"})
        except Exception:
            # Never return or log SDK messages, prompts, credentials, or tokens.
            return _json(503, {"error": "Workspace operation is unavailable. Retry or inspect the job.", "code": "unavailable"})

    def _get(self, owner, kind, identifier):
        record = self.storage.get(owner, kind, identifier)
        if record is None:
            raise HTTPError(404, "not-found", "Resource not found")
        return record

    def _expire_job(self, owner, job):
        """One CAS attempt per read; a concurrent heartbeat or completion wins."""
        updated = job.get("updatedAt")
        now = self.storage.clock()
        if (job.get("status") not in ("queued", "running") or type(updated) is not int
                or now - updated <= STALE_JOB_MS):
            return job
        try:
            return self.storage.put(owner, "job", {
                **job, "status": "failed", "stopReason": "timeout", "errorCode": "job-timeout",
                "error": "작업 진행이 16분 이상 갱신되지 않아 종료했습니다. 다시 실행해 주세요.",
                "finishedAt": now,
            }, job["version"])
        except Conflict:
            return self._get(owner, "job", job["id"])

    def _blob_key(self, owner, key):
        if not key or not self.storage.owns_key(owner, key):
            raise HTTPError(404, "not-found", "Private artifact not found")
        return key

    def _route(self, owner, method, parts, event, query):
        from engine import model_catalog
        if method == "GET" and parts == ["config"]:
            return _json(200, {"models": model_catalog.options(), "defaultModel": model_catalog.resolve(),
                               "maxFileBytes": MAX_FILE_BYTES, "chunkBytes": CHUNK_BYTES, "extensions": list(EXTENSIONS)})
        if len(parts) == 1 and parts[0] in ("assets", "contracts", "runs") and method == "GET":
            kind = {"assets": "asset", "contracts": "contract", "runs": "run"}[parts[0]]
            page = self.storage.list_page(owner, kind, limit=100, cursor=query.get("cursor"))
            return _json(200, {parts[0]: page["items"], **({"cursor": page["cursor"]} if page.get("cursor") else {})})
        if parts == ["assets"] and method == "POST":
            return self._create_asset(owner, _body(event))
        if parts == ["contracts", "propose"] and method == "POST":
            return self._propose(owner, _body(event))
        if parts == ["contracts"] and method == "POST":
            return self._contract_create(owner, _body(event))
        if parts == ["runs"] and method == "POST":
            return self._run_create(owner, _body(event))
        if len(parts) < 2 or parts[0] not in ("assets", "contracts", "runs", "jobs"):
            raise HTTPError(404, "not-found", "Route not found")
        kind = {"assets": "asset", "contracts": "contract", "runs": "run", "jobs": "job"}[parts[0]]
        record = self._get(owner, kind, parts[1])
        if len(parts) == 2 and method == "GET":
            if kind == "job":
                record = self._expire_job(owner, record)
            payload = {kind: record}
            if kind == "asset" and record.get("analysisKey"):
                key = self._blob_key(owner, record["analysisKey"])
                if self.storage.blob_info(key)["size"] > MAX_JSON_BYTES:
                    raise HTTPError(413, "analysis-too-large", "Analysis exceeds the response limit")
                payload["analysis"] = json.loads(self.storage.get_blob(key))
            return _json(200, payload)
        if kind == "asset":
            if len(parts) == 4 and parts[2] == "parts" and method == "PUT":
                if not re.fullmatch(r"0|[1-9][0-9]{0,2}", parts[3]):
                    raise HTTPError(400, "invalid-part", "Invalid part index")
                return self._upload_part(owner, record, int(parts[3]), _body(event, binary=True))
            if len(parts) == 3 and parts[2] == "complete" and method == "POST":
                _body(event)
                return self._complete(owner, record)
            if len(parts) == 2 and method == "DELETE":
                if not record.get("archived"):
                    record = self.storage.put(owner, kind, {**record, "archived": True}, record["version"])
                return _json(200, {"asset": record})
        if kind == "contract" and len(parts) == 2 and method == "PUT":
            return self._contract_edit(owner, record, _body(event))
        if len(parts) == 3 and parts[2] == "approve" and method == "POST":
            if kind == "contract":
                return self._contract_approve(owner, record, _body(event))
            if kind == "run":
                return self._run_approve(owner, record, _body(event))
        if len(parts) == 3 and parts[2] == "blob" and method == "GET" and kind in ("asset", "run"):
            return self._download(owner, kind, record, query)
        raise HTTPError(404, "not-found", "Route not found")

    def _create_asset(self, owner, body):
        name = _text(body.get("name"), "file name", 180)
        if any(c in name for c in "\x00\r\n/\\") or name.rsplit(".", 1)[-1].lower() not in EXTENSIONS or "." not in name:
            raise HTTPError(400, "unsupported-extension", "Choose a supported file extension")
        size = body.get("size")
        if type(size) is int and size > MAX_FILE_BYTES:
            raise HTTPError(413, "too-large", "Files must be at most 50 MiB")
        size = _integer(size, "File size", 0, MAX_FILE_BYTES)
        digest = body.get("sha256")
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise HTTPError(400, "invalid-hash", "A lowercase SHA256 file hash is required")
        purpose = body.get("purpose")
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise HTTPError(400, "invalid-purpose", "Choose a supported file purpose")
        parent_id = body.get("parentId")
        parent = None
        if parent_id is not None:
            parent = self._get(owner, "asset", parent_id)
        identifier = uuid.uuid4().hex
        import_revision, lineage_id = 1, identifier
        if parent is not None:
            # Older intake records have no import revision. Their CAS version
            # is never evidence of how many times the file was imported.
            prior_revision = parent.get("importRevision", 1)
            if type(prior_revision) is not int or not 1 <= prior_revision < 2**53 - 1:
                raise HTTPError(409, "invalid-lineage", "The parent import revision is unavailable")
            import_revision = prior_revision + 1
            lineage_id = parent.get("lineageId") or parent["id"]
        asset = self.storage.put(owner, "asset", {
            "id": identifier, "name": name, "size": size, "sha256": digest, "purpose": purpose,
            "importRevision": import_revision, "lineageId": lineage_id, "parentId": parent_id,
            "status": "uploading", "uploadStatus": "uploading", "parseStatus": "pending",
            "partCount": (size + CHUNK_BYTES - 1) // CHUNK_BYTES, "parts": {},
            "originalKey": key_for(owner, "asset", identifier, "original"), "previews": [], "warnings": [], "archived": False,
        })
        return _json(201, {"asset": asset, "chunkBytes": CHUNK_BYTES})

    def _upload_part(self, owner, asset, index, data):
        identifier = asset["id"]
        digest = hashlib.sha256(data).hexdigest()
        expected = min(CHUNK_BYTES, asset["size"] - index * CHUNK_BYTES)
        if index >= asset["partCount"] or len(data) != expected:
            raise HTTPError(409, "part-size-mismatch", "Part index or size does not match the declared file")
        slot = str(index)
        for _ in range(8):
            if asset.get("uploadStatus") != "uploading" or asset.get("archived"):
                raise HTTPError(409, "upload-closed", "This upload no longer accepts parts")
            previous = asset.get("parts", {}).get(slot)
            if previous and (previous["sha256"] != digest or previous["size"] != len(data)):
                raise HTTPError(409, "part-changed", "Retry bytes differ from the original part")
            if previous and previous.get("status") == "stored":
                return _json(200, {"asset": asset, "index": index, "sha256": digest})
            if previous:
                break
            part = {"key": key_for(owner, "asset", identifier, f"parts/{index}-{digest}"),
                    "sha256": digest, "size": len(data), "status": "uploading"}
            try:
                asset = self.storage.put(owner, "asset", {**asset, "parts": {**asset.get("parts", {}), slot: part}},
                                         asset["version"])
                break
            except Conflict:
                asset = self._get(owner, "asset", identifier)
        else:
            raise Conflict("Upload manifest is busy")
        part = asset["parts"][slot]
        self.storage.put_blob_once(part["key"], data, "application/octet-stream")
        for _ in range(8):
            asset = self._get(owner, "asset", identifier)
            if asset.get("uploadStatus") != "uploading" or asset.get("archived"):
                raise HTTPError(409, "upload-closed", "This upload no longer accepts parts")
            part = asset["parts"][slot]
            if part["sha256"] != digest:
                raise Conflict("Part changed")
            if part["status"] == "stored":
                return _json(200, {"asset": asset, "index": index, "sha256": digest})
            try:
                asset = self.storage.put(owner, "asset", {
                    **asset, "parts": {**asset["parts"], slot: {**part, "status": "stored"}}}, asset["version"])
                return _json(200, {"asset": asset, "index": index, "sha256": digest})
            except Conflict:
                continue
        raise Conflict("Upload manifest is busy")

    def _worker_ready(self):
        if not self.worker_fn:
            raise HTTPError(503, "worker-unavailable", "Workspace worker is not configured")

    def _invoke(self, owner, job):
        if job["status"] != "queued":
            return
        self._worker_ready()
        try:
            if self.lambda_client is None:
                import boto3
                self.lambda_client = boto3.client("lambda", config=Storage._config())
            result = self.lambda_client.invoke(FunctionName=self.worker_fn, InvocationType="Event",
                                               Payload=json.dumps({"owner": owner, "jobId": job["id"]}).encode())
            if result.get("StatusCode") != 202:
                raise RuntimeError("Worker did not accept the job")
        except Exception:
            current = self.storage.get(owner, "job", job["id"])
            if current and current["status"] == "queued":
                try:
                    self.storage.put(owner, "job", {**current, "status": "failed",
                                     "error": "Worker invocation failed"}, current["version"])
                    kind = {"finalize": "asset", "run": "run"}.get(current["task"])
                    target_id = current["input"].get("assetId" if kind == "asset" else "runId")
                    target = self.storage.get(owner, kind, target_id) if kind and target_id else None
                    if target and target.get("status") in ("queued", "processing"):
                        update = {"status": "failed", "error": "Worker invocation failed"}
                        if kind == "asset":
                            update["uploadStatus"] = "failed"
                        self.storage.put(owner, kind, {**target, **update}, target["version"])
                except Conflict:
                    pass
            raise HTTPError(503, "worker-unavailable", "Worker invocation failed; inspect the job before retrying")

    def _new_job(self, owner, identifier, task, data, request_hash=None):
        record = {"id": identifier, "task": task, "input": data, "status": "queued", "progress": 0}
        if request_hash:
            record["requestHash"] = request_hash
        try:
            return self.storage.put(owner, "job", record)
        except Conflict:
            existing = self._get(owner, "job", identifier)
            if existing.get("requestHash") != request_hash or existing["task"] != task:
                raise HTTPError(409, "request-changed", "The request ID was already used with different input")
            return existing

    def _complete(self, owner, asset):
        if asset.get("archived"):
            raise HTTPError(409, "archived", "Archived files cannot be finalized")
        if asset.get("jobId") and asset["uploadStatus"] in ("processing", "stored", "failed"):
            job = self.storage.get(owner, "job", asset["jobId"])
            if asset["uploadStatus"] == "processing":
                self._worker_ready()
                job = job or self._new_job(owner, asset["jobId"], "finalize", {"assetId": asset["id"]})
                self._invoke(owner, job)
            if not job:
                raise HTTPError(409, "job-unavailable", "Finalization job is unavailable")
            return _json(202, {"asset": asset, "job": job})
        if asset.get("uploadStatus") != "uploading":
            raise HTTPError(409, "upload-closed", "Upload cannot be finalized")
        self._worker_ready()
        parts = asset.get("parts", {})
        for index in range(asset["partCount"]):
            part = parts.get(str(index), {})
            if part.get("status") != "stored":
                raise HTTPError(409, "parts-missing", "Upload every part before finalizing")
            info = self.storage.blob_info(self._blob_key(owner, part["key"]))
            if info["sha256"] != part["sha256"] or info["size"] != min(CHUNK_BYTES, asset["size"] - index * CHUNK_BYTES):
                raise HTTPError(409, "part-changed", "A stored part no longer matches the upload manifest")
        job_id = f"finalize-{asset['id']}"
        try:
            asset = self.storage.put(owner, "asset", {
                **asset, "jobId": job_id, "status": "processing", "uploadStatus": "processing"}, asset["version"])
        except Conflict:
            asset = self._get(owner, "asset", asset["id"])
            if asset.get("jobId") != job_id or asset.get("uploadStatus") != "processing":
                raise
        job = self._new_job(owner, job_id, "finalize", {"assetId": asset["id"]})
        self._invoke(owner, job)
        return _json(202, {"asset": asset, "job": job})

    def _assets(self, owner, identifiers):
        if (not isinstance(identifiers, list) or len(identifiers) > 20
                or any(not isinstance(identifier, str) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise HTTPError(400, "invalid-assets", "Select at most 20 distinct assets")
        assets = [self._get(owner, "asset", identifier) for identifier in identifiers]
        for asset in assets:
            if asset.get("archived") or asset.get("uploadStatus") != "stored":
                raise HTTPError(409, "asset-not-ready", "Selected files must be stored and not archived")
            info = self.storage.blob_info(self._blob_key(owner, asset.get("originalKey")))
            if info["size"] != asset["size"] or info["sha256"] != asset["sha256"]:
                raise HTTPError(409, "asset-changed", "Selected file bytes do not match the stored identity")
        return assets

    def _asset_texts(self, owner, assets):
        texts = {}
        for asset in assets:
            text = ""
            if asset.get("analysisKey"):
                key = self._blob_key(owner, asset["analysisKey"])
                if self.storage.blob_info(key)["size"] > MAX_JSON_BYTES:
                    raise HTTPError(413, "analysis-too-large", "Extracted analysis exceeds the contract limit")
                analysis = json.loads(self.storage.get_blob(key))
                text = analysis.get("text", "")
                if not isinstance(text, str):
                    raise HTTPError(409, "invalid-analysis", "Extracted source text is unavailable")
            texts[asset["id"]] = text
        return texts

    def _validated_contract(self, owner, data):
        assets = self._assets(owner, data.get("assetIds", []))
        try:
            normalized = self.rules().validate_contract(data, asset_texts=self._asset_texts(owner, assets))
        except ValueError as error:
            raise HTTPError(400, "invalid-contract", str(error)[:240]) from error
        return normalized, assets

    @staticmethod
    def _snapshot_assets(assets):
        fields = ("id", "version", "importRevision", "lineageId", "parentId", "sha256", "name", "size",
                  "purpose", "originalKey", "analysisKey", "previews", "parseStatus")
        return [{key: asset[key] for key in fields if key in asset} for asset in assets]

    def _contract_create(self, owner, body):
        normalized, _ = self._validated_contract(owner, {key: body[key] for key in _EDITABLE if key in body})
        record = self.storage.put(owner, "contract", {**normalized, "id": uuid.uuid4().hex, "status": "draft"})
        return _json(201, {"contract": record})

    def _contract_edit(self, owner, record, body):
        version = _integer(body.get("version"), "Version", 1, 2**53 - 1)
        if version != record["version"]:
            raise Conflict("Contract changed")
        editable = {key: body.get(key, record.get(key)) for key in _EDITABLE if key in body or key in record}
        normalized, _ = self._validated_contract(owner, editable)
        revisions = list(record.get("revisions", []))
        if record.get("status") == "approved":
            digest = self.rules().contract_hash(record)
            key = key_for(owner, "contract", record["id"], f"revisions/{version}-{digest}.json")
            self.storage.put_blob_once(key, json.dumps(record, ensure_ascii=False, sort_keys=True).encode(), "application/json")
            revisions.append({"version": version, "hash": digest, "key": key})
        result = self.storage.put(owner, "contract", {
            **normalized, "id": record["id"], "status": "draft", "revisions": revisions}, version)
        return _json(200, {"contract": result})

    def _contract_approve(self, owner, record, body):
        version = _integer(body.get("version"), "Version", 1, 2**53 - 1)
        if record["version"] != version:
            raise Conflict("Contract changed")
        normalized, _ = self._validated_contract(owner, record)
        if normalized.get("unresolved") or not normalized.get("rules"):
            raise HTTPError(409, "contract-unresolved", "Resolve every requirement before approving")
        digest = self.rules().contract_hash(normalized)
        if record.get("status") == "approved" and (record.get("approval") or {}).get("hash") == digest:
            return _json(200, {"contract": record})
        result = self.storage.put(owner, "contract", {
            **record, **normalized, "status": "approved",
            "approval": {"version": version + 1, "hash": digest, "actor": owner, "at": self.storage.clock()},
        }, version)
        return _json(200, {"contract": result})

    @staticmethod
    def _request_id(body, task):
        request_id = body.get("requestId")
        if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
            raise HTTPError(400, "invalid-request-id", "A stable requestId is required")
        return f"{task}-{hashlib.sha256(request_id.encode()).hexdigest()}"

    @staticmethod
    def _fingerprint(data):
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def _existing_job(self, owner, identifier, fingerprint):
        job = self.storage.get(owner, "job", identifier)
        if job and job.get("requestHash") != fingerprint:
            raise HTTPError(409, "request-changed", "The request ID was already used with different input")
        return job

    def _propose(self, owner, body):
        from engine import model_catalog
        identifier = self._request_id(body, "propose")
        data = {"assetIds": body.get("assetIds", []), "brief": _text(body.get("brief", ""), "brief", 4000, empty=True),
                "model": model_catalog.resolve(body.get("model"))}
        fingerprint = self._fingerprint(data)
        job = self._existing_job(owner, identifier, fingerprint)
        if not job:
            self._worker_ready()
            assets = self._assets(owner, data["assetIds"])
            job = self._new_job(owner, identifier, "propose", {
                **data, "assetSnapshots": self._snapshot_assets(assets)}, fingerprint)
        self._invoke(owner, job)
        return _json(202, {"job": job})

    def _run_create(self, owner, body):
        from engine import model_catalog
        identifier = self._request_id(body, "run")
        model = model_catalog.resolve(body.get("model"))
        mode = body.get("mode", "generate")
        if mode not in ("generate", "verify"):
            raise HTTPError(400, "invalid-mode", "생성 또는 원본 검사 모드를 선택하세요.")
        maximum = 1 if mode == "verify" else _integer(body.get("maxRounds", 3), "Rounds", 1, 5)
        version = _integer(body.get("contractVersion"), "Contract version", 1, 2**53 - 1)
        variant = body.get("variant", "balanced")
        if variant not in ("balanced", "dense", "emphasis", "flow"):
            raise HTTPError(400, "invalid-variant", "Choose a supported design variant")
        tolerance = body.get("visualTolerance", 0.15)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0 <= tolerance <= 0.5:
            raise HTTPError(400, "invalid-tolerance", "Visual tolerance must be between zero and 0.5")
        data = {"contractId": body.get("contractId"), "contractVersion": version, "model": model, "mode": mode,
                "maxRounds": maximum, "variant": variant, "visualTolerance": tolerance,
                "instruction": _text(body.get("instruction", ""), "instruction", 4000, empty=True)}
        for key in ("baseRunId", "baseRound", "referenceAssetId", "referencePage", "sourceAssetId"):
            if key in body:
                data[key] = body[key]
        fingerprint = self._fingerprint(data)
        job = self._existing_job(owner, identifier, fingerprint)
        if job:
            run = self._get(owner, "run", job["input"]["runId"])
            self._invoke(owner, job)
            return _json(202, {"job": job, "run": run})
        retained_run = self.storage.get(owner, "run", identifier)
        if retained_run and retained_run.get("status") != "queued":
            raise HTTPError(409, "request-expired",
                            "The operational job expired. Open the stored run or start a new request.")
        self._worker_ready()
        contract = self._get(owner, "contract", data["contractId"])
        normalized, assets = self._validated_contract(owner, contract)
        digest = self.rules().contract_hash(normalized)
        approval = contract.get("approval") or {}
        if (contract["version"] != version or contract.get("status") != "approved"
                or approval.get("version") != version or approval.get("hash") != digest
                or normalized.get("unresolved") or not normalized.get("rules")):
            raise HTTPError(409, "contract-not-approved", "Run the exact resolved and approved contract version")
        fields = {}
        if mode == "verify":
            source = next((asset for asset in assets if asset["id"] == data.get("sourceAssetId")), None)
            if not source:
                if data.get("sourceAssetId"):
                    self._get(owner, "asset", data["sourceAssetId"])
                raise HTTPError(409, "source-not-selected", "확정된 규칙에 포함된 HTML 파일을 선택하세요.")
            if (source["name"].rsplit(".", 1)[-1].lower() not in ("html", "htm")
                    or source.get("parseStatus") not in ("complete", "partial")):
                raise HTTPError(409, "source-unavailable", "해석 가능한 HTML 원본이 필요합니다.")
            if data.get("baseRunId") or "baseRound" in data:
                raise HTTPError(400, "invalid-base", "원본 검사와 이전 시안 수정은 별도로 실행하세요.")
            fields.update(sourceHtmlKey=self._blob_key(owner, source["originalKey"]),
                          sourceArtifactSha256=source["sha256"])
        elif data.get("sourceAssetId"):
            raise HTTPError(400, "invalid-source", "원본 검사 모드에서 HTML 파일을 지정하세요.")
        if "baseRound" in data and not data.get("baseRunId"):
            raise HTTPError(400, "invalid-base", "Select a base run for the requested round")
        if data.get("baseRunId"):
            base = self._get(owner, "run", data["baseRunId"])
            number = _integer(data.get("baseRound", base.get("bestRound")), "Base round", 1, 5)
            selected = next((row for row in base.get("rounds", []) if row.get("number") == number), None)
            if not selected or not selected.get("htmlKey") or not selected.get("artifactSha256"):
                raise HTTPError(409, "base-unavailable", "The selected base round has no stored artifact")
            fields.update(baseHtmlKey=self._blob_key(owner, selected["htmlKey"]),
                          baseArtifactSha256=selected["artifactSha256"], baseRound=number)
            if self.storage.blob_info(fields["baseHtmlKey"])["sha256"] != fields["baseArtifactSha256"]:
                raise HTTPError(409, "base-changed", "The selected base artifact changed")
        if "referencePage" in data and not data.get("referenceAssetId"):
            raise HTTPError(400, "invalid-reference", "Select a reference file")
        if data.get("referenceAssetId"):
            reference = next((asset for asset in assets if asset["id"] == data["referenceAssetId"]), None)
            if reference is None:
                # Resolve ownership first so a foreign identifier never becomes an existence oracle.
                self._get(owner, "asset", data["referenceAssetId"])
                raise HTTPError(409, "reference-not-selected", "The reference must be an approved contract asset")
            page = _integer(data.get("referencePage", 1), "Reference page", 1, 10000)
            preview = next((row for row in reference.get("previews", []) if row.get("page") == page), None)
            if not preview or preview.get("mime") != "image/png":
                raise HTTPError(409, "reference-unavailable", "The selected reference has no PNG preview")
            fields.update(referenceKey=self._blob_key(owner, preview["key"]), referencePage=page)
            fields["referenceSha256"] = self.storage.blob_info(fields["referenceKey"])["sha256"]
        run_data = {**data, **fields, "id": identifier, "status": "queued", "contractHash": digest,
                    "contract": normalized, "assetSnapshots": self._snapshot_assets(assets), "bestRound": 0,
                    "rounds": [], "functionalStatus": "not-run", "visualStatus": "not-run", "jobId": identifier}
        try:
            run = self.storage.put(owner, "run", run_data)
        except Conflict:
            run = self._get(owner, "run", identifier)
            if run.get("contractHash") != digest or self._fingerprint({key: run[key] for key in data}) != fingerprint:
                raise HTTPError(409, "request-changed", "The request ID was already used")
        job = self._new_job(owner, identifier, "run", {"runId": run["id"]}, fingerprint)
        self._invoke(owner, job)
        return _json(202, {"job": job, "run": run})

    @staticmethod
    def _passing_evidence(run, report):
        """Check browser evidence against frozen rules, not an overall flag alone."""
        if (not isinstance(report, dict) or report.get("passed") is not True
                or report.get("functionalStatus") != "pass"
                or report.get("networkRequests") != [] or report.get("consoleErrors") != []
                or report.get("blockingFindings") != []
                or not isinstance(report.get("accessibility"), dict)
                or report["accessibility"].get("status") != "pass"):
            return False
        visual = report.get("visual")
        allowed_visual = ("pass",) if run.get("referenceAssetId") else ("pass", "not-run")
        if not isinstance(visual, dict) or visual.get("status") not in allowed_visual:
            return False
        rules = (run.get("contract") or {}).get("rules", [])
        checks = report.get("checks")
        if not rules or not isinstance(checks, list) or len(checks) != len(rules):
            return False
        by_id = {}
        for check in checks:
            if (not isinstance(check, dict) or not isinstance(check.get("caseId"), str)
                    or check["caseId"] in by_id or check.get("status") not in ("pass", "fail")):
                return False
            by_id[check["caseId"]] = check
        return all(rule["id"] in by_id and (not rule.get("required", True)
                   or by_id[rule["id"]]["status"] == "pass") for rule in rules)

    def _run_approve(self, owner, run, body):
        version = _integer(body.get("contractVersion"), "Contract version", 1, 2**53 - 1)
        number = _integer(body.get("round"), "Round", 1, 5)
        selected = next((row for row in run.get("rounds", []) if row.get("number") == number), None)
        digest = body.get("artifactSha256")
        contract = self._get(owner, "contract", run["contractId"])
        if (run.get("status") not in ("completed", "needs_changes")
                or version != run["contractVersion"] or contract["version"] != version or contract.get("status") != "approved"
                or (contract.get("approval") or {}).get("hash") != run["contractHash"]
                or not selected or selected.get("passed") is not True
                or selected.get("blockingFindings") != []
                or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                or digest != selected.get("artifactSha256") or not selected.get("reportKey")):
            raise HTTPError(409, "approval-evidence-required", "Approve only the exact artifact with passing required evidence")
        html_key = self._blob_key(owner, selected.get("htmlKey"))
        report_key = self._blob_key(owner, selected["reportKey"])
        if self.storage.blob_info(html_key)["sha256"] != digest:
            raise HTTPError(409, "artifact-changed", "The tested artifact bytes have changed")
        report = json.loads(self.storage.get_blob(report_key))
        if (not self._passing_evidence(run, report)
                or not self.rules().report_passes(run["contract"], report,
                                                 visual_required=bool(run.get("referenceAssetId")))
                or report.get("artifactSha256") != digest
                or report.get("contractHash") != run["contractHash"]
                or self.rules().contract_hash(run["contract"]) != run["contractHash"]):
            raise HTTPError(409, "approval-evidence-required", "The stored verification report did not pass")
        result = self.storage.put(owner, "run", {
            **run, "approval": {"round": number, "artifactSha256": digest, "contractVersion": version,
                                "contractHash": run["contractHash"], "actor": owner, "at": self.storage.clock()},
        }, run["version"])
        return _json(200, {"run": result})

    @staticmethod
    def _query_int(query, key, default, maximum, minimum=0):
        value = query.get(key, str(default))
        if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,9}", value):
            raise HTTPError(400, "invalid-offset", f"Invalid {key}")
        return _integer(int(value), key, minimum, maximum)

    def _download(self, owner, kind, record, query):
        offset = self._query_int(query, "offset", 0, MAX_FILE_BYTES)
        requested = query.get("kind", "original" if kind == "asset" else "html")
        filename, declared_type = record.get("name", "artifact"), "application/octet-stream"
        if kind == "asset":
            if record.get("uploadStatus") != "stored":
                raise HTTPError(409, "asset-not-ready", "File storage is not complete")
            if requested == "original":
                key = record.get("originalKey")
            elif requested == "preview":
                page = self._query_int(query, "page", 1, 10000, minimum=1)
                preview = next((row for row in record.get("previews", []) if row.get("page") == page), None)
                if not preview:
                    raise HTTPError(404, "not-found", "Preview not available")
                key, declared_type = preview.get("key"), preview.get("mime")
            else:
                raise HTTPError(400, "invalid-kind", "Choose original or preview")
        else:
            if requested not in ("html", "screenshot", "diff", "report"):
                raise HTTPError(400, "invalid-kind", "Choose a run artifact kind")
            number = self._query_int(query, "round", 1, 5, minimum=1)
            row = next((row for row in record.get("rounds", []) if row.get("number") == number), None)
            if not row:
                raise HTTPError(404, "not-found", "Round artifact not available")
            key = row.get(requested + "Key")
            declared_type = "image/png" if requested in ("screenshot", "diff") else "application/octet-stream"
            filename = f"{record['id']}-r{number}-{requested}"
        key = self._blob_key(owner, key)
        info = self.storage.blob_info(key)
        if info["size"] > MAX_FILE_BYTES or offset > info["size"] or (offset == info["size"] and offset != 0):
            raise HTTPError(416, "invalid-range", "Offset is outside the stored artifact")
        # Only inert raster formats can be navigated inline on the app origin.
        mime = declared_type if declared_type in ("image/png", "image/jpeg", "image/webp") and info["contentType"] == declared_type else "application/octet-stream"
        data = self.storage.get_blob(key, offset=offset, length=CHUNK_BYTES)
        headers = {
            **_BASE_HEADERS, "Content-Type": mime, "X-Content-Type": mime,
            "X-Total-Size": str(info["size"]), "X-Chunk-Size": str(len(data)), "X-SHA256": info["sha256"],
            "Content-Disposition": f"{'inline' if mime.startswith('image/') else 'attachment'}; filename*=UTF-8''{quote(filename, safe='')}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        }
        return {"statusCode": 200, "headers": headers, "body": base64.b64encode(data).decode(), "isBase64Encoded": True}


_api = None


def handler(event, context):
    global _api
    if _api is None:
        _api = WorkspaceAPI()
    return _api.handle(event, context)
