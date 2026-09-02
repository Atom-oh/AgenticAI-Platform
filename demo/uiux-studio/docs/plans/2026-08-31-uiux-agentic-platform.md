# Hana UI/UX Agentic AI Platform PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a working PoC of an org-wide UI/UX Agentic AI Platform: Figma asset ingestion → shared design-asset MCP (AgentCore Gateway) → shared skill registry → design-draft harness (AgentCore Runtime) → CloudFront gallery.

**Architecture:** CDK (Python) provisions S3/DynamoDB/Lambda/Cognito/CloudFront/Secrets; boto3 scripts provision AgentCore Gateway + Runtime (no mature CDK support). A Strands agent on AgentCore Runtime consumes the Gateway MCP tools and the skill registry to generate 3 axis-driven HTML draft variants per brief, published to a CloudFront gallery.

**Tech Stack:** Python 3.13, AWS CDK v2 (Python), boto3, Strands Agents + bedrock-agentcore SDK, pytest + moto, Figma REST API.

## Global Constraints

- Region `ap-northeast-2`, account `180294183052`. Root: `hana/uiux-platform/` (repo root is `/home/atomoh/account-test`).
- Cognito: `self_sign_up_enabled=False` set EXPLICITLY. Admin-created users only. (Org policy — violation is a defect.)
- No public compute. CloudFront (OAC) is the only public entry for the gallery. AgentCore Gateway/Runtime are AWS-managed endpoints (allowed).
- Figma PAT lives ONLY in Secrets Manager secret `hana/figma-token`. Never in code, git, env files, or logs. Operator injects the value post-deploy.
- Model: `global.anthropic.claude-sonnet-5`.
- Variation policy: tokens are law; registry components are vocabulary; 3 variants per brief, each moving one named axis (밀도/강조/흐름); default layout = single-screen.
- Buckets: `hana-design-assets-180294183052`, `hana-skill-registry-180294183052`, `hana-design-drafts-180294183052`. Table: `hana-design-registry`.
- All Python under `hana/uiux-platform/`; run tests with `python -m pytest` from `hana/uiux-platform/`.
- Commit after every task (repo already initialized on `main`).

---

### Task 1: Project scaffold + shared Figma normalizer (TDD)

**Files:**
- Create: `hana/uiux-platform/pyproject.toml`
- Create: `hana/uiux-platform/README.md`
- Create: `hana/uiux-platform/ingestion/normalizer.py`
- Test: `hana/uiux-platform/tests/test_normalizer.py`

**Interfaces:**
- Produces: `normalize_figma_file(file_json: dict) -> dict` returning `{"tokens": {"color": {name: hex}, "type": {name: {"fontFamily","fontSize","fontWeight"}}, "space": {name: int}}, "components": [{"name", "node_id", "description"}]}`.
- Seed-file conventions (the Figma seed file is authored to match): page named `Design Tokens` holds RECTANGLE nodes named `color/<name>` (solid fill = value), TEXT nodes named `type/<name>`, FRAME nodes named `space/<name>` (width = px value). Page `Components` holds COMPONENT nodes (name + description).

- [ ] **Step 1: Scaffold project metadata**

`hana/uiux-platform/pyproject.toml`:

```toml
[project]
name = "hana-uiux-platform"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`hana/uiux-platform/README.md`:

```markdown
# Hana UI/UX Agentic AI Platform (PoC)

Org-wide agentic platform for UI/UX work: Figma ingestion → shared design-asset
MCP (AgentCore Gateway) → shared skill registry → design-draft harness
(AgentCore Runtime) → CloudFront gallery.

Spec: docs/specs/2026-08-31-uiux-agentic-platform-design.md

## Layout
- ingestion/  Figma sync Lambda + normalizer
- mcp/        Gateway Lambda tools + tool schemas
- skills/     Org skill registry seed content
- harness/    Strands agent container for AgentCore Runtime
- gallery/    Showcase gallery static frontend
- infra/      CDK app (Python)
- scripts/    Gateway/Runtime deploy, e2e verify, teardown

## Deploy (order matters)
1. `cd infra && pip install -r requirements.txt && cdk deploy` → writes `config/stack.json`
2. `aws secretsmanager put-secret-value --secret-id hana/figma-token --secret-string '<PAT>'` (operator; PAT never committed)
3. `python scripts/deploy_gateway.py` → Gateway MCP URL into `config/stack.json`
4. `python scripts/deploy_runtime.py` → builds/pushes harness image, creates Runtime
5. `python scripts/sync_skills.py` → seed skills to registry bucket
6. `aws lambda invoke --function-name hana-figma-sync --payload '{"file_key":"<from config/figma.json>"}' out.json`
7. `python scripts/verify_e2e.py` → full pipeline check
```

- [ ] **Step 2: Write the failing normalizer test**

`hana/uiux-platform/tests/test_normalizer.py`:

```python
from ingestion.normalizer import normalize_figma_file

SAMPLE = {
    "name": "Hana Design System",
    "document": {
        "children": [
            {
                "name": "Design Tokens",
                "type": "CANVAS",
                "children": [
                    {"type": "RECTANGLE", "name": "color/primary",
                     "fills": [{"type": "SOLID", "color": {"r": 0.0, "g": 0.5176, "b": 0.5215}}]},
                    {"type": "TEXT", "name": "type/heading",
                     "style": {"fontFamily": "Noto Sans KR", "fontSize": 22.0, "fontWeight": 700}},
                    {"type": "FRAME", "name": "space/md",
                     "absoluteBoundingBox": {"width": 16.0, "height": 8.0}, "children": []},
                ],
            },
            {
                "name": "Components",
                "type": "CANVAS",
                "children": [
                    {"type": "COMPONENT", "id": "12:34", "name": "Button/Primary",
                     "description": "Primary CTA. 56px height, radius 14, bg color/primary."},
                    {"type": "FRAME", "id": "12:99", "name": "scratch", "children": []},
                ],
            },
        ]
    },
}


def test_extracts_color_tokens_as_hex():
    out = normalize_figma_file(SAMPLE)
    assert out["tokens"]["color"]["primary"] == "#008485"


def test_extracts_type_and_space_tokens():
    out = normalize_figma_file(SAMPLE)
    assert out["tokens"]["type"]["heading"] == {
        "fontFamily": "Noto Sans KR", "fontSize": 22, "fontWeight": 700}
    assert out["tokens"]["space"]["md"] == 16


def test_extracts_components_only():
    out = normalize_figma_file(SAMPLE)
    assert out["components"] == [{
        "name": "Button/Primary", "node_id": "12:34",
        "description": "Primary CTA. 56px height, radius 14, bg color/primary."}]


def test_missing_pages_yield_empty():
    out = normalize_figma_file({"document": {"children": []}})
    assert out == {"tokens": {"color": {}, "type": {}, "space": {}}, "components": []}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd hana/uiux-platform && python -m pytest tests/test_normalizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingestion'`

- [ ] **Step 4: Implement the normalizer**

`hana/uiux-platform/ingestion/normalizer.py` (add empty `hana/uiux-platform/ingestion/__init__.py` and `hana/uiux-platform/tests/__init__.py`):

```python
"""Normalize a Figma file JSON (GET /v1/files/{key}) into design tokens + components.

Seed-file conventions: page 'Design Tokens' holds RECTANGLE 'color/<name>',
TEXT 'type/<name>', FRAME 'space/<name>' (width=px); page 'Components' holds
COMPONENT nodes.
"""


def _hex(c: dict) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(c.get("r", 0) * 255), round(c.get("g", 0) * 255), round(c.get("b", 0) * 255))


def _walk(node: dict):
    yield node
    for child in node.get("children", []) or []:
        yield from _walk(child)


def normalize_figma_file(file_json: dict) -> dict:
    tokens = {"color": {}, "type": {}, "space": {}}
    components = []
    for page in file_json.get("document", {}).get("children", []) or []:
        if page.get("name") == "Design Tokens":
            for n in _walk(page):
                name = n.get("name", "")
                kind, _, key = name.partition("/")
                if not key:
                    continue
                if kind == "color" and n.get("type") == "RECTANGLE":
                    solid = next((f for f in n.get("fills", []) if f.get("type") == "SOLID"), None)
                    if solid:
                        tokens["color"][key] = _hex(solid["color"])
                elif kind == "type" and n.get("type") == "TEXT":
                    s = n.get("style", {})
                    tokens["type"][key] = {
                        "fontFamily": s.get("fontFamily"),
                        "fontSize": round(s.get("fontSize", 0)),
                        "fontWeight": s.get("fontWeight"),
                    }
                elif kind == "space" and n.get("type") == "FRAME":
                    tokens["space"][key] = round(n.get("absoluteBoundingBox", {}).get("width", 0))
        elif page.get("name") == "Components":
            for n in _walk(page):
                if n.get("type") == "COMPONENT":
                    components.append({
                        "name": n.get("name"), "node_id": n.get("id"),
                        "description": n.get("description", "")})
    return {"tokens": tokens, "components": components}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd hana/uiux-platform && python -m pytest tests/test_normalizer.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): scaffold uiux-platform + figma token normalizer"
```

