"""브리지 Lambda — Two-Plane VPC 안에서만 동작하는 얇은 프록시.

클라우드 플레인 Lambda(VPC 밖, 인터넷·Bedrock 접근)가 이 함수를 invoke 하면
  op=onprem  : 격리 서브넷의 온프렘 ECS(내부 ALB)로 HTTP POST (PLANE_TOKEN 이 있으면 X-Plane-Token 동봉)
  op=neptune : Neptune openCypher HTTPS 엔드포인트로 질의
  op=health  : 두 경로 점검
NAT 없음 — 이 함수는 AWS API도 인터넷도 호출하지 않는다. IAM invoke 권한이 유일한 입구다.
로그에 요청 본문(프롬프트·임베딩)을 남기지 않는다 (§12.3).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

ONPREM_URL = os.environ.get("ONPREM_URL", "")
NEPTUNE = os.environ.get("NEPTUNE_ENDPOINT", "")
PLANE_TOKEN = os.environ.get("PLANE_TOKEN", "")  # 온프렘 서비스 공유 비밀 (Secrets Manager 주입)


def _onprem_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if PLANE_TOKEN:
        h["X-Plane-Token"] = PLANE_TOKEN
    return h


def _post_json(url: str, data: bytes, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"errorMessage": f"HTTP {e.code}: {e.read().decode()[:300]}"}


def handler(event, context):
    op = event.get("op")
    if op == "onprem":
        return _post_json(ONPREM_URL + event["path"],
                          json.dumps(event.get("body") or {}, ensure_ascii=False).encode(),
                          _onprem_headers(), 20)
    if op == "neptune":
        data = urllib.parse.urlencode({"query": event["cypher"],
                                       "parameters": json.dumps(event.get("params") or {})}).encode()
        return _post_json(f"https://{NEPTUNE}:8182/openCypher", data,
                          {"Content-Type": "application/x-www-form-urlencoded"}, 55)
    if op == "health":
        out = {"onprem": None, "neptune": None}
        try:
            req = urllib.request.Request(ONPREM_URL + "/health", headers=_onprem_headers(), method="GET")
            out["onprem"] = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        except Exception as e:
            out["onprem"] = {"error": str(e)[:200]}
        try:
            out["neptune"] = json.loads(urllib.request.urlopen(f"https://{NEPTUNE}:8182/status", timeout=5).read().decode())
        except Exception as e:
            out["neptune"] = {"error": str(e)[:200]}
        return out
    return {"errorMessage": f"unknown op: {op}"}
