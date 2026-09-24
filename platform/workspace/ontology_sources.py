"""Resolve canonical ontology references through their existing access authority."""
from __future__ import annotations

import hashlib
import json
import re

from workbench.service import fail
from workspace import ontology_schema as schema
from workspace.collaboration import CollaborationError

PROJECT_AUDIENCE = "current-project-members-v1"
# Kinds whose audience is the current project (or grant), never a ref-supplied role list.
_SHARED_AUDIENCE = {"asset", "product-guideline", "package", "run-round", "ux-contract", "published-asset"}
# edit_design-class roles may inspect draft/failed/needs-changes rounds (AGENTCORE_CONTRACT publishing-handoff/1).
ROUND_EDITORS = frozenset({"owner", "designer", "developer"})
MAX_ROUND_ADMISSIONS = 50
MAX_ARCHIVE_BYTES = 8_000_000
_ROUND = re.compile(r"[1-9][0-9]{0,3}\Z")
_VERSION = re.compile(r"[1-9][0-9]{0,15}\Z")
# Authority failures pass through unchanged; they are not upstream lineage outcomes.
_AUTHORITY_CODES = {"ontology-authority-changed", "ontology-authority-limit", "authorization-expired"}


def _not_found():
    # One message for missing and inaccessible records: no existence is disclosed.
    fail(404, "not-found", "원본을 찾을 수 없거나 읽을 권한이 없습니다.")


def _authority_error(error):
    return error.status in (401, 403) or error.code in _AUTHORITY_CODES


def round_state(run, row):
    """publishing-handoff/1 state of one generated round, fail-closed to the restricted states."""
    approval = run.get("approval") or {}
    if row.get("passed") is True and approval.get("round") == row.get("number") and (
            row.get("sourceHash") and approval.get("sourceHash") == row["sourceHash"]
            # An HTML round's approval binds its exact artifact bytes instead.
            or not row.get("sourceHash") and row.get("artifactSha256")
            and approval.get("artifactSha256") == row["artifactSha256"]):
        return "approved"
    verification = row.get("verification")
    if (row.get("passed") is True and row.get("blockingFindings") == []
            and run.get("status") in ("completed", "needs_changes")
            and (verification is None or isinstance(verification, dict) and verification.get("approvable") is True)):
        return "reviewable"
    return "draft" if run.get("status") in ("queued", "running") else "failed"


def authority_identity(reference):
    ref = schema.source_ref(reference)
    value = {key: item for key, item in ref.items() if key != "location"}
    if ref.get("location", {}).get("documentId"):
        value["documentId"] = ref["location"]["documentId"]
    return schema.digest(value)


def asset_reference(asset):
    return {"sourceKind": "asset", "sourceId": asset["id"],
            "revision": str(asset.get("importRevision", 1)), "sha256": asset["sha256"],
            "audienceRevision": PROJECT_AUDIENCE}


def guideline_reference(guideline):
    return {"sourceKind": "product-guideline", "sourceId": guideline["id"],
            "revision": str(guideline["revision"]), "sha256": guideline["sha256"],
            "audienceRevision": PROJECT_AUDIENCE}


def run_round_reference(run, number):
    row = next((item for item in run.get("rounds", []) if item.get("number") == number), None)
    if row is None or not row.get("sourceHash"):
        raise ValueError("The run has no generated source for this round")
    return {"sourceKind": "run-round", "sourceId": run["id"], "revision": str(number),
            "sha256": row["sourceHash"], "audienceRevision": PROJECT_AUDIENCE}


def contract_reference(contract):
    approval = contract.get("approval") or {}
    if contract.get("status") != "approved" or not approval.get("hash"):
        raise ValueError("Only an approved contract is a ux-contract source")
    return {"sourceKind": "ux-contract", "sourceId": contract["id"], "revision": str(contract["version"]),
            "sha256": approval["hash"], "audienceRevision": PROJECT_AUDIENCE}


