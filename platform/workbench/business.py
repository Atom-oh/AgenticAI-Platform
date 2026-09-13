"""Synthetic pension cases and source-bound internal reports.

Financial projections are explicit illustrative calculations, not statutory
pension benefits or investment advice. Model prose never supplies numeric facts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

from workspace.collaboration import CollaborationError
from workspace.storage import Conflict

PERSONAS = [
    {"id": "starter", "title": "차근차근 준비하는 직장인", "ageBand": "30대", "stage": "적립 초기",
     "description": "연금 계좌와 적립 계획을 처음 정리하는 합성 페르소나",
     "accounts": [{"id": "sample-dc-a", "name": "합성 퇴직연금 DC", "type": "DC", "balance": 18000000},
                  {"id": "sample-irp-a", "name": "합성 개인형 IRP", "type": "IRP", "balance": 7000000}],
     "defaults": {"yearsToRetirement": 25, "monthlyContribution": 300000, "monthlyTarget": 2000000}},
    {"id": "growing", "title": "준비 상황을 점검하는 직장인", "ageBand": "40대", "stage": "적립 확대",
     "description": "계좌별 준비 수준과 가정에 따른 부족분을 비교하는 합성 페르소나",
     "accounts": [{"id": "sample-dc-b", "name": "합성 퇴직연금 DC", "type": "DC", "balance": 68000000},
                  {"id": "sample-irp-b", "name": "합성 개인형 IRP", "type": "IRP", "balance": 22000000},
                  {"id": "sample-saving-b", "name": "합성 연금저축", "type": "연금저축", "balance": 15000000}],
     "defaults": {"yearsToRetirement": 15, "monthlyContribution": 500000, "monthlyTarget": 2500000}},
    {"id": "retiring", "title": "수령 시나리오를 준비하는 직장인", "ageBand": "50대", "stage": "수령 준비",
     "description": "수령 기간과 생활비 가정을 비교하는 합성 페르소나",
     "accounts": [{"id": "sample-dc-c", "name": "합성 퇴직연금 DC", "type": "DC", "balance": 180000000},
                  {"id": "sample-irp-c", "name": "합성 개인형 IRP", "type": "IRP", "balance": 65000000}],
     "defaults": {"yearsToRetirement": 5, "monthlyContribution": 700000, "monthlyTarget": 3000000}},
]
TOPICS = {
    "diagnosis": "현황 진단", "planning": "노후 설계", "contribution": "적립 계획",
    "operation": "운용 가정", "withdrawal": "수령 시나리오",
}
QUESTIONS = {
    "diagnosis": "현재 연금 준비 상황을 알려주세요.",
    "planning": "은퇴 준비 부족분을 알려주세요.",
    "contribution": "적립액을 바꾸면 어떻게 달라지나요?",
    "operation": "수익률 가정이 결과에 어떤 영향을 주나요?",
    "withdrawal": "수령 기간을 바꾸면 어떻게 달라지나요?",
}
CALCULATION_VERSION = "illustrative-pension-v1"
FEEDBACK_COMMENTS = {
    "clear": "계산 가정과 근거가 명확합니다.",
    "needs-evidence": "근거 설명을 보완해야 합니다.",
    "needs-clarity": "설명을 더 이해하기 쉽게 보완해야 합니다.",
    "incorrect": "계산 또는 답변의 재검토가 필요합니다.",
}
DATASET_VERSION = "synthetic-pension-v1"
REPORT_TYPES = {"change-impact", "pension-evaluation", "management", "underwriting", "regulation"}
METRICS = {
    "totalBalance": "현재 합성 연금 자산", "projectedBalance": "가정에 따른 은퇴 시점 자산",
    "monthlyPension": "단순 월 수령액", "monthlyTarget": "목표 월 생활비",
    "monthlyGap": "목표 대비 월 부족분", "monthlySurplus": "목표 대비 월 여유분",
    "totalContributions": "추가 적립 원금",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _text(value, maximum=1000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not empty and not value.strip()):
        raise CollaborationError(400, "invalid-input", "입력 내용을 확인해 주세요.")
    return value.strip()


def _request_id(body):
    value = _text(body.get("requestId"), 128)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise CollaborationError(400, "invalid-input", "요청 식별자를 확인해 주세요.")
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("계산 가정의 범위와 형식을 확인해 주세요.")
    return value


def calculate(persona, supplied=None):
    supplied = supplied or {}
    defaults = {**persona["defaults"], "withdrawalYears": 20, "annualReturnBps": 0}
    if not isinstance(supplied, dict) or set(supplied) - set(defaults):
        raise ValueError("지원하지 않는 계산 가정입니다.")
    assumptions = {**defaults, **supplied}
    _integer(assumptions["yearsToRetirement"], 0, 40)
    _integer(assumptions["withdrawalYears"], 5, 40)
    _integer(assumptions["monthlyContribution"], 0, 10000000)
    _integer(assumptions["monthlyTarget"], 0, 20000000)
    _integer(assumptions["annualReturnBps"], -500, 1000)
    balance = sum(account["balance"] for account in persona["accounts"])
    months = assumptions["yearsToRetirement"] * 12
    contribution = assumptions["monthlyContribution"]
    with localcontext() as context:
        context.prec = 40
        rate = Decimal(assumptions["annualReturnBps"]) / Decimal(120000)
        growth = (1 + rate) ** months
        accumulated = (Decimal(balance) * growth + Decimal(contribution) *
                       ((growth - 1) / rate if rate else Decimal(months)))
        projected = int(accumulated.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        monthly = int((Decimal(projected) / (assumptions["withdrawalYears"] * 12))
                      .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    target = assumptions["monthlyTarget"]
    return {
        "totalBalance": balance, "projectedBalance": projected, "monthlyPension": monthly,
        "monthlyTarget": target, "monthlyGap": max(target - monthly, 0),
        "monthlySurplus": max(monthly - target, 0), "totalContributions": contribution * months,
        "assumptions": assumptions, "calculationVersion": CALCULATION_VERSION,
        "formula": "현재 자산의 월 복리 가정 + 매월 말 적립액의 누적 가정; 수령액은 수령 개월 수로 단순 나눔",
        "caveats": ["합성 자료와 사용자가 정한 가정에 따른 검증용 계산입니다.",
                    "국민연금·세금·수수료·물가·수령 중 운용 수익은 포함하지 않습니다.",
                    "미래 수익과 실제 연금 수령액을 보장하지 않습니다."],
    }


def _persona(identifier):
    for value in PERSONAS:
        if value["id"] == identifier:
            return copy.deepcopy(value)
    raise CollaborationError(400, "invalid-persona", "제공된 합성 페르소나를 선택해 주세요.")


def _get(api, scope, kind, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identifier):
        raise CollaborationError(404, "not-found", "자료를 찾을 수 없습니다.")
    item = api.storage.get(scope["owner"], kind, identifier)
    if not item:
        raise CollaborationError(404, "not-found", "현재 작업 공간의 자료를 찾을 수 없습니다.")
    return item


def _commit(api, scope, writes, action="read", checks=()):
    api.collaboration.require(scope, action)
    if checks:
        from workspace.storage import Conflict
        if scope.get("project"):
            writes = [*writes, _write(scope, "project", scope["project"], scope["project"]["version"])]
        try:
            result = api.storage.put_many(writes, checks=list(checks))
            return result[:-1] if scope.get("project") else result
        except Conflict as error:
            raise CollaborationError(409, "source-changed", "원본 또는 프로젝트가 변경되었습니다.") from error
    if scope.get("project"):
        # Preserve the fence from before evidence reads. Refreshing it here could
        # approve a report after another writer changed its underlying evidence.
        return api.collaboration._commit(scope, writes)
    return api.storage.put_many(writes)


def _write(scope, kind, item, version=None):
    return {"owner": scope["owner"], "kind": kind, "item": item, "expected_version": version}


def _public(item):
    return {key: value for key, value in item.items() if key not in {"contentKey", "requestHash"}}


def _list(api, scope, kind):
    items, cursor = [], None
    for _ in range(20):
        page = api.storage.list_page(scope["owner"], kind, 100, cursor)
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            return items
    raise CollaborationError(413, "too-large", "자료 범위를 나누어 주세요.")


def _require_version(item, value):
    if type(value) is not int or value != item["version"]:
        raise CollaborationError(409, "conflict", "자료가 변경되었습니다. 다시 조회해 주세요.")


def _insights(facts):
    return [
        {"id": "diagnosis", "title": "내 연금 준비 상황", "metricId": "totalBalance",
         "value": facts["totalBalance"], "question": QUESTIONS["diagnosis"]},
        {"id": "planning", "title": "목표 생활비와 비교", "metricId": "monthlyGap",
         "value": facts["monthlyGap"], "question": QUESTIONS["planning"]},
        {"id": "withdrawal", "title": "수령 시나리오 확인", "metricId": "monthlyPension",
         "value": facts["monthlyPension"], "question": QUESTIONS["withdrawal"]},
    ]


def _create_session(api, scope, body):
    request_id = _request_id(body)
    persona = _persona(body.get("personaId"))
    facts = calculate(persona, body.get("assumptions"))
    fingerprint = _hash({"personaId": persona["id"], "assumptions": facts["assumptions"]})
    identifier = "pension-" + _hash([scope["owner"], request_id])[:32]
    existing = api.storage.get(scope["owner"], "wb_pension", identifier)
    if existing:
        if existing.get("requestHash") != fingerprint:
            raise CollaborationError(409, "request-changed", "같은 요청에 다른 입력이 전달되었습니다.")
        return 200, {"session": _public(existing)}
    item = {"id": identifier, "title": persona["title"], "persona": persona, "facts": facts,
            "factHash": _hash(facts), "datasetVersion": DATASET_VERSION, "synthetic": True,
            "status": "ready", "answers": [], "feedback": [], "insights": _insights(facts),
            "createdBy": scope["actor"], "projectId": (scope.get("project") or {}).get("id"),
            "requestHash": fingerprint}
    saved = _commit(api, scope, [_write(scope, "wb_pension", item)])[0]
    return 201, {"session": _public(saved)}


def _baseline(session, topic):
    facts = session["facts"]
    opening = {
        "diagnosis": f"합성 연금 자산은 {facts['totalBalance']:,}원입니다. 계좌 구성과 적립 가정을 함께 확인하세요.",
        "planning": f"가정에 따른 월 수령액은 {facts['monthlyPension']:,}원이며, 목표 대비 월 부족분은 {facts['monthlyGap']:,}원입니다.",
        "contribution": f"선택한 기간 동안 추가 적립 원금은 {facts['totalContributions']:,}원입니다. 적립액 가정을 바꾸어 결과를 비교할 수 있습니다.",
        "operation": f"선택한 수익률 가정에 따른 은퇴 시점 자산은 {facts['projectedBalance']:,}원입니다. 수익률은 확정 수익이 아닌 계산 가정입니다.",
        "withdrawal": f"은퇴 시점 자산을 선택한 수령 기간으로 단순 나눈 월 금액은 {facts['monthlyPension']:,}원입니다. 실제 수령 조건은 별도 확인이 필요합니다.",
    }
    return opening[topic] + "\n\n" + " ".join(facts["caveats"])


def _answer(api, scope, session, topic, question, text, mode, extra=None):
    identifier = "answer-" + _hash([session["id"], session["factHash"], topic, question, mode, text])[:24]
    answer = {"id": identifier, "topic": topic, "question": question, "text": text, "mode": mode,
              "modeLabel": "계산 근거 안내" if mode == "baseline" else "모델 선택 지표·서버 계산",
              "factHash": session["factHash"], "calculationVersion": CALCULATION_VERSION,
              "metricIds": list(METRICS), "stale": False, **(extra or {})}
    answers = [*session.get("answers", []), answer][-20:]
    updated = _commit(api, scope, [_write(scope, "wb_pension",
                        {**session, "answers": answers}, session["version"])])[0]
    return answer, updated


def render_model_answer(raw, facts):
    """Only server-owned numeric placeholders may become visible financial values."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    result = json.loads(cleaned)
    if not isinstance(result, dict) or set(result) - {"text", "metricIds"}:
        raise ValueError("모델 출력 형식이 검증 기준과 다릅니다.")
    text, identifiers = result.get("text"), result.get("metricIds")
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or not isinstance(identifiers, list):
        raise ValueError("모델 설명에 필요한 근거가 없습니다.")
    used = re.findall(r"\{\{([A-Za-z][A-Za-z0-9]*)\}\}", text)
    if not used or set(identifiers) != set(used) or any(key not in METRICS or key not in facts for key in used):
        raise ValueError("모델이 알 수 없는 계산 항목을 인용했습니다.")
    remaining = re.sub(r"\{\{[A-Za-z][A-Za-z0-9]*\}\}", "", text)
    spelled_amount = r"(?<![가-힣])(?:[영공일이삼사오육칠팔구십백천만억조]+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(?:원|배|퍼센트|년|개월)"
    if re.search(spelled_amount, remaining):
        raise ValueError("모델이 자리표시자 밖의 수량을 생성했습니다.")
    if re.search(r"\d|[{}]", remaining):
        raise ValueError("모델이 검증되지 않은 숫자를 생성했습니다.")
    # The model selects relevant metric IDs; it cannot author their labels,
    # signs, currency or surrounding financial claims. Even valid placeholders
    # embedded in misleading prose become canonical, server-owned statements.
    return "\n".join(f"{METRICS[key]}: {facts[key]:,}원" for key in dict.fromkeys(used)) + (
        "\n\n가정에 따른 합성 계산입니다. 세금·물가·수수료·국민연금은 포함하지 않습니다.")


