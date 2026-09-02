# Phase 2: Asset Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the read-only gallery into a platform: register design assets of 7 types with version history, and generate UX drafts from user-selected assets — all in the web UI.

**Architecture:** The existing feedback Lambda becomes a routed platform API (`/api/assets`, `/api/generate`, `/api/jobs`, `/api/feedback`). Generation is async: the API enqueues a job and async-invokes a dispatcher Lambda (900s) that calls the AgentCore Runtime; the harness fetches the selected assets THROUGH the shared Gateway MCP (`get_asset`), keeping MCP the single asset supply route. Frontend becomes a 3-tab SPA on the same CloudFront site.

**Tech Stack:** unchanged (Python 3.13 Lambdas, CDK, Strands/AgentCore, vanilla JS SPA).

## Global Constraints

- All Phase 1 global constraints still bind (region/account, no public compute, Cognito no self-signup, secrets hygiene, Korean literal UTF-8).
- Asset types (exact strings): `token`, `palette`, `icon-set`, `component`, `style-guide`, `skill`, `workflow`.
- User-registered asset content lives in the assets bucket under `user-assets/<asset_id>/v<version>.<json|md>`; latest pointer in `hana-design-registry`; every version appended to new table `hana-asset-history`.
- A registered `skill` asset is ALSO written to the skill registry bucket at `skills/<asset_id>/<version>.0.0/SKILL.md` so `get_skill` serves it.
- Figma-sourced ingestion stays the Phase 1 pipeline (company-shared); the new form paths are JSON/Markdown. A Figma URL in the form is out of Phase 2 scope (documented in UI placeholder only).
- Tests: `cd hana/uiux-platform && .venv/bin/python -m pytest tests/ -q` — currently 24 passed; keep green.
- Commits end with "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>".

---

### Task P2-1: Platform API router (assets/history/jobs/feedback) — TDD

**Files:**
- Modify: `hana/uiux-platform/feedback/handler.py` (becomes the router; keep feedback logic)
- Create: `hana/uiux-platform/feedback/assets_api.py`
- Test: `hana/uiux-platform/tests/test_assets_api.py` (new), keep `tests/test_feedback.py` green

**Interfaces:**
- Router: Function URL event → route on `event["requestContext"]["http"]["method"]` + `event["rawPath"]` (paths arrive as `/api/...`):
  - `POST /api/feedback` → existing feedback logic (unchanged behavior)
  - `POST /api/assets` body `{"name": str, "type": <7 types>, "scope": "mine"|"shared", "actor": str?, "content": str}` → validates type + non-empty name/content; JSON types (`token`,`palette`,`icon-set`) must parse as JSON (400 otherwise); computes `asset_id = <type>:<slug(name)>` (slug: lowercase, spaces→`-`, keep hangul/alnum/dash); version = latest+1; writes content to `ASSETS_BUCKET` `user-assets/<asset_id>/v<version>.<ext>` (ext json for JSON types else md); upserts registry item `{asset_id, type, name, scope, version, s3_key, actor, updated_at, source:"user"}`; appends history item to `HISTORY_TABLE` `{asset_id, version (N zero-padded "v%04d"), action:"register"|"update", actor, s3_key, note, created_at}`; if type==`skill` also puts SKILL.md to `SKILLS_BUCKET` at `skills/<slug>/<version>.0.0/SKILL.md`. Returns `{"ok":true,"asset_id":...,"version":N}`.
  - `GET /api/assets` → `{"assets":[registry items where source=="user" OR type in ("token","component")]}` — i.e., user assets plus the Phase 1 figma-synced entries (they lack `scope`; default them to `"shared"`, `source:"figma"` in the response).
  - `GET /api/assets/history?asset_id=...` → `{"history":[items newest-first]}` (Query on HISTORY_TABLE, ScanIndexForward False).
  - `POST /api/generate` body `{"brief": str, "asset_ids": [str]}` → creates `job_id` uuid12, history-table item `{asset_id:"job:"+job_id, version:"job", status:"running", brief, asset_ids, created_at}`, async-invokes `DISPATCHER_FN` (`InvocationType="Event"`, payload `{"job_id","brief","asset_ids"}`), returns `{"ok":true,"job_id":...}`. Empty brief → 400.
  - `GET /api/jobs?job_id=...` → `{"job": item}` (404 if missing).
