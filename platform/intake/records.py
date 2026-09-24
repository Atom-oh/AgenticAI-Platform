"""Closed `source-admission/1` record schemas.

Unknown fields, kinds, statuses and versions fail closed. Validators confer no
authority: administration is IAM-only (`intake.admin_handler`) and admission
decisions are produced only by `intake.admission` after current checks.
"""
from __future__ import annotations

import copy
import re

from workspace import ontology_schema as schema
from workspace.ontology_schema import _fields

INTAKE_OWNER = "intake:deployment"
KINDS = ("adm_policy", "adm_provenance", "adm_grant", "adm_decision", "adm_audit", "adm_resolver", "capability",
         "adm_sharing")
# Organization-sharing source policies: the separately authorized source-policy
# change that admits one exact origin source revision to organization publication.
SHARING_SOURCE_KINDS = ("asset", "document-revision", "product-guideline")
# Organization publication capabilities (AGENTCORE_CONTRACT "Shared-publication authority").
CAPABILITY_NAMES = ("design_publish", "policy_publish")
DATA_CLASSES = ("synthetic", "public", "internal-non-sensitive")
DECISION_CLASSES = DATA_CLASSES + ("sensitive",)
INSPECTION_PROFILE = "inspect-1"
NORMALIZATION_PROFILE = "identifier-normalization-1"
DECISION_STATUSES = ("pending-review", "admitted", "rejected", "blocked", "revoked", "expired")
ARTIFACT_KINDS = ("document-pages", "image", "code-collection", "prompt-text", "diagram-transcription")
PROVENANCE_SOURCE_KINDS = ("document-revision", "asset", "product-guideline")
DECISION_SOURCE_KINDS = PROVENANCE_SOURCE_KINDS + ("prompt-text",)
AUDIT_OPS = ("put_policy", "activate_policy", "retire_policy", "register_provenance",
             "revoke_provenance", "grant_reviewer", "revoke_grant", "put_resolver_profile",
             "retire_resolver_profile", "grant_capability", "revoke_capability", "grant_sharing",
             "revoke_sharing")
_PACKAGE = re.compile(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+(?:/[a-zA-Z0-9._/-]+)?\Z")
_JSON_FIELD = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,63}\Z")
# Storage.put owns these; they are never part of the sealed content hash.
STORAGE_FIELDS = ("version", "createdAt", "updatedAt")
MAX_TIME = 2 ** 53 - 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_REASON = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_HTTPS = re.compile(r"https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[^\s]*)?\Z")


def _bad(message="The admission record does not match its closed schema"):
    raise ValueError(message)


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _bad("Invalid admission identifier")
    return value


def _int(value, minimum=1):
    if type(value) is not int or not minimum <= value <= MAX_TIME:
        _bad("Admission revisions and times must be bounded integers")
    return value


def _hash(value):
    try:
        return schema._hash(value)
    except ValueError:
        _bad("Invalid admission hash")


def _label(value, pattern=_LABEL):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        _bad("Invalid admission label")
    return value