def _queue_question(api, scope, claims, session, body, topic):
    from workspace.http import HTTPError
    api._worker_ready()
    expiry = claims.get("exp")
    if type(expiry) not in (int, str) or not str(expiry).isdigit() or int(expiry) * 1000 <= api.storage.clock():
        raise HTTPError(401, "authorization-expired", "유효한 인증으로 다시 요청하세요.")
    question = _text(body.get("question"), 500)
    privacy_evidence = {"processor": "server-synthetic-question", "modelInvoked": False}
    if question not in QUESTIONS.values():
        from common import privacy
        try:
            result = privacy.process(question, "qwen", "query", "pension-" + session["id"][-24:])
        except privacy.PrivacyUnavailable:
            raise HTTPError(503, "privacy-unavailable", "자유 입력의 개인정보 처리가 준비되지 않았습니다. 예제 질문을 선택하거나 연결 상태를 확인하세요.")
        question, privacy_evidence = result["text"], result["evidence"]
    from engine import model_catalog
    model = model_catalog.resolve(body.get("model"))
    payload = {"operation": "pension_ask", "sessionId": session["id"], "factHash": session["factHash"],
               "question": question, "topic": topic, "model": model, "actor": scope["actor"],
               "projectId": (scope.get("project") or {}).get("id"), "privacy": privacy_evidence,
               "authorizationExpiresAt": int(expiry) * 1000}
    fingerprint = _hash(payload)
    identifier = "pension-answer-" + fingerprint[:32]
    job = api._new_job(scope["owner"], identifier, "workbench", payload, fingerprint)
    if job["status"] == "queued":
        api._invoke(scope["owner"], job)
    return 202, {"job": job, "session": _public(session)}