- New env vars on the Lambda: `HISTORY_TABLE`, `DISPATCHER_FN` (plus existing `DRAFTS_BUCKET`; add `ASSETS_BUCKET`, `REGISTRY_TABLE`, `SKILLS_BUCKET`).
- Produces for P2-2: job item shape above; dispatcher updates the same item with `status: "done"|"failed"`, `drafts: [...]`, `error`.

- [ ] Step 1: write failing tests

`hana/uiux-platform/tests/test_assets_api.py`:

```python
import json
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws


def _event(method, path, body=None, qs=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path,
            "queryStringParameters": qs or {},
            "body": json.dumps(body, ensure_ascii=False) if body is not None else None}


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_DEFAULT_REGION": "ap-northeast-2", "DRAFTS_BUCKET": "drafts",
                 "ASSETS_BUCKET": "assets", "SKILLS_BUCKET": "skills",
                 "REGISTRY_TABLE": "registry", "HISTORY_TABLE": "history",
                 "DISPATCHER_FN": "hana-generate-dispatcher"}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-2")
        cfg = {"CreateBucketConfiguration": {"LocationConstraint": "ap-northeast-2"}}
        for b in ("drafts", "assets", "skills"):
            s3.create_bucket(Bucket=b, **cfg)
        ddb = boto3.client("dynamodb", region_name="ap-northeast-2")
        ddb.create_table(TableName="registry",
                         KeySchema=[{"AttributeName": "asset_id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "asset_id", "AttributeType": "S"}],
                         BillingMode="PAY_PER_REQUEST")
        ddb.create_table(TableName="history",
                         KeySchema=[{"AttributeName": "asset_id", "KeyType": "HASH"},
                                    {"AttributeName": "version", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "asset_id", "AttributeType": "S"},
                                               {"AttributeName": "version", "AttributeType": "S"}],
                         BillingMode="PAY_PER_REQUEST")
        yield s3


def test_register_palette_and_history_and_version_bump(aws):
    from feedback.handler import handler
    body = {"name": "카드상품 팔레트", "type": "palette", "scope": "mine", "actor": "오준석",
            "content": json.dumps({"primary": "#7d2882"})}
    r1 = handler(_event("POST", "/api/assets", body), None)
    assert r1["statusCode"] == 200
    out = json.loads(r1["body"])
    aid = out["asset_id"]
    assert out["version"] == 1 and aid.startswith("palette:")
    r2 = handler(_event("POST", "/api/assets", body), None)
    assert json.loads(r2["body"])["version"] == 2
    h = handler(_event("GET", "/api/assets/history", qs={"asset_id": aid}), None)
    hist = json.loads(h["body"])["history"]
    assert len(hist) == 2 and hist[0]["version"] > hist[1]["version"]
    obj = aws.get_object(Bucket="assets", Key=f"user-assets/{aid}/v2.json")["Body"].read()
    assert json.loads(obj)["primary"] == "#7d2882"


def test_register_skill_writes_skill_registry(aws):
    from feedback.handler import handler
    r = handler(_event("POST", "/api/assets", {
        "name": "senior-mode", "type": "skill", "scope": "shared",
        "content": "# senior-mode\n큰 글자"}), None)
    assert r["statusCode"] == 200
    keys = [o["Key"] for o in aws.list_objects_v2(Bucket="skills")["Contents"]]
    assert "skills/senior-mode/1.0.0/SKILL.md" in keys


def test_register_validation(aws):
    from feedback.handler import handler
    assert handler(_event("POST", "/api/assets", {"name": "x", "type": "nope", "content": "y"}),
                   None)["statusCode"] == 400
    assert handler(_event("POST", "/api/assets", {"name": "x", "type": "palette",
                                                  "content": "not json"}), None)["statusCode"] == 400


def test_list_assets_includes_figma_synced(aws):
    from feedback.handler import handler
    boto3.resource("dynamodb", region_name="ap-northeast-2").Table("registry").put_item(
        Item={"asset_id": "token:latest", "type": "token", "name": "hana-tokens",
              "version": "latest", "s3_key": "tokens/latest.json",
              "figma_node_id": "k", "updated_at": "t"})
    handler(_event("POST", "/api/assets", {"name": "팔", "type": "palette", "scope": "mine",
                                           "content": "{}"}), None)
    out = json.loads(handler(_event("GET", "/api/assets"), None)["body"])
    ids = {a["asset_id"] for a in out["assets"]}
    assert "token:latest" in ids and any(i.startswith("palette:") for i in ids)
    figma = next(a for a in out["assets"] if a["asset_id"] == "token:latest")
    assert figma["scope"] == "shared" and figma["source"] == "figma"


def test_generate_creates_job_and_dispatches(aws, monkeypatch):
    from feedback import handler as mod
    calls = {}
    class FakeLambda:
        def invoke(self, **kw): calls.update(kw); return {"StatusCode": 202}
    monkeypatch.setattr(mod, "_lambda_client", lambda: FakeLambda())
    r = mod.handler(_event("POST", "/api/generate",
                           {"brief": "카드 신청 화면", "asset_ids": ["palette:x"]}), None)
    job_id = json.loads(r["body"])["job_id"]
    assert calls["InvocationType"] == "Event"
    assert json.loads(calls["Payload"])["job_id"] == job_id
    j = mod.handler(_event("GET", "/api/jobs", qs={"job_id": job_id}), None)
    assert json.loads(j["body"])["job"]["status"] == "running"
    assert mod.handler(_event("GET", "/api/jobs", qs={"job_id": "nope"}), None)["statusCode"] == 404


def test_feedback_route_still_works(aws):
    import json as _json
    from feedback.handler import handler
    aws.put_object(Bucket="drafts", Key="drafts.json", Body=_json.dumps({"drafts": [
        {"id": "abc", "title": "t", "axis": "밀도", "status": "검토중",
         "url": "https://x/drafts/abc.html", "created_at": "t"}]}))
    aws.put_object(Bucket="drafts", Key="drafts/abc.html", Body=b"<html>x</html>")
    r = handler(_event("POST", "/api/feedback", {"draft_id": "abc", "action": "approve"}), None)
    assert _json.loads(r["body"])["status"] == "승인됨"
```

