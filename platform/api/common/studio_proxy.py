# platform/api/common/studio_proxy.py
"""uiux-studio(자매 데모) 자산 레지스트리 프록시 — 자산 조회·등록·에이전트 프리셋만. 시안·잡은 플랫폼 소유(studio.store).
CloudFront OAC 때문에 본문 있는 요청은 x-amz-content-sha256 이 필수다 (없으면 403)."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

STUDIO_URL = os.environ.get("STUDIO_URL", "https://d4zwmnh2s47e9.cloudfront.net")


def studio(method: str, path: str, token: str = "", body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if data is not None:
        headers["x-amz-content-sha256"] = hashlib.sha256(data).hexdigest()
    if token:
        headers["x-hana-auth"] = token
    req = urllib.request.Request(STUDIO_URL + path, data=data, headers=headers, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except urllib.error.HTTPError as e:
        try:
            return {"error": json.loads(e.read().decode()).get("error", str(e.code))}
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def studio_get(path: str) -> dict:
    return studio("GET", path)


def _content(asset_id: str) -> str:
    r = studio_get(f"/api/assets/content?asset_id={urllib.parse.quote(asset_id)}")
    c = r.get("content", r) if isinstance(r, dict) else r
    if isinstance(c, dict) and "content" in c:
        c = c["content"]
    return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)


def asset_text(asset_ids: list) -> str:
    blocks = []
    for aid in asset_ids[:20]:
        try:
            blocks.append(f"### 자산 {aid}\n{_content(aid)[:4000]}")
        except Exception as e:  # noqa: BLE001 — 자산 하나가 실패해도 생성은 계속
            blocks.append(f"### 자산 {aid}\n(조회 실패: {str(e)[:80]})")
    return "\n\n".join(blocks)


def agent_preset(agent_id: str) -> str:
    if not agent_id:
        return ""
    raw = _content(agent_id)
    try:
        return str(json.loads(raw).get("system", raw))
    except Exception:
        return raw
