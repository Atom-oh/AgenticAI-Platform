"""Private HTTP API for uploads, approved contracts, and verified run artifacts.

API Gateway's JWT authorizer supplies identity. No owner, S3 key, approval, or
worker status supplied by a request body is trusted. Uploaded programs are data.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import uuid
from typing import NamedTuple
from urllib.parse import quote

from workspace.storage import Conflict, Storage, key_for
from workspace.collaboration import Collaboration, CollaborationError
from workspace.rules import CRITERIA_HASHES, CRITERIA_IDS

MAX_FILE_BYTES = 50 * 1024 * 1024
CHUNK_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
STALE_JOB_MS = 16 * 60 * 1000
from workspace.intake import EXTENSIONS
PURPOSES = frozenset({"reference", "component", "token", "skill", "guide", "prototype", "archive"})
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_EDITABLE = ("schemaVersion", "title", "brief", "assetIds", "viewport", "rules", "unresolved", "bindings", "guideRefs", "requiredStates", "changeRequest")
_BASE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
_JOB_TARGETS = {"finalize": ("asset", "assetId"), "run": ("run", "runId"), "release": ("release", "releaseId"),
                "git": ("gitexport", "exportId"), "document-finalize": ("docrevision", "revisionId"),
                "document-analysis": ("docanalysis", "analysisId"), "intake-image": ("adm_decision", "decisionId")}


class HTTPError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise HTTPError(400, "invalid-input", f"{label} must be an integer between {low} and {high}")
    return value


def _text(value, label, maximum, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise HTTPError(400, "invalid-input", f"Invalid {label}")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise HTTPError(400, "invalid-input", f"Invalid {label}") from error
    return value


def _public(value):
    """Do not publish storage addresses or the private upload manifest."""
    if isinstance(value, dict):
        visible = {key: _public(item) for key, item in value.items()
                   if key not in ("pk", "sk", "owner", "parts", "key", "requestHash")
                   and not key.endswith("Key")}
        for kind in ("html", "screenshot", "diff", "report", "source", "dist", "manifest", "candidate"):
            if kind + "Key" in value:
                visible["has" + kind.capitalize()] = bool(value[kind + "Key"])
        return visible
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _json(status, payload):
    return {"statusCode": status, "headers": {**_BASE_HEADERS, "Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(_public(payload), ensure_ascii=False, allow_nan=False), "isBase64Encoded": False}


def _body(event, binary=False):
    body = event.get("body") or ""
    maximum = CHUNK_BYTES if binary else MAX_JSON_BYTES
    if not isinstance(body, (str, bytes)):
        raise HTTPError(400, "invalid-body", "Invalid request body")
    if len(body) > (4 * ((maximum + 2) // 3) if event.get("isBase64Encoded") else maximum):
        raise HTTPError(413, "too-large", "Request body exceeds the size limit")
    try:
        if event.get("isBase64Encoded"):
            data = base64.b64decode(body, validate=True)
        else:
            data = body.encode("utf-8") if isinstance(body, str) else body
        if len(data) > maximum:
            raise HTTPError(413, "too-large", "Request body exceeds the size limit")
        if binary:
            return data
        value = json.loads(data.decode("utf-8")) if data else {}
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise HTTPError(400, "invalid-body", "Invalid request body") from error


class Route(NamedTuple):
    """One registered workspace route and the gate views its responses are serialized through.

    `views` maps response fields to authorized views; `fields` are plain fields
    derived from the gated resource (or carrying no source-bound content);
    `resource` pre-authorizes the addressed `:id` record before any processing;
    `round` names where a run-round target comes from ("query", "baseline",
    "body"); `blob` is the authorized byte view; `public` routes carry only
    platform-wide configuration.
    """
    method: str
    pattern: tuple
    views: dict = {}
    fields: tuple = ()
    resource: str | None = None
    blob: str | None = None
    round: str | None = None
    public: bool = False


def _r(method, path, **options):
    return Route(method, tuple(path.split("/")), **options)


# The complete project-scoped workspace route inventory. `match_route` dispatches
# nothing else, so a route that is not registered here is unreachable, and a
# registered route's response always passes `ResponseGate.finish`.
ROUTES = (
    _r("GET", "config", public=True),
    _r("GET", "components", public=True),
    _r("GET", "git-connections", public=True),
    _r("GET", "assets", views={"assets": "asset[]"}, fields=("cursor",)),
    _r("POST", "assets", views={"asset": "asset"}, fields=("chunkBytes",)),
    _r("GET", "assets/:id", resource="asset", views={"asset": "asset"}, fields=("analysis",)),
    _r("DELETE", "assets/:id", resource="asset", views={"asset": "asset"}),
    _r("GET", "assets/:id/guidelines", resource="asset", fields=("sources", "pages", "total", "cursor")),
    _r("PUT", "assets/:id/parts/:part", resource="asset", views={"asset": "asset"}, fields=("index", "sha256")),
    _r("POST", "assets/:id/complete", resource="asset", views={"asset": "asset", "job": "job"}),
    _r("GET", "assets/:id/blob", resource="asset", blob="asset"),
    _r("GET", "contracts", views={"contracts": "contract[]"}, fields=("cursor",)),
    _r("POST", "contracts", views={"contract": "contract"}),
    _r("POST", "contracts/propose", views={"job": "job"}),
    _r("GET", "contracts/:id", resource="contract", views={"contract": "contract"}),
    _r("PUT", "contracts/:id", resource="contract", views={"contract": "contract"}),
    _r("POST", "contracts/:id/approve", resource="contract", views={"contract": "contract"}),
    _r("GET", "runs", views={"runs": "run[]"}, fields=("cursor",)),
    _r("POST", "runs", views={"job": "job", "run": "run"}),
    _r("GET", "runs/:id", resource="run", views={"run": "run"}),
    _r("POST", "runs/:id/approve", resource="run", round="body", views={"run": "run"}),
    _r("GET", "runs/:id/baseline", resource="run", round="baseline", views={"baseline": "baseline"},
       fields=("files",)),
    _r("GET", "runs/:id/blob", resource="run", round="query", blob="run-round"),
    _r("GET", "jobs/:id", resource="job", views={"job": "job"}),
    _r("GET", "batches", views={"batches": "batch[]"}, fields=("cursor",)),
    _r("POST", "batches", views={"batch": "batch", "runs": "run[]"}),
    _r("GET", "batches/:id", resource="batch", views={"batch": "batch", "runs": "run[]"}),
    _r("GET", "releases", views={"releases": "release[]"}, fields=("cursor",)),
    _r("POST", "releases", round="body", views={"release": "release", "job": "job"}),
    _r("GET", "releases/:id", resource="release", views={"release": "release"}),
    _r("GET", "releases/:id/blob", resource="release", blob="release"),
    _r("POST", "releases/:id/git", resource="release", views={"export": "export", "release": "release", "job": "job"}),
    _r("GET", "publications", views={"publications": "publication[]", "grants": "granted[]"}, fields=("cursor",)),
    _r("POST", "publications", views={"publication": "publication"}),
    _r("GET", "publications/:id", views={"publication": "publication"}),
    _r("GET", "publications/:id/impact", views={"publicationId": "impact-origin", "dependents": "impact-dependents"},
       fields=("coverage",)),
    _r("POST", "publications/:id/approve", views={"publication": "publication"}),
    _r("POST", "publications/:id/withdraw", views={"publication": "publication-withdrawn"}),
    _r("POST", "publications/:id/grants", views={"grant": "grant", "reference": "published-ref"}),
    # Delegated-module routes that return run/round-derived metadata are gated here too.
    _r("GET", "products/:id/impact", views={"affectedRuns": "run-summary[]"}, fields=("currentGuidelineId", "cursor")),
    _r("GET", "comments", round="anchor", views={"comments": "comment[]"}, fields=("cursor",)),
    _r("POST", "comments", round="anchor", views={"comment": "comment"}),
)

# Prefixes whose modules own their authority (collaboration, ontology, intake review,
# workbench and the document library); they are dispatched by name, never by fallthrough.
# Their routes that return run, round or other source-derived workspace metadata are
# registered in ROUTES above and served through the response gate instead.
DELEGATED = {"projects": "workspace.collaboration", "products": "workspace.collaboration",
             "comments": "workspace.collaboration", "ontology": "workspace.ontology_api",
             "intake": "intake.review", "workbench": "workbench.api", "documents": "documents.api",
             "impact-analyses": "documents.analysis"}


def match_route(method, parts):
    """The registered route for a request, or None; literal segments win over `:param` ones."""
    best = None
    for route in ROUTES:
        if route.method != method or len(route.pattern) != len(parts):
            continue
        params, literal = {}, 0
        for expected, actual in zip(route.pattern, parts):
            if expected.startswith(":"):
                if not actual:
                    break
                params[expected[1:]] = actual
            elif expected == actual:
                literal += 1
            else:
                break
        else:
            if best is None or literal > best[0]:
                best = (literal, route, params)
    return (best[1], best[2]) if best else None


def _missing():
    return HTTPError(404, "not-found", "Resource not found")


class ResponseGate:
    """The single response-layer authorization of project-scoped workspace resources.

    Every registered route's response is serialized through authorized views that
    consult the shared `Sources` lineage reader: each view authorizes on a probe
    reader absorbed into ONE aggregate reader, and `finish` runs one final
    aggregate recheck (plus retained destination readers) after all storage reads,
    immediately before the response is returned. An inaccessible singular record
    is the same `404` as a missing one; inaccessible list rows are omitted.
    """

    VIEWS = frozenset({"asset", "asset[]", "contract", "contract[]", "run", "run[]", "release", "release[]",
                       "job", "batch", "batch[]", "export", "baseline", "publication", "publication[]",
                       "publication-withdrawn", "granted[]", "grant", "published-ref", "impact-origin",
                       "impact-dependents", "run-summary[]", "comment", "comment[]"})
    BLOBS = frozenset({"asset", "run-round", "release"})
    _STORED = {"asset": "asset", "contract": "contract", "run": "run", "release": "release", "job": "job",
               "batch": "batch", "export": "gitexport"}
    # Tasks whose job records carry their own authority (asset upload, workbench,
    # document and intake jobs) are authorized by their owning modules.
    SELF_AUTHORIZED_TASKS = ("finalize", "workbench", "document-finalize", "document-analysis", "intake-image")

    def __init__(self, api, scope, claims, route):
        self.api, self.scope, self.route = api, scope, route
        self.context = api._lineage_context(scope, claims) if api is not None else None
        self.aggregate = None
        if self.context is not None:
            from workspace.ontology_sources import aggregate_reader
            self.aggregate = aggregate_reader(self.context)
        self._views, self._rounds, self.retained = {}, set(), {}
        self.prepared, self.record = False, None

    @property
    def owner(self):
        return self.scope["owner"]

    # authorization ------------------------------------------------------------

    def authorize(self, view, record):
        """The authorized view of one stored record, or None when it is inaccessible."""
        if not isinstance(record, dict):
            return None
        if self.context is None:
            return record
        key = view, record.get("id"), record.get("version")
        if key in self._views:
            return self._views[key]
        from workspace.ontology_sources import _AUTHORITY_CODES, Sources
        reader = Sources(self.context)
        try:
            value = self._authorize(view.removesuffix("[]"), reader, record)
        except CollaborationError as error:
            if error.status == 401 or error.code in _AUTHORITY_CODES:
                raise
            value = None
        if value is not None:
            self.aggregate.absorb(reader)
        self._views[key] = value
        return value

    def _authorize(self, kind, reader, record):
        storage = self.api.storage
        if kind == "contract":
            return reader.contract_access(record)
        if kind == "run":
            return reader.run_access(record)
        if kind == "release":
            reader.round_delivery(record.get("runId"), record.get("round"))
            return record
        if kind == "asset":
            return reader.asset_access(record)
        if kind == "batch":
            # The batch's OWN pinned contract revision, not whatever the contract has
            # since become: the exact same reference and check a run's own contract
            # lineage uses (`_contract_ref` + historical `_contract`), so an edited-away
            # or revoked pinned input denies the batch exactly like it denies its runs.
            ref = reader._contract_ref(record)
            reader._contract(ref, historical=True)
            return record
        if kind == "export":
            release = (storage.get(self.owner, "release", record["releaseId"])
                       if isinstance(record.get("releaseId"), str) else None)
            if release is None:
                return None
            reader.round_delivery(release.get("runId"), release.get("round"))
            return record
        if kind == "job":
            return self._job(reader, record)
        raise ValueError("Unknown gate view")

    def _job(self, reader, job):
        """A content-bearing job is authorized like its resource GET."""
        storage = self.api.storage
        task, data = job.get("task"), job.get("input") if isinstance(job.get("input"), dict) else {}
        if task in self.SELF_AUTHORIZED_TASKS:
            return self._module_job(reader, task, data, job)
        if task == "propose":
            reader.inputs_access(data)
        elif task == "run":
            run = storage.get(self.owner, "run", data.get("runId")) if isinstance(data.get("runId"), str) else None
            if run is None:
                return None
            reader.run_access(run)
        elif task in ("release", "git"):
            release_id = data.get("releaseId")
            if task == "git":
                exported = (storage.get(self.owner, "gitexport", data.get("exportId"))
                            if isinstance(data.get("exportId"), str) else None)
                release_id = (exported or {}).get("releaseId")
            release = storage.get(self.owner, "release", release_id) if isinstance(release_id, str) else None
            if release is None:
                return None
            reader.round_delivery(release.get("runId"), release.get("round"))
        else:
            return None
        return job

    def _module_job(self, reader, task, data, job):
        """Jobs whose inputs are owned by another module: that module's read authority applies.

        Every observation joins the response's aggregate reader (retained until the
        final recheck); an inaccessible input makes the job a missing one.
        """
        ctx, storage = self.context, self.api.storage
        if task == "finalize":
            asset = storage.get(self.owner, "asset", data["assetId"]) if isinstance(data.get("assetId"), str) else None
            return job if asset is not None and reader.asset_access(asset) else None
        if task == "intake-image":
            if data.get("projectId") != ctx.project_id or not isinstance(data.get("sourceRef"), dict):
                return None
            reader.authorize(data["sourceRef"])
            return job
        if task in ("document-finalize", "document-analysis"):
            from documents.errors import DocumentError
            from documents.library import authorize_job
            try:
                checks = authorize_job(self.api, self.scope, job)
            except DocumentError as error:
                # The document library's own contract decides the denial (creator-only 403).
                raise HTTPError(error.status, error.code, error.message) from None
            for check in checks or []:
                reader._remember_owned(check["owner"], check["kind"], check)
            return job
        # workbench
        if data.get("projectId") != ctx.project_id:
            return None
        operation = data.get("operation")
        if operation == "ontology-analyze":
            refs = data.get("sourceRefs")
            if not isinstance(refs, list) or not refs:
                return None
            for ref in refs:
                reader.authorize(ref)
            return job
        if operation in ("index", "skill-propose", "skill-validate", "skill-execute"):
            from workbench import knowledge
            versions = data.get("sourceVersions")
            if not isinstance(versions, dict):
                return None
            for source_id in versions:
                source = ctx.get("wb_source", source_id)
                if not knowledge.source_current(ctx, source):
                    return None
                reader._remember("wb_source", source)
            if operation != "index":
                skill = (storage.get(self.owner, "wb_skill", data["skillId"])
                         if isinstance(data.get("skillId"), str) else None)
                if skill is None or skill.get("projectId") != ctx.project_id:
                    return None
                refs = skill.get("sourceRefs", [])
                authority = "canonical" if refs and all(isinstance(r, dict) and "sourceKind" in r for r in refs) \
                    else "legacy"
                for check in knowledge.authorize_refs(ctx, refs, authority=authority) or []:
                    reader._remember_owned(check["owner"], check["kind"], check)
            return job
        if operation == "pension_ask":
            # A private answer job: readable only by the member who asked.
            return job if data.get("actor") == ctx.actor else None
        return None

    def round(self, run_id, number):
        """publishing-handoff/1 delivery gate for one round (state permission + upstream lineage)."""
        if self.context is None:
            return
        if (run_id, number) in self._rounds:
            return
        from workspace.ontology_sources import Sources
        reader = Sources(self.context)
        try:
            reader.round_delivery(run_id, number)
        except CollaborationError as error:
            if error.status == 404:
                # Inaccessible equals missing: the same body as a missing run or round.
                raise _missing() from None
            raise
        self.aggregate.absorb(reader)
        self._rounds.add((run_id, number))

    def retain(self, project_id, recheck):
        """Keep a reader of another project scope (impact destinations) for the final recheck."""
        self.retained[project_id] = recheck

    def prepare(self, params, query, event):
        """Resource and round gates before any processing: inaccessible is a missing record."""
        route = self.route
        if route.resource:
            record = self.api._get(self.owner, self._STORED[route.resource], params["id"])
            if self.authorize(route.resource, record) is None:
                raise _missing()
            self.record = record
        if route.round == "anchor":
            self._anchor(query, event)
        elif route.round:
            run_id, number = self._round_target(params, query, event)
            if run_id is not None:
                self.round(run_id, number)
        self.prepared = True

    def _anchor(self, query, event):
        """A run-anchored discussion (query filter or new comment) names an authorized run and round.

        New comments always persist the exact round that produced their page (see
        `Collaboration._anchor`), so an explicit `round` is the normal case here. A
        page anchor with no `round` at all only occurs on a comment stored before
        that fix: fall back to authorizing EVERY round that could have produced the
        page (conservative -- a page ambiguous between two rounds is denied unless
        both authorize), never just the first match.
        """
        anchor = query if self.route.method == "GET" else (_body(event).get("anchor") or {})
        if not isinstance(anchor, dict) or not isinstance(anchor.get("runId"), str):
            return
        run = self.api.storage.get(self.owner, "run", anchor["runId"])
        if run is None or self.authorize("run", run) is None:
            raise _missing()
        number = anchor.get("round")
        if isinstance(number, str) and number.isdigit():
            number = int(number)
        if type(number) is int:
            self.round(run["id"], number)
        elif isinstance(anchor.get("pageId"), str):
            for candidate in self._page_rounds(run, anchor["pageId"]):
                self.round(run["id"], candidate)

    @staticmethod
    def _page_rounds(run, page_id):
        """Every round number whose produced pages include `page_id`."""
        return [row.get("number") for row in (run.get("rounds", []) if isinstance(run, dict) else [])
                if isinstance(row, dict) and any(isinstance(entry, dict) and entry.get("pageId") == page_id
                                                 for entry in row.get("pageSources", []))]

    def _run_visible(self, run_id, number=None, page_id=None):
        """The run (and, with `number` or a round-produced `page_id`, that round/those
        rounds) is readable now. A page with no `round` binding at all (a comment
        stored before rounds were persisted) is visible only if EVERY round that
        could have produced it is authorized -- never just one of several candidates."""
        run = self.api.storage.get(self.owner, "run", run_id) if isinstance(run_id, str) else None
        authorized = self.authorize("run", run) if run else None
        if authorized is None:
            return False
        if number is None and isinstance(page_id, str):
            numbers = self._page_rounds(run, page_id)
            if not numbers:
                return True  # A declared (not round-specific) page: plain run visibility.
            allowed = {row.get("number") for row in authorized.get("rounds", []) if isinstance(row, dict)}
            return all(candidate in allowed for candidate in numbers)
        if number is None:
            return True
        return any(isinstance(row, dict) and row.get("number") == number for row in authorized.get("rounds", []))

    def _round_target(self, params, query, event):
        if self.route.round == "query":
            return params["id"], WorkspaceAPI._query_int(query, "round", 1, 5, minimum=1)
        if self.route.round == "baseline":
            try:
                number = int(query.get("round", (self.record.get("approval") or {}).get("round", 0)))
            except (ValueError, TypeError):
                raise _missing() from None
            return params["id"], number
        body = _body(event)
        run_id, number = params.get("id", body.get("runId")), body.get("round")
        if not isinstance(run_id, str) or type(number) is not int:
            return None, None  # The handler rejects the malformed request.
        return run_id, number

    # serialization ------------------------------------------------------------

    def _unavailable(self):
        return HTTPError(503, "response-not-authorized", "Workspace operation is unavailable. Retry or inspect the job.")

    def serialize(self, view, value):
        if view.endswith("[]"):
            if not isinstance(value, list):
                raise self._unavailable()
            return [row for row in (self._one(view[:-2], item) for item in value) if row is not None]
        if view == "impact-dependents":
            if not isinstance(value, list):
                raise self._unavailable()
            # Only destinations whose reader is retained for the final recheck may contribute.
            return [row for row in value if isinstance(row, dict) and row.get("projectId") in self.retained]
        result = self._one(view, value)
        if result is None:
            raise _missing()
        return result

    def _one(self, view, item):
        if self.context is None:
            return item
        if view in self._STORED:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise self._unavailable()
            stored = self.api.storage.get(self.owner, self._STORED[view], item["id"])
            authorized = self.authorize(view, stored) if stored else None
            if authorized is None:
                return None
            if item.get("version") != stored.get("version"):
                # The record moved since this response was built. Ordinary progress
                # (a job the worker has since claimed, a round it has since recorded)
                # bumps the version too, and must not 404 a perfectly normal, still-
                # authorized submission -- but the OLDER item's own content must never
                # be released once a NEWER version exists, since that content was
                # never re-authorized (generalizes the exact-revision fence
                # publications already applies via `publication_visible`). Serve the
                # CURRENT, just-authorized record's own content instead of the stale
                # item: `authorized` already decided whether the CURRENT version may
                # be released at all. `item` (from the already-serialized response
                # body) went through `_public()` in `_json()`; `authorized` is the
                # raw stored record and has not -- run it through the same filter
                # before it substitutes for `item`, or private storage keys/fields
                # (assetSnapshots[].originalKey/analysisKey, requestHash, etc.) leak.
                item = _public(authorized)
            if view == "run":
                numbers = {row.get("number") for row in authorized.get("rounds", []) if isinstance(row, dict)}
                item = {**item, "rounds": [row for row in item.get("rounds", [])
                                           if isinstance(row, dict) and row.get("number") in numbers]}
            elif view == "batch":
                # The pinned-contract check above only gates the batch as a whole; each
                # emitted run reference (runIds, baselineRunId, slots) is independently
                # authorized too, so a run inaccessible on its own (e.g. its own upstream
                # revoked) never surfaces its ID here either.
                visible = {run_id for run_id in item.get("runIds", [])
                          if isinstance(run_id, str) and self._run_visible(run_id)}
                item = {**item, "runIds": [run_id for run_id in item.get("runIds", []) if run_id in visible],
                       "baselineRunId": item.get("baselineRunId") if item.get("baselineRunId") in visible else None,
                       "slots": [dict(slot, runId=None) if isinstance(slot, dict) and slot.get("runId")
                                and slot["runId"] not in visible else slot
                                for slot in item["slots"]] if isinstance(item.get("slots"), list) else item.get("slots")}
            return item
        if view == "baseline":
            if not isinstance(item, dict):
                raise self._unavailable()
            self.round(item.get("runId"), item.get("round"))
            return item
        if view == "run-summary":
            if not isinstance(item, dict):
                raise self._unavailable()
            return item if self._run_visible(item.get("id")) else None
        if view == "comment":
            if not isinstance(item, dict):
                raise self._unavailable()
            anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
            if "runId" in anchor and not self._run_visible(anchor["runId"], anchor.get("round"), anchor.get("pageId")):
                return None
            return item
        return self._publication_view(view, item)

    def _publication_view(self, view, item):
        from workspace import publications
        from workspace.ontology_sources import _AUTHORITY_CODES
        ctx, storage = self.context, self.api.storage
        try:
            if view == "impact-origin":
                record = publications._origin_publication(ctx, item)
                if not publications._origin_readable(ctx, record, self.aggregate):
                    publications._not_found()
                return item
            if view in ("publication", "publication-withdrawn"):
                record = publications._publication(storage, item.get("id") if isinstance(item, dict) else None)
                if view == "publication":
                    # The exact returned revision (and its sources) is authorized and fenced.
                    return item if publications.publication_visible(ctx, record, self.aggregate, item) else None
                if (not record or record.get("originProject") != ctx.project_id
                        or any(item.get(key) != record.get(key) for key in ("revision", "hash", "status"))):
                    return None
                self.aggregate._remember_owned(publications.PUBLICATION_OWNER, "publication", record)
                if not publications._origin_readable(ctx, record, self.aggregate):
                    item = {key: value for key, value in item.items() if key not in ("nodes", "sharing")}
                return item
            if view in ("grant", "granted"):
                identifier = item.get("id") if isinstance(item, dict) else None
            elif view == "published-ref":
                revision = item.get("revision") if isinstance(item, dict) else None
                if not isinstance(revision, str) or not revision.isdigit():
                    return None
                identifier = publications.grant_id(item.get("sourceId"), int(revision))
            else:
                raise ValueError("Unknown gate view")
            row = storage.get(ctx.owner, "pub_grant", identifier) if isinstance(identifier, str) else None
            if view == "granted":
                return item if publications._granted_view(ctx, row, self.aggregate) is not None else None
            return item if publications.grant_visible(ctx, row, self.aggregate) else None
        except CollaborationError as error:
            if error.status == 401 or error.code in _AUTHORITY_CODES:
                raise
            return None

    def recheck(self):
        """The final aggregate recheck, normalized exactly like `authorize()`.

        `authorize()` already catches a non-401/non-authority-code
        `CollaborationError` and converts it to `None` -> 404 (review 9's
        fix to `_existing_job` replicated this locally for one raise site).
        This recheck -- reached from `finish()` on every ordinary successful
        response -- let the SAME class of failure escape raw (e.g. a private
        "ontology-source-changed" 409), disclosing that a race landed here
        rather than 404ing identically to an already-inaccessible record.
        Normalize it here once, so every caller of this recheck (not just
        the sites that already do their own local try/except) is covered.
        """
        from workspace.ontology_sources import _AUTHORITY_CODES
        try:
            if self.aggregate is not None:
                self.aggregate.recheck()
            for recheck in self.retained.values():
                recheck()
        except CollaborationError as error:
            if error.status == 401 or error.code in _AUTHORITY_CODES:
                raise
            raise HTTPError(404, "not-found", "Resource not found") from None

    def finish(self, response):
        """Serialize the response through its declared views; one final recheck, then release it."""
        route = self.route
        if (not isinstance(response, dict) or not 200 <= response.get("statusCode", 0) < 300
                or route is None or route.public):
            return response
        if response.get("isBase64Encoded"):
            if not route.blob or not self.prepared:
                raise self._unavailable()
            self.recheck()  # After the chunk's bytes were read, before they leave.
            return response
        try:
            payload = json.loads(response["body"])
        except (KeyError, TypeError, ValueError):
            raise self._unavailable() from None
        if not isinstance(payload, dict) or set(payload) - set(route.views) - set(route.fields):
            # An undeclared response field is never released unauthorized.
            raise self._unavailable()
        if route.method != "GET" and self.record is not None and self.aggregate is not None:
            # The addressed record is this write's output: its pre-write observation is
            # replaced by the authorization of the version now stored (serialized below).
            self.aggregate.observed.pop((self.owner, self._STORED[route.resource], self.record["id"]), None)
        for key, view in route.views.items():
            if payload.get(key) is not None:
                payload[key] = self.serialize(view, payload[key])
        self.recheck()
        return {**response, "body": json.dumps(payload, ensure_ascii=False, allow_nan=False)}


class WorkspaceAPI:
    def __init__(self, storage=None, lambda_client=None, worker_fn=None, rules=None, directory=None, collaboration=None,
                 git_connections=None):
        self.storage = storage if storage is not None else Storage()
        self.lambda_client = lambda_client
        self.worker_fn = worker_fn if worker_fn is not None else os.environ.get("WORKSPACE_WORKER_FN", "")
        self._rules = rules
        if directory is None and os.environ.get("WORKSPACE_USER_POOL_ID"):
            from workspace.directory import CognitoDirectory
            directory = CognitoDirectory(os.environ["WORKSPACE_USER_POOL_ID"])
        self.collaboration = collaboration if collaboration is not None else Collaboration(self.storage, directory=directory)
        self.ontology_mode = os.environ.get("PROJECT_ONTOLOGY_MODE", "legacy")
        if self.ontology_mode not in {"legacy", "canonical"}:
            raise ValueError("Unknown project ontology backend")
        self.collaboration.ontology_enabled = self.ontology_mode == "canonical"
        # The foundation does not install a cloud adapter. The adapter milestone
        # supplies a verified readiness installer; an environment label cannot.
        self.ontology_analyzer_ready = False
        from workspace.git_service import configured_connections
        self.git_connections = git_connections or configured_connections
        from workbench.runtime import install
        install(self)

    def rules(self):
        if self._rules is None:
            from workspace import rules
            self._rules = rules
        return self._rules

    def handle(self, event, context=None):
        try:
            context_data = event.get("requestContext") or {}
            authorizer = context_data.get("authorizer")
            jwt = authorizer.get("jwt") if isinstance(authorizer, dict) else None
            claims = jwt.get("claims") if isinstance(jwt, dict) else None
            claims = claims if isinstance(claims, dict) else {}
            owner = claims.get("sub")
            if (not isinstance(owner, str) or not owner.strip() or len(owner) > 256
                    or claims.get("token_use") != "access"):
                raise HTTPError(401, "unauthorized", "An authorized access token is required")
            method = (context_data.get("http") or {}).get("method", event.get("httpMethod", "")).upper()
            path = event.get("rawPath") or event.get("path") or ""
            if path == "/studio-api":
                path = "/"
            elif path.startswith("/studio-api/"):
                path = path[len("/studio-api"):]
            if not path.startswith("/") or "//" in path:
                raise HTTPError(404, "not-found", "Resource not found")
            segments = path.strip("/").split("/")
            query = event.get("queryStringParameters") or {}
            headers = event.get("headers") or {}
            if not isinstance(headers, dict) or not isinstance(query, dict):
                raise HTTPError(400, "invalid-input", "Invalid request headers or query")
            project_headers = [value for key, value in headers.items() if key.lower() == "x-workspace-project"]
            if len(project_headers) > 1:
                raise HTTPError(400, "invalid-project", "Only one workspace project may be selected")
            project_id = project_headers[0] if project_headers else None
            if segments[0] in ("projects", "products", "comments") and (
                    segments[0] == "projects" or match_route(method, segments) is None):
                # Routes registered in ROUTES (run-derived metadata) take the gated path below.
                response = self.collaboration.handle(method, segments, _body(event) if method in ("POST", "PUT", "PATCH") else {},
                                                       query, owner, project_id,
                                                       authorization_expires_at=int(claims["exp"]) * 1000 if claims.get("exp") is not None else None)
                if response is not None:
                    return _json(response[0], response[1])
            scope = self.collaboration.resolve_scope(owner, project_id)
            if segments[0] == "ontology":
                from workspace.ontology_api import route
                scope = self.collaboration.require(scope, "read")
                result = route(self, scope, claims, method, segments[1:],
                               _body(event) if method in ("POST", "PUT", "PATCH") else {}, query)
                return _json(result[0], result[1])
            if segments[0] == "intake":
                # Reviewer routes only: JWT + a current IAM-administered grant.
                # Policy/provenance/grant administration is never reachable here.
                from intake.review import route
                scope = self.collaboration.require(scope, "read")
                result = route(self, scope, claims, method, segments[1:],
                               _body(event) if method in ("POST", "PUT", "PATCH") else {}, query)
                return _json(result[0], result[1])
            if segments[0] == "workbench":
                from workbench.api import route
                scope = self.collaboration.require(scope, "read")
                result = route(self, scope, claims, method, segments[1:],
                               _body(event) if method in ("POST", "PUT", "PATCH") else {}, query)
                return _json(result[0], result[1])
            if segments[0] in ("documents", "impact-analyses"):
                # Delegated: the document library owns its authority (DELEGATED).
                scope = self.collaboration.require(scope, "read")
                return self._route(scope["owner"], method, segments, event, query, scope=scope, claims=claims)
            matched = match_route(method, segments)
            if matched is None:
                raise HTTPError(404, "not-found", "Route not found")
            route, params = matched
            action = "read"
            if method != "GET":
                if segments[0] == "assets":
                    action = "upload"
                elif segments[0] == "contracts":
                    action = "edit_rules"
                elif segments[0] in ("runs", "batches"):
                    action = "approve" if segments[-1] == "approve" else "generate"
                elif segments[0] == "releases":
                    action = "export" if segments[-1] == "git" else "release"
            scope = self.collaboration.require(scope, action)
            # The single response gate: resource/round authorization before processing,
            # authorized serialization and one final recheck before release.
            gate = ResponseGate(self, scope, claims, route)
            gate.prepare(params, query, event)
            if segments[0] == "publications":
                # Capability grants are IAM-only (intake admin entry point); no route writes them.
                from workspace.publications import route as publication_route
                status, payload = publication_route(self, scope, claims, method, segments[1:],
                                                    _body(event) if method in ("POST", "PUT", "PATCH") else {},
                                                    query, gate=gate)
                response = _json(status, payload)
            else:
                response = self._route(scope["owner"], method, segments, event, query, scope=scope, claims=claims,
                                       gate=gate)
            return gate.finish(response)
        except CollaborationError as error:
            return _json(error.status, {"error": error.message, "code": error.code})
        except HTTPError as error:
            return _json(error.status, {"error": error.message, "code": error.code})
        except Conflict:
            return _json(409, {"error": "The resource has changed; reload and retry.", "code": "conflict"})
        except FileNotFoundError:
            return _json(404, {"error": "The requested private artifact is not available.", "code": "not-found"})
        except ValueError:
            return _json(400, {"error": "Invalid request or resource data.", "code": "invalid-input"})
        except Exception:
            # Never return or log SDK messages, prompts, credentials, or tokens.
            return _json(503, {"error": "Workspace operation is unavailable. Retry or inspect the job.", "code": "unavailable"})

    def _get(self, owner, kind, identifier):
        record = self.storage.get(owner, kind, identifier)
        if record is None:
            raise HTTPError(404, "not-found", "Resource not found")
        return record

    def _authorized_run(self, owner, identifier, gate=None):
        """The current, accessible run (its round list already filtered by
        permission), or a 404 identical to a missing one.

        A run whose own upstream lineage is revoked must never be distinguishable
        from a missing/foreign one by a DIFFERENT, content-dependent status (e.g.
        a refinement-compatibility or base-round check reached using its stale
        fields) -- authorize it the same way a plain GET's response gate would,
        before any of its fields are read for that purpose.
        """
        stored = self._get(owner, "run", identifier)
        run = gate.authorize("run", stored) if gate is not None else stored
        if run is None:
            raise HTTPError(404, "not-found", "Resource not found")
        return run

    def _authorized_contract(self, owner, identifier, gate=None):
        """The current, accessible contract, or a 404 identical to a missing one.

        A contract whose own lineage (its referenced assets, historically, or its
        change-request baseline) is revoked must never be distinguishable from a
        missing/foreign one by a DIFFERENT, content-dependent status (a
        criteria/catalog-compatibility check reached using its stale
        catalogHash/guidelineId/version before authorization) -- authorize it the
        same way a plain GET's response gate would, before any of its fields are
        read for that purpose.
        """
        stored = self._get(owner, "contract", identifier)
        contract = gate.authorize("contract", stored) if gate is not None else stored
        if contract is None:
            raise HTTPError(404, "not-found", "Resource not found")
        return contract

    def _run_view(self, owner, record, scope, cache=None, seen=()):
        """Report current criteria independently of the browser's product filter."""
        cache = {} if cache is None else cache
        if record.get("id") in seen or len(seen) >= 25:
            return {**record, "needsRevalidation": True}
        view_key = "view", record.get("id"), record.get("version")
        if view_key in cache:
            return cache[view_key]
        def read(kind, identifier):
            if not identifier:
                return None
            key = kind, identifier
            if key not in cache:
                cache[key] = self.storage.get(owner, kind, identifier)
            return cache[key]
        contract = read("contract", record.get("contractId"))
        stale = (not contract or contract.get("status") != "approved"
                 or contract.get("version") != record.get("contractVersion")
                 or (contract.get("approval") or {}).get("hash") != record.get("contractHash"))
        project_id = (scope.get("project") or {}).get("id") if scope else None
        if record.get("projectId") != project_id:
            stale = True
        if record.get("productId"):
            product = read("product", record["productId"])
            stale = stale or (not product or product.get("publishedGuidelineId") != record.get("guidelineId")
                              or product.get("ontologyHash") != record.get("ontologyHash"))
        elif project_id:
            stale = True
        if record.get("catalogHash"):
            if "catalogHash" not in cache:
                from workspace.component_catalog import read_catalog
                cache["catalogHash"] = read_catalog()["hash"]
            stale = stale or record["catalogHash"] != cache["catalogHash"]
        reference = (record.get("contract") or {}).get("changeRequest", {}).get("baseline")
        if not stale and reference:
            parent = read("run", reference["runId"])
            approval = (parent or {}).get("approval") or {}
            selected = next((row for row in (parent or {}).get("rounds", []) if row.get("number") == reference["round"]), None)
            stale = (not parent or not selected or selected.get("passed") is not True
                     or approval.get("round") != reference["round"] or selected.get("sourceHash") != reference["sourceHash"]
                     or any(not selected.get(key) or approval.get(key) != selected[key]
                            for key in ("sourceHash", "bundleHash", "catalogHash")))
            if not stale:
                stale = self._run_view(owner, parent, scope, cache, (*seen, record.get("id")))["needsRevalidation"]
        result = {**record, "needsRevalidation": bool(stale)}
        cache[view_key] = result
        return result

    def _expire_job(self, owner, job, scope=None):
        """One CAS attempt per read; a concurrent heartbeat or completion wins."""
        if job.get("task") == "workbench" and job.get("status") == "failed" and job.get("errorCode") == "job-timeout":
            from workbench.worker import _mark_failed
            _mark_failed(self, owner, job, "job-timeout")
            return job
        if job.get("task") in ("document-finalize", "document-analysis"):
            from documents.jobs import expire_raw_job
            if scope is None:
                raise HTTPError(403, "forbidden", "Document scope is required")
            return expire_raw_job(self, scope, job)
        updated = job.get("updatedAt")
        now = self.storage.clock()
        if (job.get("status") not in ("queued", "running") or type(updated) is not int
                or now - updated <= STALE_JOB_MS):
            return job
        try:
            saved = self.storage.put(owner, "job", {
                **job, "status": "failed", "stopReason": "timeout", "errorCode": "job-timeout",
                "error": "작업 진행이 16분 이상 갱신되지 않아 종료했습니다. 다시 실행해 주세요.",
                "finishedAt": now,
            }, job["version"])
            if job.get("task") == "workbench":
                from workbench.worker import _mark_failed
                _mark_failed(self, owner, saved, "job-timeout")
            return saved
        except Conflict:
            return self._get(owner, "job", job["id"])

    def _blob_key(self, owner, key):
        if not key or not self.storage.owns_key(owner, key):
            raise HTTPError(404, "not-found", "Private artifact not found")
        return key

    def _lineage_context(self, scope, claims):
        """A request context for the shared lineage reader, or None in the single-owner workspace.

        The single-owner legacy workspace has no other audience and no admissions.
        """
        if not scope or not scope.get("project"):
            return None
        from workbench.service import Service
        return Service(self, scope, claims or {})

    def _authorized_list(self, gate, owner, kind, name, query, scope):
        """A listing of authorized rows only, with opaque caller-bound cursors (ONT-09)."""
        from workspace.ontology_sources import authorized_page
        cache = {}

        def include(record):
            value = gate.authorize(kind, record)
            if value is not None and kind == "run":
                value = {**value, "needsRevalidation": self._run_view(owner, record, scope, cache)["needsRevalidation"]}
            return value
        page = authorized_page(gate.context, query, name, owner, kind, "", include,
                               purpose="workspace-list", token="pagecur", stale_code="list-cursor-stale",
                               default=100, reader=gate.aggregate)
        return {name: page["items"], **({"cursor": page["cursor"]} if "cursor" in page else {})}

    def _listing(self, gate, owner, kind, name, query, scope):
        if gate is not None and gate.context is not None:
            return _json(200, self._authorized_list(gate, owner, kind, name, query, scope))
        page = self.storage.list_page(owner, kind, limit=100, cursor=query.get("cursor"))
        if kind == "run":
            cache = {}
            page["items"] = [self._run_view(owner, record, scope, cache) for record in page["items"]]
        return _json(200, {name: page["items"], **({"cursor": page["cursor"]} if page.get("cursor") else {})})

    def _product_impact(self, gate, owner, product_id, query, scope):
        """Runs affected by a product republication, each authorized through the gate's run view.

        Rows are paged over authorized runs only (opaque caller-bound cursor), so an
        inaccessible run contributes neither metadata nor continuation.
        """
        if gate is None or gate.context is None:
            raise HTTPError(400, "project-required", "Select a project first")
        from workspace.ontology_sources import authorized_page
        product = self.collaboration._get(scope, "product", product_id)

        def include(run):
            summary = self.collaboration.impact_summary(scope, product, run)
            return summary if summary is not None and gate.authorize("run", run) is not None else None
        page = authorized_page(gate.context, query, "product-impact:" + product["id"], owner, "run", "", include,
                               purpose="product-impact", token="pagecur", stale_code="list-cursor-stale",
                               default=100, reader=gate.aggregate)
        gate.aggregate._remember("product", product)
        return _json(200, {"currentGuidelineId": product.get("publishedGuidelineId"), "affectedRuns": page["items"],
                           **({"cursor": page["cursor"]} if "cursor" in page else {})})

    def _comments_listing(self, gate, owner, scope, query):
        """Authorized comments, paged with an opaque cursor: the gate filters comments,
        but a raw storage cursor still encodes a hidden comment's own storage key even
        while it contributes nothing to the visible page. Fold the same anchor filter
        and run/round visibility (`gate._one("comment", ...)`, review 6 #2) into the
        page scan itself, so a hidden row's key never becomes a public continuation
        token either (ONT-09, no-existence-disclosure)."""
        if gate is None or gate.context is None:
            raise HTTPError(400, "project-required", "Select a project first")
        from workspace.collaboration import _ANCHORS
        from workspace.ontology_sources import authorized_page
        filters = {key: value for key, value in query.items() if key in _ANCHORS}
        anchor = self.collaboration._anchor(scope, filters) if filters else {}

        def include(row):
            if not isinstance(row, dict) or row.get("projectId") != scope["project"]["id"]:
                return None
            row_anchor = row.get("anchor") if isinstance(row.get("anchor"), dict) else {}
            if not all(row_anchor.get(key) == value for key, value in anchor.items()):
                return None
            return gate._one("comment", row)
        page = authorized_page(gate.context, query, "comments", owner, "comment", "", include,
                               purpose="comments", token="commentcur", stale_code="list-cursor-stale",
                               default=50, reader=gate.aggregate)
        return _json(200, {"comments": page["items"], **({"cursor": page["cursor"]} if "cursor" in page else {})})

    def _route(self, owner, method, parts, event, query, scope=None, claims=None, gate=None):
        """Handlers of the registered `ROUTES`; `gate` authorized the addressed resource and round,
        and serializes the response (the caller runs `gate.finish`)."""
        if parts[0] in ("documents", "impact-analyses"):
            from documents.errors import DocumentError
            try:
                if parts[0] == "documents":
                    if parts == ["documents", "samples"] and method == "POST":
                        from documents.samples import install_samples
                        return _json(202, install_samples(self, scope, _body(event)))
                    from documents.api import handle
                else:
                    from documents.analysis import handle
                return handle(self, scope, method, parts, event, query)
            except DocumentError as error:
                return _json(error.status, {"error": error.message, "code": error.code})
        if parts[0] == "products" and len(parts) == 3 and parts[2] == "impact" and method == "GET":
            return self._product_impact(gate, owner, parts[1], query, scope)
        if parts == ["comments"] and method == "GET":
            # A gated, opaquely-paged read (see `_comments_listing`); Collaboration
            # itself is used only for validating/building the query anchor filter.
            return self._comments_listing(gate, owner, scope, query)
        if parts == ["comments"]:
            # Collaboration owns discussion writes; the gate authorizes every run anchor.
            expiry = (claims or {}).get("exp")
            status, payload = self.collaboration.handle(
                method, parts, _body(event) if method == "POST" else {}, query, scope["actor"],
                (scope.get("project") or {}).get("id"),
                authorization_expires_at=int(expiry) * 1000 if expiry is not None else None)
            return _json(status, payload)
        from engine import model_catalog
        if method == "GET" and parts == ["config"]:
            from workspace.component_catalog import read_catalog
            catalog = read_catalog()
            return _json(200, {"models": model_catalog.options(), "defaultModel": model_catalog.resolve(),
                               "actorId": scope["actor"] if scope else owner,
                               "componentCatalog": {key: catalog[key] for key in ("id", "version", "label", "hash")},
                               "generationModes": ["creative", "guided"], "variationRange": {"min": 2, "max": 5},
                               "maxFileBytes": MAX_FILE_BYTES, "chunkBytes": CHUNK_BYTES, "extensions": list(EXTENSIONS)})
        if method == "GET" and parts == ["components"]:
            from workspace.component_catalog import read_catalog
            return _json(200, {"catalog": read_catalog()})
        if method == "GET" and parts == ["git-connections"]:
            from workspace.git_service import public_connections
            return _json(200, {"connections": public_connections(self.git_connections())})
        if parts == ["batches"] and method == "POST":
            from workspace.batches import create_batch
            return create_batch(self, owner, _body(event), scope, gate=gate)
        if parts == ["batches"] and method == "GET":
            return self._listing(gate, owner, "batch", "batches", query, scope)
        if len(parts) == 2 and parts[0] == "batches" and method == "GET":
            from workspace.batches import batch_view
            return _json(200, batch_view(self, owner, self._get(owner, "batch", parts[1])))
        if parts == ["releases"] and method == "POST":
            from workspace.releases import create_release
            return create_release(self, owner, _body(event), scope, gate=gate)
        if parts == ["releases"] and method == "GET":
            return self._listing(gate, owner, "release", "releases", query, scope)
        if parts[0] == "releases" and len(parts) in (2, 3) and method == "GET":
            release = self._get(owner, "release", parts[1])
            if len(parts) == 2:
                from workspace.git_service import hydrate_release
                return _json(200, {"release": hydrate_release(self.storage, owner, release)})
            if parts[2] == "blob":
                return self._release_download(owner, release, query)
        if len(parts) == 3 and parts[0] == "releases" and parts[2] == "git" and method == "POST":
            from workspace.git_service import create_export
            release = self._get(owner, "release", parts[1])
            return create_export(self, owner, release, _body(event), scope, gate=gate)
        if len(parts) == 1 and parts[0] in ("assets", "contracts", "runs") and method == "GET":
            kind = {"assets": "asset", "contracts": "contract", "runs": "run"}[parts[0]]
            return self._listing(gate, owner, kind, parts[0], query, scope)
        if parts == ["assets"] and method == "POST":
            return self._create_asset(owner, _body(event), gate=gate)
        if parts == ["contracts", "propose"] and method == "POST":
            return self._propose(owner, _body(event), scope=scope, gate=gate)
        if parts == ["contracts"] and method == "POST":
            return self._contract_create(owner, _body(event), scope=scope, gate=gate)
        if parts == ["runs"] and method == "POST":
            return self._run_create(owner, _body(event), scope=scope, gate=gate)
        if len(parts) < 2 or parts[0] not in ("assets", "contracts", "runs", "jobs"):
            raise HTTPError(404, "not-found", "Route not found")
        kind = {"assets": "asset", "contracts": "contract", "runs": "run", "jobs": "job"}[parts[0]]
        record = self._get(owner, kind, parts[1])
        if kind == "run" and len(parts) == 3 and parts[2] == "baseline" and method == "GET":
            from workspace.releases import approved_artifacts
            from workspace.react_artifacts import generated_files
            try:
                number = int(query.get("round", (record.get("approval") or {}).get("round", 0)))
            except (ValueError, TypeError):
                raise HTTPError(404, "not-found", "Resource not found") from None
            if not any(isinstance(row, dict) and row.get("number") == number for row in record.get("rounds", [])):
                raise HTTPError(404, "not-found", "Resource not found")
            # The gate authorized this round before artifact validation; its final recheck
            # runs after these artifact reads, before the response is released.
            try:
                row, project, _ = approved_artifacts(self.storage, owner, record, number, gate=gate)
            except (ValueError, TypeError) as error:
                raise HTTPError(409, "baseline-unavailable", "기준 시안의 현재 승인을 확인할 수 없습니다.") from error
            return _json(200, {"baseline": {"runId": record["id"], "round": number, "sourceHash": row["sourceHash"]},
                               "files": sorted(generated_files(project))})
        if len(parts) == 2 and method == "GET":
            if kind == "run":
                record = self._run_view(owner, record, scope)
            if kind == "job":
                if record.get("task") in ("document-finalize", "document-analysis"):
                    from documents.errors import DocumentError
                    from documents.library import authorize_job
                    try:
                        authorize_job(self, scope, record)
                        record = self._expire_job(owner, record, scope=scope)
                    except DocumentError as error:
                        return _json(error.status, {"error": error.message, "code": error.code})
                else:
                    record = self._expire_job(owner, record)
            payload = {kind: record}
            if kind == "asset" and record.get("analysisKey"):
                key = self._blob_key(owner, record["analysisKey"])
                if self.storage.blob_info(key)["size"] > MAX_JSON_BYTES:
                    raise HTTPError(413, "analysis-too-large", "Analysis exceeds the response limit")
                payload["analysis"] = json.loads(self.storage.get_blob(key))
            return _json(200, payload)
        if kind == "asset":
            if len(parts) == 3 and parts[2] == "guidelines" and method == "GET":
                return self._guidelines(owner, record, query)
            if len(parts) == 4 and parts[2] == "parts" and method == "PUT":
                if not re.fullmatch(r"0|[1-9][0-9]{0,2}", parts[3]):
                    raise HTTPError(400, "invalid-part", "Invalid part index")
                return self._upload_part(owner, record, int(parts[3]), _body(event, binary=True))
            if len(parts) == 3 and parts[2] == "complete" and method == "POST":
                _body(event)
                return self._complete(owner, record)
            if len(parts) == 2 and method == "DELETE":
                if record.get("system"):
                    raise HTTPError(409, "published-guide-readonly", "확정된 기획 가이드는 파일 목록에서 제외할 수 없습니다. 상품 기준의 새 버전을 작성하세요.")
                if not record.get("archived"):
                    record = self.storage.put(owner, kind, {**record, "archived": True}, record["version"])
                return _json(200, {"asset": record})
        if kind == "contract" and len(parts) == 2 and method == "PUT":
            return self._contract_edit(owner, record, _body(event), scope=scope, gate=gate)
        if len(parts) == 3 and parts[2] == "approve" and method == "POST":
            if kind == "contract":
                return self._contract_approve(owner, record, _body(event), scope=scope, gate=gate)
            if kind == "run":
                return self._run_approve(owner, record, _body(event), scope=scope, gate=gate)
        if len(parts) == 3 and parts[2] == "blob" and method == "GET" and kind in ("asset", "run"):
            # The gate authorized the asset or the round (every chunk, every artifact kind)
            # and rechecks after this chunk's bytes are read, before they are returned.
            return self._download(owner, kind, record, query)
        raise HTTPError(404, "not-found", "Route not found")

    def _create_asset(self, owner, body, gate=None):
        name = _text(body.get("name"), "file name", 180)
        if any(c in name for c in "\x00\r\n/\\") or name.rsplit(".", 1)[-1].lower() not in EXTENSIONS or "." not in name:
            raise HTTPError(400, "unsupported-extension", "Choose a supported file extension")
        size = body.get("size")
        if type(size) is int and size > MAX_FILE_BYTES:
            raise HTTPError(413, "too-large", "Files must be at most 50 MiB")
        size = _integer(size, "File size", 0, MAX_FILE_BYTES)
        digest = body.get("sha256")
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise HTTPError(400, "invalid-hash", "A lowercase SHA256 file hash is required")
        purpose = body.get("purpose")
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise HTTPError(400, "invalid-purpose", "Choose a supported file purpose")
        parent_id = body.get("parentId")
        parent = None
        if parent_id is not None:
            parent = self._get(owner, "asset", parent_id)
            # The parent's current authority (never revoked/tombstoned/deleted, and in
            # this project when one is selected) is required before its lineage
            # (importRevision, lineageId) is extended and its id disclosed as parentId;
            # inaccessible is the same 404 as a missing parent. The observation is
            # retained for the response's own final recheck.
            if gate is not None and gate.authorize("asset", parent) is None:
                raise _missing()
        identifier = uuid.uuid4().hex
        import_revision, lineage_id = 1, identifier
        if parent is not None:
            # Older intake records have no import revision. Their CAS version
            # is never evidence of how many times the file was imported.
            prior_revision = parent.get("importRevision", 1)
            if type(prior_revision) is not int or not 1 <= prior_revision < 2**53 - 1:
                raise HTTPError(409, "invalid-lineage", "The parent import revision is unavailable")
            import_revision = prior_revision + 1
            lineage_id = parent.get("lineageId") or parent["id"]
        asset = self.storage.put(owner, "asset", {
            "id": identifier, "name": name, "size": size, "sha256": digest, "purpose": purpose,
            "importRevision": import_revision, "lineageId": lineage_id, "parentId": parent_id,
            "status": "uploading", "uploadStatus": "uploading", "parseStatus": "pending",
            "partCount": (size + CHUNK_BYTES - 1) // CHUNK_BYTES, "parts": {},
            "originalKey": key_for(owner, "asset", identifier, "original"), "previews": [], "warnings": [], "archived": False,
        })
        return _json(201, {"asset": asset, "chunkBytes": CHUNK_BYTES})

    def _upload_part(self, owner, asset, index, data):
        identifier = asset["id"]
        digest = hashlib.sha256(data).hexdigest()
        expected = min(CHUNK_BYTES, asset["size"] - index * CHUNK_BYTES)
        if index >= asset["partCount"] or len(data) != expected:
            raise HTTPError(409, "part-size-mismatch", "Part index or size does not match the declared file")
        slot = str(index)
        for _ in range(8):
            if asset.get("uploadStatus") != "uploading" or asset.get("archived"):
                raise HTTPError(409, "upload-closed", "This upload no longer accepts parts")
            previous = asset.get("parts", {}).get(slot)
            if previous and (previous["sha256"] != digest or previous["size"] != len(data)):
                raise HTTPError(409, "part-changed", "Retry bytes differ from the original part")
            if previous and previous.get("status") == "stored":
                return _json(200, {"asset": asset, "index": index, "sha256": digest})
            if previous:
                break
            part = {"key": key_for(owner, "asset", identifier, f"parts/{index}-{digest}"),
                    "sha256": digest, "size": len(data), "status": "uploading"}
            try:
                asset = self.storage.put(owner, "asset", {**asset, "parts": {**asset.get("parts", {}), slot: part}},
                                         asset["version"])
                break
            except Conflict:
                asset = self._get(owner, "asset", identifier)
        else:
            raise Conflict("Upload manifest is busy")
        part = asset["parts"][slot]
        self.storage.put_blob_once(part["key"], data, "application/octet-stream")
        for _ in range(8):
            asset = self._get(owner, "asset", identifier)
            if asset.get("uploadStatus") != "uploading" or asset.get("archived"):
                raise HTTPError(409, "upload-closed", "This upload no longer accepts parts")
            part = asset["parts"][slot]
            if part["sha256"] != digest:
                raise Conflict("Part changed")
            if part["status"] == "stored":
                return _json(200, {"asset": asset, "index": index, "sha256": digest})
            try:
                asset = self.storage.put(owner, "asset", {
                    **asset, "parts": {**asset["parts"], slot: {**part, "status": "stored"}}}, asset["version"])
                return _json(200, {"asset": asset, "index": index, "sha256": digest})
            except Conflict:
                continue
        raise Conflict("Upload manifest is busy")

    def _worker_ready(self):
        if not self.worker_fn:
            raise HTTPError(503, "worker-unavailable", "Workspace worker is not configured")

    def _invoke(self, owner, job):
        if job["status"] != "queued":
            return
        self._worker_ready()
        try:
            if self.lambda_client is None:
                import boto3
                self.lambda_client = boto3.client("lambda", config=Storage._config())
            result = self.lambda_client.invoke(FunctionName=self.worker_fn, InvocationType="Event",
                                               Payload=json.dumps({"owner": owner, "jobId": job["id"]}).encode())
            if result.get("StatusCode") != 202:
                raise RuntimeError("Worker did not accept the job")
        except Exception:
            current = self.storage.get(owner, "job", job["id"])
            if current and current["status"] == "queued":
                try:
                    writes = [{"owner": owner, "kind": "job", "item": {**current, "status": "failed",
                               "error": "Worker invocation failed", "errorCode": "dispatch-failed"},
                               "expected_version": current["version"]}]
                    kind, id_field = _JOB_TARGETS.get(current["task"], (None, None))
                    target_id = current["input"].get(id_field) if id_field else None
                    target = self.storage.get(owner, kind, target_id) if kind and target_id else None
                    if target and target.get("status") in ("queued", "processing"):
                        update = {"status": "failed", "error": "Worker invocation failed"}
                        if kind == "asset":
                            update["uploadStatus"] = "failed"
                        writes.append({"owner": owner, "kind": kind, "item": {**target, **update},
                                       "expected_version": target["version"]})
                    self.storage.put_many(writes)
                except Conflict:
                    pass
            raise HTTPError(503, "worker-unavailable", "Worker invocation failed; inspect the job before retrying")

    def _retry_dispatch(self, owner, job):
        """Only a job that never started may be requeued after a dispatch failure."""
        if job.get("status") != "failed" or job.get("errorCode") != "dispatch-failed" or job.get("startedAt"):
            return job
        writes = [{"owner": owner, "kind": "job", "item": {**job, "status": "queued", "error": None,
                   "errorCode": None, "progress": 0}, "expected_version": job["version"]}]
        kind, id_field = _JOB_TARGETS.get(job["task"], (None, None))
        target_id = job["input"].get(id_field) if id_field else None
        if kind and target_id:
            target = self._get(owner, kind, target_id)
            if target.get("status") != "failed" or target.get("rounds"):
                return job
            update = {"status": "processing" if kind in ("asset", "docrevision") else "queued", "error": None}
            if kind == "asset":
                update["uploadStatus"] = "processing"
            writes.append({"owner": owner, "kind": kind, "item": {**target, **update}, "expected_version": target["version"]})
        try:
            return self.storage.put_many(writes)[0]
        except Conflict:
            return self._get(owner, "job", job["id"])

    def _new_job(self, owner, identifier, task, data, request_hash=None, gate=None):
        from workspace.storage import RESERVED_TASKS
        if task in RESERVED_TASKS:
            raise HTTPError(400, "reserved-task", "이 작업 유형은 별도 실행 경로에서만 생성됩니다.")
        record = {"id": identifier, "task": task, "input": data, "status": "queued", "progress": 0}
        if request_hash:
            record["requestHash"] = request_hash
        try:
            return self.storage.put(owner, "job", record)
        except Conflict:
            # A genuine concurrent race: another request created a job with this
            # SAME identifier between this call's own pre-check and this write.
            # Authorize that job (the same lineage check its own GET's response
            # gate applies) before ever comparing requestHash/task against it --
            # an inaccessible job must 404 identically to a missing one, not
            # disclose through this 409 that a DIFFERENT-content job with this
            # exact requestId now exists.
            existing = self._get(owner, "job", identifier)
            if gate is not None:
                authorized = gate.authorize("job", existing)
                if authorized is None:
                    raise HTTPError(404, "not-found", "Resource not found")
                existing = authorized
            if existing.get("requestHash") != request_hash or existing["task"] != task:
                raise HTTPError(409, "request-changed", "The request ID was already used with different input")
            return existing

    def _complete(self, owner, asset):
        if asset.get("archived"):
            raise HTTPError(409, "archived", "Archived files cannot be finalized")
        if asset.get("system"):
            raise HTTPError(409, "published-guide-readonly", "확정된 기획 가이드는 다시 반입 처리할 수 없습니다.")
        if asset.get("jobId") and asset["uploadStatus"] in ("processing", "stored", "failed"):
            job = self.storage.get(owner, "job", asset["jobId"])
            if job and job.get("errorCode") == "dispatch-failed":
                job = self._retry_dispatch(owner, job)
                asset = self._get(owner, "asset", asset["id"])
            if asset["uploadStatus"] == "processing":
                self._worker_ready()
                job = job or self._new_job(owner, asset["jobId"], "finalize", {"assetId": asset["id"]})
                self._invoke(owner, job)
            if not job:
                raise HTTPError(409, "job-unavailable", "Finalization job is unavailable")
            return _json(202, {"asset": asset, "job": job})
        if asset.get("uploadStatus") != "uploading":
            raise HTTPError(409, "upload-closed", "Upload cannot be finalized")
        self._worker_ready()
        parts = asset.get("parts", {})
        for index in range(asset["partCount"]):
            part = parts.get(str(index), {})
            if part.get("status") != "stored":
                raise HTTPError(409, "parts-missing", "Upload every part before finalizing")
            info = self.storage.blob_info(self._blob_key(owner, part["key"]))
            if info["sha256"] != part["sha256"] or info["size"] != min(CHUNK_BYTES, asset["size"] - index * CHUNK_BYTES):
                raise HTTPError(409, "part-changed", "A stored part no longer matches the upload manifest")
        job_id = f"finalize-{asset['id']}"
        try:
            asset = self.storage.put(owner, "asset", {
                **asset, "jobId": job_id, "status": "processing", "uploadStatus": "processing"}, asset["version"])
        except Conflict:
            asset = self._get(owner, "asset", asset["id"])
            if asset.get("jobId") != job_id or asset.get("uploadStatus") != "processing":
                raise
        job = self._new_job(owner, job_id, "finalize", {"assetId": asset["id"]})
        self._invoke(owner, job)
        return _json(202, {"asset": asset, "job": job})

    def _authorized_asset(self, owner, identifier, gate=None):
        """The current, accessible asset, or a 404 identical to a missing one.

        Revoked, tombstoned, deleted and never-existed must all be indistinguishable
        (never an existence oracle): access is decided BEFORE readiness, using the
        same authorization a plain GET would apply. When a gate is given, the
        observation joins its aggregate for the response's own final recheck.
        """
        stored = self.storage.get(owner, "asset", identifier)
        asset = gate.authorize("asset", stored) if gate is not None and stored is not None else stored
        if (asset is None or asset.get("accessRevoked") or asset.get("tombstone")
                or asset.get("status") == "deleted"):
            raise HTTPError(404, "not-found", "Resource not found")
        return asset

    def _assets(self, owner, identifiers, gate=None):
        if (not isinstance(identifiers, list) or len(identifiers) > 20
                or any(not isinstance(identifier, str) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise HTTPError(400, "invalid-assets", "Select at most 20 distinct assets")
        assets = [self._authorized_asset(owner, identifier, gate) for identifier in identifiers]
        for asset in assets:
            # Readiness (still uploading) and identity (bytes changed) are checked
            # only once access is already confirmed -- neither ever discloses whether
            # an inaccessible id exists.
            if asset.get("archived") or asset.get("uploadStatus") != "stored":
                raise HTTPError(409, "asset-not-ready", "Selected files must be stored and not archived")
            info = self.storage.blob_info(self._blob_key(owner, asset.get("originalKey")))
            if info["size"] != asset["size"] or info["sha256"] != asset["sha256"]:
                raise HTTPError(409, "asset-changed", "Selected file bytes do not match the stored identity")
        return assets

    def _guidelines(self, owner, asset, query):
        from workspace.guidelines import load_pack, summaries
        import unicodedata
        if asset.get("archived") or asset.get("uploadStatus") != "stored":
            raise HTTPError(409, "asset-not-ready", "가이드 파일의 보관 상태를 확인하세요.")
        pack = load_pack(self.storage, owner, asset)
        source_id = _text(query.get("sourceId", ""), "sourceId", 128, empty=True)
        search = unicodedata.normalize("NFKC", _text(query.get("q", ""), "query", 120, empty=True)).casefold()
        cursor = query.get("cursor", "0")
        if not isinstance(cursor, str) or not re.fullmatch(r"[0-9]{1,5}", cursor):
            raise HTTPError(400, "invalid-input", "가이드 페이지 위치를 확인하세요.")
        rows = []
        for source in pack["sources"]:
            if source_id and source["id"] != source_id:
                continue
            for page in source["pages"]:
                searchable = page["text"] + " " + source["name"] + " " + json.dumps(source.get("metadata", {}), ensure_ascii=False)
                if search and search not in unicodedata.normalize("NFKC", searchable).casefold():
                    continue
                rows.append({**page, "sourceId": source["id"], "sourceName": source["name"],
                             "sourceSha256": source["sha256"], "category": source["category"]})
        start = int(cursor)
        return _json(200, {"sources": summaries(pack, originals_stored=bool(asset.get("name")) and not asset["name"].lower().endswith(".json")), "pages": rows[start:start + 8], "total": len(rows),
                           "cursor": str(start + 8) if start + 8 < len(rows) else None})

    def _asset_texts(self, owner, assets, guide_refs=None):
        texts = {}
        for asset in assets:
            text = ""
            if asset.get("analysisKey"):
                key = self._blob_key(owner, asset["analysisKey"])
                if self.storage.blob_info(key)["size"] > MAX_JSON_BYTES:
                    raise HTTPError(413, "analysis-too-large", "Extracted analysis exceeds the contract limit")
                analysis = json.loads(self.storage.get_blob(key))
                text = analysis.get("text", "")
                if not isinstance(text, str):
                    raise HTTPError(409, "invalid-analysis", "Extracted source text is unavailable")
            texts[asset["id"]] = text
            if asset.get("guidelinesKey"):
                from workspace.guidelines import context_for, load_pack
                selected = [ref for ref in guide_refs or [] if ref["assetId"] == asset["id"]]
                texts[asset["id"]], _ = context_for(load_pack(self.storage, owner, asset), selected)
        return texts

    def _validated_contract(self, owner, data, gate=None):
        assets = self._assets(owner, data.get("assetIds", []), gate=gate)
        try:
            from workspace.guidelines import validate_citations, validate_selection
            refs, pages = validate_selection(self.storage, owner, assets, data.get("guideRefs", []))
            normalized = self.rules().validate_contract(data, asset_texts=self._asset_texts(owner, assets, refs))
            validate_citations(normalized, pages)
            from workspace.change_requests import baseline_project
            baseline_project(self.storage, owner, normalized.get("changeRequest", {}), gate=gate)
        except ValueError as error:
            # A validation failure (e.g. a quote that doesn't match) must never be
            # distinguishable from an asset that lost access while its text was being
            # read for that same comparison (review 8 #3): re-authorize every
            # referenced asset one more time before reporting the content-revealing
            # 400 -- a race that revoked one raises the same 404 a plain GET (or the
            # matching-quote path's own later gate recheck) would.
            self._assets(owner, [asset["id"] for asset in assets], gate=gate)
            # The re-check above still authorizes/reads each asset one at a
            # time (each asset's own blob_info read can take time), so a
            # revocation racing a LATER asset's read in that SAME pass -- not
            # only the very first one checked -- was still invisible to it
            # (review 8 #3 only closed the window up to that pass's own
            # first check). Every one of those re-checks already joined this
            # gate's aggregate reader; recheck it through the gate's own
            # normalized final recheck (review 10: converts a caught
            # authorization change to the canonical 404, exactly like
            # `authorize()` itself does), strictly after every read this
            # validation performed -- so it does not matter which asset's
            # read raced the revocation.
            if gate is not None:
                gate.recheck()
            raise HTTPError(400, "invalid-contract", str(error)[:240]) from error
        return normalized, assets

    @staticmethod
    def _snapshot_assets(assets):
        fields = ("id", "version", "importRevision", "lineageId", "parentId", "sha256", "name", "size",
                  "purpose", "originalKey", "analysisKey", "previews", "parseStatus", "guidelinesKey", "guidelinesSha256")
        return [{key: asset[key] for key in fields if key in asset} for asset in assets]

    def _criteria(self, owner, body, scope, previous=None, allow_request_draft=False):
        from workspace.component_catalog import read_catalog
        catalog = read_catalog()
        if previous and previous.get("catalogHash") and previous["catalogHash"] != catalog["hash"]:
            raise HTTPError(409, "criteria-changed", "React 코드 기준이 바뀌었습니다. 새 규칙을 확인·승인하세요.")
        if body.get("catalogHash") and body["catalogHash"] != catalog["hash"]:
            raise HTTPError(409, "criteria-changed", "화면에 표시된 React 코드 기준을 새로 조회하세요.")
        criteria = {"catalogHash": catalog["hash"]}
        project = scope.get("project") if scope else None
        product_id = body.get("productId", (previous or {}).get("productId"))
        if project is None:
            if product_id:
                raise HTTPError(400, "project-required", "상품 기준은 프로젝트에서 선택하세요.")
            return criteria
        if not product_id:
            if allow_request_draft and not (previous or {}).get("productId"):
                return criteria
            raise HTTPError(409, "guideline-required", "기획에서 확정한 상품 기준을 먼저 선택하세요.")
        if previous and previous.get("productId") and previous["productId"] != product_id:
            raise HTTPError(409, "product-changed", "다른 상품은 새 규칙으로 생성하세요.")
        context = self.collaboration.published_context(scope, product_id)
        if previous and previous.get("guidelineId") and previous["guidelineId"] != context["guideline"]["id"]:
            raise HTTPError(409, "criteria-changed", "상품 가이드가 바뀌었습니다. 새 규칙을 확인·승인하세요.")
        criteria.update(projectId=project["id"], productId=context["product"]["id"],
                        guidelineId=context["guideline"]["id"], guidelineAssetId=context["assetId"],
                        ontologyHash=context["ontology"]["hash"])
        return criteria

    @staticmethod
    def _with_criteria(data, criteria):
        result = {**data, **criteria}
        assets = data.get("assetIds", [])
        if not isinstance(assets, list):
            raise HTTPError(400, "invalid-assets", "Select an array of input assets")
        assets = list(assets)
        guide = criteria.get("guidelineAssetId")
        if guide and guide not in assets:
            assets.append(guide)
        result["assetIds"] = assets
        return result

    def _contract_create(self, owner, body, scope=None, gate=None):
        data = self._with_criteria({key: body[key] for key in _EDITABLE if key in body},
                                   self._criteria(owner, body, scope, allow_request_draft=isinstance(body.get("changeRequest"), dict)))
        normalized, _ = self._validated_contract(owner, data, gate=gate)
        record = self.storage.put(owner, "contract", {**normalized, "id": uuid.uuid4().hex, "status": "draft"})
        return _json(201, {"contract": record})

    def _contract_edit(self, owner, record, body, scope=None, gate=None):
        version = _integer(body.get("version"), "Version", 1, 2**53 - 1)
        if version != record["version"]:
            raise Conflict("Contract changed")
        editable = {key: body.get(key, record.get(key)) for key in _EDITABLE if key in body or key in record}
        editable = self._with_criteria(editable, self._criteria(owner, body, scope, record,
                                      allow_request_draft=isinstance(editable.get("changeRequest"), dict)))
        normalized, _ = self._validated_contract(owner, editable, gate=gate)
        revisions = list(record.get("revisions", []))
        if record.get("status") == "approved":
            digest = self.rules().contract_hash(record)
            key = key_for(owner, "contract", record["id"], f"revisions/{version}-{digest}.json")
            self.storage.put_blob_once(key, json.dumps(record, ensure_ascii=False, sort_keys=True).encode(), "application/json")
            revisions.append({"version": version, "hash": digest, "key": key})
        result = self.storage.put(owner, "contract", {
            **normalized, "id": record["id"], "status": "draft", "revisions": revisions}, version)
        return _json(200, {"contract": result})

    def _approval_put(self, owner, kind, record, expected_version, scope, action, assets=(), gate=None):
        writes = [{"owner": owner, "kind": kind, "item": record, "expected_version": expected_version}]
        if scope and scope.get("project"):
            fresh = self.collaboration.require(scope, action)
            project = scope["project"]
            if fresh["project"]["version"] != project["version"] or fresh["owner"] != owner:
                raise Conflict("Project authority or planning criteria changed during approval")
            writes.append({"owner": owner, "kind": "project", "item": project, "expected_version": project["version"]})
        checks = [{"owner": owner, "kind": "contract", "id": record["contractId"], "version": record["contractVersion"]}] if kind == "run" else []
        checks.extend({"owner": owner, "kind": "asset", "id": asset["id"], "version": asset["version"]} for asset in assets)
        from workspace.change_requests import baseline_project
        criteria = record["contract"] if kind == "run" else record
        baseline_project(self.storage, owner, criteria.get("changeRequest", {}), checks=checks, gate=gate)
        unique = {}
        written = {(write["owner"], write["kind"], write["item"]["id"]) for write in writes}
        for check in checks:
            key = check["owner"], check["kind"], check["id"]
            if key in written:
                raise Conflict("Approval depends on a record being changed")
            if key in unique and unique[key]["version"] != check["version"]:
                raise Conflict("Baseline changed during approval")
            unique[key] = check
        checks = list(unique.values())
        before_attempt = None
        reader = gate.aggregate if gate is not None else None
        if reader is not None:
            # The approval is fenced by the complete lineage the gate authorized (the
            # addressed resource and its round): every observed version joins the
            # transaction, and every attempt first reruns the full recheck (currency,
            # expiry, historical references, packages), so a revocation before storage
            # leaves no approval behind.
            present = {(check["owner"], check["kind"], check["id"]) for check in checks}
            versions = {(write["owner"], write["kind"], write["item"]["id"]): write["expected_version"]
                        for write in writes}
            for key, check in reader.observed.items():
                if key in versions:
                    if versions[key] != check["version"]:
                        raise Conflict("The approved record changed after authorization")
                    continue
                if key not in present:
                    checks.append(dict(check))
                    present.add(key)
            if len(writes) + len(checks) > 100:
                raise HTTPError(409, "approval-scope-limit", "승인 근거가 한 번에 확인할 수 있는 범위를 넘습니다.")
            before_attempt = reader.recheck
        # Human approval may update a design-linked run; outcome statuses remain ledger-only.
        from workspace import storage as storage_module
        return self.storage.put_many(writes, checks=checks, before_attempt=before_attempt,
                                     _writer=storage_module._human_writer())[0]

    def _contract_approve(self, owner, record, body, scope=None, gate=None):
        version = _integer(body.get("version"), "Version", 1, 2**53 - 1)
        if record["version"] != version:
            raise Conflict("Contract changed")
        if record.get("catalogHash"):
            self._criteria(owner, {}, scope, record)
        normalized, assets = self._validated_contract(owner, record, gate=gate)
        from workspace.rules import state_coverage_issues
        coverage_issues = state_coverage_issues(normalized)
        if coverage_issues:
            raise HTTPError(409, "contract-states-incomplete", " ".join(coverage_issues))
        if normalized.get("unresolved") or not normalized.get("rules"):
            raise HTTPError(409, "contract-unresolved", "Resolve every requirement before approving")
        if normalized.get("productId"):
            from workspace.criteria import notice_coverage_issues, resolve_generation_context
            try:
                context = resolve_generation_context(self.storage, owner, {**normalized, "actor": scope["actor"]}, "edit_rules")
            except ValueError as error:
                raise HTTPError(409, "criteria-changed", str(error)) from error
            missing = notice_coverage_issues(normalized, context["pages"])
            if missing:
                raise HTTPError(409, "required-guide-tests-missing", missing[0])
        digest = self.rules().contract_hash(normalized)
        if record.get("status") == "approved" and (record.get("approval") or {}).get("hash") == digest:
            return _json(200, {"contract": record})
        result = self._approval_put(owner, "contract", {
            **record, **normalized, "status": "approved",
            "approval": {"version": version + 1, "hash": digest, "actor": scope["actor"] if scope else owner,
                         "at": self.storage.clock()},
        }, version, scope, "edit_rules", assets=assets, gate=gate)
        return _json(200, {"contract": result})

    @staticmethod
    def _request_id(body, task):
        request_id = body.get("requestId")
        if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
            raise HTTPError(400, "invalid-request-id", "A stable requestId is required")
        return f"{task}-{hashlib.sha256(request_id.encode()).hexdigest()}"

    @staticmethod
    def _fingerprint(data):
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def _existing_job(self, owner, identifier, fingerprint, gate=None):
        job = self.storage.get(owner, "job", identifier)
        if job is not None and gate is not None:
            # Authorize the SAME job (its task-specific input lineage) before
            # ever comparing the private requestHash fingerprint against it --
            # a job whose input has since been revoked must 404 identically to
            # a missing one, not disclose through this 409 that a DIFFERENT-
            # content job with this exact requestId still exists.
            authorized = gate.authorize("job", job)
            if authorized is None:
                raise HTTPError(404, "not-found", "Resource not found")
            job = authorized
        if job and job.get("requestHash") != fingerprint:
            # Recheck retained authority before disclosing this content-dependent
            # mismatch (review 9 #3): a revocation racing in AFTER the authorize()
            # above (but before this comparison raises) is never caught by the
            # caller's own success-path recheck -- an HTTPError raised here skips
            # straight past `gate.finish()`'s final aggregate recheck entirely, so
            # the matching-fingerprint retry (which DOES reach `finish()`) 404s on
            # the exact same race while this one still discloses the private
            # brief/input mismatch via a distinguishable 409. Recheck through the
            # gate's own normalized final recheck (review 10: it now converts a
            # caught authorization change to the canonical 404, exactly like
            # `authorize()` itself does), so a race here 404s identically to the
            # matching-fingerprint path's own recheck.
            if gate is not None:
                gate.recheck()
            raise HTTPError(409, "request-changed", "The request ID was already used with different input")
        return job

    def _propose(self, owner, body, scope=None, gate=None):
        from engine import model_catalog
        identifier = self._request_id(body, "propose")
        data = {"assetIds": body.get("assetIds", []), "brief": _text(body.get("brief", ""), "brief", 4000, empty=True),
                "model": model_catalog.resolve(body.get("model"))}
        data = self._with_criteria(data, self._criteria(owner, body, scope))
        if body.get("guideRefs") is not None:
            from workspace.guidelines import normalize_refs
            data["guideRefs"] = normalize_refs(body["guideRefs"], data["assetIds"])
        if "requiredStates" in body:
            from workspace.rules import normalize_required_states
            data["requiredStates"] = normalize_required_states(body["requiredStates"])
        if "changeRequest" in body:
            from workspace.change_requests import normalize_request
            data["changeRequest"] = normalize_request(body["changeRequest"])
        data["actor"] = scope["actor"] if scope else owner
        fingerprint = self._fingerprint(data)
        job = self._existing_job(owner, identifier, fingerprint, gate=gate)
        if job:
            job = self._retry_dispatch(owner, job)
        if not job:
            self._worker_ready()
            assets = self._assets(owner, data["assetIds"], gate=gate)
            from workspace.guidelines import validate_selection
            try:
                validate_selection(self.storage, owner, assets, data.get("guideRefs", []))
                if "changeRequest" in data:
                    # The baseline's own lineage (review 5) must be authorized inside
                    # THIS SAME guarded block, not before it: review 6 found that a
                    # revocation racing the read here still bypassed the aggregate
                    # recheck entirely when this call sat outside every local
                    # except-ValueError handler, propagating its 400 straight past
                    # the gate (baseline_project's own successful authorization still
                    # joins the aggregate below, but only a recheck INSIDE this
                    # handler ever consults it).
                    from workspace.change_requests import baseline_project
                    baseline_project(self.storage, owner, data["changeRequest"], gate=gate)
                    if any(ref not in data.get("guideRefs", []) for screen in data["changeRequest"]["screens"]
                           for ref in screen.get("sourceRefs", [])):
                        raise HTTPError(400, "source-reference-mismatch", "화면별 원문 연결을 현재 선택한 페이지와 다시 대조하세요.")
            except (HTTPError, ValueError) as error:
                # A validation failure (e.g. a guide page whose textSha256 no
                # longer matches, a change request's baseline hash, or its own
                # sourceRefs/guideRefs mismatch) must never be distinguishable
                # from an asset or baseline revoked while ITS OWN or a LATER read
                # was being performed for that same comparison (same shape as
                # review 8 #3 / review 1 #2's fix for `_validated_contract`, and
                # review 5's fix to `baseline_project` itself, both extended here
                # to cover this validation path too). The recheck below must be
                # structural, not exception-type-dependent (review 7 found that
                # catching only `ValueError` still let the source-reference-
                # mismatch check's own `HTTPError` bypass it entirely, the same
                # "enumerate types" trap the git_export.py receipt fix hit
                # earlier) -- every content-dependent failure raised in this
                # block, of EITHER type, goes through the SAME recheck before
                # being reported or re-raised. Every asset above, and the
                # baseline (when authorized successfully), already joined this
                # gate's aggregate reader; recheck it through the gate's own
                # normalized final recheck (review 10: converts a caught
                # authorization change to the canonical 404, exactly like
                # `authorize()` itself does), strictly after every read this
                # validation performed, so a revocation racing ANY of them
                # raises the same 404 a plain GET (or the matching-value
                # path's own later gate recheck) would.
                if gate is not None:
                    gate.recheck()
                # The recheck found nothing newly wrong: an HTTPError already
                # carries its own correct status/code (e.g. baseline_project's
                # own 404, or this block's 400 source-reference-mismatch) and is
                # preserved as-is; only a plain ValueError is converted.
                if isinstance(error, HTTPError):
                    raise
                raise HTTPError(400, "invalid-input", str(error)[:240]) from error
            job = self._new_job(owner, identifier, "propose", {
                **data, "assetSnapshots": self._snapshot_assets(assets)}, fingerprint, gate=gate)
        self._invoke(owner, job)
        return _json(202, {"job": job})

    def _run_create(self, owner, body, scope=None, batch_context=None, gate=None):
        from engine import model_catalog
        inherited_policy = None
        if body.get("baseRunId"):
            base_run = self._authorized_run(owner, body["baseRunId"], gate)
            if (base_run.get("outputType") == "react" and body.get("contractId") == base_run.get("contractId")
                    and body.get("contractVersion") == base_run.get("contractVersion")):
                body = dict(body)
                if body.get("outputType", "react") != "react":
                    raise HTTPError(409, "refine-criteria-changed", "React 수정은 같은 코드·검증 기준을 유지해야 합니다.")
                body["outputType"] = "react"
                for key in ("variant", "generationMode", "referenceAssetId", "referencePage", "visualTolerance"):
                    previous = base_run.get(key)
                    if key in body and body[key] != previous:
                        raise HTTPError(409, "refine-criteria-changed", "다른 생성·화면 비교 기준은 새 비교 요청으로 확인하세요.")
                    if previous is not None:
                        body[key] = previous
                inherited_policy = base_run.get("visualPolicy", "exact")
        identifier = self._request_id(body, "run")
        model = model_catalog.resolve(body.get("model"))
        mode = body.get("mode", "generate")
        if mode not in ("generate", "verify"):
            raise HTTPError(400, "invalid-mode", "생성 또는 원본 검사 모드를 선택하세요.")
        maximum = 1 if mode == "verify" else _integer(body.get("maxRounds", 3), "Rounds", 1, 5)
        version = _integer(body.get("contractVersion"), "Contract version", 1, 2**53 - 1)
        variant = body.get("variant", "balanced")
        if variant not in ("balanced", "baseline", "layout", "dense", "emphasis", "flow", "information"):
            raise HTTPError(400, "invalid-variant", "Choose a supported design variant")
        tolerance = body.get("visualTolerance", 0.15)
        if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0 <= tolerance <= 0.5:
            raise HTTPError(400, "invalid-tolerance", "Visual tolerance must be between zero and 0.5")
        data = {"contractId": body.get("contractId"), "contractVersion": version, "model": model, "mode": mode,
                "maxRounds": maximum, "variant": variant, "visualTolerance": tolerance,
                "instruction": _text(body.get("instruction", ""), "instruction", 4000, empty=True)}
        if "outputType" in body:
            if body["outputType"] not in ("react", "html"):
                raise HTTPError(400, "invalid-output", "React 결과 또는 HTML 참고 검사를 선택하세요.")
            data["outputType"] = body["outputType"]
        data["actor"] = scope["actor"] if scope else owner
        generation_mode = body.get("generationMode", "creative")
        if generation_mode not in ("creative", "guided"):
            raise HTTPError(400, "invalid-mode", "Choose a supported generation mode")
        data["generationMode"] = generation_mode
        data["visualPolicy"] = "exact" if variant == "baseline" or data.get("outputType") == "html" or mode == "verify" else "variation-review"
        if inherited_policy:
            data["visualPolicy"] = inherited_policy
        if body.get("batchId"):
            if batch_context is None or batch_context.get("id") != body["batchId"]:
                raise HTTPError(400, "batch-context-required", "비교 생성 API에서 기준안과 변형안을 함께 생성하세요.")
            batch = self._get(owner, "batch", body["batchId"])
            if batch["contractId"] != data["contractId"] or batch["contractVersion"] != version:
                raise HTTPError(409, "batch-criteria-mismatch", "비교 안의 기준 버전이 일치하지 않습니다.")
            data["batchId"] = batch["id"]
            data["variationIndex"] = _integer(body.get("variationIndex"), "Variation index", 0, 5)
        for key in ("baseRunId", "baseRound", "referenceAssetId", "referencePage", "sourceAssetId"):
            if key in body:
                data[key] = body[key]
        fingerprint = self._fingerprint(data)
        job = self._existing_job(owner, identifier, fingerprint, gate=gate)
        if job:
            existing_run = self._get(owner, "run", job["input"]["runId"])
            if job.get("errorCode") == "dispatch-failed" and existing_run.get("outputType") == "react":
                self._criteria(owner, {}, scope, self._authorized_contract(owner, existing_run["contractId"], gate))
            job = self._retry_dispatch(owner, job)
            run = self._get(owner, "run", job["input"]["runId"])
            self._invoke(owner, job)
            return _json(202, {"job": job, "run": run})
        retained_run = self.storage.get(owner, "run", identifier)
        if retained_run is not None and gate is not None:
            # Authorize the SAME retained run before ever inspecting its
            # status -- an inaccessible run must 404 identically to a missing
            # one (the normal, expected case that proceeds to create a new
            # run below), not disclose through this 409 that a run with this
            # exact requestId still exists and has moved past "queued".
            authorized = gate.authorize("run", retained_run)
            if authorized is None:
                raise HTTPError(404, "not-found", "Resource not found")
            retained_run = authorized
        if retained_run and retained_run.get("status") != "queued":
            raise HTTPError(409, "request-expired",
                            "The operational job expired. Open the stored run or start a new request.")
        self._worker_ready()
        contract = self._authorized_contract(owner, data["contractId"], gate)
        output_type = "html" if mode == "verify" else data.get("outputType", "react" if contract.get("catalogHash") else "html")
        if output_type == "react":
            if not contract.get("catalogHash"):
                raise HTTPError(409, "code-criteria-required", "React 코드 기준을 포함한 새 규칙을 승인하세요.")
            self._criteria(owner, {}, scope, contract)
        normalized, assets = self._validated_contract(owner, contract, gate=gate)
        digest = self.rules().contract_hash(normalized)
        approval = contract.get("approval") or {}
        if (contract["version"] != version or contract.get("status") != "approved"
                or approval.get("version") != version or approval.get("hash") != digest
                or normalized.get("unresolved") or not normalized.get("rules")):
            raise HTTPError(409, "contract-not-approved", "Run the exact resolved and approved contract version")
        fields = {}
        if mode == "verify":
            source = next((asset for asset in assets if asset["id"] == data.get("sourceAssetId")), None)
            if not source:
                if data.get("sourceAssetId"):
                    # Resolve access before existence: a revoked/tombstoned/deleted
                    # asset that just is not one of the contract's own selected
                    # inputs must 404 identically to a foreign/missing id, never
                    # the distinguishable 409 below.
                    self._authorized_asset(owner, data["sourceAssetId"], gate)
                raise HTTPError(409, "source-not-selected", "확정된 규칙에 포함된 HTML 파일을 선택하세요.")
            if (source["name"].rsplit(".", 1)[-1].lower() not in ("html", "htm")
                    or source.get("parseStatus") not in ("complete", "partial")):
                raise HTTPError(409, "source-unavailable", "해석 가능한 HTML 원본이 필요합니다.")
            if data.get("baseRunId") or "baseRound" in data:
                raise HTTPError(400, "invalid-base", "원본 검사와 이전 시안 수정은 별도로 실행하세요.")
            fields.update(sourceHtmlKey=self._blob_key(owner, source["originalKey"]),
                          sourceArtifactSha256=source["sha256"])
        elif data.get("sourceAssetId"):
            raise HTTPError(400, "invalid-source", "원본 검사 모드에서 HTML 파일을 지정하세요.")
        if "baseRound" in data and not data.get("baseRunId"):
            raise HTTPError(400, "invalid-base", "Select a base run for the requested round")
        if data.get("baseRunId"):
            base = self._authorized_run(owner, data["baseRunId"], gate)
            number = _integer(data.get("baseRound", base.get("bestRound")), "Base round", 1, 5)
            selected = next((row for row in base.get("rounds", []) if row.get("number") == number), None)
            if not selected or not selected.get("htmlKey") or not selected.get("artifactSha256"):
                raise HTTPError(409, "base-unavailable", "The selected base round has no stored artifact")
            fields.update(baseHtmlKey=self._blob_key(owner, selected["htmlKey"]),
                          baseArtifactSha256=selected["artifactSha256"], baseRound=number)
            if self.storage.blob_info(fields["baseHtmlKey"])["sha256"] != fields["baseArtifactSha256"]:
                raise HTTPError(409, "base-changed", "The selected base artifact changed")
            if output_type == "react" and base.get("outputType") == "react":
                source_key = self._blob_key(owner, selected.get("sourceKey"))
                if not selected.get("sourceHash") or not selected.get("sourceArchiveSha256"):
                    raise HTTPError(409, "base-unavailable", "검증된 React 원본이 없는 라운드는 기준으로 사용할 수 없습니다.")
                if self.storage.blob_info(source_key)["sha256"] != selected["sourceArchiveSha256"]:
                    raise HTTPError(409, "base-changed", "선택한 React 원본 파일이 변경되었습니다.")
                fields.update(baseSourceKey=source_key, baseSourceHash=selected["sourceHash"],
                              baseSourceArchiveSha256=selected["sourceArchiveSha256"], baseContractHash=base["contractHash"])
        if "referencePage" in data and not data.get("referenceAssetId"):
            raise HTTPError(400, "invalid-reference", "Select a reference file")
        if data.get("referenceAssetId"):
            reference = next((asset for asset in assets if asset["id"] == data["referenceAssetId"]), None)
            if reference is None:
                # Resolve access before existence (review 2 #2): plain ownership/
                # existence alone let a revoked/tombstoned/deleted-but-unselected
                # asset return this 409 while a truly missing one 404s --
                # distinguishable. Authorize it the same way `assetIds` is, so
                # every inaccessible reason 404s identically; only an asset that
                # is genuinely accessible yet not one of the contract's own
                # selected inputs reaches the 409 below.
                self._authorized_asset(owner, data["referenceAssetId"], gate)
                raise HTTPError(409, "reference-not-selected", "The reference must be an approved contract asset")
            page = _integer(data.get("referencePage", 1), "Reference page", 1, 10000)
            preview = next((row for row in reference.get("previews", []) if row.get("page") == page), None)
            if not preview or preview.get("mime") != "image/png":
                raise HTTPError(409, "reference-unavailable", "The selected reference has no PNG preview")
            fields.update(referenceKey=self._blob_key(owner, preview["key"]), referencePage=page)
            fields["referenceSha256"] = self.storage.blob_info(fields["referenceKey"])["sha256"]
        criteria = {key: normalized[key] for key in (*CRITERIA_IDS, *CRITERIA_HASHES) if key in normalized}
        run_data = {**data, **fields, **criteria, "outputType": output_type,
                    "id": identifier, "status": "queued", "contractHash": digest,
                    "contract": normalized, "assetSnapshots": self._snapshot_assets(assets), "bestRound": 0,
                    "rounds": [], "functionalStatus": "not-run", "visualStatus": "not-run", "jobId": identifier}
        try:
            run = self.storage.put(owner, "run", run_data)
        except Conflict:
            # A genuine concurrent race: another request created a run with this
            # SAME identifier between this call's own pre-check and this write.
            # Authorize that run (the same lineage check its own GET's response
            # gate applies) before ever comparing contractHash/fingerprint against
            # it -- an inaccessible run must 404 identically to a missing one,
            # not disclose through this 409 that a DIFFERENT-content run with
            # this exact requestId now exists.
            run = self._get(owner, "run", identifier)
            if gate is not None:
                authorized = gate.authorize("run", run)
                if authorized is None:
                    raise HTTPError(404, "not-found", "Resource not found")
                run = authorized
            if run.get("contractHash") != digest or self._fingerprint({key: run[key] for key in data}) != fingerprint:
                raise HTTPError(409, "request-changed", "The request ID was already used")
        job = self._new_job(owner, identifier, "run", {"runId": run["id"]}, fingerprint, gate=gate)
        self._invoke(owner, job)
        return _json(202, {"job": job, "run": run})

    @staticmethod
    def _passing_evidence(run, report):
        """Check browser evidence against frozen rules, not an overall flag alone."""
        if (not isinstance(report, dict) or report.get("passed") is not True
                or report.get("functionalStatus") != "pass"
                or report.get("networkRequests") != [] or report.get("consoleErrors") != []
                or report.get("blockingFindings") != []
                or not isinstance(report.get("accessibility"), dict)
                or report["accessibility"].get("status") != "pass"):
            return False
        visual = report.get("visual")
        allowed_visual = ("pass",) if run.get("referenceAssetId") else ("pass", "not-run")
        if not isinstance(visual, dict) or visual.get("status") not in allowed_visual:
            return False
        rules = (run.get("contract") or {}).get("rules", [])
        checks = report.get("checks")
        if not rules or not isinstance(checks, list) or len(checks) != len(rules):
            return False
        by_id = {}
        for check in checks:
            if (not isinstance(check, dict) or not isinstance(check.get("caseId"), str)
                    or check["caseId"] in by_id or check.get("status") not in ("pass", "fail")):
                return False
            by_id[check["caseId"]] = check
        return all(rule["id"] in by_id and (not rule.get("required", True)
                   or by_id[rule["id"]]["status"] == "pass") for rule in rules)

    def _run_approve(self, owner, run, body, scope=None, gate=None):
        version = _integer(body.get("contractVersion"), "Contract version", 1, 2**53 - 1)
        number = _integer(body.get("round"), "Round", 1, 5)
        selected = next((row for row in run.get("rounds", []) if row.get("number") == number), None)
        digest = body.get("artifactSha256")
        contract = self._get(owner, "contract", run["contractId"])
        if (run.get("status") not in ("completed", "needs_changes")
                or version != run["contractVersion"] or contract["version"] != version or contract.get("status") != "approved"
                or (contract.get("approval") or {}).get("hash") != run["contractHash"]
                or not selected or selected.get("passed") is not True
                or selected.get("blockingFindings") != []
                or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                or digest != selected.get("artifactSha256") or not selected.get("reportKey")):
            raise HTTPError(409, "approval-evidence-required", "Approve only the exact artifact with passing required evidence")
        _, approval_assets = self._validated_contract(owner, contract, gate=gate)
        html_key = self._blob_key(owner, selected.get("htmlKey"))
        report_key = self._blob_key(owner, selected["reportKey"])
        if self.storage.blob_info(html_key)["sha256"] != digest:
            raise HTTPError(409, "artifact-changed", "The tested artifact bytes have changed")
        report = json.loads(self.storage.get_blob(report_key))
        extra_approval = {}
        if run.get("outputType") == "react":
            from workspace.react_artifacts import read_archive
            from workspace.react_quality import react_report_passes
            self._criteria(owner, {}, scope, contract)
            if (body.get("sourceHash") != selected.get("sourceHash") or body.get("bundleHash") != selected.get("bundleHash")
                    or selected.get("catalogHash") != contract.get("catalogHash")
                    or report.get("sourceHash") != selected.get("sourceHash") or report.get("bundleHash") != selected.get("bundleHash")
                    or report.get("catalogHash") != contract.get("catalogHash")):
                raise HTTPError(409, "react-evidence-mismatch", "선택한 React 소스·배포 파일·코드 기준이 일치해야 합니다.")
            if report.get("visual", {}).get("status") == "review-required":
                if body.get("acceptVariation") is not True:
                    raise HTTPError(409, "variation-review-required", "허용된 화면 변형 범위와 비교 근거를 확인하세요.")
                extra_approval["acceptedVariation"] = True
            if not react_report_passes(run["contract"], report, visual_required=bool(run.get("referenceAssetId")),
                                       visual_policy=run.get("visualPolicy", "exact")):
                raise HTTPError(409, "react-evidence-required", "React 코드·빌드·필수 동작의 검증 근거가 필요합니다.")
            for kind in ("source", "dist"):
                blob_key = self._blob_key(owner, selected.get(kind + "Key"))
                contents = self.storage.get_blob(blob_key)
                if hashlib.sha256(contents).hexdigest() != selected.get(kind + "ArchiveSha256"):
                    raise HTTPError(409, "artifact-changed", "검증된 React 산출물 파일이 변경되었습니다.")
                try:
                    project = read_archive(contents, selected["sourceHash" if kind == "source" else "bundleHash"])
                    if kind == "source" and run["contract"].get("changeRequest"):
                        from workspace.change_requests import baseline_project, enforce_scope
                        from workspace.react_artifacts import generated_files
                        change = run["contract"]["changeRequest"]
                        baseline = baseline_project(self.storage, owner, change, gate=gate)
                        enforce_scope(change, generated_files(baseline) if baseline else {}, generated_files(project))
                except ValueError as error:
                    raise HTTPError(409, "artifact-changed", "검증된 React 파일 구성과 해시가 일치하지 않습니다.") from error
            extra_approval.update(sourceHash=selected["sourceHash"], bundleHash=selected["bundleHash"],
                                  catalogHash=selected["catalogHash"], guidelineId=run.get("guidelineId"))
        elif (not self._passing_evidence(run, report) or not self.rules().report_passes(
                run["contract"], report, visual_required=bool(run.get("referenceAssetId")))):
            raise HTTPError(409, "approval-evidence-required", "The stored verification report did not pass")
        if (report.get("artifactSha256") != digest
                or report.get("contractHash") != run["contractHash"]
                or self.rules().contract_hash(run["contract"]) != run["contractHash"]):
            raise HTTPError(409, "approval-evidence-required", "The stored verification report did not pass")
        result = self._approval_put(owner, "run", {
            **run, "approval": {"round": number, "artifactSha256": digest, "contractVersion": version,
                                "contractHash": run["contractHash"], "actor": scope["actor"] if scope else owner,
                                "at": self.storage.clock(), **extra_approval},
        }, run["version"], scope, "approve", assets=approval_assets, gate=gate)
        return _json(200, {"run": result})

    @staticmethod
    def _query_int(query, key, default, maximum, minimum=0):
        value = query.get(key, str(default))
        if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,9}", value):
            raise HTTPError(400, "invalid-offset", f"Invalid {key}")
        return _integer(int(value), key, minimum, maximum)

    def _release_download(self, owner, release, query):
        kind = query.get("kind", "source")
        if kind not in ("source", "dist", "manifest", "report"):
            raise HTTPError(400, "invalid-kind", "Choose a release artifact")
        if release.get("status") != "ready" and kind != "report":
            raise HTTPError(409, "release-not-ready", "승인본 재빌드·검증이 끝난 뒤 내려받을 수 있습니다.")
        key = self._blob_key(owner, release.get(kind + "Key"))
        info = self.storage.blob_info(key)
        offset = self._query_int(query, "offset", 0, MAX_FILE_BYTES)
        if info["size"] > MAX_FILE_BYTES or offset > info["size"] or (offset == info["size"] and offset != 0):
            raise HTTPError(416, "invalid-range", "Offset is outside the stored artifact")
        data = self.storage.get_blob(key, offset=offset, length=CHUNK_BYTES)
        extension = "zip" if kind in ("source", "dist") else "json"
        filename = f"{release['id']}-{kind}.{extension}"
        return {"statusCode": 200, "isBase64Encoded": True, "body": base64.b64encode(data).decode(),
                "headers": {**_BASE_HEADERS, "Content-Type": "application/octet-stream",
                            "Content-Disposition": f"attachment; filename=\"{filename}\"",
                            "X-Content-Type": info["contentType"], "X-Total-Size": str(info["size"]),
                            "X-Chunk-Size": str(len(data)), "X-SHA256": info["sha256"]}}

    def _download(self, owner, kind, record, query):
        offset = self._query_int(query, "offset", 0, MAX_FILE_BYTES)
        requested = query.get("kind", "original" if kind == "asset" else "html")
        filename, declared_type = record.get("name", "artifact"), "application/octet-stream"
        if kind == "asset":
            if record.get("uploadStatus") != "stored":
                raise HTTPError(409, "asset-not-ready", "File storage is not complete")
            if requested == "original":
                key = record.get("originalKey")
            elif requested == "preview":
                page = self._query_int(query, "page", 1, 10000, minimum=1)
                preview = next((row for row in record.get("previews", []) if row.get("page") == page), None)
                if not preview:
                    raise HTTPError(404, "not-found", "Preview not available")
                key, declared_type = preview.get("key"), preview.get("mime")
            else:
                raise HTTPError(400, "invalid-kind", "Choose original or preview")
        else:
            if requested not in ("html", "screenshot", "diff", "report", "source", "dist", "candidate"):
                raise HTTPError(400, "invalid-kind", "Choose a run artifact kind")
            number = self._query_int(query, "round", 1, 5, minimum=1)
            row = next((row for row in record.get("rounds", []) if row.get("number") == number), None)
            if not row:
                raise HTTPError(404, "not-found", "Resource not found")
            key = row.get(requested + "Key")
            declared_type = "image/png" if requested in ("screenshot", "diff") else "application/octet-stream"
            extension = ".zip" if requested in ("source", "dist") else ".json" if requested in ("candidate", "report") else ".html" if requested == "html" else ".png"
            filename = f"{record['id']}-r{number}-{requested}{extension}"
        key = self._blob_key(owner, key)
        info = self.storage.blob_info(key)
        if info["size"] > MAX_FILE_BYTES or offset > info["size"] or (offset == info["size"] and offset != 0):
            raise HTTPError(416, "invalid-range", "Offset is outside the stored artifact")
        # Only inert raster formats can be navigated inline on the app origin.
        mime = declared_type if declared_type in ("image/png", "image/jpeg", "image/webp") and info["contentType"] == declared_type else "application/octet-stream"
        data = self.storage.get_blob(key, offset=offset, length=CHUNK_BYTES)
        headers = {
            **_BASE_HEADERS, "Content-Type": mime, "X-Content-Type": mime,
            "X-Total-Size": str(info["size"]), "X-Chunk-Size": str(len(data)), "X-SHA256": info["sha256"],
            "Content-Disposition": f"{'inline' if mime.startswith('image/') else 'attachment'}; filename*=UTF-8''{quote(filename, safe='')}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        }
        return {"statusCode": 200, "headers": headers, "body": base64.b64encode(data).decode(), "isBase64Encoded": True}


_api = None


def handler(event, context):
    global _api
    if _api is None:
        _api = WorkspaceAPI()
    return _api.handle(event, context)
