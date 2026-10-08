"""Code-collection and prompt-text admission (Task I6a; review round 3, F12 and F2)."""
from __future__ import annotations

import hashlib
import io
import json
import secrets
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_intake_admission import DAY, env, guide  # noqa: F401,E402
from intake_support import api  # noqa: F401,E402
from intake import admission, collection, prompts, records, review  # noqa: E402
from intake.admission import AdmissionError  # noqa: E402
from intake.records import INTAKE_OWNER  # noqa: E402
from workspace.ontology_analysis import local_analyze  # noqa: E402
from workspace.ontology_sources import asset_reference  # noqa: E402

PACKAGE_HASH = hashlib.sha256(b"synthetic registered package").hexdigest()


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def asset(env, files, identifier=None):
    identifier = identifier or "repo-" + secrets.token_hex(4)
    data = zipped(files)
    owner = f"project:{env.pid}"
    key = env.api.storage.key_for(owner, "asset", identifier, "original.zip")
    env.api.storage.put_blob_once(key, data, "application/zip")
    row = env.api.storage.put(owner, "asset", {
        "id": identifier, "projectId": env.pid, "name": "repo.zip", "uploadStatus": "stored",
        "parseStatus": "complete", "originalKey": key, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "importRevision": 1})
    return asset_reference(row)


def profile(env, *, aliases=None, packages=None, expected=None, identifier="resolver-1"):
    event = {"op": "put_resolver_profile", "record": {
        "id": identifier, "aliases": aliases or {}, "packages": packages or {}, "jsonAssetFields": [],
        "expiresAt": env.api.storage.clock() + 30 * DAY}}
    if expected is not None:
        event["expectedRevision"] = expected
    return env.admin(event)


def admitted_collection(env, files, *, aliases=None, packages=None, root="", setup=True):
    if setup:
        env.policy()
        env.grant("bob")
        profile(env, aliases=aliases, packages=packages)
    ref = asset(env, files)
    pending = collection.request_collection(env.api, env.scope(), ref, root=root, resolver_profile_id="resolver-1")
    assert pending["status"] == "pending-review", pending
    decision = review.decide(env.api, env.scope("bob"), pending["id"], approve=True, reason="checked")
    assert decision["status"] == "admitted"
    assert records.validate("adm_decision", decision)
    return decision, ref


def references(analysis, path):
    return [r for r in analysis["references"] if r["path"] == path]


def test_same_basenames_keep_distinct_paths_and_relative_imports_resolve(env):
    decision, _ = admitted_collection(env, {
        "src/a/index.ts": 'import { b } from "../b/index";\nexport const a = b + 1;\n',
        "src/b/index.ts": "export const b = 1;\n"})
    payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert [f["path"] for f in payload["files"]] == ["src/a/index.ts", "src/b/index.ts"]
    assert set(payload) == {"schemaVersion", "files", "resolver"}
    assert all(set(f) == {"path", "kind", "sha256", "text"} for f in payload["files"])
    analysis = local_analyze(payload)["analysis"]
    [ref] = [r for r in references(analysis, "src/a/index.ts") if r["specifier"] == "../b/index"]
    assert ref["resolution"]["status"] == "resolved-local"
    assert ref["resolution"]["targetPath"] == "src/b/index.ts"


def test_alias_resolves_through_a_directory_renamed_by_normalization(env):
    term = env.term.lower()
    decision, _ = admitted_collection(env, {
        f"src/{term}-parts/Header.tsx": "export function Header() { return null; }\n",
        "src/App.tsx": 'import { Header } from "@parts/Header";\nexport const App = () => <Header />;\n'},
        aliases={"@parts/*": f"src/{term}-parts/*"})
    payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert term not in serialized
    assert payload["resolver"]["aliases"] == {"@parts/*": "src/neutral_1-parts/*"}
    analysis = local_analyze(payload)["analysis"]
    refs = [r for r in references(analysis, "src/App.tsx") if r["specifier"] == "@parts/Header"]
    assert {r["kind"] for r in refs} >= {"import", "jsx-use"}
    for ref in refs:
        assert ref["resolution"]["status"] == "resolved-local"
        assert ref["resolution"]["targetPath"] == "src/neutral_1-parts/Header.tsx"


