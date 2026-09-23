"""AgentCore Gateway(MCP, IAM 인바운드) → Strands 도구.

- 인바운드 인증: SigV4 (서비스명 'bedrock-agentcore'). httpx.Auth 구현이 요청마다 다시 서명한다.
  자격증명은 botocore 세션(런타임 실행 역할 / 로컬은 환경변수·프로파일)에서 가져오며 갱신형 자격증명을 그대로 따른다.
- 전송: MCP streamable HTTP (mcp.client.streamable_http.streamable_http_client) 에 서명 httpx.AsyncClient 를 주입.
- 도구 목록은 '<target>___<tool>' 형태(예: platform___list_regulations)로 온다. 에이전트 명세 allowedTools 는 bare 이름이므로
  둘 다 받아 필터링하고, 모델에는 bare 이름으로 노출한다(시스템 프롬프트가 bare 이름을 가리킨다). 서버 호출은 원래 이름.

환경변수: GATEWAY_URL(필수), AWS_REGION(기본 ap-northeast-2).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import httpx

log = logging.getLogger("agents.mcp_gateway")

SERVICE_NAME = "bedrock-agentcore"
# 서명에 포함할 헤더 — 전송 중 바뀔 수 있는 헤더(connection, user-agent, accept-encoding 등)는 제외한다.
SIGNED_HEADERS = {"content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"}
TOOL_NAME_SEP = "___"

class GatewayCleanupFailed(RuntimeError):
    pass


def bare_name(name: str) -> str:
    """'platform___list_regulations' → 'list_regulations' (이미 bare 면 그대로)."""
    return (name or "").split(TOOL_NAME_SEP)[-1]


def gateway_url() -> str:
    url = os.environ.get("GATEWAY_URL", "").strip()
    if not url:
        raise RuntimeError("GATEWAY_URL is not set — Gateway(MCP) tools unavailable")
    return url


def region_name() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"


class SigV4Auth(httpx.Auth):
    """요청마다 SigV4 서명을 다시 계산하는 httpx.Auth. 본문은 서명에 필요하므로 스트리밍 본문도 먼저 읽는다."""

    requires_request_body = True

    def __init__(self, region: Optional[str] = None, service: str = SERVICE_NAME, botocore_session: Any = None):
        import botocore.session

        self._region = region or region_name()
        self._service = service
        self._session = botocore_session or botocore.session.get_session()

    def _frozen_credentials(self):
        creds = self._session.get_credentials()
        if creds is None:
            raise RuntimeError("AWS credentials not found (runtime execution role / AWS_* env) — cannot sign Gateway request")
        return creds.get_frozen_credentials()

    def sign(self, method: str, url: str, headers: dict, body: bytes) -> dict:
        """서명 헤더(Authorization, X-Amz-Date, X-Amz-Security-Token)를 계산해 반환한다."""
        from botocore.auth import SigV4Auth as _Signer
        from botocore.awsrequest import AWSRequest

        to_sign = {k: v for k, v in headers.items() if k.lower() in SIGNED_HEADERS}
        req = AWSRequest(method=method, url=url, data=body or b"", headers=to_sign)
        _Signer(self._frozen_credentials(), self._service, self._region).add_auth(req)
        out = {}
        for k in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token", "X-Amz-Content-SHA256"):
            v = req.headers.get(k)
            if v:
                out[k] = v
        return out

    def auth_flow(self, request: httpx.Request):
        body = request.content if request.content is not None else b""
        signed = self.sign(request.method, str(request.url), dict(request.headers), body)
        for k, v in signed.items():
            request.headers[k] = v
        yield request


def make_transport(url: Optional[str] = None, region: Optional[str] = None, *, timeout: float = 30.0,
                   read_timeout: float = 120.0):
    """MCPClient(transport_callable=...) 에 넘길 호출 가능 객체. 매 start() 마다 새 서명 클라이언트를 만든다."""
    from mcp.client.streamable_http import streamable_http_client

    target = url or gateway_url()
    auth = SigV4Auth(region)

    @asynccontextmanager
    async def _transport():
        async with httpx.AsyncClient(auth=auth, follow_redirects=True,
                                     timeout=httpx.Timeout(timeout, read=read_timeout)) as http:
            async with streamable_http_client(target, http_client=http) as streams:
                yield streams

    return _transport


def make_client(url: Optional[str] = None, region: Optional[str] = None, startup_timeout: int = 30):
    """Strands MCPClient (아직 start 하지 않은 상태)."""
    from strands.tools.mcp import MCPClient

    return MCPClient(make_transport(url, region), startup_timeout=startup_timeout,
                     application_name="bank-platform-agents")


def filter_tool_names(discovered: Iterable[str], allowed: Optional[Sequence[str]]) -> List[str]:
    """Select explicitly allowed tools; an empty list grants no tools."""
    allowed_set = {a for a in (allowed or [])}
    allowed_bare = {a for a in allowed_set if TOOL_NAME_SEP not in a}
    out = []
    for name in discovered:
        if allowed_set and (name in allowed_set or bare_name(name) in allowed_bare):
            out.append(name)
    return out


def missing_tool_names(discovered: Iterable[str], allowed: Optional[Sequence[str]]) -> List[str]:
    names = set(discovered)
    bare = {bare_name(name) for name in names}
    return sorted(name for name in (allowed or []) if
                  (name not in names if TOOL_NAME_SEP in name else name not in bare))


def load_tools(client: Any, allowed: Optional[Sequence[str]]) -> Tuple[List[Any], List[str]]:
    """start 된 MCPClient 에서 도구를 나열해 allowed 로 필터링하고 bare 이름으로 노출하는 AgentTool 목록을 만든다.

    반환: (tools, discovered_names). discovered_names 는 서버가 준 원래 이름 전체(진단용).
    """
    from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

    discovered: List[str] = []
    raw: List[Any] = []
    token = None
    for _ in range(20):  # 페이지 상한 (무한 루프 방지)
        page = client.list_tools_sync(token)
        for t in page:
            discovered.append(t.tool_name)
            raw.append(t)
        token = getattr(page, "pagination_token", None)
        if not token:
            break
    keep = set(filter_tool_names(discovered, allowed))
    counts = {}
    for name in keep:
        counts[bare_name(name)] = counts.get(bare_name(name), 0) + 1
    tools = [MCPAgentTool(t.mcp_tool, client,
             name_override=t.tool_name if counts[bare_name(t.tool_name)] > 1 else bare_name(t.tool_name))
             for t in raw if t.tool_name in keep]
    missing = missing_tool_names(keep, allowed)
    if missing:
        log.warning("gateway tools missing for spec: %s (discovered=%d)", missing, len(discovered))
    return tools, discovered


def open_tools(allowed: Optional[Sequence[str]], url: Optional[str] = None, region: Optional[str] = None):
    """편의 함수: 클라이언트 생성 + start + 도구 로드. 반환 (client, tools, discovered). 호출자가 client.stop(None, None, None)."""
    if not allowed or any(not isinstance(name, str) or not name.strip() or "*" in name for name in allowed):
        raise ValueError("An explicit nonempty tool allowlist is required")
    client = make_client(url, region)
    try:
        client.start()
        tools, discovered = load_tools(client, allowed)
    except Exception:
        try:
            client.stop(None, None, None)
        except Exception:
            raise GatewayCleanupFailed("Gateway setup cleanup failed") from None
        raise
    return client, tools, discovered
