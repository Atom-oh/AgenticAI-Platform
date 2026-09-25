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
    deadline = data.get("authorizationExpiresAt")
    if type(deadline) is not int or deadline <= storage.clock():
        raise IntakeBlocked(["authorization-expired"])
    # The reconstructed scope keeps the request's authorization deadline, so every
    # later `fresh()`/`Sources.recheck` (including each commit attempt) enforces it.
    scope = collaboration.resolve_scope(data.get("actorId"), data.get("projectId"), deadline)
    if scope["owner"] != owner or job.get("task") != "intake-image":
        raise ValueError("작업 범위가 반입 요청과 다릅니다.")
    frozen = data.get("authorityRevision")
    observed = data.get("observed")
    if (not isinstance(observed, list) or not observed
            or any(not isinstance(check, dict) or set(check) != {"owner", "kind", "id", "version"}
                   or check["owner"] != owner or type(check["version"]) is not int for check in observed)):
        raise IntakeBlocked(["source-changed"])

    def current_authority():
        """The membership epoch and every source record version observed at queueing
        must still hold: a removed and restored member (a later epoch) or a revoked
        and restored asset (a later version) cannot resurrect the queued request.

        These reads only collect: the job's deadline is returned as one
        `(expiresAt, error)` pair for the caller to compare with a fresh clock
        read taken after every read it has collected — this callback's own,
        immediately (`_check_deadlines`, below), or together with `decide()`'s
        own policy/provenance/upstream deadlines when this runs as one of its
        `guards` (a clock read taken before those combined reads could miss a
        deadline crossed during any of them, including this callback's)."""
        fresh = collaboration.resolve_scope(data.get("actorId"), data.get("projectId"), deadline)
        if type(frozen) is not int or fresh["project"].get("authorityRevision", 0) != frozen:
            raise IntakeBlocked(["authority-changed"])
        for check in observed:
            row = storage.get(check["owner"], check["kind"], check["id"])
            if not row or row.get("version") != check["version"]:
                raise IntakeBlocked(["source-changed"])
        return [(deadline, IntakeBlocked(["authorization-expired"]))]

    def _check_deadlines(pairs):
        now = storage.clock()  # after every read that collected a pair
        if pairs:
            expiry, error = min(pairs, key=lambda item: item[0])
            if expiry <= now:
                raise error

    _check_deadlines(current_authority())
    current = storage.get(owner, "job", job["id"])
    if not current or current.get("status") != "running" or current.get("input") != data:
        raise ValueError("반입 작업이 변경되었습니다.")

    def completion(decision):
        result = {"decisionId": decision["id"], "status": decision["status"]}
        return [{"owner": owner, "kind": "job", "expected_version": current["version"],
                 "item": {**current, "status": "completed", "progress": 100, "result": result}}]

    outcome = imaging.admit_image(worker, scope, data["sourceRef"], data_class=data["dataClass"],
                                  ocr=_ocr_adapter(worker), identifier=data["decisionId"],
                                  completion=completion, policy_binding=data["policy"],
                                  guards=[current_authority])
    if outcome.get("status") == "blocked" and "id" not in outcome:
        raise IntakeBlocked(outcome["blocking"])
    return {"decisionId": outcome["id"], "status": outcome["status"]}
