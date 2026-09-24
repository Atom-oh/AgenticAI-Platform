"""Worker task `intake-image` (I8a; review rounds 5/6/9/28: Y4, Z7, AC1, AV1).

Runs in the workspace Worker Docker image, which ships Pillow and the private
OCR. The job input is pinned by `intake.admission.request_image`. The decision
and the job's terminal state commit in one version-CAS transaction; the Worker
then verifies the committed job instead of writing it again.
"""
from __future__ import annotations

from intake import imaging


class IntakeBlocked(ValueError):
    """A bounded, metadata-only blocking outcome (reason codes only)."""

    def __init__(self, reasons):
        self.reasons = sorted(set(reasons))
        super().__init__("반입 차단: " + ", ".join(self.reasons))


def _ocr_adapter(worker):
    def run(data):
        text, status = worker.ocr(data)
        return {"status": status, "text": text}
    return run


def process_image(worker, owner, job):
    from workspace.collaboration import Collaboration
    data = job.get("input", {})
    storage = worker.storage
    collaboration = getattr(worker, "collaboration", None) or Collaboration(storage)
    scope = collaboration.resolve_scope(data.get("actorId"), data.get("projectId"))
    if scope["owner"] != owner or job.get("task") != "intake-image":
        raise ValueError("작업 범위가 반입 요청과 다릅니다.")
    if data.get("authorizationExpiresAt", 0) <= storage.clock():
        raise IntakeBlocked(["authorization-expired"])
    current = storage.get(owner, "job", job["id"])
    if not current or current.get("status") != "running" or current.get("input") != data:
        raise ValueError("반입 작업이 변경되었습니다.")

    def completion(decision):
        result = {"decisionId": decision["id"], "status": decision["status"]}
        return [{"owner": owner, "kind": "job", "expected_version": current["version"],
                 "item": {**current, "status": "completed", "progress": 100, "result": result}}]

    outcome = imaging.admit_image(worker, scope, data["sourceRef"], data_class=data["dataClass"],
                                  ocr=_ocr_adapter(worker), identifier=data["decisionId"],
                                  completion=completion, policy_binding=data["policy"])
    if outcome.get("status") == "blocked" and "id" not in outcome:
        raise IntakeBlocked(outcome["blocking"])
    return {"decisionId": outcome["id"], "status": outcome["status"]}
