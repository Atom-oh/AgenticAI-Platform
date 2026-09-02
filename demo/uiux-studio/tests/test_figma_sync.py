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
