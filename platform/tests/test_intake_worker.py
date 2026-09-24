"""Worker `intake-image` task, vision derivative and bounded delivery (Task I8a)."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_intake_admission import env  # noqa: F401,E402
from test_intake_transcription import MODEL, VisionAdapter, flowchart_png  # noqa: E402
from intake_support import api  # noqa: F401,E402
from engine import gate  # noqa: E402
from intake import admission, derivative, imaging, review  # noqa: E402
from intake.admission import AdmissionError  # noqa: E402
from workspace.ontology_sources import asset_reference  # noqa: E402
from workspace.worker import Worker  # noqa: E402

OCR_TEXT = "자격 미충족 시 사유 화면"


def put_asset(env, data, name, identifier):
    owner = f"project:{env.pid}"
    key = env.api.storage.key_for(owner, "asset", identifier, "original" + Path(name).suffix)
    env.api.storage.put_blob_once(key, data, "application/octet-stream")
    row = env.api.storage.put(owner, "asset", {
        "id": identifier, "projectId": env.pid, "name": name, "uploadStatus": "stored", "parseStatus": "complete",
        "originalKey": key, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "importRevision": 1})
    return asset_reference(row)


def noise_png(side):
    buffer = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buffer, "PNG")
    return buffer.getvalue()


def worker_for(env, ocr_status="complete", loader=None):
    worker = Worker(storage=env.api.storage, ocr=lambda data: (OCR_TEXT if ocr_status == "complete" else "", ocr_status))
    worker.collaboration = env.api.collaboration
    worker.intake_denylist_loader = loader or env.api.intake_denylist_loader
    return worker


def run(env, ref, *, data_class="internal-non-sensitive", **options):
    queued = admission.request(env.api, env.scope(), ref, data_class=data_class, kind="image")
    assert queued["status"] == "queued" and queued["job"]["status"] == "queued"
    owner = f"project:{env.pid}"
    invoked = json.loads(env.api.lambda_client.calls[-1]["Payload"])
    assert invoked == {"owner": owner, "jobId": queued["job"]["id"]}
    outcome = worker_for(env, **options).handle(invoked)
    job = env.api.storage.get(owner, "job", queued["job"]["id"])
    decision = env.api.storage.get(owner, "adm_decision", queued["decisionId"])
    return outcome, job, decision


def test_request_job_worker_produces_a_pending_image_decision_with_a_vision_derivative(env):
    env.policy()
    env.grant("bob")
    ref = put_asset(env, flowchart_png(), "flow.png", "flow")
    outcome, job, decision = run(env, ref)
    assert outcome == {"status": "completed", "jobId": job["id"]}
    assert job["status"] == "completed" and job["result"] == {"decisionId": decision["id"], "status": "pending-review"}
    vision = decision["artifact"]["vision"]
    assert vision["format"] == "png" and vision["size"] <= 3_000_000 and vision["transform"] == []
    admitted = review.decide(env.api, env.scope("bob"), decision["id"], approve=True, reason="ok")
    assert admitted["status"] == "admitted"
    assert imaging.descriptor(env.api, env.scope(), decision["id"])["ocrText"] == OCR_TEXT


def test_public_image_with_provenance_is_admitted_by_the_worker(env):
    policy = env.policy()
    ref = put_asset(env, flowchart_png(), "flow.png", "flow")
    env.provenance(ref, policy, kind="fixture")
    _, job, decision = run(env, ref, data_class="synthetic")
    assert job["status"] == "completed" and decision["status"] == "admitted"


def test_unsupported_jpeg_color_profile_is_blocked_without_a_decision(env):
    env.policy()
    buffer = io.BytesIO()
    Image.new("CMYK", (16, 16), (0, 50, 100, 0)).save(buffer, "JPEG")
    ref = put_asset(env, buffer.getvalue(), "cmyk.jpg", "cmyk")
    outcome, job, decision = run(env, ref)
    assert outcome["status"] == "failed" and decision is None
    assert job["status"] == "failed" and job["errorCode"] == "intake-blocked"
    assert job["result"]["status"] == "blocked" and job["result"]["blocking"] == ["unsupported-color-profile"]


def test_incomplete_ocr_blocks(env):
    env.policy()
    ref = put_asset(env, flowchart_png(), "flow.png", "flow")
    _, job, decision = run(env, ref, ocr_status="partial")
    assert decision is None and job["result"]["blocking"] == ["ocr-incomplete"]


class ScopedSsm:
    """An SSM client under the Worker principal: only the exact granted parameter is readable."""

    def __init__(self, granted, value):
        self.granted, self.value = granted, value

    def get_parameter(self, Name, WithDecryption):
        if Name != self.granted or not WithDecryption:
            raise PermissionError("AccessDeniedException")
        return {"Parameter": {"Value": self.value}}


@pytest.mark.parametrize("case", ["clean", "missing", "denied"])
def test_worker_deny_list_access_under_its_principal(env, monkeypatch, case):
    env.policy()
    env.grant("bob")
    ref = put_asset(env, flowchart_png(), "flow.png", "flow-" + case)
    value = json.dumps([{"term": env.term, "kind": "org"}])
    monkeypatch.delenv("INTAKE_DENYLIST_KEY", raising=False)
    if case == "missing":
        monkeypatch.delenv("INTAKE_DENYLIST_PARAM", raising=False)
    else:
        monkeypatch.setenv("INTAKE_DENYLIST_PARAM", "/offline/intake/denylist")
    granted = "/offline/intake/denylist" if case == "clean" else "/offline/other"
    loader = lambda: derivative.load_denylist(ssm=ScopedSsm(granted, value))  # noqa: E731
    _, job, decision = run(env, ref, loader=loader)
    if case == "clean":
        assert job["status"] == "completed" and decision["status"] == "pending-review"
    else:
        assert decision is None and job["result"]["blocking"] == ["denylist-unavailable"]


def test_failure_after_the_atomic_commit_keeps_the_committed_outcome(env, monkeypatch):
    policy = env.policy()
    ref = put_asset(env, flowchart_png(), "flow.png", "flow")
    env.provenance(ref, policy, kind="fixture")
    storage = env.api.storage
    original = storage.put_many

    def lost_response(writes, checks=None, **kwargs):
        result = original(writes, checks, **kwargs)
        if any(w["kind"] == "adm_decision" for w in writes):
            raise RuntimeError("response lost after commit")
        return result

    monkeypatch.setattr(storage, "put_many", lost_response)
    outcome, job, decision = run(env, ref, data_class="synthetic")
    assert outcome == {"status": "completed", "jobId": job["id"]}
    assert job["status"] == "completed" and decision["status"] == "admitted"


def admitted_image(env, data, name="flow.png", identifier="flow"):
    env.policy()
    env.grant("bob")
    ref = put_asset(env, data, name, identifier)
    _, _, decision = run(env, ref)
    return review.decide(env.api, env.scope("bob"), decision["id"], approve=True, reason="ok")


def test_real_gate_accepts_the_produced_vision_derivative_and_refuses_missing_ocr(env, monkeypatch):
    decision = admitted_image(env, flowchart_png())
    adapter = VisionAdapter("ok")
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    _, image = imaging.vision_input(env.api, env.scope(), decision["id"])
    text, _, info = gate.generate_with_images("s", "u", [image], model_id=MODEL)
    assert text == "ok" and info["imageCount"] == 1
    with pytest.raises(gate.GateUnsupported):
        gate.generate_with_images("s", "u", [{**image, "ocrStatus": "partial"}], model_id=MODEL)


def test_oversize_source_is_downscaled_under_the_gate_limit(env):
    decision = admitted_image(env, noise_png(1100), "noise.png", "noise")
    vision = decision["artifact"]["vision"]
    assert decision["artifact"]["width"] == 1100 and vision["size"] <= 3_000_000
    assert vision["transform"] and vision["transform"][0].startswith("downscale-")


def test_runtime_delivery_in_bounded_chunks_with_revocation_between_chunks(env, monkeypatch):
    decision = admitted_image(env, noise_png(470), "noise.png", "noise")
    vision = decision["artifact"]["vision"]
    assert vision["size"] > 512 * 1024
    chunks, offset = [], 0
    while offset < vision["size"]:
        chunk = imaging.read_vision_chunk(env.api, env.scope(), decision["id"], offset)
        wire = {**chunk, "bytes": base64.b64encode(chunk["bytes"]).decode()}
        assert len(json.dumps(wire).encode()) <= 512 * 1024
        assert hashlib.sha256(chunk["bytes"]).hexdigest() == chunk["sha256"]
        chunks.append(chunk["bytes"])
        offset += len(chunk["bytes"])
    assembled = b"".join(chunks)
    assert len(chunks) > 1 and hashlib.sha256(assembled).hexdigest() == vision["sha256"]
    adapter = VisionAdapter("ok")
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    descriptor = imaging.descriptor(env.api, env.scope(), decision["id"])
    assert set(descriptor) == {"format", "ocrStatus", "ocrText", "visionSha256", "size"}
    gate.generate_with_images("s", "u", [{"format": "png", "bytes": assembled, "ocrStatus": descriptor["ocrStatus"],
                                          "ocrText": descriptor["ocrText"]}], model_id=MODEL)
    first = imaging.read_vision_chunk(env.api, env.scope(), decision["id"], 0)
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    with pytest.raises(AdmissionError) as error:
        imaging.read_vision_chunk(env.api, env.scope(), decision["id"], len(first["bytes"]))
    assert error.value.code == "grant-revoked"


def test_policy_retired_during_the_image_request_queues_no_job(env, monkeypatch):
    """Review 3 systematic pass: request_image rechecks authority before queueing the job."""
    from intake.records import INTAKE_OWNER
    from workspace.ontology_sources import Sources
    env.policy()
    ref = put_asset(env, flowchart_png(), "flow.png", "flow")
    original = Sources.resolve

    def resolving(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        policy = env.api.storage.get(INTAKE_OWNER, "adm_policy", "policy-1")
        if policy["status"] == "active":
            env.admin({"op": "retire_policy", "id": "policy-1", "expectedRevision": policy["revision"]})
        return result

    monkeypatch.setattr(Sources, "resolve", resolving)
    with pytest.raises(AdmissionError) as error:
        admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive", kind="image")
    assert error.value.code == "policy-changed"
    assert env.api.storage.list(f"project:{env.pid}", "job") == []
