"""Export verified archives to registered feature-branch destinations.

Only operator configuration selects a repository. token_provider(connection)
returns a token; webUrl is the provider's web root, not a caller-supplied link.
No checkout, package scripts, hooks, credential helpers, redirects, or force
updates are used. Local Git operates on objects and an invocation-private index.

Primary API definitions verified 2026-09-11:
https://docs.github.com/en/rest/git/trees
https://docs.github.com/en/rest/git/commits
https://docs.github.com/en/rest/git/refs
https://docs.gitlab.com/api/commits/
https://docs.gitlab.com/api/repositories/
GitLab's start_sha + force=false creates a new branch and rejects an existing
one (Commits::CreateService.validate_branch_existence! in upstream GitLab).
GitLab controls commit timestamps; commit_time applies to local/GitHub (ISO8601
with a timezone, or integer Unix seconds). GitLab's allow_empty option requires
18.8+ when releasing an unchanged source tree. Non-regular GitLab target entries
are rejected rather than rewritten through ambiguous file actions.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
from urllib.parse import quote, urlencode, urlsplit

from workspace.react_artifacts import files_hash

MAX_FILES = 500
MAX_SOURCE_BYTES = 12_000_000
MAX_RESPONSE_BYTES = 8_000_000
MAX_TREE_ENTRIES = 20_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_SHA = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_MESSAGES = {
    "invalid-input": "Invalid export identity, configuration, or source path.",
    "source-mismatch": "Source bytes do not match the approved archive hash.",
    "conflict": "The base or feature branch conflicts with this release.",
    "unavailable": "The registered Git destination is unavailable.",
    "authentication": "Git authentication is unavailable or was refused.",
    "redirect-refused": "Git API redirects are not permitted.",
    "invalid-response": "Git returned incomplete or inconsistent evidence.",
    "too-large": "The repository or export exceeds the adapter limit.",
    "http-404": "The Git resource was not found.",
    "rate-limited": "The Git provider rate limit was reached.",
}


class GitExportError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _fail(code):
    raise GitExportError(code, _MESSAGES[code])


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("invalid-input")
    return value


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail("invalid-response")
    return value


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        _fail("invalid-input")
    for part in value.split("/"):
        if (part in ("", ".", "..") or len(part) > 180 or part.rstrip(". ").casefold() == ".git"
                or part.endswith((".", " ")) or not re.fullmatch(r"[A-Za-z0-9_.@+-]+", part)):
            _fail("invalid-input")
    return value


def _branch(value):
    _path(value)
    if (len(value) > 200 or value.startswith(("-", "refs/")) or ".." in value
            or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))):
        _fail("invalid-input")
    return value


def _url(value):
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) <= 32 for c in value):
        _fail("invalid-input")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "\\" in value or "%" in parsed.path
            or any(part in (".", "..") for part in parsed.path.split("/"))):
        _fail("invalid-input")
    try:
        parsed.port
    except ValueError:
        _fail("invalid-input")
    return value.rstrip("/")


def _inside(path, target):
    return path == target or path.startswith(target + "/")


def _blob_sha(data, sha_length=40):
    body = b"blob " + str(len(data)).encode() + b"\0" + data
    return (hashlib.sha1(body) if sha_length == 40 else hashlib.sha256(body)).hexdigest()


def _timestamp(value):
    try:
        if value is None:
            result = datetime.now(timezone.utc)
        elif type(value) is int and 0 <= value <= 253402300799:
            result = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str) and len(value) <= 40:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if result.tzinfo is None:
                _fail("invalid-input")
        else:
            _fail("invalid-input")
        return result.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        _fail("invalid-input")


def _https_transport(method, url, headers, payload):
    """One HTTPS request, no redirect following or inherited proxy settings."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        _fail("invalid-input")
    body = _json(payload) if payload is not None else None
    if body is not None and len(body) > 20_000_000:
        _fail("too-large")
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                            timeout=20, context=ssl.create_default_context())
    try:
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if 300 <= response.status < 400:
            _fail("redirect-refused")
        if response.status in (401, 403):
            _fail("authentication")
        if response.status == 404:
            _fail("http-404")
        if response.status == 429:
            _fail("rate-limited")
        if response.status in (400, 409, 422):
            _fail("conflict")
        if not 200 <= response.status < 300:
            _fail("unavailable")
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            _fail("too-large")
        return json.loads(data)
    except GitExportError:
        raise
    except (OSError, http.client.HTTPException):
        raise GitExportError("unavailable", _MESSAGES["unavailable"]) from None
    except (ValueError, UnicodeError):
        raise GitExportError("invalid-response", _MESSAGES["invalid-response"]) from None
    finally:
        connection.close()


