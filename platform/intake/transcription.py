"""Diagram and table transcription for guidelines (I6b; O-02; review rounds 8-15, AB5/AC2/AD1/AD2).

Visible images and PDF graphics are untranscribed by the library, so guideline
diagrams take a separate, source-bound path:

1. an admitted image decision (reviewer grant, `internal-non-sensitive`),
2. `request_diagram` pins the page and a region on the normalized image,
3. `transcribe` (the `intake.transcribe` operation: context -> generate -> verify)
   sends the admitted vision derivative through `engine.gate.generate_with_images`,
4. a reviewer validates the transcription (`/intake/reviews`); in the same
   transaction `documents.library.prepare_transcription` (callable only from
   `intake.review`) creates an in-review library revision carrying server-owned
   `transcriptionOf`,
5. a planner/owner approves it through the existing library review route.

Registering `intake.transcribe` as a B0 ledger operation belongs to the ledger unit.
"""
from __future__ import annotations

import hashlib
import json
import re

from intake import admission, derivative, images, imaging, inspect, records
from intake.admission import AdmissionError
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources

OPERATION = "intake.transcribe"
MAX_TEXT = 20_000
MAX_ROWS, MAX_CELLS, MAX_CELL = 200, 20, 500
SYSTEM = ("You transcribe one guideline diagram or table image into JSON only: "
          '{"kind": "diagram"|"table", "text": string, "tables": [[string]]}. '
          "Copy numbers, rates and terms verbatim. Do not add content that is not visible.")


def _image_decisions(host, scope, source):
    storage, owner = host.storage, scope["owner"]
    cursor, found = None, []
    while True:
        page = storage.list_page(owner, "adm_decision", limit=100, cursor=cursor)
        for row in page["items"]:
            try:
                decision = records.validate("adm_decision", row)
            except ValueError:
                continue
            if (decision["artifact"]["kind"] == "image" and decision["status"] == "admitted"
                    and decision["source"] == source):
                found.append(decision)
        cursor = page.get("cursor")
        if not cursor:
            return found


def request_diagram(host, scope, source_ref, *, page, region, claims=None):
    """Pin an admitted image decision, page and normalized-image region for transcription."""
    source = {key: source_ref.get(key) for key in ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision")} \
        if isinstance(source_ref, dict) else None
    if not source or source["sourceKind"] != "asset":
        raise AdmissionError("image-admission-required", 409)
    candidates = []
    for decision in _image_decisions(host, scope, source):
        try:
            candidates.append(admission.verify(host, scope, decision["id"], claims=claims))
        except AdmissionError:
            continue
    if not candidates:
        raise AdmissionError("image-admission-required", 409)
    decision = max(candidates, key=lambda d: (d["expiresAt"], d["id"]))
    if type(page) is not int or page != 1:
        raise AdmissionError("invalid-region", 400)
    try:
        schema._fields(region, {"left", "top", "width", "height", "normalizedImageHash"})
    except ValueError:
        raise AdmissionError("invalid-region", 400) from None
    if (region["normalizedImageHash"] != decision["artifact"]["sha256"]
            or not images.region_ok(region, {"width": decision["artifact"]["width"],
                                             "height": decision["artifact"]["height"]})):
        raise AdmissionError("invalid-region", 400)
    return {"status": "pending-transcription", "operation": OPERATION, "imageDecisionId": decision["id"],
            "imageDecisionRevision": decision["revision"], "page": page, "region": dict(region)}


def _parse(text):
    body = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.S)
    if fenced:
        body = fenced.group(1)
    try:
        value = json.loads(body)
        schema._fields(value, {"kind", "text"}, {"tables"})
    except ValueError:
        raise AdmissionError("transcription-invalid", 422) from None
    tables = value.get("tables", [])
    if (value["kind"] not in ("diagram", "table") or not isinstance(value["text"], str)
            or len(value["text"]) > MAX_TEXT or not isinstance(tables, list) or len(tables) > MAX_ROWS
            or any(not isinstance(row, list) or len(row) > MAX_CELLS
                   or any(not isinstance(cell, str) or len(cell) > MAX_CELL for cell in row) for row in tables)):
        raise AdmissionError("transcription-invalid", 422)
    return {"kind": value["kind"], "text": value["text"], "tables": tables}


