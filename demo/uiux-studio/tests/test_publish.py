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
