from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

from graph.store import LocalGraphStore  # noqa: E402
from studio import loop, spec as specmod  # noqa: E402

STORE = LocalGraphStore.from_seed_dir(ROOT / "seed" / "out")
SPEC = specmod.build_spec(STORE, "PRD-DEP-001", "ux-flow")
LLM_IDS = [i["id"] for i in SPEC["items"] if i["check"] == "llm"]

STEP_HTML = "".join(
    f'<section data-step="{i + 1}" data-screen="{s["screenId"]}"><h2>{s["name"]}</h2>'
    f'<p>기본금리 연 3.0% 적금 우대금리 축구클럽 회원 인증 시 우대금리 +1.0%p 자동이체 월 납입금액 가입기간 만기 예상 이자</p>'
    f'<label><input type="checkbox">동의</label><input type="number" placeholder="금액"><button>다음</button></section>'
    for i, s in enumerate(SPEC["steps"]))
GOOD_HTML = f"<html><head><style>.cta{{background:#008485}}</style></head><body>{STEP_HTML}</body></html>"
BAD_HTML = "<html><body><section data-step=\"1\"><h2>적금</h2><button>확인</button></section></body></html>"


class FakeStream(list):
    usage = {"inputTokens": 100, "outputTokens": 50}


def gen_seq(*htmls):
    it = iter(htmls)
    calls = []

    def generate(system, user, max_tokens):
        calls.append(user)
        return FakeStream(["```html\n", next(it), "\n```"])
    generate.calls = calls
    return generate


def reviewer(verdict="pass"):
    import json

    def review_generate(system, user, max_tokens):
        return json.dumps({"items": [{"id": i, "verdict": verdict, "evidence": "e", "fix": "f"} for i in LLM_IDS]}), {"inputTokens": 10, "outputTokens": 5}
    return review_generate


def publish(job_id, n, html):
    return f"https://x/studio/drafts/{job_id}-r{n}.html"


def _job(**kw):
    return loop.clamp_job({"jobId": "j1", "brief": "축구사랑 적금 가입 플로우", "productCode": "PRD-DEP-001", "outputType": "ux-flow", **kw})


def test_clamp_job_bounds():
    j = loop.clamp_job({"maxRounds": 99, "passScore": 10, "outputType": "weird", "axis": "x", "brief": "a" * 2000})
    assert j["maxRounds"] == 20 and j["passScore"] == 50 and j["outputType"] == "design" and j["axis"] == "흐름" and len(j["brief"]) == 1000
    assert loop.clamp_job({})["maxRounds"] == 3 and loop.clamp_job({"maxRounds": 0})["maxRounds"] == 1 and loop.clamp_job({})["mode"] == "generate"


def test_passes_first_round_and_stops():
    em = loop.ListEmitter()
    out = loop.run(_job(maxRounds=5), em, spec=SPEC, generate=gen_seq(GOOD_HTML), review_generate=reviewer("pass"), publish=publish, skills={"s": "skill"})
    assert out["stopReason"] == "passed" and out["rounds"] == 1 and out["passed"] and out["score"] == 100
    assert [s["step"] for s in em.stages] == ["spec_build", "assets", "generate", "review", "publish"]
    assert out["url"].endswith("j1-r1.html") and out["bestRound"] == 1 and out["usage"]["outputTokens"] == 55
    assert "".join(em.tokens).strip().endswith("```")


def test_regenerates_until_pass_and_feeds_failures():
    gen = gen_seq(BAD_HTML, GOOD_HTML)
    out = loop.run(_job(maxRounds=3), loop.ListEmitter(), spec=SPEC, generate=gen, review_generate=reviewer("pass"), publish=publish)
    assert out["rounds"] == 2 and out["stopReason"] == "passed" and out["bestRound"] == 2
    assert "이전 라운드" in gen.calls[1] and "FLOW-COND" in gen.calls[1] and BAD_HTML in gen.calls[1]
    assert [h["round"] for h in out["history"]] == [1, 2] and out["history"][0]["score"] < out["history"][1]["score"]


def test_max_rounds_picks_best_round():
    gen = gen_seq(BAD_HTML, GOOD_HTML, BAD_HTML)
    out = loop.run(_job(maxRounds=3, passScore=100), loop.ListEmitter(), spec=SPEC, generate=gen, review_generate=reviewer("fail"), publish=publish)
    assert out["stopReason"] == "max_rounds" and out["rounds"] == 3 and not out["passed"]
    assert out["bestRound"] == 2 and out["url"].endswith("j1-r2.html") and out["html"] == GOOD_HTML
    assert all("html" not in h and "items" not in h for h in out["history"])
    assert out["items"] and any(i["verdict"] == "pass" for i in out["items"])


