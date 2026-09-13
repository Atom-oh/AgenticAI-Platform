"""Offline behavioral tests. All temporary writes stay in the task directory."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).parents[1] / "privacy/deploy/shared-cluster/qwen"
spec = importlib.util.spec_from_file_location("privacy_qwen_publisher", ROOT / "stage_artifacts.py")
uploader = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = uploader
spec.loader.exec_module(uploader)
REAL_CREATE_CLIENTS = uploader.create_clients


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__("untrusted AWS error body; do not display")
        self.response = {"Error": {"Code": code}}


class FakeSTS:
    def __init__(self, account=uploader.ACCOUNT):
        self.account = account
        self.calls = 0

    def get_caller_identity(self):
        self.calls += 1
        return {"Account": self.account, "Arn": "identity-not-for-output"}


class FakeS3:
    exceptions = SimpleNamespace(ClientError=FakeClientError)

    def __init__(self):
        self.objects = {}
        self.calls = []
        self.wrong_owner = False
        self.post_checksum_bad = False
        self.race = None
        self.after_put_head = None
        self.put_count = 0

    def head_object(self, **request):
        assert request["Bucket"] == uploader.BUCKET
        assert request["ExpectedBucketOwner"] == uploader.ACCOUNT
        assert request["ChecksumMode"] == "ENABLED"
        self.calls.append(("head", dict(request)))
        if self.wrong_owner:
            raise FakeClientError("403")
        if request["Key"] not in self.objects:
            raise FakeClientError("404")
        if self.put_count and self.after_put_head:
            hook, self.after_put_head = self.after_put_head, None
            hook()
        response = dict(self.objects[request["Key"]])
        if self.post_checksum_bad and self.put_count:
            response["ChecksumSHA256"] = base64.b64encode(bytes(32)).decode()
        return response

    def put_object(self, **request):
        self.put_count += 1
        assert request["Bucket"] == uploader.BUCKET
        assert request["ExpectedBucketOwner"] == uploader.ACCOUNT
        assert request["IfNoneMatch"] == "*"
        assert request["ChecksumAlgorithm"] == "SHA256"
        assert request["ServerSideEncryption"] == "AES256"
        assert request["ContentLength"] < 5_000_000_000
        body = request["Body"]
        assert body.tell() == 0
        payload = body.read()
        actual_checksum = base64.b64encode(hashlib.sha256(payload).digest()).decode()
        assert len(payload) == request["ContentLength"]
        assert actual_checksum == request["ChecksumSHA256"]
        self.calls.append(("put", {
            key: value for key, value in request.items() if key != "Body"
        }))
        if request["Key"] in self.objects:
            raise FakeClientError("PreconditionFailed")
        if self.race == "missing409":
            raise FakeClientError("ConditionalRequestConflict")
        self.objects[request["Key"]] = {
            "ContentLength": len(payload), "ChecksumSHA256": actual_checksum,
            "ChecksumType": "FULL_OBJECT", "ServerSideEncryption": "AES256",
            "VersionId": "fake-version",
        }
        if self.race == "different412":
            self.objects[request["Key"]]["ContentLength"] += 1
            raise FakeClientError("PreconditionFailed")
        if self.race == "matching412":
            raise FakeClientError("PreconditionFailed")
        return {"ChecksumSHA256": actual_checksum, "VersionId": "fake-version"}


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(payload)
        self.headers = headers or {}
        self.read_sizes = []

    def getcode(self):
        return 200

    def read(self, size=-1):
        assert size > 0
        self.read_sizes.append(size)
        return super().read(size)


class OfflineUploaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".uploader-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / "stage"
        self.addCleanup(patch.stopall)
        patch.object(uploader, "STAGE", self.stage).start()
        # Accidental real networking or SDK construction is a hard test failure.
        patch.object(socket, "getaddrinfo", side_effect=AssertionError("offline test DNS")).start()
        patch.object(socket.socket, "connect", side_effect=AssertionError("offline test TCP")).start()
        patch.object(uploader, "create_clients", side_effect=AssertionError("no real AWS clients")).start()
        self.s3, self.sts = FakeS3(), FakeSTS()
        self.payload = b"synthetic artifact bytes"
        self.artifact = uploader.Artifact(
            "Qwen/Qwen3-8B", uploader.REVISIONS["Qwen/Qwen3-8B"],
            "config.json", len(self.payload), hashlib.sha256(self.payload).hexdigest(),
        )
        self.download_calls = []
        self.results = []

    def open_payload(self, payload=None, headers=None):
        def opened(url):
            self.download_calls.append(url)
            self.assertEqual(len(list(self.stage.glob("qwen-upload-*.part"))), 1)
            return FakeResponse(self.payload if payload is None else payload, headers)
        return opened

    def run_publish(self, **kwargs):
        uploader.publish(
            kwargs.pop("artifacts", [self.artifact]), self.s3, self.sts,
            open_url=kwargs.pop("open_url", self.open_payload()),
            emit=self.results.append, **kwargs,
        )

    def staged(self):
        return list(self.stage.glob("qwen-upload-*.part"))

    def matching_remote(self):
        return {
            "ContentLength": self.artifact.size,
            "ChecksumSHA256": self.artifact.checksum,
            "ChecksumType": "FULL_OBJECT",
            "ServerSideEncryption": "AES256",
        }

    def assert_failed_download(self, payload, code):
        with self.assertRaisesRegex(uploader.Failure, code):
            self.run_publish(open_url=self.open_payload(payload))
        self.assertEqual(self.s3.put_count, 0)
        self.assertEqual(len(self.staged()), 1)
        self.assertLessEqual(self.staged()[0].stat().st_size, self.artifact.size)

    def test_success_verifies_and_deletes_only_owned_file(self):
        self.stage.mkdir(mode=0o700)
        unrelated = self.stage / "parent-owned.txt"
        unrelated.write_text("keep")
        self.run_publish()
        self.assertEqual(self.sts.calls, 1)
        self.assertEqual([op for op, _ in self.s3.calls], ["head", "put", "head"])
        self.assertEqual(self.s3.calls[-1][1]["VersionId"], "fake-version")
        self.assertEqual(self.staged(), [])
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertTrue(self.results[0]["own_staged_file_deleted"])

    def test_two_artifacts_never_overlap_on_disk(self):
        second = uploader.Artifact(
            self.artifact.model, self.artifact.revision, "generation_config.json",
            self.artifact.size, self.artifact.sha256,
        )
        self.run_publish(artifacts=[self.artifact, second])
        self.assertEqual(self.s3.put_count, 2)
        self.assertEqual(len(self.download_calls), 2)
        self.assertEqual(self.staged(), [])

    def test_corruption_never_uploads(self):
        self.assert_failed_download(b"x" * self.artifact.size, "download_sha256_mismatch")

    def test_partial_download_never_uploads(self):
        self.assert_failed_download(self.payload[:-1], "download_partial")

    def test_oversize_never_exceeds_staging_bound_or_uploads(self):
        self.assert_failed_download(self.payload + b"extra", "download_oversize")

    def test_declared_content_length_mismatch_fails_before_body(self):
        with self.assertRaisesRegex(uploader.Failure, "download_content_length"):
            self.run_publish(open_url=self.open_payload(headers={"Content-Length": "99999"}))
        self.assertEqual(self.s3.put_count, 0)
        self.assertEqual(self.staged()[0].stat().st_size, 0)

    def test_wrong_caller_account_prevents_all_s3_and_staging(self):
        self.sts = FakeSTS("000000000000")
        with self.assertRaisesRegex(uploader.Failure, "caller_account_mismatch"):
            self.run_publish()
        self.assertEqual(self.s3.calls, [])
        self.assertEqual(self.download_calls, [])
        self.assertFalse(self.stage.exists())

    def test_wrong_bucket_owner_is_not_treated_as_missing(self):
        self.s3.wrong_owner = True
        with self.assertRaises(FakeClientError):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 0)
        self.assertEqual(self.download_calls, [])
        self.assertEqual(self.staged(), [])

    def test_matching_existing_checksum_skips_without_download(self):
        self.s3.objects[self.artifact.key] = self.matching_remote()
        self.run_publish()
        self.assertEqual(self.s3.put_count, 0)
        self.assertEqual(self.download_calls, [])
        self.assertTrue(self.results[0]["already_verified_in_s3"])

    def test_preexisting_conflict_is_never_overwritten(self):
        obj = self.matching_remote()
        obj["ChecksumSHA256"] = base64.b64encode(bytes(32)).decode()
        self.s3.objects[self.artifact.key] = obj
        with self.assertRaisesRegex(uploader.Failure, "s3_existing_or_uploaded_object_mismatch"):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 0)
        self.assertEqual(self.download_calls, [])
        self.assertEqual(self.s3.objects[self.artifact.key], obj)

    def test_metadata_hash_does_not_substitute_for_s3_checksum(self):
        obj = self.matching_remote()
        obj.pop("ChecksumSHA256")
        obj["Metadata"] = {"sha256": self.artifact.sha256}
        self.s3.objects[self.artifact.key] = obj
        with self.assertRaises(uploader.Failure):
            self.run_publish()
        self.assertEqual(self.download_calls, [])
        self.assertEqual(self.s3.put_count, 0)

    def test_composite_checksum_is_not_accepted(self):
        obj = self.matching_remote()
        obj["ChecksumType"] = "COMPOSITE"
        self.s3.objects[self.artifact.key] = obj
        with self.assertRaises(uploader.Failure):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 0)

    def test_post_upload_bad_checksum_retains_file_and_blocks_retry(self):
        self.s3.post_checksum_bad = True
        with self.assertRaises(uploader.Failure):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 1)
        self.assertEqual(len(self.staged()), 1)
        with self.assertRaisesRegex(uploader.Failure, "retained_artifact_needs_parent_review"):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 1)
        self.assertEqual(len(self.staged()), 1)

    def test_conditional_race_same_content_checks_head_then_cleans(self):
        self.s3.race = "matching412"
        self.run_publish()
        self.assertEqual(self.s3.put_count, 1)
        self.assertEqual(self.staged(), [])
        self.assertTrue(self.results[0]["s3_sha256_verified"])

    def test_conditional_race_different_content_retains_without_retry(self):
        self.s3.race = "different412"
        with self.assertRaises(uploader.Failure):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 1)
        self.assertEqual(len(self.staged()), 1)

    def test_409_missing_object_retains_without_unconditional_retry(self):
        self.s3.race = "missing409"
        with self.assertRaisesRegex(uploader.Failure, "s3_object_missing_after_put"):
            self.run_publish()
        self.assertEqual(self.s3.put_count, 1)
        self.assertEqual(len(self.staged()), 1)

    def test_foreign_replacement_file_is_never_deleted(self):
        def replace():
            foreign = self.stage / "parent-replacement.txt"
            foreign.write_text("foreign data")
            os.replace(foreign, self.staged()[0])
        self.s3.after_put_head = replace
        with self.assertRaisesRegex(uploader.Failure, "staged_file_identity_changed"):
            self.run_publish()
        self.assertEqual(self.staged()[0].read_text(), "foreign data")

    def test_low_space_prevents_download_and_staging_file(self):
        with patch.object(uploader.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(uploader.Failure, "insufficient_staging_space"):
                self.run_publish()
        self.assertEqual(self.download_calls, [])
        self.assertEqual(self.staged(), [])

    def test_default_plan_constructs_no_clients_and_writes_nothing(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(uploader.main([]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["aws_calls"], 0)
        self.assertEqual(plan["downloads"], 0)
        self.assertEqual(plan["files"], 21)
        self.assertEqual(plan["peak_staged_bytes"], 3996250744)
        self.assertFalse(self.stage.exists())
        uploader.create_clients.assert_not_called()

    def test_boto3_client_configuration_without_sdk_or_network(self):
        class FakeConfig:
            def __init__(self, **values):
                self.values = values

            def merge(self, other):
                return FakeConfig(**{**self.values, **other.values})
        required = {"IfNoneMatch", "ExpectedBucketOwner", "ChecksumSHA256", "ContentLength"}
        fake_s3 = SimpleNamespace(meta=SimpleNamespace(service_model=SimpleNamespace(
            operation_model=lambda name: SimpleNamespace(input_shape=SimpleNamespace(members=required))
        )))
        created = []

        class FakeSession:
            def __init__(self, **kwargs):
                assert kwargs == {"region_name": uploader.REGION}

            def client(self, service, **kwargs):
                created.append((service, kwargs))
                return fake_s3 if service == "s3" else object()
        boto3_module, botocore_module = ModuleType("boto3"), ModuleType("botocore")
        config_module = ModuleType("botocore.config")
        boto3_module.Session = FakeSession
        config_module.Config = FakeConfig
        with patch.dict(sys.modules, {
            "boto3": boto3_module, "botocore": botocore_module,
            "botocore.config": config_module,
        }), patch.dict(os.environ, {
            "AWS_EC2_METADATA_DISABLED": "false",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1:65535/fake-credentials",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/fake-role",
        }):
            REAL_CREATE_CLIENTS()
            self.assertEqual(os.environ["AWS_EC2_METADATA_DISABLED"], "false")
            self.assertEqual(os.environ["AWS_CONTAINER_CREDENTIALS_FULL_URI"],
                             "http://127.0.0.1:65535/fake-credentials")
            self.assertEqual(os.environ["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"], "/fake-role")
        self.assertEqual([name for name, _ in created], ["s3", "sts"])
        self.assertEqual(created[0][1]["config"].values["signature_version"], "s3v4")
        self.assertEqual(created[1][1]["config"].values["signature_version"], "v4")
        self.assertEqual(created[0][1]["endpoint_url"], "https://s3.ap-northeast-2.amazonaws.com")
        self.assertEqual(created[1][1]["endpoint_url"], "https://sts.ap-northeast-2.amazonaws.com")
        self.assertEqual(created[0][1]["config"].values["retries"]["total_max_attempts"], 2)

    def test_signed_url_errors_are_sanitized(self):
        sentinel = "https://cas-bridge.xethub.hf.co/object?Signature=SIGNED-URL-SENTINEL"
        def bad_open(url):
            raise urllib.error.URLError(sentinel)
        stderr = io.StringIO()
        with patch.object(uploader, "load_manifest", return_value=[self.artifact]), \
             patch.object(uploader, "create_clients", return_value=(self.s3, self.sts)), \
             patch.object(uploader, "public_open", side_effect=bad_open), \
             redirect_stderr(stderr):
            self.assertEqual(uploader.main(["--publish"]), 1)
        self.assertNotIn("SIGNED-URL-SENTINEL", stderr.getvalue())
        self.assertNotIn("https://", stderr.getvalue())
        self.assertEqual(json.loads(stderr.getvalue())["code"], "URLError")
        self.assertEqual(self.s3.put_count, 0)

    def test_manifest_rejects_path_id_revision_size_hash_and_duplicates(self):
        original = json.loads((ROOT / "artifact-manifest.json").read_text())
        mutations = [
            ("path", "../escape", "manifest_path"),
            ("path", "/home/escape", "manifest_path"),
            ("path", "config.json?token=x", "manifest_path"),
            ("size", 4_000_000_001, "manifest_size"),
            ("size", 5_000_000_000, "manifest_size"),
            ("size", 0, "manifest_size"),
            ("size", True, "manifest_size"),
            ("size", "100", "manifest_size"),
            ("sha256", "not-a-hash", "manifest_sha256"),
        ]
        for field, value, code in mutations:
            with self.subTest(field=field, value=value):
                changed = deepcopy(original)
                changed["models"][0]["files"][0][field] = value
                with self.assertRaisesRegex(uploader.Failure, code):
                    uploader.validate_manifest(changed)
        for field, value, code in [
            ("id", "evil/model", "manifest_model_id"),
            ("revision", "main", "manifest_revision"),
            ("revision", "a" * 40, "manifest_revision"),
        ]:
            changed = deepcopy(original)
            changed["models"][0][field] = value
            with self.assertRaisesRegex(uploader.Failure, code):
                uploader.validate_manifest(changed)
        changed = deepcopy(original)
        changed["models"][0]["files"].append(changed["models"][0]["files"][0])
        with self.assertRaisesRegex(uploader.Failure, "manifest_path"):
            uploader.validate_manifest(changed)
        changed = deepcopy(original)
        changed["bucket"] = "different-owner-bucket"
        with self.assertRaises(uploader.Failure):
            uploader.validate_manifest(changed)
        with self.assertRaisesRegex(uploader.Failure, "duplicate_json_key"):
            json.loads('{"schema":1,"schema":2}', object_pairs_hook=uploader.unique_object)

    def test_public_https_and_redirect_destination_validation(self):
        public = lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        private = lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]
        uploader.validate_public_url("https://huggingface.co/Qwen/model", resolver=public)
        uploader.validate_public_url("https://cas-bridge.xethub.hf.co/object?Signature=dummy", resolver=public)
        for url in (
            "http://huggingface.co/file", "file:///tmp/file",
            "https://huggingface.co.evil.test/file", "https://127.0.0.1/file",
            "https://user:pass@huggingface.co/file", "https://huggingface.co:8443/file",
        ):
            with self.subTest(url=url), self.assertRaises(uploader.Failure):
                uploader.validate_public_url(url, resolver=public)
        with self.assertRaisesRegex(uploader.Failure, "download_destination_not_public"):
            uploader.validate_public_url("https://huggingface.co/file", resolver=private)


if __name__ == "__main__":
    unittest.main()


def test_artifact_role_cannot_read_other_objects_or_assume_another_identity():
    template = json.loads((ROOT / "artifact-reader.template.json").read_text())
    resources = template["Resources"]
    assert len(resources) == 1
    role = resources["QwenArtifactReader"]
    assert role["Type"] == "AWS::IAM::Role"
    props = role["Properties"]
    assert props["RoleName"] == "fsi-demo-qwen-artifacts"
    assert not props.get("ManagedPolicyArns")
    assert len(props["Policies"]) == 1
    statements = props["Policies"][0]["PolicyDocument"]["Statement"]
    assert len(statements) == 1
    statement = statements[0]
    assert statement["Effect"] == "Allow" and statement["Action"] == "s3:GetObject"
    expected = {f"arn:aws:s3:::{uploader.BUCKET}/{a.key}" for a in uploader.load_manifest()}
    assert set(statement["Resource"]) == expected and len(expected) == 21
    assert statement["Condition"] == {
        "StringEquals": {"aws:SourceVpce": "vpce-04a82e15d312f39b8"},
        "Bool": {"aws:SecureTransport": "true"},
    }
    issuer = "oidc.eks.ap-northeast-2.amazonaws.com/id/269C604E98E9CB47E169C868AC3A1587"
    trust = props["AssumeRolePolicyDocument"]["Statement"]
    assert trust == [{
        "Effect": "Allow",
        "Principal": {"Federated": f"arn:aws:iam::{uploader.ACCOUNT}:oidc-provider/{issuer}"},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {"StringEquals": {
            issuer + ":aud": "sts.amazonaws.com",
            issuer + ":sub": "system:serviceaccount:sllm:qwen-artifacts",
        }},
    }]