- [ ] Step 2: run, verify new tests fail (`python -m pytest tests/test_assets_api.py -q`)
- [ ] Step 3: implement

`hana/uiux-platform/feedback/assets_api.py`:

```python
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
        boto3.client("s3").put_object(
            Bucket=os.environ["SKILLS_BUCKET"],
            Key=f"skills/{_slug(name)}/{version}.0.0/SKILL.md",
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
```

`feedback/handler.py` — refactor into a router. Keep the existing feedback logic in a `handle_feedback(body)` function returning `(status, dict)`; add at module level:

```python
def _lambda_client():
    return boto3.client("lambda")


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
```

(`_resp(code, body)` as today; Decimal values from DynamoDB must serialize — add a `default=str` to `json.dumps` in `_resp`.)

- [ ] Step 4: full suite green (24 existing + 6 new = 30)
- [ ] Step 5: commit `feat(hana): platform API — asset registration, history, async generate jobs`

---

### Task P2-2: MCP get_asset/list_assets tools + dispatcher Lambda + harness asset selection — TDD

**Files:**
- Modify: `hana/uiux-platform/mcp/asset_tools.py`, `mcp/tool_schemas.py`
- Create: `hana/uiux-platform/dispatch/handler.py` (+ empty `dispatch/__init__.py`)
- Modify: `hana/uiux-platform/harness/app.py`
- Test: extend `tests/test_asset_tools.py`; create `tests/test_dispatch.py`

**Interfaces:**
- New MCP tools (append to TOOLS + TOOL_SCHEMAS):
  - `list_assets()` → same shape as API list (scan registry, exclude `job:`; user assets + figma token/component entries).
  - `get_asset(asset_id)` → registry item + `"content"`: S3 object body decoded (from `s3_key`); `{"error": ...}` when missing.
