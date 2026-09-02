"""온프렘 플레인 클라이언트 (SPEC §3.1).

모드:
  bridge : BRIDGE_FN(같은 VPC의 브리지 Lambda) 경유 → 격리 서브넷 ECS 온프렘 서비스. 시연 표준.
  direct : ONPREM_URL 직접 HTTP (Lambda가 VPC 안에 있을 때).
  local  : 폴백 없음 — PlaneUnavailable. 온프렘 역할을 같은 Lambda에서 흉내내지 않는다.
           (개발용 로컬 폴백은 핸들러가 ALLOW_LOCAL_PLANE=1 일 때만 명시적으로 사용하고 UI에 표기)
"""
from __future__ import annotations

import json
import os
import urllib.request

import boto3

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
BRIDGE_FN = os.environ.get("BRIDGE_FN", "")
ONPREM_URL = os.environ.get("ONPREM_URL", "")
ALLOW_LOCAL = os.environ.get("ALLOW_LOCAL_PLANE", "") == "1"


class PlaneUnavailable(Exception):
    pass


def mode() -> str:
    if BRIDGE_FN:
        return "bridge"
    if ONPREM_URL:
        return "direct"
    return "local" if ALLOW_LOCAL else "none"


def label() -> str:
    return {"bridge": "온프렘 플레인 (격리 서브넷 ECS · 브리지 경유)",
            "direct": "온프렘 플레인 (격리 서브넷 ECS · 직접)",
            "local": "로컬 폴백 (개발용 — 분리 아님)",
            "none": "온프렘 플레인 미연결"}[mode()]


def bridge(op: str, **payload) -> dict:
    lam = boto3.client("lambda", region_name=REGION)
    r = lam.invoke(FunctionName=BRIDGE_FN, Payload=json.dumps({"op": op, **payload}).encode())
    body = json.loads(r["Payload"].read().decode() or "{}")
    if r.get("FunctionError") or "errorMessage" in body:
        raise PlaneUnavailable(str(body)[:300])
    return body


def call(path: str, body: dict, timeout: int = 20) -> dict:
    m = mode()
    if m == "bridge":
        return bridge("onprem", path=path, body=body)
    if m == "direct":
        req = urllib.request.Request(ONPREM_URL + path, data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    raise PlaneUnavailable("온프렘 플레인이 연결되어 있지 않습니다 (BRIDGE_FN/ONPREM_URL 미설정).")