def test_time_cap_stops_before_next_round():
    t = [0.0]

    def clock():
        t[0] += 500.0
        return t[0]
    out = loop.run(_job(maxRounds=10), loop.ListEmitter(), spec=SPEC, generate=gen_seq(BAD_HTML, BAD_HTML, BAD_HTML), review_generate=reviewer("fail"), publish=publish, clock=clock, time_cap_s=780)
    assert out["stopReason"] == "time_cap" and out["rounds"] < 10 and out["rounds"] == 1


def test_time_cap_already_exceeded_still_completes_one_round():
    calls = [0]

    def clock():
        calls[0] += 1
        return 0.0 if calls[0] == 1 else 100000.0
    out = loop.run(_job(maxRounds=10), loop.ListEmitter(), spec=SPEC, generate=gen_seq(BAD_HTML, BAD_HTML, BAD_HTML), review_generate=reviewer("fail"), publish=publish, clock=clock, time_cap_s=780)
    assert out["stopReason"] == "time_cap" and out["rounds"] == 1


def test_reviewer_garbage_is_undetermined_not_pass():
    def bad_reviewer(system, user, max_tokens):
        return "죄송합니다, 판정할 수 없습니다.", {}
    em = loop.ListEmitter()
    out = loop.run(_job(maxRounds=1), em, spec=SPEC, generate=gen_seq(GOOD_HTML), review_generate=bad_reviewer, publish=publish)
    rv = next(s for s in em.stages if s["step"] == "review")
    assert rv["reviewerError"] and set(rv["undetermined"]) == set(LLM_IDS) and not out["passed"] and out["stopReason"] == "max_rounds"
    assert all(i["verdict"] is None for i in rv["items"] if i["check"] == "llm")


def test_no_html_in_output_is_a_failed_round():
    out = loop.run(_job(maxRounds=1), loop.ListEmitter(), spec=SPEC, generate=gen_seq("텍스트만 있음"), review_generate=reviewer("pass"), publish=publish)
    assert out["score"] == 0 and not out["passed"] and out["history"][0]["failures"][0]["id"] == "OUTPUT"


def test_refine_mode_single_round_with_stability_item():
    gen = gen_seq(GOOD_HTML)
    em = loop.ListEmitter()
    job = _job(mode="refine", selector="body > section:nth-of-type(2) > h2", instruction="제목 크게", elementHtml="<h2>기간 선택</h2>")
    out = loop.run(job, em, spec=SPEC, generate=gen, review_generate=reviewer("pass"), publish=publish, base_html=GOOD_HTML)
    assert job["maxRounds"] == 1 and out["rounds"] == 1
    assert "제목 크게" in gen.calls[0] and "body > section:nth-of-type(2) > h2" in gen.calls[0]
    rv = next(s for s in em.stages if s["step"] == "review")
    stable = next(i for i in rv["items"] if i["id"] == "STABLE")
    assert stable["verdict"] == "pass" and stable["weight"] == 1


def test_generate_regenerate_round_stable_is_informational():
    gen = gen_seq(BAD_HTML, GOOD_HTML)
    em = loop.ListEmitter()
    out = loop.run(_job(maxRounds=3, passScore=100), em, spec=SPEC, generate=gen, review_generate=reviewer("pass"), publish=publish)
    reviews = [s for s in em.stages if s["step"] == "review"]
    round2 = reviews[1]
    stable = next(i for i in round2["items"] if i["id"] == "STABLE")
    assert stable["weight"] == 0
    assert round2["passed"] and round2["score"] == 100
    assert out["passed"] and out["score"] == 100


def test_best_round_prefers_published_html_on_tie():
    gen = gen_seq(BAD_HTML)

    def boom(system, user, max_tokens):
        raise RuntimeError("gate refused")

    calls = [0]

    def gen_then_boom(system, user, max_tokens):
        calls[0] += 1
        if calls[0] == 1:
            return gen(system, user, max_tokens)
        raise RuntimeError("gate refused")

    out = loop.run(_job(maxRounds=3), loop.ListEmitter(), spec=SPEC, generate=gen_then_boom, review_generate=reviewer("fail"), publish=publish)
    assert out["stopReason"] == "error" and out["bestRound"] == 1
    assert out["url"].endswith("j1-r1.html")
    assert out["score"] > 0


