"""One claimed Runtime attempt, serialized heartbeat and bounded immutable transfers."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time

from ontology_runtime.capability import AuthorizationDenied
from workspace.execution_ledger import CHUNK_BYTES, receipt_hash
from workspace.execution_source import encode


class ExecutionSession:
    def __init__(self, gateway, signer, event, session_id, *, clock=lambda: int(time.time() * 1000)):
        if event.get("runtimeSessionId") != session_id:
            raise AuthorizationDenied()
        self.gateway, self.signer, self.event, self.clock = gateway, signer, event, clock
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.failure, self.previous, self.receipts, self.sequence = False, None, [], 0
        self.job = self.call("claim", {}, "claim")
        if self.job["executionId"] != event["executionId"] or self.job["sessionId"] != session_id:
            raise AuthorizationDenied()
        if event.get("operation") != self.job["operation"]:
            raise AuthorizationDenied()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()

    def call(self, action, arguments, tag=None):
        with self.lock:
            if self.failure:
                raise RuntimeError("Execution heartbeat failed")
            if tag is None:
                self.sequence += 1;tag = action + "-" + str(self.sequence)
            op = hashlib.sha256((self.event["executionId"] + ":" + self.event["runtimeSessionId"] + ":" + tag).encode()).hexdigest()
            return self.gateway.call("ontology___execution", {"action": action, "arguments": arguments},
                                     capability=self.event["capability"], operation_id=op)

    def _heartbeat(self):
        while not self.stop.wait(self.job["heartbeatMs"] / 1000):
            try:self.call("heartbeat", {}, "heartbeat")
            except Exception:
                self.failure = True
                return

    def close(self):
        self.stop.set();self.thread.join(timeout=50)

    def read(self, action="open_manifest", arguments=None):
        handle = self.call(action, arguments or {})
        if handle["total"] > 32 * 1024 * 1024:
            raise ValueError("Input transfer limit")
        raw = bytearray()
        for index in range(handle["chunks"]):
            chunk = self.call("read_chunk", {"handle_id": handle["handleId"], "index": index})
            data = base64.b64decode(chunk["data"], validate=True)
            if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                raise AuthorizationDenied()
            raw.extend(data)
        if len(raw) != handle["total"] or hashlib.sha256(raw).hexdigest() != handle["sha256"]:
            raise AuthorizationDenied()
        return bytes(raw), {"key": handle["key"], "sha256": handle["sha256"], "size": handle["total"]}

    def upload(self, stage, name, value, *, binary=False, role=None):
        raw = value if binary else encode(value)
        handle = self.call("open_output", {"stage": stage, "name": name, "total": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest()})
        for index, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
            self.call("write_chunk", {"handle_id": handle["handleId"], "index": index,
                                      "data": base64.b64encode(raw[offset:offset + CHUNK_BYTES]).decode()})
        result = self.call("close_output", {"handle_id": handle["handleId"]})
        return {**result, **({"role": role} if role else {})}

    def stage(self, stage, inputs, outputs, result, service=None, *, status="ok"):
        body = {k: self.job[k] for k in ("executionId", "attemptId", "fence", "sessionId", "profileHash", "admissions")}
        body.update(schemaVersion=1, stage=stage, nonce=secrets.token_hex(24), iat=self.clock(), exp=self.job["deadlineAt"],
                    previous=self.previous, inputs=[{k: x[k] for k in ("key", "sha256", "size")} for x in inputs],
                    outputs=outputs, service=service or {"kind": "runtime", "sessionId": self.job["sessionId"]},
                    status=status, result=result)
        receipt = self.signer.sign(body)
        self.call("stage", {"receipt": receipt})
        self.previous = receipt_hash(receipt);self.receipts.append(self.previous)

    def intent(self, stage, kind, remaining_ms, max_tokens=None):
        args = {"stage": stage, "kind": kind, "min_remaining_ms": remaining_ms}
        if max_tokens is not None:args["max_tokens"] = max_tokens
        result = self.call("intent", args)
        if result.get("replayed"):
            raise RuntimeError("A recorded call never authorizes repeating paid work")
        return result["callId"]

    def outcome(self, identifier, *, usage=None, session_id=None, status="completed"):
        args = {"call_id": identifier, "status": status}
        if usage is not None:args["usage"] = usage
        if session_id is not None:args["service_session_id"] = session_id
        return self.call("outcome", args)

    def finish(self, manifest, status="succeeded"):
        self.stop.set()
        return self.call("finish", {"status": status, "result": {"manifestRef": manifest["key"],
            "manifestHash": manifest["sha256"], "receipts": self.receipts}})