- `dispatch/handler.py` `handler(event, ctx)`: event `{"job_id","brief","asset_ids"}`; builds harness payload `{"brief", "asset_ids"}`; calls `invoke_agent_runtime` (env `RUNTIME_ARN`, boto config read_timeout=850); parses response; updates job item (`status`:"done", `drafts`, `summary`) or on exception `status`:"failed", `error`. Env: `HISTORY_TABLE`, `RUNTIME_ARN`.
- `harness/app.py`: when payload has non-empty `asset_ids`, append to the system prompt:
  "사용자가 선택한 자산: <ids 나열>. 생성 전에 각 id에 대해 get_asset을 호출해 내용을 확인하고, 이 자산들을 최우선 규범으로 사용하라 (선택 자산과 충돌하는 기본 토큰 값은 선택 자산이 이긴다). type이 workflow인 자산은 화면 흐름 제약으로, style-guide는 스타일 규범으로, skill은 추가 지침으로 따르라."
  (Keep default behavior when asset_ids absent.)

- [ ] Step 1: failing tests — `tests/test_asset_tools.py` add:

```python
def test_get_asset_and_list_assets(aws):
    import json as _json
    from mcp.asset_tools import handler
    boto3.client("s3", region_name="ap-northeast-2").put_object(
        Bucket="assets", Key="user-assets/palette:p/v1.json",
        Body=_json.dumps({"primary": "#7d2882"}))
    boto3.resource("dynamodb", region_name="ap-northeast-2").Table("registry").put_item(
        Item={"asset_id": "palette:p", "type": "palette", "name": "팔레트", "version": 1,
              "s3_key": "user-assets/palette:p/v1.json", "scope": "mine",
              "source": "user", "updated_at": "t"})
    got = handler({"asset_id": "palette:p"}, ctx("get_asset"))
    assert _json.loads(got["content"])["primary"] == "#7d2882"
    assert "error" in handler({"asset_id": "nope:x"}, ctx("get_asset"))
    ids = {a["asset_id"] for a in handler({}, ctx("list_assets"))["assets"]}
    assert "palette:p" in ids and "component:1:2" in ids
```

and update `test_schemas_cover_all_tools` to the 8-name set. `tests/test_dispatch.py`:

```python
import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("HISTORY_TABLE", "history")
    monkeypatch.setenv("RUNTIME_ARN", "arn:aws:bedrock-agentcore:ap-northeast-2:1:runtime/x")
    with mock_aws():
        boto3.client("dynamodb", region_name="ap-northeast-2").create_table(
            TableName="history",
            KeySchema=[{"AttributeName": "asset_id", "KeyType": "HASH"},
                       {"AttributeName": "version", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "asset_id", "AttributeType": "S"},
                                  {"AttributeName": "version", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        t = boto3.resource("dynamodb", region_name="ap-northeast-2").Table("history")
        t.put_item(Item={"asset_id": "job:j1", "version": "job", "status": "running"})
        yield t


def test_dispatch_success_updates_job(aws, monkeypatch):
    from dispatch import handler as mod
    fake = MagicMock()
    fake.invoke_agent_runtime.return_value = {"response": MagicMock(read=lambda: json.dumps(
        {"drafts": [{"id": "d1", "title": "t", "axis": "밀도", "url": "u"}],
         "summary": "ok"}).encode())}
    monkeypatch.setattr(mod, "_agentcore", lambda: fake)
    mod.handler({"job_id": "j1", "brief": "b", "asset_ids": ["palette:p"]}, None)
    item = aws.get_item(Key={"asset_id": "job:j1", "version": "job"})["Item"]
    assert item["status"] == "done" and item["drafts"][0]["id"] == "d1"
    sent = json.loads(fake.invoke_agent_runtime.call_args.kwargs["payload"])
    assert sent["asset_ids"] == ["palette:p"]


def test_dispatch_failure_marks_failed(aws, monkeypatch):
    from dispatch import handler as mod
    fake = MagicMock()
    fake.invoke_agent_runtime.side_effect = RuntimeError("boom")
    monkeypatch.setattr(mod, "_agentcore", lambda: fake)
    mod.handler({"job_id": "j1", "brief": "b", "asset_ids": []}, None)
    item = aws.get_item(Key={"asset_id": "job:j1", "version": "job"})["Item"]
    assert item["status"] == "failed" and "boom" in item["error"]
```

- [ ] Step 2: verify fail  
- [ ] Step 3: implement. `dispatch/handler.py`:

