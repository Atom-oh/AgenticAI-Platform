"""온프렘 플레인 서비스 (SPEC §3.1) — NAT 없는 격리 서브넷의 ECS Fargate에서 실행.

담당: 정확 조회 · 결정론적 계산 · 마스킹/재식별 · 감사 원문 보관.
프롬프트 원문은 이 플레인 밖으로 나가지 않는다 — 클라우드에는 메트릭과 traceId만 (§12.3).

의존성 없는 표준 라이브러리 HTTP 서버 — 컨테이너는 인터넷 접근이 없다.
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from calc_engine import jeonse_loan_limit, preferential_rate, verify_no_generated_numbers
from masking import mask, unmask
from personal_store import exact_lookup

# 감사 원문 저장 (온프렘 플레인 내부 보관 — 데모는 컨테이너 로컬 파일)
AUDIT_PATH = os.environ.get("AUDIT_PATH", "/tmp/onprem-audit.jsonl")


def _audit(rec: dict) -> None:
    rec["ts"] = int(time.time() * 1000)
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def handle(path: str, body: dict) -> tuple[int, dict]:
    if path == "/health":
        return 200, {"ok": True, "plane": "onprem", "audit": AUDIT_PATH}

    if path == "/s2/prepare":
        # 정확 조회 → 계산 → 마스킹까지 온프렘에서 일괄 수행.
        # 반환: 화면 표시용 원본 값(플랫폼 UI는 신뢰 경계 내부), 계산 내역,
        #        경계 통과용 마스킹 페이로드, 재식별 매핑은 반환하지 않고 보관한다.
        email = body.get("email", "")
        query = body.get("query", "")[:500]
        profile = exact_lookup(email)
        rate = preferential_rate(profile, profile["product"]["baseRate"])
        limit = jeonse_loan_limit(profile["jeonse"]["depositKrw"],
                                  profile["jeonse"]["guaranteeRatio"],
                                  profile["jeonse"]["annualIncomeKrw"],
                                  profile["jeonse"]["existingDebtKrw"])
        prompt = (f"고객 세그먼트: {profile['segment']}\n"
                  f"고객 식별: {profile['customerId']} / 계좌 {profile['account']['accountId']}\n"
                  f"상품: {profile['product']['name']}\n"
                  f"[계산엔진 확정값 — 이 숫자만 사용할 것]\n"
                  f"- 적용금리: {rate.value}% (기본 {profile['product']['baseRate']}%)\n"
                  f"- 우대 판정: " + "; ".join(f"{s.label}→{s.value}" for s in rate.steps[1:-1]) + "\n"
                  f"- 대출 가능 한도: {int(limit.value):,}원\n\n질문: {query}")
        m = mask(prompt, {"customerName": profile["name"]})
        trace_id = body.get("traceId", "")
        # 재식별 매핑과 프롬프트 원문은 온프렘에만 보관
        _audit({"traceId": trace_id, "kind": "s2.prompt", "email": email,
                "prompt": prompt, "mapping": m.mapping})
        raw_values = {
            "고객": f"{profile['name']} ({profile['customerId']}, {profile['segment']})",
            "상품": f"{profile['product']['name']} · 기본금리 {profile['product']['baseRate']}%",
            "급여이체": f"{profile['salaryTransferMonths']}개월 연속",
            "전월 카드사용": f"{profile['cardMonthlyKrw']:,}원",
            "자동이체": f"{profile['autoTransferCount']}건",
            "생애최초/신혼": f"{profile['isFirstHome']}/{profile['isNewlywed']}",
            "임차보증금": f"{profile['jeonse']['depositKrw']:,}원",
            "연소득": f"{profile['jeonse']['annualIncomeKrw']:,}원",
            "기존대출": f"{profile['jeonse']['existingDebtKrw']:,}원",
        }
        allowed = ([str(rate.value), f"{int(limit.value):,}", str(int(limit.value)),
                    profile["product"]["baseRate"], f"{profile['cardMonthlyKrw']:,}",
                    f"{profile['jeonse']['depositKrw']:,}", f"{profile['jeonse']['annualIncomeKrw']:,}",
                    f"{profile['jeonse']['existingDebtKrw']:,}"]
                   + [s.value for s in rate.steps] + [s.value for s in limit.steps]
                   + [s.formula for s in rate.steps] + [s.formula for s in limit.steps])
        return 200, {"rawValues": raw_values, "rate": rate.to_dict(), "limit": limit.to_dict(),
                     "maskedPayload": m.text,
                     "maskedFields": m.masked_fields,
                     "allowedNumbers": [a.replace("%", "").replace("원", "").replace("%p", "")
                                        for a in allowed],
                     "dataSource": os.environ.get("DATA_SOURCE", "container-synthetic")}

    if path == "/s2/finalize":
        # 재식별 + 수치 검증 (매핑은 감사 원문에서 traceId로 복원 — 경계를 넘지 않음)
        trace_id = body.get("traceId", "")
        answer = body.get("answer", "")
        allowed = body.get("allowedNumbers", [])
        mapping = {}
        try:
            with open(AUDIT_PATH, encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    if r.get("traceId") == trace_id and r.get("kind") == "s2.prompt":
                        mapping = r.get("mapping", {})
        except FileNotFoundError:
            pass
        invented = verify_no_generated_numbers(answer, allowed)
        _audit({"traceId": trace_id, "kind": "s2.answer", "answer": answer,
                "invented": invented})
        return 200, {"unmasked": unmask(answer, mapping), "inventedNumbers": invented}

    if path == "/audit/recent":
        rows = []
        try:
            with open(AUDIT_PATH, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f][-20:]
        except FileNotFoundError:
            pass
        # 원문은 요약만 노출 (원문 자체는 플레인 내부 보관 증빙용)
        return 200, {"count": len(rows), "items": [
            {"traceId": r.get("traceId"), "kind": r.get("kind"), "ts": r.get("ts"),
             "promptChars": len(r.get("prompt", "") or ""),
             "answerChars": len(r.get("answer", "") or "")} for r in rows]}

    return 404, {"error": f"no route: {path}"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 프롬프트 원문이 액세스 로그로 새지 않게 기본 로그 최소화
        pass

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(*handle(self.path, {}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"}); return
        self._send(*handle(self.path, body))


if __name__ == "__main__":
    if os.environ.get("DATA_BACKEND") == "rds":
        from personal_store import ensure_rds_seed
        for attempt in range(30):  # RDS 기동 대기
            try:
                ensure_rds_seed(); print("rds seed OK", flush=True); break
            except Exception as e:
                print(f"rds seed retry {attempt}: {e}", flush=True); time.sleep(10)
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
