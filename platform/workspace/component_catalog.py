"""The API and worker pin the same real component bytes as the Node compiler."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1] / "react-kit"


def read_catalog(root: Path | None = None) -> dict:
    root = root or KIT_ROOT
    descriptor_path = root / "catalog.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("components"), list):
        raise ValueError("React 컴포넌트 카탈로그 형식이 올바르지 않습니다.")
    paths = [descriptor_path, *(root / "ui").rglob("*")]
    files = []
    for path in paths:
        if path.is_symlink():
            raise ValueError("컴포넌트 코드에 외부 심볼릭 링크를 사용할 수 없습니다.")
        if path.is_file():
            files.append({"path": path.relative_to(root).as_posix(),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if len(files) < 2:
        raise ValueError("실제 React 컴포넌트 코드가 없습니다.")
    files.sort(key=lambda item: item["path"])
    encoded = json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {**descriptor, "hash": hashlib.sha256(encoded).hexdigest(), "files": files}
