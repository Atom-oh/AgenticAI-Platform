"""Code-collection admission (I6a; review rounds 3/4/8/22/23/24/32: F12, AB2, AP2, AQ1, AR2, AZ3).

The source is an `asset` import revision holding a ZIP of a repository subset.
The resolver is never caller-supplied: it comes from an IAM-administered
`adm_resolver` profile whose packages are verified against the platform package
registry. The decision freezes the profile id/revision/hash and the derived
resolver hash; a resolver change is a new decision.

Identifier normalization is applied consistently to file text, to path segments
and to package scopes, with one neutral ASCII alias per matched deny-list term,
so imports still resolve. The original-to-derivative path/package mapping stays
in a private decision blob and is never transferred.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from pathlib import PurePosixPath

from intake import admission, derivative, inspect, records
from intake.admission import AdmissionError
from intake.records import INTAKE_OWNER
from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources

MAX_FILES = 100
MAX_FILE_BYTES = 102400
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 3_900_000
MAX_ZIP_BYTES = 8 * 1024 * 1024
# Aggregate decompressed size of all selected members, assets included.
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_PATH_CHARS = 500
# Mirrors workspace/ontology_analysis.py KINDS (the ingestion adapter's formats).
KINDS = {".ts": "code", ".tsx": "code", ".js": "code", ".jsx": "code",
         ".css": "style", ".scss": "style", ".json": "json",
         ".html": "html", ".htm": "html",
         ".png": "asset", ".jpg": "asset", ".jpeg": "asset", ".svg": "asset",
         ".woff": "asset", ".woff2": "asset"}


def _path_ok(path):
    """The analyzer `safePath` rules plus the ingestion adapter's stricter checks
    (`ontology_analysis.source_input`), except the length limit reported separately."""
    return (isinstance(path, str) and path and len(path.encode()) <= 1024
            and path == unicodedata.normalize("NFC", path) and not path.startswith("/")
            and not any(c in "\\:?#" or ord(c) < 32 or ord(c) == 127 for c in path)
            and not any(part in {"", ".", ".."} for part in path.split("/")))


def _check_paths(paths):
    folded = set()
    for path in paths:
        if not _path_ok(path):
            raise AdmissionError("path-invalid", 422)
        if len(path) > MAX_PATH_CHARS:
            raise AdmissionError("path-too-long", 422)
        if path.lower() in folded:
            raise AdmissionError("path-collision", 422)
        folded.add(path.lower())


def package_registry(host):
    """Platform package identities: the react-kit catalog plus private registered packages."""
    from workspace.component_catalog import read_catalog
    catalog = read_catalog()
    registry = {catalog["id"]: {"version": catalog["version"], "sha256": catalog["hash"]}}
    for name, spec in (getattr(host, "intake_package_registry", None) or {}).items():
        registry[name] = {"version": spec["version"], "sha256": spec["sha256"]}
    return registry


def resolver_profile(host, profile_id):
    storage = host.storage
    try:
        profile = storage.get(INTAKE_OWNER, "adm_resolver", profile_id)
        profile = records.validate("adm_resolver", profile) if profile else None
    except ValueError:
        profile = None
    if not profile or not records.is_current(profile, storage.clock()):
        raise AdmissionError("resolver-unavailable")
    registry = package_registry(host)
    for name, spec in profile["packages"].items():
        if registry.get(name) != spec:
            raise AdmissionError("resolver-package-unverified")
    return profile


def _read_member(archive, info, limit):
    """Decompress one member, never beyond its declared size (or the remaining budget)."""
    bound = min(info.file_size, limit)
    try:
        with archive.open(info) as member:
            raw = member.read(bound + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError, OSError,
            EOFError):
        raise AdmissionError("collection-format", 422) from None
    if len(raw) > bound:
        raise AdmissionError("collection-too-large" if len(raw) > limit else "collection-format", 422)
    if len(raw) != info.file_size:
        raise AdmissionError("collection-format", 422)
    return raw


def _read_zip(data, root):
    """Select, preflight and then read the members of a code-collection ZIP.

    Every limit (member count, per-file size, text total and the aggregate
    expanded size including assets) is checked from the central directory before
    any member is decompressed, and again while reading, where each read is
    bounded by the member's declared size and the remaining aggregate budget.
    """
    if root and (not _path_ok(root.rstrip("/")) or not root.endswith("/")):
        raise AdmissionError("collection-root-invalid", 400)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise AdmissionError("collection-format", 422) from None
    with archive:
        infos = archive.infolist()
        if len(infos) > 5000:
            raise AdmissionError("collection-too-large", 422)
        selected, declared_text, declared_all = [], 0, 0
        for info in infos:
            if info.is_dir():
                continue
            if info.flag_bits & 0x1 or stat.S_ISLNK(info.external_attr >> 16):
                raise AdmissionError("collection-format", 422)
            name = info.filename
            if not name.startswith(root):
                continue
            path = name[len(root):]
            kind = KINDS.get(PurePosixPath(path).suffix.lower())
            if kind is None:
                continue  # not analyzable, never transferred
            if info.file_size > MAX_FILE_BYTES and kind != "asset" or info.file_size > MAX_EXPANDED_BYTES:
                raise AdmissionError("collection-too-large", 422)
            selected.append((info, path, kind))
            declared_all += info.file_size
            if kind != "asset":
                declared_text += info.file_size
            if (len(selected) > MAX_FILES or declared_text > MAX_TOTAL_BYTES
                    or declared_all > MAX_EXPANDED_BYTES):
                raise AdmissionError("collection-too-large", 422)
        if not selected:
            raise AdmissionError("collection-empty", 422)
        entries, total, expanded = [], 0, 0
        for info, path, kind in selected:
            raw = _read_member(archive, info, MAX_EXPANDED_BYTES - expanded)
            expanded += len(raw)
            entry = {"path": path, "kind": kind, "bytes": raw}
            if kind != "asset":
                try:
                    entry["text"] = raw.decode("utf-8")
                except UnicodeError:
                    raise AdmissionError("collection-format", 422) from None
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise AdmissionError("collection-too-large", 422)
            entries.append(entry)
    return sorted(entries, key=lambda e: e["path"].encode())


def _collection_terms(entries, profile, denylist):
    """One neutral ASCII alias per deny-list term matched anywhere in the collection."""
    matched = {}
    haystack = [e["path"] for e in entries] + [e["text"] for e in entries if "text" in e]
    haystack += list(profile["aliases"]) + list(profile["aliases"].values()) + list(profile["packages"])
    for text in haystack:
        for _, _, entry in inspect.find_terms(text, denylist):
            matched[inspect.fold(entry["term"])] = entry
    # `neutral_<n>` stays a valid JS identifier fragment, package-name segment and path segment.
    return [{"term": entry["term"], "alias": f"neutral_{index}"}
            for index, (_, entry) in enumerate(sorted(matched.items()), 1)]


def _rename(value, terms, denylist):
    """Apply the collection mapping; internal URLs in text become the link alias."""
    value = unicodedata.normalize("NFC", value)
    value, _ = derivative._replace_links(value, denylist)
    value, _ = derivative._replace_terms(value, terms)
    return value


def derive_resolver(profile, terms, denylist):
    return {"aliases": {_rename(alias, terms, denylist): _rename(target, terms, denylist)
                        for alias, target in profile["aliases"].items()},
            "packages": {_rename(name, terms, denylist): dict(spec) for name, spec in profile["packages"].items()},
            "jsonAssetFields": list(profile["jsonAssetFields"])}


def analyzer_payload(files, resolver):
    """Exactly the request the `source.analyze` context stage builds (`analyze.cjs:30-40`)."""
    return {"schemaVersion": 1,
            "files": [{key: file[key] for key in ("path", "kind", "sha256", "text") if key in file} for file in files],
            "resolver": resolver}


def request_size(payload):
    # The same bytes the Interpreter adapter writes (json.dumps(..., ensure_ascii=False)).
    return len(json.dumps(payload, ensure_ascii=False).encode())


def request_collection(host, scope, source_ref, *, root, resolver_profile_id, data_class="internal-non-sensitive",
                       claims=None, **unexpected):
    if unexpected:
        # A caller-supplied `resolver` (or any other override) is never accepted.
        raise AdmissionError("resolver-not-accepted" if "resolver" in unexpected else "invalid-request", 400)
    if not isinstance(root, str) or len(root) > 400:
        raise AdmissionError("collection-root-invalid", 400)
    storage, project_id = host.storage, admission._project(scope)
    if data_class not in records.DATA_CLASSES:
        return admission._blocked(["data-class-ineligible"])
    try:
        policy = admission.current_policy(storage, project_id)
    except AdmissionError as error:
        return admission._blocked([error.code])
    try:
        records._identifier(resolver_profile_id)
    except ValueError:
        raise AdmissionError("resolver-unavailable", 400) from None
    profile = resolver_profile(host, resolver_profile_id)
    try:
        denylist = derivative.canonical_entries(admission._denylist(host))
    except derivative.DenylistUnavailable:
        return admission._blocked(["denylist-unavailable"])
    reader = Sources(inspect.context(host, scope, claims))
    try:
        ref = schema.source_ref(source_ref)
    except ValueError:
        fail(400, "invalid-source", "원본 참조 형식이 올바르지 않습니다.")
    if ref["sourceKind"] != "asset" or "location" in ref:
        fail(422, "intake-source-unsupported", "코드 묶음은 반입된 자산 리비전이어야 합니다.")
    resolved = reader.resolve(ref)
    asset = resolved["record"]
    if PurePosixPath(asset.get("name", "")).suffix.lower() != ".zip" or asset["size"] > MAX_ZIP_BYTES:
        fail(422, "intake-source-unsupported", "코드 묶음은 8 MiB 이하 ZIP 자산이어야 합니다.")
    data = reader._blob(asset["originalKey"], ref["sha256"], MAX_ZIP_BYTES)
    entries = _read_zip(data, root)
    _check_paths([e["path"] for e in entries])  # request time, before any normalization
    terms = _collection_terms(entries, profile, denylist)
    files, mapping, pii_counts, identifiers, total = [], {}, {}, 0, 0
    for entry in entries:
        path = _rename(entry["path"], terms, denylist)
        mapping[entry["path"]] = path
        item = {"path": path, "kind": entry["kind"]}
        if "text" in entry:
            text = _rename(entry["text"], terms, denylist)
            encoded = text.encode()
            if len(encoded) > MAX_FILE_BYTES:
                raise AdmissionError("collection-too-large", 422)
            total += len(encoded)
            item.update(text=text, sha256=hashlib.sha256(encoded).hexdigest(), size=len(encoded))
        else:
            item.update(sha256=hashlib.sha256(entry["bytes"]).hexdigest(), size=len(entry["bytes"]))
        files.append(item)
    if total > MAX_TOTAL_BYTES:
        raise AdmissionError("collection-too-large", 422)
    _check_paths([f["path"] for f in files])  # again after normalization (review round 24, AR2)
    resolver = derive_resolver(profile, terms, denylist)
    residual_text = [f["path"] for f in files] + [f["text"] for f in files if "text" in f] + [
        json.dumps(resolver, ensure_ascii=False)]
    report = derivative.residual([{"page": i + 1, "text": t} for i, t in enumerate(residual_text)], denylist,
                                 contiguous=False)  # separate files, not one continuous text
    identifiers, pii_counts = report["identifiers"], report["pii"]
    payload = analyzer_payload(files, resolver)
    size = request_size(payload)
    if size > MAX_REQUEST_BYTES:
        error = AdmissionError("collection-too-large", 422)
        error.measured = size
        raise error
    index = {"files": [{key: f[key] for key in ("path", "kind", "sha256", "size")} for f in files],
             "resolver": resolver}
    contents = {"files": [{"path": f["path"], "text": f["text"]} for f in files if "text" in f]}
    private_mapping = {"paths": mapping, "packages": {name: _rename(name, terms, denylist)
                                                      for name in profile["packages"]}}
    original_pages = [{"page": i + 1, "text": e["path"] + "\n" + e.get("text", "")} for i, e in enumerate(entries)]
    receipt = inspect.inspect(original_pages, denylist=denylist, contiguous=False)
    blocking = (["residual-identifiers"] if identifiers else []) + (["redaction-required"] if pii_counts else [])
    index_bytes = schema.canonical(index)
    resolver_binding = {"profile": {"id": profile["id"], "revision": profile["revision"], "hash": profile["hash"]},
                        "derivativeHash": schema.digest(resolver)}
    source = {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision")}
    return admission.decide(
        host, scope, reader=reader, source=source, data_class=data_class, policy=policy,
        artifact={"kind": "code-collection", "files": len(files), "resolver": resolver_binding,
                  "suffix": "collection-index.json"},
        derivation={"profile": derivative.PROFILE, "originalHash": hashlib.sha256(data).hexdigest(),
                    "derivativeHash": hashlib.sha256(index_bytes).hexdigest()},
        receipt=receipt, receipt_bytes=schema.canonical(receipt), payload=index_bytes, blocking=blocking,
        identity=[root, resolver_binding],
        extra_checks=[admission._check(INTAKE_OWNER, "adm_resolver", profile)],
        extra_blobs={"collection-files.json": schema.canonical(contents),
                     "collection-mapping.json": schema.canonical(private_mapping)})


def _private(host, scope, decision, suffix):
    storage = host.storage
    key = storage.key_for(scope["owner"], "adm_decision", decision["id"], suffix)
    try:
        return json.loads(storage.get_blob(key, length=admission.MAX_RESPONSE_BYTES * 16))
    except (FileNotFoundError, ValueError):
        raise AdmissionError("artifact-changed") from None


def check_resolver(host, scope, decision, authority=None):
    """`source.analyze` recheck: the profile is current and unchanged, and re-applying
    the private mapping to it yields exactly the derivative resolver."""
    binding = decision["artifact"].get("resolver")
    if decision["artifact"]["kind"] != "code-collection" or not binding:
        raise AdmissionError("artifact-kind-unsupported", 422)
    try:
        profile = resolver_profile(host, binding["profile"]["id"])
    except AdmissionError:
        raise AdmissionError("resolver-changed") from None
    if profile["revision"] != binding["profile"]["revision"] or profile["hash"] != binding["profile"]["hash"]:
        raise AdmissionError("resolver-changed")
    if authority is not None:
        authority.observe(INTAKE_OWNER, "adm_resolver", profile)
    index = _private(host, scope, decision, "collection-index.json")
    mapping = _private(host, scope, decision, "collection-mapping.json")["packages"]
    derived = index["resolver"]
    if schema.digest(derived) != binding["derivativeHash"] or set(mapping) != set(profile["packages"]):
        raise AdmissionError("resolver-changed")
    reverse = {}
    for authoritative, renamed in mapping.items():
        if renamed in reverse:
            raise AdmissionError("resolver-changed")
        reverse[renamed] = authoritative
    if set(derived["packages"]) != set(reverse):
        raise AdmissionError("resolver-changed")
    for renamed, spec in derived["packages"].items():
        if profile["packages"][reverse[renamed]] != spec:
            raise AdmissionError("resolver-changed")
    if derived["jsonAssetFields"] != profile["jsonAssetFields"] or len(derived["aliases"]) != len(profile["aliases"]):
        raise AdmissionError("resolver-changed")
    return index


def analyzer_request(host, scope, decision_id, *, claims=None):
    """Build the analyzer request from the verified private objects (derivative only)."""
    decision, _, authority = admission.authorized(host, scope, decision_id, claims=claims)
    index = check_resolver(host, scope, decision, authority)
    contents = {f["path"]: f["text"] for f in _private(host, scope, decision, "collection-files.json")["files"]}
    files = []
    for entry in index["files"]:
        file = {"path": entry["path"], "kind": entry["kind"], "sha256": entry["sha256"]}
        if entry["kind"] != "asset":
            text = contents.get(entry["path"])
            if text is None or hashlib.sha256(text.encode()).hexdigest() != entry["sha256"]:
                raise AdmissionError("artifact-changed")
            file["text"] = text
        files.append(file)
    payload = analyzer_payload(files, index["resolver"])
    if request_size(payload) > MAX_REQUEST_BYTES:
        raise AdmissionError("collection-too-large", 422)
    # Sources, decision, policy, grant/provenance and resolver profile, after the last read.
    authority.recheck()
    return payload