def _pension(api, scope, claims, method, parts, body):
    if parts == ["pension", "personas"] and method == "GET":
        return 200, {"personas": copy.deepcopy(PERSONAS), "topics": TOPICS,
                     "questions": QUESTIONS, "datasetVersion": DATASET_VERSION, "synthetic": True,
                     "feedbackComments": FEEDBACK_COMMENTS,
                     "feedbackFreeTextAvailable": bool(os.environ.get("MYDATA_PRIVACY_FUNCTION_ARN"))}
    if parts == ["pension", "sessions"] and method == "POST":
        return _create_session(api, scope, body)
    if len(parts) not in (3, 4) or parts[:2] != ["pension", "sessions"]:
        return None
    session = _get(api, scope, "wb_pension", parts[2])
    if len(parts) == 3 and method == "GET":
        return 200, {"session": _public(session)}
    if method != "POST" or len(parts) != 4:
        return None
    if parts[3] == "calculate":
        _require_version(session, body.get("version"))
        facts = calculate(session["persona"], {**session["facts"]["assumptions"], **body.get("assumptions", {})})
        answers = [{**answer, "stale": answer.get("factHash") != _hash(facts)}
                   for answer in session.get("answers", [])]
        updated = _commit(api, scope, [_write(scope, "wb_pension", {
            **session, "facts": facts, "factHash": _hash(facts), "insights": _insights(facts),
            "answers": answers}, session["version"])])[0]
        return 200, {"session": _public(updated)}
    if parts[3] == "ask":
        _require_version(session, body.get("version"))
        topic = body.get("topic", "diagnosis")
        if topic not in TOPICS:
            raise CollaborationError(400, "invalid-topic", "상담 주제를 선택해 주세요.")
        question = _text(body.get("question") or QUESTIONS[topic], 500)
        mode = body.get("mode", "baseline")
        if mode == "model":
            return _queue_question(api, scope, claims, session, {**body, "question": question}, topic)
        if mode != "baseline":
            raise CollaborationError(400, "invalid-mode", "지원하는 설명 방식을 선택해 주세요.")
        # Baseline does not retain arbitrary free-text input or claim interpretation.
        answer, updated = _answer(api, scope, session, topic, QUESTIONS[topic],
                                  _baseline(session, topic), "baseline")
        return 200, {"answer": answer, "session": _public(updated)}
    if parts[3] == "feedback":
        rating = body.get("rating")
        if type(rating) is not int or not 1 <= rating <= 5:
            raise CollaborationError(400, "invalid-rating", "평가는 1점부터 5점까지 선택해 주세요.")
        answer_id = body.get("answerId")
        answer = next((a for a in session.get("answers", []) if a["id"] == answer_id), None)
        if not answer:
            raise CollaborationError(400, "answer-required", "실제 답변을 선택한 뒤 평가해 주세요.")
        comment = _text(body.get("comment", ""), 1000, True)
        code = body.get("commentCode", "")
        if code and code not in FEEDBACK_COMMENTS:
            raise CollaborationError(400, "invalid-feedback", "지원하는 평가 의견을 선택하세요.")
        privacy_receipt = None
        if comment:
            from common import privacy
            try:
                cleaned = privacy.process(comment, "qwen", "query", "feedback-" + session["id"][-24:])
            except privacy.PrivacyUnavailable:
                raise CollaborationError(503, "privacy-unavailable",
                                         "자유 의견의 개인정보 처리가 준비되지 않았습니다. 선택 의견이나 평점만 저장하세요.")
            comment, privacy_receipt = cleaned["text"], cleaned["evidence"]
        elif code:
            comment = FEEDBACK_COMMENTS[code]
        feedback = {"id": "feedback-" + _hash([scope["actor"], answer_id])[:24], "answerId": answer_id,
                    "rating": rating, "comment": comment, "actor": scope["actor"],
                    "factHash": answer["factHash"], "mode": answer["mode"],
                    "commentCode": code, "commentMode": "private-processed" if privacy_receipt else "structured",
                    "privacy": privacy_receipt}
        values = [f for f in session.get("feedback", []) if f["id"] != feedback["id"]] + [feedback]
        _commit(api, scope, [_write(scope, "wb_pension", {**session, "feedback": values[-100:]},
                                   session["version"])])
        return 200, {"feedback": feedback}
    return None