def test_deny_listed_package_scope_is_renamed_consistently_and_still_resolves(env):
    term = env.term.lower()
    package = f"@{term}/ui"
    env.api.intake_package_registry = {package: {"version": "1.0.0", "sha256": PACKAGE_HASH}}
    decision, _ = admitted_collection(env, {
        "src/App.tsx": f'import {{ Button }} from "{package}";\nexport const App = () => <Button />;\n',
        "package.json": json.dumps({"name": "synthetic-app", "dependencies": {package: "1.0.0"}})},
        packages={package: {"version": "1.0.0", "sha256": PACKAGE_HASH}})
    payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert term not in json.dumps(payload, ensure_ascii=False).lower()
    assert payload["resolver"]["packages"] == {"@neutral_1/ui": {"version": "1.0.0", "sha256": PACKAGE_HASH}}
    manifest = json.loads(next(f["text"] for f in payload["files"] if f["path"] == "package.json"))
    assert manifest["dependencies"] == {"@neutral_1/ui": "1.0.0"}
    analysis = local_analyze(payload)["analysis"]
    refs = [r for r in references(analysis, "src/App.tsx") if r["specifier"] == "@neutral_1/ui"]
    assert {r["kind"] for r in refs} >= {"import", "jsx-use"}
    for ref in refs:
        assert ref["resolution"]["status"] == "approved-package"
        assert ref["resolution"]["sha256"] == PACKAGE_HASH and ref["resolution"]["version"] == "1.0.0"
    # The private mapping maps each derivative key back to the authoritative profile key.
    owner = f"project:{env.pid}"
    key = env.api.storage.key_for(owner, "adm_decision", decision["id"], "collection-mapping.json")
    mapping = json.loads(env.api.storage.get_blob(key))
    assert mapping["packages"] == {package: "@neutral_1/ui"}
    env.api.storage.put_blob(key, json.dumps({**mapping, "packages": {package: "@neutral_2/ui"}}).encode(),
                             "application/json")
    with pytest.raises(AdmissionError) as error:
        collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert error.value.code == "resolver-changed"


def test_unregistered_or_forged_package_in_a_profile_is_rejected(env):
    env.policy()
    profile(env, packages={"@synthetic/kit": {"version": "1.0.0", "sha256": "a" * 64}})
    ref = asset(env, {"src/a.ts": "export const a = 1;\n"})
    with pytest.raises(AdmissionError) as error:
        collection.request_collection(env.api, env.scope(), ref, root="", resolver_profile_id="resolver-1")
    assert error.value.code == "resolver-package-unverified"


def test_caller_supplied_resolver_is_rejected(env):
    env.policy()
    profile(env)
    ref = asset(env, {"src/a.ts": "export const a = 1;\n"})
    with pytest.raises(AdmissionError) as error:
        collection.request_collection(env.api, env.scope(), ref, root="", resolver_profile_id="resolver-1",
                                      resolver={"aliases": {}, "packages": {}, "jsonAssetFields": []})
    assert error.value.code == "resolver-not-accepted"


def test_resolver_change_is_a_new_decision_and_invalidates_the_old_one(env):
    files = {"src/a.ts": "export const a = 1;\n"}
    first, ref = admitted_collection(env, files)
    changed = profile(env, aliases={"@lib/*": "src/*"}, expected=1)
    assert changed["revision"] == 2
    second = collection.request_collection(env.api, env.scope(), ref, root="", resolver_profile_id="resolver-1")
    assert second["id"] != first["id"] and second["status"] == "pending-review"
    assert second["artifact"]["resolver"]["profile"]["revision"] == 2
    with pytest.raises(AdmissionError) as error:
        collection.analyzer_request(env.api, env.scope(), first["id"])
    assert error.value.code == "resolver-changed"


@pytest.mark.parametrize("length,accepted", [(500, True), (501, False)])
def test_path_length_limit_is_enforced_at_admission(env, length, accepted):
    path = "src/" + "a" * (length - 7) + ".ts"
    assert len(path) == length
    files = {path: "export const a = 1;\n"}
    if accepted:
        decision, _ = admitted_collection(env, files)
        payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
        assert local_analyze(payload)["analysis"]["coverage"]["files"] == 1
    else:
        env.policy()
        profile(env)
        with pytest.raises(AdmissionError) as error:
            collection.request_collection(env.api, env.scope(), asset(env, files), root="",
                                          resolver_profile_id="resolver-1")
        assert error.value.code == "path-too-long"
        assert env.api.storage.list(f"project:{env.pid}", "adm_decision") == []