---

### Task 2: figma-sync Lambda (TDD)

**Files:**
- Create: `hana/uiux-platform/ingestion/figma_sync.py`
- Test: `hana/uiux-platform/tests/test_figma_sync.py`

**Interfaces:**
- Consumes: `normalize_figma_file` from Task 1.
- Produces: Lambda `handler(event, context)`; event `{"file_key": str}`. Side effects: writes `tokens/latest.json` and `components/<node_id>.json` to assets bucket; upserts DynamoDB items `{"asset_id": "token:latest"|"component:<node_id>", "type", "name", "version", "s3_key", "figma_node_id", "updated_at"}`. Returns `{"synced": {"tokens": int, "components": int}}`.
- Env vars (set by CDK in Task 5): `ASSETS_BUCKET`, `REGISTRY_TABLE`, `FIGMA_SECRET_ID`.
- Figma API access is isolated in `fetch_figma_file(file_key, token) -> dict` (stdlib urllib; retries 429/5xx up to 3 times with 2^n backoff) so tests can monkeypatch it.

- [ ] **Step 1: Write the failing test**

`hana/uiux-platform/tests/test_figma_sync.py`:

```python
import json
import boto3
import pytest
from moto import mock_aws

SAMPLE = {
    "name": "Hana DS",
    "document": {"children": [
        {"name": "Design Tokens", "type": "CANVAS", "children": [
            {"type": "RECTANGLE", "name": "color/primary",
             "fills": [{"type": "SOLID", "color": {"r": 0.0, "g": 0.5176, "b": 0.5215}}]}]},
        {"name": "Components", "type": "CANVAS", "children": [
            {"type": "COMPONENT", "id": "1:2", "name": "Button/Primary", "description": "CTA"}]},
    ]},
}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("ASSETS_BUCKET", "assets")
    monkeypatch.setenv("REGISTRY_TABLE", "registry")
    monkeypatch.setenv("FIGMA_SECRET_ID", "hana/figma-token")
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-2")
        s3.create_bucket(Bucket="assets",
                         CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        ddb = boto3.client("dynamodb", region_name="ap-northeast-2")
        ddb.create_table(TableName="registry",
                         KeySchema=[{"AttributeName": "asset_id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "asset_id", "AttributeType": "S"}],
                         BillingMode="PAY_PER_REQUEST")
        sm = boto3.client("secretsmanager", region_name="ap-northeast-2")
        sm.create_secret(Name="hana/figma-token", SecretString="figd_test")
        yield s3, ddb


def test_sync_writes_tokens_components_and_registry(aws, monkeypatch):
    s3, ddb = aws
    from ingestion import figma_sync
    monkeypatch.setattr(figma_sync, "fetch_figma_file", lambda key, token: SAMPLE)
    out = figma_sync.handler({"file_key": "abc123"}, None)
    assert out == {"synced": {"tokens": 1, "components": 1}}
    tokens = json.loads(s3.get_object(Bucket="assets", Key="tokens/latest.json")["Body"].read())
    assert tokens["tokens"]["color"]["primary"] == "#008485"
    item = ddb.get_item(TableName="registry", Key={"asset_id": {"S": "component:1:2"}})["Item"]
    assert item["name"]["S"] == "Button/Primary"
    assert item["s3_key"]["S"] == "components/1:2.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hana/uiux-platform && pip install moto[all] boto3 pytest -q && python -m pytest tests/test_figma_sync.py -v`
Expected: FAIL (`figma_sync` missing)

- [ ] **Step 3: Implement**

`hana/uiux-platform/ingestion/figma_sync.py`:

```python
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

from ingestion.normalizer import normalize_figma_file

FIGMA_API = "https://api.figma.com/v1"


def fetch_figma_file(file_key: str, token: str) -> dict:
    req = urllib.request.Request(f"{FIGMA_API}/files/{file_key}",
                                 headers={"X-Figma-Token": token})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def _figma_token() -> str:
    sm = boto3.client("secretsmanager")
    return sm.get_secret_value(SecretId=os.environ["FIGMA_SECRET_ID"])["SecretString"]


def handler(event, context):
    file_key = event["file_key"]
    normalized = normalize_figma_file(fetch_figma_file(file_key, _figma_token()))
    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(os.environ["REGISTRY_TABLE"])
    bucket = os.environ["ASSETS_BUCKET"]
    now = datetime.now(timezone.utc).isoformat()

    s3.put_object(Bucket=bucket, Key="tokens/latest.json",
                  Body=json.dumps(normalized, ensure_ascii=False).encode(),
                  ContentType="application/json")
    table.put_item(Item={"asset_id": "token:latest", "type": "token", "name": "hana-tokens",
                         "version": "latest", "s3_key": "tokens/latest.json",
                         "figma_node_id": file_key, "updated_at": now})
    for comp in normalized["components"]:
        key = f"components/{comp['node_id']}.json"
        s3.put_object(Bucket=bucket, Key=key,
                      Body=json.dumps(comp, ensure_ascii=False).encode(),
                      ContentType="application/json")
        table.put_item(Item={"asset_id": f"component:{comp['node_id']}", "type": "component",
                             "name": comp["name"], "version": "latest", "s3_key": key,
                             "figma_node_id": comp["node_id"], "updated_at": now})
    return {"synced": {"tokens": 1, "components": len(normalized["components"])}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hana/uiux-platform && python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): figma-sync lambda with secrets-manager PAT and asset registry"
```

---

### Task 3: Design-asset MCP tool Lambda (TDD)

**Files:**
- Create: `hana/uiux-platform/mcp/asset_tools.py`
- Create: `hana/uiux-platform/mcp/tool_schemas.py`
- Test: `hana/uiux-platform/tests/test_asset_tools.py`

**Interfaces:**
- Consumes: S3/DynamoDB layout from Task 2; skill registry layout `skills/<name>/<version>/SKILL.md` (Task 4).
- Produces: Lambda `handler(event, context)` for an AgentCore Gateway lambda target. Gateway passes the tool name as `context.client_context.custom["bedrockAgentCoreToolName"]` (format `<target>___<tool>`); `event` is the tool input dict. Tools: `list_design_tokens()`, `search_assets(query)`, `get_component(node_id)`, `get_brand_guideline()`, `list_skills()`, `get_skill(name)`. Errors return `{"error": "<message>"}` (never raise).
- Produces: `TOOL_SCHEMAS` list (MCP inlinePayload format) used by `deploy_gateway.py` in Task 7.
- Env vars: `ASSETS_BUCKET`, `REGISTRY_TABLE`, `SKILLS_BUCKET`.

- [ ] **Step 1: Write the failing test**

`hana/uiux-platform/tests/test_asset_tools.py`:

```python
import json
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws


def ctx(tool):
    return SimpleNamespace(client_context=SimpleNamespace(
        custom={"bedrockAgentCoreToolName": f"design-asset-tools___{tool}"}))


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("ASSETS_BUCKET", "assets")
    monkeypatch.setenv("REGISTRY_TABLE", "registry")
    monkeypatch.setenv("SKILLS_BUCKET", "skills")
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-2")
        cfg = {"CreateBucketConfiguration": {"LocationConstraint": "ap-northeast-2"}}
        s3.create_bucket(Bucket="assets", **cfg)
        s3.create_bucket(Bucket="skills", **cfg)
        s3.put_object(Bucket="assets", Key="tokens/latest.json", Body=json.dumps(
            {"tokens": {"color": {"primary": "#008485"}}, "components": []}))
        s3.put_object(Bucket="assets", Key="components/1:2.json", Body=json.dumps(
            {"name": "Button/Primary", "node_id": "1:2", "description": "CTA"}))
        s3.put_object(Bucket="skills", Key="skills/design-draft-html/1.0.0/SKILL.md",
                      Body=b"# design-draft-html\nrules")
        ddb = boto3.client("dynamodb", region_name="ap-northeast-2")
        ddb.create_table(TableName="registry",
                         KeySchema=[{"AttributeName": "asset_id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "asset_id", "AttributeType": "S"}],
                         BillingMode="PAY_PER_REQUEST")
        boto3.resource("dynamodb", region_name="ap-northeast-2").Table("registry").put_item(
            Item={"asset_id": "component:1:2", "type": "component", "name": "Button/Primary",
                  "version": "latest", "s3_key": "components/1:2.json",
                  "figma_node_id": "1:2", "updated_at": "now"})
        yield


def test_list_design_tokens(aws):
    from mcp.asset_tools import handler
    out = handler({}, ctx("list_design_tokens"))
    assert out["tokens"]["color"]["primary"] == "#008485"


def test_search_assets(aws):
    from mcp.asset_tools import handler
    out = handler({"query": "button"}, ctx("search_assets"))
    assert out["results"][0]["asset_id"] == "component:1:2"


def test_get_component_and_missing(aws):
    from mcp.asset_tools import handler
    assert handler({"node_id": "1:2"}, ctx("get_component"))["name"] == "Button/Primary"
    assert "error" in handler({"node_id": "9:9"}, ctx("get_component"))


def test_skills_tools(aws):
    from mcp.asset_tools import handler
    assert handler({}, ctx("list_skills"))["skills"] == [
        {"name": "design-draft-html", "version": "1.0.0"}]
    assert "design-draft-html" in handler({"name": "design-draft-html"}, ctx("get_skill"))["content"]


def test_unknown_tool(aws):
    from mcp.asset_tools import handler
    assert "error" in handler({}, ctx("nope"))


def test_schemas_cover_all_tools():
    from mcp.tool_schemas import TOOL_SCHEMAS
    assert {s["name"] for s in TOOL_SCHEMAS} == {
        "list_design_tokens", "search_assets", "get_component",
        "get_brand_guideline", "list_skills", "get_skill"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hana/uiux-platform && python -m pytest tests/test_asset_tools.py -v`
