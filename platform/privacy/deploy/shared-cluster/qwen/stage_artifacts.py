"""Reviewed-parent publisher. Default is offline plan; --publish is explicit.

No production downloads/uploads were executed while preparing this script.
One <=4 GB file at a time; no multipart upload and no overwrite. Failed files
remain for parent review, and subsequent runs refuse to accumulate another.
"""
import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import sys
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
STAGE = Path("/home/atomoh/.cache/mydata-qwen-artifacts")
ACCOUNT = "180294183052"
BUCKET = "fsi-demo-models-180294183052"
REGION = "ap-northeast-2"
MAX_BYTES = 4_000_000_000
CHUNK = 8 * 1024 * 1024
REVISIONS = {
    "Qwen/Qwen3-8B": "b968826d9c46dd6066d109eabc6255188de91218",
    "Qwen/Qwen3-0.6B": "c1899de289a04d12100db370d81485cdf75e47ca",
}
COMMON_FILES = {
    "LICENSE", "config.json", "generation_config.json", "merges.txt",
    "tokenizer.json", "tokenizer_config.json", "vocab.json",
}
EXPECTED_FILES = {
    "Qwen/Qwen3-8B": COMMON_FILES | {"model.safetensors.index.json"} | {
        f"model-{i:05d}-of-00005.safetensors" for i in range(1, 6)
    },
    "Qwen/Qwen3-0.6B": COMMON_FILES | {"model.safetensors"},
}


class Failure(RuntimeError):
    """Only fixed diagnostic codes and an owned local path may be displayed."""
    def __init__(self, code, staged_path=None):
        super().__init__(code)
        self.code = code
        self.staged_path = str(staged_path) if staged_path else None


@dataclass(frozen=True)
class Artifact:
    model: str
    revision: str
    filename: str
    size: int
    sha256: str

    @property
    def key(self):
        return f"qwen-offline/{self.model.split('/')[1]}/{self.revision}/{self.filename}"

    @property
    def url(self):
        return f"https://huggingface.co/{self.model}/resolve/{self.revision}/{self.filename}"

    @property
    def checksum(self):
        return base64.b64encode(bytes.fromhex(self.sha256)).decode("ascii")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Failure("duplicate_json_key")
        result[key] = value
    return result


def validate_manifest(manifest):
    if (manifest.get("schema") != 1 or manifest.get("bucket") != BUCKET
            or manifest.get("region") != REGION
            or manifest.get("key_prefix") != "qwen-offline"):
        raise Failure("manifest_destination_or_schema")
    models = manifest.get("models")
    if not isinstance(models, list) or len(models) != 2:
        raise Failure("manifest_models")
    seen_models, keys, artifacts = set(), set(), []
    for model in models:
        model_id = model.get("id")
        if model_id not in REVISIONS or model_id in seen_models:
            raise Failure("manifest_model_id")
        seen_models.add(model_id)
        revision = model.get("revision")
        if revision != REVISIONS[model_id]:
            raise Failure("manifest_revision")
        files = model.get("files")
        if not isinstance(files, list):
            raise Failure("manifest_files")
        seen_files = set()
        for file in files:
            name = file.get("path")
            if (not isinstance(name, str) or name not in EXPECTED_FILES[model_id]
                    or name in seen_files or "/" in name or "\\" in name):
                raise Failure("manifest_path")
            seen_files.add(name)
            size, digest = file.get("size"), file.get("sha256")
            if type(size) is not int or not 0 < size <= MAX_BYTES or size >= 5_000_000_000:
                raise Failure("manifest_size")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise Failure("manifest_sha256")
            artifact = Artifact(model_id, revision, name, size, digest)
            if artifact.key in keys:
                raise Failure("duplicate_object_key")
            keys.add(artifact.key)
            artifacts.append(artifact)
        if seen_files != EXPECTED_FILES[model_id]:
            raise Failure("manifest_incomplete_model")
    return artifacts