def _literal(text):
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", str(text)).replace("\x00", "")


def _report_sources(api, scope, claims, body):
    references, sections, unresolved = [], [], []
    if body.get("sessionId"):
        session = _get(api, scope, "wb_pension", body["sessionId"])
        references.append({"kind": "wb_pension", "id": session["id"], "version": session["version"],
                           "factHash": session["factHash"]})
        sections += ["## 연금 합성 시나리오", f"페르소나: {_literal(session['title'])}",
                     "합성 데이터와 명시된 가정의 검증 결과이며 실제 고객 연금 평가가 아닙니다.",
                     "| 계산 항목 | 값 |", "|---|---:|"]
        sections += [f"| {label} | {session['facts'][key]:,}원 |" for key, label in METRICS.items()]
        sections += ["", f"계산 버전: `{CALCULATION_VERSION}`",
                     f"가정: `{json.dumps(session['facts']['assumptions'], ensure_ascii=False)}`",
                     " ".join(session["facts"]["caveats"]), "", "### 직원 평가",
                     f"기록된 답변 {len(session.get('answers', []))}건, 평가 {len(session.get('feedback', []))}건."]
        ratings = [f["rating"] for f in session.get("feedback", [])]
        if ratings:
            sections.append(f"평균 점수: {sum(ratings) / len(ratings):.2f} / 5")
        else:
            sections.append("직원 평가 미수집.")
    if body.get("changeId"):
        change = _get(api, scope, "wb_change", body["changeId"])
        from workbench.service import Service
        from workbench import impact, knowledge
        ctx = Service(api, scope, claims)
        knowledge.authorize_refs(ctx, change.get("sourceRefs", []))
        current_impact = impact.read_impact(ctx, change["id"]) if change.get("impactHash") else None
        references.append({"kind": "wb_change", "id": change["id"], "version": change["version"],
                           "impactHash": change.get("impactHash"), "generation": change.get("generation"),
                           "sourceRefs": copy.deepcopy(change.get("sourceRefs", []))})
        tasks = [t for t in _list(api, scope, "wb_task") if current_impact
                 and t.get("changeId") == change["id"] and t.get("impactHash") == change["impactHash"]]
        for task in tasks:
            knowledge.verify_refs(ctx, task.get("sourceRefs", []))
            references.append({"kind": "wb_task", "id": task["id"], "version": task["version"],
                               "changeId": change["id"], "impactHash": task["impactHash"],
                               "sourceRefs": copy.deepcopy(task.get("sourceRefs", []))})
        sections += ["## 변경 영향", f"변경: {_literal(change.get('title', change['id']))}",
                     f"사유: {_literal(change.get('reason', '미기록'))}",
                     "| 대상 | 담당 역할 | 상태 |", "|---|---|---|"]
        sections += [f"| {_literal(t.get('title', t.get('targetId', t['id'])))} | "
                     f"{_literal(t.get('role', '미지정'))} | {_literal(t.get('status', 'unknown'))} |" for t in tasks]
        if not tasks:
            sections.append("영향 작업이 없거나 아직 분석되지 않았습니다. 이 결과만으로 영향 없음이 확인되지는 않습니다.")
        if current_impact:
            sections += ["### 분석 범위",
                         "등록된 자료와 관계의 영향 후보입니다. 고객 전체 시스템의 영향 누락이 없음을 의미하지 않습니다.",
                         f"분석 해시: `{change['impactHash']}`"]
            if any(item.get("confidence") == "unknown" for item in current_impact["items"]):
                unresolved.append("매핑되지 않은 영향 대상을 확인해야 합니다.")
        if not change.get("impactHash") and not change.get("impact"):
            unresolved.append("변경 영향 분석 근거를 확인해야 합니다.")
    evidence_ids = body.get("evidenceIds", [])
    if not isinstance(evidence_ids, list) or len(evidence_ids) > 10:
        raise CollaborationError(400, "invalid-evidence", "근거 자료는 최대 10개까지 선택해 주세요.")
    for identifier in evidence_ids:
        from workbench.api import route as core_route
        _, payload = core_route(api, scope, claims, "GET", ["knowledge", identifier], {}, {})
        document = payload.get("document") or {}
        content = document.get("content", document.get("text", ""))
        if not isinstance(content, str) or not content.strip():
            unresolved.append("선택한 근거의 본문이 준비되지 않았습니다.")
            continue
        references.append({"kind": "knowledge", "id": identifier, "evidence": payload["evidence"],
                           "contentHash": hashlib.sha256(content.encode()).hexdigest()})
        sections += ["## 문서 근거", f"자료: {_literal(document.get('title', identifier))}",
                     f"근거 ID: `{_literal(identifier)}`", _literal(content[:6000])]
    if not references:
        unresolved.append("검토할 변경 요청, 합성 연금 시나리오 또는 게시 지식 자료를 선택해야 합니다.")
    return references, sections, unresolved


