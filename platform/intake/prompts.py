"""Prompt-text admission: actor-authored instructions (I6a; review rounds 3/4, F2/X3).

An edit instruction or proposal note is normalized with the private deny-list and
residual-scanned synchronously. The immutable instruction (original, normalized
text and author) is kept in a private blob; only the normalized derivative can
reach a model, through `admission.pages_for`. Authorship does not establish
classification: v1 decisions are `internal-non-sensitive` and reviewer-admitted
(`promptText: "review"`; roadmap D-5). The intake-only source kind `prompt-text`
is resolved here from that blob, not through the ontology `SOURCE_KINDS`.
"""
from __future__ import annotations

import hashlib
import json
import secrets

from intake import admission, derivative, inspect
from intake.admission import AdmissionError
from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.ontology_sources import Sources

PURPOSES = ("edit-instruction", "proposal-note")
MAX_CHARS = 4000


def _key(storage, owner, instruction_id):
    return storage.key_for(owner, "adm_decision", instruction_id, "prompt.json")


def admit_prompt_text(host, scope, text, *, purpose, claims=None):
    if purpose not in PURPOSES:
        raise AdmissionError("invalid-purpose", 400)
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS or "\x00" in text:
        raise AdmissionError("invalid-prompt-text", 400)
    storage, owner = host.storage, scope["owner"]
    project_id = admission._project(scope)
    try:
        policy = admission.current_policy(storage, project_id)
    except AdmissionError as error:
        return admission._blocked([error.code])
    if policy["promptText"] != "review" or "internal-non-sensitive" not in policy["dataClasses"]:
        return admission._blocked(["data-class-ineligible"])
    try:
        denylist = derivative.canonical_entries(admission._denylist(host))
    except derivative.DenylistUnavailable:
        return admission._blocked(["denylist-unavailable"])
    try:
        normalized = derivative.normalize_text(text, denylist)
    except derivative.NormalizationBlocked as blocked:
        reasons = ["residual-identifiers"] if "IDENTIFIER" in blocked.types else []
        if set(blocked.types) - {"IDENTIFIER"}:
            reasons.append("redaction-required")
        return admission._blocked(reasons)  # nothing, not even the original, is stored
    reader = Sources(inspect.context(host, scope, claims))
    project = reader.ctx.scope["project"]
    instruction_id = "instr-" + secrets.token_hex(20)
    storage.put_blob_once(_key(storage, owner, instruction_id), schema.canonical({
        "schemaVersion": 1, "purpose": purpose, "author": scope["actor"], "original": text,
        "normalized": normalized}), "application/json")
    source = {"sourceKind": "prompt-text", "sourceId": instruction_id, "revision": "1",
              "sha256": hashlib.sha256(normalized.encode()).hexdigest(),
              "audienceRevision": str(project.get("authorityRevision", 0))}
    original_pages = [{"page": 1, "text": text}]
    pages = [{"page": 1, "text": normalized}]
    receipt = inspect.inspect(original_pages, denylist=denylist)
    return admission.decide(
        host, scope, reader=reader, source=source, data_class="internal-non-sensitive", policy=policy,
        artifact={"kind": "prompt-text", "pages": 1},
        derivation={"profile": derivative.PROFILE, "originalHash": inspect.pages_hash(original_pages),
                    "derivativeHash": derivative.derivative_hash(pages)},
        receipt=receipt, receipt_bytes=schema.canonical(receipt), payload=schema.canonical(pages))


def resolve(host, scope, decision, reader):
    """Current authority for an intake-only prompt-text source (raises CollaborationError)."""
    reader._fresh()
    source = decision["source"]
    project = reader.ctx.scope["project"]
    if source["audienceRevision"] != str(project.get("authorityRevision", 0)) or source["revision"] != "1":
        fail(409, "ontology-source-stale", "프로젝트 권한 기준이 변경되었습니다.")
    storage = host.storage
    key = _key(storage, scope["owner"], source["sourceId"])
    try:
        value = json.loads(storage.get_blob(key, length=1024 * 1024))
    except (FileNotFoundError, ValueError):
        fail(409, "ontology-source-integrity", "지시문 원본을 확인하지 못했습니다.")
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("normalized"), str)
            or hashlib.sha256(value["normalized"].encode()).hexdigest() != source["sha256"]):
        fail(409, "ontology-source-integrity", "지시문 원본을 확인하지 못했습니다.")
    return {"ref": source, "kind": "prompt-text", "record": None, "text": None}