def test_generate_exception_becomes_error_stop():
    def boom(system, user, max_tokens):
        raise RuntimeError("gate refused")
    out = loop.run(_job(maxRounds=3), loop.ListEmitter(), spec=SPEC, generate=boom, review_generate=reviewer(), publish=publish)
    assert out["stopReason"] == "error" and "gate refused" in out["error"] and out["rounds"] == 1


def test_done_payload_stays_under_frame_budget():
    """20라운드 전부 실패 + 긴 근거/수정 문구 — .done 은 WebSocket 프레임(128KB) 안에 들어와야 한다."""
    import json as _json

    def generate(system, user, max_tokens):
        return FakeStream(["```html\n", BAD_HTML, "\n```"])

    long_text = "가" * 300

    def review_generate(system, user, max_tokens):
        return _json.dumps({"items": [{"id": i, "verdict": "fail", "evidence": long_text, "fix": long_text} for i in LLM_IDS]}), {}

    out = loop.run(_job(maxRounds=20, passScore=100), loop.ListEmitter(), spec=SPEC, generate=generate,
                   review_generate=review_generate, publish=publish)
    assert out["rounds"] == 20 and out["stopReason"] == "max_rounds"
    size = len(_json.dumps(out, ensure_ascii=False).encode())
    assert size < 100_000, size
    for h in out["history"]:
        assert len(h["failures"]) <= loop.DONE_FAILURES_PER_ROUND and h["failuresTotal"] >= len(h["failures"])
        assert all(set(f) == {"id", "text", "verdict"} for f in h["failures"])
        assert len(h["undetermined"]) <= loop.DONE_UNDETERMINED_MAX
    assert all(len(i["evidence"]) <= loop.DONE_TEXT_MAX and len(i["fix"]) <= loop.DONE_TEXT_MAX for i in out["items"])


def test_reports_measured_model_and_route():
    class MeasuredStream(FakeStream):
        model_id = "measured-model"
        route = "claude"

    def generate(system, user, max_tokens):
        return MeasuredStream(["```html\n", GOOD_HTML, "\n```"])

    out = loop.run(_job(maxRounds=1), loop.ListEmitter(), spec=SPEC, generate=generate, review_generate=reviewer("pass"), publish=publish)
    assert out["model"] == "measured-model" and out["route"] == "claude"


def test_model_falls_back_to_configured_id_when_unmeasured():
    out = loop.run(_job(maxRounds=1), loop.ListEmitter(), spec=SPEC, generate=gen_seq(GOOD_HTML), review_generate=reviewer("pass"), publish=publish)
    assert out["model"] == loop.MODEL and out["route"] == ""


BAD_HTML_WITH_ACCOUNT = ("<html><body><section data-step=\"1\"><h2>적금</h2>"
                         "<p>계좌 110-234-567890 로 입금하세요</p><button>확인</button></section></body></html>")


def test_regenerate_prompt_is_gate_safe():
    """라운드 1이 합성 계좌번호를 담은 HTML을 냈을 때, 라운드 2 생성 프롬프트는 그 원문 숫자를
    싣지 않고 마스킹된 형태를 실어야 게이트(GateRefused)를 다시 맞지 않는다."""
    gen = gen_seq(BAD_HTML_WITH_ACCOUNT, GOOD_HTML)
    out = loop.run(_job(maxRounds=2), loop.ListEmitter(), spec=SPEC, generate=gen, review_generate=reviewer("fail"), publish=publish)
    assert out["rounds"] == 2
    assert "110-234-567890" not in gen.calls[1]
    assert "***-***-******" in gen.calls[1]


def test_review_digest_is_scrubbed():
    """리뷰어 프롬프트(다이제스트)에도 합성 계좌번호 원문이 실리면 안 된다."""
    captured = []

    def review_generate(system, user, max_tokens):
        captured.append(user)
        import json
        return json.dumps({"items": [{"id": i, "verdict": "pass", "evidence": "e", "fix": "f"} for i in LLM_IDS]}), {}

    loop.run(_job(maxRounds=1), loop.ListEmitter(), spec=SPEC, generate=gen_seq(BAD_HTML_WITH_ACCOUNT),
              review_generate=review_generate, publish=publish)
    assert captured
    assert "110-234-567890" not in captured[0]
