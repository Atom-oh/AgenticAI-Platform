"""Diagram and table transcription for guidelines (Task I6b; O-02; review round 8, AB5)."""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_intake_admission import DAY, env  # noqa: F401,E402
from intake_support import api, call  # noqa: F401,E402
from engine import gate  # noqa: E402
from intake import admission, imaging, review, transcription  # noqa: E402
from intake.admission import AdmissionError  # noqa: E402
from intake.records import INTAKE_OWNER  # noqa: E402
from workspace.collaboration import CollaborationError  # noqa: E402
from workspace.ontology_sources import Sources, asset_reference  # noqa: E402

MODEL = "global.anthropic.claude-fable-5-1"
DIAGRAM_TEXT = "자격 미충족 시 사유 화면"


class VisionAdapter:
    tier = "0/1"
    model_id = MODEL

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def converse_with_tools(self, system, messages, tools, **kwargs):
        self.calls.append((messages, kwargs))
        return {"output": {"message": {"content": [{"text": self.reply}]}},
                "usage": {"inputTokens": 10, "outputTokens": 5}}


def flowchart_png():
    image = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 140, 70), outline="black")
    draw.rectangle((180, 120, 300, 170), outline="black")
    draw.line((80, 70, 240, 120), fill="black", width=2)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def image_asset(env, identifier="diagram"):
    data = flowchart_png()
    owner = f"project:{env.pid}"
    key = env.api.storage.key_for(owner, "asset", identifier, "original.png")
    env.api.storage.put_blob_once(key, data, "image/png")
    row = env.api.storage.put(owner, "asset", {
        "id": identifier, "projectId": env.pid, "name": identifier + ".png", "uploadStatus": "stored",
        "parseStatus": "complete", "originalKey": key, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "importRevision": 1})
    return asset_reference(row)


def ocr(data):
    assert data.startswith(b"\x89PNG")
    return {"status": "complete", "text": DIAGRAM_TEXT}


@pytest.fixture
def chain(env, monkeypatch):
    """Admission -> vision derivative -> transcription -> reviewer validation -> library revision."""
    env.policy()
    env.grant("bob", "grant-bob")      # validates transcriptions and approves library revisions
    env.grant("dana", "grant-dana")    # admits the original image
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    assert pending["status"] == "pending-review"
    image = review.decide(env.api, env.scope("dana"), pending["id"], approve=True, reason="image checked")
    request = transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
        "left": 10, "top": 10, "width": 300, "height": 180, "normalizedImageHash": image["artifact"]["sha256"]})
    adapter = VisionAdapter(json.dumps({"kind": "diagram", "text": f"{env.term} 가입 흐름: {DIAGRAM_TEXT}",
                                        "tables": [["조건", "화면"], ["자격 미충족", "사유 화면"]]},
                                       ensure_ascii=False))
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    transcribed = transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    assert transcribed["status"] == "pending-review" and transcribed["lineage"]["decisionId"] == image["id"]
    validated = review.decide(env.api, env.scope("bob"), transcribed["id"], approve=True, reason="matches image")
    assert validated["status"] == "admitted"
    from documents.library import fingerprint
    did = "d-" + fingerprint(["intake-transcription", validated["id"]])[:40]
    owner = f"project:{env.pid}"
    document = env.api.storage.get(owner, "document", did)
    revision = env.api.storage.get(owner, "docrevision", did + "--r000001")
    env.chain = dict(ref=ref, image=image, adapter=adapter, transcription=validated, document=document,
                     revision=revision, owner=owner)
    return env


def doc_ref(env):
    document = env.api.storage.get(env.chain["owner"], "document", env.chain["document"]["id"])
    revision = env.api.storage.get(env.chain["owner"], "docrevision", env.chain["revision"]["id"])
    return {"sourceKind": "document-revision", "sourceId": document["id"], "revision": revision["id"],
            "sha256": revision["sha256"], "audienceRevision": str(document["aclVersion"])}


