import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3

ASSET_TYPES = {"token", "palette", "icon-set", "component", "style-guide", "skill", "workflow"}
JSON_TYPES = {"token", "palette", "icon-set"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _slug(name):
    s = re.sub(r"\s+", "-", name.strip().lower())
    return re.sub(r"[^0-9a-z가-힣\-]", "", s) or uuid.uuid4().hex[:8]


def _tables():
    ddb = boto3.resource("dynamodb")
    return ddb.Table(os.environ["REGISTRY_TABLE"]), ddb.Table(os.environ["HISTORY_TABLE"])


def register_asset(body):
    name, atype = body.get("name", "").strip(), body.get("type", "")
    content = body.get("content", "")
    if not name or atype not in ASSET_TYPES or not content.strip():
        return 400, {"error": f"name/content required; type must be one of {sorted(ASSET_TYPES)}"}
    if atype in JSON_TYPES:
        try:
            json.loads(content)
        except json.JSONDecodeError:
            return 400, {"error": f"{atype} content must be valid JSON"}
    registry, history = _tables()
    asset_id = f"{atype}:{_slug(name)}"
    prev = registry.get_item(Key={"asset_id": asset_id}).get("Item")
    version = (int(prev["version"]) if prev and str(prev.get("version", "")).isdigit() else 0) + 1
    ext = "json" if atype in JSON_TYPES else "md"
    s3_key = f"user-assets/{asset_id}/v{version}.{ext}"
    boto3.client("s3").put_object(Bucket=os.environ["ASSETS_BUCKET"], Key=s3_key,
                                  Body=content.encode(), ContentType="application/json"
                                  if ext == "json" else "text/markdown")
    actor = body.get("actor", "anonymous")
    registry.put_item(Item={"asset_id": asset_id, "type": atype, "name": name,
                            "scope": body.get("scope", "mine"), "version": version,
                            "s3_key": s3_key, "actor": actor, "updated_at": _now(),
                            "source": "user"})
    history.put_item(Item={"asset_id": asset_id, "version": f"v{version:04d}",
                           "action": "register" if version == 1 else "update",
                           "actor": actor, "s3_key": s3_key,
                           "note": body.get("note", ""), "created_at": _now()})
    if atype == "skill":
        # user-registered skills are namespaced under "user-" so they can never
        # collide with (or overwrite) org/seed skills, which are deploy-managed
        # only (seeded via scripts/sync_skills.py, not writable through this API).
        boto3.client("s3").put_object(
            Bucket=os.environ["SKILLS_BUCKET"],
            Key=f"skills/user-{_slug(name)}/{version}.0.0/SKILL.md",
            Body=content.encode(), ContentType="text/markdown")
    return 200, {"ok": True, "asset_id": asset_id, "version": version}


def list_assets():
    registry, _ = _tables()
    out = []
    for item in registry.scan()["Items"]:
        aid = item.get("asset_id", "")
        if aid.startswith("job:"):
            continue
        if item.get("source") == "user":
            out.append(item)
        elif item.get("type") in ("token", "component"):
            out.append({**item, "scope": "shared", "source": "figma"})
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return 200, {"assets": out, "count": len(out)}


def asset_history(asset_id):
    if not asset_id:
        return 400, {"error": "asset_id required"}
    _, history = _tables()
    from boto3.dynamodb.conditions import Key
    items = history.query(KeyConditionExpression=Key("asset_id").eq(asset_id),
                          ScanIndexForward=False)["Items"]
    return 200, {"history": items}


def create_job(body, lambda_client):
    brief = body.get("brief", "").strip()
    if not brief:
        return 400, {"error": "brief required"}
    _, history = _tables()
    job_id = uuid.uuid4().hex[:12]
    history.put_item(Item={"asset_id": f"job:{job_id}", "version": "job", "status": "running",
                           "brief": brief, "asset_ids": body.get("asset_ids", []),
                           "created_at": _now()})
    lambda_client.invoke(FunctionName=os.environ["DISPATCHER_FN"], InvocationType="Event",
                         Payload=json.dumps({"job_id": job_id, "brief": brief,
                                             "asset_ids": body.get("asset_ids", [])},
                                            ensure_ascii=False).encode())
    return 200, {"ok": True, "job_id": job_id}


def get_job(job_id):
    _, history = _tables()
    item = history.get_item(Key={"asset_id": f"job:{job_id}", "version": "job"}).get("Item")
    if not item:
        return 404, {"error": f"job not found: {job_id}"}
    return 200, {"job": item}
