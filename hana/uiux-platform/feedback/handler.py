import base64
import json
import os
from datetime import datetime, timezone

import boto3

from . import assets_api


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False, default=str)}


def _lambda_client():
    return boto3.client("lambda")


def handle_feedback(body):
    draft_id, action = body.get("draft_id"), body.get("action")
    if not draft_id or action not in ("approve", "reject"):
        return 400, {"error": "draft_id and action (approve|reject) required"}

    s3 = boto3.client("s3")
    bucket = os.environ["DRAFTS_BUCKET"]
    try:
        manifest = json.loads(s3.get_object(Bucket=bucket, Key="drafts.json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return 404, {"error": "drafts.json not found"}
    entry = next((d for d in manifest["drafts"] if d["id"] == draft_id), None)
    if entry is None:
        return 404, {"error": f"draft not found: {draft_id}"}

    status = "승인됨" if action == "approve" else "반려"
    entry["status"] = status
    if body.get("comment"):
        entry["comment"] = body["comment"]
    s3.put_object(Bucket=bucket, Key="drafts.json",
                  Body=json.dumps(manifest, ensure_ascii=False).encode(),
                  ContentType="application/json")

    if action == "approve":
        s3.copy_object(Bucket=bucket, Key=f"approved-patterns/{draft_id}.html",
                       CopySource={"Bucket": bucket, "Key": f"drafts/{draft_id}.html"})
        try:
            idx = json.loads(s3.get_object(
                Bucket=bucket, Key="approved-patterns/index.json")["Body"].read())
        except s3.exceptions.NoSuchKey:
            idx = {"patterns": []}
        idx["patterns"] = [p for p in idx["patterns"] if p["id"] != draft_id]
        idx["patterns"].append({"id": draft_id, "title": entry.get("title", ""),
                                "axis": entry.get("axis", ""),
                                "approved_at": datetime.now(timezone.utc).isoformat()})
        s3.put_object(Bucket=bucket, Key="approved-patterns/index.json",
                      Body=json.dumps(idx, ensure_ascii=False).encode(),
                      ContentType="application/json")
    return 200, {"ok": True, "status": status}


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    qs = event.get("queryStringParameters") or {}
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid JSON"})
    try:
        # Legacy feedback route (no rawPath for backwards compatibility)
        if method == "POST" and not path and "draft_id" in body:
            return _resp(*handle_feedback(body))
        # New API routes
        if method == "POST" and path.endswith("/api/feedback"):
            return _resp(*handle_feedback(body))
        if method == "POST" and path.endswith("/api/assets"):
            return _resp(*assets_api.register_asset(body))
        if method == "GET" and path.endswith("/api/assets"):
            return _resp(*assets_api.list_assets())
        if method == "GET" and path.endswith("/api/assets/history"):
            return _resp(*assets_api.asset_history(qs.get("asset_id", "")))
        if method == "POST" and path.endswith("/api/generate"):
            return _resp(*assets_api.create_job(body, _lambda_client()))
        if method == "GET" and path.endswith("/api/jobs"):
            return _resp(*assets_api.get_job(qs.get("job_id", "")))
    except Exception as e:
        return _resp(500, {"error": str(e)})
    return _resp(404, {"error": f"no route: {method} {path}"})