def transcribe(host, scope, pending, *, model_id, generate=None, claims=None, trace_id=""):
    """Run context -> generate -> verify and record a `pending-review` transcription decision."""
    if not isinstance(pending, dict) or pending.get("operation") != OPERATION:
        raise AdmissionError("invalid-request", 400)
    project_id = admission._project(scope)
    # context: the image decision must still be admitted, unchanged and current.
    decision, image, authority = imaging.vision_input_authority(host, scope, pending["imageDecisionId"],
                                                               claims=claims)
    if decision["revision"] != pending["imageDecisionRevision"]:
        raise AdmissionError("source-changed")
    region = pending["region"]
    if region["normalizedImageHash"] != decision["artifact"]["sha256"]:
        raise AdmissionError("invalid-region", 400)
    policy = admission.current_policy(host.storage, project_id)
    try:
        denylist = derivative.canonical_entries(admission._denylist(host))
    except derivative.DenylistUnavailable:
        return admission._blocked(["denylist-unavailable"])
    # generate: the measured boundary applies (OCR text is checked before bytes leave).
    if generate is None:
        from engine.gate import generate_with_images as generate
    user = (f"영역: left={region['left']}, top={region['top']}, width={region['width']}, "
            f"height={region['height']} (정규화 이미지 기준 픽셀). 이 영역의 도식 또는 표를 전사하세요.")
    # The policy and deny-list reads above are I/O after `vision_input`: recheck the
    # image's full authority immediately before invoking the model.
    authority.recheck()
    output, _, _ = generate(SYSTEM, user, [image], model_id=model_id, purpose=OPERATION, trace_id=trace_id)
    # verify: closed output schema, identifier normalization and residual scan.
    raw = _parse(output)
    flat = [raw["text"], *[cell for row in raw["tables"] for cell in row]]
    receipt = inspect.inspect([{"page": 1, "text": "\n".join(flat)}], denylist=denylist)
    normalized = {"kind": raw["kind"],
                  "text": derivative._normalize_one(raw["text"], denylist)[0],
                  "tables": [[derivative._normalize_one(cell, denylist)[0] for cell in row] for row in raw["tables"]]}
    rest = derivative.residual([{"page": 1, "text": "\n".join(
        [normalized["text"], *[c for row in normalized["tables"] for c in row]])}], denylist)
    blocking = (["residual-identifiers"] if rest["identifiers"] else []) + (
        ["redaction-required"] if rest["pii"] else [])
    placed = {"page": pending["page"], **{k: region[k] for k in ("left", "top", "width", "height")},
              "normalizedImageHash": region["normalizedImageHash"]}
    payload = schema.canonical({"page": pending["page"], "region": placed, **normalized})
    reader = Sources(inspect.context(host, scope, claims))
    reader.resolve(dict(decision["source"]))
    lineage = {"decisionId": decision["id"], "decisionRevision": decision["revision"]}
    return admission.decide(
        host, scope, reader=reader, source=dict(decision["source"]), data_class="internal-non-sensitive",
        policy=policy, artifact={"kind": "diagram-transcription", "pages": 1, "region": placed,
                                 "suffix": "transcription.json"},
        derivation={"profile": derivative.PROFILE, "originalHash": decision["derivation"]["derivativeHash"],
                    "derivativeHash": hashlib.sha256(payload).hexdigest()},
        receipt=receipt, receipt_bytes=schema.canonical(receipt), payload=payload, blocking=blocking,
        identity=[lineage, placed], lineage=lineage,
        # The image lineage joins the commit fences and the final/attempt recheck.
        extra_checks=list(authority.checks))


def read(host, scope, decision):
    """The verified transcription derivative of an admitted transcription decision."""
    data = admission._read_verified(host.storage, scope["owner"], decision["artifact"]["key"],
                                    decision["derivation"]["derivativeHash"], "artifact-changed")
    return json.loads(data)


def markdown(value):
    def cell(text):
        return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = [f"# 가이드 {'표' if value['kind'] == 'table' else '도식'} 전사", "", value["text"].strip(), ""]
    rows = value.get("tables") or []
    if rows:
        width = max(len(row) for row in rows) or 1
        padded = [row + [""] * (width - len(row)) for row in rows]
        lines.append("| " + " | ".join(cell(c) for c in padded[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
        lines.extend("| " + " | ".join(cell(c) for c in row) + " |" for row in padded[1:])
        lines.append("")
    return "\n".join(lines).encode("utf-8")
