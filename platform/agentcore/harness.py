"""AgentCore Harness 래퍼 — 에이전트 생성(멱등)·조회·스트리밍 호출.

환경변수: HARNESS_ROLE_ARN(실행 역할), GATEWAY_ARN(bank-platform-tools), SKILLS_S3_URI(s3://bucket/skills/), AWS_REGION.
Harness API는 최신 boto3가 필요하다 — deploy.sh가 boto3를 배포 패키지에 동봉한다.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
HARNESS_ROLE_ARN = os.environ.get("HARNESS_ROLE_ARN", "")
GATEWAY_ARN = os.environ.get("GATEWAY_ARN", "")
SKILLS_S3_URI = os.environ.get("SKILLS_S3_URI", "")
PLATFORM_TAG = {"platform": "bank-agentic-platform"}

_ctl = None
_data = None


def ctl():
    global _ctl
    if _ctl is None:
        _ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _ctl


def data():
    global _data
    if _data is None:
        _data = boto3.client("bedrock-agentcore", region_name=REGION)
    return _data


def list_platform_harnesses() -> list[dict]:
    """플랫폼이 만든 Harness 목록 (이름 접두어 'bank_' 또는 태그 기준은 list 응답에 없어 이름으로 구분)."""
    out = []
    token = None
    while True:
        kw = {"maxResults": 50}
        if token:
            kw["nextToken"] = token
        r = ctl().list_harnesses(**kw)
        out += [h for h in r.get("harnesses", []) if str(h.get("harnessName") or h.get("harnessId", "")).startswith("bank_")]
        token = r.get("nextToken")
        if not token:
            break
    return out


def find_harness(name: str) -> dict | None:
    for h in list_platform_harnesses():
        if h.get("harnessName") == name or str(h.get("harnessId", "")).startswith(name + "-"):
            return ctl().get_harness(harnessId=h["harnessId"])["harness"]
    return None


def build_config(spec: dict) -> dict:
    """에이전트 명세 → create_harness 파라미터."""
    tools = []
    if GATEWAY_ARN and spec.get("allowedTools"):
        tools.append({"type": "agentcore_gateway", "name": "bank_platform_tools",
                      "config": {"agentCoreGateway": {"gatewayArn": GATEWAY_ARN, "outboundAuth": {"awsIam": {}}}}})
    skills = []
    if SKILLS_S3_URI and spec.get("skills"):
        skills = [{"s3": {"uri": f"{SKILLS_S3_URI.rstrip('/')}/{name}/"}} for name in spec["skills"]]
    cfg = {
        "harnessName": f"bank_{spec['name']}",
        "executionRoleArn": HARNESS_ROLE_ARN,
        "model": {"bedrockModelConfig": {"modelId": spec.get("model", "global.anthropic.claude-sonnet-5"),
                                         "maxTokens": int(spec.get("maxTokens", 2048))}},  # Claude 5: temperature 미지원
        "systemPrompt": [{"text": spec["systemPrompt"]}],
        "tools": tools,
        "skills": skills,
        "allowedTools": [f"bank_platform_tools___{t}" for t in spec.get("allowedTools", [])] or ["*"],
        "maxIterations": int(spec.get("maxIterations", 12)),
        "maxTokens": 8192,
        "timeoutSeconds": 120,
        "tags": {**PLATFORM_TAG, "scenario": str(spec.get("scenario", "custom")), "createdBy": str(spec.get("createdBy", "platform"))[:64]},
    }
    if spec.get("memory"):
        cfg["memory"] = {"managedMemoryConfiguration": {"strategies": ["SEMANTIC"], "eventExpiryDuration": 30}}
    # memory 미사용이면 키를 생략한다 (구형 botocore 모델에는 'disabled'가 없다)
    return cfg


def ensure_harness(spec: dict) -> dict:
    """이름 기준 멱등 생성. 이미 있으면 그대로 반환 (설정 변경은 update_harness)."""
    existing = find_harness(f"bank_{spec['name']}")
    if existing:
        return existing
    cfg = build_config(spec)
    cfg["clientToken"] = str(uuid.uuid4()) + "-bank"  # 최소 길이 33
    r = ctl().create_harness(**cfg)
    hid = r.get("harnessId") or r.get("harness", {}).get("harnessId")
    # READY 대기 (최대 90초) — 생성 직후 호출하면 실패한다
    for _ in range(30):
        h = ctl().get_harness(harnessId=hid)["harness"]
        if h.get("status") in ("READY", "ACTIVE"):
            return h
        if h.get("status") in ("FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"harness {hid} status {h.get('status')}: {h.get('statusReason', '')}")
        time.sleep(3)
    return ctl().get_harness(harnessId=hid)["harness"]


def invoke_stream(harness_arn: str, text: str, session_id: str | None = None):
    """invoke_harness 스트림을 (event_type, payload) 튜플로 정규화해 yield. 마지막에 usage/stop을 담은 'meta'."""
    sid = session_id or (uuid.uuid4().hex + "-session")
    r = data().invoke_harness(harnessArn=harness_arn, runtimeSessionId=sid,
                              messages=[{"role": "user", "content": [{"text": text}]}])
    usage, stop = {}, ""
    tool_name = None
    tool_buf: list[str] = []
    for ev in r["stream"]:
        if "contentBlockStart" in ev:
            start = ev["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                tool_name = start["toolUse"].get("name", "")
                tool_buf = []
                yield ("tool_start", {"name": tool_name, "toolUseId": start["toolUse"].get("toolUseId", "")})
        elif "contentBlockDelta" in ev:
            delta = ev["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                yield ("text", delta["text"])
            elif "toolUse" in delta:
                tool_buf.append(delta["toolUse"].get("input", ""))
        elif "contentBlockStop" in ev:
            if tool_name:
                yield ("tool_input", {"name": tool_name, "input": "".join(tool_buf)[:2000]})
                tool_name = None
        elif "messageStop" in ev:
            stop = ev["messageStop"].get("stopReason", "")
        elif "metadata" in ev:
            usage = ev["metadata"].get("usage", {}) or usage
        elif any(k in ev for k in ("validationException", "internalServerException", "runtimeClientError",
                                   "throttlingException", "accessDeniedException")):
            yield ("error", json.dumps(ev, ensure_ascii=False, default=str)[:400])
    yield ("meta", {"usage": usage, "stopReason": stop, "sessionId": sid})