```python
import json
import os

import boto3
from botocore.config import Config


def _agentcore():
    return boto3.client("bedrock-agentcore",
                        config=Config(read_timeout=850, retries={"total_max_attempts": 1}))


def _job_table():
    return boto3.resource("dynamodb").Table(os.environ["HISTORY_TABLE"])


def handler(event, context):
    job_id = event["job_id"]
    key = {"asset_id": f"job:{job_id}", "version": "job"}
    try:
        resp = _agentcore().invoke_agent_runtime(
            agentRuntimeArn=os.environ["RUNTIME_ARN"], qualifier="DEFAULT",
            payload=json.dumps({"brief": event["brief"],
                                "asset_ids": event.get("asset_ids", [])},
                               ensure_ascii=False).encode())
        body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
        out = json.loads(body)
        _job_table().update_item(
            Key=key, UpdateExpression="SET #s=:s, drafts=:d, summary=:m",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "done", ":d": out.get("drafts", []),
                                       ":m": out.get("summary", "")})
    except Exception as e:
        _job_table().update_item(
            Key=key, UpdateExpression="SET #s=:s, #e=:e",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":s": "failed", ":e": str(e)[:1000]})
        raise
```

MCP tools in `asset_tools.py` (env `ASSETS_BUCKET` already present):

```python
def list_assets(_):
    table = boto3.resource("dynamodb").Table(os.environ["REGISTRY_TABLE"])
    out = []
    for item in table.scan()["Items"]:
        aid = item.get("asset_id", "")
        if aid.startswith("job:"):
            continue
        if item.get("source") == "user":
            out.append(item)
        elif item.get("type") in ("token", "component"):
            out.append({**item, "scope": "shared", "source": "figma"})
    return {"assets": out}


def get_asset(event):
    asset_id = event.get("asset_id", "")
    table = boto3.resource("dynamodb").Table(os.environ["REGISTRY_TABLE"])
    item = table.get_item(Key={"asset_id": asset_id}).get("Item")
    if not item:
        return {"error": f"asset not found: {asset_id}"}
    body = _s3().get_object(Bucket=os.environ["ASSETS_BUCKET"],
                            Key=item["s3_key"])["Body"].read()
    return {**item, "content": body.decode()}
```

(register both in `TOOLS`; add schemas: `list_assets` no args; `get_asset` requires `asset_id`. NOTE: DynamoDB returns Decimal for `version` — the gateway serializes tool output with `json.dumps`; convert Decimals via `default=str` is not available there, so in both new tools cast `item = json.loads(json.dumps(item, default=str))` before returning.)

Harness `app.py` — inside `invoke` before building `system`:

```python
    asset_ids = payload.get("asset_ids") or []
    ...
    if asset_ids:
        system += ("\n\n사용자가 선택한 자산: " + ", ".join(asset_ids) +
                   "\n생성 전에 각 id로 get_asset을 호출해 내용을 확인하고, 선택 자산을 "
                   "최우선 규범으로 사용하라 (기본 토큰과 충돌 시 선택 자산이 이긴다). "
                   "type=workflow는 화면 흐름 제약, style-guide는 스타일 규범, "
                   "skill은 추가 지침으로 따르라.")
```

- [ ] Step 4: full suite green (30 + 3 = 33)
- [ ] Step 5: commit `feat(hana): MCP asset tools, generate dispatcher, asset-selected harness context`

---

### Task P2-3: Infra — history table, dispatcher Lambda, permissions (assertion-tested)

**Files:** Modify `hana/uiux-platform/infra/stack.py`; Test: extend `tests/test_stack.py`

