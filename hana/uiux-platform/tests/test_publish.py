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