Expected: FAIL (`mcp.asset_tools` missing)

- [ ] **Step 3: Implement**

`hana/uiux-platform/mcp/asset_tools.py` (add empty `mcp/__init__.py`):

```python
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
```

`hana/uiux-platform/mcp/tool_schemas.py`:

```python
def _tool(name, description, properties=None, required=None):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": required or []}}


TOOL_SCHEMAS = [
    _tool("list_design_tokens", "Return the Hana design tokens (color/type/space) and component index."),
    _tool("search_assets", "Search the design asset registry by name.",
          {"query": {"type": "string", "description": "substring to match asset names"}}, ["query"]),
    _tool("get_component", "Get one component's semantic metadata by Figma node id.",
          {"node_id": {"type": "string"}}, ["node_id"]),
    _tool("get_brand_guideline", "Return Hana brand palette, font, and draft-generation rules."),
    _tool("list_skills", "List org-shared skills in the skill registry."),
    _tool("get_skill", "Fetch a skill's SKILL.md content (latest version).",
          {"name": {"type": "string"}}, ["name"]),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd hana/uiux-platform && python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): design-asset MCP tool lambda + gateway tool schemas"
```

---

### Task 4: Skill registry seed content + sync script

**Files:**
- Create: `hana/uiux-platform/skills/hana-design-system/1.0.0/SKILL.md`
- Create: `hana/uiux-platform/skills/design-draft-html/1.0.0/SKILL.md`
- Create: `hana/uiux-platform/skills/a11y-finance/1.0.0/SKILL.md`
- Create: `hana/uiux-platform/scripts/sync_skills.py`

**Interfaces:**
- Produces: S3 layout `skills/<name>/<version>/SKILL.md` in `SKILLS_BUCKET` consumed by Task 3's `list_skills`/`get_skill`. `sync_skills.py` reads bucket name from `config/stack.json` (written by Task 5).

- [ ] **Step 1: Write the three skills**

`skills/hana-design-system/1.0.0/SKILL.md`:

```markdown
---
name: hana-design-system
description: How to apply Hana design tokens and registry components in generated UI.
---

# Hana Design System

1. ALWAYS call `list_design_tokens` first. Tokens are law: every color, font
   size, and spacing value in your output must come from `tokens.color`,
   `tokens.type`, `tokens.space`. Never invent values.
2. Call `get_brand_guideline` for palette roles: `primary` (#008485) is the
   single accent — CTAs, active states, key highlights. `primaryDark` for
   pressed/secondary emphasis. `ink` for text, `bg` for page ground,
   `mist` for tinted surfaces.
3. Components in the registry are your vocabulary: `search_assets` /
   `get_component` give each component's purpose and usage rules. Prefer
   them; compose new elements only from token values.
4. Typography: Noto Sans KR only. Weight 900 for screen titles, 700 for
   section labels and CTAs, 400-500 for body.
```

`skills/design-draft-html/1.0.0/SKILL.md`:

```markdown
---
name: design-draft-html
description: Rules for generating a Hana UI draft screen as self-contained HTML.
---

# Design Draft HTML

Output: ONE self-contained HTML file per variant. Inline CSS only. Google
Fonts Noto Sans KR via <link>. Mobile frame 390px wide, min-height 844px.

Layout default: SINGLE-SCREEN, card-sectioned (approved direction) — white
cards (radius 16px) on #f4f6f5 ground, one section per concern
(출금계좌 / 받는분 / 금액 / 확인), one primary CTA pinned at the bottom.

Hard rules:
- Hit targets >= 44px. Body text >= 13px.
- NO fake device chrome: no status bar, no clock/battery, no fake keyboard.
- Korean copy, real-sounding but sample data (김하나, 하나 주거래 통장 …).
- All colors/type/spacing from the design tokens (see hana-design-system).

Variation policy — produce exactly 3 variants per brief, each moving ONE axis:
1. 밀도 (density): compact ↔ airy — spacing scale and card padding shift.
2. 강조 (hierarchy): which section dominates (e.g. 금액 중심 vs 받는분 중심).
3. 흐름 (flow): single-screen (default) vs stepped wizard.
Name each variant with its axis (e.g. "v1-밀도-compact"). Do NOT produce
variants by randomly recombining components — near-identical output is a
failure.
```

`skills/a11y-finance/1.0.0/SKILL.md`:

```markdown
---
name: a11y-finance
description: 금융권 웹접근성 체크리스트 (KWCAG-informed) for generated drafts.
---

# Accessibility — Finance

- Text contrast >= 4.5:1 against its background (#17332f on #fbfcfb passes;
  never place #8aa19c text on #e6f3f2).
- Every interactive element: >= 44px hit target, visible focus style.
- Amounts and account numbers: never color-only meaning; pair with label text.
- Form inputs carry <label>; buttons are <button>, not styled <div>, in
  final production handoff (drafts may use divs but must note it).
- Font sizes scale with rem; minimum body 13px, 시니어 모드 16px+.
```

- [ ] **Step 2: Write the sync script**

`hana/uiux-platform/scripts/sync_skills.py`:

```python
"""Upload skills/ tree to the org skill registry bucket (from config/stack.json)."""
import json
import mimetypes
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    bucket = cfg["skills_bucket"]
    s3 = boto3.client("s3", region_name=cfg["region"])
    count = 0
    for path in (ROOT / "skills").rglob("*"):
        if path.is_file():
            key = f"skills/{path.relative_to(ROOT / 'skills')}"
            s3.upload_file(str(path), bucket, key, ExtraArgs={
                "ContentType": mimetypes.guess_type(path.name)[0] or "text/markdown"})
            count += 1
    print(f"synced {count} skill files to s3://{bucket}/skills/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Sanity check + commit**

Run: `cd hana/uiux-platform && python -m pytest tests/ -v` (still green — no code touched)

```bash
git add hana/uiux-platform && git commit -m "feat(hana): seed org skill registry (design-system, draft-html, a11y) + sync script"
```

---

### Task 5: CDK infra stack (assertion-tested)

**Files:**
- Create: `hana/uiux-platform/infra/app.py`
- Create: `hana/uiux-platform/infra/stack.py`
- Create: `hana/uiux-platform/infra/cdk.json`
- Create: `hana/uiux-platform/infra/requirements.txt`
- Test: `hana/uiux-platform/tests/test_stack.py`

**Interfaces:**
- Produces stack `HanaUiuxPlatform` with outputs (consumed by deploy scripts via `config/stack.json`): `AssetsBucket`, `SkillsBucket`, `DraftsBucket`, `RegistryTable`, `FigmaSecretArn`, `UserPoolId`, `M2MClientId`, `CognitoDomain`, `CognitoDiscoveryUrl`, `DistributionDomain`, `GatewayRoleArn`, `RuntimeRoleArn`, `FigmaSyncFn`, `AssetToolsFnArn`.
- Lambdas package `ingestion/` + `mcp/` via bundled asset (code root = project dir).

- [ ] **Step 1: Write the failing assertion test**

`hana/uiux-platform/tests/test_stack.py`:

```python
import aws_cdk as cdk
from aws_cdk.assertions import Match, Template


def synth():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "infra"))
    from stack import HanaUiuxPlatformStack
    app = cdk.App()
    return Template.from_stack(HanaUiuxPlatformStack(
        app, "HanaUiuxPlatform",
        env=cdk.Environment(account="180294183052", region="ap-northeast-2")))


def test_cognito_self_signup_disabled():
    t = synth()
    t.has_resource_properties("AWS::Cognito::UserPool", {
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True}})


def test_m2m_client_uses_client_credentials():
    t = synth()
    t.has_resource_properties("AWS::Cognito::UserPoolClient", {
        "AllowedOAuthFlows": ["client_credentials"], "GenerateSecret": True})