def library_review(env, actor):
    revision = env.api.storage.get(env.chain["owner"], "docrevision", env.chain["revision"]["id"])
    path = f"/documents/{revision['documentId']}/revisions/{revision['id']}/review"
    return env.http("POST", path, {"version": revision["version"], "decision": "approved", "note": "ok"}, actor=actor)


def revoke_image_asset(env):
    owner = env.chain["owner"]
    asset = env.api.storage.get(owner, "asset", env.chain["ref"]["sourceId"])
    env.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])


def test_vision_derivative_goes_through_the_real_gate_with_ocr_and_admitted_bytes(chain):
    messages, kwargs = chain.chain["adapter"].calls[0]
    image_block = messages[0]["content"][-1]["image"]
    _, expected = imaging.vision_input(chain.api, chain.scope(), chain.chain["image"]["id"])
    assert image_block["source"]["bytes"] == expected["bytes"] and kwargs["model"] == MODEL
    assert imaging.descriptor(chain.api, chain.scope(), chain.chain["image"]["id"]) == {
        "format": "png", "ocrStatus": "complete", "ocrText": DIAGRAM_TEXT,
        "visionSha256": chain.chain["image"]["artifact"]["vision"]["sha256"],
        "size": chain.chain["image"]["artifact"]["vision"]["size"]}


def test_published_transcription_is_an_in_review_md_revision_with_server_owned_lineage(chain):
    document, revision = chain.chain["document"], chain.chain["revision"]
    assert document["kind"] == "guide-transcription" and document["readRoles"] == ["owner", "planner", "designer",
                                                                                     "developer"]
    assert revision["status"] == "in_review" and revision["name"] == "transcription.md"
    lineage = revision["transcriptionOf"]
    image = chain.chain["image"]
    transcribed = chain.chain["transcription"]
    assert lineage == {"sourceRef": image["source"], "decisionId": image["id"], "decisionRevision": image["revision"],
                       "transcription": {"decisionId": transcribed["id"], "decisionRevision": transcribed["revision"],
                                         "artifactHash": transcribed["derivation"]["derivativeHash"]},
                       "visionSha256": image["artifact"]["vision"]["sha256"],
                       "normalizedImageHash": image["artifact"]["sha256"],
                       "region": {"left": 10, "top": 10, "width": 300, "height": 180}}
    original = chain.api.storage.get_blob(revision["originalKey"]).decode()
    assert "고객사 A 가입 흐름" in original and chain.term not in original
    assert "| 자격 미충족 | 사유 화면 |" in original


def test_publish_transcription_outside_intake_review_is_refused(chain):
    from documents.library import prepare_transcription
    with pytest.raises(PermissionError):
        prepare_transcription(chain.api, chain.scope(), chain.chain["transcription"])


def test_public_document_api_cannot_supply_lineage(chain):
    status, _ = chain.http("POST", "/documents", {
        "requestId": "forged", "title": "x", "kind": "guide-transcription", "name": "x.md", "size": 1,
        "sha256": "a" * 64, "transcriptionOf": {"decisionId": "x"}})
    assert status == 201  # unknown fields are ignored, never stored
    rows = chain.api.storage.list(chain.chain["owner"], "docrevision")
    assert sum("transcriptionOf" in row for row in rows) == 1


def test_library_approval_is_separate_designer_denied_planner_approves(chain):
    status, payload = library_review(chain, "carol")
    assert status == 403, payload
    status, payload = library_review(chain, "bob")
    assert status == 200, payload
    assert payload["revision"]["status"] == "approved"