def _create_report(api, scope, claims, body):
    request_id = _request_id(body)
    kind = body.get("type")
    if kind not in REPORT_TYPES:
        raise CollaborationError(400, "invalid-report", "보고서 유형을 선택해 주세요.")
    title = _text(body.get("title", "내부 검토 보고서"), 160).replace("\n", " ")
    identifier = "report-" + _hash([scope["owner"], request_id])[:32]
    fingerprint = _hash({k: body.get(k) for k in ("type", "title", "sessionId", "changeId", "evidenceIds")})
    existing = api.storage.get(scope["owner"], "wb_report", identifier)
    if existing:
        if existing.get("requestHash") != fingerprint:
            raise CollaborationError(409, "request-changed", "같은 요청에 다른 보고서 조건이 전달되었습니다.")
        if not can_read_report(api, scope, claims, existing):
            raise CollaborationError(403, "source-forbidden", "보고서 원본을 읽을 현재 권한이 없습니다.")
        return 200, {"report": _public(existing)}
    references, sections, unresolved = _report_sources(api, scope, claims, body)
    if len(references) > 90:
        raise CollaborationError(422, "report-limit", "보고서의 검증 근거를 나누어 생성하세요.")
    markdown = "\n\n".join([f"# {_literal(title)}", "상태: 검토 초안",
                            f"보고서 유형: `{kind}`",
                            "이 문서는 아래에 기록한 자료만 사용하는 근거 템플릿입니다. 미제공 재무자료·신용판단·규정 해석을 생성하지 않습니다.",
                            *sections, "## 검토 사항",
                            *[f"- {_literal(x)}" for x in unresolved],
                            "## 출처와 버전",
                            *[f"- `{ref['kind']}:{_literal(ref['id'])}` · 버전/해시 `{ref.get('version', ref.get('contentHash', ''))}`"
                              for ref in references]]) + "\n"
    raw = markdown.encode()
    key = api.storage.key_for(scope["owner"], "wb_report", identifier, "document.md")
    api.storage.put_blob_once(key, raw, "text/markdown; charset=utf-8")
    record = {"id": identifier, "title": title, "type": kind, "status": "draft",
              "projectId": (scope.get("project") or {}).get("id"),
              "mode": "evidence-template", "contentHash": hashlib.sha256(raw).hexdigest(),
              "contentKey": key, "sourceRefs": references, "unresolved": unresolved,
              "createdBy": scope["actor"], "requestHash": fingerprint,
              "validation": {"status": "pass" if not unresolved else "incomplete",
                             "sourceCount": len(references), "generatedNumericClaims": False}}
    saved = _commit(api, scope, [_write(scope, "wb_report", record)])[0]
    return 201, {"report": _public(saved)}


