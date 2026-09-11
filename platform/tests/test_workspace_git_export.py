"""Real bare-repository exports and provider contracts, without remote writes."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FILES = {"src/App.tsx": b"export default function App(){return null;}\n",
         "package.json": b'{"name":"approved-project","private":true}\n',
         "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff"}
TARGET = "generated/studio/project-a"


def source_hash(files):
    records = [{"path": path, "sha256": hashlib.sha256(data).hexdigest()}
               for path, data in sorted(files.items())]
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def blob_id(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def git(bare, *args, data=None, index=None):
    env = {"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
           "GIT_AUTHOR_DATE": "2026-09-11T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-11T00:00:00Z"}
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", f"--git-dir={bare}", *args],
                          input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=True, timeout=10).stdout


def make_commit(bare, files, parent=None, message="initial", index=None):
    git(bare, "read-tree", "--empty", index=index)
    for path, data in sorted(files.items()):
        sha = git(bare, "hash-object", "-w", "--stdin", data=data).decode().strip()
        git(bare, "update-index", "--add", "--cacheinfo", f"100644,{sha},{path}", index=index)
    tree = git(bare, "write-tree", index=index).decode().strip()
    return git(bare, "commit-tree", tree, *(["-p", parent] if parent else []),
               data=(message + "\n").encode()).decode().strip()


@pytest.fixture
def repo(tmp_path):
    bare = tmp_path / "destination.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    initial = {"README.md": b"keep outside\n", "generated/other/keep.txt": b"other project\n",
               TARGET + "/obsolete.txt": b"remove stale\n", TARGET + "/src/App.tsx": b"old app\n"}
    base = make_commit(bare, initial, index=tmp_path / "seed-index")
    git(bare, "update-ref", "refs/heads/main", base)
    connection = {"id": "local-test", "provider": "local", "repository": "tests/design",
                  "barePath": str(bare), "baseBranch": "main",
                  "pathPrefix": "generated/studio", "branchPrefix": "feature/studio-"}
    return bare, connection, base, initial


def export(connection, files=None, release_id="release-1", **kwargs):
    from workspace.git_export import GitExporter
    files = FILES if files is None else files
    return GitExporter(connection).export_release(release_id, source_hash(files), files, "project-a", **kwargs)


def test_local_export_has_exact_source_no_stale_files_and_no_outside_changes(repo):
    bare, connection, base, initial = repo
    result = export(connection, expected_base_sha=base, commit_time="2026-09-11T01:02:03Z")
    assert result["status"] == "committed" and result["branch"] == "feature/studio-release-1"
    assert result["baseSha"] == base and result["sourceHash"] == source_hash(FILES)
    assert result["commitUrl"] is None and result["filesUrl"] is None
    assert result["connectionId"] == "local-test" and result["repository"] == "tests/design"
    sha = result["commitSha"]
    assert git(bare, "rev-parse", "refs/heads/main").decode().strip() == base
    assert git(bare, "rev-parse", "refs/heads/" + result["branch"]).decode().strip() == sha
    assert git(bare, "show", "-s", "--format=%P", sha).decode().strip() == base
    actual_paths = git(bare, "ls-tree", "-r", "--name-only", sha).decode().splitlines()
    expected = {path for path in initial if not path.startswith(TARGET + "/")}
    expected.update(TARGET + "/" + path for path in FILES)
    assert set(actual_paths) == expected
    for path, data in FILES.items():
        assert git(bare, "show", f"{sha}:{TARGET}/{path}") == data
    for path in expected:
        if not path.startswith(TARGET + "/"):
            assert git(bare, "show", f"{sha}:{path}") == initial[path]


def test_retry_returns_original_commit_after_main_advances(repo, tmp_path):
    from workspace.git_export import GitExportError
    bare, connection, base, initial = repo
    first = export(connection, expected_base_sha=base)
    second_base = make_commit(bare, {**initial, "README.md": b"new base"},
                              parent=base, index=tmp_path / "second-index")
    git(bare, "update-ref", "refs/heads/main", second_base, base)
    assert export(connection, expected_base_sha=base) == first
    with pytest.raises(GitExportError) as error:
        export(connection, expected_base_sha=second_base)
    assert error.value.code == "conflict"
    with pytest.raises(GitExportError) as error:
        export(connection, release_id="new-release", expected_base_sha=base)
    assert error.value.code == "conflict"


def test_occupied_or_changed_branch_is_never_overwritten(repo, tmp_path):
    from workspace.git_export import GitExportError
    bare, connection, base, initial = repo
    git(bare, "update-ref", "refs/heads/feature/studio-release-1", base)
    with pytest.raises(GitExportError) as error:
        export(connection)
    assert error.value.code == "conflict"
    assert git(bare, "rev-parse", "refs/heads/feature/studio-release-1").decode().strip() == base
    first = export(connection, release_id="release-2")
    changed = make_commit(bare, {**initial, "outside.txt": b"external change"},
                          parent=first["commitSha"], index=tmp_path / "external-index")
    git(bare, "update-ref", "refs/heads/feature/studio-release-2", changed, first["commitSha"])
    with pytest.raises(GitExportError):
        export(connection, release_id="release-2")
    assert git(bare, "rev-parse", "refs/heads/feature/studio-release-2").decode().strip() == changed


def test_forged_message_cannot_hide_outside_changes(repo, tmp_path):
    from workspace.git_export import GitExportError
    bare, connection, base, initial = repo
    first = export(connection)
    message = git(bare, "show", "-s", "--format=%B", first["commitSha"]).decode().strip()
    forged = {**initial, **{TARGET + "/" + path: data for path, data in FILES.items()}, "README.md": b"unauthorized"}
    forged.pop(TARGET + "/obsolete.txt")
    sha = make_commit(bare, forged, parent=base, message=message, index=tmp_path / "forged-index")
    git(bare, "update-ref", "refs/heads/feature/studio-release-1", sha, first["commitSha"])
    with pytest.raises(GitExportError) as error:
        export(connection)
    assert error.value.code == "conflict"


@pytest.mark.parametrize("path", ["../escape", "/absolute", "src/../../escape", "src\\bad", ".git/config",
                                   "src/.GiT/hooks/pre-commit", "src//bad", "src/a\0b", "src/file:stream"])
def test_unsafe_paths_are_rejected_before_ref_changes(repo, path):
    from workspace.git_export import GitExportError
    bare, connection, _, _ = repo
    refs = git(bare, "show-ref")
    with pytest.raises(GitExportError):
        export(connection, {path: b"unsafe"})
    assert git(bare, "show-ref") == refs


def test_source_hash_size_and_path_collisions_are_rejected(repo):
    from workspace.git_export import GitExporter, GitExportError
    bare, connection, _, _ = repo
    refs = git(bare, "show-ref")
    with pytest.raises(GitExportError) as error:
        GitExporter(connection).export_release("r", "0" * 64, FILES, "project-a")
    assert error.value.code == "source-mismatch"
    for files in ({}, {"src": b"file", "src/App.tsx": b"other"},
                  {"too-large": b"x" * 12_000_001}, {str(i): b"x" for i in range(501)}):
        with pytest.raises(GitExportError):
            export(connection, files)
    assert git(bare, "show-ref") == refs


@pytest.mark.parametrize("change", [
    {"branchPrefix": "main"}, {"branchPrefix": "feature/../main"}, {"pathPrefix": "../outside"},
    {"pathPrefix": ".git"}, {"baseBranch": "--upload-pack=evil"}, {"repository": "https://customer.example/repo"},
])
def test_connection_paths_and_branches_are_validated(repo, change):
    from workspace.git_export import GitExportError
    with pytest.raises(GitExportError):
        export({**repo[1], **change})


def test_no_git_hooks_or_inherited_commands_execute(repo, tmp_path, monkeypatch):
    bare, connection, _, _ = repo
    sentinel = tmp_path / "hook-executed"
    hook = bare / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\n touch '{sentinel}'\n")
    hook.chmod(0o755)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(bare / "hooks"))
    monkeypatch.setenv("GIT_DIR", "/nonexistent")
    export(connection)
    assert not sentinel.exists()


def test_dangling_symbolic_feature_ref_cannot_redirect_a_write(repo, monkeypatch):
    from workspace.git_export import GitExportError, _Local
    bare, connection, _, _ = repo
    original = _Local.publish
    def race(self, branch, sha, base_branch, base):
        git(bare, "symbolic-ref", "refs/heads/" + branch, "refs/heads/outside-prefix")
        return original(self, branch, sha, base_branch, base)
    monkeypatch.setattr(_Local, "publish", race)
    with pytest.raises(GitExportError) as error:
        export(connection)
    assert error.value.code == "conflict"
    outside = subprocess.run(["git", f"--git-dir={bare}", "rev-parse", "--verify", "--quiet",
                              "refs/heads/outside-prefix"], capture_output=True)
    assert outside.returncode != 0


def test_main_advance_during_local_publication_cancels_feature_creation(repo, tmp_path, monkeypatch):
    from workspace.git_export import GitExportError, _Local
    bare, connection, base, initial = repo
    advanced = make_commit(bare, {**initial, "README.md": b"advanced"}, parent=base,
                           index=tmp_path / "advance-index")
    original = _Local.publish
    def race(self, branch, sha, base_branch, captured):
        git(bare, "update-ref", "refs/heads/main", advanced, base)
        return original(self, branch, sha, base_branch, captured)
    monkeypatch.setattr(_Local, "publish", race)
    with pytest.raises(GitExportError) as error:
        export(connection, expected_base_sha=base)
    assert error.value.code == "conflict"
    assert git(bare, "show-ref").decode().splitlines() == [advanced + " refs/heads/main"]


class Remote:
    """Provider-shaped object/branch service; no HTTP requests or real tokens."""
    def __init__(self, provider):
        self.provider, self.calls, self.counter = provider, [], 1
        self.base = "a" * 40
        self.refs = {"main": self.base}
        self.trees = {"b" * 40: {
            "README.md": ("100644", "blob", blob_id(b"keep")),
            TARGET + "/stale.txt": ("100644", "blob", blob_id(b"stale")),
        }}
        self.commits = {self.base: {"message": "base", "parents": [], "tree": "b" * 40}}

    def oid(self):
        self.counter += 1
        return f"{self.counter:040x}"

    def __call__(self, method, url, headers, payload):
        from workspace.git_export import GitExportError
        self.calls.append((method, url, copy.deepcopy(headers), copy.deepcopy(payload)))
        parsed = urlsplit(url)
        if self.provider == "github":
            assert parsed.netloc == "api.github.com"
            assert headers["Authorization"] == "Bearer test-token"
            path = parsed.path.removeprefix("/repos/acme/screens")
            if path.startswith("/git/ref/heads/"):
                branch = unquote(path[len("/git/ref/heads/"):])
                if branch not in self.refs:
                    raise GitExportError("http-404", "Not found")
                return {"ref": "refs/heads/" + branch, "object": {"type": "commit", "sha": self.refs[branch]}}
            if method == "GET" and path.startswith("/git/commits/"):
                sha = path.rsplit("/", 1)[1]
                commit = self.commits[sha]
                return {"sha": sha, "message": commit["message"], "tree": {"sha": commit["tree"]},
                        "parents": [{"sha": parent} for parent in commit["parents"]]}
            if method == "GET" and path.startswith("/git/trees/"):
                sha = path.rsplit("/", 1)[1]
                return {"sha": sha, "truncated": False, "tree": [
                    {"path": path, "mode": mode, "type": kind, "sha": oid}
                    for path, (mode, kind, oid) in self.trees[sha].items()]}
            if method == "POST" and path == "/git/blobs":
                assert payload["encoding"] == "base64"
                return {"sha": blob_id(base64.b64decode(payload["content"], validate=True))}
            if method == "POST" and path == "/git/trees":
                tree = copy.deepcopy(self.trees[payload["base_tree"]]) if "base_tree" in payload else {}
                for entry in payload["tree"]:
                    if entry["type"] == "tree":
                        assert entry["path"] == TARGET
                        tree = {key: value for key, value in tree.items()
                                if key != TARGET and not key.startswith(TARGET + "/")}
                        tree.update({TARGET + "/" + key: value for key, value in self.trees[entry["sha"]].items()})
                    else:
                        tree[entry["path"]] = (entry["mode"], entry["type"], entry["sha"])
                sha = self.oid()
                self.trees[sha] = tree
                return {"sha": sha}
            if method == "POST" and path == "/git/commits":
                sha = self.oid()
                self.commits[sha] = copy.deepcopy(payload)
                return {"sha": sha}
            if method == "POST" and path == "/git/refs":
                branch = payload["ref"].removeprefix("refs/heads/")
                assert branch.startswith("feature/studio-")
                if branch in self.refs:
                    raise GitExportError("conflict", "Occupied")
                self.refs[branch] = payload["sha"]
                return {"ref": payload["ref"], "object": {"sha": payload["sha"], "type": "commit"}}
        else:
            assert parsed.netloc == "gitlab.com"
            assert headers["PRIVATE-TOKEN"] == "test-token"
            path = parsed.path.removeprefix("/api/v4/projects/acme%2Fscreens/repository")
            if path.startswith("/branches/"):
                branch = unquote(path[len("/branches/"):])
                if branch not in self.refs:
                    raise GitExportError("http-404", "Not found")
                return {"name": branch, "commit": {"id": self.refs[branch]}}
            if method == "GET" and path.startswith("/commits/"):
                sha = path.rsplit("/", 1)[1]
                commit = self.commits[sha]
                return {"id": sha, "message": commit["message"], "parent_ids": commit["parents"]}
            if method == "GET" and path == "/tree":
                query = parse_qs(parsed.query)
                tree = self.trees[self.commits[query["ref"][0]]["tree"]]
                records = [{"path": path, "mode": mode, "type": kind, "id": oid}
                           for path, (mode, kind, oid) in sorted(tree.items())]
                start = (int(query.get("page", ["1"])[0]) - 1) * 100
                return records[start:start + 100]
            if method == "POST" and path == "/commits":
                assert payload["force"] is False and payload["start_sha"] == self.base
                branch = payload["branch"]
                if branch in self.refs:
                    raise GitExportError("conflict", "Occupied")
                tree = copy.deepcopy(self.trees[self.commits[self.base]["tree"]])
                for action in payload["actions"]:
                    path = action["file_path"]
                    assert path.startswith(TARGET + "/")
                    if action["action"] == "delete":
                        del tree[path]
                    elif action["action"] == "chmod":
                        assert action["execute_filemode"] is False
                        tree[path] = ("100644", *tree[path][1:])
                    else:
                        assert action["action"] in ("create", "update") and action["encoding"] == "base64"
                        tree[path] = ("100644", "blob", blob_id(base64.b64decode(action["content"], validate=True)))
                tree_sha, sha = self.oid(), self.oid()
                self.trees[tree_sha] = tree
                self.commits[sha] = {"tree": tree_sha, "parents": [self.base], "message": payload["commit_message"]}
                self.refs[branch] = sha
                return {"id": sha}
        raise AssertionError(f"Unexpected provider operation: {method} {parsed.path}")


def remote_exporter(provider, transport):
    from workspace.git_export import GitExporter
    connection = {"id": "registered", "provider": provider, "repository": "acme/screens",
                  "baseBranch": "main", "pathPrefix": "generated/studio", "branchPrefix": "feature/studio-"}
    return GitExporter(connection, token_provider=lambda connection: "test-token", transport=transport)


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_remote_exports_verify_contents_and_retry_without_another_mutation(provider):
    service = Remote(provider)
    exporter = remote_exporter(provider, service)
    result = exporter.export_release("release-1", source_hash(FILES), FILES, "project-a")
    assert result["status"] == "committed" and result["baseSha"] == service.base
    committed = service.trees[service.commits[result["commitSha"]]["tree"]]
    assert set(committed) == {"README.md", *(TARGET + "/" + path for path in FILES)}
    assert committed["README.md"] == ("100644", "blob", blob_id(b"keep"))
    mutations = sum(call[0] != "GET" for call in service.calls)
    assert exporter.export_release("release-1", source_hash(FILES), FILES, "project-a") == result
    assert sum(call[0] != "GET" for call in service.calls) == mutations
    assert service.refs["main"] == service.base
    assert result["commitSha"] in result["commitUrl"] and result["commitSha"] in result["filesUrl"]
    assert "test-token" not in json.dumps(result)


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_remote_branch_race_never_overwrites_other_writer(provider):
    from workspace.git_export import GitExportError
    service = Remote(provider)
    def racing(method, url, headers, payload):
        if method == "POST" and (url.endswith("/git/refs") or (
                provider == "gitlab" and url.endswith("/commits"))):
            service.refs["feature/studio-release-1"] = service.base
        return service(method, url, headers, payload)
    with pytest.raises(GitExportError) as error:
        remote_exporter(provider, racing).export_release("release-1", source_hash(FILES), FILES, "project-a")
    assert error.value.code == "conflict"
    assert service.refs["feature/studio-release-1"] == service.base


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_remote_lost_success_response_is_recovered_by_verified_branch(provider):
    service = Remote(provider)
    def lost_response(method, url, headers, payload):
        result = service(method, url, headers, payload)
        if method == "POST" and (url.endswith("/git/refs") or (
                provider == "gitlab" and url.endswith("/commits"))):
            raise OSError("response lost; never expose test-token")
        return result
    result = remote_exporter(provider, lost_response).export_release(
        "release-1", source_hash(FILES), FILES, "project-a")
    assert result["commitSha"] == service.refs["feature/studio-release-1"]
    assert result["status"] == "committed"


def test_truncated_tree_and_transport_errors_fail_closed_without_logging(capsys):
    from workspace.git_export import GitExportError
    service = Remote("github")
    def truncated(method, url, headers, payload):
        result = service(method, url, headers, payload)
        if "/git/trees/" in url:
            result["truncated"] = True
        return result
    with pytest.raises(GitExportError):
        remote_exporter("github", truncated).export_release("r", source_hash(FILES), FILES, "project-a")
    assert all(call[0] == "GET" for call in service.calls)
    def broken(*args):
        raise RuntimeError("secret-token private HTTP body")
    with pytest.raises(GitExportError) as error:
        remote_exporter("github", broken).export_release("r", source_hash(FILES), FILES, "project-a")
    assert "secret-token" not in str(error.value)
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_default_https_transport_refuses_redirect_without_forwarding_token(monkeypatch):
    from workspace.git_export import GitExportError, _https_transport
    requests, connections = [], []
    class Connection:
        def __init__(self, host, port, **kwargs):
            connections.append(host)
        def request(self, method, path, **kwargs):
            requests.append((method, path))
        def getresponse(self):
            return type("Response", (), {"status": 302})()
        def close(self):
            pass
    monkeypatch.setattr("workspace.git_export.http.client.HTTPSConnection", Connection)
    with pytest.raises(GitExportError) as error:
        _https_transport("GET", "https://git.example/api", {"Authorization": "Bearer secret"}, None)
    assert error.value.code == "redirect-refused"
    assert connections == ["git.example"] and requests == [("GET", "/api")]


def test_export_freezes_source_before_calling_injected_services():
    from workspace.git_export import GitExporter
    service = Remote("github")
    files = dict(FILES)
    approved_hash = source_hash(files)
    connection = {"id": "registered", "provider": "github", "repository": "acme/screens",
                  "baseBranch": "main", "pathPrefix": "generated/studio", "branchPrefix": "feature/studio-"}
    def token(config):
        files["src/App.tsx"] = b"changed after verification"
        return "test-token"
    result = GitExporter(connection, token_provider=token, transport=service).export_release(
        "r", approved_hash, files, "project-a")
    tree = service.trees[service.commits[result["commitSha"]]["tree"]]
    assert tree[TARGET + "/src/App.tsx"][2] == blob_id(FILES["src/App.tsx"])
    assert result["sourceHash"] == approved_hash


def test_gitlab_tree_pagination_does_not_drop_unrelated_files():
    service = Remote("gitlab")
    for index in range(150):
        service.trees["b" * 40][f"existing/{index}.txt"] = ("100644", "blob", blob_id(str(index).encode()))
    result = remote_exporter("gitlab", service).export_release("r", source_hash(FILES), FILES, "project-a")
    tree = service.trees[service.commits[result["commitSha"]]["tree"]]
    assert len(tree) == 151 + len(FILES)
    assert any("page=2" in call[1] for call in service.calls)


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_remote_success_response_with_wrong_source_is_not_committed_evidence(provider):
    from workspace.git_export import GitExportError
    service = Remote(provider)
    def corrupted(method, url, headers, payload):
        response = service(method, url, headers, payload)
        if method == "POST" and url.endswith("/commits"):
            sha = response["sha" if provider == "github" else "id"]
            tree = service.trees[service.commits[sha]["tree"]]
            tree[TARGET + "/src/App.tsx"] = ("100644", "blob", blob_id(b"wrong source"))
        return response
    with pytest.raises(GitExportError) as error:
        remote_exporter(provider, corrupted).export_release("r", source_hash(FILES), FILES, "project-a")
    assert error.value.code == "conflict"
    if provider == "github":
        assert "feature/studio-r" not in service.refs


def test_unavailable_token_provider_cannot_leak_errors_or_contact_transport(capsys):
    from workspace.git_export import GitExporter, GitExportError
    connection = {"id": "registered", "provider": "github", "repository": "acme/screens",
                  "baseBranch": "main", "pathPrefix": "generated/studio", "branchPrefix": "feature/studio-"}
    calls = []
    def broken(config):
        raise RuntimeError("private-secret-provider-detail")
    with pytest.raises(GitExportError) as error:
        GitExporter(connection, token_provider=broken, transport=lambda *args: calls.append(args)).export_release(
            "r", source_hash(FILES), FILES, "project-a")
    assert error.value.code == "authentication"
    assert "private-secret" not in str(error.value) and calls == []
    output = capsys.readouterr()
    assert not output.out and not output.err