def test_no_public_bucket_and_oac_distribution():
    t = synth()
    t.resource_count_is("AWS::CloudFront::Distribution", 1)
    for props in t.find_resources("AWS::S3::Bucket").values():
        cfg = props["Properties"]["PublicAccessBlockConfiguration"]
        assert cfg["BlockPublicPolicy"] is True


def test_lambdas_have_env():
    t = synth()
    t.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "FunctionName": "hana-figma-sync",
        "Environment": {"Variables": Match.object_like({"FIGMA_SECRET_ID": "hana/figma-token"})}}))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hana/uiux-platform && pip install aws-cdk-lib constructs -q && python -m pytest tests/test_stack.py -v`
Expected: FAIL (no `stack` module)

- [ ] **Step 3: Implement the stack**

`hana/uiux-platform/infra/requirements.txt`:

```
aws-cdk-lib>=2.190.0
constructs>=10.0.0
```

`hana/uiux-platform/infra/cdk.json`:

```json
{ "app": "python3 app.py" }
```

`hana/uiux-platform/infra/stack.py`:

```python
import aws_cdk as cdk
from aws_cdk import (
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_secretsmanager as sm,
)
from constructs import Construct

ACCOUNT = "180294183052"


class HanaUiuxPlatformStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)

        def bucket(name):
            return s3.Bucket(self, name.title().replace("-", ""),
                             bucket_name=f"hana-{name}-{ACCOUNT}",
                             block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                             removal_policy=cdk.RemovalPolicy.DESTROY,
                             auto_delete_objects=True)

        assets = bucket("design-assets")
        skills = bucket("skill-registry")
        drafts = bucket("design-drafts")

        registry = ddb.Table(self, "Registry", table_name="hana-design-registry",
                             partition_key=ddb.Attribute(name="asset_id",
                                                         type=ddb.AttributeType.STRING),
                             billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                             removal_policy=cdk.RemovalPolicy.DESTROY)

        figma_secret = sm.Secret(self, "FigmaToken", secret_name="hana/figma-token",
                                 description="Figma PAT (operator-injected, temporary)")

        code = lambda_.Code.from_asset("..", exclude=[
            "infra", "harness", "gallery", "docs", "design-canvas", "tests",
            "scripts", "config", ".venv", "**/__pycache__"])
        common_env = {"ASSETS_BUCKET": assets.bucket_name,
                      "REGISTRY_TABLE": registry.table_name,
                      "SKILLS_BUCKET": skills.bucket_name,
                      "FIGMA_SECRET_ID": "hana/figma-token"}

        figma_sync = lambda_.Function(
            self, "FigmaSync", function_name="hana-figma-sync",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="ingestion.figma_sync.handler", code=code,
            timeout=cdk.Duration.minutes(2), environment=common_env)
        asset_tools = lambda_.Function(
            self, "AssetTools", function_name="hana-design-asset-tools",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="mcp.asset_tools.handler", code=code,
            timeout=cdk.Duration.seconds(30), environment=common_env)

        assets.grant_read_write(figma_sync)
        registry.grant_read_write_data(figma_sync)
        figma_secret.grant_read(figma_sync)
        assets.grant_read(asset_tools)
        skills.grant_read(asset_tools)
        registry.grant_read_data(asset_tools)

        pool = cognito.UserPool(
            self, "Pool", user_pool_name="hana-uiux-platform",
            self_sign_up_enabled=False,  # org policy: admin-created users only
            removal_policy=cdk.RemovalPolicy.DESTROY)
        domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(
            domain_prefix=f"hana-uiux-{ACCOUNT}"))
        server = pool.add_resource_server("Rs", identifier="hana-mcp", scopes=[
            cognito.ResourceServerScope(scope_name="invoke", scope_description="invoke MCP")])
        m2m = pool.add_client("M2M", generate_secret=True, o_auth=cognito.OAuthSettings(
            flows=cognito.OAuthFlows(client_credentials=True),
            scopes=[cognito.OAuthScope.resource_server(
                server, cognito.ResourceServerScope(scope_name="invoke",
                                                    scope_description="invoke MCP"))]))

        dist = cloudfront.Distribution(
            self, "Gallery",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(drafts),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS))

        gw_role = iam.Role(self, "GatewayRole", role_name="hana-agentcore-gateway",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        asset_tools.grant_invoke(gw_role)

        rt_role = iam.Role(self, "RuntimeRole", role_name="hana-agentcore-runtime",
                           assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        drafts.grant_read_write(rt_role)
        skills.grant_read(rt_role)
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"]))
        rt_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken", "ecr:BatchGetImage",
                     "ecr:GetDownloadUrlForLayer", "logs:CreateLogGroup",
                     "logs:CreateLogStream", "logs:PutLogEvents",
                     "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                     "cloudwatch:PutMetricData"],
            resources=["*"]))

        discovery = (f"https://cognito-idp.{self.region}.amazonaws.com/"
                     f"{pool.user_pool_id}/.well-known/openid-configuration")
        for name, value in {
            "AssetsBucket": assets.bucket_name, "SkillsBucket": skills.bucket_name,
            "DraftsBucket": drafts.bucket_name, "RegistryTable": registry.table_name,
            "FigmaSecretArn": figma_secret.secret_arn, "UserPoolId": pool.user_pool_id,
            "M2MClientId": m2m.user_pool_client_id,
            "CognitoDomain": f"{domain.domain_name}.auth.{self.region}.amazoncognito.com",
            "CognitoDiscoveryUrl": discovery,
            "DistributionDomain": dist.distribution_domain_name,
            "GatewayRoleArn": gw_role.role_arn, "RuntimeRoleArn": rt_role.role_arn,
            "FigmaSyncFn": figma_sync.function_name, "AssetToolsFnArn": asset_tools.function_arn,
        }.items():
            cdk.CfnOutput(self, name, value=value)
```

`hana/uiux-platform/infra/app.py`:

```python
import aws_cdk as cdk
from stack import HanaUiuxPlatformStack

app = cdk.App()
HanaUiuxPlatformStack(app, "HanaUiuxPlatform",
                      env=cdk.Environment(account="180294183052", region="ap-northeast-2"))
app.synth()
```

- [ ] **Step 4: Run tests**

Run: `cd hana/uiux-platform && python -m pytest tests/test_stack.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): CDK stack — buckets, registry, cognito (no self-signup), cloudfront, agentcore roles"
```

---

### Task 6: Showcase gallery frontend (Option B)

**Files:**
- Create: `hana/uiux-platform/gallery/index.html`
- Create: `hana/uiux-platform/gallery/drafts.json`
- Create: `hana/uiux-platform/scripts/deploy_gallery.py`

**Interfaces:**
- Produces: static gallery reading `./drafts.json` — `{"drafts": [{"id", "title", "brief", "axis", "status", "url", "created_at"}]}`. The harness (Task 8) appends entries to this manifest in the drafts bucket. `deploy_gallery.py` uploads `gallery/` to the drafts bucket root.
- Visual: approved Option B showcase — prompt-first hero, large cards, Hana palette (#008485/#00615f/#17332f/#fbfcfb), Noto Sans KR.

- [ ] **Step 1: Write the gallery page**

`hana/uiux-platform/gallery/index.html`:

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hana Design Studio — 시안 갤러리</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap">
<style>
  body { margin:0; font-family:'Noto Sans KR','Apple SD Gothic Neo',sans-serif;
         background:#fbfcfb; color:#17332f; }
  header { display:flex; align-items:center; justify-content:space-between;
           padding:0 48px; height:68px; }
  .logo { display:flex; align-items:center; gap:10px; font-weight:900; font-size:15px; }
  .logo i { width:26px; height:26px; border-radius:50%; background:#008485; }
  .hero { display:flex; flex-direction:column; align-items:center; gap:22px;
          padding:44px 24px 36px; }
  .hero h1 { margin:0; font-size:clamp(24px,4vw,34px); font-weight:900;
             letter-spacing:-0.03em; }
  .hero p { margin:0; color:#5c6f6b; font-size:14px; }
  main { max-width:1344px; margin:0 auto; padding:0 48px 60px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:24px; }
  .card { text-decoration:none; color:inherit; display:flex; flex-direction:column; gap:12px; }
  .thumb { height:300px; border-radius:18px; overflow:hidden; border:1px solid #e2e8e6;
           background:linear-gradient(160deg,#e9f4f3 0%,#d8ebe9 100%); }
  .thumb iframe { width:200%; height:200%; border:0; transform:scale(.5);
                  transform-origin:0 0; pointer-events:none; }
  .meta { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .title { font-size:15px; font-weight:700; }
  .sub { font-size:12px; color:#8aa19c; }
  .badge { font-size:11px; font-weight:700; padding:4px 10px; border-radius:20px;
           background:#eef1f0; color:#5c6f6b; white-space:nowrap; }
  .badge.approved { background:#e6f3f2; color:#00615f; }
  .badge.review { background:#f8f0dd; color:#9a7522; }
  .empty { text-align:center; color:#8aa19c; padding:80px 0; font-size:14px; }
</style>
</head>
<body>
<header>
  <div class="logo"><i></i>Hana Design Studio</div>
  <div class="sub">Agentic UI/UX Platform PoC · ap-northeast-2</div>
</header>
<div class="hero">
  <h1>어떤 화면을 만들어 볼까요?</h1>
  <p>scripts/invoke.py 로 브리프를 보내면, 생성된 시안이 아래에 나타납니다.</p>
</div>
<main>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" hidden>아직 생성된 시안이 없습니다.</div>
</main>
<script>
  const badgeClass = s => s === '승인됨' ? 'badge approved'
                        : s === '검토중' ? 'badge review' : 'badge';
  fetch('./drafts.json', {cache: 'no-store'}).then(r => r.json()).then(d => {
    const drafts = (d.drafts || []).slice().reverse();
    if (!drafts.length) { document.getElementById('empty').hidden = false; return; }
    document.getElementById('grid').innerHTML = drafts.map(x => `
      <a class="card" href="${x.url}" target="_blank" rel="noopener">
        <div class="thumb"><iframe src="${x.url}" loading="lazy" tabindex="-1"></iframe></div>
        <div class="meta">
          <div>
            <div class="title">${x.title}</div>
            <div class="sub">${x.axis} · ${new Date(x.created_at).toLocaleString('ko-KR')}</div>
          </div>
          <div class="${badgeClass(x.status)}">${x.status}</div>
        </div>
      </a>`).join('');
  }).catch(() => { document.getElementById('empty').hidden = false; });
</script>
</body>
</html>
```

