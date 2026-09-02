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
