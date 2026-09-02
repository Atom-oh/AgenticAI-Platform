"""Bedrock 단기 API 키(Bearer) 발급 — IAM 자격증명으로 SigV4 presign (SPEC v2 §4-2, §16).

bedrock-mantle(OpenAI 호환) 엔드포인트는 SigV4 헤더가 아니라 `Authorization: Bearer <key>` 를 쓴다.
장기 API 키가 Secrets Manager 에 없으면(§16 확인: 계정에 없음) 현재 IAM 자격으로 12시간 단기 키를 만든다.

  presigned = SigV4QueryAuth(POST https://bedrock.amazonaws.com/?Action=CallWithBearerToken, service="bedrock", region, expires=43200)
  token     = "bedrock-api-key-" + base64(presigned_url_without_scheme + "&Version=1")

실측(2026-09-02, bedrock-mantle us-west-2): 호스트는 **글로벌** `bedrock.amazonaws.com` 이어야 하고(리전 호스트는
"Invalid bearer token"), 서명 대상 메서드는 **POST** 다(GET 으로 서명하면 서버가 "Canonical String … should have been 'POST /
Action=CallWithBearerToken…'" 으로 거부). 리전은 서명(credential scope)에만 들어간다 — 공식 aws-bedrock-token-generator 와 같다.
호출 주체(IAM 역할)에는 `bedrock:CallWithBearerToken` 권한이 필요하다.
토큰은 프로세스 안에서 11시간(자격증명이 먼저 만료되면 그 5분 전까지) 캐시한다. 비밀값은 로그에 남기지 않는다.
런타임 의존: botocore 만 (Lambda Python 3.12 기본 제공).
"""
from __future__ import annotations

import base64
import os
import threading
import time
from typing import Optional

AUTH_PREFIX = "bedrock-api-key-"
TOKEN_VERSION = "&Version=1"
TOKEN_DURATION_SEC = 43200          # 12h — 서비스가 허용하는 단기 키 최대 수명
CACHE_TTL_SEC = 11 * 3600           # 11h 뒤 재발급
SERVICE = "bedrock"
DEFAULT_REGION = "us-west-2"        # bedrock-mantle 리전 (§4-2) — 서명 scope 에만 쓰인다
DEFAULT_HOST = "bedrock.amazonaws.com"   # 글로벌 호스트 (실측: 리전 호스트는 "Invalid bearer token")
SIGN_METHOD = "POST"                # 실측: 서버가 POST 캐노니컬 요청으로 검증한다

_lock = threading.Lock()
_cache: dict = {}                   # (region, host) -> {"token": str, "expires": float}


def default_host(region: str) -> str:
    """서명 대상 호스트. 기본은 글로벌 bedrock.amazonaws.com, 필요하면 BEDROCK_TOKEN_HOST 로 교체."""
    return os.environ.get("BEDROCK_TOKEN_HOST", "") or DEFAULT_HOST


def assemble_token(presigned_url: str) -> str:
    """presigned URL → Bearer 토큰 문자열. 순수 함수 (네트워크·자격증명 없음, 단위테스트 대상)."""
    without_scheme = presigned_url.split("://", 1)[1] if "://" in presigned_url else presigned_url
    return AUTH_PREFIX + base64.b64encode((without_scheme + TOKEN_VERSION).encode("utf-8")).decode("ascii")


def decode_token(token: str) -> str:
    """토큰 → 원래의 (scheme 없는) presigned URL + Version 접미. 검증·테스트용."""
    if not token.startswith(AUTH_PREFIX):
        raise ValueError("bedrock-api-key- 접두어가 없습니다")
    return base64.b64decode(token[len(AUTH_PREFIX):].encode("ascii")).decode("utf-8")


def presign(credentials, region: str, host: Optional[str] = None, expires: int = TOKEN_DURATION_SEC) -> str:
    """POST https://{host}/?Action=CallWithBearerToken 을 SigV4 쿼리 서명한 URL 을 돌려준다 (네트워크 없음)."""
    from botocore.auth import SigV4QueryAuth
    from botocore.awsrequest import AWSRequest

    host = host or default_host(region)
    req = AWSRequest(method=SIGN_METHOD, url=f"https://{host}/", params={"Action": "CallWithBearerToken"},
                     headers={"host": host})
    SigV4QueryAuth(credentials, SERVICE, region, expires=expires).add_auth(req)
    return req.url


def _credentials():
    import botocore.session
    creds = botocore.session.get_session().get_credentials()
    if creds is None:
        raise RuntimeError("AWS 자격증명을 찾을 수 없어 단기 Bedrock API 키를 발급할 수 없습니다")
    return creds


def _credential_expiry(creds) -> Optional[float]:
    """임시 자격증명(Lambda 역할 등)의 만료 epoch. 없으면 None."""
    exp = getattr(creds, "_expiry_time", None)
    if exp is None:
        return None
    try:
        return exp.timestamp()
    except Exception:  # noqa: BLE001 — 형식이 다르면 만료를 모른다고 본다
        return None


def mint(region: str = DEFAULT_REGION, host: Optional[str] = None) -> dict:
    """새 토큰을 발급한다. 반환 {"token", "expiresAt", "source": "sigv4-short-term", "region", "host"} — 토큰은 로그 금지."""
    creds = _credentials()
    frozen = creds.get_frozen_credentials() if hasattr(creds, "get_frozen_credentials") else creds
    host = host or default_host(region)
    url = presign(frozen, region, host=host)
    now = time.time()
    expires = now + CACHE_TTL_SEC
    cred_exp = _credential_expiry(creds)
    if cred_exp is not None:
        expires = min(expires, cred_exp - 300)
    return {"token": assemble_token(url), "expiresAt": expires, "source": "sigv4-short-term",
            "region": region, "host": host}


def get_token(region: str = DEFAULT_REGION, host: Optional[str] = None) -> str:
    """캐시된 단기 토큰(11h 또는 자격증명 만료 5분 전까지)."""
    host = host or default_host(region)
    key = (region, host)
    with _lock:
        ent = _cache.get(key)
        if ent and ent["expires"] - 60 > time.time():
            return ent["token"]
        ent = mint(region, host)
        _cache[key] = {"token": ent["token"], "expires": ent["expiresAt"]}
        return ent["token"]


def reset_cache() -> None:
    with _lock:
        _cache.clear()