`hana/uiux-platform/gallery/drafts.json`:

```json
{ "drafts": [] }
```

- [ ] **Step 2: Write the upload script**

`hana/uiux-platform/scripts/deploy_gallery.py`:

```python
"""Upload gallery/ to the drafts bucket root (CloudFront default origin)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
TYPES = {".html": "text/html", ".json": "application/json"}


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    s3 = boto3.client("s3", region_name=cfg["region"])
    for path in (ROOT / "gallery").iterdir():
        # never clobber a live manifest that already has drafts
        if path.name == "drafts.json":
            try:
                s3.head_object(Bucket=cfg["drafts_bucket"], Key="drafts.json")
                continue
            except s3.exceptions.ClientError:
                pass
        s3.upload_file(str(path), cfg["drafts_bucket"], path.name,
                       ExtraArgs={"ContentType": TYPES.get(path.suffix, "text/plain")})
        print(f"uploaded {path.name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): showcase gallery frontend (Option B) + deploy script"
```

---

### Task 7: Gateway + config deploy scripts

**Files:**
- Create: `hana/uiux-platform/scripts/write_config.py`
- Create: `hana/uiux-platform/scripts/deploy_gateway.py`

**Interfaces:**
- Consumes: stack outputs (Task 5), `TOOL_SCHEMAS` (Task 3).
- Produces: `config/stack.json` with snake_case keys used by every other script: `region, account, assets_bucket, skills_bucket, drafts_bucket, registry_table, user_pool_id, m2m_client_id, m2m_client_secret (NOT stored — fetched live), cognito_domain, discovery_url, distribution_domain, gateway_role_arn, runtime_role_arn, figma_sync_fn, asset_tools_fn_arn, gateway_url, gateway_id, runtime_arn`.
- `deploy_gateway.py` is idempotent (get-or-create by name `hana-design-assets-gw`).

- [ ] **Step 1: Write config extractor**

`hana/uiux-platform/scripts/write_config.py`:

```python
"""Read HanaUiuxPlatform stack outputs into config/stack.json (idempotent merge)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
KEYMAP = {
    "AssetsBucket": "assets_bucket", "SkillsBucket": "skills_bucket",
    "DraftsBucket": "drafts_bucket", "RegistryTable": "registry_table",
    "UserPoolId": "user_pool_id", "M2MClientId": "m2m_client_id",
    "CognitoDomain": "cognito_domain", "CognitoDiscoveryUrl": "discovery_url",
    "DistributionDomain": "distribution_domain", "GatewayRoleArn": "gateway_role_arn",
    "RuntimeRoleArn": "runtime_role_arn", "FigmaSyncFn": "figma_sync_fn",
    "AssetToolsFnArn": "asset_tools_fn_arn", "FigmaSecretArn": "figma_secret_arn",
}


def main():
    cfn = boto3.client("cloudformation", region_name="ap-northeast-2")
    outputs = cfn.describe_stacks(StackName="HanaUiuxPlatform")["Stacks"][0]["Outputs"]
    path = ROOT / "config" / "stack.json"
    path.parent.mkdir(exist_ok=True)
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg.update({"region": "ap-northeast-2", "account": "180294183052"})
    cfg.update({KEYMAP[o["OutputKey"]]: o["OutputValue"]
                for o in outputs if o["OutputKey"] in KEYMAP})
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write gateway deploy script**

`hana/uiux-platform/scripts/deploy_gateway.py`:

```python
"""Create (or update) the AgentCore Gateway exposing design-asset MCP tools."""
import json
import pathlib
import sys
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp.tool_schemas import TOOL_SCHEMAS  # noqa: E402

GW_NAME = "hana-design-assets-gw"
TARGET_NAME = "design-asset-tools"


def main():
    cfg_path = ROOT / "config" / "stack.json"
    cfg = json.loads(cfg_path.read_text())
    client = boto3.client("bedrock-agentcore-control", region_name=cfg["region"])

    gws = client.list_gateways()["items"]
    gw = next((g for g in gws if g["name"] == GW_NAME), None)
    if gw is None:
        gw = client.create_gateway(
            name=GW_NAME,
            roleArn=cfg["gateway_role_arn"],
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={"customJWTAuthorizer": {
                "discoveryUrl": cfg["discovery_url"],
                "allowedClients": [cfg["m2m_client_id"]]}},
            description="Org-shared Hana design asset MCP")
    gw_id = gw["gatewayId"]
    while client.get_gateway(gatewayIdentifier=gw_id)["status"] not in ("READY", "FAILED"):
        time.sleep(5)
    detail = client.get_gateway(gatewayIdentifier=gw_id)
    assert detail["status"] == "READY", detail

    targets = client.list_gateway_targets(gatewayIdentifier=gw_id)["items"]
    target_cfg = {"mcp": {"lambda": {
        "lambdaArn": cfg["asset_tools_fn_arn"],
        "toolSchema": {"inlinePayload": TOOL_SCHEMAS}}}}
    creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    existing = next((t for t in targets if t["name"] == TARGET_NAME), None)
    if existing:
        client.update_gateway_target(gatewayIdentifier=gw_id, targetId=existing["targetId"],
                                     name=TARGET_NAME, targetConfiguration=target_cfg,
                                     credentialProviderConfigurations=creds)
    else:
        client.create_gateway_target(gatewayIdentifier=gw_id, name=TARGET_NAME,
                                     targetConfiguration=target_cfg,
                                     credentialProviderConfigurations=creds)

    cfg["gateway_id"] = gw_id
    cfg["gateway_url"] = detail["gatewayUrl"]
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"gateway READY: {detail['gatewayUrl']}")


if __name__ == "__main__":
    main()
```

Note for executor: field names above follow the bedrock-agentcore-control API. If a call fails with `ParamValidationError`, run `aws bedrock-agentcore-control create-gateway help` (or inspect `boto3.client('bedrock-agentcore-control').meta.service_model.operation_model('CreateGateway').input_shape.members`) and adjust to the real shapes — do not guess repeatedly.

- [ ] **Step 3: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): agentcore gateway deploy + stack config extractor"
```

---

### Task 8: Harness agent container (Strands on AgentCore Runtime)

**Files:**
- Create: `hana/uiux-platform/harness/app.py`
- Create: `hana/uiux-platform/harness/publish.py`
- Create: `hana/uiux-platform/harness/Dockerfile`
- Create: `hana/uiux-platform/harness/requirements.txt`
- Test: `hana/uiux-platform/tests/test_publish.py`

**Interfaces:**
- Consumes: Gateway MCP URL + Cognito M2M (env: `GATEWAY_URL`, `USER_POOL_DOMAIN`, `M2M_CLIENT_ID`, `M2M_SECRET_ARN`), drafts bucket (`DRAFTS_BUCKET`, `DISTRIBUTION_DOMAIN`), model `global.anthropic.claude-sonnet-5` (`MODEL_ID` env).
- Produces: AgentCore Runtime entrypoint; payload `{"brief": str}` → `{"drafts": [{"id","title","axis","url"}], "summary": str}`. `publish.py:publish_draft(title, axis, html) -> url` uploads `drafts/<id>.html` + appends to `drafts.json` manifest (status `검토중`).

- [ ] **Step 1: Write the failing publish test**

`hana/uiux-platform/tests/test_publish.py`:

