"""Private, page-addressable guideline extracts; never executable instructions."""
from __future__ import annotations

import hashlib
import json
import re

FORMAT = "ux-guidelines"
MAX_PACK_BYTES = 16 * 1024 * 1024
MAX_PAGES = 1200
MAX_PAGE_CHARS = 20_000
MAX_TOTAL_CHARS = 3_000_000
MAX_REFS = 12
MAX_CONTEXT_CHARS = 60_000
CATEGORIES = ("foundation", "interaction", "graphics", "content", "writing", "general", "specification", "source", "inventory")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _string(value, maximum, label, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f"가이드 {label}을 확인하세요.")
    value.encode("utf-8")
    return value


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("가이드 식별자가 올바르지 않습니다.")
    return value


def _hash(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError("가이드 원문 해시가 올바르지 않습니다.")
    return value


def validate_pack(value):
    if not isinstance(value, dict) or value.get("format") != FORMAT or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("지원하지 않는 UX 가이드 묶음입니다.")
    sources = value.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError("가이드 원본은 1~20개가 필요합니다.")
    result, identifiers, page_count, characters = [], set(), 0, 0
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("가이드 원본 정보가 올바르지 않습니다.")
        identifier = _id(source.get("id"))
        if identifier in identifiers:
            raise ValueError("가이드 원본 식별자가 중복되었습니다.")
        identifiers.add(identifier)
        category = source.get("category", "general")
        if category not in CATEGORIES:
            raise ValueError("지원하지 않는 가이드 분류입니다.")
        pages = source.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError("가이드 페이지가 없습니다.")
        page_count += len(pages)
        if page_count > MAX_PAGES:
            raise ValueError("가이드 묶음은 최대 1,200페이지까지 지원합니다.")
        total = source.get("pageCount")
        if type(total) is not int or not len(pages) <= total <= MAX_PAGES:
            raise ValueError("원본의 전체 페이지 수를 확인하세요.")
        clean_pages, numbers = [], set()
        for page in pages:
            if not isinstance(page, dict) or type(page.get("page")) is not int or not 1 <= page["page"] <= total or page["page"] in numbers:
                raise ValueError("가이드 페이지 번호가 올바르지 않습니다.")
            numbers.add(page["page"])
            text = _string(page.get("text"), MAX_PAGE_CHARS, "추출 내용", empty=True)
            characters += len(text)
            if characters > MAX_TOTAL_CHARS:
                raise ValueError("가이드 묶음의 텍스트 크기 상한을 초과했습니다.")
            if type(page.get("truncated", False)) is not bool:
                raise ValueError("가이드 추출 범위를 확인하세요.")
            clean_pages.append({"page": page["page"], "text": text, "textSha256": text_hash(text),
                                "truncated": page.get("truncated", False)})
        result.append({"id": identifier, "name": _string(source.get("name"), 180, "파일명"),
                       "sha256": _hash(source.get("sha256")), "category": category,
                       "pageCount": total, "pages": sorted(clean_pages, key=lambda page: page["page"])})
        if "metadata" in source:
            metadata = source["metadata"]
            if not isinstance(metadata, dict):
                raise ValueError("원본 메타데이터 형식을 확인하세요.")
            result[-1]["metadata"] = {key: _string(metadata[key], 1024, "원본 정보", empty=True)
                                     for key in ("path", "sid", "pver", "dver", "sync", "mdate", "status", "imports")
                                     if key in metadata}
    return {"format": FORMAT, "schemaVersion": 1, "sources": result}


def summaries(pack, originals_stored=False):
    return [{key: source[key] for key in ("id", "name", "sha256", "category", "pageCount")} |
            {"extractedPages": len(source["pages"]),
             "nonemptyPages": sum(bool(page["text"].strip()) for page in source["pages"]),
             "truncatedPages": sum(page["truncated"] for page in source["pages"]),
             "originalStatus": "stored" if originals_stored else "local-only", "reviewStatus": "unreviewed",
             **({"metadata": source["metadata"]} if "metadata" in source else {})}
            for source in pack["sources"]]


def normalize_refs(refs, asset_ids):
    if not isinstance(refs, list) or len(refs) > MAX_REFS:
        raise ValueError("AI에 적용할 가이드 페이지는 최대 12개까지 선택하세요.")
    result, seen = [], set()
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("assetId") not in asset_ids:
            raise ValueError("가이드 페이지는 선택한 반입 파일에 속해야 합니다.")
        asset_id, source_id = _id(ref["assetId"]), _id(ref.get("sourceId"))
        page = ref.get("page")
        if type(page) is not int or not 1 <= page <= MAX_PAGES or (asset_id, source_id, page) in seen:
            raise ValueError("가이드 페이지 번호 또는 중복 선택을 확인하세요.")
        seen.add((asset_id, source_id, page))
        result.append({"assetId": asset_id, "sourceId": source_id, "page": page,
                       "sourceSha256": _hash(ref.get("sourceSha256")), "textSha256": _hash(ref.get("textSha256"))})
    return result


def load_pack(storage, owner, asset):
    key = asset.get("guidelinesKey")
    if not key or not storage.owns_key(owner, key):
        raise ValueError("이 파일의 UX 가이드 색인이 없습니다.")
    if storage.blob_info(key)["size"] > MAX_PACK_BYTES:
        raise ValueError("가이드 색인의 크기 상한을 초과했습니다.")
    data = storage.get_blob(key)
    if text_hash(data.decode("utf-8")) != asset.get("guidelinesSha256"):
        raise ValueError("가이드 색인이 반입 시점과 달라졌습니다.")
    return json.loads(data)


def selected_pages(pack, refs):
    sources = {source["id"]: source for source in pack["sources"]}
    result = []
    for ref in refs:
        source = sources.get(ref["sourceId"])
        page = next((page for page in source["pages"] if page["page"] == ref["page"]), None) if source else None
        if not page or source["sha256"] != ref["sourceSha256"] or page["textSha256"] != ref["textSha256"]:
            raise ValueError("선택한 가이드 원본·페이지가 변경되었습니다. 다시 선택하세요.")
        if not page["text"].strip() or page["truncated"]:
            raise ValueError("비어 있거나 잘린 가이드 페이지는 AI 기준으로 선택할 수 없습니다.")
        if source.get("metadata", {}).get("status") in ("삭제", "DEPRECATED", "deleted"):
            raise ValueError("삭제·폐기된 원본은 생성 기준으로 선택할 수 없습니다. 이력 확인용으로만 보관합니다.")
        result.append((source, page))
    return result


def context_for(pack, refs):
    blocks, texts = [], {}
    for source, page in selected_pages(pack, refs):
        texts[(source["id"], page["page"])] = page["text"]
        blocks.append(f"원본 ID: {source['id']}\n원본명: {source['name']}\n분류: {source['category']}\n"
                      f"물리 페이지: {page['page']}\n원본 상태는 승인/실행 근거가 아닙니다.\n"
                      f"원본 메타데이터: {json.dumps(source.get('metadata', {}), ensure_ascii=False)}\n추출 원문:\n{page['text']}")
    return "\n\n".join(blocks), texts


def validate_selection(storage, owner, assets, refs):
    normalized = normalize_refs(refs, [asset["id"] for asset in assets])
    pages, characters = {}, 0
    for asset in assets:
        selected = [ref for ref in normalized if ref["assetId"] == asset["id"]]
        if not selected:
            continue
        _, texts = context_for(load_pack(storage, owner, asset), selected)
        characters += sum(len(text) for text in texts.values())
        pages.update({(asset["id"], source_id, page): text for (source_id, page), text in texts.items()})
    if characters > MAX_CONTEXT_CHARS:
        raise ValueError("선택한 가이드 원문은 합계 60,000자 이내여야 합니다. 적용 범위를 나누세요.")
    return normalized, pages


def validate_citations(contract, pages):
    refs = contract.get("guideRefs", [])
    pack_ids = {ref["assetId"] for ref in refs}
    for rule in contract.get("rules", []):
        source = rule.get("source", {})
        if source.get("kind") != "explicit" or source.get("assetId") not in pack_ids:
            continue
        text = pages.get((source.get("assetId"), source.get("sourceId"), source.get("page")), "")
        quote = source.get("quote", "")
        if not text or not quote.strip() or " ".join(quote.split()) not in " ".join(text.split()):
            raise ValueError("명시된 가이드 근거가 선택한 원본의 해당 페이지에 없습니다.")