def _validate_report_sources(api, scope, claims, report, exact=True):
    checks = {}
    for reference in report["sourceRefs"]:
        if reference["kind"] == "knowledge":
            from workbench.api import route as core_route
            _, value = core_route(api, scope, claims, "GET", ["knowledge", reference["id"]], {}, {})
            document = value.get("document") or {}
            text = document.get("content", document.get("text", ""))
            if exact and hashlib.sha256(text.encode()).hexdigest() != reference["contentHash"]:
                raise CollaborationError(409, "source-changed", "보고서 원본 자료가 변경되었습니다.")
            if exact:
                from workbench.service import Service
                from workbench.knowledge import verify_refs
                if value.get("evidence") != reference.get("evidence"):
                    raise CollaborationError(409, "source-changed", "보고서 원본 버전 또는 권한이 변경되었습니다.")
                for check in verify_refs(Service(api, scope, claims), [value["evidence"]]):
                    checks[(check["kind"], check["id"])] = check
        else:
            current = _get(api, scope, reference["kind"], reference["id"])
            if reference["kind"] in {"wb_change", "wb_task"}:
                from workbench.service import Service
                from workbench import knowledge, impact
                ctx = Service(api, scope, claims)
                # Historical output retains its own bindings. Current read access
                # must hold for both those bindings and the current record.
                knowledge.authorize_refs(ctx, reference.get("sourceRefs", []))
                knowledge.authorize_refs(ctx, current.get("sourceRefs", []))
                if exact:
                    if current.get("sourceRefs", []) != reference.get("sourceRefs", []):
                        raise CollaborationError(409, "source-changed", "간접 원본 근거가 변경되었습니다.")
                    if current.get("impactHash") != reference.get("impactHash"):
                        raise CollaborationError(409, "source-changed", "영향 분석 버전이 변경되었습니다.")
                    change_id = current["id"] if reference["kind"] == "wb_change" else current["changeId"]
                    if current.get("impactHash"):
                        analyzed = impact.read_impact(ctx, change_id)
                        if analyzed["impactHash"] != reference.get("impactHash"):
                            raise CollaborationError(409, "source-changed", "작업의 영향 분석이 변경되었습니다.")
                        manifest = api.storage.get(scope["owner"], "wb_index", "current")
                        if manifest:
                            check = ctx.check("wb_index", manifest)
                            checks[(check["kind"], check["id"])] = check
                    for check in knowledge.verify_refs(ctx, reference.get("sourceRefs", [])):
                        checks[(check["kind"], check["id"])] = check
            if exact and current["version"] != reference["version"]:
                raise CollaborationError(409, "source-changed", "보고서 원본 자료가 변경되었습니다.")
            checks[(reference["kind"], reference["id"])] = {
                "owner": scope["owner"], "kind": reference["kind"], "id": reference["id"],
                "version": current["version"]}
    return list(checks.values())


