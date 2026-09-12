"""Usage: PYTHONPATH=platform python3 -m privacy.training --help."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .datasets import PreparationError, read_config, validate_dataset
from .jobs import prepare_job
from .registry import MODEL_FAMILIES


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_bundle(directory: Path, artifacts: dict):
    # Exclusive directory creation prevents replacing inputs or previous approvals.
    directory.mkdir(mode=0o700)
    written = []
    try:
        for name, content in artifacts.items():
            path = directory / name
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                written.append(path)
                output.write(content)
    except OSError:
        for path in written:
            path.unlink()
        directory.rmdir()
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline MyData JSONL validation and optional SageMaker request preparation.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="show candidate metadata; no configured trainer is implied")
    for command in ("validate", "prepare"):
        sub = commands.add_parser(command)
        sub.add_argument("--train", type=Path, required=True)
        sub.add_argument("--eval", type=Path, required=True)
        sub.add_argument("--data-policy", choices=("synthetic", "approved"), required=True)
        sub.add_argument("--approval-reference", default="", help="required for approved data; only its SHA-256 is exported")
        sub.add_argument("--output-dir", type=Path, required=True, help="new directory beneath an existing parent")
        if command == "prepare":
            sub.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "models":
        print(_json_bytes(MODEL_FAMILIES).decode(), end="")
        return 0
    try:
        manifest = validate_dataset(args.train, args.eval, data_policy=args.data_policy,
                                    approval_reference=args.approval_reference)
        artifacts = {"manifest.json": _json_bytes(manifest)}
        status = "validated-offline"
        if args.command == "prepare":
            config = read_config(args.config)
            request = prepare_job(config, manifest)
            artifacts["training-job.json"] = _json_bytes(request)
            status = "prepared-offline"
            artifacts["preparation.json"] = _json_bytes({
                "status": status, "aws_calls_made": False, "training_started": False,
                "evaluated": False, "deployment_approved": False,
                "manifest_sha256": hashlib.sha256(artifacts["manifest.json"]).hexdigest(),
                "request_sha256": hashlib.sha256(artifacts["training-job.json"]).hexdigest(),
                "config_sha256": hashlib.sha256(_json_bytes(config)).hexdigest(),
                "region": config["region"], "vpc_id": config["vpc_id"],
                "private_network_verification": "operator-declared",
                "trainer_compatibility_verification": "operator-declared",
            })
        _write_bundle(args.output_dir, artifacts)
        print(json.dumps({"status": status, "artifacts": list(artifacts)}))
        return 0
    except PreparationError as error:
        print(f"Preparation failed: {error}", file=sys.stderr)
    except OSError:
        print("Preparation failed: cannot create fresh local output artifacts", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
