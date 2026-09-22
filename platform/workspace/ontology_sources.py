"""Resolve canonical ontology references through their existing access authority."""
from __future__ import annotations

import hashlib

from workbench.service import fail
from workspace import ontology_schema as schema

PROJECT_AUDIENCE = "current-project-members-v1"


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

    def resolve(self, reference, *, text=False):
        ref = schema.source_ref(reference)
        self._fresh()
        if ref["sourceKind"] in {"asset", "product-guideline", "package"} and "allowedRoles" in ref:
            fail(403, "ontology-source-audience", "프로젝트 공용 원본의 권한을 참조 필드로 변경할 수 없습니다.")
        if "allowedRoles" in ref and self.ctx.scope["role"] not in ref["allowedRoles"]:
            fail(403, "ontology-source-forbidden", "기록된 원본 읽기 권한이 없습니다.")
        kind = ref["sourceKind"]
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
        # Never substitute an ID lookup for the not-yet-configured authority
        # adapters of publications, approved UX contracts or generated rounds.
        fail(503, "ontology-source-adapter-unavailable", "이 원본 유형의 권한 연결이 아직 준비되지 않았습니다.")

    def authorize(self, reference, *, remember=True):
        """Authorize historical metadata, never reuse/approval or original bytes."""
        ref = schema.source_ref(reference)
        self._fresh()
        if ref["sourceKind"] in {"asset", "product-guideline", "package"} and "allowedRoles" in ref:
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
        else:
            self.resolve(ref)
        if remember:
            self.historical_refs[schema.digest(ref)] = ref
        return True

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
        return list(self.observed.values())
