"""Image admission: normalized image derivative plus a bounded vision derivative.

Runs only where Pillow is available: the workspace Worker image (`intake-image`
task, I8a) or offline tests. The vision derivative is exactly the input that
`engine.gate.generate_with_images` accepts: `{format, bytes <= 3 000 000,
ocrStatus, ocrText}`. OCR comes from the Worker's private OCR; incomplete OCR
blocks, and the residual PII/deny-list scan runs over `ocrText`.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import PurePosixPath

from intake import admission, derivative, images, inspect, records
from intake.admission import AdmissionError
from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources

MAX_VISION_BYTES = 3_000_000
EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg")


def vision_derivative(normalized, maximum=MAX_VISION_BYTES):
    """Downscale the normalized PNG until it fits the gate's image limit; record the transform."""
    data = normalized["bytes"]
    if len(data) <= maximum:
        return data, []
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    width, height = image.size
    for _ in range(30):
        width, height = max(1, int(width * 0.8)), max(1, int(height * 0.8))
        buffer = io.BytesIO()
        image.resize((width, height), Image.Resampling.LANCZOS).save(buffer, "PNG", compress_level=9)
        if buffer.tell() <= maximum:
            return buffer.getvalue(), [f"downscale-{width}x{height}"]
    raise AdmissionError("vision-derivative-too-large", 422)


def run_ocr(ocr, data):
    """The Worker's private OCR returns `{"status", "text"}`; anything else is incomplete."""
    if ocr is None:
        return None
    try:
        result = ocr(data)
    except Exception:  # noqa: BLE001 - OCR failure blocks; never log image text
        return None
    if (not isinstance(result, dict) or result.get("status") != "complete"
            or not isinstance(result.get("text"), str) or len(result["text"]) > 100_000):
        return None
    return result["text"]


def prepare(host, scope, source_ref, *, claims=None):
    """Resolve the image asset with current authority and read its original bytes."""
    reader = Sources(inspect.context(host, scope, claims))
    try:
        ref = schema.source_ref(source_ref)
    except ValueError:
        fail(400, "invalid-source", "원본 참조 형식이 올바르지 않습니다.")
    if ref["sourceKind"] != "asset" or "location" in ref:
        fail(422, "intake-source-unsupported", "이미지는 반입된 자산이어야 합니다.")
    asset = reader.resolve(ref)["record"]
    if PurePosixPath(asset.get("name", "")).suffix.lower() not in EXTENSIONS or asset["size"] > images.MAX_BYTES:
        fail(422, "intake-source-unsupported", "PNG/JPEG 이미지 자산만 반입할 수 있습니다.")
    data = reader._blob(asset["originalKey"], ref["sha256"], images.MAX_BYTES)
    return reader, ref, data