def load_manifest():
    with (ROOT / "artifact-manifest.json").open("rb") as handle:
        raw = handle.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise Failure("manifest_too_large")
    return validate_manifest(json.loads(raw, object_pairs_hook=unique_object))


def validate_public_url(url, resolver=None):
    """Restrict initial/redirect destinations; never display signed query URLs."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or not (host == "huggingface.co" or host.endswith(".huggingface.co")
                    or host.endswith(".hf.co"))):
        raise Failure("download_destination_not_allowed")
    resolve = resolver or socket.getaddrinfo
    answers = resolve(host, 443, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(row[4][0]).is_global for row in answers):
        raise Failure("download_destination_not_public")


class PublicHTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def public_open(url):
    validate_public_url(url)
    # Direct HTTPS; do not inherit a proxy that could redirect public fetches.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), PublicHTTPSRedirect()
    )
    request = urllib.request.Request(url, headers={
        "Accept-Encoding": "identity", "User-Agent": "qwen-artifact-publisher/1",
    })
    return opener.open(request, timeout=120)


def create_clients():
    # Imports/client construction occur only with --publish, never in dry run.
    # The approved publisher host uses its normal SDK credential chain.
    # EKS init-container IRSA/no-IMDS settings are separate from this publisher.
    import boto3
    from botocore.config import Config
    for name in ("boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    config = Config(
        retries={"total_max_attempts": 2, "mode": "standard"},
        connect_timeout=5, read_timeout=120, max_pool_connections=1,
        signature_version="v4", use_dualstack_endpoint=False,
    )
    s3_config = config.merge(Config(
        signature_version="s3v4",
        s3={"addressing_style": "virtual", "use_accelerate_endpoint": False},
    ))
    session = boto3.Session(region_name=REGION)
    s3 = session.client("s3", config=s3_config,
                        endpoint_url=f"https://s3.{REGION}.amazonaws.com")
    sts = session.client("sts", config=config,
                         endpoint_url=f"https://sts.{REGION}.amazonaws.com")
    required = {"IfNoneMatch", "ExpectedBucketOwner", "ChecksumSHA256", "ContentLength"}
    if not required <= set(s3.meta.service_model.operation_model("PutObject").input_shape.members):
        raise Failure("boto3_putobject_model_too_old")
    return s3, sts


def remote_matches(s3, artifact, version_id=None):
    request = {
        "Bucket": BUCKET, "Key": artifact.key,
        "ExpectedBucketOwner": ACCOUNT, "ChecksumMode": "ENABLED",
    }
    if version_id:
        request["VersionId"] = version_id
    try:
        response = s3.head_object(**request)
    except s3.exceptions.ClientError as error:
        # HEAD's unmodeled 404 is not necessarily the typed NoSuchKey exception.
        if error.response.get("Error", {}).get("Code") in ("404", "NotFound", "NoSuchKey"):
            return False
        raise
    if (response.get("ContentLength") != artifact.size
            or response.get("ChecksumSHA256") != artifact.checksum
            or response.get("ChecksumType") != "FULL_OBJECT"
            or response.get("ServerSideEncryption") != "AES256"):
        raise Failure("s3_existing_or_uploaded_object_mismatch")
    return True


@contextmanager
def staging_lock():
    STAGE.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = STAGE.lstat()
    if (STAGE.resolve() != STAGE or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise Failure("staging_directory_not_private_owned_directory")
    descriptor = os.open(STAGE / ".qwen-artifact-upload.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        info = os.fstat(lock.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise Failure("unsafe_staging_lock")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("another_publisher_is_running") from None
        if list(STAGE.glob("qwen-upload-*.part")):
            raise Failure("retained_artifact_needs_parent_review")
        yield


def download_and_verify(artifact, handle, open_url):
    with open_url(artifact.url) as response:
        if response.getcode() != 200:
            raise Failure("download_http_status")
        content_length = response.headers.get("Content-Length")
        if content_length is not None and content_length != str(artifact.size):
            raise Failure("download_content_length")
        if response.headers.get("Content-Encoding", "identity") != "identity":
            raise Failure("download_content_encoding")
        total = 0
        while True:
            # Read at most the remaining exact size plus one overflow byte.
            chunk = response.read(min(CHUNK, artifact.size - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > artifact.size or total > MAX_BYTES:
                raise Failure("download_oversize")
            handle.write(chunk)
        if total != artifact.size:
            raise Failure("download_partial")
    handle.flush()
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(CHUNK), b""):
        digest.update(chunk)
    if digest.hexdigest() != artifact.sha256:
        raise Failure("download_sha256_mismatch")
    handle.seek(0)


def delete_owned(path, identity):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or (info.st_dev, info.st_ino) != identity):
        raise Failure("staged_file_identity_changed")
    path.unlink()


def publish_one(s3, artifact, open_url):
    if remote_matches(s3, artifact):
        return {"key": artifact.key, "already_verified_in_s3": True}
    if shutil.disk_usage(STAGE).free < artifact.size + 128 * 1024 * 1024:
        raise Failure("insufficient_staging_space")
    descriptor, filename = tempfile.mkstemp(prefix="qwen-upload-", suffix=".part", dir=STAGE)
    path = Path(filename)
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    try:
        with os.fdopen(descriptor, "w+b") as handle:
            download_and_verify(artifact, handle, open_url)
            version_id = None
            try:
                result = s3.put_object(
                    Bucket=BUCKET, Key=artifact.key, Body=handle,
                    ContentLength=artifact.size, ChecksumAlgorithm="SHA256",
                    ChecksumSHA256=artifact.checksum, ExpectedBucketOwner=ACCOUNT,
                    IfNoneMatch="*", ServerSideEncryption="AES256",
                    Metadata={"sha256": artifact.sha256},
                )
                if result.get("ChecksumSHA256") != artifact.checksum:
                    raise Failure("putobject_response_checksum_mismatch")
                version_id = result.get("VersionId")
            except s3.exceptions.ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in ("412", "PreconditionFailed", "409", "ConditionalRequestConflict"):
                    raise
                # Conditional races never authorize overwrite or unconditional retry.
            if not remote_matches(s3, artifact, version_id):
                raise Failure("s3_object_missing_after_put")
        # Sole deletion site: after full local verification and S3 checksum HEAD.
        delete_owned(path, identity)
        return {"key": artifact.key, "s3_sha256_verified": True, "own_staged_file_deleted": True}
    except Exception as error:
        # Retain this run's file, abort the sequence, suppress URLs/credential errors.
        code = error.code if isinstance(error, Failure) else type(error).__name__
        raise Failure(code, path) from None


def publish(artifacts, s3, sts, open_url=None, emit=None):
    if sts.get_caller_identity().get("Account") != ACCOUNT:
        raise Failure("caller_account_mismatch")
    open_url = open_url or public_open
    emit = emit or (lambda result: print(json.dumps(result)))
    with staging_lock():
        for artifact in artifacts:
            emit(publish_one(s3, artifact, open_url))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true",
                        help="After parent review, download/verify/publish sequentially.")
    args = parser.parse_args(argv)
    try:
        artifacts = load_manifest()
        if not args.publish:
            print(json.dumps({
                "mode": "offline_plan_only", "files": len(artifacts),
                "staging_directory": str(STAGE),
                "peak_staged_bytes": max(a.size for a in artifacts),
                "total_artifact_bytes": sum(a.size for a in artifacts),
                "aws_calls": 0, "downloads": 0, "writes": 0,
            }, indent=2))
            return 0
        s3, sts = create_clients()
        publish(artifacts, s3, sts)
        return 0
    except Exception as error:
        result = {"status": "FAIL",
                  "code": error.code if isinstance(error, Failure) else type(error).__name__}
        if isinstance(error, Failure) and error.staged_path:
            result["retained_staged_file"] = error.staged_path
        print(json.dumps(result), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
