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
    # all POST routes require a signed-in designer; tests stub the verifier
    import feedback.handler as _h
    monkeypatch.setattr(_h, "actor_from_event", lambda e: "테스트")
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
    assert "skills/user-senior-mode/1.0.0/SKILL.md" in keys


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


def test_post_requires_auth(aws, monkeypatch):
    import feedback.handler as _h
    monkeypatch.setattr(_h, "actor_from_event", lambda e: None)
    r = _h.handler(_event("POST", "/api/assets", {"name": "x", "type": "palette",
                                                  "content": "{}"}), None)
    assert r["statusCode"] == 401


def test_register_agent_asset(aws):
    from feedback.handler import handler
    r = handler(_event("POST", "/api/assets", {
        "name": "카드 전문 에이전트", "type": "agent", "scope": "shared",
        "content": json.dumps({"system": "카드 상품 전문", "model_id": "apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
                               "asset_ids": []}, ensure_ascii=False)}), None)
    assert r["statusCode"] == 200
    bad = handler(_event("POST", "/api/assets", {"name": "x", "type": "agent",
                                                 "content": "[1,2]"}), None)
    assert bad["statusCode"] == 400


def test_generate_with_agent_preset(aws, monkeypatch):
    from feedback import handler as mod
    calls = {}
    class FakeLambda:
        def invoke(self, **kw): calls.update(kw); return {"StatusCode": 202}
    monkeypatch.setattr(mod, "_lambda_client", lambda: FakeLambda())
    mod.handler(_event("POST", "/api/assets", {
        "name": "에이전트A", "type": "agent", "scope": "shared",
        "content": json.dumps({"system": "S", "model_id": "m1", "asset_ids": ["palette:x"]})}),
        None)
    r = mod.handler(_event("POST", "/api/generate",
                           {"brief": "b", "asset_ids": [], "agent_id": "agent:에이전트a"}), None)
    assert r["statusCode"] == 200
    sent = json.loads(calls["Payload"])
    assert sent["agent_cfg"]["model_id"] == "m1"
    assert sent["actor"] == "테스트"


def test_list_jobs(aws, monkeypatch):
    from feedback import handler as mod
    class FakeLambda:
        def invoke(self, **kw): return {"StatusCode": 202}
    monkeypatch.setattr(mod, "_lambda_client", lambda: FakeLambda())
    mod.handler(_event("POST", "/api/generate", {"brief": "b1"}), None)
    mod.handler(_event("POST", "/api/generate", {"brief": "b2"}), None)
    out = json.loads(mod.handler(_event("GET", "/api/jobs"), None)["body"])
    assert len(out["jobs"]) == 2
    assert out["jobs"][0]["actor"] == "테스트"


def test_list_models(aws, monkeypatch):
    from feedback import assets_api as api
    class FakeBedrock:
        def list_inference_profiles(self, **kw):
            return {"inferenceProfileSummaries": [
                {"inferenceProfileId": "global.anthropic.claude-sonnet-5",
                 "inferenceProfileName": "Claude Sonnet 5", "status": "ACTIVE"},
                {"inferenceProfileId": "apac.meta.llama-x", "inferenceProfileName": "Llama",
                 "status": "INACTIVE"}]}
    real = api.boto3.client
    monkeypatch.setattr(api.boto3, "client",
                        lambda name, **kw: FakeBedrock() if name == "bedrock" else real(name, **kw))
    code, out = api.list_models()
    assert code == 200 and out["models"] == [
        {"id": "global.anthropic.claude-sonnet-5", "name": "Claude Sonnet 5"}]


def test_config_route(aws, monkeypatch):
    from feedback.handler import handler
    monkeypatch.setenv("SPA_CLIENT_ID", "abc123")
    out = json.loads(handler(_event("GET", "/api/config"), None)["body"])
    assert out["spa_client_id"] == "abc123" and out["region"]