def backslash_files(count, size=86_400):
    body = "\\" * (size - len('export const s00 = "";\n'))
    return {f"src/f{i:02d}.ts": f'export const s{i:02d} = "{body}";\n' for i in range(count)}


def test_backslash_heavy_collection_over_the_serialized_envelope_is_rejected(env):
    env.policy()
    profile(env)
    files = backslash_files(24)
    assert sum(len(t.encode()) for t in files.values()) == 2_073_600
    with pytest.raises(AdmissionError) as error:
        collection.request_collection(env.api, env.scope(), asset(env, files), root="",
                                      resolver_profile_id="resolver-1")
    assert error.value.code == "collection-too-large" and error.value.measured > 3_900_000


def test_at_limit_escaped_collection_passes_the_real_analyzer_cli(env):
    decision, _ = admitted_collection(env, backslash_files(22))
    payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert 3_700_000 < collection.request_size(payload) <= 3_900_000
    assert local_analyze(payload)["analysis"]["coverage"]["files"] == 22


def test_root_selects_the_repository_subset(env):
    decision, _ = admitted_collection(env, {"repo/src/a.ts": "export const a = 1;\n",
                                            "other/b.ts": "export const b = 1;\n",
                                            "repo/README.md": "not analyzable"}, root="repo/")
    payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert [f["path"] for f in payload["files"]] == ["src/a.ts"]


def test_collection_with_a_phone_number_is_blocked(env):
    env.policy()
    profile(env)
    ref = asset(env, {"src/a.ts": 'export const contact = "010-5550-7391";\n'})
    decision = collection.request_collection(env.api, env.scope(), ref, root="", resolver_profile_id="resolver-1")
    assert decision["status"] == "blocked" and "redaction-required" in decision["blocking"]


# Prompt text ----------------------------------------------------------------------

def test_edit_instruction_with_a_deny_listed_name_is_pending_then_reviewer_admitted(env):
    env.policy()
    env.grant("bob")
    text = f"{env.term} 상품 화면의 금리 문구를 연 2.0%로 유지하세요."
    pending = prompts.admit_prompt_text(env.api, env.scope(), text, purpose="edit-instruction")
    assert pending["status"] == "pending-review" and pending["source"]["sourceKind"] == "prompt-text"
    assert env.term not in json.dumps(pending, ensure_ascii=False)
    listed = review.list_pending(env.api, env.scope("bob"))["reviews"]
    assert [item["derivativePreview"] for item in listed] == ["고객사 A 상품 화면의 금리 문구를 연 2.0%로 유지하세요."]
    decision = review.decide(env.api, env.scope("bob"), pending["id"], approve=True, reason="checked")
    assert decision["status"] == "admitted"
    batch = admission.pages_for(env.api, env.scope(), decision["id"])
    assert batch["pages"][0]["text"] == "고객사 A 상품 화면의 금리 문구를 연 2.0%로 유지하세요."
    # A membership/authority change of the project invalidates the prompt source.
    owner = f"project:{env.pid}"
    project = env.api.storage.get(owner, "project", env.pid)
    members = {k: v for k, v in project["members"].items() if k != "dana"}
    env.api.storage.put(owner, "project", {**project, "members": members}, project["version"])
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "source-changed"


def test_prompt_text_with_a_phone_number_is_blocked_and_nothing_is_stored(env):
    env.policy()
    result = prompts.admit_prompt_text(env.api, env.scope(), "연락처 010-5550-7391 로 안내", purpose="proposal-note")
    assert result == {"status": "blocked", "blocking": ["redaction-required"]}
    assert env.api.storage.list(f"project:{env.pid}", "adm_decision") == []


def test_prompt_text_auto_admission_is_rejected_by_policy_validation(env):
    with pytest.raises(AssertionError):
        env.policy(promptText="auto")
    assert env.api.storage.get(INTAKE_OWNER, "adm_policy", "policy-1") is None


# PR #28 review round 1 ----------------------------------------------------------

def _count_opens(monkeypatch):
    opened = []
    original = zipfile.ZipFile.open

    def counting(self, name, *args, **kwargs):
        opened.append(name)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", counting)
    return opened


def test_member_count_is_rejected_before_any_member_is_decompressed(monkeypatch):
    """Finding 7: the 100-file limit is a preflight over the central directory."""
    data = zipped({f"src/f{i:03d}.ts": "export const x = 1;\n" for i in range(101)})
    opened = _count_opens(monkeypatch)
    with pytest.raises(AdmissionError) as error:
        collection._read_zip(data, "")
    assert error.value.code == "collection-too-large" and opened == []


