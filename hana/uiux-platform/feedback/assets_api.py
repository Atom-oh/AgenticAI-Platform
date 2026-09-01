import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3

ASSET_TYPES = {"token", "palette", "icon-set", "component", "style-guide", "skill",
               "workflow", "agent"}
JSON_TYPES = {"token", "palette", "icon-set", "agent"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _slug(name):
    s = re.sub(r"\s+", "-", name.strip().lower())
    return re.sub(r"[^0-9a-z가-힣\-]", "", s) or uuid.uuid4().hex[:8]


def _tables():
    ddb = boto3.resource("dynamodb")
    return ddb.Table(os.environ["REGISTRY_TABLE"]), ddb.Table(os.environ["HISTORY_TABLE"])


def register_asset(body, actor=None):
    name, atype = body.get("name", "").strip(), body.get("type", "")
    content = body.get("content", "")
    if not name or atype not in ASSET_TYPES or not content.strip():
        return 400, {"error": f"name/content required; type must be one of {sorted(ASSET_TYPES)}"}
    if atype in JSON_TYPES:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return 400, {"error": f"{atype} content must be valid JSON"}
        if atype == "agent" and not isinstance(parsed, dict):
            return 400, {"error": "agent content must be a JSON object "
                                  "(keys: system, model_id, asset_ids, skills)"}
    registry, history = _tables()
    asset_id = f"{atype}:{_slug(name)}"
    prev = registry.get_item(Key={"asset_id": asset_id}).get("Item")
    version = (int(prev["version"]) if prev and str(prev.get("version", "")).isdigit() else 0) + 1
    ext = "json" if atype in JSON_TYPES else "md"
    s3_key = f"user-assets/{asset_id}/v{version}.{ext}"
    boto3.client("s3").put_object(Bucket=os.environ["ASSETS_BUCKET"], Key=s3_key,
                                  Body=content.encode(), ContentType="application/json"
                                  if ext == "json" else "text/markdown")
    actor = actor or body.get("actor", "anonymous")
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


def _load_agent_cfg(agent_id):
    """Resolve a shared agent preset (asset type 'agent') to its config dict."""
    if not agent_id:
        return None
    registry, _ = _tables()
    item = registry.get_item(Key={"asset_id": agent_id}).get("Item")
    if not item or item.get("type") != "agent":
        return None
    body = boto3.client("s3").get_object(Bucket=os.environ["ASSETS_BUCKET"],
                                         Key=item["s3_key"])["Body"].read()
    cfg = json.loads(body)
    cfg["name"] = item.get("name", agent_id)
    return cfg


def create_job(body, lambda_client, actor=None):
    brief = body.get("brief", "").strip()
    if not brief:
        return 400, {"error": "brief required"}
    actor = actor or body.get("actor", "anonymous")
    mode = body.get("mode", "generate")
    if mode == "refine" and not body.get("base_draft_id"):
        return 400, {"error": "refine mode requires base_draft_id"}
    agent_cfg = _load_agent_cfg(body.get("agent_id", ""))
    _, history = _tables()
    job_id = uuid.uuid4().hex[:12]
    job = {"asset_id": f"job:{job_id}", "version": "job", "status": "running",
           "brief": brief, "asset_ids": body.get("asset_ids", []),
           "model_id": body.get("model_id", ""), "agent_id": body.get("agent_id", ""),
           "mode": mode, "output_type": body.get("output_type", "design"),
           "base_draft_id": body.get("base_draft_id", ""),
           "actor": actor, "created_at": _now()}
    history.put_item(Item=job)
    lambda_client.invoke(FunctionName=os.environ["DISPATCHER_FN"], InvocationType="Event",
                         Payload=json.dumps({"job_id": job_id, "brief": brief,
                                             "asset_ids": body.get("asset_ids", []),
                                             "model_id": body.get("model_id", ""),
                                             "agent_cfg": agent_cfg, "actor": actor,
                                             "mode": mode,
                                             "output_type": body.get("output_type", "design"),
                                             "base_draft_id": body.get("base_draft_id", ""),
                                             "selector": body.get("selector", ""),
                                             "element_html": body.get("element_html", "")[:4000]},
                                            ensure_ascii=False).encode())
    return 200, {"ok": True, "job_id": job_id}


def list_jobs(limit=20):
    _, history = _tables()
    from boto3.dynamodb.conditions import Attr
    items, kwargs = [], {"FilterExpression": Attr("version").eq("job")}
    while True:
        page = history.scan(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return 200, {"jobs": items[:limit]}


def list_models():
    """All Bedrock models usable in this region, via system inference profiles."""
    client = boto3.client("bedrock")
    models, token = [], None
    while True:
        kwargs = {"typeEquals": "SYSTEM_DEFINED"}
        if token:
            kwargs["nextToken"] = token
        page = client.list_inference_profiles(**kwargs)
        for p in page.get("inferenceProfileSummaries", []):
            if p.get("status") == "ACTIVE":
                models.append({"id": p["inferenceProfileId"],
                               "name": p.get("inferenceProfileName", p["inferenceProfileId"])})
        token = page.get("nextToken")
        if not token:
            break
    models.sort(key=lambda m: m["id"])
    return 200, {"models": models}


def get_job(job_id):
    _, history = _tables()
    item = history.get_item(Key={"asset_id": f"job:{job_id}", "version": "job"}).get("Item")
    if not item:
        return 404, {"error": f"job not found: {job_id}"}
    return 200, {"job": item}