class _Local:
    def __init__(self, config, deadline):
        self.config, self.deadline = config, deadline
        path = config.get("barePath")
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_dir():
            _fail("invalid-input")
        self.bare = str(Path(path).resolve())
        self.env = {"PATH": os.defpath, "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
                    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ALLOW_PROTOCOL": "file",
                    "GIT_AUTHOR_NAME": "Studio Workspace", "GIT_AUTHOR_EMAIL": "studio@workspace.invalid",
                    "GIT_COMMITTER_NAME": "Studio Workspace", "GIT_COMMITTER_EMAIL": "studio@workspace.invalid"}
        if self.run("rev-parse", "--is-bare-repository").strip() != b"true":
            _fail("invalid-input")

    def run(self, *args, data=None, env=None, missing=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            _fail("unavailable")
        command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                   "-c", "gc.auto=0", "-c", "commit.gpgSign=false", f"--git-dir={self.bare}", *args]
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(command, input=data, stdout=output, stderr=subprocess.DEVNULL,
                                        env={**self.env, **(env or {})}, timeout=min(20, remaining))
                if result.returncode:
                    if missing and result.returncode == 1:
                        return None
                    _fail("conflict" if args[0] == "update-ref" else "unavailable")
                if output.tell() > MAX_RESPONSE_BYTES:
                    _fail("too-large")
                output.seek(0)
                return output.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, subprocess.TimeoutExpired):
            raise GitExportError("unavailable", _MESSAGES["unavailable"]) from None

    def ref(self, branch):
        if self.run("symbolic-ref", "--quiet", "refs/heads/" + branch, missing=True):
            _fail("conflict")
        raw = self.run("rev-parse", "--verify", "--quiet", "refs/heads/" + branch, missing=True)
        return _sha(raw.decode().strip()) if raw else None

    def commit(self, sha):
        raw = self.run("cat-file", "commit", sha).decode("utf-8")
        head, message = raw.split("\n\n", 1)
        lines = head.splitlines()
        return {"sha": sha, "tree": _sha(lines[0].removeprefix("tree ")),
                "parents": [_sha(line[7:]) for line in lines if line.startswith("parent ")],
                "message": message.rstrip("\n")}

    def tree(self, commit):
        raw = self.run("ls-tree", "-r", "-z", commit["tree"])
        entries = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, kind, sha = header.decode().split(" ")
            entries[path.decode("utf-8")] = (mode, kind, _sha(sha))
        if len(entries) > MAX_TREE_ENTRIES:
            _fail("too-large")
        return entries

    def create(self, base, tree, target, files, message, when, branch):
        with tempfile.TemporaryDirectory(prefix="studio-git-export-") as directory:
            env = {"GIT_INDEX_FILE": str(Path(directory) / "index"),
                   "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
            self.run("read-tree", base, env=env)
            removals = b"".join(f"0 {'0' * len(base)}\t{path}\0".encode()
                                for path in tree if _inside(path, target))
            if removals:
                self.run("update-index", "-z", "--index-info", data=removals, env=env)
            additions = []
            for path, contents in sorted(files.items()):
                sha = self.run("hash-object", "-w", "--stdin", data=contents).decode().strip()
                additions.append(f"100644 {_sha(sha)}\t{target}/{path}\0".encode())
            self.run("update-index", "-z", "--index-info", data=b"".join(additions), env=env)
            root = self.run("write-tree", env=env).decode().strip()
            return _sha(self.run("commit-tree", _sha(root), "-p", base,
                                 data=(message + "\n").encode(), env=env).decode().strip())

    def publish(self, branch, sha, base_branch, base):
        # Verify base and create the feature ref in one Git reference transaction.
        if self.ref(branch) is not None:
            _fail("conflict")
        transaction = (f"start\nverify refs/heads/{base_branch} {base}\n"
                       f"create refs/heads/{branch} {sha}\nprepare\ncommit\n").encode()
        self.run("update-ref", "--no-deref", "--stdin", data=transaction)


class _Remote:
    def __init__(self, config, token_provider, transport, deadline):
        self.config, self.transport, self.deadline = config, transport, deadline
        self.github = config["provider"] == "github"
        self.commits, self.trees, self.calls = {}, {}, 0
        try:
            token = token_provider(copy.deepcopy(config)) if token_provider else None
        except Exception:
            raise GitExportError("authentication", _MESSAGES["authentication"]) from None
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            _fail("authentication")
        self.headers = {"Accept": "application/json", "Content-Type": "application/json",
                        "User-Agent": "Studio-Workspace-Git-Exporter"}
        if self.github:
            self.root = config["baseUrl"] + "/repos/" + config["repository"]
            self.headers.update({"Authorization": "Bearer " + token,
                                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        else:
            self.root = config["baseUrl"] + "/projects/" + quote(config["repository"], safe="") + "/repository"
            self.headers["PRIVATE-TOKEN"] = token

    def call(self, method, path, payload=None):
        self.calls += 1
        if self.calls > 2000 or time.monotonic() >= self.deadline:
            _fail("unavailable")
        try:
            result = self.transport(method, self.root + path, dict(self.headers), payload)
            if not isinstance(result, (dict, list)):
                _fail("invalid-response")
            if len(_json(result)) > MAX_RESPONSE_BYTES:
                _fail("too-large")
            return result
        except GitExportError as error:
            code = error.code if error.code in _MESSAGES else "unavailable"
            raise GitExportError(code, _MESSAGES[code]) from None
        except Exception:
            raise GitExportError("unavailable", _MESSAGES["unavailable"]) from None

    def ref(self, branch):
        path = ("/git/ref/heads/" + quote(branch, safe="/") if self.github
                else "/branches/" + quote(branch, safe=""))
        try:
            result = self.call("GET", path)
        except GitExportError as error:
            if error.code == "http-404":
                return None
            raise
        if self.github:
            if result["ref"] != "refs/heads/" + branch or result["object"]["type"] != "commit":
                _fail("invalid-response")
            sha = result["object"]["sha"]
        else:
            if result["name"] != branch:
                _fail("invalid-response")
            sha = result["commit"]["id"]
        if len(_sha(sha)) != 40:
            _fail("invalid-response")
        return sha

    def commit(self, sha):
        if sha not in self.commits:
            result = self.call("GET", ("/git/commits/" if self.github else "/commits/") + sha)
            if result["sha" if self.github else "id"] != sha:
                _fail("invalid-response")
            message = result["message"]
            parents = [item["sha"] for item in result["parents"]] if self.github else result["parent_ids"]
            if not isinstance(message, str) or len(message) > 16_000 or not isinstance(parents, list):
                _fail("invalid-response")
            commit = {"sha": sha, "message": message, "parents": [_sha(parent) for parent in parents]}
            if self.github:
                commit["tree"] = _sha(result["tree"]["sha"])
            self.commits[sha] = commit
        return self.commits[sha]

    def tree(self, commit):
        sha = commit["sha"]
        if sha in self.trees:
            return self.trees[sha]
        if self.github:
            result = self.call("GET", "/git/trees/" + commit["tree"] + "?recursive=1")
            if result.get("truncated") is not False or result.get("sha") != commit["tree"]:
                _fail("invalid-response")
            rows = result["tree"]
            if not isinstance(rows, list):
                _fail("invalid-response")
        else:
            rows = []
            for page in range(1, MAX_TREE_ENTRIES // 100 + 2):
                result = self.call("GET", "/tree?" + urlencode(
                    {"ref": sha, "recursive": "true", "per_page": 100, "page": page}))
                if not isinstance(result, list) or len(result) > 100:
                    _fail("invalid-response")
                rows.extend(result)
                if len(rows) > MAX_TREE_ENTRIES:
                    _fail("too-large")
                if len(result) < 100:
                    break
        if len(rows) > MAX_TREE_ENTRIES:
            _fail("too-large")
        tree = {}
        seen = set()
        for entry in rows:
            path, kind, mode = entry["path"], entry["type"], entry["mode"]
            if (not isinstance(path, str) or not path or len(path) > 2048 or "\0" in path
                    or any(part in ("", ".", "..") for part in path.split("/")) or path in seen):
                _fail("invalid-response")
            seen.add(path)
            if kind == "tree" and mode in ("040000", "40000"):
                continue
            if (kind, mode) not in (("blob", "100644"), ("blob", "100755"), ("blob", "120000"), ("commit", "160000")):
                _fail("invalid-response")
            tree[path] = (mode, kind, _sha(entry["sha" if self.github else "id"]))
        self.trees[sha] = tree
        return tree

    def create(self, base, tree, target, files, message, when, branch):
        if self.github:
            entries = []
            for path, data in sorted(files.items()):
                result = self.call("POST", "/git/blobs", {
                    "encoding": "base64", "content": base64.b64encode(data).decode("ascii")})
                sha = _sha(result["sha"])
                if sha != _blob_sha(data):
                    _fail("invalid-response")
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
            # Replace one complete subtree; stale target files cannot survive,
            # and the base_tree preserves all unrelated repository entries.
            subtree = _sha(self.call("POST", "/git/trees", {"tree": entries})["sha"])
            root = _sha(self.call("POST", "/git/trees", {
                "base_tree": self.commit(base)["tree"],
                "tree": [{"path": target, "mode": "040000", "type": "tree", "sha": subtree}],
            })["sha"])
            identity = {"name": "Studio Workspace", "email": "studio@workspace.invalid", "date": when}
            return _sha(self.call("POST", "/git/commits", {
                "tree": root, "parents": [base], "message": message,
                "author": identity, "committer": identity,
            })["sha"])
        current = {path: entry for path, entry in tree.items() if _inside(path, target)}
        if target in current or any(entry[:2] not in (("100644", "blob"), ("100755", "blob"))
                                    for entry in current.values()):
            _fail("conflict")
        desired = {target + "/" + path: data for path, data in files.items()}
        actions = [{"action": "delete", "file_path": path} for path in sorted(current) if path not in desired]
        for path, data in sorted(desired.items()):
            actions.append({"action": "update" if path in current else "create", "file_path": path,
                            "encoding": "base64", "content": base64.b64encode(data).decode("ascii")})
            if path in current and current[path][0] == "100755":
                actions.append({"action": "chmod", "file_path": path, "execute_filemode": False})
        if self.ref(self.config["baseBranch"]) != base:
            _fail("conflict")
        return _sha(self.call("POST", "/commits", {
            "branch": branch, "start_sha": base, "force": False, "commit_message": message,
            "actions": actions, "allow_empty": True,
        })["id"])

    def publish(self, branch, sha, base_branch, base):
        if not self.github:
            # GitLab commits API created the branch and commit atomically.
            if self.ref(branch) != sha:
                _fail("conflict")
            return
        result = self.call("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": sha})
        if result["ref"] != "refs/heads/" + branch or result["object"]["sha"] != sha:
            _fail("invalid-response")


class GitExporter:
    def __init__(self, connection, token_provider=None, transport=None):
        if not isinstance(connection, dict):
            _fail("invalid-input")
        self.connection = copy.deepcopy(connection)
        self.token_provider, self.transport = token_provider, transport or _https_transport
        config = self.connection
        _id(config.get("id"))
        if config.get("provider") not in ("local", "github", "gitlab"):
            _fail("invalid-input")
        repository = _path(config.get("repository"))
        if len(repository.split("/")) < 2 or (config["provider"] == "github" and len(repository.split("/")) != 2):
            _fail("invalid-input")
        _path(config.get("pathPrefix"))
        _branch(config.get("baseBranch"))
        prefix = config.get("branchPrefix")
        if not isinstance(prefix, str) or not prefix.startswith("feature/"):
            _fail("invalid-input")
        _branch(prefix + "release")
        if config["provider"] != "local":
            defaults = ("https://api.github.com", "https://github.com") if config["provider"] == "github" else (
                "https://gitlab.com/api/v4", "https://gitlab.com")
            config["baseUrl"] = _url(config.get("baseUrl", defaults[0]))
            if "webUrl" not in config and config["baseUrl"] != defaults[0]:
                _fail("invalid-input")
            config["webUrl"] = _url(config.get("webUrl", defaults[1]))

    def _message(self, release_id, source_hash, project_key, target, base):
        identity = {"schemaVersion": 1, "releaseId": release_id, "sourceHash": source_hash,
                    "projectKey": project_key, "target": target, "baseSha": base,
                    "connectionId": self.connection["id"], "repository": self.connection["repository"]}
        return "Studio verified release\n\nStudio-Export: " + _json(identity).decode()

    def _verify(self, backend, sha, release_id, source_hash, project_key, target, files, expected):
        commit = backend.commit(sha)
        if len(commit["parents"]) != 1:
            _fail("conflict")
        base = commit["parents"][0]
        if expected is not None and base != expected:
            _fail("conflict")
        if commit["message"].rstrip("\n") != self._message(release_id, source_hash, project_key, target, base):
            _fail("conflict")
        before, after = backend.tree(backend.commit(base)), backend.tree(commit)
        wanted = {target + "/" + path: ("100644", "blob", _blob_sha(data, len(base))) for path, data in files.items()}
        if ({path: entry for path, entry in after.items() if _inside(path, target)} != wanted
                or {path: entry for path, entry in before.items() if not _inside(path, target)}
                != {path: entry for path, entry in after.items() if not _inside(path, target)}):
            _fail("conflict")
        return base

    def _result(self, branch, base, sha, source_hash, target):
        config = self.connection
        commit_url = files_url = None
        if config["provider"] != "local":
            root = config["webUrl"] + "/" + config["repository"]
            separator = "/-/" if config["provider"] == "gitlab" else "/"
            commit_url = root + separator + "commit/" + sha
            files_url = root + separator + "tree/" + sha + "/" + quote(target, safe="/")
        return {"status": "committed", "branch": branch, "baseSha": base, "commitSha": sha,
                "commitUrl": commit_url, "filesUrl": files_url, "repository": config["repository"],
                "connectionId": config["id"], "sourceHash": source_hash}

    def export_release(self, release_id, source_hash, files, project_key, expected_base_sha=None, commit_time=None):
        _id(release_id)
        _id(project_key)
        if (not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES
                or not isinstance(source_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", source_hash)):
            _fail("invalid-input")
        files = dict(files)  # Bytes are immutable; callbacks cannot replace our snapshot.
        paths, size = set(), 0
        for path, contents in files.items():
            _path(path)
            if not isinstance(contents, bytes):
                _fail("invalid-input")
            paths.add(path)
            size += len(contents)
        if size > MAX_SOURCE_BYTES:
            _fail("too-large")
        if len({path.casefold() for path in paths}) != len(paths) or any(
                "/".join(path.split("/")[:index]) in paths for path in paths for index in range(1, len(path.split("/")))):
            _fail("invalid-input")
        if files_hash(files) != source_hash:
            _fail("source-mismatch")
        if expected_base_sha is not None:
            _sha(expected_base_sha)
        when = _timestamp(commit_time)
        target = _path(self.connection["pathPrefix"] + "/" + project_key)
        branch = _branch(self.connection["branchPrefix"] + release_id)
        if branch == self.connection["baseBranch"]:
            _fail("invalid-input")
        try:
            return self._export(release_id, source_hash, files, project_key, expected_base_sha, when, target, branch)
        except GitExportError as error:
            code = error.code if error.code in _MESSAGES else "unavailable"
            raise GitExportError(code, _MESSAGES[code]) from None
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise GitExportError("invalid-response", _MESSAGES["invalid-response"]) from None

    def _export(self, release_id, source_hash, files, project_key, expected, when, target, branch):
        deadline = time.monotonic() + 120
        if self.connection["provider"] == "local":
            backend = _Local(self.connection, deadline)
        else:
            backend = _Remote(self.connection, self.token_provider, self.transport, deadline)
        def existing(sha):
            base = self._verify(backend, sha, release_id, source_hash, project_key, target, files, expected)
            if backend.ref(branch) != sha:
                _fail("conflict")
            return self._result(branch, base, sha, source_hash, target)
        occupied = backend.ref(branch)
        if occupied:
            return existing(occupied)
        base = backend.ref(self.connection["baseBranch"])
        if not base:
            _fail("http-404")
        if expected is not None and base != expected:
            _fail("conflict")
        tree = backend.tree(backend.commit(base))
        if any("/".join(target.split("/")[:index]) in tree for index in range(1, len(target.split("/")))):
            _fail("conflict")
        message = self._message(release_id, source_hash, project_key, target, base)
        try:
            sha = backend.create(base, tree, target, files, message, when, branch)
            self._verify(backend, sha, release_id, source_hash, project_key, target, files, base)
            if backend.ref(self.connection["baseBranch"]) != base:
                _fail("conflict")
            backend.publish(branch, sha, self.connection["baseBranch"], base)
        except GitExportError:
            occupied = backend.ref(branch)
            if occupied:
                return existing(occupied)
            raise
        return existing(sha)