def test_aggregate_expanded_size_including_assets_is_rejected_before_reads(monkeypatch):
    """Finding 7: assets count toward the aggregate expanded size limit."""
    blob = b"\0" * (3 * 1024 * 1024)  # compresses to a few KiB
    data = zipped({"src/a.png": blob, "src/b.png": blob, "src/c.png": blob, "src/index.ts": "export {};\n"})
    assert len(data) < 100_000
    opened = _count_opens(monkeypatch)
    with pytest.raises(AdmissionError) as error:
        collection._read_zip(data, "")
    assert error.value.code == "collection-too-large" and opened == []


def test_text_total_is_rejected_before_reads(monkeypatch):
    """Finding 7: the text aggregate is checked from declared sizes first."""
    text = "a" * 100_000
    data = zipped({f"src/f{i:02d}.ts": text for i in range(25)})
    opened = _count_opens(monkeypatch)
    with pytest.raises(AdmissionError) as error:
        collection._read_zip(data, "")
    assert error.value.code == "collection-too-large" and opened == []


def test_member_expanding_beyond_its_declared_size_is_rejected_during_the_read(monkeypatch):
    """Finding 7: reads are bounded by the declared size, not trusted."""
    data = zipped({"src/index.ts": "export const x = 1;\n" * 10})
    original = zipfile.ZipFile.infolist

    def understated(self):
        infos = original(self)
        for info in infos:
            info.file_size = 5
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", understated)
    with pytest.raises(AdmissionError) as error:
        collection._read_zip(data, "")
    assert error.value.code in ("collection-format", "collection-too-large")


def test_grant_revoked_during_the_analyzer_source_read_blocks_delivery(env, monkeypatch):
    """Review 2, finding 1: analyzer source is rechecked after its last read."""
    decision, _ = admitted_collection(env, {"src/index.ts": "export const x = 1;\n"})
    storage, fired = env.api.storage, []
    original = storage.get_blob

    def get_blob(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key.endswith("collection-files.json") and not fired:
            fired.append(key)
            env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
        return data

    monkeypatch.setattr(storage, "get_blob", get_blob)
    with pytest.raises(AdmissionError) as error:
        collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert fired and error.value.status == 409


def test_resolver_retired_during_the_analyzer_source_read_blocks_delivery(env, monkeypatch):
    decision, _ = admitted_collection(env, {"src/index.ts": "export const x = 1;\n"})
    storage, fired = env.api.storage, []
    original = storage.get_blob

    def get_blob(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key.endswith("collection-files.json") and not fired:
            fired.append(key)
            row = storage.get(INTAKE_OWNER, "adm_resolver", "resolver-1")
            env.admin({"op": "retire_resolver_profile", "id": "resolver-1", "expectedRevision": row["revision"]})
        return data

    monkeypatch.setattr(storage, "get_blob", get_blob)
    with pytest.raises(AdmissionError) as error:
        collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert fired and error.value.status == 409


def test_analyzer_request_uses_only_the_verified_index(env, monkeypatch):
    """Review 3, finding 2: no unverified second index read reaches the analyzer."""
    decision, _ = admitted_collection(env, {"src/index.ts": "export const x = 1;\n"})
    expected = collection.analyzer_request(env.api, env.scope(), decision["id"])
    storage, seen = env.api.storage, []
    original = storage.get_blob
    evil = "export const phone = '010-5550-7391';\n"

    def get_blob(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key.endswith("collection-index.json"):
            seen.append(key)
            if len(seen) > 1:  # every read after the verified one is replaced
                index = json.loads(data)
                index["files"].append({"path": "src/evil.ts", "kind": "code",
                                       "sha256": hashlib.sha256(evil.encode()).hexdigest(), "size": len(evil)})
                return json.dumps(index).encode()
        if key.endswith("collection-files.json"):
            files = json.loads(data)
            files["files"].append({"path": "src/evil.ts", "text": evil})
            return json.dumps(files).encode()
        return data

    monkeypatch.setattr(storage, "get_blob", get_blob)
    payload = collection.analyzer_request(env.api, env.scope(), decision["id"])
    assert "010-5550-7391" not in json.dumps(payload) and payload == expected
