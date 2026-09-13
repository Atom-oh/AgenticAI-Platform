"""Pure source selection and reference validation; no models, storage or tools."""
from __future__ import annotations

import json
import html
import math
import re
import unicodedata
from collections import Counter, deque
from urllib.parse import unquote

MAX_CONTEXT_CHARS = 36_000
MAX_MODEL_SOURCES = 20
MAX_MODEL_OUTPUT_BYTES = 64_000
NODE_IDS = re.compile(r"(?<![A-Za-z0-9_])(?:REG|PRD|SCR|CMP|CND|DOC|TPL|POL|PAT|PRC|SM|D)-[A-Za-z0-9_-]+(?![A-Za-z0-9_-])", re.IGNORECASE | re.ASCII)
EVIDENCE_IDS = re.compile(r"(?<![A-Za-z0-9_])E[0-9]+(?![A-Za-z0-9_])", re.IGNORECASE | re.ASCII)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("Non-finite JSON value")


def parse_answer(text):
    if not isinstance(text, str):
        return {"accepted": False, "error": "invalid-output"}
    try:
        if len(text.encode("utf-8")) > MAX_MODEL_OUTPUT_BYTES:
            return {"accepted": False, "error": "output-limit"}
        text = text.strip()
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if not isinstance(value, dict):
            raise ValueError()
        return {"accepted": True, "value": value}
    except (ValueError, UnicodeError, RecursionError):
        return {"accepted": False, "error": "invalid-json"}


def validate_answer(value, allowed_nodes, evidence_ids, *, context_nodes=()):
    invalid = {"accepted": False, "error": "unbound-output"}
    if not isinstance(value, dict) or set(value) != {"summary", "findings"}:
        return invalid
    summary, findings = value["summary"], value["findings"]
    if (not isinstance(summary, str) or not summary.strip() or len(summary) > 3000
            or not isinstance(findings, list) or len(findings) > 50):
        return invalid
    nodes, evidence = set(allowed_nodes), set(evidence_ids)
    seen = set()
    for row in findings:
        if not isinstance(row, dict) or set(row) != {"nodeId", "reason", "citationIds"}:
            return invalid
        node, reason, citations = row["nodeId"], row["reason"], row["citationIds"]
        if (not isinstance(node, str) or node not in nodes or node in seen
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
                or not isinstance(citations, list) or not 1 <= len(citations) <= 8
                or any(not isinstance(c, str) or c not in evidence for c in citations)
                or len(set(citations)) != len(citations)):
            return invalid
        seen.add(node)
    all_text = _normalize_prose("\n".join([summary, *(row["reason"] for row in findings)]))
    if all_text is None:
        return invalid
    allowed_mentions = nodes | set(context_nodes)
    mentions = set(NODE_IDS.findall(all_text))
    prefixes = {node.split("-", 1)[0] for node in allowed_mentions if isinstance(node, str) and "-" in node}
    if prefixes:
        supplied_ids = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(prefix) for prefix in sorted(prefixes)) +
                                  r")-[A-Za-z0-9_-]+(?![A-Za-z0-9_-])", re.IGNORECASE | re.ASCII)
        mentions.update(supplied_ids.findall(all_text))
    if mentions - allowed_mentions or set(EVIDENCE_IDS.findall(all_text)) - evidence:
        return invalid
    return {"accepted": True, "answer": value}


_LOCATION = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]{1,24}\s*:\s*[/\\]{2}|\b(?:www\.|javascript:|data:|vbscript:)"
    r"|\barn:[a-z0-9-]+:(?:s3|dynamodb|secretsmanager):"
    r"|(?:^|[\s`'\"(])//[^\s/]+"
    r"|\b(?:[a-z0-9-]+\.)+[a-z]{2,63}/[^\s]*"
    r"|\[[^\]\n]{0,200}\]\(\s*[^)\s]+\s*\)"
    r"|(?:^|[\s`'\"(])(?:[a-z]:[\\/]|\\\\[^\s\\]+\\|\.{1,2}[\\/]|/?workspace[\\/]|/[^\s/\\<>]+))",
    re.IGNORECASE,
)
_CLAIMS = re.compile(
    r"(?:(?:검증|검수|검사)(?:[이가은는을를에])?\s*(?:통과|완료|성공|되었|됐|됨)"
    r"|승인(?:\s*(?:절차|처리|검토))?(?:[이가은는])?\s*(?:완료|확정|성공|됨|되었|됐)"
    r"|자동(?:으로)?\s*승인"
    r"|승인\s*(?:했|하였|합니다)"
    r"|승인(?:[이가은는])?\s*(?:입니다|이다|이에요|상태입니다)"
    r"|(?:승인|검증|검수|검사)(?:[이가은는])?\s*된\s*(?:상태|결과|분석|것)"
    r"|수정(?:\s*(?:대상|범위|여부|필요))?(?:[이가은는])?\s*(?:확정|완료|승인)"
    r"|실제\s*(?:은행|금융사)\s*(?:정책|규정|내규)[^.!?\n]{0,20}(?:적용|시행|인증|확정)"
    r"|(?:automatically|auto[- ]?)\s*approved"
    r"|(?:verification|validation|checks?)\s+(?:(?:has|have|had|is|are|was|were|been|now|already|successfully)\s+){0,4}(?:passed|complete|successful)"
    r"|approval\s+(?:(?:has|have|had|is|was|been|now|already)\s+){0,4}(?:complete|granted|confirmed)"
    r"|(?:changes?|analysis|results?|findings?)\s+(?:(?:has|have|had|is|are|was|were|been|now|already)\s+){0,4}(?:confirmed|approved|finalized|verified|validated)"
    r"|\b(?:approved|verified|validated)\b"
    r"|official\s+(?:bank(?:ing)?\s+)?(?:policy|regulation)"
    r"|\b(?:approval|verification|validation)\b|승인|검증)",
    re.IGNORECASE,
)
_NEGATIVE_SUFFIX = re.compile(
    r"^\s*(?:[을를은는이가]\s*)?(?:(?:의미|뜻|선언)(?:하지|하지는)\s*(?:않|못)"
    r"|(?:상태|결과)(?:가|는)?\s*아니|(?:하지|되지|되지는)\s*(?:않|못)|아니|아닙|불가|금지)"
)
_REVIEW_SUFFIX = re.compile(
    r"^\s*여부(?:는|를|가|에)?[^.!?\n]{0,40}(?:확인|검토)(?:해야|하세요|해\s*주세요)"
)


