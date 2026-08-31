import json
import os
from datetime import datetime, timezone

import boto3


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid JSON"})
    draft_id, action = body.get("draft_id"), body.get("action")
    if not draft_id or action not in ("approve", "reject"):
        return _resp(400, {"error": "draft_id and action (approve|reject) required"})

    s3 = boto3.client("s3")
    bucket = os.environ["DRAFTS_BUCKET"]
    manifest = json.loads(s3.get_object(Bucket=bucket, Key="drafts.json")["Body"].read())
    entry = next((d for d in manifest["drafts"] if d["id"] == draft_id), None)
    if entry is None:
        return _resp(404, {"error": f"draft not found: {draft_id}"})

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
        idx["patterns"].append({"id": draft_id, "title": entry["title"], "axis": entry["axis"],
                                "approved_at": datetime.now(timezone.utc).isoformat()})
        s3.put_object(Bucket=bucket, Key="approved-patterns/index.json",
                      Body=json.dumps(idx, ensure_ascii=False).encode(),
                      ContentType="application/json")
    return _resp(200, {"ok": True, "status": status})