def _text(value, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        _bad("Invalid admission text")
    value.encode("utf-8")
    return value


def _choice(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        _bad("Unknown admission value")
    return value


def _unique_list(value, item, *, minimum=1, maximum=100):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _bad("Invalid admission list")
    for element in value:
        item(element)
    if len({schema.digest(x) for x in value}) != len(value):
        _bad("Duplicate admission list entry")
    return value


def _closed(value, required, optional=()):
    try:
        _fields(value, required, optional)
    except ValueError:
        _bad()


def _project_scope(value, *, deployment):
    _closed(value, {"deployment", "projectIds"} if deployment else {"projectIds"})
    if deployment:
        _label(value["deployment"])
    ids = value["projectIds"]
    if ids == "*" and deployment:
        return
    _unique_list(ids, _identifier)


def _source(value, *, audience, kinds):
    required = {"sourceKind", "sourceId", "revision", "sha256"} | ({"audienceRevision"} if audience else set())
    _closed(value, required)
    _choice(value["sourceKind"], kinds)
    _identifier(value["sourceId"])
    _text(value["revision"])
    _hash(value["sha256"])
    if audience:
        _text(value["audienceRevision"])


def _policy(record):
    _closed(record, {"id", "revision", "scope", "dataClasses", "profiles", "requiresReviewer",
                     "trustedProvenance", "promptText", "status", "expiresAt", "hash"})
    _project_scope(record["scope"], deployment=True)
    _unique_list(record["dataClasses"], lambda x: _choice(x, DATA_CLASSES), maximum=len(DATA_CLASSES))
    if record["profiles"] != {"inspection": INSPECTION_PROFILE, "normalization": NORMALIZATION_PROFILE}:
        _bad("Unknown admission profile")
    # The mandatory human review of internal material cannot be disabled by policy.
    if record["requiresReviewer"] != {"internal-non-sensitive": True}:
        _bad("Internal-non-sensitive admission always requires a reviewer")
    trusted = record["trustedProvenance"]
    _closed(trusted, set(), {"public", "synthetic"})
    if any(type(value) is not bool for value in trusted.values()):
        _bad("Invalid trusted provenance rule")
    # v1: actor-authored prompt text is reviewer-admitted only (roadmap D-5).
    if record["promptText"] != "review":
        _bad("Prompt-text admission is reviewer-only in source-admission/1")
    _choice(record["status"], ("draft", "active", "retired"))


def _provenance(record):
    _closed(record, {"id", "revision", "policyId", "policyRevision", "kind", "reference", "scope",
                     "status", "expiresAt", "hash"}, {"publicUrl"})
    _identifier(record["policyId"])
    _int(record["policyRevision"])
    _choice(record["kind"], ("fixture", "public-reference"))
    _source(record["reference"], audience=False, kinds=PROVENANCE_SOURCE_KINDS)
    if "publicUrl" in record:
        if record["kind"] != "public-reference":
            _bad("A public URL applies only to a public reference")
        url = record["publicUrl"]
        if not isinstance(url, str) or len(url) > 500 or not _HTTPS.fullmatch(url):
            _bad("A public reference URL must use https")
    _project_scope(record["scope"], deployment=True)
    _choice(record["status"], ("active", "revoked"))


def _grant(record):
    _closed(record, {"id", "revision", "actor", "policyId", "scope", "operations", "status",
                     "expiresAt", "hash"})
    _label(record["actor"], _ACTOR)
    _identifier(record["policyId"])
    _project_scope(record["scope"], deployment=False)
    if record["operations"] != ["review-internal"]:
        _bad("Unknown reviewer operation")
    _choice(record["status"], ("active", "revoked"))


def _capability(record):
    """IAM-administered `design_publish`/`policy_publish` capability of one verified actor."""
    _closed(record, {"id", "revision", "actor", "name", "status", "expiresAt", "hash"})
    _label(record["actor"], _ACTOR)
    _choice(record["name"], CAPABILITY_NAMES)
    _choice(record["status"], ("active", "revoked"))


def sharing_policy_id(project_id, source):
    """Deterministic ID of the sharing policy for one exact origin source revision."""
    return "share-" + schema.digest(["organization-sharing", project_id, source["sourceKind"], source["sourceId"],
                                     source["revision"], source["sha256"]])[:40]


def _sharing(record):
    """IAM-administered organization-sharing policy of one exact origin source revision."""
    _closed(record, {"id", "revision", "projectId", "source", "audience", "status", "expiresAt", "hash"})
    _identifier(record["projectId"])
    _source(record["source"], audience=False, kinds=SHARING_SOURCE_KINDS)
    _choice(record["audience"], ("organization",))
    _choice(record["status"], ("active", "revoked"))
    if record["id"] != sharing_policy_id(record["projectId"], record["source"]):
        _bad("A sharing policy ID is bound to its project and exact source revision")


def _resolver(record):
    """IAM-administered code-collection resolver profile (I6a; review round 8, AB2)."""
    _closed(record, {"id", "revision", "aliases", "packages", "jsonAssetFields", "status", "expiresAt", "hash"})
    aliases, packages = record["aliases"], record["packages"]
    if not isinstance(aliases, dict) or len(aliases) > 30 or not isinstance(packages, dict) or len(packages) > 30:
        _bad("Invalid resolver profile")
    for alias, target in aliases.items():
        if (not alias or len(alias) > 150 or alias.count("*") > 1 or not isinstance(target, str)
                or not target or len(target) > 500 or target.count("*") > 1 or ("*" in alias) != ("*" in target)
                or target.startswith("/") or any(part in ("", ".", "..") for part in target.split("/"))
                or re.search(r"[\\:\x00-\x1f\x7f?#]", target)):
            _bad("Invalid resolver alias")
    for name, spec in packages.items():
        if not _PACKAGE.fullmatch(name):
            _bad("Invalid resolver package")
        _closed(spec, {"version", "sha256"})
        if not isinstance(spec["version"], str) or not spec["version"] or len(spec["version"]) > 80:
            _bad("Invalid resolver package")
        _hash(spec["sha256"])
    _unique_list(record["jsonAssetFields"], lambda x: _label(x, _JSON_FIELD), minimum=0, maximum=20)
    _choice(record["status"], ("active", "retired"))


def _artifact(value):
    _closed(value, {"kind", "key", "sha256"}, {"pages", "files", "resolver", "vision", "region", "width", "height"})
    kind = _choice(value["kind"], ARTIFACT_KINDS)
    if not isinstance(value["key"], str) or not value["key"].startswith("workspace/") or len(value["key"]) > 1024:
        _bad("Invalid private artifact key")
    _hash(value["sha256"])
    if "pages" in value:
        _int(value["pages"], 0)
    if "files" in value:
        if kind != "code-collection":
            _bad("Only a code collection lists files")
        _int(value["files"], 0)
    if "resolver" in value:
        if kind != "code-collection":
            _bad("Only a code collection binds a resolver")
        resolver = value["resolver"]
        _closed(resolver, {"profile", "derivativeHash"})
        _closed(resolver["profile"], {"id", "revision", "hash"})
        _identifier(resolver["profile"]["id"])
        _int(resolver["profile"]["revision"])
        _hash(resolver["profile"]["hash"])
        _hash(resolver["derivativeHash"])
    if "vision" in value:
        if kind != "image":
            _bad("Only an image binds a vision derivative")
        vision = value["vision"]
        _closed(vision, {"key", "sha256", "format", "size", "ocrSha256", "transform"})
        if not isinstance(vision["key"], str) or not vision["key"].startswith("workspace/"):
            _bad("Invalid private artifact key")
        _hash(vision["sha256"])
        _hash(vision["ocrSha256"])
        _choice(vision["format"], ("png", "jpeg"))
        _int(vision["size"])
        _unique_list(vision["transform"], lambda x: _label(x), minimum=0, maximum=20)
    for key in ("width", "height"):
        if key in value:
            if kind != "image":
                _bad("Only an image records normalized dimensions")
            _int(value[key])
    if "region" in value:
        if kind != "diagram-transcription":
            _bad("Only a transcription binds an image region")
        region = value["region"]
        _closed(region, {"page", "left", "top", "width", "height", "normalizedImageHash"})
        for key in ("left", "top"):
            _int(region[key], 0)
        for key in ("page", "width", "height"):
            _int(region[key])
        _hash(region["normalizedImageHash"])


def _decision(record):
    _closed(record, {"id", "revision", "projectId", "source", "artifact", "derivation", "policy",
                     "inspection", "dataClass", "status", "expiresAt", "hash"},
            {"provenance", "review", "blocking", "lineage"})
    _identifier(record["projectId"])
    _source(record["source"], audience=True, kinds=DECISION_SOURCE_KINDS)
    _artifact(record["artifact"])
    derivation = record["derivation"]
    _closed(derivation, {"profile", "originalHash", "derivativeHash"})
    _choice(derivation["profile"], (NORMALIZATION_PROFILE, "image-normalization-1"))
    _hash(derivation["originalHash"])
    _hash(derivation["derivativeHash"])
    _closed(record["policy"], {"id", "revision", "hash"})
    _identifier(record["policy"]["id"])
    _int(record["policy"]["revision"])
    _hash(record["policy"]["hash"])
    if "provenance" in record:
        _closed(record["provenance"], {"id", "revision"})
        _identifier(record["provenance"]["id"])
        _int(record["provenance"]["revision"])
    if "lineage" in record:
        if record["artifact"]["kind"] != "diagram-transcription":
            _bad("Only a transcription records image lineage")
        _closed(record["lineage"], {"decisionId", "decisionRevision"})
        _identifier(record["lineage"]["decisionId"])
        _int(record["lineage"]["decisionRevision"])
    elif record["artifact"]["kind"] == "diagram-transcription":
        _bad("A transcription must bind its admitted image decision")
    _closed(record["inspection"], {"receiptKey", "hash"})
    if not isinstance(record["inspection"]["receiptKey"], str) or not record["inspection"]["receiptKey"].startswith("workspace/"):
        _bad("Invalid private receipt key")
    _hash(record["inspection"]["hash"])
    if "review" in record:
        review = record["review"]
        _closed(review, {"actor", "grantId", "grantRevision", "at"}, {"decision"})
        _label(review["actor"], _ACTOR)
        _identifier(review["grantId"])
        _int(review["grantRevision"])
        _int(review["at"])
        if "decision" in review:
            _choice(review["decision"], ("approved", "rejected"))
    _choice(record["dataClass"], DECISION_CLASSES)
    status = _choice(record["status"], DECISION_STATUSES)
    if "blocking" in record:
        _unique_list(record["blocking"], lambda x: _label(x, _REASON), maximum=20)
    if status == "blocked" and not record.get("blocking"):
        _bad("A blocked decision names its blocking reasons")
    if status == "admitted" and (record.get("blocking") or record["dataClass"] == "sensitive"):
        _bad("An admitted decision cannot carry blocking reasons")
    if status == "admitted" and "review" not in record and "provenance" not in record:
        _bad("An admitted decision needs trusted provenance or a reviewer")
    if status == "admitted" and record["dataClass"] == "internal-non-sensitive" and "review" not in record:
        _bad("Internal-non-sensitive admission requires a reviewer")


def _audit(record):
    _closed(record, {"id", "op", "kind", "recordId", "revision", "operator", "at"}, {"status"})
    _choice(record["op"], AUDIT_OPS)
    _choice(record["kind"], ("adm_policy", "adm_provenance", "adm_grant", "adm_resolver", "capability",
                             "adm_sharing"))
    _identifier(record["recordId"])
    _label(record["operator"])
    _int(record["at"])
    # Storage.put assigns its default status to kinds without one.
    if "status" in record and record["status"] != "active":
        _bad("Audit events carry no lifecycle status")


_VALIDATORS = {"adm_policy": _policy, "adm_provenance": _provenance, "adm_grant": _grant,
               "adm_decision": _decision, "adm_audit": _audit, "adm_resolver": _resolver,
               "capability": _capability, "adm_sharing": _sharing}


def _content(record):
    return {key: value for key, value in record.items() if key not in STORAGE_FIELDS and key != "hash"}


def digest_record(record):
    return schema.digest(_content(record))


def validate(kind, record) -> dict:
    validator = _VALIDATORS.get(kind) if isinstance(kind, str) else None
    if validator is None or not isinstance(record, dict):
        _bad("Unknown admission record kind")
    content = {key: value for key, value in record.items() if key not in STORAGE_FIELDS}
    for key in STORAGE_FIELDS:
        if key in record:
            _int(record[key], 1)
    try:
        schema._json_value(content)
    except ValueError:
        _bad("Admission records use bounded canonical JSON values")
    validator(content)
    _identifier(record["id"])
    _int(record["revision"])
    if kind != "adm_audit":
        _int(record["expiresAt"])
        if _hash(record["hash"]) != digest_record(record):
            _bad("Admission record hash does not match its content")
    return record


def seal(kind, record) -> dict:
    if not isinstance(record, dict):
        _bad("Unknown admission record kind")
    if kind == "adm_audit":
        return validate(kind, copy.deepcopy(record))
    content = copy.deepcopy(_content(record))
    try:
        content["hash"] = schema.digest(content)
    except ValueError:
        _bad("Admission records use bounded canonical JSON values")
    return validate(kind, content)


def is_current(record, now) -> bool:
    return (isinstance(record, dict) and record.get("status") in ("active", "admitted")
            and type(record.get("expiresAt")) is int and type(now) is int and now < record["expiresAt"])
