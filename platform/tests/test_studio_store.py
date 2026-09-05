# platform/tests/test_studio_store.py
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from decimal import Decimal  # noqa: E402

from studio.store import StudioStore, _clean  # noqa: E402


def _draft(i, status="검토중", ts=None):
    return {"draftId": f"d{i}", "jobId": f"j{i}", "title": f"시안 {i}", "axis": "흐름", "outputType": "ux-flow", "productCode": "PRD-DEP-001",
            "productName": "아톰 축구사랑 적금", "score": 80 + i, "passed": i > 1, "rounds": 2, "bestRound": 2, "status": status,
            "url": f"https://x/studio/drafts/d{i}.html", "key": f"studio/drafts/d{i}.html", "createdAt": ts or (1000 + i), "createdBy": "u@x", "model": "m"}


def test_memory_backend_and_job_roundtrip():
    st = StudioStore()
    assert st.backend == "memory"
    st.put_job({"jobId": "j1", "brief": "b", "productCode": "PRD-DEP-001", "maxRounds": 3}, actor="u@x")
    st.put_round("j1", {"round": 1, "score": 40, "passed": False, "url": "u1", "html": "<html>", "items": [1, 2], "failures": []})
    st.put_round("j1", {"round": 2, "score": 90, "passed": True, "url": "u2", "html": "<html>", "items": [], "failures": []})
    st.update_job("j1", status="done", score=90, stopReason="passed")
    j = st.get_job("j1")
    assert j["status"] == "done" and j["score"] == 90 and [r["round"] for r in j["rounds"]] == [1, 2]
    assert "html" not in j["rounds"][0] and "items" not in j["rounds"][0]
    assert st.get_job("nope") is None
    st.put_job({"jobId": "j2", "brief": "b", "productCode": "PRD-DEP-002"}, actor="u@x")
    assert [x["jobId"] for x in st.list_jobs()] == ["j2", "j1"]


def test_drafts_list_status_and_approved():
    st = StudioStore()
    for i in (1, 2, 3):
        st.put_draft(_draft(i))
    assert [d["draftId"] for d in st.list_drafts()] == ["d3", "d2", "d1"]
    assert st.get_draft("d2")["score"] == 82
    upd = st.set_draft_status("d2", "승인됨", "좋음", actor="u@x")
    assert upd["status"] == "승인됨" and upd["comment"] == "좋음" and upd["reviewedBy"] == "u@x"
    st.set_draft_status("d3", "반려", "CTA 두 개", actor="u@x")
    assert st.get_draft("d3")["status"] == "반려"
    assert [d["draftId"] for d in st.list_drafts()][1] == "d2" and st.list_drafts()[1]["status"] == "승인됨", "목록 행도 갱신"
    assert [d["draftId"] for d in st.approved_drafts()] == ["d2"]
    assert st.set_draft_status("nope", "승인됨", "", actor="u@x") is None


def test_clean_converts_nested_decimals():
    item = {"pk": "x", "sk": "y", "score": Decimal("91"), "ratio": Decimal("0.5"),
            "failures": [{"weight": Decimal("3"), "tags": [Decimal("1")]}]}
    out = _clean(item)
    assert "pk" not in out and "sk" not in out
    assert out["score"] == 91 and isinstance(out["score"], int)
    assert out["ratio"] == 0.5
    assert out["failures"][0]["weight"] == 3 and isinstance(out["failures"][0]["weight"], int)
    assert out["failures"][0]["tags"] == [1] and isinstance(out["failures"][0]["tags"][0], int)


def test_put_draft_twice_keeps_single_list_row():
    st = StudioStore()
    st.put_draft(_draft(1, ts=1001))
    d1 = _draft(1, ts=5000)
    d1["score"] = 99
    st.put_draft(d1)
    rows = [d for d in st.list_drafts() if d["draftId"] == "d1"]
    assert len(rows) == 1
    assert rows[0]["score"] == 99
    assert rows[0]["createdAt"] == 1001
