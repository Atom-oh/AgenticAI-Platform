"""Source resolution and inspection receipts (Task I3; SRC-01, SRC-06)."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_support import api, approved, draft, project, scope  # noqa: F401
from intake import inspect as intake_inspect
from workspace.collaboration import CollaborationError


def fake_term():
    return "ExampleTenant" + secrets.token_hex(3)


def test_approved_synthetic_revision_gives_a_clean_receipt(api):
    pid = project(api)
    ref = approved(api, pid, b"Synthetic product guide.\n\nRate 2.0% for 12 months.\n")
    resolved = intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    assert [p["page"] for p in resolved["pages"]] == [1]
    assert "Rate 2.0% for 12 months." in resolved["pages"][0]["text"]
    assert resolved["pages"][0]["logical"] is True
    assert resolved["pages"][0]["paragraphIds"] == ["p000001", "p000002"]
    receipt = intake_inspect.inspect(resolved["pages"], denylist=[{"term": fake_term(), "alias": "고객사 A"}])
    assert receipt["profile"] == "inspect-1"
    assert receipt["pii"] == [] and receipt["identifiers"] == {"count": 0}
    assert receipt["blocking"] == []
    assert receipt["pages"] == 1 and receipt["chars"] > 0
    assert receipt["hash"] == intake_inspect.receipt_hash(receipt)


def test_draft_revision_is_not_approved(api):
    pid = project(api)
    ref = draft(api, pid, b"Synthetic draft guide.\n")
    with pytest.raises(CollaborationError) as error:
        intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    assert error.value.code == "source-not-approved"


def test_phone_number_page_requires_redaction_and_the_receipt_stores_no_digits(api):
    pid = project(api)
    phone = "010-5550-7391"
    ref = approved(api, pid, f"Synthetic contact line {phone}.\n".encode())
    resolved = intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    receipt = intake_inspect.inspect(resolved["pages"], denylist=[])
    assert receipt["blocking"] == ["redaction-required"]
    assert receipt["classificationSuggestion"] == "sensitive"
    assert {"type": "PHONE", "count": 1} in receipt["pii"]
    serialized = json.dumps({k: v for k, v in receipt.items() if k != "hash"}, ensure_ascii=False)
    for fragment in (phone, phone.replace("-", ""), "5550", "7391"):
        assert fragment not in serialized
    assert "sample" not in serialized


def test_deny_list_hits_are_counted_only(api):
    pid = project(api)
    term = fake_term()
    ref = approved(api, pid, f"{term} internal guide.\n\n{term.lower()} again.\n".encode())
    resolved = intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    receipt = intake_inspect.inspect(resolved["pages"], denylist=[{"term": term, "alias": "고객사 A"}])
    assert receipt["identifiers"] == {"count": 2}
    assert term not in json.dumps(receipt, ensure_ascii=False)
    assert receipt["blocking"] == []


def test_missing_deny_list_blocks_the_receipt(api):
    pid = project(api)
    ref = approved(api, pid, b"Synthetic guide.\n")
    resolved = intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    assert intake_inspect.inspect(resolved["pages"], denylist=None)["blocking"] == ["denylist-unavailable"]


def test_actor_without_source_access_gets_the_sources_refusal(api):
    pid = project(api)
    ref = approved(api, pid, b"Synthetic restricted guide.\n", readRoles=["owner", "planner"])
    with pytest.raises(CollaborationError) as error:
        intake_inspect.resolve(api, scope(api, "carol", pid), ref)
    assert error.value.status in (403, 404)


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("revision", "unknown--r000009"),
                                         ("audienceRevision", "999")])
def test_wrong_source_identity_is_rejected_by_current_authority(api, field, value):
    pid = project(api)
    ref = approved(api, pid, b"Synthetic guide.\n")
    with pytest.raises(CollaborationError):
        intake_inspect.resolve(api, scope(api, "alice", pid), {**ref, field: value})


@pytest.mark.parametrize("kind", ["published-asset", "ux-contract", "run-round"])
def test_reserved_source_kinds_keep_failing_503(api, kind):
    pid = project(api)
    ref = {"sourceKind": kind, "sourceId": "reserved-1", "revision": "1", "sha256": "a" * 64,
           "audienceRevision": "1"}
    with pytest.raises(CollaborationError) as error:
        intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    assert error.value.status == 503 and error.value.code == "ontology-source-adapter-unavailable"


def test_unsupported_intake_kinds_fail_closed(api):
    pid = project(api)
    for ref in ({"sourceKind": "package", "sourceId": "studio-ui", "revision": "1", "sha256": "a" * 64,
                 "audienceRevision": "platform-package-v1"},
                {"sourceKind": "unknown", "sourceId": "x", "revision": "1", "sha256": "a" * 64,
                 "audienceRevision": "1"}):
        with pytest.raises(CollaborationError) as error:
            intake_inspect.resolve(api, scope(api, "alice", pid), ref)
        assert error.value.status in (400, 422)


def test_unpaginated_sources_get_stable_bounded_logical_pages(api):
    pid = project(api)
    paragraphs = [f"Synthetic paragraph {i} " + "가" * 1500 for i in range(7)]
    ref = approved(api, pid, "\n\n".join(paragraphs).encode(), name="long.md")
    first = intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    second = intake_inspect.resolve(api, scope(api, "alice", pid), ref)
    assert first["pages"] == second["pages"] and first["originalHash"] == second["originalHash"]
    pages = first["pages"]
    assert [p["page"] for p in pages] == list(range(1, len(pages) + 1))
    assert len(pages) > 1 and all(len(p["text"]) <= 4000 for p in pages)
    assert all(p["logical"] for p in pages)
    covered = [p["paragraphIds"] for p in pages]
    assert covered[0][0] == "p000001" and covered[-1][1] == "p000007"
    for index, text in enumerate(paragraphs):
        assert sum(text in p["text"] for p in pages) == 1  # a paragraph is never split


@pytest.mark.parametrize("name,body", [
    ("guide.txt", "Synthetic guide one.\n\nSecond paragraph.\n"),
    ("guide.md", "# Synthetic guide\n\nA paragraph.\n\n- item\n"),
    ("guide.html", "<html><body><h1>Synthetic guide</h1><p>A paragraph.</p></body></html>"),
])
def test_unpaginated_formats_get_positive_logical_pages_the_contract_accepts(api, name, body):
    from workspace.rules import _integer
    pid = project(api)
    ref = approved(api, pid, body.encode(), name=name)
    pages = intake_inspect.resolve(api, scope(api, "alice", pid), ref)["pages"]
    assert pages and all(page["logical"] is True for page in pages)
    for page in pages:
        assert _integer(page["page"], "출처 페이지", 1, 10000) == page["page"]
