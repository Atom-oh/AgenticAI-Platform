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


def test_get_skill_resolves_user_prefixed_skill(aws):
    from mcp.asset_tools import handler
    boto3.client("s3", region_name="ap-northeast-2").put_object(
        Bucket="skills", Key="skills/user-senior-mode/1.0.0/SKILL.md",
        Body=b"# senior-mode\nbig text")
    out = handler({"name": "senior-mode"}, ctx("get_skill"))
    assert "big text" in out["content"] and out["name"] == "senior-mode"
    still = handler({"name": "design-draft-html"}, ctx("get_skill"))
    assert "design-draft-html" in still["content"] and still["name"] == "design-draft-html"


def test_unknown_tool(aws):
    from mcp.asset_tools import handler
    assert "error" in handler({}, ctx("nope"))


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


def test_schemas_cover_all_tools():
    from mcp.tool_schemas import TOOL_SCHEMAS
    assert {s["name"] for s in TOOL_SCHEMAS} == {
        "list_design_tokens", "search_assets", "get_component",
        "get_brand_guideline", "list_skills", "get_skill",
        "list_assets", "get_asset"}