def test_unreviewed_transcription_never_becomes_a_library_source(env, monkeypatch):
    env.policy()
    env.grant("bob", "grant-bob")
    env.grant("dana", "grant-dana")
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    image = review.decide(env.api, env.scope("dana"), pending["id"], approve=True, reason="ok")
    request = transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
        "left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": image["artifact"]["sha256"]})
    adapter = VisionAdapter(json.dumps({"kind": "table", "text": "표", "tables": []}))
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    transcribed = transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    assert transcribed["status"] == "pending-review"
    assert env.api.storage.list(f"project:{env.pid}", "document") == []
    with pytest.raises(AdmissionError):
        admission.pages_for(env.api, env.scope(), transcribed["id"])


def test_region_is_validated_on_the_normalized_image(env):
    env.policy()
    env.grant("dana", "grant-dana")
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    with pytest.raises(AdmissionError) as error:  # not yet admitted
        transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
            "left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": pending["artifact"]["sha256"]})
    assert error.value.code == "image-admission-required"
    image = review.decide(env.api, env.scope("dana"), pending["id"], approve=True, reason="ok")
    for region in ({"left": 20, "top": 0, "width": 320, "height": 10, "normalizedImageHash": image["artifact"]["sha256"]},
                   {"left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": "0" * 64}):
        with pytest.raises(AdmissionError) as error:
            transcription.request_diagram(env.api, env.scope(), ref, page=1, region=region)
        assert error.value.code == "invalid-region"


def test_image_with_ocr_identifiers_or_missing_ocr_is_blocked(env):
    env.policy()
    ref = image_asset(env)
    assert imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=None) == {
        "status": "blocked", "blocking": ["ocr-incomplete"]}
    blocked = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive",
                                  ocr=lambda data: {"status": "complete", "text": "연락처 010-5550-7391"})
    assert blocked["status"] == "blocked" and "redaction-required" in blocked["blocking"]
    named = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive",
                                ocr=lambda data: {"status": "complete", "text": env.term})
    assert named["status"] == "blocked" and "residual-identifiers" in named["blocking"]


def test_transcription_resolves_with_globally_owned_upstream_observations(chain):
    assert library_review(chain, "bob")[0] == 200
    from workbench.service import Service
    reader = Sources(Service(chain.api, chain.scope(), {"sub": "alice"}))
    value = reader.resolve(doc_ref(chain), text=True)
    assert "사유 화면" in value["text"]
    observed = set(reader.observed)
    image = chain.chain["image"]
    assert (INTAKE_OWNER, "adm_policy", "policy-1") in observed
    assert (INTAKE_OWNER, "adm_grant", "grant-dana") in observed
    assert (chain.chain["owner"], "adm_decision", image["id"]) in observed
    assert (chain.chain["owner"], "asset", image["source"]["sourceId"]) in observed
    assert reader.recheck()
    policy = chain.api.storage.get(INTAKE_OWNER, "adm_policy", "policy-1")
    chain.admin({"op": "retire_policy", "id": "policy-1", "expectedRevision": policy["revision"]})
    with pytest.raises(CollaborationError):
        reader.recheck()


