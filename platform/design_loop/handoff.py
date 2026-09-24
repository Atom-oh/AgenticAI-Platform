"""Deterministic convention-layout handoff ZIP from the approved release source (engine plan, Task E17; R-01, R-03;
Codex #14, #15; review round 4, X7).

The ZIP holds the complete approved release project **unchanged** under `project/` (every entry of the release
`source.zip`, byte-identical), so it builds and tests on its own. Nothing is relocated: the react-kit source policy
allows only `src/pages/*` and relocation would break relative imports. The customer screen-id convention travels as
data (`convention/screens.json`) plus the `convention/apply.cjs` script the customer runs after approval (roadmap
D-6). The same meta is compiled into every page as `export const meta = {...} as const;` (E12), and `build` refuses
a page whose compiled meta disagrees with the layout.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from .convention import META_KEYS

RESOURCES = Path(__file__).resolve().parent / "resources"
APPLY = "convention-apply.cjs"
MAX_ENTRIES, MAX_BYTES = 512, 40 * 1024 * 1024
MANIFEST_FIELDS = ("approvalHash", "releaseId", "sourceHash", "bundleHash")
_PAGE = re.compile(r"src/pages/[A-Za-z0-9_-]+\.tsx\Z")
_TARGET = re.compile(r"[A-Za-z0-9_./-]+\.tsx\Z")
_META = re.compile(r"^export const meta = \{ (?P<body>.*) \} as const;$", re.M)
_PAIR = re.compile(r'(?P<key>[a-z0-9]+): (?P<value>"(?:[^"\\]|\\.)*")(?:, |$)')
_DATE = (1980, 1, 1, 0, 0, 0)


def _safe(name):
    return (isinstance(name, str) and name and not name.startswith("/") and "\\" not in name
            and all(part not in ("", ".", "..") for part in name.split("/")))


def read_zip(data):
    """Release source entries `{name: bytes}`; unsafe, duplicate or directory entries are refused."""
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValueError("release-entry-limit")
        for info in infos:
            if info.is_dir() or not _safe(info.filename) or info.filename in out:
                raise ValueError("unsafe-release-entry")
            out[info.filename] = archive.read(info)
    return out


def source_hash(files):
    """`react-kit/project.cjs` hashFiles: sha256 of the canonical `[{path, sha256}]` list sorted by path."""
    listing = [{"path": name, "sha256": hashlib.sha256(files[name]).hexdigest()} for name in sorted(files)]
    return hashlib.sha256(json.dumps(listing, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def page_meta(source):
    """The compiled `export const meta = {...} as const;` of one page source."""
    matches = _META.findall(source.decode("utf-8"))
    if len(matches) != 1:
        raise ValueError("page-meta-missing")
    body, pairs, position = matches[0], {}, 0
    for m in _PAIR.finditer(body):
        if m.start() != position:
            raise ValueError("page-meta-shape")
        pairs[m.group("key")] = json.loads(m.group("value"))
        position = m.end()
    if position != len(body) or list(pairs) != list(META_KEYS):
        raise ValueError("page-meta-shape")
    return pairs


def apply_script():
    """The packaged `convention/apply.cjs`, checked against the resource manifest."""
    data = (RESOURCES / APPLY).read_bytes()
    manifest = json.loads((RESOURCES / "manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(data).hexdigest() != manifest["resources"][APPLY]["sha256"]:
        raise ValueError("packaged resource hash mismatch")
    return data


def _entry(name, data):
    info = zipfile.ZipInfo(name, date_time=_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    return info, data


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(entries):
            info, data = _entry(name, entries[name])
            archive.writestr(info, data, compresslevel=9)
    return buffer.getvalue()


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build(release_source_zip, layout, *, manifest_fields, changes=None, verification_report=None):
    """-> `(zip bytes, sha256)`. `layout` comes from `convention.layout`."""
    fields = dict(manifest_fields or {})
    if set(MANIFEST_FIELDS) - set(fields) or any(not isinstance(fields[k], str) or not fields[k] for k in MANIFEST_FIELDS):
        raise ValueError("manifest-fields")
    files = read_zip(release_source_zip)
    if source_hash(files) != fields["sourceHash"]:
        raise ValueError("source-hash-mismatch")
    screens, targets = {}, set()
    for item in layout:
        source, target = item.get("source", ""), item.get("target")
        relative = source[len("project/"):] if source.startswith("project/") else None
        if relative is None or not _PAGE.fullmatch(relative) or relative not in files:
            raise ValueError("layout-source-missing")
        if not isinstance(target, str) or not _TARGET.fullmatch(target) or not _safe(target):
            raise ValueError("convention path shape")
        if target in targets or source in screens:
            raise ValueError("duplicate-handoff-path")
        targets.add(target)
        meta = page_meta(files[relative])
        if meta["sid"] != item.get("sid"):
            raise ValueError("page-meta-mismatch")
        screens[source] = {"sid": meta["sid"], "type": meta["type"], "dver": meta["dver"], "status": meta["status"],
                           "levels": [meta["level1"], meta["level2"], meta["level3"]], "conventionPath": target,
                           "state": item.get("state"), "sha256": hashlib.sha256(files[relative]).hexdigest()}
    unmapped = sorted(f"project/{n}" for n in files if _PAGE.fullmatch(n))
    if set(unmapped) - set(screens):
        raise ValueError("unmapped-page")
    entries = {f"project/{name}": data for name, data in files.items()}
    entries["convention/screens.json"] = _json({"schemaVersion": 1, "entries": screens})
    entries["convention/apply.cjs"] = apply_script()
    entries["manifest.json"] = _json({
        "schemaVersion": 1, **{k: fields[k] for k in MANIFEST_FIELDS},
        "sourceZipSha256": hashlib.sha256(release_source_zip).hexdigest(),
        "componentPackage": "@studio/approved-ui", "customerPackage": "not-verified"})
    entries["CHANGES.md"] = (changes if changes is not None else
                             "# 변경 내역\n\n승인된 릴리스 소스를 그대로 포함합니다. 고객 경로 규칙은 convention/apply.cjs로 적용합니다.\n"
                             ).encode("utf-8")
    entries["verification-report.json"] = _json(verification_report if verification_report is not None else {})
    if len(entries) > MAX_ENTRIES:
        raise ValueError("handoff-entry-limit")
    if sum(len(v) for v in entries.values()) > MAX_BYTES:
        raise ValueError("handoff-size-limit")
    data = _zip(entries)
    if len(data) > MAX_BYTES:
        raise ValueError("handoff-size-limit")
    return data, hashlib.sha256(data).hexdigest()
