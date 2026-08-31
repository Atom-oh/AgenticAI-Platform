import json
import os
import uuid
from datetime import datetime, timezone

import boto3


def publish_draft(title: str, axis: str, html: str) -> str:
    s3 = boto3.client("s3")
    bucket = os.environ["DRAFTS_BUCKET"]
    draft_id = uuid.uuid4().hex[:12]
    key = f"drafts/{draft_id}.html"
    s3.put_object(Bucket=bucket, Key=key, Body=html.encode(),
                  ContentType="text/html; charset=utf-8")
    url = f"https://{os.environ['DISTRIBUTION_DOMAIN']}/{key}"
    try:
        manifest = json.loads(
            s3.get_object(Bucket=bucket, Key="drafts.json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        manifest = {"drafts": []}
    manifest["drafts"].append({
        "id": draft_id, "title": title, "axis": axis, "status": "검토중",
        "url": url, "created_at": datetime.now(timezone.utc).isoformat()})
    s3.put_object(Bucket=bucket, Key="drafts.json",
                  Body=json.dumps(manifest, ensure_ascii=False).encode(),
                  ContentType="application/json")
    return url
