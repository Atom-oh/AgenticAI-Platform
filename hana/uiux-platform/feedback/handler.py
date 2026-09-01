import base64
import json
import os
from datetime import datetime, timezone

import boto3

from . import assets_api
from .auth import actor_from_event


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False, default=str)}


def _lambda_client():
    return boto3.client("lambda")


def _remember_feedback(actor, draft_id, entry, action, comment):
    """Record the designer's taste signal into AgentCore Memory (best effort)."""
    memory_id = os.environ.get("MEMORY_ID", "")
    if not memory_id or not actor:
        return
    try:
        text = (f"디자이너 {actor}의 피드백: 시안 '{entry.get('title', draft_id)}' "
                f"(axis {entry.get('axis', '')}) → {'승인' if action == 'approve' else '반려'}"
                + (f" — 사유: {comment}" if comment else ""))
        boto3.client("bedrock-agentcore").create_event(
            memoryId=memory_id, actorId=actor, sessionId=f"feedback-{draft_id}",
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"content": {"text": text}, "role": "USER"}}])
    except Exception:
        pass  # memory is an enhancement, never a failure path


def handle_feedback(body, actor=None):
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
    if actor:
        entry["reviewed_by"] = actor
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
    _remember_feedback(actor, draft_id, entry, action, body.get("comment", ""))
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

        if method == "POST":
            # every write requires a signed-in designer (Cognito access token
            # in the x-hana-auth header; Authorization is reserved by OAC)
            actor = actor_from_event(event)
            if actor is None:
                return _resp(401, {"error": "로그인이 필요합니다 (x-hana-auth)"})
            if path.endswith("/api/feedback"):
                return _resp(*handle_feedback(body, actor))
            if path.endswith("/api/assets"):
                return _resp(*assets_api.register_asset(body, actor))
            if path.endswith("/api/generate"):
                return _resp(*assets_api.create_job(body, _lambda_client(), actor))

        if method == "GET":
            if path.endswith("/api/assets"):
                return _resp(*assets_api.list_assets())
            if path.endswith("/api/assets/history"):
                return _resp(*assets_api.asset_history(qs.get("asset_id", "")))
            if path.endswith("/api/jobs"):
                if qs.get("job_id"):
                    return _resp(*assets_api.get_job(qs["job_id"]))
                return _resp(*assets_api.list_jobs())
            if path.endswith("/api/models"):
                return _resp(*assets_api.list_models())
            if path.endswith("/api/config"):
                return _resp(200, {"spa_client_id": os.environ.get("SPA_CLIENT_ID", ""),
                                   "region": os.environ.get("AWS_REGION", "ap-northeast-2"),
                                   "default_model": os.environ.get(
                                       "DEFAULT_MODEL", "global.anthropic.claude-sonnet-5")})
            if path.endswith("/api/me"):
                actor = actor_from_event(event)
                if actor is None:
                    return _resp(401, {"error": "unauthenticated"})
                return _resp(200, {"username": actor})
    except Exception as e:
        return _resp(500, {"error": str(e)})
    return _resp(404, {"error": f"no route: {method} {path}"})
