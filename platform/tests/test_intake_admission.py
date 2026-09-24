"""Admission decisions, current-authority verification and derivative-only reads.

Task I6: SRC-06, AUTH-01 and AUTH-08 (O-mode and API negatives).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_support import api, approved, call, project, scope  # noqa: F401,E402
from intake import admin_handler, admission, derivative, records, review  # noqa: E402
from intake.admission import AdmissionError  # noqa: E402
from intake.records import INTAKE_OWNER  # noqa: E402
from workspace.ontology_sources import Sources  # noqa: E402

DAY = 86_400_000
LAMBDA = SimpleNamespace(invoked_function_arn="arn:aws:lambda:ap-northeast-2:000000000000:function:IntakeAdminFn")


class Env(SimpleNamespace):
    def admin(self, event):
        result = admin_handler.handler({"operator": "security-operator", **event}, LAMBDA)
        assert result.get("ok"), result
        return result["record"]

    def policy(self, **extra):
        body = {"id": "policy-1", "scope": {"deployment": "offline-test", "projectIds": [self.pid]},
                "dataClasses": ["synthetic", "public", "internal-non-sensitive"],
                "profiles": {"inspection": "inspect-1", "normalization": "identifier-normalization-1"},
                "requiresReviewer": {"internal-non-sensitive": True},
                "trustedProvenance": {"public": True, "synthetic": True},
                "promptText": "review", "expiresAt": self.api.storage.clock() + 60 * DAY, **extra}
        draft = self.admin({"op": "put_policy", "record": body})
        return self.admin({"op": "activate_policy", "id": draft["id"], "expectedRevision": draft["revision"]})

    def grant(self, actor, identifier="grant-1"):
        return self.admin({"op": "grant_reviewer", "record": {
            "id": identifier, "actor": actor, "policyId": "policy-1", "scope": {"projectIds": [self.pid]},
            "operations": ["review-internal"], "expiresAt": self.api.storage.clock() + 30 * DAY}})

    def provenance(self, ref, policy, kind="public-reference"):
        record = {"id": "prov-1", "policyId": policy["id"], "policyRevision": policy["revision"], "kind": kind,
                  "reference": {key: ref[key] for key in ("sourceKind", "sourceId", "revision", "sha256")},
                  "scope": {"deployment": "offline-test", "projectIds": [self.pid]},
                  "expiresAt": self.api.storage.clock() + 30 * DAY}
        if kind == "public-reference":
            record["publicUrl"] = "https://example.test/public-reference"
        return self.admin({"op": "register_provenance", "record": record})

    def scope(self, actor="alice"):
        return scope(self.api, actor, self.pid)

    def http(self, method, path, body=None, actor="alice"):
        status, payload, _ = call(self.api, method, path, body, actor=actor, project=self.pid)
        return status, payload


@pytest.fixture
def env(api, monkeypatch):
    monkeypatch.setenv("INTAKE_DEPLOYMENT", "offline-test")
    monkeypatch.setattr(admin_handler, "storage_factory", lambda: api.storage)
    term = "ExampleTenant" + secrets.token_hex(3)
    api.intake_denylist_loader = lambda: [{"term": term, "kind": "org"}]
    return Env(api=api, pid=project(api), term=term)


def guide(env, text=None, **fields):
    body = text if text is not None else f"{env.term} 정기예금 안내.\n\n연 2.0% 금리, 12개월.\n"
    return approved(env.api, env.pid, body.encode(), **fields)


def internal_admitted(env, ref=None, reviewer="bob"):
    env.policy()
    env.grant(reviewer)
    ref = ref or guide(env)
    pending = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    assert pending["status"] == "pending-review"
    decision = review.decide(env.api, env.scope(reviewer), pending["id"], approve=True, reason="checked")
    assert decision["status"] == "admitted"
    return decision


# Happy paths -----------------------------------------------------------------

def test_public_source_with_registered_provenance_is_admitted(env):
    policy = env.policy()
    ref = guide(env)
    env.provenance(ref, policy)
    decision = admission.request(env.api, env.scope(), ref, data_class="public")
    assert decision["status"] == "admitted" and decision["provenance"] == {"id": "prov-1", "revision": 1}
    assert "review" not in decision
    assert admission.verify(env.api, env.scope(), decision["id"])["id"] == decision["id"]
    assert admission.admission_ref(decision) == {"decisionId": decision["id"], "revision": 1,
                                                 "artifactHash": decision["derivation"]["derivativeHash"]}


def test_public_source_without_provenance_is_blocked_not_admitted(env):
    env.policy()
    decision = admission.request(env.api, env.scope(), guide(env), data_class="public")
    assert decision["status"] == "blocked" and decision["blocking"] == ["provenance-required"]


def test_internal_document_is_pending_and_a_reviewer_with_grant_and_access_admits_it(env):
    env.policy()
    env.grant("bob")
    ref = guide(env)
    pending = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    assert pending["status"] == "pending-review" and "review" not in pending
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), pending["id"])
    assert error.value.code == "decision-not-current"
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert status == 200 and [r["id"] for r in listed["reviews"]] == [pending["id"]]
    item = listed["reviews"][0]
    assert "고객사 A" in item["derivativePreview"] and env.term not in json.dumps(listed, ensure_ascii=False)
    assert item["title"] == "Synthetic policy" and item["inspection"]["identifiers"] == {"count": 1}
    status, result = env.http("POST", f"/intake/reviews/{pending['id']}", {"approve": True, "reason": "checked"},
                              actor="bob")
    assert status == 200 and result["decision"]["status"] == "admitted"
    decision = admission.verify(env.api, env.scope(), pending["id"])
    assert decision["review"]["actor"] == "bob" and decision["review"]["grantId"] == "grant-1"


def test_reviewer_rejection_is_final_and_not_admitted(env):
    env.policy()
    env.grant("bob")
    pending = admission.request(env.api, env.scope(), guide(env), data_class="internal-non-sensitive")
    rejected = review.decide(env.api, env.scope("bob"), pending["id"], approve=False, reason="needs work")
    assert rejected["status"] == "rejected"
    with pytest.raises(AdmissionError):
        admission.pages_for(env.api, env.scope(), pending["id"])


# Denied or blocked ------------------------------------------------------------

def test_reviewer_with_grant_but_no_source_access_is_denied(env):
    env.policy()
    env.grant("carol")  # designer; the document is readable only by owner/planner
    ref = guide(env, readRoles=["owner", "planner"])
    pending = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    status, payload = env.http("POST", f"/intake/reviews/{pending['id']}", {"approve": True, "reason": "x"},
                               actor="carol")
    assert status == 403 and payload["code"] == "source-access-required"
    assert env.http("GET", "/intake/reviews", actor="carol")[1]["reviews"] == []
    assert env.api.storage.get(f"project:{env.pid}", "adm_decision", pending["id"])["status"] == "pending-review"


def test_project_owner_without_a_grant_is_denied(env):
    env.policy()
    pending = admission.request(env.api, env.scope(), guide(env), data_class="internal-non-sensitive")
    for method, path, body in (("GET", "/intake/reviews", None),
                               ("POST", f"/intake/reviews/{pending['id']}", {"approve": True, "reason": "x"})):
        status, payload = env.http(method, path, body, actor="alice")
        assert status == 403 and payload["code"] == "review-grant-required"
    with pytest.raises(AdmissionError) as error:
        review.decide(env.api, env.scope("alice"), pending["id"], approve=True, reason="x")
    assert error.value.status == 403


def test_in_app_operator_group_confers_no_review_or_admin_route(env):
    env.policy()
    pending = admission.request(env.api, env.scope(), guide(env), data_class="internal-non-sensitive")
    event = {"rawPath": f"/studio-api/intake/reviews/{pending['id']}", "headers": {"X-Workspace-Project": env.pid},
             "requestContext": {"http": {"method": "POST"}, "authorizer": {"jwt": {"claims": {
                 "sub": "alice", "token_use": "access", "cognito:groups": ["platform-operators", "admin"]}}}},
             "body": json.dumps({"approve": True, "reason": "x"})}
    result = env.api.handle(event)
    assert result["statusCode"] == 403
    for path in ("/intake/policies", "/intake/grants", "/intake/provenance"):
        assert env.http("POST", path, {"id": "x"})[0] == 404


def test_pii_is_blocked(env):
    env.policy()
    ref = guide(env, text="Synthetic contact 010-5550-7391.\n")
    decision = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    assert decision["status"] == "blocked" and "redaction-required" in decision["blocking"]
    assert "7391" not in json.dumps({k: v for k, v in decision.items() if k not in ("hash",)})
    with pytest.raises(AdmissionError):
        admission.pages_for(env.api, env.scope(), decision["id"])


def test_missing_deny_list_is_blocked(env):
    env.policy()

    def unavailable():
        raise derivative.DenylistUnavailable()

    env.api.intake_denylist_loader = unavailable
    result = admission.request(env.api, env.scope(), guide(env), data_class="internal-non-sensitive")
    assert result == {"status": "blocked", "blocking": ["denylist-unavailable"]}


def test_missing_policy_and_ineligible_classes_are_blocked(env):
    ref = guide(env)
    assert admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive") == {
        "status": "blocked", "blocking": ["policy-unavailable"]}
    env.policy(dataClasses=["public"])
    for data_class in ("internal-non-sensitive", "sensitive", "unclassified"):
        assert admission.request(env.api, env.scope(), ref, data_class=data_class)["status"] == "blocked"


# Revocation and change --------------------------------------------------------

def test_policy_retirement_after_admission_fails_verify(env):
    decision = internal_admitted(env)
    policy = env.api.storage.get(INTAKE_OWNER, "adm_policy", "policy-1")
    env.admin({"op": "retire_policy", "id": "policy-1", "expectedRevision": policy["revision"]})
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "policy-changed"


def test_grant_revocation_fails_verify(env):
    decision = internal_admitted(env)
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "grant-revoked"


def test_provenance_revocation_fails_verify(env):
    policy = env.policy()
    ref = guide(env)
    env.provenance(ref, policy)
    decision = admission.request(env.api, env.scope(), ref, data_class="public")
    env.admin({"op": "revoke_provenance", "id": "prov-1", "expectedRevision": 1})
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "grant-revoked"


def test_tampered_derivative_blob_fails_verify(env):
    decision = internal_admitted(env)
    env.api.storage.put_blob(decision["artifact"]["key"], b'[{"page":1,"text":"tampered"}]', "application/json")
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "artifact-changed"


def test_tampered_inspection_blob_fails_verify(env):
    decision = internal_admitted(env)
    env.api.storage.put_blob(decision["inspection"]["receiptKey"], b"{}", "application/json")
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "inspection-changed"


def test_source_acl_change_fails_verify(env):
    ref = guide(env)
    decision = internal_admitted(env, ref=ref)
    document = env.api.storage.get(f"project:{env.pid}", "document", ref["sourceId"])
    status, payload = env.http("PUT", f"/documents/{ref['sourceId']}/permissions",
                               {"version": document["version"], "readRoles": ["owner", "planner", "designer"]})
    assert status == 200, payload
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code == "source-changed"


def test_expired_decision_is_not_current(env):
    decision = internal_admitted(env)
    clock = env.api.storage.clock
    env.api.storage.clock = lambda: clock() + 31 * DAY
    with pytest.raises(AdmissionError) as error:
        admission.verify(env.api, env.scope(), decision["id"])
    assert error.value.code in ("decision-not-current", "policy-changed")


# Full chain -------------------------------------------------------------------

def test_full_chain_from_iam_administration_without_pre_seeded_records(env):
    storage = env.api.storage
    assert storage.list(INTAKE_OWNER, "adm_policy") == [] and storage.list(INTAKE_OWNER, "adm_grant") == []
    env.policy()
    env.grant("bob")
    events = storage.list(INTAKE_OWNER, "adm_audit")
    assert sorted(e["op"] for e in events) == ["activate_policy", "grant_reviewer", "put_policy"]
    ref = guide(env)
    pending = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    decided = review.decide(env.api, env.scope("bob"), pending["id"], approve=True, reason="checked")
    batch = admission.pages_for(env.api, env.scope(), decided["id"])
    assert batch["cursor"] is None and batch["total"] == len(batch["pages"]) == 1
    assert admission.assemble([batch])[0]["text"].startswith("고객사 A 정기예금 안내.")
    for kind in ("adm_policy", "adm_grant", "adm_audit"):
        for row in storage.list(INTAKE_OWNER, kind):
            assert records.validate(kind, row)
    for row in storage.list(f"project:{env.pid}", "adm_decision"):
        assert records.validate("adm_decision", row)
    receipt = json.loads(storage.get_blob(decided["inspection"]["receiptKey"]))
    assert env.term not in json.dumps(receipt, ensure_ascii=False) and "sample" not in json.dumps(receipt)


def test_wrong_source_hash_or_historical_revision_is_rejected_although_authorize_accepts_it(env):
    import hashlib as _hashlib
    env.policy()
    first = guide(env)
    document = env.api.storage.get(f"project:{env.pid}", "document", first["sourceId"])
    data = "Second synthetic revision.\n".encode()
    status, second = env.http("POST", f"/documents/{first['sourceId']}/revisions", {
        "requestId": "rev2", "version": document["version"], "name": "policy.txt", "size": len(data),
        "sha256": _hashlib.sha256(data).hexdigest()})
    assert status == 201, second
    from test_documents_library import approve, finalize, path
    assert call(env.api, "PUT", path(second) + "/parts/0", data, project=env.pid)[0] == 200
    completed = call(env.api, "POST", path(second) + "/complete", {}, project=env.pid)[1]
    status, _ = approve(env.api, finalize(env.api, completed, project=env.pid), project=env.pid)
    assert status == 200
    from workbench.service import Service
    ctx = Service(env.api, env.scope(), {"sub": "alice"})
    wrong_hash = {**first, "sha256": "0" * 64}
    assert Sources(ctx).authorize(first) and Sources(ctx).authorize(wrong_hash)  # historical metadata only
    from workspace.collaboration import CollaborationError
    for ref in (first, wrong_hash):
        with pytest.raises(CollaborationError):
            admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    assert env.api.storage.list(f"project:{env.pid}", "adm_decision") == []


# Derivative reads ---------------------------------------------------------------

def test_pages_for_never_returns_original_text(env):
    decision = internal_admitted(env)
    batch = admission.pages_for(env.api, env.scope(), decision["id"])
    serialized = json.dumps(batch, ensure_ascii=False)
    assert env.term not in serialized and "고객사 A" in serialized
    assert "연 2.0% 금리, 12개월." in serialized
    entry = batch["pages"][0]
    assert set(entry) == {"admissionId", "derivativeHash", "sourceRef", "page", "text"}
    assert entry["admissionId"] == decision["id"] and entry["sourceRef"] == decision["source"]


def large_korean_document(env):
    unit = "가나다라마바사아자차카타파하" * 10  # 140 characters
    body = env.term + " " + "\n\n".join(unit[:139] + str(i % 10) for i in range(1286))
    assert len(body) >= 180_000
    assert len(json.dumps(body)) > 512 * 1024
    return guide(env, text=body, name="large.txt")


def test_bounded_delivery_of_a_large_korean_derivative(env):
    policy = env.policy()
    ref = large_korean_document(env)
    env.provenance(ref, policy)
    decision = admission.request(env.api, env.scope(), ref, data_class="public")
    assert decision["status"] == "admitted"
    batches, cursor = [], None
    while True:
        batch = admission.pages_for(env.api, env.scope(), decision["id"], cursor=cursor)
        assert len(json.dumps(batch).encode()) <= 512 * 1024
        assert len(json.dumps(batch, ensure_ascii=False).encode()) <= 512 * 1024
        batches.append(batch)
        cursor = batch["cursor"]
        if cursor is None:
            break
    assert len(batches) > 1
    pages = admission.assemble(batches)
    assert "".join(p["text"] for p in pages).startswith("고객사 A ")
    assert env.term not in json.dumps(pages, ensure_ascii=False)
    # A consumer detects any tampering of the concatenation before use.
    broken = json.loads(json.dumps(batches))
    broken[-1]["pages"][-1]["text"] += "x"
    with pytest.raises(AdmissionError):
        admission.assemble(broken)


def test_revocation_between_batches_stops_delivery(env):
    policy = env.policy()
    ref = large_korean_document(env)
    env.provenance(ref, policy)
    decision = admission.request(env.api, env.scope(), ref, data_class="public")
    first = admission.pages_for(env.api, env.scope(), decision["id"])
    assert first["cursor"]
    env.admin({"op": "revoke_provenance", "id": "prov-1", "expectedRevision": 1})
    with pytest.raises(AdmissionError) as error:
        admission.pages_for(env.api, env.scope(), decision["id"], cursor=first["cursor"])
    assert error.value.status == 409


def test_cursor_is_bound_to_actor_and_expires(env):
    policy = env.policy()
    ref = large_korean_document(env)
    env.provenance(ref, policy)
    decision = admission.request(env.api, env.scope(), ref, data_class="public")
    first = admission.pages_for(env.api, env.scope(), decision["id"])
    with pytest.raises(AdmissionError) as error:
        admission.pages_for(env.api, env.scope("bob"), decision["id"], cursor=first["cursor"])
    assert error.value.code == "admission-cursor-stale"
    clock = env.api.storage.clock
    env.api.storage.clock = lambda: clock() + 300_001
    with pytest.raises(AdmissionError) as error:
        admission.pages_for(env.api, env.scope(), decision["id"], cursor=first["cursor"])
    assert error.value.code == "admission-cursor-stale"


# PR #28 review round 1 ----------------------------------------------------------

def test_phone_number_split_across_logical_pages_is_blocked(env):
    """Finding 2: inspection and the residual scan cross logical-page boundaries."""
    env.policy()
    env.grant("bob")
    ref = guide(env, text="가" * 3997 + "010-5550-7391 끝.\n", name="split-phone.txt")
    decision = admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")
    assert decision["status"] == "blocked" and "redaction-required" in decision["blocking"]
    receipt = json.loads(env.api.storage.get_blob(decision["inspection"]["receiptKey"]))
    assert {"type": "PHONE", "count": 1} in receipt["pii"]
    assert "7391" not in json.dumps(receipt)


def test_deny_listed_term_split_across_logical_pages_is_normalized(env):
    """Finding 2: a deny-listed identifier spanning two pages is replaced; page mapping kept."""
    policy = env.policy()
    ref = guide(env, text="가" * (4000 - len(env.term) // 2) + env.term + " 안내.\n", name="split-term.txt")
    env.provenance(ref, policy)
    decision = admission.request(env.api, env.scope(), ref, data_class="public")
    assert decision["status"] == "admitted"
    batch = admission.pages_for(env.api, env.scope(), decision["id"])
    serialized = json.dumps(batch, ensure_ascii=False)
    joined = "".join(entry["text"] for entry in batch["pages"])
    assert env.term not in joined and env.term.lower() not in joined.lower()
    assert "고객사 A" in joined
    assert [entry["page"] for entry in batch["pages"]] == [1, 2]
    assert batch["pages"][0]["text"].startswith("가") and batch["pages"][1]["text"].endswith("안내.\n")
    assert env.term not in serialized
