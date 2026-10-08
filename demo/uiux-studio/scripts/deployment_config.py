"""Private, observed deployment records. No resource name is rewritten or guessed."""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile

import boto3

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT.parents[1] / ".local" / "uiux-studio" / "deployment.json"


def config_path(path=None):
    return pathlib.Path(path or os.environ.get("BANK_UIUX_CONFIG") or DEFAULT_PATH).expanduser().resolve()


def validate_config(cfg):
    if not isinstance(cfg, dict) or cfg.get("status") != "observed" or cfg.get("schema_version") != 1:
        raise ValueError("No observed deployment record; run scripts/write_config.py for the actual stack")
    account, region = cfg.get("account", ""), cfg.get("region", "")
    if not re.fullmatch(r"\d{12}", account) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise ValueError("Deployment account and region are required")
    stack_id = cfg.get("stack_id", "")
    if not stack_id.startswith(f"arn:aws:cloudformation:{region}:{account}:stack/{cfg.get('stack_name')}/"):
        raise ValueError("Stack identity does not match the deployment account/region/name")
    for key, value in cfg.items():
        if key.endswith("_arn") and value:
            parts = value.split(":", 5) if isinstance(value, str) else []
            if len(parts) != 6 or parts[0] != "arn" or parts[4] != account or parts[3] not in ("", region):
                raise ValueError(f"Deployment ARN has a different account or region: {key}")
    return cfg



def require_fields(cfg, *fields):
    missing = [name for name in fields if not isinstance(cfg.get(name), str) or not cfg[name].strip()]
    if missing:
        raise ValueError("Deployment record is missing required outputs: " + ", ".join(missing))
    return cfg


def load_config(path=None, *, for_deploy=False):
    path = config_path(path)
    if not path.is_file():
        raise ValueError("No private deployment record; set BANK_UIUX_CONFIG after scripts/write_config.py")
    cfg = validate_config(json.loads(path.read_text(encoding="utf-8")))
    if for_deploy and cfg["stack_name"] != "BankUiuxPlatform":
        raise ValueError("The renamed deployment scripts cannot migrate an existing namespace; use its record for explicit teardown")
    return cfg


def save_config(cfg, path=None):
    validate_config(cfg)
    path = config_path(path)
    repository = ROOT.parents[1].resolve()
    if path.is_relative_to(repository) and not path.is_relative_to(repository / ".local"):
        raise ValueError("Deployment outputs belong outside tracked source or under the ignored .local directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".deployment-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cfg, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def session_for(account, region, profile=None):
    if not isinstance(account, str) or not re.fullmatch(r"\d{12}", account):
        raise ValueError("A target AWS account is required")
    if not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise ValueError("A target AWS region is required")
    selected = profile or os.environ.get("BANK_UIUX_PROFILE") or os.environ.get("AWS_PROFILE")
    if account == "061525506239" and selected != "samples-atomoh":
        raise ValueError("The samples account requires profile samples-atomoh")
    session = boto3.Session(profile_name=selected, region_name=region)
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != account:
        raise ValueError("AWS caller account does not match the deployment record")
    if account == "061525506239" and not identity["Arn"].startswith(
            "arn:aws:sts::061525506239:assumed-role/atomoh/"):
        raise ValueError("The samples account requires the assumed atomoh role")
    return session


def client(cfg, service, **kwargs):
    validate_config(cfg)
    region = kwargs.pop("region_name", cfg["region"])
    if region != cfg["region"]:
        raise ValueError("Client region differs from the deployment record")
    return session_for(cfg["account"], region, os.environ.get("BANK_UIUX_PROFILE") or cfg.get("profile")).client(service, region_name=region, **kwargs)
