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


def load_approved_patterns(limit: int = 2) -> list:
    """Newest-first approved drafts as few-shot references. Empty list when none."""
    s3 = boto3.client("s3")
    bucket = os.environ["DRAFTS_BUCKET"]
    try:
        idx = json.loads(s3.get_object(
            Bucket=bucket, Key="approved-patterns/index.json")["Body"].read())
    except s3.exceptions.NoSuchKey:
        return []
    out = []
    for p in sorted(idx.get("patterns", []), key=lambda x: x["approved_at"], reverse=True)[:limit]:
        try:
            html = s3.get_object(Bucket=bucket,
                                 Key=f"approved-patterns/{p['id']}.html")["Body"].read().decode()
        except s3.exceptions.NoSuchKey:
            continue
        out.append({"title": p["title"], "axis": p["axis"], "html": html})
    return out