def admit_image(host, scope, source_ref, *, data_class, ocr, claims=None, prepared=None, identifier=None,
                completion=None, policy_binding=None):
    project_id = admission._project(scope)
    if data_class not in records.DATA_CLASSES:
        return admission._blocked(["data-class-ineligible"])
    try:
        policy = admission.current_policy(host.storage, project_id)
    except AdmissionError as error:
        return admission._blocked([error.code])
    if data_class not in policy["dataClasses"]:
        return admission._blocked(["data-class-ineligible"])
    if policy_binding is not None and policy_binding != {k: policy[k] for k in ("id", "revision", "hash")}:
        return admission._blocked(["policy-changed"])
    try:
        denylist = derivative.canonical_entries(admission._denylist(host))
    except derivative.DenylistUnavailable:
        return admission._blocked(["denylist-unavailable"])
    reader, ref, data = prepared or prepare(host, scope, source_ref, claims=claims)
    try:
        normalized = images.normalize_image(data)
    except images.ImageRejected as rejected:
        return admission._blocked([rejected.code])
    vision, transform = vision_derivative(normalized)
    text = run_ocr(ocr, vision)
    if text is None:
        return admission._blocked(["ocr-incomplete"])
    pages = [{"page": 1, "text": text}]
    receipt = inspect.inspect(pages, denylist=denylist)
    rest = derivative.residual(pages, denylist)
    blocking = (["residual-identifiers"] if rest["identifiers"] else []) + (
        ["redaction-required"] if rest["pii"] else [])
    ocr_bytes = schema.canonical({"ocrStatus": "complete", "ocrText": text})
    source = {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision")}
    return admission.decide(
        host, scope, reader=reader, source=source, data_class=data_class, policy=policy,
        artifact={"kind": "image", "pages": 1, "width": normalized["width"], "height": normalized["height"],
                  "suffix": "normalized.png",
                  "vision": {"suffix": "vision.png", "sha256": hashlib.sha256(vision).hexdigest(), "format": "png",
                             "size": len(vision), "ocrSha256": hashlib.sha256(ocr_bytes).hexdigest(),
                             "transform": transform}},
        derivation={"profile": images.PROFILE, "originalHash": normalized["originalSha256"],
                    "derivativeHash": normalized["sha256"]},
        receipt=receipt, receipt_bytes=schema.canonical(receipt), payload=normalized["bytes"],
        content_type="image/png", blocking=blocking, identifier=identifier, completion=completion,
        extra_blobs={"vision.png": (vision, "image/png"), "ocr.json": ocr_bytes})


def _ocr(host, scope, decision):
    storage = host.storage
    key = storage.key_for(scope["owner"], "adm_decision", decision["id"], "ocr.json")
    value = json.loads(admission._read_verified(storage, scope["owner"], key, decision["artifact"]["vision"]["ocrSha256"],
                                                "artifact-changed", maximum=1024 * 1024))
    return value


def descriptor(host, scope, decision_id, *, claims=None):
    """Runtime-facing bounded descriptor (`admission.pages` for images): no bytes."""
    decision, _, authority = admission.authorized(host, scope, decision_id, claims=claims)
    if decision["artifact"]["kind"] != "image":
        raise AdmissionError("artifact-kind-unsupported", 422)
    value = _ocr(host, scope, decision)
    vision = decision["artifact"]["vision"]
    authority.recheck()  # after the last read, immediately before delivery
    return {"format": vision["format"], "ocrStatus": value["ocrStatus"], "ocrText": value["ocrText"],
            "visionSha256": vision["sha256"], "size": vision["size"]}


def vision_input(host, scope, decision_id, *, claims=None):
    """Trusted server path only: the exact `generate_with_images` image entry."""
    decision, image, _ = vision_input_authority(host, scope, decision_id, claims=claims)
    return decision, image


def vision_input_authority(host, scope, decision_id, *, claims=None):
    """`vision_input` plus the retained Authority, for callers that do more I/O
    before the model call (they must `recheck()` immediately before invoking)."""
    decision, _, authority = admission.authorized(host, scope, decision_id, claims=claims)
    if decision["artifact"]["kind"] != "image":
        raise AdmissionError("artifact-kind-unsupported", 422)
    vision = decision["artifact"]["vision"]
    data = admission._read_verified(host.storage, scope["owner"], vision["key"], vision["sha256"], "artifact-changed")
    value = _ocr(host, scope, decision)
    authority.recheck()
    return decision, {"format": vision["format"], "bytes": data, "ocrStatus": value["ocrStatus"],
                      "ocrText": value["ocrText"]}, authority


CHUNK_BYTES = 256 * 1024


def read_vision_chunk(host, scope, decision_id, offset, *, claims=None):
    """One 256 KiB chunk of the admitted vision derivative; every chunk reruns `verify`.

    The B0 ledger's `open_input`/`read_chunk` input handles bind execution/attempt;
    this is the intake side they call for image decisions.
    """
    decision, _, authority = admission.authorized(host, scope, decision_id, claims=claims)
    vision = decision["artifact"].get("vision")
    if not vision or type(offset) is not int or not 0 <= offset < vision["size"]:
        raise AdmissionError("invalid-chunk", 400)
    data = admission._read_verified(host.storage, scope["owner"], vision["key"], vision["sha256"], "artifact-changed")
    chunk = data[offset:offset + CHUNK_BYTES]
    authority.recheck()
    return {"offset": offset, "total": vision["size"], "sha256": hashlib.sha256(chunk).hexdigest(),
            "objectSha256": vision["sha256"], "bytes": chunk}