```python
import json

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("DRAFTS_BUCKET", "drafts")
    monkeypatch.setenv("DISTRIBUTION_DOMAIN", "dxyz.cloudfront.net")
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-2")
        s3.create_bucket(Bucket="drafts",
                         CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        s3.put_object(Bucket="drafts", Key="drafts.json", Body=json.dumps({"drafts": []}))
        yield s3


def test_publish_draft_uploads_and_updates_manifest(aws):
    from harness.publish import publish_draft
    url = publish_draft("계좌이체 · compact", "밀도", "<html>x</html>")
    assert url.startswith("https://dxyz.cloudfront.net/drafts/") and url.endswith(".html")
    manifest = json.loads(aws.get_object(Bucket="drafts", Key="drafts.json")["Body"].read())
    entry = manifest["drafts"][0]
    assert entry["title"] == "계좌이체 · compact"
    assert entry["axis"] == "밀도"
    assert entry["status"] == "검토중"
    assert entry["url"] == url
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd hana/uiux-platform && python -m pytest tests/test_publish.py -v`
Expected: FAIL (`harness.publish` missing)

- [ ] **Step 3: Implement publish + agent app**

`hana/uiux-platform/harness/publish.py` (add empty `harness/__init__.py`):

```python
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
```

`hana/uiux-platform/harness/app.py`:

```python
import json
import os
import urllib.parse
import urllib.request

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.tools.mcp import MCPClient

from harness.publish import publish_draft

app = BedrockAgentCoreApp()

SYSTEM = """You are Hana Bank's UI/UX design-draft agent.
Workflow, strictly in order:
1. get_skill('hana-design-system'), get_skill('design-draft-html'),
   get_skill('a11y-finance') — follow them exactly.
2. list_design_tokens and get_brand_guideline — tokens are law.
3. Generate exactly 3 self-contained HTML draft variants for the brief.
   Each variant moves ONE axis (밀도, 강조, 흐름) per the design-draft-html skill.
4. Call publish_draft(title, axis, html) once per variant.
Finish with a short Korean summary of the three variants and their axes."""


def _m2m_token() -> str:
    secret = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["M2M_SECRET_ARN"])["SecretString"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials", "scope": "hana-mcp/invoke",
        "client_id": os.environ["M2M_CLIENT_ID"], "client_secret": secret}).encode()
    req = urllib.request.Request(
        f"https://{os.environ['USER_POOL_DOMAIN']}/oauth2/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


@app.entrypoint
def invoke(payload):
    brief = payload.get("brief", "")
    if not brief:
        return {"error": "payload must include 'brief'"}
    published = []

    @tool
    def publish_draft_tool(title: str, axis: str, html: str) -> str:
        """Publish one HTML draft variant to the gallery. Returns its public URL.

        Args:
            title: short Korean title including the variant flavor
            axis: one of 밀도, 강조, 흐름
            html: complete self-contained HTML document
        """
        url = publish_draft(title, axis, html)
        published.append({"title": title, "axis": axis, "url": url})
        return url

    token = _m2m_token()
    gateway = MCPClient(lambda: streamablehttp_client(
        os.environ["GATEWAY_URL"], headers={"Authorization": f"Bearer {token}"}))
    with gateway:
        agent = Agent(model=os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5"),
                      system_prompt=SYSTEM,
                      tools=gateway.list_tools_sync() + [publish_draft_tool])
        result = agent(brief)
    return {"drafts": published, "summary": str(result)}


if __name__ == "__main__":
    app.run()
```

`hana/uiux-platform/harness/requirements.txt`:

```
strands-agents>=1.0
bedrock-agentcore>=0.1
mcp>=1.9
boto3>=1.39
```

`hana/uiux-platform/harness/Dockerfile` (built from project root so `harness/` is a package):

```dockerfile
FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
COPY harness/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY harness/ harness/
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "harness.app"]
```

- [ ] **Step 4: Run tests**

Run: `cd hana/uiux-platform && python -m pytest tests/ -v`
Expected: all PASS (agent app itself is exercised in Task 10 e2e; only publish is unit-tested)

- [ ] **Step 5: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): strands design-draft harness with axis-driven variants"
```

---

### Task 9: Runtime deploy + invoke + teardown scripts

**Files:**
- Create: `hana/uiux-platform/scripts/deploy_runtime.py`
- Create: `hana/uiux-platform/scripts/invoke.py`
- Create: `hana/uiux-platform/scripts/teardown.py`

**Interfaces:**
- Consumes: `config/stack.json` (gateway_url etc.), harness Dockerfile.
- Produces: ECR repo `hana-design-harness`, AgentCore Runtime `hana_design_harness`; writes `runtime_arn` into config. `invoke.py "<brief>"` prints draft URLs. `teardown.py` deletes runtime, gateway, ECR repo (CDK stack removed separately via `cdk destroy`).

- [ ] **Step 1: Write deploy_runtime.py**

`hana/uiux-platform/scripts/deploy_runtime.py`:

```python
"""Build/push the harness image (linux/arm64) and create/update the AgentCore Runtime."""
import base64
import json
import pathlib
import subprocess
import time

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = "hana-design-harness"
RUNTIME = "hana_design_harness"


def sh(*args):
    print("+", " ".join(args))
    subprocess.run(args, check=True, cwd=ROOT)


def main():
    cfg_path = ROOT / "config" / "stack.json"
    cfg = json.loads(cfg_path.read_text())
    region, account = cfg["region"], cfg["account"]
    ecr = boto3.client("ecr", region_name=region)
    try:
        ecr.create_repository(repositoryName=REPO)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    auth = ecr.get_authorization_token()["authorizationData"][0]
    user_pw = base64.b64decode(auth["authorizationToken"]).decode()
    registry = auth["proxyEndpoint"].removeprefix("https://")
    subprocess.run(["docker", "login", "-u", "AWS", "--password-stdin", registry],
                   input=user_pw.split(":", 1)[1].encode(), check=True)
    uri = f"{account}.dkr.ecr.{region}.amazonaws.com/{REPO}:latest"
    sh("docker", "buildx", "build", "--platform", "linux/arm64",
       "-f", "harness/Dockerfile", "-t", uri, "--push", ".")

    m2m_secret_arn = boto3.client("cognito-idp", region_name=region).describe_user_pool_client(
        UserPoolId=cfg["user_pool_id"], ClientId=cfg["m2m_client_id"])
    # store client secret in Secrets Manager so the runtime never gets it via env
    sm = boto3.client("secretsmanager", region_name=region)
    secret_value = m2m_secret_arn["UserPoolClient"]["ClientSecret"]
    try:
        sec = sm.create_secret(Name="hana/m2m-client-secret", SecretString=secret_value)
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId="hana/m2m-client-secret", SecretString=secret_value)
        sec = sm.describe_secret(SecretId="hana/m2m-client-secret")
    boto3.client("iam").put_role_policy(
        RoleName="hana-agentcore-runtime", PolicyName="read-m2m-secret",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
            "Resource": sec["ARN"]}]}))

    env = {"GATEWAY_URL": cfg["gateway_url"],
           "USER_POOL_DOMAIN": cfg["cognito_domain"],
           "M2M_CLIENT_ID": cfg["m2m_client_id"],
           "M2M_SECRET_ARN": sec["ARN"],
           "DRAFTS_BUCKET": cfg["drafts_bucket"],
           "DISTRIBUTION_DOMAIN": cfg["distribution_domain"],
           "MODEL_ID": "global.anthropic.claude-sonnet-5"}

    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    existing = next((r for r in ac.list_agent_runtimes()["agentRuntimes"]
                     if r["agentRuntimeName"] == RUNTIME), None)
    kwargs = {"agentRuntimeArtifact": {"containerConfiguration": {"containerUri": uri}},
              "networkConfiguration": {"networkMode": "PUBLIC"},
              "roleArn": cfg["runtime_role_arn"],
              "environmentVariables": env}
    if existing:
        ac.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **kwargs)
        arn = existing["agentRuntimeArn"]
        rid = existing["agentRuntimeId"]
    else:
        created = ac.create_agent_runtime(agentRuntimeName=RUNTIME, **kwargs)
        arn, rid = created["agentRuntimeArn"], created["agentRuntimeId"]
    while ac.get_agent_runtime(agentRuntimeId=rid)["status"] in ("CREATING", "UPDATING"):
        time.sleep(10)
    status = ac.get_agent_runtime(agentRuntimeId=rid)["status"]
    assert status == "READY", status
    cfg["runtime_arn"] = arn
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    print(f"runtime READY: {arn}")


if __name__ == "__main__":
    main()