@pytest.mark.parametrize("revocation", ["asset", "image-decision"])
def test_upstream_revocation_blocks_resolve_new_admission_and_inflight_transfer(chain, revocation):
    assert library_review(chain, "bob")[0] == 200
    # An in-flight admission of the approved transcription revision.
    pending = admission.request(chain.api, chain.scope(), doc_ref(chain), data_class="internal-non-sensitive")
    admitted = review.decide(chain.api, chain.scope("bob"), pending["id"], approve=True, reason="ok")
    first = admission.pages_for(chain.api, chain.scope(), admitted["id"])
    assert "사유 화면" in first["pages"][0]["text"]
    if revocation == "asset":
        revoke_image_asset(chain)
    else:
        chain.admin({"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1})
    from workbench.service import Service
    with pytest.raises(CollaborationError) as error:
        Sources(Service(chain.api, chain.scope(), {"sub": "alice"})).resolve(doc_ref(chain))
    assert error.value.code == "source-upstream-revoked"
    with pytest.raises(CollaborationError) as error:
        admission.request(chain.api, chain.scope(), doc_ref(chain), data_class="internal-non-sensitive")
    assert error.value.code == "source-upstream-revoked"
    with pytest.raises(AdmissionError) as error:
        admission.pages_for(chain.api, chain.scope(), admitted["id"])
    assert error.value.status == 409


def test_library_read_paths_enforce_upstream_revocation(chain):
    revision = chain.chain["revision"]
    base = f"/documents/{revision['documentId']}/revisions/{revision['id']}"
    assert chain.http("GET", base)[0] == 200
    status, _, headers = call(chain.api, "GET", base + "/blob", project=chain.pid)
    assert status == 200 and headers["X-Total-Size"] == str(revision["size"])
    revoke_image_asset(chain)
    for path, query in ((base, None), (base + "/blob", {"offset": "0"})):
        status, payload, _ = call(chain.api, "GET", path, project=chain.pid, query=query)
        assert status == 409 and payload["code"] == "source-upstream-revoked"
    status, payload = library_review(chain, "bob")
    assert status == 409 and payload["code"] == "source-upstream-revoked"


@pytest.mark.parametrize("race", ["asset", "policy", "grant"])
def test_revocation_between_projection_and_approval_commit_aborts(chain, monkeypatch, race):
    from documents.library import Library
    original = Library.projection
    fired = []

    def projection(self, document, revision):
        result = original(self, document, revision)
        if not fired and revision.get("transcriptionOf"):
            fired.append(True)
            if race == "asset":
                revoke_image_asset(chain)
            else:
                kind, identifier = ("adm_policy", "policy-1") if race == "policy" else ("adm_grant", "grant-dana")
                row = chain.api.storage.get(INTAKE_OWNER, kind, identifier)
                chain.api.storage.put(INTAKE_OWNER, kind, {**row, "status": "retired" if race == "policy"
                                                           else "revoked"}, row["version"])
        return result

    monkeypatch.setattr(Library, "projection", projection)
    status, payload = library_review(chain, "bob")
    assert fired and status == 409, payload
    assert chain.api.storage.get(chain.chain["owner"], "docrevision", chain.chain["revision"]["id"])["status"] == "in_review"


def rule_graph(env, ref):
    from test_ontology_schema import node
    rule = node("reason-screen-rule", "PolicyRule", project=env.pid,
                sourceRefs=[{**ref, "location": {"page": 1}}],
                properties={"ruleId": "R-1", "statement": "자격 미충족 시 사유 화면을 표시한다.", "required": True})
    return {"schemaVersion": 1, "projectId": env.pid, "nodes": [rule], "edges": []}


def test_transcribed_rule_publishes_and_a_planner_approves_it(chain):
    from workbench.service import Service
    from workspace.ontology_store import Ontology
    assert library_review(chain, "bob")[0] == 200
    ref = doc_ref(chain)
    # The approved transcription is consumed like any other admitted page.
    pending = admission.request(chain.api, chain.scope(), ref, data_class="internal-non-sensitive")
    admitted = review.decide(chain.api, chain.scope("bob"), pending["id"], approve=True, reason="ok")
    pages = admission.pages_for(chain.api, chain.scope(), admitted["id"])["pages"]
    assert pages[0]["page"] == 1 and DIAGRAM_TEXT.split()[-1] in pages[0]["text"]
    ontology = Ontology(Service(chain.api, chain.scope(), {"sub": "alice"}))
    published = ontology.publish_candidate("guide-rules", rule_graph(chain, ref), expected_generation=None,
                                           request_id="rules-1")
    identity = published["identities"]["reason-screen-rule"]
    planner = Ontology(Service(chain.api, chain.scope("bob"), {"sub": "bob"}))
    reviewed = planner.review_node(identity, expected_generation=published["generation"], revision=1,
                                   decision="reviewed", reason="checked", request_id="rv-1")
    approved = Ontology(Service(chain.api, chain.scope("bob"), {"sub": "bob"})).review_node(
        identity, expected_generation=reviewed["generation"], revision=1, decision="approved", reason="ok",
        request_id="rv-2")
    assert approved["review"]["decision"] == "approved"


def test_policy_retired_immediately_before_the_publish_transaction_publishes_nothing(chain, monkeypatch):
    from workbench.service import Service
    from workspace.ontology_store import CURRENT, Ontology
    assert library_review(chain, "bob")[0] == 200
    ref = doc_ref(chain)
    storage = chain.api.storage
    original = storage.put_many

    def put_many(writes, checks=None, **kwargs):
        if any(w["kind"] == "ontology" for w in writes):
            row = storage.get(INTAKE_OWNER, "adm_policy", "policy-1")
            storage.put(INTAKE_OWNER, "adm_policy", {**row, "status": "retired"}, row["version"])
        return original(writes, checks, **kwargs)

    monkeypatch.setattr(storage, "put_many", put_many)
    with pytest.raises(CollaborationError) as error:
        Ontology(Service(chain.api, chain.scope(), {"sub": "alice"})).publish_candidate(
            "guide-rules", rule_graph(chain, ref), expected_generation=None, request_id="rules-2")
    assert error.value.status == 409
    assert storage.get(chain.chain["owner"], "ontology", CURRENT) is None


# PR #28 review round 1 ----------------------------------------------------------

def test_image_reviewer_revocation_invalidates_the_transcription_admission(chain):
    """Finding 4: a transcription's verification recursively verifies its image lineage."""
    transcribed = chain.chain["transcription"]
    assert admission.verify(chain.api, chain.scope(), transcribed["id"])["id"] == transcribed["id"]
    chain.admin({"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1})
    with pytest.raises(AdmissionError) as error:
        admission.verify(chain.api, chain.scope(), transcribed["id"])
    assert error.value.status == 409


def test_transcription_reviewer_revocation_fences_the_library_revision(chain):
    """Finding 4: the library revision binds both admissions and fences all upstream authority."""
    revision = chain.chain["revision"]
    lineage = revision["transcriptionOf"]
    transcribed = chain.chain["transcription"]
    assert lineage["transcription"] == {"decisionId": transcribed["id"], "decisionRevision": transcribed["revision"],
                                        "artifactHash": transcribed["derivation"]["derivativeHash"]}
    base = f"/documents/{revision['documentId']}/revisions/{revision['id']}"
    assert chain.http("GET", base)[0] == 200
    assert library_review(chain, "bob")[0] == 200
    from workbench.service import Service
    reader = Sources(Service(chain.api, chain.scope(), {"sub": "alice"}))
    reader.resolve(doc_ref(chain), text=True)
    assert (INTAKE_OWNER, "adm_grant", "grant-bob") in set(reader.observed)
    assert (chain.chain["owner"], "adm_decision", transcribed["id"]) in set(reader.observed)
    chain.admin({"op": "revoke_grant", "id": "grant-bob", "expectedRevision": 1})
    status, payload = chain.http("GET", base)
    assert status == 409 and payload["code"] == "source-upstream-revoked"
    with pytest.raises(CollaborationError):
        reader.recheck()


def test_review_queue_previews_images_and_transcriptions_by_artifact_kind(env, monkeypatch):
    """Finding 6: a pending image never breaks GET /intake/reviews; transcriptions show text/tables."""
    env.policy()
    env.grant("bob", "grant-bob")
    env.grant("dana", "grant-dana")
    ref = image_asset(env)
    pending_image = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    assert pending_image["status"] == "pending-review"
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert status == 200, listed
    [item] = listed["reviews"]
    assert item["artifactKind"] == "image" and item["pageCount"] == 1
    assert item["derivativePreview"] == DIAGRAM_TEXT
    assert item["image"] == {"decisionId": pending_image["id"], "format": "png",
                             "width": pending_image["artifact"]["width"],
                             "height": pending_image["artifact"]["height"],
                             "sha256": pending_image["artifact"]["sha256"],
                             "visionSha256": pending_image["artifact"]["vision"]["sha256"],
                             "size": pending_image["artifact"]["vision"]["size"]}
    assert "bytes" not in json.dumps(item)
    image = review.decide(env.api, env.scope("dana"), pending_image["id"], approve=True, reason="ok")
    request = transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
        "left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": image["artifact"]["sha256"]})
    adapter = VisionAdapter(json.dumps({"kind": "table", "text": f"{env.term} 표 전사",
                                        "tables": [["조건", "화면"], ["자격 미충족", "사유 화면"]]},
                                       ensure_ascii=False))
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    transcribed = transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    assert transcribed["status"] == "pending-review"
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert status == 200, listed
    [item] = [r for r in listed["reviews"] if r["id"] == transcribed["id"]]
    assert item["artifactKind"] == "diagram-transcription" and item["pageCount"] == 1
    assert item["derivativePreview"] == "고객사 A 표 전사"
    assert item["tables"] == [["조건", "화면"], ["자격 미충족", "사유 화면"]]
    assert env.term not in json.dumps(listed, ensure_ascii=False)


# PR #28 review round 2 ----------------------------------------------------------

def revoke_during_read(env, monkeypatch, suffix, action, occurrence=1):
    """Run `action` right after the `occurrence`-th blob read whose key ends with `suffix`."""
    storage, fired, seen = env.api.storage, [], []
    original = storage.get_blob

    def get_blob(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key.endswith(suffix):
            seen.append(key)
            if len(seen) == occurrence:
                fired.append(key)
                action()
        return data

    monkeypatch.setattr(storage, "get_blob", get_blob)
    return fired


@pytest.mark.parametrize("path, suffix", [("chunk", "vision.png"), ("vision", "ocr.json"), ("descriptor", "ocr.json")])
def test_grant_revoked_during_the_last_read_blocks_image_delivery(chain, monkeypatch, path, suffix):
    """Review 2, finding 1: every non-page delivery path rechecks authority after its last read."""
    image = chain.chain["image"]
    # The first read of `suffix` is inside `verify`; the second is the delivery read.
    fired = revoke_during_read(chain, monkeypatch, suffix, lambda: chain.admin(
        {"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1}), occurrence=2)
    calls = {"chunk": lambda: imaging.read_vision_chunk(chain.api, chain.scope(), image["id"], 0),
             "vision": lambda: imaging.vision_input(chain.api, chain.scope(), image["id"]),
             "descriptor": lambda: imaging.descriptor(chain.api, chain.scope(), image["id"])}
    with pytest.raises(AdmissionError) as error:
        calls[path]()
    assert fired and error.value.status == 409


def test_grant_revoked_during_the_review_queue_read_hides_the_item(env, monkeypatch):
    """Review 2, finding 1: the review queue rechecks the grant after its last read."""
    env.policy()
    env.grant("bob", "grant-bob")
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    assert pending["status"] == "pending-review"
    fired = revoke_during_read(env, monkeypatch, "ocr.json", lambda: env.admin(
        {"op": "revoke_grant", "id": "grant-bob", "expectedRevision": 1}))
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert fired and (status == 403 or listed["reviews"] == [])


@pytest.mark.parametrize("path", ["download", "approval"])
def test_admission_expiring_during_the_original_read_blocks_download_and_approval(chain, monkeypatch, path):
    """Review 2, finding 2: library fences revalidate upstream status and expiry at the final read/commit."""
    revision = chain.chain["revision"]
    storage = chain.api.storage
    clock = storage.clock

    def expire():
        storage.clock = lambda: clock() + 31 * DAY

    fired = revoke_during_read(chain, monkeypatch, revision["originalKey"], expire)
    if path == "download":
        base = f"/documents/{revision['documentId']}/revisions/{revision['id']}"
        status, payload, _ = call(chain.api, "GET", base + "/blob", project=chain.pid, query={"offset": "0"})
    else:
        status, payload = library_review(chain, "bob")
    assert fired and status == 409, payload
    assert payload["code"] == "source-upstream-revoked"
    storage.clock = clock
    saved = storage.get(chain.chain["owner"], "docrevision", revision["id"])
    assert saved["status"] == "in_review"


def test_transcription_approval_and_library_publication_are_atomic(env, monkeypatch):
    """Review 2, finding 4: a failed publication leaves the decision pending and retryable."""
    from workspace.storage import Conflict
    env.policy()
    env.grant("bob", "grant-bob")
    env.grant("dana", "grant-dana")
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    image = review.decide(env.api, env.scope("dana"), pending["id"], approve=True, reason="ok")
    request = transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
        "left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": image["artifact"]["sha256"]})
    adapter = VisionAdapter(json.dumps({"kind": "table", "text": "표 전사", "tables": [["a", "b"]]},
                                       ensure_ascii=False))
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    transcribed = transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    storage, owner = env.api.storage, f"project:{env.pid}"
    original = storage.put_many

    def failing(writes, checks=None, **kwargs):
        if any(w["kind"] == "document" for w in writes):
            raise Conflict("injected publication conflict")
        return original(writes, checks, **kwargs)

    monkeypatch.setattr(storage, "put_many", failing)
    status, payload = env.http("POST", f"/intake/reviews/{transcribed['id']}",
                               {"approve": True, "reason": "matches"}, actor="bob")
    assert status == 409, payload
    assert storage.get(owner, "adm_decision", transcribed["id"])["status"] == "pending-review"
    assert storage.list(owner, "document") == []
    assert [r["id"] for r in env.http("GET", "/intake/reviews", actor="bob")[1]["reviews"]] == [transcribed["id"]]
    monkeypatch.setattr(storage, "put_many", original)
    status, payload = env.http("POST", f"/intake/reviews/{transcribed['id']}",
                               {"approve": True, "reason": "matches"}, actor="bob")
    assert status == 200 and payload["decision"]["status"] == "admitted", payload
    [document] = storage.list(owner, "document")
    revision = storage.get(owner, "docrevision", document["id"] + "--r000001")
    assert revision["status"] == "in_review"
    assert revision["transcriptionOf"]["transcription"]["decisionId"] == transcribed["id"]
    assert revision["transcriptionOf"]["transcription"]["decisionRevision"] == payload["decision"]["revision"]


# PR #28 review round 3 ----------------------------------------------------------

def _transcription_request(env, monkeypatch, reply=None):
    env.policy()
    env.grant("bob", "grant-bob")
    env.grant("dana", "grant-dana")
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    image = review.decide(env.api, env.scope("dana"), pending["id"], approve=True, reason="ok")
    request = transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
        "left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": image["artifact"]["sha256"]})
    adapter = VisionAdapter(reply or json.dumps({"kind": "table", "text": "표 전사", "tables": [["a", "b"]]},
                                                ensure_ascii=False))
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    return ref, image, request, adapter


def _transcriptions(env):
    return [row for row in env.api.storage.list(f"project:{env.pid}", "adm_decision")
            if row["artifact"]["kind"] == "diagram-transcription"]


def test_image_revoked_during_the_denylist_read_never_reaches_the_model(env, monkeypatch):
    """Review 3, finding 1: image authority is rechecked immediately before `generate`."""
    _, _, request, adapter = _transcription_request(env, monkeypatch)
    loader = env.api.intake_denylist_loader

    def revoking():
        env.admin({"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1})
        return loader()

    env.api.intake_denylist_loader = revoking
    with pytest.raises(AdmissionError) as error:
        transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    assert error.value.status == 409
    assert adapter.calls == [] and _transcriptions(env) == []


def test_image_revoked_during_generation_publishes_no_transcription(env, monkeypatch):
    """Review 3, finding 1: image authority is rechecked again before the result is recorded."""
    _, _, request, adapter = _transcription_request(env, monkeypatch)
    converse = adapter.converse_with_tools

    def revoking(*args, **kwargs):
        result = converse(*args, **kwargs)
        env.admin({"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1})
        return result

    adapter.converse_with_tools = revoking
    with pytest.raises(AdmissionError) as error:
        transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    assert error.value.status == 409
    assert len(adapter.calls) == 1 and _transcriptions(env) == []


def test_grant_revoked_during_a_later_item_read_returns_no_earlier_preview(env, monkeypatch):
    """Review 3, finding 3: the complete review response is rechecked after all reads."""
    from test_intake_admission import guide
    env.policy()
    env.grant("bob", "grant-bob")
    for name in ("first.txt", "second.txt"):
        ref = guide(env, text=f"{name} 정기예금 안내.\n", name=name, request=name)
        assert admission.request(env.api, env.scope(), ref, data_class="internal-non-sensitive")["status"] == \
            "pending-review"
    fired = revoke_during_read(env, monkeypatch, "inspection.json", lambda: env.admin(
        {"op": "revoke_grant", "id": "grant-bob", "expectedRevision": 1}), occurrence=2)
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert fired and (status == 403 or listed["reviews"] == []), listed


def test_pending_transcription_preview_requires_its_image_admission(env, monkeypatch):
    """Review 3, finding 4: pending transcription previews verify the image lineage."""
    _, _, request, _ = _transcription_request(env, monkeypatch)
    transcribed = transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    assert transcribed["status"] == "pending-review"
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert [r["id"] for r in listed["reviews"]] == [transcribed["id"]]
    env.admin({"op": "revoke_grant", "id": "grant-dana", "expectedRevision": 1})
    status, listed = env.http("GET", "/intake/reviews", actor="bob")
    assert status == 200 and listed["reviews"] == []


def test_reviewer_grant_expiring_during_publication_commits_nothing(env, monkeypatch):
    """Review 3, finding 5: the publication guard covers the pending decision and the reviewing grant."""
    env.policy()
    env.admin({"op": "grant_reviewer", "record": {
        "id": "grant-bob", "actor": "bob", "policyId": "policy-1", "scope": {"projectIds": [env.pid]},
        "operations": ["review-internal"], "expiresAt": env.api.storage.clock() + 2 * DAY}})
    env.grant("dana", "grant-dana")
    ref = image_asset(env)
    pending = imaging.admit_image(env.api, env.scope(), ref, data_class="internal-non-sensitive", ocr=ocr)
    image = review.decide(env.api, env.scope("dana"), pending["id"], approve=True, reason="ok")
    request = transcription.request_diagram(env.api, env.scope(), ref, page=1, region={
        "left": 0, "top": 0, "width": 10, "height": 10, "normalizedImageHash": image["artifact"]["sha256"]})
    adapter = VisionAdapter(json.dumps({"kind": "table", "text": "표 전사", "tables": [["a", "b"]]},
                                       ensure_ascii=False))
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    transcribed = transcription.transcribe(env.api, env.scope(), request, model_id=MODEL)
    storage, owner = env.api.storage, f"project:{env.pid}"
    clock = storage.clock

    def expire():
        storage.clock = lambda: clock() + 3 * DAY  # past grant-bob only

    # 1st read: the review's artifact check; 2nd: prepare_transcription's derivative read.
    fired = revoke_during_read(env, monkeypatch, "transcription.json", expire, occurrence=2)
    status, payload = env.http("POST", f"/intake/reviews/{transcribed['id']}",
                               {"approve": True, "reason": "matches"}, actor="bob")
    assert fired and status == 409, payload
    storage.clock = clock
    assert storage.get(owner, "adm_decision", transcribed["id"])["status"] == "pending-review"
    assert storage.list(owner, "document") == []
