"""Source-only Runtime workflow over bounded B0 transfers and signed observations."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time

from ontology_runtime.capability import AuthorizationDenied
from workspace.execution_ledger import CHUNK_BYTES, receipt_hash
from workspace.execution_source import encode


class ExecutionWorkflow:
    def __init__(self, gateway, interpreter, signer, *, memory=None, clock=lambda: int(time.time() * 1000)):
        self.gateway, self.interpreter, self.signer, self.clock = gateway, interpreter, signer, clock
        self.memory = memory

    def analyze(self, event, runtime_session_id):
        if event.get("runtimeSessionId") != runtime_session_id:
            raise AuthorizationDenied()
        lock, stop, errors = threading.RLock(), threading.Event(), []
        def call(action, arguments, tag):
            with lock:
                if errors:
                    raise RuntimeError("Execution heartbeat failed")
                operation = hashlib.sha256((event["executionId"] + ":" + runtime_session_id + ":" + tag).encode()).hexdigest()
                return self.gateway.call("ontology___execution", {"action": action, "arguments": arguments},
                    capability=event["capability"], operation_id=operation)
        job = call("claim", {}, "claim")
        if job["executionId"] != event["executionId"] or job["sessionId"] != runtime_session_id:
            raise AuthorizationDenied()
        def heartbeat():
            while not stop.wait(job["heartbeatMs"] / 1000):
                try:
                    call("heartbeat", {}, "heartbeat")
                except Exception:
                    errors.append(True)
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        previous = None
        def upload(stage, name, value):
            raw = encode(value)
            handle = call("open_output", {"stage": stage, "name": name, "total": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest()}, "open-" + name)
            for index, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
                call("write_chunk", {"handle_id": handle["handleId"], "index": index,
                    "data": base64.b64encode(raw[offset:offset + CHUNK_BYTES]).decode()}, f"write-{name}-{index}")
            return call("close_output", {"handle_id": handle["handleId"]}, "close-" + name)
        def stage(name, inputs, outputs, service, result):
            nonlocal previous
            body = {key: job[key] for key in ("executionId", "attemptId", "fence", "sessionId", "profileHash", "admissions")}
            body.update(schemaVersion=1, stage=name, nonce=hashlib.sha256((job["attemptId"] + name).encode()).hexdigest(),
                        iat=self.clock(), exp=job["deadlineAt"], previous=previous, inputs=inputs, outputs=outputs,
                        service=service, status="ok", result=result)
            receipt = self.signer.sign(body)
            call("stage", {"receipt": receipt}, "stage-" + name)
            previous = receipt_hash(receipt)
            return previous
        try:
            handle = call("open_manifest", {}, "open-manifest")
            if handle["total"] > 4_500_000:
                raise ValueError("Input manifest exceeds the supported transfer")
            raw = bytearray()
            for index in range(handle["chunks"]):
                chunk = call("read_chunk", {"handle_id": handle["handleId"], "index": index}, f"read-manifest-{index}")
                data = base64.b64decode(chunk["data"], validate=True)
                if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                    raise AuthorizationDenied()
                raw.extend(data)
            if len(raw) != handle["total"] or hashlib.sha256(raw).hexdigest() != handle["sha256"]:
                raise AuthorizationDenied()
            manifest = json.loads(raw)
            if (manifest["operation"] != "source.analyze" or manifest["admissions"] != job["admissions"]
                    or manifest["tool"] != self.interpreter.config):
                raise AuthorizationDenied()
            source = {"key": handle["key"], "size": handle["total"], "sha256": handle["sha256"]}
            memory_scope = {"actor": job["actor"], "projectId": job["projectId"], "executionId": job["executionId"],
                            "attemptId": job["attemptId"], "resourcesHash": handle["sha256"], "iat": self.clock() // 1000}
            continuity = self.memory.read(memory_scope) if self.memory else {"events": [], "authority": False}
            context = upload("context", "context.json", {"manifestHash": handle["sha256"],
                             "admissions": job["admissions"], "continuity": continuity})
            receipts = [stage("context", [source], [context], {"kind": "runtime", "sessionId": runtime_session_id}, {})]
            intent = call("intent", {"stage": "analyze", "kind": "interpreter", "min_remaining_ms": 300_000}, "intent-analyze")
            if intent.get("replayed"):
                raise RuntimeError("An existing paid call cannot be repeated")
            result = self.interpreter.execute("analyze", manifest["payload"])
            call("outcome", {"call_id": intent["callId"], "status": "completed",
                 "service_session_id": result["execution"]["sessionId"]}, "outcome-analyze")
            output = upload("analyze", "analysis.json", result)
            final = upload("analyze", "result.json", {"schemaVersion": 1, "objects": [output]})
            receipts.append(stage("analyze", [context, source], [output, final], {"kind": "interpreter",
                "sessionId": result["execution"]["sessionId"], "taskId": intent["callId"], "exitCode": 0,
                "profile": job["toolProfiles"]["interpreter"]}, {"coverage": result["analysis"]["coverage"], "analysis": output}))
            if self.memory:
                self.memory.append(memory_scope, "analyzed", receipts[-1])
            # Serialize heartbeat with terminal completion; no background renewal races with publication.
            stop.set()
            return call("finish", {"status": "succeeded", "result": {"manifestRef": final["key"],
                        "manifestHash": final["sha256"], "receipts": receipts}}, "finish")
        finally:
            stop.set()
            thread.join(timeout=50)
