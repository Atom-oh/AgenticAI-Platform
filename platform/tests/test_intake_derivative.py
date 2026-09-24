"""identifier-normalization-1 derivative and residual scan (Task I4)."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from intake import derivative  # noqa: E402


def fake(prefix="ExampleTenant"):
    return prefix + secrets.token_hex(3)


def pages(*texts):
    return [{"page": index, "text": text} for index, text in enumerate(texts, 1)]


def test_deny_listed_name_is_replaced_and_financial_values_are_byte_identical():
    term = fake()
    denylist = [{"term": term, "kind": "org"}]
    source = f"{term} 정기예금은 연 2.0% 금리로 12개월 동안 운용합니다. 한도 1,000,000원."
    result = derivative.normalize(pages(source), denylist)
    text = result["pages"][0]["text"]
    assert term not in text and "고객사 A" in text
    for literal in ("연 2.0%", "12개월", "1,000,000원"):
        assert source.count(literal) == text.count(literal) == 1
        assert literal.encode() in text.encode()
    assert result["replacements"] == {"aliasCount": 1}
    assert derivative.residual(result["pages"], denylist) == {"identifiers": 0, "pii": []}


def test_ascii_terms_match_case_insensitively_and_explicit_aliases_apply():
    term = fake()
    denylist = [{"term": term, "alias": "고객사 B"}]
    result = derivative.normalize(pages(f"{term.upper()} and {term.lower()}"), denylist)
    assert result["pages"][0]["text"] == "고객사 B and 고객사 B"
    assert result["replacements"]["aliasCount"] == 2


def test_overlapping_term_is_replaced_as_the_longer_one():
    short = fake("Short")
    long = short + " Capital Partners"
    denylist = [{"term": short, "alias": "고객사 A"}, {"term": long, "alias": "고객사 B"}]
    result = derivative.normalize(pages(f"{long} owns {short}."), denylist)
    assert result["pages"][0]["text"] == "고객사 B owns 고객사 A."


def test_internal_urls_and_deny_listed_hosts_become_internal_link_aliases():
    host = fake("portal") + ".example"
    denylist = [{"term": host, "kind": "host"}]
    text = (f"See https://wiki.team.internal/page?id=1 and http://{host}/docs/a and "
            "https://build.corp/x and https://public.example.org/ok")
    result = derivative.normalize(pages(text), denylist)["pages"][0]["text"]
    assert result.count("[내부 링크]") == 3
    assert "internal" not in result and host not in result and ".corp" not in result
    assert "https://public.example.org/ok" in result


def test_residual_count_is_zero_after_normalization_and_nonzero_before():
    term = fake()
    denylist = [{"term": term, "kind": "org"}]
    raw = pages(f"{term} guide", "second page")
    assert derivative.residual(raw, denylist)["identifiers"] == 1
    assert derivative.residual(derivative.normalize(raw, denylist)["pages"], denylist)["identifiers"] == 0


def test_residual_reports_pii_by_type_and_count_only():
    report = derivative.residual(pages("call 010-5550-7391"), [{"term": fake(), "kind": "org"}])
    assert {"type": "PHONE", "count": 1} in report["pii"]
    assert "7391" not in json.dumps(report)


def test_derivative_is_deterministic_and_hash_covers_page_and_text():
    term = fake()
    denylist = [{"term": term, "kind": "org"}]
    raw = pages(f"{term} guide", "second page")
    first, second = derivative.normalize(raw, denylist), derivative.normalize(raw, denylist)
    assert first == second
    assert first["derivativeHash"] == derivative.derivative_hash(first["pages"])
    assert first["derivativeHash"] != derivative.normalize(pages("other"), denylist)["derivativeHash"]
    assert [set(p) for p in first["pages"]] == [{"page", "text"}] * 2


def test_nfc_normalized_matching():
    import unicodedata
    term = "예시" + secrets.token_hex(2) + "은행"
    decomposed = unicodedata.normalize("NFD", term)
    result = derivative.normalize(pages(f"{decomposed} 안내"), [{"term": term, "kind": "org"}])
    assert result["pages"][0]["text"] == "고객사 A 안내"


def test_missing_deny_list_configuration_is_unavailable(monkeypatch):
    monkeypatch.delenv("INTAKE_DENYLIST_PARAM", raising=False)
    monkeypatch.delenv("INTAKE_DENYLIST_KEY", raising=False)
    with pytest.raises(derivative.DenylistUnavailable):
        derivative.load_denylist()


class FakeSsm:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, []

    def get_parameter(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"Parameter": {"Value": self.value}}


def test_deny_list_loads_from_the_named_secure_parameter(monkeypatch):
    term = fake()
    monkeypatch.setenv("INTAKE_DENYLIST_PARAM", "/offline/intake/denylist")
    monkeypatch.delenv("INTAKE_DENYLIST_KEY", raising=False)
    ssm = FakeSsm(json.dumps([{"term": term, "kind": "org"}]))
    assert derivative.load_denylist(ssm=ssm) == [{"term": term, "alias": "고객사 A"}]
    assert ssm.calls == [{"Name": "/offline/intake/denylist", "WithDecryption": True}]


@pytest.mark.parametrize("value,error", [(None, RuntimeError("AccessDenied")), ("not json", None),
                                         ("[]", None), ('[{"term": ""}]', None),
                                         ('[{"term": "x", "extra": 1}]', None)])
def test_unreadable_or_invalid_deny_list_is_unavailable(monkeypatch, value, error):
    monkeypatch.setenv("INTAKE_DENYLIST_PARAM", "/offline/intake/denylist")
    with pytest.raises(derivative.DenylistUnavailable):
        derivative.load_denylist(ssm=FakeSsm(value, error))


def test_deny_list_loads_from_a_private_s3_key(monkeypatch):
    from test_workspace_storage import FakeS3
    term = fake()
    s3 = FakeS3()
    s3.put_object(Bucket="private-config", Key="intake/denylist.json",
                  Body=json.dumps([{"term": term, "kind": "org"}]).encode(), ContentType="application/json")
    monkeypatch.delenv("INTAKE_DENYLIST_PARAM", raising=False)
    monkeypatch.setenv("INTAKE_DENYLIST_KEY", "s3://private-config/intake/denylist.json")
    assert derivative.load_denylist(s3=s3) == [{"term": term, "alias": "고객사 A"}]


def test_normalize_text_replaces_and_keeps_financial_values():
    term = fake()
    denylist = [{"term": term, "kind": "org"}]
    assert derivative.normalize_text(f"{term} 상품 연 2.0% 12개월", denylist) == "고객사 A 상품 연 2.0% 12개월"


def test_normalize_text_blocks_a_phone_number_with_counts_only():
    with pytest.raises(derivative.NormalizationBlocked) as error:
        derivative.normalize_text("연락처 010-5550-7391", [{"term": fake(), "kind": "org"}])
    assert "PHONE" in error.value.types
    assert "7391" not in str(error.value) and "7391" not in repr(error.value.counts)


def test_normalize_text_requires_a_deny_list():
    with pytest.raises(derivative.DenylistUnavailable):
        derivative.normalize_text("text", None)