def workbench_reference(reference):
    return {"sourceKind": "workbench-document", "sourceId": reference["sourceId"],
            "revision": reference["generation"], "sha256": reference["contentHash"],
            "audienceRevision": str(reference["permissionVersion"]),
            "location": {"documentId": reference["documentId"]}, "allowedRoles": reference["allowedRoles"]}


class Sources:
    def __init__(self, context, *, max_records=90, max_sources=50):
        self.ctx = context
        self.storage = context.storage
        self.observed = {}
        self.package_hashes = set()
        self.workbench_refs = {}
        self.historical_refs = {}
        self.max_records = max_records
        self.max_sources = max_sources
        self.ctx.fresh()
        self.authority = self._authority()

    def _authority(self):
        project = self.ctx.scope["project"]
        membership = {actor: member["role"] for actor, member in project["members"].items()}
        return (self.ctx.actor, self.ctx.project_id, self.ctx.scope["role"],
                schema.digest(membership), project.get("status"), project.get("archived", False),
                project.get("authorityRevision", 0))

    def _fresh(self):
        self.ctx.fresh()
        if self._authority() != self.authority:
            fail(409, "ontology-authority-changed", "조회 중 프로젝트 역할 또는 권한이 변경되었습니다.")

    def _remember(self, kind, record):
        check = self.ctx.check(kind, record)
        key = (check["owner"], kind, record["id"])
        prior = self.observed.get(key)
        if prior and prior != check:
            fail(409, "ontology-source-changed", "온톨로지 원본이 조회 중 변경되었습니다.")
        self.observed[key] = check
        if len(self.observed) > self.max_records:
            fail(422, "ontology-authority-limit", "근거 조회 범위 제한을 초과했습니다. 분석 단위를 나누세요.")
        return record

    def _remember_owned(self, owner, kind, record):
        """Observe an upstream record in its actual owner partition (e.g. intake:deployment)."""
        check = {"owner": owner, "kind": kind, "id": record["id"], "version": record["version"]}
        key = (owner, kind, record["id"])
        prior = self.observed.get(key)
        if prior and prior != check:
            fail(409, "ontology-source-changed", "온톨로지 원본이 조회 중 변경되었습니다.")
        self.observed[key] = check
        if len(self.observed) > self.max_records:
            fail(422, "ontology-authority-limit", "근거 조회 범위 제한을 초과했습니다. 분석 단위를 나누세요.")
        return record

    def _blob(self, key, expected, maximum=2_000_000):
        if not self.storage.owns_key(self.ctx.owner, key):
            fail(403, "ontology-source-forbidden", "이 프로젝트에서 읽을 수 없는 원본입니다.")
        info = self.storage.blob_info(key)
        if info["sha256"] != expected or info["size"] > maximum:
            fail(409, "ontology-source-integrity", "온톨로지 원본 크기 또는 해시가 다릅니다.")
        raw = self.storage.get_blob(key, length=maximum)
        if len(raw) != info["size"] or hashlib.sha256(raw).hexdigest() != expected:
            fail(409, "ontology-source-integrity", "온톨로지 원본 바이트를 확인하지 못했습니다.")
        return raw

    def resolve(self, reference, *, text=False, historical=False):
        """Current authority. `historical=True` returns metadata only, never reuse authority."""
        ref = schema.source_ref(reference)
        if historical:
            if text:
                fail(422, "ontology-source-historical", "과거 원본은 메타데이터만 조회할 수 있습니다.")
            record = self._authorize(ref, remember=True)
            return {"ref": ref, "kind": ref["sourceKind"], "record": record, "text": None, "historical": True}
        self._fresh()
        if ref["sourceKind"] in _SHARED_AUDIENCE and "allowedRoles" in ref:
            fail(403, "ontology-source-audience", "프로젝트 공용 원본의 권한을 참조 필드로 변경할 수 없습니다.")
        if "allowedRoles" in ref and self.ctx.scope["role"] not in ref["allowedRoles"]:
            fail(403, "ontology-source-forbidden", "기록된 원본 읽기 권한이 없습니다.")
        kind = ref["sourceKind"]
        if kind == "run-round":
            run, row = self._round(ref)
            if run.get("archived"):
                fail(409, "ontology-source-stale", "보관된 생성 실행의 라운드는 현재 근거로 사용할 수 없습니다.")
            self._round_upstream_current(run)
            self._round_admissions_current(row)
            content = None
            if text:
                path = ref.get("location", {}).get("path")
                if not path:
                    fail(400, "ontology-source-location", "라운드 원본 파일 경로가 필요합니다.")
                files = self._round_files(row)
                if path not in files:
                    _not_found()
                if len(files[path]) > 102400:
                    fail(422, "ontology-source-limit", "분석할 텍스트 원본은 100 KiB 이하여야 합니다.")
                try:
                    content = files[path].decode("utf-8")
                except UnicodeError:
                    fail(422, "ontology-source-format", "텍스트로 분석할 수 없는 원본입니다.")
            return {"ref": ref, "kind": kind, "record": row, "text": content}
        if kind == "published-asset":
            from workspace.publications import resolve_published
            value = resolve_published(self, ref, text=text)
            return {"ref": ref, "kind": kind, "record": value["record"], "text": value["text"]}
        if kind == "ux-contract":
            contract, _ = self._contract(ref, historical=False)
            content = None
            if text:
                normalized = self._rules().validate_contract(contract)
                content = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return {"ref": ref, "kind": kind, "record": contract, "text": content}
        if kind == "asset":
            asset = self._remember("asset", self.ctx.get("asset", ref["sourceId"]))
            if (asset.get("archived") or asset.get("accessRevoked") or asset.get("tombstone")
                    or asset.get("status") == "deleted" or asset.get("uploadStatus") != "stored"):
                fail(409, "ontology-source-stale", "보관 완료된 활성 원본이 필요합니다.")
            expected = asset_reference(asset)
            self._identity(ref, expected)
            key = asset.get("originalKey")
            if not key or not self.storage.owns_key(self.ctx.owner, key):
                fail(403, "ontology-source-forbidden", "원본 위치를 확인하지 못했습니다.")
            info = self.storage.blob_info(key)
            if info["sha256"] != ref["sha256"] or info["size"] != asset["size"]:
                fail(409, "ontology-source-integrity", "등록한 원본 파일이 변경되었습니다.")
            content = None
            if text:
                if asset["size"] > 102400:
                    fail(422, "ontology-source-limit", "분석할 텍스트 원본은 100 KiB 이하여야 합니다.")
                try:
                    content = self._blob(key, ref["sha256"], 102400).decode("utf-8")
                except UnicodeError:
                    fail(422, "ontology-source-format", "텍스트로 분석할 수 없는 원본입니다.")
            return {"ref": ref, "kind": kind, "record": asset, "text": content}
        if kind == "product-guideline":
            guide = self._remember("guideline", self.ctx.get("guideline", ref["sourceId"]))
            product = self._remember("product", self.ctx.get("product", guide["productId"]))
            if guide.get("status") != "published" or product.get("publishedGuidelineId") != guide["id"]:
                fail(409, "ontology-source-stale", "현재 게시된 상품 기준이 아닙니다.")
            self._identity(ref, guideline_reference(guide))
            context = self.ctx.collaboration.published_context(self.ctx.scope, product["id"])
            if context["guideline"]["sha256"] != ref["sha256"]:
                fail(409, "ontology-source-integrity", "게시 기준의 원문 근거가 다릅니다.")
            return {"ref": ref, "kind": kind, "record": guide, "text": guide.get("text") if text else None}
        if kind == "workbench-document":
            from workbench import knowledge
            identifier = ref.get("location", {}).get("documentId")
            if not identifier:
                fail(400, "ontology-source-location", "지식 문서의 원본 ID가 필요합니다.")
            document_id = "d-" + schema.digest([ref["sourceId"], identifier])[:40]
            result = knowledge.read_document(self.ctx, document_id)
            expected = workbench_reference(result["evidence"])
            self._identity(ref, expected)
            if ref.get("location", {}).get("documentId") != expected["location"]["documentId"]:
                fail(409, "ontology-source-stale", "지식 원본 문서가 변경되었습니다.")
            if "allowedRoles" in ref and ref["allowedRoles"] != expected["allowedRoles"]:
                fail(409, "ontology-source-stale", "원본의 기록된 읽기 권한이 다릅니다.")
            self.workbench_refs[schema.digest(result["evidence"])] = result["evidence"]
            for check in knowledge.verify_refs(self.ctx, [result["evidence"]]):
                record = self.storage.get(check["owner"], check["kind"], check["id"])
                if not record or record["version"] != check["version"]:
                    fail(409, "ontology-source-changed", "지식 원본 권한이 변경되었습니다.")
                self._remember(check["kind"], record)
            return {"ref": ref, "kind": kind, "record": result["document"],
                    "text": result["document"]["content"] if text else None}
        if kind == "document-revision":
            from documents.library import Library
            from documents.errors import DocumentError
            try:
                library = Library(self.ctx.host, self.ctx.scope)
                document = library.document(ref["sourceId"])
                revision = library.revision(document, ref["revision"], approved=True)
                if revision["sha256"] != ref["sha256"] or str(document["aclVersion"]) != ref["audienceRevision"]:
                    fail(409, "ontology-source-stale", "문서 원본 또는 읽기 권한이 변경되었습니다.")
                if "allowedRoles" in ref and ref["allowedRoles"] != document["readRoles"]:
                    fail(409, "ontology-source-stale", "문서의 기록된 읽기 권한이 다릅니다.")
                self._remember("document", document)
                self._remember("docrevision", revision)
                # A transcription revision's lineage (image asset, image admission
                # decision, policy/grant) is fenced with each record's own owner.
                for check in library._upstream:
                    self._remember_owned(check["owner"], check["kind"], check)
                projection = library.projection(document, revision) if text else None
                return {"ref": ref, "kind": kind, "record": revision,
                        "text": "\n".join(p["text"] for p in projection["paragraphs"]) if projection else None}
            except DocumentError as error:
                fail(error.status, error.code, error.message)
        if kind == "package":
            from workspace.component_catalog import read_catalog
            catalog = read_catalog()
            if (ref["sourceId"] != catalog["id"] or ref["revision"] != catalog["version"]
                    or ref["sha256"] != catalog["hash"] or ref["audienceRevision"] != "platform-package-v1"):
                fail(409, "ontology-package-stale", "플랫폼 컴포넌트 기준이 변경되었습니다.")
            self.package_hashes.add(catalog["hash"])
            return {"ref": ref, "kind": kind, "record": catalog, "text": None}
        # Never substitute an ID lookup for an authority adapter that is not installed.
        fail(503, "ontology-source-adapter-unavailable", "이 원본 유형의 권한 연결이 아직 준비되지 않았습니다.")

    def authorize(self, reference, *, remember=True):
        """Authorize historical metadata, never reuse/approval or original bytes."""
        self._authorize(schema.source_ref(reference), remember)
        return True

    def _authorize(self, ref, remember):
        """Historical checks; returns the adapter record for installed sharing kinds."""
        self._fresh()
        record = None
        if ref["sourceKind"] in _SHARED_AUDIENCE and "allowedRoles" in ref:
            fail(403, "ontology-source-audience", "프로젝트 공용 원본의 권한을 참조 필드로 변경할 수 없습니다.")
        kind = ref["sourceKind"]
        if "allowedRoles" in ref and self.ctx.scope["role"] not in ref["allowedRoles"]:
            fail(403, "ontology-source-forbidden", "기록된 원본 읽기 권한이 없습니다.")
        if kind == "asset":
            asset = self._remember("asset", self.ctx.get("asset", ref["sourceId"]))
            if asset.get("accessRevoked") or asset.get("tombstone") or asset.get("status") == "deleted":
                fail(403, "ontology-source-forbidden", "원본의 읽기 권한이 회수되었습니다.")
            if ref["audienceRevision"] != PROJECT_AUDIENCE:
                fail(403, "ontology-source-forbidden", "기록된 원본 권한을 확인하지 못했습니다.")
        elif kind == "product-guideline":
            self._remember("guideline", self.ctx.get("guideline", ref["sourceId"]))
            if ref["audienceRevision"] != PROJECT_AUDIENCE:
                fail(403, "ontology-source-forbidden", "기록된 지침 권한을 확인하지 못했습니다.")
        elif kind == "document-revision":
            from documents.library import Library
            from documents.errors import DocumentError
            try:
                library = Library(self.ctx.host, self.ctx.scope)
                document = library.document(ref["sourceId"])
                revision = library.revision(document, ref["revision"])
                if "allowedRoles" not in ref and str(document["aclVersion"]) != ref["audienceRevision"]:
                    fail(403, "ontology-source-forbidden", "과거 문서의 읽기 권한 근거가 없습니다.")
                self._remember("document", document)
                self._remember("docrevision", revision)
            except DocumentError as error:
                fail(error.status, error.code, error.message)
        elif kind == "workbench-document":
            from workbench import knowledge
            source = self._remember("wb_source", self.ctx.get("wb_source", ref["sourceId"]))
            access = source.get("access", {}).get(ref.get("location", {}).get("documentId"), {})
            if (not knowledge.source_current(self.ctx, source) or access.get("tombstone") is not False
                    or self.ctx.scope["role"] not in access.get("allowedRoles", [])
                    or "allowedRoles" not in ref and str(source["permissionVersion"]) != ref["audienceRevision"]):
                fail(403, "ontology-source-forbidden", "과거 지식의 현재 읽기 권한을 확인하지 못했습니다.")
        elif kind == "package":
            if ref["sourceId"] != "studio-ui" or ref["audienceRevision"] != "platform-package-v1":
                fail(403, "ontology-source-forbidden", "허용된 플랫폼 패키지가 아닙니다.")
        elif kind == "run-round":
            run, record = self._round(ref)
            self._round_upstream_historical(run, record)
        elif kind == "ux-contract":
            record, _ = self._contract(ref, historical=True)
        elif kind == "published-asset":
            from workspace.publications import resolve_published
            record = resolve_published(self, ref, historical=True)["record"]
        else:
            self.resolve(ref)
        if remember:
            self.historical_refs[schema.digest(ref)] = ref
        return record

    # run-round ------------------------------------------------------------

    def _round(self, ref):
        """The exact round of a React run in this project, readable by this role in its state.

        Missing, foreign-project and state-restricted rounds all fail identically
        before any hash comparison, so a restricted reader learns nothing.
        """
        try:
            run = self.ctx.get("run", ref["sourceId"])
        except CollaborationError as error:
            if error.status == 404:
                _not_found()
            raise
        if run.get("outputType") != "react" or not _ROUND.fullmatch(ref["revision"]):
            _not_found()
        number = int(ref["revision"])
        rows = [row for row in run.get("rounds", []) if isinstance(row, dict) and row.get("number") == number]
        if len(rows) != 1:
            _not_found()
        row = rows[0]
        self._round_permission(run, row)
        location = ref.get("location", {})
        if "round" in location and location["round"] != number:
            fail(400, "ontology-source-location", "라운드 위치가 참조 리비전과 다릅니다.")
        if not row.get("sourceHash") or row["sourceHash"] != ref["sha256"]:
            fail(409, "source-changed", "생성 라운드의 원본 해시가 다릅니다.")
        if ref["audienceRevision"] != PROJECT_AUDIENCE:
            fail(409, "ontology-source-stale", "라운드의 읽기 권한 기준이 다릅니다.")
        self._remember("run", run)
        return run, row

    def _round_permission(self, run, row):
        """publishing-handoff/1 content permission: restricted states are owner/designer/developer only."""
        if (round_state(run, row) not in ("reviewable", "approved")
                and self.ctx.scope["role"] not in ROUND_EDITORS):
            _not_found()

    def round_delivery(self, run_id, number):
        """The copy/download gate for every stored artifact of one round (each chunk, release, export).

        The same content permission and upstream-lineage checks as the `run-round`
        adapter: a restricted state or a revoked upstream (contract revision,
        admission, guideline or bound input asset) is `404 not-found`. A merely
        superseded upstream keeps diagnostic delivery (publishing-handoff/1
        "Stale/withdrawn/quarantined"); current reuse still requires `resolve`.
        """
        self._fresh()
        try:
            run = self.ctx.get("run", run_id)
        except CollaborationError as error:
            if error.status == 404:
                _not_found()
            raise
        rows = [row for row in run.get("rounds", []) if isinstance(row, dict) and row.get("number") == number]
        if type(number) is not int or len(rows) != 1:
            _not_found()
        row = rows[0]
        self._round_permission(run, row)
        self._remember("run", run)
        self._round_upstream_historical(run, row)
        self.recheck()
        return run, row

    def _round_files(self, row):
        raw = self._round_archive(row)
        from workspace.react_artifacts import read_archive
        try:
            return read_archive(raw, row["sourceHash"])
        except (ValueError, KeyError, OSError) as error:
            fail(409, "ontology-source-integrity", "라운드 원본 파일 구성과 해시가 일치하지 않습니다.")
            raise error  # pragma: no cover

    def _round_archive(self, row):
        key, expected = row.get("sourceKey"), row.get("sourceArchiveSha256")
        if not key or not isinstance(expected, str):
            fail(409, "ontology-source-integrity", "라운드 원본 파일을 확인하지 못했습니다.")
        return self._blob(key, expected, MAX_ARCHIVE_BYTES)

    def release_source(self, reference):
        """Verified source-archive bytes of a current round, under the same constraints as `resolve`."""
        value = self.resolve(reference)
        if value["kind"] != "run-round":
            fail(422, "ontology-source-kind", "생성 라운드 원본만 내려받을 수 있습니다.")
        row = value["record"]
        files = self._round_files(row)
        raw = self._round_archive(row)
        self.recheck()
        return {"ref": value["ref"], "bytes": raw, "sha256": row["sourceArchiveSha256"],
                "sourceHash": row["sourceHash"], "files": sorted(files)}

    @staticmethod
    def _contract_ref(run):
        version = run.get("contractVersion")
        if type(version) is not int or not isinstance(run.get("contractId"), str) or not run.get("contractHash"):
            fail(409, "source-upstream-revoked", "라운드의 승인 규칙 근거가 없습니다.")
        return {"sourceKind": "ux-contract", "sourceId": run["contractId"], "revision": str(version),
                "sha256": run["contractHash"], "audienceRevision": PROJECT_AUDIENCE}

    def _round_upstream_current(self, run):
        """The approved contract and published product/guideline criteria must still be current."""
        from workspace.criteria import resolve_generation_context
        try:
            self._contract(self._contract_ref(run), historical=False)
            if self._rules().contract_hash(run.get("contract") or {}) != run["contractHash"]:
                fail(409, "source-changed", "라운드의 규칙 사본이 승인본과 다릅니다.")
            resolve_generation_context(self.storage, self.ctx.owner, {**run, "actor": self.ctx.actor}, "read")
        except CollaborationError as error:
            if _authority_error(error):
                raise
            fail(409, "source-upstream-revoked", "라운드의 규칙 또는 상품 기준이 더 이상 현재 승인본이 아닙니다.")
        except ValueError:
            fail(409, "source-upstream-revoked", "라운드의 규칙 또는 상품 기준이 더 이상 현재 승인본이 아닙니다.")

    @staticmethod
    def _admission_refs(row):
        manifest = row.get("designManifestInput")
        if manifest is None:
            return []
        admissions = manifest.get("admissions", []) if isinstance(manifest, dict) else None
        if not isinstance(admissions, list) or len(admissions) > MAX_ROUND_ADMISSIONS or any(
                not isinstance(item, dict) or set(item) != {"decisionId", "revision", "artifactHash"}
                or not isinstance(item["decisionId"], str) for item in admissions):
            fail(409, "source-upstream-revoked", "라운드의 반입 승인 근거 형식을 확인하지 못했습니다.")
        return admissions

    @staticmethod
    def _admission_matches(decision, binding):
        return (str(decision["revision"]) == str(binding["revision"])
                and decision["derivation"]["derivativeHash"] == binding["artifactHash"])

    def _round_admissions_current(self, row):
        from intake import admission
        for binding in self._admission_refs(row):
            observed = []
            try:
                decision = admission.verify(self.ctx.host, self.ctx.scope, binding["decisionId"],
                                            claims=self.ctx.claims, sources=self, observe=observed)
            except admission.AdmissionError:
                fail(409, "source-upstream-revoked", "라운드 입력의 반입 승인이 회수되었거나 현재가 아닙니다.")
            except CollaborationError as error:
                if _authority_error(error):
                    raise
                fail(409, "source-upstream-revoked", "라운드 입력의 반입 승인이 회수되었거나 현재가 아닙니다.")
            if not self._admission_matches(decision, binding):
                fail(409, "source-upstream-revoked", "라운드 입력의 반입 승인 리비전이 다릅니다.")
            for check in observed:
                self._remember_owned(check["owner"], check["kind"], check)

    def _round_upstream_historical(self, run, row):
        """Historical round metadata requires upstream permission, not upstream currency.

        A superseded but still readable upstream admits diagnostics; a revoked one
        (contract revision gone, admission grant/decision revoked, guideline no
        longer published) denies with the same not-found error.
        """
        from intake import admission
        try:
            self._contract(self._contract_ref(run), historical=True)
            if run.get("productId"):
                product = self.ctx.get("product", run["productId"])
                guideline = self.ctx.get("guideline", run.get("guidelineId") or "-")
                if guideline.get("productId") != product["id"] or guideline.get("status") != "published":
                    _not_found()
            for binding in self._admission_refs(row):
                probe = Sources(self.ctx)
                try:
                    decision = admission.verify(self.ctx.host, self.ctx.scope, binding["decisionId"],
                                                claims=self.ctx.claims, sources=probe)
                except admission.AdmissionError as error:
                    if error.code != "source-changed":
                        _not_found()
                    # Superseded source: the decision itself is still current and admitted.
                    decision = self.storage.get(self.ctx.owner, "adm_decision", binding["decisionId"])
                    if not decision or decision["source"]["sourceKind"] == "prompt-text":
                        _not_found()
                    Sources(self.ctx).authorize(dict(decision["source"]), remember=False)
                if not self._admission_matches(decision, binding):
                    _not_found()
        except CollaborationError as error:
            # Only expiry and frozen-authority failures pass through; every upstream
            # permission failure is indistinguishable from a missing record.
            if error.status == 401 or error.code in _AUTHORITY_CODES:
                raise
            _not_found()

    # ux-contract ----------------------------------------------------------

    def _rules(self):
        rules = getattr(self.ctx.host, "rules", None)
        if callable(rules):
            return rules()
        from workspace import rules as module
        return module

    def _contract(self, ref, *, historical):
        """An exact approved contract revision; current use also requires current criteria."""
        try:
            contract = self.ctx.get("contract", ref["sourceId"])
        except CollaborationError as error:
            if error.status == 404:
                _not_found()
            raise
        if not _VERSION.fullmatch(ref["revision"]):
            _not_found()
        version = int(ref["revision"])
        approval = contract.get("approval") or {}
        current = (contract.get("status") == "approved" and contract.get("version") == version
                   and approval.get("version") == version and approval.get("hash") == ref["sha256"])
        retained = any(isinstance(item, dict) and item.get("version") == version and item.get("hash") == ref["sha256"]
                       for item in contract.get("revisions", []))
        if not current and not retained:
            if contract.get("status") != "approved":
                _not_found()
            fail(409, "source-changed", "승인 규칙의 리비전 또는 해시가 다릅니다.")
        if ref["audienceRevision"] != PROJECT_AUDIENCE:
            fail(409, "ontology-source-stale", "승인 규칙의 읽기 권한 기준이 다릅니다.")
        self._remember("contract", contract)
        if current:
            try:
                digest = self._rules().contract_hash(contract)
            except ValueError:
                digest = None
            if digest != approval.get("hash"):
                fail(409, "source-changed", "승인 규칙 내용이 승인 해시와 다릅니다.")
        if historical:
            return contract, current
        if not current:
            fail(409, "source-superseded", "새 규칙 리비전으로 대체된 승인 규칙입니다.")
        criteria = {key: contract[key] for key in ("projectId", "productId", "guidelineId", "ontologyHash")
                    if key in contract}
        superseded = not self.ctx.collaboration.is_current(self.ctx.scope, criteria)
        if not superseded and contract.get("catalogHash"):
            from workspace.component_catalog import read_catalog
            superseded = read_catalog()["hash"] != contract["catalogHash"]
        if superseded:
            fail(409, "source-superseded", "승인 규칙의 상품·가이드 기준이 현재 게시본이 아닙니다.")
        return contract, current

    @staticmethod
    def _identity(actual, expected):
        if any(actual.get(key) != expected.get(key) for key in
               ("sourceKind", "sourceId", "revision", "sha256", "audienceRevision")):
            fail(409, "ontology-source-stale", "온톨로지의 원본 버전 또는 권한 기준이 변경되었습니다.")

    def verify(self, references, *, recheck=True):
        if not isinstance(references, list) or len(references) > 30000:
            fail(422, "ontology-source-limit", "한 번에 확인할 원본 근거가 너무 많습니다.")
        unique = {}
        for raw in references:
            ref = schema.source_ref(raw)
            unique[authority_identity(ref)] = ref
        if len(unique) > self.max_sources:
            fail(422, "ontology-source-limit", "서로 다른 원본 근거 수 제한을 초과했습니다. 범위를 나누세요.")
        for ref in unique.values():
            self.resolve(ref)
        return self.recheck() if recheck else list(self.observed.values())

    def recheck(self):
        self._fresh()
        if self.workbench_refs:
            from workbench import knowledge
            refs = list(self.workbench_refs.values())
            for offset in range(0, len(refs), 50):
                knowledge.verify_refs(self.ctx, refs[offset:offset + 50])
        for ref in list(self.historical_refs.values()):
            self.authorize(ref, remember=False)
        if self.package_hashes:
            from workspace.component_catalog import read_catalog
            if self.package_hashes != {read_catalog()["hash"]}:
                fail(409, "ontology-package-stale", "조회 중 컴포넌트 기준이 변경되었습니다.")
        for check in self.observed.values():
            row = self.storage.get(check["owner"], check["kind"], check["id"])
            if not row or row["version"] != check["version"]:
                fail(409, "ontology-source-changed", "조회 중 원본 또는 접근 권한이 변경되었습니다.")
            if check["kind"] in ("adm_policy", "adm_provenance", "adm_grant", "adm_decision", "capability"):
                from intake.records import is_current
                if not is_current(row, self.storage.clock()):
                    fail(409, "source-upstream-revoked", "원본 반입 승인이 만료되었거나 회수되었습니다.")
        return list(self.observed.values())