def can_read_report(api, scope, claims, report):
    try:
        _validate_report_sources(api, scope, claims, report, exact=False)
        return True
    except CollaborationError as error:
        if error.status in (400, 403, 404, 409):
            return False
        raise


def route(api, scope, claims, method, parts, body, query):
    scope = api.collaboration.require(scope, "read")
    if parts and parts[0] == "pension":
        return _pension(api, scope, claims, method, parts, body)
    if parts == ["reports"] and method == "GET":
        return 200, {"items": [_public(item) for item in _list(api, scope, "wb_report")
                               if can_read_report(api, scope, claims, item)]}
    if parts == ["reports"] and method == "POST":
        return _create_report(api, scope, claims, body)
    if len(parts) == 3 and parts[0] == "reports":
        report = _get(api, scope, "wb_report", parts[1])
        if parts[2] == "document" and method == "GET":
            # A saved report cannot bypass a later source permission revocation.
            _validate_report_sources(api, scope, claims, report, exact=False)
            raw = api.storage.get_blob(report["contentKey"])
            if hashlib.sha256(raw).hexdigest() != report["contentHash"]:
                raise CollaborationError(409, "content-changed", "보고서 파일의 무결성을 확인할 수 없습니다.")
            _validate_report_sources(api, scope, claims, report, exact=False)
            api.collaboration.require(scope, "read")
            return 200, {"markdown": raw.decode(), "contentHash": report["contentHash"], "status": report["status"]}
        if parts[2] == "approve" and method == "POST":
            api.collaboration.require(scope, "publish")
            _require_version(report, body.get("version"))
            if body.get("contentHash") != report["contentHash"] or report.get("unresolved"):
                raise CollaborationError(409, "approval-evidence-required", "현재 보고서와 검증 근거가 일치해야 합니다.")
            checks = _validate_report_sources(api, scope, claims, report)
            updated = _commit(api, scope, [_write(scope, "wb_report", {
                **report, "status": "approved", "approval": {"actor": scope["actor"],
                "contentHash": report["contentHash"], "sourceRefs": report["sourceRefs"]}}, report["version"])],
                action="publish", checks=checks)[0]
            return 200, {"report": _public(updated)}
    return None


