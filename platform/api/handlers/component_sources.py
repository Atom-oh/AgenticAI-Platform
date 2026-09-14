"""Exact Portal reference implementations; independent of Registry approvals."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from hashlib import sha256
from pathlib import Path


def _root() -> Path:
    # Source checkout and assembled Lambda have different parent depths.
    for parent in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
        candidate = parent / "component-library"
        if (candidate / "catalog.json").is_file():
            return candidate
    raise FileNotFoundError("component-library/catalog.json is missing")


def _read(root: Path, name: str) -> bytes:
    path = root / name
    if (not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+", name)
            or ".." in name.split("/") or path.resolve() != path
            or not path.is_file()):
        raise ValueError("Invalid component source path")
    content = path.read_bytes()
    content.decode("utf-8")
    if not content or len(content) > 100_000:
        raise ValueError("Invalid component source size")
    return content


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    """Hash the files shipped with this deployment once, never query a registry."""
    root = _root()
    data = json.loads(_read(root, "catalog.json"))
    if data["schemaVersion"] != 1 or data["package"] != "@atom/portal-components":
        raise ValueError("Unsupported component library")
    result = {}
    for component in data["components"]:
        identity = {key: component[key] for key in ("id", "version")}
        identity["package"] = data["package"]
        identity["exportName"] = component["exportName"]
        files = sorted(set([component["entry"], *data["sharedFiles"]]))
        identity["files"] = [{"path": name, "sha256": sha256(_read(root, name)).hexdigest()} for name in files]
        content = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
        if identity["id"] in result:
            raise ValueError("Duplicate component identity")
        result[identity["id"]] = {
            "kind": "react-component", "id": identity["id"], "name": component["name"],
            "version": identity["version"], "package": identity["package"],
            "exportName": identity["exportName"], "sourceHash": sha256(content).hexdigest(),
        }
    return result


def binding(node_id: str, props: dict) -> dict | None:
    try:
        candidate = catalog().get(node_id)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if candidate and all(props.get(key) == candidate[key] for key in ("name", "version")):
        return dict(candidate)
    return None


def implementation_status(node_id: str, props: dict) -> str:
    if binding(node_id, props):
        return "reference"
    if re.fullmatch(r"CMP-GEN-\d{2}", node_id) and props.get("name") == f"Widget{int(node_id[-2:])}":
        return "placeholder"
    return "unlinked"