```

Note for executor: as in Task 7, verify parameter names against the live `bedrock-agentcore-control` service model on the first `ParamValidationError` instead of guessing.

- [ ] **Step 2: Write invoke.py**

`hana/uiux-platform/scripts/invoke.py`:

```python
"""Invoke the design-draft harness: python scripts/invoke.py "계좌이체 화면 시안"."""
import json
import pathlib
import sys

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    brief = sys.argv[1] if len(sys.argv) > 1 else "하나은행 모바일 계좌이체 화면 시안"
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    client = boto3.client("bedrock-agentcore", region_name=cfg["region"])
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=cfg["runtime_arn"], qualifier="DEFAULT",
        payload=json.dumps({"brief": brief}, ensure_ascii=False).encode())
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    out = json.loads(body)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\ngallery: https://{cfg['distribution_domain']}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write teardown.py**

`hana/uiux-platform/scripts/teardown.py`:

```python
"""Remove AgentCore + ECR resources (run before `cdk destroy`)."""
import json
import pathlib

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    region = cfg["region"]
    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    for rt in ac.list_agent_runtimes()["agentRuntimes"]:
        if rt["agentRuntimeName"] == "hana_design_harness":
            ac.delete_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
            print("deleted runtime", rt["agentRuntimeId"])
    for gw in ac.list_gateways()["items"]:
        if gw["name"] == "hana-design-assets-gw":
            gid = gw["gatewayId"]
            for t in ac.list_gateway_targets(gatewayIdentifier=gid)["items"]:
                ac.delete_gateway_target(gatewayIdentifier=gid, targetId=t["targetId"])
            ac.delete_gateway(gatewayIdentifier=gid)
            print("deleted gateway", gid)
    ecr = boto3.client("ecr", region_name=region)
    try:
        ecr.delete_repository(repositoryName="hana-design-harness", force=True)
        print("deleted ECR repo")
    except ecr.exceptions.RepositoryNotFoundException:
        pass
    sm = boto3.client("secretsmanager", region_name=region)
    for sid in ("hana/m2m-client-secret",):
        try:
            sm.delete_secret(SecretId=sid, ForceDeleteWithoutRecovery=True)
            print("deleted secret", sid)
        except sm.exceptions.ResourceNotFoundException:
            pass
    print("now run: cd infra && cdk destroy  (and revoke the Figma PAT)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add hana/uiux-platform && git commit -m "feat(hana): runtime deploy, invoke client, teardown scripts"
```

---

### Task 10: Deploy + Figma seed + end-to-end verification

This task runs against real AWS. The ORCHESTRATOR (main session) performs the Figma-dependent steps — the seed Figma file is created via the session's Figma MCP, and the operator PAT is injected by the user's instruction (temporary token, revoked after PoC).

**Files:**
- Create: `hana/uiux-platform/config/figma.json` (`{"file_key": "<seed file key>"}` — written by orchestrator after creating the seed file)
- Create: `hana/uiux-platform/scripts/verify_e2e.py`

- [ ] **Step 1: Write verify_e2e.py**

`hana/uiux-platform/scripts/verify_e2e.py`:

```python
"""Post-deploy verification: figma-sync → MCP tools via gateway → harness → gallery."""
import base64
import json
import pathlib
import urllib.parse
import urllib.request

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]


def m2m_token(cfg):
    client = boto3.client("cognito-idp", region_name=cfg["region"])
    secret = client.describe_user_pool_client(
        UserPoolId=cfg["user_pool_id"],
        ClientId=cfg["m2m_client_id"])["UserPoolClient"]["ClientSecret"]
    basic = base64.b64encode(f"{cfg['m2m_client_id']}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "scope": "hana-mcp/invoke"}).encode()
    req = urllib.request.Request(f"https://{cfg['cognito_domain']}/oauth2/token", data=data,
                                 headers={"Authorization": f"Basic {basic}",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def mcp_call(cfg, token, method, params, rpc_id):
    req = urllib.request.Request(cfg["gateway_url"], method="POST", data=json.dumps(
        {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
    # streamable HTTP may answer as SSE; extract the data line
    if body.startswith("event:") or "\ndata:" in body or body.startswith("data:"):
        body = next(l[5:] for l in body.splitlines() if l.startswith("data:"))
    return json.loads(body)


def main():
    cfg = json.loads((ROOT / "config" / "stack.json").read_text())
    file_key = json.loads((ROOT / "config" / "figma.json").read_text())["file_key"]

    print("1. figma-sync ...")
    lam = boto3.client("lambda", region_name=cfg["region"])
    r = lam.invoke(FunctionName=cfg["figma_sync_fn"],
                   Payload=json.dumps({"file_key": file_key}).encode())
    sync = json.loads(r["Payload"].read())
    assert "synced" in sync, sync
    print("   ", sync)

    print("2. gateway MCP tools ...")
    token = m2m_token(cfg)
    tools = mcp_call(cfg, token, "tools/list", {}, 1)["result"]["tools"]
    names = {t["name"].split("___")[-1] for t in tools}
    assert "list_design_tokens" in names and "get_skill" in names, names
    tok = mcp_call(cfg, token, "tools/call", {
        "name": next(t["name"] for t in tools if t["name"].endswith("list_design_tokens")),
        "arguments": {}}, 2)["result"]
    assert "008485" in json.dumps(tok), tok
    print("    tokens OK,", len(tools), "tools")

    print("3. harness invoke (takes a few minutes) ...")
    ac = boto3.client("bedrock-agentcore", region_name=cfg["region"])
    resp = ac.invoke_agent_runtime(agentRuntimeArn=cfg["runtime_arn"], qualifier="DEFAULT",
                                   payload=json.dumps({"brief": "모바일 계좌이체 화면 시안"},
                                                      ensure_ascii=False).encode())
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    out = json.loads(body)
    assert len(out.get("drafts", [])) == 3, out
    print("    drafts:", [d["url"] for d in out["drafts"]])

    print("4. gallery + draft render ...")
    with urllib.request.urlopen(f"https://{cfg['distribution_domain']}/drafts.json") as r:
        manifest = json.loads(r.read())
    assert len(manifest["drafts"]) >= 3
    with urllib.request.urlopen(out["drafts"][0]["url"]) as r:
        assert b"<html" in r.read()[:2000].lower()
    print("\nALL GREEN — gallery:", f"https://{cfg['distribution_domain']}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2 (ORCHESTRATOR): Create the Figma seed file via Figma MCP**

Pages/nodes per the Task 1 conventions: page `Design Tokens` with `color/primary #008485`, `color/primaryDark #00615f`, `color/ink #17332f`, `color/bg #fbfcfb`, `color/mist #e6f3f2`, `color/warn #d9a441`; `type/display 34/900`, `type/heading 22/700`, `type/body 14/400`, `type/caption 12/500` (Noto Sans KR); `space/xs 8`, `space/sm 12`, `space/md 16`, `space/lg 24`, `space/xl 32`. Page `Components` with components `Button/Primary`, `Card/Section`, `ListItem/Account`, `Input/Amount`, `Chip/Quick`, `Badge/Status`, each with a one-line usage description. Write the file key to `config/figma.json`.

- [ ] **Step 3: Deploy CDK + inject PAT**

```bash
cd hana/uiux-platform/infra && pip install -r requirements.txt -q && npx cdk deploy --require-approval never
cd .. && python scripts/write_config.py
# operator step (orchestrator runs with the user-provided temporary PAT):
aws secretsmanager put-secret-value --secret-id hana/figma-token --secret-string "$FIGMA_PAT" --region ap-northeast-2
```

- [ ] **Step 4: Deploy gateway, skills, gallery, runtime**

```bash
cd hana/uiux-platform
python scripts/deploy_gateway.py
python scripts/sync_skills.py
python scripts/deploy_gallery.py
python scripts/deploy_runtime.py
```

- [ ] **Step 5: Run e2e verification**

```bash
python scripts/verify_e2e.py
```
Expected: `ALL GREEN` with a gallery URL and 3 draft URLs. Open the gallery URL and visually confirm the showcase page lists 3 variants (밀도/강조/흐름).

- [ ] **Step 6: Update README with live URLs + commit**

Append actual gallery URL, gateway URL, runtime ARN to README "Deployed endpoints" section (values from `config/stack.json`; the config file itself contains no secrets and is committed).

```bash
git add hana/uiux-platform && git commit -m "feat(hana): deploy PoC to ap-northeast-2 + e2e verification"
```

---

### Task 11: Organizational learning loop (feedback → approved patterns → few-shot)

Added 2026-08-31 after user approval. Runs after Task 9, before Task 10 deploy.

**Files:**
- Create: `hana/uiux-platform/feedback/handler.py` (+ empty `feedback/__init__.py`)
- Modify: `hana/uiux-platform/infra/stack.py` (feedback Lambda + Function URL + CloudFront `/api/*` behavior)
- Modify: `hana/uiux-platform/gallery/index.html` (승인/반려 buttons)
- Modify: `hana/uiux-platform/harness/app.py` and `hana/uiux-platform/harness/publish.py` (few-shot loading)
- Test: `hana/uiux-platform/tests/test_feedback.py`, extend `tests/test_publish.py`