def process(worker, owner, job):
    """The shared worker delegates business operations after its normal job claim."""
    data = job["input"]
    if data.get("operation") != "pension_ask":
        raise ValueError("지원하지 않는 업무 작업입니다.")
    if type(data.get("authorizationExpiresAt")) is not int or data["authorizationExpiresAt"] <= worker.storage.clock():
        raise ValueError("작업 인증이 만료되었습니다.")
    from workspace.collaboration import Collaboration
    from workspace.http import WorkspaceAPI
    from common import pii
    collaboration = Collaboration(worker.storage)
    scope = collaboration.resolve_scope(data["actor"], data.get("projectId"))
    if scope["owner"] != owner:
        raise ValueError("작업 공간이 일치하지 않습니다.")
    api = WorkspaceAPI(storage=worker.storage, collaboration=collaboration)
    session = _get(api, scope, "wb_pension", data["sessionId"])
    if session["factHash"] != data["factHash"]:
        raise ValueError("계산 가정이 변경되었습니다. 최신 조건으로 다시 요청해 주세요.")
    safe_facts = {key: session["facts"][key] for key in METRICS}
    system = (
        "Select the relevant synthetic pension metrics for the user's question. "
        "Return only JSON {text,metricIds}. text contains one known metric placeholder "
        "per line, such as {{monthlyGap}}. Do not author financial claims, labels, signs "
        "or currency: the server renders those. metricIds lists the placeholders used. "
        "Do not invent metrics or include personal identifiers."
    )
    user = json.dumps({"topic": TOPICS[data["topic"]], "question": data["question"], "facts": safe_facts,
                       "labels": METRICS, "gapDirection": "shortage" if safe_facts["monthlyGap"] else "no-shortage"},
                      ensure_ascii=False)
    raw, usage, info = worker.model_call(system, user, [], data["model"], 2000, job["id"], "workbench.pension")
    text = render_model_answer(raw, safe_facts)
    scan = pii.scan_outbound(text, strict=True)
    if scan.get("count"):
        raise ValueError("생성된 설명에 식별자 검사가 필요해 표시를 중단했습니다.")
    if data["authorizationExpiresAt"] <= worker.storage.clock():
        raise ValueError("답변 검증 중 작업 인증이 만료되었습니다.")
    current = _get(api, scope, "wb_pension", session["id"])
    if current["factHash"] != data["factHash"]:
        raise ValueError("답변 생성 중 계산 가정이 변경되었습니다.")
    answer, saved = _answer(api, scope, current, data["topic"], data["question"], text, "model",
                            {"model": data["model"], "modelInfo": info, "usage": usage,
                             "privacy": data.get("privacy"), "validation": {"numbers": "server-substitution",
                             "labelsSignsUnits": "server-owned", "modelRole": "metric-selection",
                             "pii": "pass", "displayedAfterValidation": True}})
    return {"answer": answer, "sessionId": saved["id"]}
