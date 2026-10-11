"""Measured, exactly selected model calls after private inspection and ledger reservation."""
from __future__ import annotations

import hashlib
import os

from design_loop.references import References
from engine import gate
from workspace.execution_source import encode


class DesignModel:
    def __init__(self, execution, manifest):
        self.execution, self.manifest = execution, manifest
        self.refs = References(manifest.get("snapshot", {}), manifest.get("pages", []))
        self.calls, self.boundaries = [], []

    def normalize(self, text):
        # Actual residual inspection is performed over the complete outgoing pair in generate().
        # This early check rejects malformed extractor input without calling any model.
        if not isinstance(text, str) or len(text.encode()) > 512 * 1024:
            raise ValueError("model-payload-invalid")
        return text

    def generate(self, system, user, on_token=None):
        payload = {"system": self.refs.encode(system), "user": self.refs.encode(user)}
        inspected = self.execution.call("inspect_model", payload)
        if inspected.get("clean") is not True or inspected.get("sha256") != hashlib.sha256(encode(payload)).hexdigest():
            raise ValueError("model-inspection-mismatch")
        # Refuse missing transport configuration before reserving a paid call.
        if not os.environ.get("BEDROCK_READ_TIMEOUT"):
            raise RuntimeError("single-attempt-model-transport-required")
        call_id = self.execution.intent("generate", "model", 180000, max_tokens=8192)
        text, usage, info = gate.generate(payload["system"], payload["user"], max_tokens=8192, route="bedrock",
            model_id=self.manifest["model"], purpose="design.execute", trace_id=self.execution.job["executionId"])
        self.execution.outcome(call_id, usage={key: usage[key] for key in ("inputTokens", "outputTokens")})
        self.calls.append(call_id)
        self.boundaries.append({"callId": call_id, "modelId": info["modelId"], "usage": usage,
                                "boundary": info["boundary"], "payloadHash": inspected["sha256"], "requestId": info.get("requestId")})
        return self.refs.decode(text)

    def dependencies(self):
        return {"generate": self.generate, "normalize": self.normalize,
                "model": self.manifest["model"], "profileHash": self.execution.job["profileHash"]}

    def service(self):
        if not self.calls:
            raise ValueError("model-observation-required")
        return {"kind": "model", "sessionId": self.execution.job["sessionId"], "taskId": self.calls[-1]}
