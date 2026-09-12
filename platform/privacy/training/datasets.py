"""Bounded JSONL validation; provenance contains no source records or identifiers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import unicodedata

from ..gateway.models import TYPES
from . import CONTRACT_VERSION

# These are local preparation limits, not SageMaker service quotas.
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_RECORDS = 10_000
MAX_TEXT_BYTES = 8192
MAX_ENTITIES = 64
MAX_ORIGINAL_CHARS = 512
MAX_CONFIG_BYTES = 32 * 1024


class PreparationError(ValueError):
    """A fixed diagnostic that never includes record contents or filesystem paths."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreparationError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise PreparationError("non-finite JSON value")


def parse_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise PreparationError("invalid JSON") from None


def read_config(path: Path) -> dict:
    try:
        if not stat.S_ISREG(Path(path).stat().st_mode):
            raise PreparationError("config must be a regular file")
        with Path(path).open("rb") as source:
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise PreparationError("config byte limit exceeded")
        result = parse_json(raw)
        if not isinstance(result, dict):
            raise PreparationError("config must be an object")
        return result
    except OSError:
        raise PreparationError("cannot read config") from None


def _validate_row(row: object) -> tuple[str, str, list]:
    if not isinstance(row, dict) or set(row) != {"id", "text", "entities"}:
        raise PreparationError("expected exactly id, text, entities")
    identifier, text, entities = row["id"], row["text"], row["entities"]
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", identifier):
        raise PreparationError("invalid opaque id")
    if not isinstance(text, str) or not text.strip():
        raise PreparationError("text must be a nonempty string")
    try:
        text_bytes = text.encode("utf-8")
    except UnicodeError:
        raise PreparationError("text must be valid UTF-8") from None
    if len(text_bytes) > MAX_TEXT_BYTES:
        raise PreparationError("text byte limit exceeded")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise PreparationError("text contains unsupported control characters")
    if not isinstance(entities, list):
        raise PreparationError("entities must be an array")
    if len(entities) > MAX_ENTITIES:
        raise PreparationError("entity count limit exceeded")
    originals = set()
    for entity in entities:
        if not isinstance(entity, dict) or set(entity) != {"type", "original"}:
            raise PreparationError("invalid entity fields")
        kind, original = entity["type"], entity["original"]
        if not isinstance(kind, str) or kind not in TYPES:
            raise PreparationError("invalid entity type")
        if not isinstance(original, str) or not original or original.strip() != original:
            raise PreparationError("invalid entity original")
        if len(original) > MAX_ORIGINAL_CHARS:
            raise PreparationError("entity original length limit exceeded")
        if original not in text:
            raise PreparationError("entity original does not match text")
        if original in originals:
            raise PreparationError("duplicate or conflicting entity original")
        originals.add(original)
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return identifier.casefold(), content_hash, entities


def _scan_split(path: Path, split: str, ids: set, contents: set) -> dict:
    digest = hashlib.sha256()
    counts = {}
    records = total_bytes = total_entities = 0
    try:
        if not stat.S_ISREG(Path(path).stat().st_mode):
            raise PreparationError(f"{split}: input must be a regular file")
        with Path(path).open("rb") as source:
            while True:
                raw = source.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                records += 1
                total_bytes += len(raw)
                if len(raw) > MAX_LINE_BYTES or total_bytes > MAX_FILE_BYTES or records > MAX_RECORDS:
                    raise PreparationError(f"{split}: file, line or record limit exceeded")
                digest.update(raw)
                try:
                    identifier, content_hash, entities = _validate_row(parse_json(raw))
                    if identifier in ids:
                        raise PreparationError("duplicate id within or across splits")
                    if content_hash in contents:
                        raise PreparationError("duplicate content within or across splits")
                except PreparationError as error:
                    raise PreparationError(f"{split} line {records}: {error}") from None
                ids.add(identifier)
                contents.add(content_hash)
                total_entities += len(entities)
                for entity in entities:
                    kind = entity["type"]
                    counts[kind] = counts.get(kind, 0) + 1
    except OSError:
        raise PreparationError(f"{split}: cannot read input") from None
    if not records:
        raise PreparationError(f"{split}: empty split")
    return {"sha256": digest.hexdigest(), "bytes": total_bytes, "records": records,
            "entities": total_entities, "entity_counts": counts}


def validate_dataset(train: Path, evaluation: Path, *, data_policy: str, approval_reference: str = "") -> dict:
    """Validate both local splits every time; classification is an operator assertion."""
    if data_policy not in ("synthetic", "approved"):
        raise PreparationError("data policy must be synthetic or approved")
    policy = {"classification": data_policy, "verification": "operator-declared"}
    if data_policy == "approved":
        if (not isinstance(approval_reference, str) or not approval_reference.strip()
                or len(approval_reference) > 256):
            raise PreparationError("approved data requires a bounded approval reference")
        try:
            policy["approval_reference_sha256"] = hashlib.sha256(approval_reference.encode("utf-8")).hexdigest()
        except UnicodeError:
            raise PreparationError("invalid approval reference") from None
    elif approval_reference:
        raise PreparationError("approval reference is only valid for approved data")
    ids, contents = set(), set()
    return {
        "schema_version": 1,
        "dataset_format": CONTRACT_VERSION,
        "status": "validated-offline",
        "data_policy": policy,
        "splits": {
            "train": _scan_split(train, "train", ids, contents),
            "eval": _scan_split(evaluation, "eval", ids, contents),
        },
    }
