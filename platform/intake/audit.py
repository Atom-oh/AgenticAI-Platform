"""Metadata-only admission and transfer audit export (`AGENTCORE_CONTRACT.md` §7.2).

Used for the 혁신금융 application record. The export contains identifiers,
revisions, hashes, classes, stages, model IDs and times only: never page text,
prompts, deny-list terms or credentials. Every value is validated against a
closed metadata shape, so a ledger field that carries text cannot leak through.
"""
from __future__ import annotations

import re

from intake import records

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,159}\Z")
MAX_ROWS = 5000


def _time(value):
    return type(value) is int and 0 <= value <= records.MAX_TIME


def _ok(pattern, value):
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _rows(storage, owner, kind):
    rows, cursor = [], None
    while True:
        page = storage.list_page(owner, kind, limit=100, cursor=cursor)
        rows.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            return rows
        if len(rows) >= MAX_ROWS:
            raise ValueError("Audit export window is too large; narrow since/until")


def _decision(row):
    try:
        decision = records.validate("adm_decision", row)
    except ValueError:
        return None
    if decision["status"] != "admitted":
        return None
    entry = {"decisionId": decision["id"], "revision": decision["revision"], "dataClass": decision["dataClass"],
             "policy": {key: decision["policy"][key] for key in ("id", "revision", "hash")},
             "derivativeHash": decision["derivation"]["derivativeHash"], "at": decision.get("updatedAt")}
    if "review" in decision:
        entry["reviewer"] = {key: decision["review"][key] for key in ("actor", "grantId", "grantRevision")}
    if "provenance" in decision:
        entry["provenance"] = {key: decision["provenance"][key] for key in ("id", "revision")}
    return entry


def _transfer(job, decision_id, artifact_hash, attempt, stage, model, at):
    entry = {"executionId": job.get("id"), "attemptId": attempt, "decisionId": decision_id,
             "artifactHash": artifact_hash, "stage": stage, "model": model, "at": at}
    if (_ok(_ID, entry["executionId"]) and _ok(_ID, attempt) and _ok(_ID, decision_id)
            and _ok(_HASH, artifact_hash) and _ok(_LABEL, stage) and _ok(_LABEL, model) and _time(at)):
        return entry
    return None


def _job_entries(job):
    """B0 ledger jobs: `transfers` (input handles) and `calls` (model calls) per attempt."""
    admissions = [a for a in job.get("admissions") or [] if isinstance(a, dict)]
    hashes = {a.get("decisionId"): a.get("artifactHash") for a in admissions}
    entries = []
    for transfer in job.get("transfers") or []:
        if isinstance(transfer, dict):
            decision_id = transfer.get("decisionId")
            entries.append(_transfer(job, decision_id, transfer.get("artifactHash", hashes.get(decision_id)),
                                     transfer.get("attemptId"), transfer.get("stage"), job.get("model"),
                                     transfer.get("at")))
    for call in job.get("calls") or []:
        if isinstance(call, dict):
            for admission in admissions:
                entries.append(_transfer(job, admission.get("decisionId"), admission.get("artifactHash"),
                                         call.get("attemptId"), call.get("stage"), call.get("model", job.get("model")),
                                         call.get("at")))
    return [entry for entry in entries if entry is not None]


def export(storage, owner, *, since, until) -> dict:
    if not _time(since) or not _time(until) or until < since:
        raise ValueError("A bounded [since, until) window is required")
    decisions = []
    for row in _rows(storage, owner, "adm_decision"):
        entry = _decision(row)
        if entry and _time(entry["at"]) and since <= entry["at"] < until:
            decisions.append(entry)
    transfers, seen = [], set()
    for job in _rows(storage, owner, "job"):
        if job.get("executionSchemaVersion") != 1:
            continue  # legacy jobs are not ledger executions
        for entry in _job_entries(job):
            key = tuple(sorted(entry.items()))
            if since <= entry["at"] < until and key not in seen:
                seen.add(key)
                transfers.append(entry)
    decisions.sort(key=lambda e: (e["at"], e["decisionId"]))
    transfers.sort(key=lambda e: (e["at"], e["executionId"], e["attemptId"], e["stage"], e["decisionId"]))
    return {"decisions": decisions, "transfers": transfers}
