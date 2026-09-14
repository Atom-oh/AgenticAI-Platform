"""Bounded collection from server-owned connection profiles.

HTTP paths are constructed locally. Redirects and upstream next URLs are never
followed. Credentials are supplied only by an injected server token provider.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from workbench.service import ROLES, _hash, fail
from workspace.collaboration import CollaborationError

MAX_DOCUMENTS = 100
MAX_RESPONSE_BYTES = 2_000_000
SECRET_ARN = re.compile(r"arn:[a-z0-9-]+:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+\Z")


def configured_connections():
    try:
        profiles = json.loads(os.environ.get("WORKBENCH_CONNECTIONS_JSON", "{}"))
    except ValueError:
        fail(503, "connection-not-configured", "연결 구성이 올바르지 않습니다.")
    return profiles if isinstance(profiles, dict) else {}


def profiles(host):
    provider = getattr(host, "workbench_connections", configured_connections)
    value = provider() if callable(provider) else provider
    return value if isinstance(value, dict) else {}


def validate_profile(profile):
    if (not isinstance(profile, dict) or profile.get("approved") is not True
            or profile.get("kind") not in {"confluence", "git"}):
        fail(503, "connection-not-configured", "승인된 연결 프로필이 없습니다.")
    parsed = urlsplit(profile.get("baseUrl", ""))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        fail(503, "connection-not-configured", "허용된 HTTPS 연결 프로필이 필요합니다.")
    roles = profile.get("allowedRoles")
    if not isinstance(roles, list) or not roles or any(x not in ROLES for x in roles):
        fail(503, "connection-not-configured", "연결의 역할별 접근 정책이 필요합니다.")
    if profile["kind"] == "confluence" and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile.get("spaceKey", "")):
        fail(503, "connection-not-configured", "수집 범위가 구성되지 않았습니다.")
    if profile.get("secretRef") and not SECRET_ARN.fullmatch(profile["secretRef"]):
        fail(503, "connection-not-configured", "정확한 Secrets Manager ARN이 필요합니다.")
    if profile["kind"] == "git":
        paths, revision, repository = profile.get("paths"), profile.get("ref", ""), profile.get("repository", "")
        if (profile.get("provider") not in {"github", "gitlab"}
                or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision)
                or not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", repository)
                or not isinstance(paths, list) or not 1 <= len(paths) <= 50):
            fail(503, "connection-not-configured", "고정 커밋과 승인된 저장소 파일 경로가 필요합니다.")
        if len(set(paths)) != len(paths):
            fail(503, "connection-not-configured", "중복된 Git 수집 경로입니다.")
        for path in paths:
            if (not isinstance(path, str) or len(path) > 300 or path.startswith("/")
                    or any(segment in {"", ".", ".."} for segment in path.split("/"))
                    or not re.fullmatch(r"[A-Za-z0-9_./-]+\.(md|txt|json|tsx|ts|jsx|js|css)", path)):
                fail(503, "connection-not-configured", "허용된 텍스트 파일 경로가 필요합니다.")
    return profile


def connection(host, connection_id, project_id, kind):
    profile = profiles(host).get(connection_id)
    validate_profile(profile)
    if profile["kind"] != kind or project_id not in profile.get("allowedProjectIds", []):
        fail(403, "connection-forbidden", "프로젝트에 허용되지 않은 연결입니다.")
    return profile


def profile_hash(profile):
    # Credential rotation does not grant a new scope; scope changes invalidate jobs.
    return _hash({k: v for k, v in profile.items() if k not in {"secretRef"} and not callable(v)})


def server_token(secret_ref):
    """Resolve an exact server-owned ARN using the existing workspace provider."""
    if not isinstance(secret_ref, str) or not SECRET_ARN.fullmatch(secret_ref):
        fail(503, "connection-not-configured", "정확한 Secrets Manager ARN이 필요합니다.")
    from workspace.git_service import secret_token
    try:
        return secret_token({"secretArn": secret_ref})
    except Exception:
        fail(503, "credentials-unavailable", "서버 연결 자격 증명을 읽지 못했습니다.")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_transport(token_provider=None):
    opener = build_opener(_NoRedirect())
    provider = token_provider or server_token
    tokens = {}

    def fetch(profile, path, params):
        headers = {"Accept": "application/json"}
        if profile.get("secretRef"):
            if profile["secretRef"] not in tokens:
                tokens[profile["secretRef"]] = provider(profile["secretRef"])
            token = tokens[profile["secretRef"]]
            if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
                fail(503, "collector-failed", "연결 자격 증명을 확인하지 못했습니다.")
            headers["Authorization"] = "Bearer " + token
        request = Request(profile["baseUrl"].rstrip("/") + path + "?" + urlencode(params), headers=headers)
        try:
            with opener.open(request, timeout=10) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                fail(422, "collection-limit", "수집 응답이 크기 제한을 초과했습니다.")
            return json.loads(raw)
        except Exception:
            fail(503, "collector-failed", "승인된 연결에서 자료를 읽지 못했습니다.")
    return fetch


def collect(profile, transport=None, token_provider=None, effective_acl_resolver=None):
    validate_profile(profile)
    fetch = transport or http_transport(token_provider)
    # Bound total work as well as pages/bytes. Custom transports own their own
    # per-call deadline; the real transport has a ten-second socket timeout.
    started, requests = time.monotonic(), 0
    def bounded_fetch(profile, path, params):
        nonlocal requests
        requests += 1
        if requests > 150 or time.monotonic() - started > 90:
            fail(422, "collection-limit", "수집 요청 수 또는 실행 시간 제한을 초과했습니다.")
        return fetch(profile, path, params)
    if profile["kind"] == "git":
        return collect_git(profile, bounded_fetch)
    max_pages = profile.get("maxPages", 4)
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        fail(503, "connection-not-configured", "수집 페이지 제한이 올바르지 않습니다.")
    documents, complete, seen = [], True, set()
    try:
        for page_number in range(max_pages):
            page = bounded_fetch(profile, "/rest/api/content", {
                "spaceKey": profile["spaceKey"], "type": "page", "status": "current",
                "expand": "body.storage,version,ancestors", "start": page_number * 25, "limit": 25})
            rows = page.get("results")
            if not isinstance(rows, list) or len(rows) > 25:
                fail(422, "collector-invalid", "수집 응답 형식이 올바르지 않습니다.")
            for row in rows:
                identifier = str(row["id"])
                if not re.fullmatch(r"[0-9]{1,40}", identifier) or identifier in seen:
                    fail(422, "collector-invalid", "수집 문서 식별자가 올바르지 않습니다.")
                seen.add(identifier)
                parser = _Text()
                html = row["body"]["storage"]["value"]
                if not isinstance(html, str) or len(html.encode()) > 100_000:
                    fail(422, "collection-limit", "문서가 수집 크기 제한을 초과했습니다.")
                parser.feed(html)
                acl = effective_roles(profile, row, bounded_fetch, effective_acl_resolver)
                documents.append({"id": identifier, "title": row["title"],
                                  "content": " ".join("".join(parser.parts).split()),
                                  "revision": str(row["version"]["number"]), "kind": "document",
                                  "sourceUrl": profile["baseUrl"].rstrip("/") + "/pages/viewpage.action?pageId=" + identifier,
                                  "allowedRoles": acl})
            more = bool(page.get("_links", {}).get("next"))
            if not more:
                break
            if page_number == max_pages - 1 or len(documents) >= MAX_DOCUMENTS:
                complete = False
                break
    except CollaborationError:
        raise
    except Exception:
        fail(503, "collector-failed", "수집을 완료하지 못했습니다.")
    return {"documents": documents, "complete": complete, "collector": "confluence-rest-bounded",
            "permissionPolicy": "page-and-all-ancestor-restrictions-or-trusted-effective-resolver"}


def effective_roles(profile, row, fetch, resolver):
    if callable(resolver):
        result = resolver(profile=profile, document=row)
        if not isinstance(result, dict) or result.get("effective") is not True:
            return []
        roles = result.get("allowedRoles")
        return [role for role in roles if role in profile["allowedRoles"]] if isinstance(roles, list) else []
    # A missing expansion cannot prove this page has no ancestors. Deny even
    # if a page body happens to include a workbenchAllowedRoles property.
    ancestors = row.get("ancestors")
    if not isinstance(ancestors, list) or len(ancestors) > 20:
        return []
    identifiers = [str(row["id"])]
    for ancestor in ancestors:
        identifier = str(ancestor.get("id", "")) if isinstance(ancestor, dict) else ""
        if not re.fullmatch(r"[0-9]{1,40}", identifier) or identifier in identifiers:
            return []
        identifiers.append(identifier)
    for identifier in identifiers:
        restriction = fetch(profile, f"/rest/api/content/{identifier}/restriction/byOperation/read",
                            {"expand": "restrictions.user,restrictions.group", "limit": 1})
        policy = restriction.get("restrictions", {})
        user, group = policy.get("user", {}), policy.get("group", {})
        if not (user.get("size") == 0 and group.get("size") == 0
                and user.get("results") == [] and group.get("results") == []
                and not user.get("_links", {}).get("next") and not group.get("_links", {}).get("next")):
            return []
    return list(profile["allowedRoles"])


def collect_git(profile, fetch):
    docs, size = [], 0
    for path in profile["paths"]:
        repository = profile["repository"]
        if profile["provider"] == "github":
            route = "/repos/" + quote(repository, safe="/") + "/contents/" + quote(path, safe="/")
        else:
            route = "/projects/" + quote(repository, safe="") + "/repository/files/" + quote(path, safe="")
        try:
            result = fetch(profile, route, {"ref": profile["ref"]})
            expected_path = result.get("path") if profile["provider"] == "github" else result.get("file_path")
            if (expected_path != path or result.get("encoding") != "base64"
                    or result.get("type", "file") != "file" or result.get("submodule_git_url")
                    or type(result.get("size")) is not int or not 0 <= result["size"] <= 100_000
                    or not isinstance(result.get("content"), str) or len(result["content"]) > 150_000):
                fail(422, "collector-invalid", "Git 파일 응답을 확인하지 못했습니다.")
            raw = base64.b64decode("".join(result["content"].split()), validate=True)
            blob_hash = result.get("sha") if profile["provider"] == "github" else result.get("blob_id")
            header = b"blob " + str(len(raw)).encode() + b"\x00" + raw
            expected_hash = hashlib.sha1(header).hexdigest() if isinstance(blob_hash, str) and len(blob_hash) == 40 else hashlib.sha256(header).hexdigest()
            if len(raw) != result["size"] or expected_hash != blob_hash:
                fail(422, "collector-invalid", "Git 파일 크기 또는 객체 해시가 일치하지 않습니다.")
            content = raw.decode("utf-8")
            if "\x00" in content:
                fail(422, "collector-invalid", "텍스트 Git 파일만 수집할 수 있습니다.")
            size += len(raw)
            if size > MAX_RESPONSE_BYTES:
                fail(422, "collection-limit", "Git 수집 크기 제한을 초과했습니다.")
            docs.append({"id": "git-" + hashlib.sha256(path.encode()).hexdigest()[:40],
                         "title": path, "content": content, "revision": profile["ref"], "kind": "code",
                         "allowedRoles": list(profile["allowedRoles"])})
        except CollaborationError:
            raise
        except Exception:
            fail(503, "collector-failed", "승인된 Git 파일을 읽지 못했습니다.")
    return {"documents": docs, "complete": True, "collector": "git-pinned-paths",
            "coverageScope": "configured-files-only", "permissionPolicy": "server-approved-repository-roles"}
