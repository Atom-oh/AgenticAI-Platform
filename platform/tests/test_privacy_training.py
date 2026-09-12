"""Offline preparation only: all examples, accounts and digests here are synthetic."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from privacy.training import datasets, jobs
from privacy.training.datasets import PreparationError, validate_dataset
from privacy.training.jobs import prepare_job


def example(identifier="train-1", text="김테스트 kim@example.invalid 300,000원 3.5% 12개월"):
    return {
        "id": identifier,
        "text": text,
        "entities": [
            {"type": "PERSON", "original": "김테스트"},
            {"type": "EMAIL", "original": "kim@example.invalid"},
        ],
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return path


@pytest.fixture
def splits(tmp_path):
    return (
        write_jsonl(tmp_path / "train.jsonl", [example()]),
        write_jsonl(tmp_path / "eval.jsonl", [example("eval-1", "김테스트 kim@example.invalid 400,000원 2.5% 24개월")]),
    )


@pytest.fixture
def config():
    key = "arn:aws:kms:ap-northeast-2:111122223333:key/12345678-1234-1234-1234-123456789abc"
    return {
        "training_job_name": "mydata-synthetic-prep",
        "region": "ap-northeast-2",
        "role_arn": "arn:aws:iam::111122223333:role/mydata-trainer",
        "training_image": "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/approved/trainer@sha256:" + "a" * 64,
        "trainer_contract_version": "mydata-entities-jsonl-v1",
        "trainer_contract_approved": True,
        "model_family": "qwen",
        "base_model_s3_uri": "s3://mydata-private-fixture/base/model.tar.gz",
        "base_model_sha256": "b" * 64,
        "train_s3_uri": "s3://mydata-private-fixture/dataset/train.jsonl",
        "eval_s3_uri": "s3://mydata-private-fixture/dataset/eval.jsonl",
        "output_s3_uri": "s3://mydata-private-fixture/output/",
        "output_kms_key_arn": key,
        "volume_kms_key_arn": key,
        "vpc_id": "vpc-0123456789abcdef0",
        "private_subnet_ids": ["subnet-0123456789abcdef0"],
        "security_group_ids": ["sg-0123456789abcdef0"],
        "private_network_approved": True,
        "instance_type": "ml.p3.2xlarge",
        "volume_size_gb": 100,
        "max_runtime_seconds": 3600,
    }


def test_manifest_hashes_exact_bytes_counts_annotations_and_never_exports_examples(splits):
    train, evaluation = splits
    result = validate_dataset(train, evaluation, data_policy="synthetic")
    assert result["status"] == "validated-offline"
    assert result["splits"]["train"] == {
        "sha256": hashlib.sha256(train.read_bytes()).hexdigest(),
        "bytes": train.stat().st_size,
        "records": 1,
        "entities": 2,
        "entity_counts": {"PERSON": 1, "EMAIL": 1},
    }
    serialized = json.dumps(result, ensure_ascii=False)
    for private_value in ("김테스트", "kim@example.invalid", "300,000", "train-1", str(train)):
        assert private_value not in serialized
    assert result == validate_dataset(train, evaluation, data_policy="synthetic")


@pytest.mark.parametrize("entities", [
    None, {}, ["김테스트"], [None], [{"type": "PERSON"}],
    [{"type": "PERSON", "original": "김테스트", "confidence": 1}],
    [{"type": ["PERSON"], "original": "김테스트"}],
    [{"type": "UNSUPPORTED", "original": "김테스트"}],
    [{"type": "PERSON", "original": 5}],
    [{"type": "PERSON", "original": ""}],
    [{"type": "PERSON", "original": " 김테스트"}],
    [{"type": "PERSON", "original": "없는테스트"}],
    [{"type": "PERSON", "original": "김테스트"}] * 2,
    [{"type": "PERSON", "original": "김테스트"}, {"type": "ADDRESS", "original": "김테스트"}],
])
def test_bad_entity_formats_fail_closed_without_echoing_data(splits, entities):
    row = example()
    row["entities"] = entities
    write_jsonl(splits[0], [row])
    with pytest.raises(PreparationError) as raised:
        validate_dataset(*splits, data_policy="synthetic")
    assert "김테스트" not in str(raised.value)
    assert "kim@example.invalid" not in str(raised.value)


@pytest.mark.parametrize("row", [
    [], None, {}, {"id": "one", "text": "test", "entities": [], "extra": True},
    {"id": "", "text": "test", "entities": []},
    {"id": True, "text": "test", "entities": []},
    {"id": "one", "text": None, "entities": []},
    {"id": "one", "text": " ", "entities": []},
    {"id": "one", "text": "\ud800", "entities": []},
])
def test_bad_records_are_rejected(splits, row):
    splits[0].write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(PreparationError):
        validate_dataset(*splits, data_policy="synthetic")


@pytest.mark.parametrize("raw", [
    b"", b"\n", b"not-json\n", b"\xff\n",
    b'{"id":"one","id":"two","text":"x","entities":[]}\n',
    b'{"id":"one","text":NaN,"entities":[]}\n',
    b"[" * 1500 + b"]" * 1500,
])
def test_bad_jsonl_is_rejected_without_tracebacks(splits, raw):
    splits[0].write_bytes(raw)
    with pytest.raises(PreparationError):
        validate_dataset(*splits, data_policy="synthetic")


@pytest.mark.parametrize("row,reason", [
    (example("train-1", "김테스트 kim@example.invalid 다른 문장"), "id"),
    (example("new-id"), "content"),
    ({"id": "new-id", "text": example()["text"], "entities": []}, "content"),
    (example("new-id", "  김테스트  kim@example.invalid ３００,０００원 3.5% 12개월\n"), "content"),
])
def test_leakage_uses_ids_and_normalized_text_even_when_labels_change(splits, row, reason):
    write_jsonl(splits[1], [row])
    with pytest.raises(PreparationError, match=reason):
        validate_dataset(*splits, data_policy="synthetic")


def test_duplicate_within_split_is_rejected(splits):
    write_jsonl(splits[0], [example(), example("another-id")])
    with pytest.raises(PreparationError, match="content"):
        validate_dataset(*splits, data_policy="synthetic")


def test_all_gateway_entity_types_and_negative_examples_are_supported(splits):
    from privacy.gateway.models import TYPES

    values = [f"synthetic-{index}" for index, _ in enumerate(TYPES)]
    row = {"id": "all-types", "text": " ".join(values),
           "entities": [{"type": kind, "original": value} for kind, value in zip(TYPES, values)]}
    write_jsonl(splits[0], [row, {"id": "negative", "text": "납입액 200,000원, 금리 4%, 36개월", "entities": []}])
    manifest = validate_dataset(*splits, data_policy="synthetic")
    assert manifest["splits"]["train"]["records"] == 2
    assert manifest["splits"]["train"]["entity_counts"] == dict.fromkeys(TYPES, 1)


@pytest.mark.parametrize("limit,value", [
    ("MAX_FILE_BYTES", 40), ("MAX_LINE_BYTES", 40), ("MAX_RECORDS", 1),
    ("MAX_TEXT_BYTES", 20), ("MAX_ENTITIES", 1), ("MAX_ORIGINAL_CHARS", 3),
])
def test_dataset_resource_limits_are_enforced(splits, monkeypatch, limit, value):
    write_jsonl(splits[0], [example(), example("train-2", "김테스트 kim@example.invalid 두 번째 문장")])
    monkeypatch.setattr(datasets, limit, value)
    with pytest.raises(PreparationError, match="limit"):
        validate_dataset(*splits, data_policy="synthetic")


def test_text_limit_counts_utf8_bytes(splits):
    write_jsonl(splits[0], [{"id": "wide", "text": "가" * 3000, "entities": []}])
    with pytest.raises(PreparationError, match="limit"):
        validate_dataset(*splits, data_policy="synthetic")


def test_approved_data_requires_reference_but_reference_is_only_hashed(splits):
    with pytest.raises(PreparationError, match="approval"):
        validate_dataset(*splits, data_policy="approved")
    manifest = validate_dataset(*splits, data_policy="approved", approval_reference="internal-review-123")
    assert manifest["data_policy"]["approval_reference_sha256"] == hashlib.sha256(b"internal-review-123").hexdigest()
    assert "internal-review-123" not in json.dumps(manifest)
    with pytest.raises(PreparationError):
        validate_dataset(*splits, data_policy="raw")


def test_approval_reference_limit_applies_before_whitespace_stripping(splits):
    with pytest.raises(PreparationError, match="approval"):
        validate_dataset(*splits, data_policy="approved", approval_reference=" " * 1000 + "REVIEW")


def test_exact_file_line_and_record_limits_are_inclusive(splits, monkeypatch):
    monkeypatch.setattr(datasets, "MAX_RECORDS", 1)
    # Use the larger split's exact byte length as both the file and line cap.
    limit = max(path.stat().st_size for path in splits)
    monkeypatch.setattr(datasets, "MAX_FILE_BYTES", limit)
    monkeypatch.setattr(datasets, "MAX_LINE_BYTES", limit)
    assert validate_dataset(*splits, data_policy="synthetic")["splits"]["eval"]["records"] == 1


def test_prepare_emits_sagemaker_request_with_private_encrypted_channels(splits, config):
    manifest = validate_dataset(*splits, data_policy="synthetic")
    job = prepare_job(config, manifest)
    assert job["AlgorithmSpecification"] == {
        "TrainingImage": config["training_image"], "TrainingInputMode": "File",
    }
    assert job["RoleArn"] == config["role_arn"]
    assert job["EnableNetworkIsolation"] is True
    assert job["EnableInterContainerTrafficEncryption"] is True
    assert job["VpcConfig"] == {"Subnets": config["private_subnet_ids"], "SecurityGroupIds": config["security_group_ids"]}
    assert job["ResourceConfig"] == {
        "InstanceCount": 1, "InstanceType": "ml.p3.2xlarge",
        "VolumeSizeInGB": 100, "VolumeKmsKeyId": config["volume_kms_key_arn"],
    }
    assert job["OutputDataConfig"] == {"S3OutputPath": config["output_s3_uri"], "KmsKeyId": config["output_kms_key_arn"]}
    assert job["StoppingCondition"] == {"MaxRuntimeInSeconds": 3600}
    assert job["ProfilerConfig"] == {"DisableProfiler": True}
    channels = {item["ChannelName"]: item for item in job["InputDataConfig"]}
    assert set(channels) == {"train", "eval", "base-model"}
    for name, key in (("train", "train_s3_uri"), ("eval", "eval_s3_uri"), ("base-model", "base_model_s3_uri")):
        assert channels[name]["InputMode"] == "File"
        assert channels[name]["DataSource"]["S3DataSource"] == {
            "S3Uri": config[key], "S3DataType": "S3Prefix", "S3DataDistributionType": "FullyReplicated",
        }
    assert job["Environment"]["MYDATA_TRAIN_SHA256"] == hashlib.sha256(splits[0].read_bytes()).hexdigest()
    assert job["Environment"]["MYDATA_EVAL_SHA256"] == hashlib.sha256(splits[1].read_bytes()).hexdigest()
    assert job["Environment"]["MYDATA_BASE_MODEL_SHA256"] == "b" * 64
    assert "VpcId" not in job["VpcConfig"]
    assert "김테스트" not in json.dumps(job, ensure_ascii=False)


@pytest.mark.parametrize("key,value", [
    ("training_image", "public.ecr.aws/example/trainer@sha256:" + "a" * 64),
    ("training_image", "docker.io/example/trainer@sha256:" + "a" * 64),
    ("training_image", "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/trainer:latest"),
    ("training_image", "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/trainer@sha256:" + "a" * 63),
    ("training_image", "111122223333.dkr.ecr.us-east-1.amazonaws.com/trainer@sha256:" + "a" * 64),
    ("training_image", "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com.evil.invalid/trainer@sha256:" + "a" * 64),
    ("trainer_contract_approved", False), ("trainer_contract_approved", "true"),
    ("trainer_contract_version", "generic-huggingface"), ("model_family", "unknown"),
    ("base_model_sha256", "main"), ("base_model_s3_uri", "https://model.invalid/model.tar.gz"),
    ("train_s3_uri", "s3://mydata-private-fixture/"), ("train_s3_uri", "s3://mydata-private-fixture/x.jsonl?versionId=1"),
    ("train_s3_uri", "s3://mydata-private-fixture/../train.jsonl"),
    ("train_s3_uri", "s3://mydata-private-fixture/a%2ftrain.jsonl"),
    ("eval_s3_uri", "s3://mydata-private-fixture/dataset/train.jsonl"),
    ("output_s3_uri", "s3://mydata-private-fixture/dataset/"),
    ("role_arn", "arn:aws:iam::111122223333:user/operator"),
    ("role_arn", "arn:aws:iam::444455556666:role/trainer"),
    ("output_kms_key_arn", ""), ("output_kms_key_arn", "alias/aws/s3"),
    ("output_kms_key_arn", "arn:aws:kms:us-east-1:111122223333:key/12345678-1234-1234-1234-123456789abc"),
    ("volume_kms_key_arn", "arn:aws:kms:ap-northeast-2:444455556666:key/12345678-1234-1234-1234-123456789abc"),
    ("volume_kms_key_arn", "*"),
    ("vpc_id", ""), ("private_subnet_ids", []), ("security_group_ids", []),
    ("private_subnet_ids", ["subnet-public"]), ("security_group_ids", ["0.0.0.0/0"]),
    ("private_subnet_ids", ["subnet-01234567"] * 2),
    ("security_group_ids", ["sg-01234567"] * 6),
    ("private_network_approved", False), ("private_network_approved", "true"),
    ("instance_type", "ml.g5.xlarge"), ("instance_type", "ml.p3dn.24xlarge"),
    ("max_runtime_seconds", 0), ("max_runtime_seconds", 86401), ("max_runtime_seconds", True),
    ("volume_size_gb", 0), ("volume_size_gb", 4097), ("volume_size_gb", 1.5),
    ("training_job_name", "-bad"), ("region", "ap-northeast-2.evil.invalid"),
])
def test_unsafe_or_unsupported_job_configuration_is_rejected(splits, config, key, value):
    config[key] = value
    with pytest.raises(PreparationError):
        prepare_job(config, validate_dataset(*splits, data_policy="synthetic"))


def test_unknown_overrides_and_missing_required_config_are_rejected(splits, config):
    manifest = validate_dataset(*splits, data_policy="synthetic")
    for key in config:
        missing = {name: value for name, value in config.items() if name != key}
        with pytest.raises(PreparationError):
            prepare_job(missing, manifest)
    config["EnableNetworkIsolation"] = False
    with pytest.raises(PreparationError):
        prepare_job(config, manifest)


def test_manifest_shape_cannot_inject_environment_values(splits, config):
    manifest = validate_dataset(*splits, data_policy="synthetic")
    corrupt = copy.deepcopy(manifest)
    corrupt["splits"]["train"]["sha256"] = "김테스트"
    with pytest.raises(PreparationError):
        prepare_job(config, corrupt)


@pytest.mark.parametrize("family", ["qwen", "gemma4", "deepseek"])
def test_family_selection_keeps_explicit_trainer_and_bundle_pins(splits, config, family):
    config["model_family"] = family
    job = prepare_job(config, validate_dataset(*splits, data_policy="synthetic"))
    assert job["Environment"]["MYDATA_MODEL_FAMILY"] == family
    assert job["AlgorithmSpecification"]["TrainingImage"] == config["training_image"]
    assert job["Environment"]["MYDATA_BASE_MODEL_SHA256"] == config["base_model_sha256"]


def test_network_id_count_limits_reject_unique_overflow(splits, config):
    manifest = validate_dataset(*splits, data_policy="synthetic")
    for key, prefix, count in (("private_subnet_ids", "subnet", 17), ("security_group_ids", "sg", 6)):
        invalid = {**config, key: [f"{prefix}-{index:08x}" for index in range(count)]}
        with pytest.raises(PreparationError):
            prepare_job(invalid, manifest)


def run_cli(*arguments):
    return subprocess.run(
        [sys.executable, "-S", "-m", "privacy.training", *map(str, arguments)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True, text=True, check=False,
    )


def test_cli_works_with_only_stdlib_and_writes_reviewable_artifacts(splits, config, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "prepared"
    result = run_cli("prepare", "--train", splits[0], "--eval", splits[1], "--data-policy", "synthetic",
                     "--config", config_path, "--output-dir", output)
    assert result.returncode == 0, result.stderr
    assert set(path.name for path in output.iterdir()) == {"manifest.json", "training-job.json", "preparation.json"}
    receipt = json.loads((output / "preparation.json").read_text())
    assert receipt["status"] == "prepared-offline"
    assert receipt["aws_calls_made"] is False
    assert receipt["training_started"] is False
    assert receipt["vpc_id"] == config["vpc_id"]
    assert receipt["request_sha256"] == hashlib.sha256((output / "training-job.json").read_bytes()).hexdigest()
    assert "김테스트" not in result.stdout + result.stderr
    assert (output / "training-job.json").stat().st_mode & 0o777 == 0o600
    before = (output / "manifest.json").read_bytes()
    again = run_cli("validate", "--train", splits[0], "--eval", splits[1], "--data-policy", "synthetic",
                    "--output-dir", output)
    assert again.returncode == 2
    assert (output / "manifest.json").read_bytes() == before


def test_cli_failure_writes_no_artifacts_and_never_echoes_bad_data(splits, tmp_path):
    splits[0].write_text('{"id":"김테스트",bad-json}\n', encoding="utf-8")
    output = tmp_path / "failed"
    result = run_cli("validate", "--train", splits[0], "--eval", splits[1],
                     "--data-policy", "synthetic", "--output-dir", output)
    assert result.returncode == 2
    assert not output.exists()
    assert "김테스트" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("contents", [
    '{"training_image":"김테스트", "training_image":"duplicate"}',
    "{" * 1000,
    " " * (32 * 1024 + 1),
])
def test_config_parse_errors_and_byte_limit_fail_without_artifacts(splits, tmp_path, contents):
    config_path = tmp_path / "config.json"
    config_path.write_text(contents, encoding="utf-8")
    output = tmp_path / "bad-config"
    result = run_cli("prepare", "--train", splits[0], "--eval", splits[1], "--data-policy", "synthetic",
                     "--config", config_path, "--output-dir", output)
    assert result.returncode == 2
    assert not output.exists()
    assert "김테스트" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_prepare_does_not_open_network_connections(splits, config, tmp_path, monkeypatch):
    import socket
    from privacy.training.__main__ import main

    def forbidden(*_args, **_kwargs):
        pytest.fail("offline preparation attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    assert main(["prepare", "--train", str(splits[0]), "--eval", str(splits[1]),
                 "--data-policy", "synthetic", "--config", str(config_path),
                 "--output-dir", str(tmp_path / "offline")]) == 0


def test_example_config_cannot_accidentally_prepare_training(splits, tmp_path):
    config_path = Path(__file__).resolve().parents[1] / "privacy/training/config.example.json"
    result = run_cli("prepare", "--train", splits[0], "--eval", splits[1], "--data-policy", "synthetic",
                     "--config", config_path, "--output-dir", tmp_path / "not-approved")
    assert result.returncode == 2
    assert not (tmp_path / "not-approved").exists()


def test_nonregular_or_missing_input_is_rejected_without_hanging(splits, tmp_path):
    for path in (tmp_path, tmp_path / "absent"):
        with pytest.raises(PreparationError):
            validate_dataset(path, splits[1], data_policy="synthetic")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(PreparationError):
        validate_dataset(fifo, splits[1], data_policy="synthetic")
    with pytest.raises(PreparationError):
        datasets.read_config(fifo)


def test_public_fixture_validation_is_executable(tmp_path):
    fixtures = Path(__file__).resolve().parents[1] / "privacy/training/fixtures"
    result = run_cli("validate", "--train", fixtures / "train.jsonl", "--eval", fixtures / "eval.jsonl",
                     "--data-policy", "synthetic", "--output-dir", tmp_path / "fixtures-validated")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "fixtures-validated/manifest.json").read_text())
    assert manifest["splits"]["train"]["records"] >= 2
    assert manifest["splits"]["eval"]["records"] >= 2