**Interfaces:**
- Produces: Lambda Function URL handler for `POST /api/feedback` with JSON body `{"draft_id": str, "action": "approve"|"reject", "comment": str?}` → `{"ok": true, "status": "승인됨"|"반려"}`; 400 `{"error": ...}` on bad input. Approve copies `drafts/<id>.html` → `approved-patterns/<id>.html` and upserts `approved-patterns/index.json` `{"patterns": [{"id","title","axis","approved_at"}]}`.
- Produces: `load_approved_patterns(limit=2) -> list[dict]` in `harness/publish.py` returning `[{"title", "axis", "html"}]` newest-first from the drafts bucket; harness injects them into the system prompt as reference examples.
- Env for feedback Lambda: `DRAFTS_BUCKET`.

- [ ] **Step 1: Write failing tests**

`hana/uiux-platform/tests/test_feedback.py`:

```python
import json

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("DRAFTS_BUCKET", "drafts")
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-northeast-2")
        s3.create_bucket(Bucket="drafts",
                         CreateBucketConfiguration={"LocationConstraint": "ap-northeast-2"})
        s3.put_object(Bucket="drafts", Key="drafts.json", Body=json.dumps({"drafts": [
            {"id": "abc123", "title": "계좌이체 · compact", "axis": "밀도",
             "status": "검토중", "url": "https://x/drafts/abc123.html",
             "created_at": "2026-08-31T00:00:00+00:00"}]}, ensure_ascii=False))
        s3.put_object(Bucket="drafts", Key="drafts/abc123.html", Body=b"<html>draft</html>")
        yield s3


def _event(body):
    return {"requestContext": {"http": {"method": "POST"}},
            "body": json.dumps(body, ensure_ascii=False)}


def test_approve_promotes_pattern(aws):
    from feedback.handler import handler
    resp = handler(_event({"draft_id": "abc123", "action": "approve"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "승인됨"
    manifest = json.loads(aws.get_object(Bucket="drafts", Key="drafts.json")["Body"].read())
    assert manifest["drafts"][0]["status"] == "승인됨"
    idx = json.loads(aws.get_object(Bucket="drafts",
                                    Key="approved-patterns/index.json")["Body"].read())
    assert idx["patterns"][0]["id"] == "abc123"
    aws.get_object(Bucket="drafts", Key="approved-patterns/abc123.html")


def test_reject_updates_status_only(aws):
    from feedback.handler import handler
    resp = handler(_event({"draft_id": "abc123", "action": "reject", "comment": "너무 밀집"}), None)
    assert json.loads(resp["body"])["status"] == "반려"
    manifest = json.loads(aws.get_object(Bucket="drafts", Key="drafts.json")["Body"].read())
    assert manifest["drafts"][0]["status"] == "반려"
    assert manifest["drafts"][0]["comment"] == "너무 밀집"


def test_bad_input_400(aws):
    from feedback.handler import handler
    assert handler(_event({"draft_id": "abc123", "action": "nope"}), None)["statusCode"] == 400
    assert handler(_event({"draft_id": "zzz", "action": "approve"}), None)["statusCode"] == 404
```

Extend `hana/uiux-platform/tests/test_publish.py` with:

```python
def test_load_approved_patterns(aws):
    import json as _json
    from harness.publish import load_approved_patterns, publish_draft
    url = publish_draft("이체 · airy", "밀도", "<html>approved one</html>")
    draft_id = url.rsplit("/", 1)[1].removesuffix(".html")
    aws.put_object(Bucket="drafts", Key=f"approved-patterns/{draft_id}.html",
                   Body=b"<html>approved one</html>")
    aws.put_object(Bucket="drafts", Key="approved-patterns/index.json", Body=_json.dumps(
        {"patterns": [{"id": draft_id, "title": "이체 · airy", "axis": "밀도",
                       "approved_at": "2026-08-31T01:00:00+00:00"}]}, ensure_ascii=False))
    pats = load_approved_patterns(limit=2)
    assert pats == [{"title": "이체 · airy", "axis": "밀도", "html": "<html>approved one</html>"}]


def test_load_approved_patterns_empty(aws):
    from harness.publish import load_approved_patterns
    assert load_approved_patterns() == []
```

- [ ] **Step 2: Run to verify failures**

Run: `cd hana/uiux-platform && python -m pytest tests/test_feedback.py tests/test_publish.py -v`
Expected: new tests FAIL (missing module/function)

- [ ] **Step 3: Implement**

`hana/uiux-platform/feedback/handler.py`:

```python
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
```

`hana/uiux-platform/harness/publish.py` — append:

```python
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
```

`hana/uiux-platform/harness/app.py` — in `invoke`, right before `token = _m2m_token()`, add:

```python
    patterns = load_approved_patterns(limit=2)
    system = SYSTEM
    if patterns:
        refs = "\n\n".join(
            f"### 승인 패턴: {p['title']} (axis: {p['axis']})\n```html\n{p['html']}\n```"
            for p in patterns)
        system = SYSTEM + ("\n\nBelow are org-approved reference drafts. Follow their "
                           "structure and quality bar; do not copy their brief-specific "
                           "content.\n\n" + refs)
```

and change `Agent(... system_prompt=SYSTEM ...)` to `system_prompt=system`, and the import to `from harness.publish import publish_draft, load_approved_patterns`.

`hana/uiux-platform/infra/stack.py` — additions:

```python
        feedback = lambda_.Function(
            self, "Feedback", function_name="hana-draft-feedback",
            runtime=lambda_.Runtime.PYTHON_3_13, architecture=lambda_.Architecture.ARM_64,
            handler="feedback.handler.handler", code=code,
            timeout=cdk.Duration.seconds(15),
            environment={"DRAFTS_BUCKET": drafts.bucket_name})
        drafts.grant_read_write(feedback)
        feedback_url = feedback.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM)
        dist.add_behavior(
            "/api/*",
            origins.FunctionUrlOrigin.with_origin_access_control(feedback_url),
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY)
```

(also exclude `"feedback"` must NOT be added to the Lambda asset exclude list — the shared `code` asset must include `feedback/`; verify the exclude list in `stack.py` doesn't exclude it.)

Add to `tests/test_stack.py`:

```python
def test_feedback_lambda_and_api_behavior():
    t = synth()
    t.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})
    dist = list(t.find_resources("AWS::CloudFront::Distribution").values())[0]
    behaviors = dist["Properties"]["DistributionConfig"]["CacheBehaviors"]
    assert any(b["PathPattern"] == "/api/*" for b in behaviors)
```

`hana/uiux-platform/gallery/index.html` — in the card template add under `.meta`:

```html
        <div class="actions">
          <button onclick="feedback(event,'${x.id}','approve')">승인</button>
          <button class="reject" onclick="feedback(event,'${x.id}','reject')">반려</button>
        </div>
```

with CSS `.actions{display:flex;gap:8px} .actions button{min-height:44px;padding:0 16px;border-radius:22px;border:1px solid #d5e0dd;background:#e6f3f2;color:#00615f;font-weight:700;font-family:inherit;font-size:13px;cursor:pointer} .actions button.reject{background:#fff;color:#5c6f6b}` and script:

```js
  async function feedback(ev, id, action) {
    ev.preventDefault(); ev.stopPropagation();
    const comment = action === 'reject' ? (prompt('반려 사유 (선택)') || '') : '';
    const r = await fetch('/api/feedback', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({draft_id: id, action, comment})});
    const out = await r.json();
    if (out.ok) location.reload(); else alert(out.error || '실패');
  }
```

(buttons live inside the `<a class="card">` — move the `.meta`+`.actions` block outside the anchor or call `preventDefault` as above; keep cards as `<div class="card">` with an inner anchor on the thumb if simpler.)

- [ ] **Step 4: Run tests** — `python -m pytest tests/ -v`, all green (including new stack assertion).

- [ ] **Step 5: Commit** — `git add hana/uiux-platform && git commit -m "feat(hana): org learning loop — feedback endpoint, approved patterns, few-shot"`

- [ ] **Step 6 (deploy phase, Task 10):** `deploy_gallery.py` re-upload + `cdk deploy` + `deploy_runtime.py` rebuild, then e2e: approve one draft via `curl -X POST https://<dist>/api/feedback` and re-invoke the harness confirming the summary mentions reference patterns.

---

## Self-review notes

- Spec coverage: Figma ingestion (T1-2, T10), shared MCP + Cognito no-self-signup (T3, T5, T7), skill registry (T4), harness + variation policy (T8), gallery Option B (T6), IaC + deploy scripts (T5, T7, T9), tests + e2e (throughout, T10), teardown (T9).
- AgentCore control-plane API shapes (Tasks 7/9) are the highest-risk area; both tasks carry an explicit "verify against the live service model" instruction instead of blind retries.
- `docker buildx --platform linux/arm64`: this host is aarch64, so a native build works even without buildx emulation; if `buildx` is unavailable, plain `docker build --push` is equivalent here.
