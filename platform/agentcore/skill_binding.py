"""Bind custom Harness instructions to approved Registry and bundled file revisions."""
import hashlib
import json
from pathlib import Path

from registry import api
from registry.model import ValidationError

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _snapshot(record):
    fields = ("name", "recordVersion", "recordType", "subtype", "status", "stateRevision", "payload", "description")
    return _hash(json.dumps({key: record.get(key) for key in fields},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def _content(record):
    name = record["name"]
    path = (SKILLS_DIR / (name + ".md")).resolve()
    if (record.get("payload", {}).get("path") != f"skills/{name}.md"
            or not path.is_relative_to(SKILLS_DIR.resolve()) or not path.is_file()):
        raise ValidationError("SKILL requires a reviewed bundled Markdown source")
    raw = path.read_bytes()
    if len(raw) > 60000:
        raise ValidationError("SKILL source exceeds the Harness instruction limit")
    return raw


def capture(names):
    bindings = []
    for name in names:
        records = [r for r in api.get_store().versions(name)
                   if r.get("recordType") == "SKILL" and r.get("status") == "APPROVED"]
        if not records:
            raise ValidationError("SKILL is not approved")
        record = max(records, key=lambda r: int(r["recordVersion"][1:]))
        bindings.append({"name": name, "version": record["recordVersion"],
                         "recordHash": _snapshot(record), "contentHash": _hash(_content(record))})
    return bindings


def resolve(bindings, names):
    if not isinstance(bindings, list) or [row.get("name") for row in bindings if isinstance(row, dict)] != names:
        raise ValidationError("SKILL approval bindings are missing; submit a new agent specification")
    texts = []
    for row in bindings:
        if set(row) != {"name", "version", "recordHash", "contentHash"}:
            raise ValidationError("Invalid SKILL approval binding")
        record = api.get_record(row["name"], row["version"])
        if (not record or record.get("recordType") != "SKILL" or record.get("status") != "APPROVED"
                or _snapshot(record) != row["recordHash"]):
            raise ValidationError("Approved SKILL revision changed; submit a new agent specification")
        raw = _content(record)
        if _hash(raw) != row["contentHash"]:
            raise ValidationError("Approved SKILL source changed; submit a new agent specification")
        texts.append(f"[SKILL {row['name']} {row['version']} sha256:{row['contentHash']}]\n{raw.decode('utf-8')}")
    return "\n\n".join(texts)
