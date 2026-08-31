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
