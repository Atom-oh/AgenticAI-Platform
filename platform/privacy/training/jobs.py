"""Build a CreateTrainingJob request as plain data. Never import an AWS SDK."""
from __future__ import annotations

import ipaddress
import re

from ..gateway.models import TYPES
from . import CONTRACT_VERSION
from .datasets import PreparationError
from .registry import MODEL_FAMILIES

# Deliberately small EBS-only subset: g5/g4dn/p3dn/p4d have local storage and
# cannot meet this workflow's explicit VolumeKmsKeyId requirement.
EBS_INSTANCE_TYPES = frozenset({
    "ml.p3.2xlarge", "ml.p3.8xlarge", "ml.p3.16xlarge",
    "ml.m5.xlarge", "ml.m5.2xlarge",
})
FIELDS = frozenset({
    "training_job_name", "region", "role_arn", "training_image",
    "trainer_contract_version", "trainer_contract_approved", "model_family",
    "base_model_s3_uri", "base_model_sha256", "train_s3_uri", "eval_s3_uri",
    "output_s3_uri", "output_kms_key_arn", "volume_kms_key_arn", "vpc_id",
    "private_subnet_ids", "security_group_ids", "private_network_approved",
    "instance_type", "volume_size_gb", "max_runtime_seconds",
})
DIGEST = r"[a-f0-9]{64}"


def _match(value, pattern: str, field: str, maximum: int = 1024):
    if not isinstance(value, str) or len(value) > maximum or not re.fullmatch(pattern, value):
        raise PreparationError(f"invalid {field}")
    return re.fullmatch(pattern, value)


def _integer(value, minimum: int, maximum: int, field: str):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PreparationError(f"{field} outside preparation limits")


def _ids(values, prefix: str, maximum: int):
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise PreparationError(f"invalid {prefix} list")
    for value in values:
        _match(value, rf"{prefix}-(?:[a-f0-9]{{8}}|[a-f0-9]{{17}})", prefix)
    if len(set(values)) != len(values):
        raise PreparationError(f"duplicate {prefix}")


def _s3(value, field: str, suffix: str):
    match = _match(value, r"s3://([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/([A-Za-z0-9][A-Za-z0-9._/-]*)", field)
    bucket, key = match.groups()
    try:
        ipaddress.ip_address(bucket)
    except ValueError:
        pass
    else:
        raise PreparationError(f"invalid {field} bucket")
    parts = key.rstrip("/").split("/")
    if (".." in bucket or ".-" in bucket or "-." in bucket or
            any(part in ("", ".", "..") for part in parts) or not value.endswith(suffix)):
        raise PreparationError(f"invalid {field}")


def _manifest_hashes(manifest: dict) -> dict:
    """Only bounded hashes can flow from a supplied manifest into job metadata."""
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("status") != "validated-offline"
            or manifest.get("dataset_format") != CONTRACT_VERSION):
        raise PreparationError("invalid validated manifest")
    policy = manifest.get("data_policy")
    if (not isinstance(policy, dict) or policy.get("classification") not in ("synthetic", "approved")
            or policy.get("verification") != "operator-declared"):
        raise PreparationError("invalid manifest data policy")
    if policy["classification"] == "approved":
        _match(policy.get("approval_reference_sha256"), DIGEST, "approval reference hash")
    splits = manifest.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "eval"}:
        raise PreparationError("invalid manifest splits")
    hashes = {}
    for name, split in splits.items():
        if not isinstance(split, dict):
            raise PreparationError("invalid manifest split")
        _match(split.get("sha256"), DIGEST, "manifest hash")
        _integer(split.get("records"), 1, 10_000, "manifest records")
        _integer(split.get("bytes"), 1, 32 * 1024 * 1024, "manifest bytes")
        _integer(split.get("entities"), 0, split["records"] * 64, "manifest entities")
        counts = split.get("entity_counts")
        if not isinstance(counts, dict) or any(kind not in TYPES for kind in counts):
            raise PreparationError("invalid manifest entity counts")
        for count in counts.values():
            _integer(count, 1, split["entities"], "manifest entity count")
        if sum(counts.values()) != split["entities"]:
            raise PreparationError("inconsistent manifest entity counts")
        hashes[name] = split["sha256"]
    if hashes["train"] == hashes["eval"]:
        raise PreparationError("identical manifest splits")
    return hashes


