"""Validate compiler archives and derive a private preview from the tested bundle."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile


def files_hash(files: dict[str, bytes]) -> str:
    entries = [{"path": name, "sha256": hashlib.sha256(files[name]).hexdigest()} for name in sorted(files)]
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_archive(data: bytes, expected_hash: str) -> dict[str, bytes]:
    if (not isinstance(data, bytes) or len(data) > 8_000_000 or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
        raise ValueError("React 산출물 크기 또는 해시가 올바르지 않습니다.")
    files, size = {}, 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > 500:
            raise ValueError("React 산출물 파일 개수가 올바르지 않습니다.")
        for entry in entries:
            name = entry.filename
            if (not name or name.startswith("/") or "\\" in name or "\0" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or name in files or entry.is_dir() or entry.flag_bits & 1
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("허용되지 않은 React 산출물 경로입니다.")
            size += entry.file_size
            if size > 12_000_000:
                raise ValueError("React 산출물 해제 크기가 한도를 초과했습니다.")
            files[name] = archive.read(entry)
    if files_hash(files) != expected_hash:
        raise ValueError("승인 대상 React 산출물의 파일 해시가 일치하지 않습니다.")
    return files


def preview_html(files: dict[str, bytes]) -> str:
    """Only compiler-owned index structure is converted; no model rewrites here."""
    try:
        html = files["index.html"].decode("utf-8")
        script = files["assets/app.js"].decode("utf-8")
        style = files["assets/app.css"].decode("utf-8")
    except (KeyError, UnicodeError) as error:
        raise ValueError("React 배포 파일이 불완전합니다.") from error
    html = re.sub(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">', "", html, count=1)
    script = re.sub(r"</script", lambda _: r"<\/script", script, flags=re.I)
    return html.replace('<link rel="stylesheet" href="./assets/app.css">', f"<style>{style}</style>") \
        .replace('<script src="./assets/app.js" defer></script>', f"<script>{script}</script>")


def generated_files(project: dict[str, bytes]) -> dict[str, str]:
    """Extract only model-owned sources; the asset module is rebuilt from snapshots."""
    result = {}
    for name, contents in project.items():
        if name.startswith("src/") and name != "src/logic/assets.ts":
            if not re.fullmatch(r"src/(?:App\.tsx|pages/[a-z][a-z0-9-]*\.tsx|logic/[a-z][a-z0-9-]*\.ts)", name):
                raise ValueError("승인 소스의 파일 경로가 올바르지 않습니다.")
            result[name] = contents.decode("utf-8")
    if "src/App.tsx" not in result:
        raise ValueError("승인된 React 진입 소스가 없습니다.")
    return result


def frozen_assets(project: dict[str, bytes]) -> dict[str, str]:
    raw = project.get("src/logic/assets.ts")
    if raw is None:
        return {}
    match = re.fullmatch(r"export const assets = Object\.freeze\((.*) as const\);\n", raw.decode("utf-8"), re.S)
    if not match:
        raise ValueError("승인 자산 모듈에 실행 코드를 추가할 수 없습니다.")
    assets = json.loads(match.group(1))
    if not isinstance(assets, dict) or len(assets) > 20:
        raise ValueError("승인 자산 목록이 올바르지 않습니다.")
    size = 0
    for identifier, uri in assets.items():
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", identifier)
                or identifier in ("__proto__", "constructor", "prototype") or not isinstance(uri, str)
                or not re.fullmatch(r"data:image/(?:png|jpeg|svg\+xml);base64,[A-Za-z0-9+/=]+", uri)):
            raise ValueError("승인한 로컬 이미지 자산만 재사용할 수 있습니다.")
        size += len(uri.encode())
    if size > 3 * 1024 * 1024:
        raise ValueError("승인 자산 크기가 재빌드 한도를 초과했습니다.")
    return assets


def decode_build_archives(build: dict) -> tuple[bytes, bytes, dict, dict]:
    source = base64.b64decode(build["projectZipBase64"], validate=True)
    dist = base64.b64decode(build["distZipBase64"], validate=True)
    return source, dist, read_archive(source, build["sourceHash"]), read_archive(dist, build["bundleHash"])