def _normalize_prose(prose):
    """Resolve mixed encoding to a fixed point; excess nesting is not a pass."""
    for _ in range(4):
        normalized = unicodedata.normalize("NFKC", prose)
        normalized = html.unescape(unquote(normalized))
        normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
        if normalized == prose:
            return normalized
        prose = normalized
    return None


def output_policy(value):
    """Reject explicit model-created locations/authority claims, not source quotes.

    This bounded policy does not certify semantic truth; human review remains
    required even for prose that passes it.
    """
    prose = "\n".join([value["summary"], *(row["reason"] for row in value["findings"])])
    normalized = _normalize_prose(prose)
    if normalized is None:
        return {"accepted": False, "violations": ["ambiguous-encoding"]}
    violations = []
    if _LOCATION.search(normalized):
        violations.append("model-location")
    for match in _CLAIMS.finditer(normalized):
        prefix = normalized[max(0, match.start() - 40):match.start()]
        suffix = normalized[match.end():match.end() + 60]
        word = match.group().lower()
        if word in {"approval", "verification", "validation"} and re.match(
                r"^\s+(?:(?:is|are|has|have|been)\s+)*(?:not|never|required|needed|pending|criteria|steps?|process|requests?)\b",
                suffix, re.IGNORECASE):
            continue
        if word in {"승인", "검증"} and (
                re.search(r"(?:미|비|불)$", prefix) or re.match(
                    r"^\s*(?:된\s*(?:원문|문서|자료)|기준|절차|방법|항목|범위|계획|요청|필요|대기|여부|전(?:에|에는|까지|\s|$))",
                    suffix)):
            continue
        if match.group().lower() == "approved" and re.match(r"^\s+(?:sources?|originals?|documents?|references?)\b", suffix, re.IGNORECASE):
            # Approval is an input-source attribute, not an analysis verdict.
            continue
        if (_NEGATIVE_SUFFIX.match(suffix)
                or _REVIEW_SUFFIX.match(suffix)
                or re.search(r"\b(?:not|never|no)(?:\s+(?:claim|imply|mean|declare|all|been|be|being|yet|fully|successfully)){0,5}\s+$", prefix, re.IGNORECASE)):
            continue
        violations.append("automatic-authority")
        break
    return {"accepted": not violations, "violations": violations}


def _tokens(text):
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.casefold())
    return set(words + [word[i:i + 2] for word in words for i in range(len(word) - 1)])


def prompt_evidence(row):
    return {key: row[key] for key in ("id", "title", "revision", "quote", "page", "provenance")}


def select_evidence(sources, query, max_chars=MAX_CONTEXT_CHARS):
    """Round-robin relevant complete paragraphs; never invent/truncate a quote."""
    limit = min(MAX_CONTEXT_CHARS, max(0, max_chars))
    selected = sources[:MAX_MODEL_SOURCES]
    token_sets = [_tokens(p["text"]) for source in selected for p in source.get("paragraphs", [])]
    frequency = Counter(token for tokens in token_sets for token in tokens)
    query_tokens = _tokens(query)
    queues = []
    for source in selected:
        ranked = []
        for index, paragraph in enumerate(source.get("paragraphs", [])):
            text = paragraph.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                continue
            tokens = _tokens(text)
            score = sum(math.log(1 + len(token_sets) / (1 + frequency[t]))
                        for t in tokens & query_tokens)
            ranked.append((-score, index, paragraph))
        ranked.sort(key=lambda item: (item[0], item[1]))
        queues.append((source, deque(p for _, _, p in ranked)))
    evidence, used = [], 0
    while any(paragraphs for _, paragraphs in queues):
        added = False
        for source, paragraphs in queues:
            if not paragraphs:
                continue
            paragraph = paragraphs.popleft()
            row = {
                "id": f"E{len(evidence) + 1}", "documentId": source["documentId"],
                "revisionId": source["revisionId"], "paragraphId": paragraph["id"],
                "title": source["title"], "revision": source["revision"],
                "versionLabel": source.get("versionLabel", ""),
                "originalSha256": source["sha256"], "textHash": source["textHash"],
                "quote": paragraph["text"], "page": paragraph.get("page"),
                "provenance": source.get("provenance", "uploaded"),
            }
            cost = len(canonical(prompt_evidence(row))) + 1
            if used + cost > limit:
                continue
            used += cost
            evidence.append(row)
            added = True
        if not added:
            # Remaining smaller paragraphs can still fit, so finish scanning the
            # bounded queues rather than silently dropping the rest of a source.
            continue
    included = {(row["documentId"], row["paragraphId"]) for row in evidence}
    total = sum(source.get("totalParagraphs", len(source.get("paragraphs", []))) for source in sources)
    return evidence, {
        "contextCharacters": used,
        "sourceDocuments": len({row["documentId"] for row in evidence}),
        "evidenceParagraphs": len(evidence), "availableParagraphs": total,
        "truncated": len(sources) > MAX_MODEL_SOURCES or len(included) < total,
    }