def prepare_job(config: dict, manifest: dict) -> dict:
    """Require operator-approved infrastructure/trainer; verify syntax locally only."""
    if not isinstance(config, dict) or set(config) != FIELDS:
        raise PreparationError("config requires exactly the documented fields")
    hashes = _manifest_hashes(manifest)
    _match(config["training_job_name"], r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", "training_job_name", 63)
    _match(config["region"], r"(?:af|ap|ca|eu|il|me|mx|sa|us)-[a-z]+-[1-9][0-9]?", "region")
    region = re.escape(config["region"])
    image = _match(
        config["training_image"],
        rf"([0-9]{{12}})\.dkr\.ecr\.{region}\.amazonaws\.com/"
        rf"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:{DIGEST}",
        "private digest-pinned ECR training_image", 255,
    )
    account = image.group(1)
    _match(config["role_arn"], rf"arn:aws:iam::{account}:role/[A-Za-z0-9+=,.@_/-]+", "role_arn")
    for field in ("output_kms_key_arn", "volume_kms_key_arn"):
        _match(config[field], rf"arn:aws:kms:{region}:{account}:key/"
               r"(?:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|mrk-[a-f0-9]{32})", field)
    if config["trainer_contract_version"] != CONTRACT_VERSION or config["trainer_contract_approved"] is not True:
        raise PreparationError("explicit trainer contract approval required")
    if not isinstance(config["model_family"], str) or config["model_family"] not in MODEL_FAMILIES:
        raise PreparationError("unknown candidate model family")
    _match(config["base_model_sha256"], DIGEST, "base_model_sha256")
    for field, suffix in (("train_s3_uri", ".jsonl"), ("eval_s3_uri", ".jsonl"),
                          ("base_model_s3_uri", ".tar.gz"), ("output_s3_uri", "/")):
        _s3(config[field], field, suffix)
    locations = [config[field] for field in ("train_s3_uri", "eval_s3_uri", "base_model_s3_uri", "output_s3_uri")]
    for index, first in enumerate(locations):
        if any(first.startswith(other) or other.startswith(first) for other in locations[index + 1:]):
            raise PreparationError("S3 input/output prefixes must be disjoint")
    _match(config["vpc_id"], r"vpc-(?:[a-f0-9]{8}|[a-f0-9]{17})", "vpc_id")
    _ids(config["private_subnet_ids"], "subnet", 16)
    _ids(config["security_group_ids"], "sg", 5)
    if config["private_network_approved"] is not True:
        raise PreparationError("explicit private network approval required")
    if not isinstance(config["instance_type"], str) or config["instance_type"] not in EBS_INSTANCE_TYPES:
        raise PreparationError("instance type must support this EBS KMS volume contract")
    _integer(config["volume_size_gb"], 1, 4096, "volume_size_gb")
    _integer(config["max_runtime_seconds"], 60, 86400, "max_runtime_seconds")
    channels = []
    for name, field in (("train", "train_s3_uri"), ("eval", "eval_s3_uri"), ("base-model", "base_model_s3_uri")):
        channels.append({
            "ChannelName": name, "InputMode": "File", "CompressionType": "None",
            "ContentType": "application/gzip" if name == "base-model" else "application/x-ndjson",
            "DataSource": {"S3DataSource": {"S3Uri": config[field], "S3DataType": "S3Prefix",
                                           "S3DataDistributionType": "FullyReplicated"}},
        })
    return {
        "TrainingJobName": config["training_job_name"],
        "RoleArn": config["role_arn"],
        "AlgorithmSpecification": {"TrainingImage": config["training_image"], "TrainingInputMode": "File"},
        "InputDataConfig": channels,
        "OutputDataConfig": {"S3OutputPath": config["output_s3_uri"], "KmsKeyId": config["output_kms_key_arn"]},
        "ResourceConfig": {"InstanceCount": 1, "InstanceType": config["instance_type"],
                           "VolumeSizeInGB": config["volume_size_gb"], "VolumeKmsKeyId": config["volume_kms_key_arn"]},
        "VpcConfig": {"Subnets": list(config["private_subnet_ids"]), "SecurityGroupIds": list(config["security_group_ids"])},
        "EnableNetworkIsolation": True,
        "EnableInterContainerTrafficEncryption": True,
        "EnableManagedSpotTraining": False,
        "ProfilerConfig": {"DisableProfiler": True},
        "StoppingCondition": {"MaxRuntimeInSeconds": config["max_runtime_seconds"]},
        "Environment": {
            "MYDATA_CONTRACT_VERSION": CONTRACT_VERSION, "MYDATA_MODEL_FAMILY": config["model_family"],
            "MYDATA_TRAIN_SHA256": hashes["train"], "MYDATA_EVAL_SHA256": hashes["eval"],
            "MYDATA_BASE_MODEL_SHA256": config["base_model_sha256"],
        },
    }
