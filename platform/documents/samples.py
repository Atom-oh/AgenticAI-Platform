"""Explicitly requested private examples, never original bank policy or approval."""
from __future__ import annotations

import base64
import hashlib
import json

from documents.errors import DocumentError
from documents.library import Library, public_document, request_id

SAMPLES = (
    ("REG-LN-001", "전세자금대출 담보 인정 기준", "regulation",
     "담보 인정 기준을 바꾸는 경우 상품 조건, 고객 안내, 업무 화면, 구성 컴포넌트와 관련 문서가 같은 변경 범위를 따르는지 검토합니다.",
     "영향 목록은 등록된 관계에서 찾은 후보입니다. 담당자가 각 원문과 변경 내용을 대조한 뒤 수정 여부를 결정합니다."),
    ("DOC-000", "전세자금대출 담보 인정 기준 개정 기안", "policy",
     "검토자는 담보 인정 범위의 변경 사유와 대상 조항, 변경 전후의 설명을 기안에 기록합니다.",
     "관련 상품과 화면의 검토 결과, 협의 담당자와 남은 확인 사항을 기안의 근거로 연결합니다."),
    ("DOC-001", "전세대출 상품 심의 보고", "report",
     "상품 심의에서는 담보 기준과 상품 설명이 일치하는지 확인하고, 조건 차이가 있으면 검토 의견을 남깁니다.",
     "관계 목록만으로 상품 조건을 자동 변경하거나 심의 완료로 처리하지 않습니다."),
    ("DOC-002", "전세대출 화면 변경 요청", "specification",
     "화면 담당자는 입력 항목, 안내 문구, 조건 분기와 재사용 컴포넌트에 변경이 필요한지 검토합니다.",
     "수정 요청에는 대상 화면과 컴포넌트의 식별자, 승인된 원문 버전과 필요한 동작 검증을 기록합니다."),
    ("DOC-003", "담보 인정 비율 리스크 점검", "report",
     "리스크 점검자는 기준 변경이 상품의 적용 범위와 조건에 미치는 영향을 검토합니다.",
     "실제 수치와 한도는 승인된 상품 기준으로 확인해야 하며 이 합성 문서에는 실제 금융 수치가 없습니다."),
    ("DOC-004", "전세대출 영업점 시행 공문", "notice",
     "시행 공문 담당자는 승인된 변경 내용과 적용 대상, 시행 시점, 문의 담당자를 원문에 근거하여 작성합니다.",
     "미승인 변경안이나 검토 중인 영향 후보를 시행 확정 사항으로 안내하지 않습니다."),
    ("DOC-005", "전세대출 취급 교육 자료", "guide",
     "교육 담당자는 변경된 기준, 화면 안내와 업무 절차가 교육 자료에 반영되어야 하는지 검토합니다.",
     "교육 자료에는 확인한 원문 버전과 관련 화면을 연결하고, 미확인 사항을 별도로 남깁니다."),
)
SAMPLE_NOTICE = (
    "이 파일은 문서함과 검토 흐름의 동작 확인을 위한 합성 자료입니다. "
    "실제 은행 내규, 상품 조건 또는 법률 자문이 아닙니다."
)


def _definition(sample):
    ref, title, kind, paragraph1, paragraph2 = sample
    data = (
        f"# [합성 예제] {title}\n\n{SAMPLE_NOTICE}\n\n"
        f"## 검토 항목\n\n{paragraph1}\n\n## 확인 원칙\n\n{paragraph2}\n"
    ).encode()
    return {
        "graphRef": ref, "title": "[합성 예제] " + title, "kind": kind,
        "name": ref + "-sample.md", "versionLabel": "검증 예제 1",
        "sha256": hashlib.sha256(data).hexdigest(),
        "sections": [{"title": "검토 항목", "text": paragraph1}, {"title": "확인 원칙", "text": paragraph2}],
    }, data


def sample_catalog():
    """Public template descriptions, not a list of a collection's private files."""
    return {"schemaVersion": 1, "notice": SAMPLE_NOTICE,
            "samples": [_definition(sample)[0] for sample in SAMPLES]}


def _invoke(host, scope, method, parts, body, *, trusted=False):
    from documents.api import handle
    if isinstance(body, bytes):
        event = {"body": base64.b64encode(body).decode(), "isBase64Encoded": True}
    else:
        event = {"body": json.dumps(body, ensure_ascii=False)}
    response = handle(host, scope, method, parts, event, {}, trusted_sample=trusted)
    payload = json.loads(response["body"])
    if response["statusCode"] >= 400:
        raise DocumentError(response["statusCode"], payload.get("code", "sample-failed"),
                            payload.get("error", "예제 등록을 완료하지 못했습니다."))
    return payload


def install_samples(host, scope, body):
    if set(body) != {"requestId"}:
        raise DocumentError(400, "invalid-input", "예제 등록 요청 ID만 전달하세요.")
    request = request_id(body["requestId"])
    if len(request) > 70:
        raise DocumentError(400, "invalid-input", "예제 등록 요청 ID가 너무 깁니다.")
    documents, jobs = [], []
    for sample in SAMPLES:
        template, data = _definition(sample)
        ref = template["graphRef"]
        library = Library(host, scope)
        library.fresh()
        if library.scope["role"] != "owner":
            raise DocumentError(403, "forbidden", "문서함 소유자만 합성 예제를 등록할 수 있습니다.")
        created = _invoke(host, library.scope, "POST", ["documents"], {
            "requestId": request + ":" + ref, "title": template["title"], "kind": template["kind"],
            "graphRef": ref, "name": template["name"], "size": len(data),
            "sha256": template["sha256"], "versionLabel": template["versionLabel"],
        }, trusted=True)
        doc, revision = created["document"], created["revision"]
        root = ["documents", doc["id"], "revisions", revision["id"]]
        if revision["status"] == "uploading":
            _invoke(host, library.scope, "PUT", root + ["parts", "0"], data)
        completed = _invoke(host, library.scope, "POST", root + ["complete"], {})
        documents.append(completed["document"])
        jobs.append({key: completed["job"][key] for key in ("id", "status")})
    current = Library(host, scope)
    current.fresh()
    if current.scope["role"] != "owner":
        raise DocumentError(403, "forbidden", "예제 등록 중 소유자 권한이 변경되었습니다.")
    # Final access check before returning any document names.
    final_docs = [current.document(doc["id"]) for doc in documents]
    current.assert_current(final_docs)
    return {"documents": [public_document(doc) for doc in final_docs], "jobs": jobs,
            "note": "합성 예제가 등록되었습니다. 본문 추출 후 내용을 검토하고 별도로 승인하세요. 자동 승인은 수행하지 않습니다."}