**Changes (all in stack.py):**
- DynamoDB `hana-asset-history`: PK `asset_id` (S), SK `version` (S), PAY_PER_REQUEST, RemovalPolicy.DESTROY.
- Lambda `hana-generate-dispatcher`: same `code` asset, handler `dispatch.handler.handler`, PYTHON_3_13/ARM_64, timeout 900s, env `{HISTORY_TABLE, RUNTIME_ARN: ""}` — RUNTIME_ARN is set post-deploy by `deploy_runtime.py` (see note), history table grant_read_write, plus `bedrock-agentcore:InvokeAgentRuntime` on `*` (runtime ARN unknown at synth).
- Feedback Lambda (now platform API): env adds `ASSETS_BUCKET`, `REGISTRY_TABLE`, `SKILLS_BUCKET`, `HISTORY_TABLE`, `DISPATCHER_FN`; grants: assets bucket rw, skills bucket write, registry rw, history rw, `dispatcher.grant_invoke(feedback)`.
- Outputs: `HistoryTable`, `DispatcherFn`.
- `scripts/write_config.py` KEYMAP adds `HistoryTable→history_table`, `DispatcherFn→dispatcher_fn`.
- `scripts/deploy_runtime.py`: after runtime READY, also `lambda.update_function_configuration(FunctionName="hana-generate-dispatcher", Environment={... existing + RUNTIME_ARN: arn})` — merge, don't clobber (read current env first).

Tests to add to `tests/test_stack.py`:

```python
def test_history_table_and_dispatcher():
    t = synth()
    t.has_resource_properties("AWS::DynamoDB::Table", Match.object_like({
        "TableName": "hana-asset-history"}))
    t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "hana-generate-dispatcher", "Timeout": 900}))
    t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "hana-draft-feedback",
        "Environment": {"Variables": Match.object_like({
            "DISPATCHER_FN": Match.any_value(), "HISTORY_TABLE": Match.any_value()})}}))
```

- [ ] TDD steps as usual; full suite green; commit `feat(hana): infra for asset history + generate dispatcher`

---

### Task P2-4: 3-tab SPA frontend

**Files:** Rewrite `hana/uiux-platform/gallery/index.html` (single file SPA)

**Requirements (visuals per the approved canvas artboards AssetsTab/GenerateTab — Option B showcase tone, Hana palette, Noto Sans KR, 44px+ targets, Korean copy, esc() on all interpolated values):**
- Top nav: 갤러리 · 디자인 자산 · 생성 (hash routing `#gallery` `#assets` `#generate`; default gallery keeps current behavior incl. 승인/반려 with sha256 header).
- 자산 탭: registration form (이름, 유형 select of the 7 types with Korean labels, scope toggle 회사 공유, content textarea with the placeholder from the mock) → POST /api/assets (sha256 header like feedback) → refresh list. Asset list from GET /api/assets: name, type label, scope badge(내 자산 보라/회사 공유 민트), version chip, updated_at; click → right/below panel loads GET /api/assets/history and renders a timeline (v, action, actor, time). No 되돌리기 in Phase 2 (mock had it — render disabled with '준비중' tooltip title).
- 생성 탭: brief textarea; asset checkbox grid from GET /api/assets (preselect none); [시안 3종 생성] → POST /api/generate → job appears in right column; poll GET /api/jobs?job_id every 10s until done/failed (stop polling then); done shows draft links + "갤러리에서 보기"(switch tab). Persist nothing; jobs list is session-local (created this page-load).
- All fetches: JSON, on non-ok show alert with error. POSTs include x-amz-content-sha256 (reuse existing helper).

- [ ] Implement, then sanity: `python -m pytest tests -q` still green (no py changes); check HTML with `node -e` not required. Commit `feat(hana): 3-tab SPA — assets registration/history + selective generation`

---

### Task P2-5 (ORCHESTRATOR): deploy + e2e

1. `cdk deploy` → `write_config.py` → `deploy_gateway.py` (updates target with 8 tool schemas) → `deploy_runtime.py` (rebuild image with new harness prompt; sets dispatcher RUNTIME_ARN) → upload new index.html to drafts bucket.
2. E2E: POST /api/assets (독자 팔레트, e.g. purple #7d2882 계열) → GET /api/assets shows it → POST /api/generate with that asset selected → poll job → drafts exist AND visually verify the generated HTML uses the registered palette (grep the hex in the draft) → history shows v1 → gallery renders all three tabs.
3. Update README (new API routes, asset types), commit, merge.

## Self-review notes
- API auth remains PoC-open (documented limitation, unchanged from Phase 1 decision).
- Decimal serialization handled in `_resp(default=str)` and MCP tools (json round-trip).
- Dispatcher async invoke needs Lambda self-concurrency headroom — default account limits fine for PoC.
