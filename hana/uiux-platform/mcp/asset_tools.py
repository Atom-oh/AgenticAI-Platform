import json
import os

import boto3

BRAND_GUIDELINE = {
    "brand": "Hana Bank UI/UX PoC",
    "palette": {"primary": "#008485", "primaryDark": "#00615f", "ink": "#17332f",
                "bg": "#fbfcfb", "mist": "#e6f3f2"},
    "font": "Noto Sans KR",
    "rules": [
        "tokens are law: colors/type/spacing come from the registry, never invented",
        "single-screen card-sectioned layout is the default draft style",
        "hit targets >= 44px; no fake device chrome (status bar, keyboard)",
        "variants move one named axis each: 밀도(density), 강조(hierarchy), 흐름(flow)",
    ],
}


def _s3():
    return boto3.client("s3")


def list_design_tokens(_):
    body = _s3().get_object(Bucket=os.environ["ASSETS_BUCKET"],
                            Key="tokens/latest.json")["Body"].read()
    return json.loads(body)


def search_assets(event):
    q = event.get("query", "").lower()
    table = boto3.resource("dynamodb").Table(os.environ["REGISTRY_TABLE"])
    items = table.scan()["Items"]
    return {"results": [i for i in items if q in i.get("name", "").lower()]}


def get_component(event):
    node_id = event.get("node_id", "")
    try:
        body = _s3().get_object(Bucket=os.environ["ASSETS_BUCKET"],
                                Key=f"components/{node_id}.json")["Body"].read()
        return json.loads(body)
    except _s3().exceptions.NoSuchKey:
        return {"error": f"component not found: {node_id}"}


def get_brand_guideline(_):
    return BRAND_GUIDELINE


def list_skills(_):
    resp = _s3().list_objects_v2(Bucket=os.environ["SKILLS_BUCKET"], Prefix="skills/")
    skills = []
    for obj in resp.get("Contents", []):
        parts = obj["Key"].split("/")  # skills/<name>/<version>/SKILL.md
        if len(parts) == 4 and parts[3] == "SKILL.md":
            skills.append({"name": parts[1], "version": parts[2]})
    return {"skills": sorted(skills, key=lambda s: s["name"])}


def get_skill(event):
    name = event.get("name", "")
    versions = [s["version"] for s in list_skills({})["skills"] if s["name"] == name]
    if not versions:
        return {"error": f"skill not found: {name}"}
    key = f"skills/{name}/{max(versions)}/SKILL.md"
    body = _s3().get_object(Bucket=os.environ["SKILLS_BUCKET"], Key=key)["Body"].read()
    return {"name": name, "version": max(versions), "content": body.decode()}


TOOLS = {f.__name__: f for f in [list_design_tokens, search_assets, get_component,
                                 get_brand_guideline, list_skills, get_skill]}


def handler(event, context):
    tool = context.client_context.custom["bedrockAgentCoreToolName"].split("___")[-1]
    fn = TOOLS.get(tool)
    if fn is None:
        return {"error": f"unknown tool: {tool}"}
    try:
        return fn(event or {})
    except Exception as e:  # tool errors surface as MCP error results, never exceptions
        return {"error": str(e)}
