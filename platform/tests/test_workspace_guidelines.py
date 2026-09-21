import os
import copy
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_workspace_http import call, contract_data
from test_workspace_worker import environment, request, upload
from workspace.guidelines import context_for, normalize_refs, text_hash, validate_pack
from workspace.intake import extract_file
from workspace.prepare_guidelines import archive_entries, pdf_pages, pptx_pages, prepare
from workspace.rules import contract_hash
from workspace.storage import key_for


def pack_data():
    return {"format": "ux-guidelines", "schemaVersion": 1, "sources": [
        {"id": "interaction", "name": "Synthetic interaction.pdf", "sha256": "a" * 64,
         "category": "interaction", "pageCount": 3, "pages": [
             {"page": 1, "text": "Unselected unrelated guidance"},
             {"page": 2, "text": "Keep entered values when returning to the form."},
             {"page": 3, "text": "Show the entered value in the summary."}]},
        {"id": "writing", "name": "Synthetic writing.pptx", "sha256": "b" * 64,
         "category": "writing", "pageCount": 1, "pages": [{"page": 1, "text": "Write a clear action label."}]},
    ]}


def ref_for(asset_id, source=0, page=2, pack=None):
    original = (pack or pack_data())["sources"][source]
    text = next(item["text"] for item in original["pages"] if item["page"] == page)
    return {"assetId": asset_id, "sourceId": original["id"], "page": page,
            "sourceSha256": original["sha256"], "textSha256": text_hash(text)}


def imported(model=None, pack=None):
    storage, api, worker = environment(model=model)
    identifier = upload(api, worker, "synthetic-guidelines.json", json.dumps(pack or pack_data()).encode())
    return storage, api, worker, identifier


def cited_contract(identifier):
    value = contract_data(identifier)
    value["guideRefs"] = [ref_for(identifier)]
    value["rules"][0]["source"] = {"kind": "explicit", "assetId": identifier, "sourceId": "interaction", "page": 2,
                                  "quote": "Keep entered values when returning to the form."}
    return value


def test_pack_extraction_does_not_truncate_later_pages_or_claim_original_storage():
    pack = pack_data()
    pack["sources"][0].update(pageCount=624, pages=[{"page": number, "text": "Synthetic text. " * 80}
                                                   for number in range(1, 625)])
    pack["sources"][0]["pages"][-1]["text"] = "Late chapter: recover user input"
    result = extract_file("guidelines.json", json.dumps(pack).encode())
    assert result["parseStatus"] == "complete" and not result["truncated"]
    assert result["text"] == "" and not result["previews"]
    assert result["guidelinePack"]["sources"][0]["pages"][-1]["text"].startswith("Late chapter")
    assert result["guidelineSources"][0]["originalStatus"] == "local-only"
    assert result["guidelineSources"][0]["reviewStatus"] == "unreviewed"
    assert result["warnings"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(schemaVersion=True),
    lambda value: value["sources"].append(copy.deepcopy(value["sources"][0])),
    lambda value: value["sources"][0]["pages"].append({"page": 2, "text": "duplicate"}),
    lambda value: value["sources"][0]["pages"][0].update(page=True),
    lambda value: value["sources"][0]["pages"][0].update(truncated="false"),
    lambda value: value["sources"][0].update(sha256="unverified"),
    lambda value: value["sources"][0].update(category="executable"),
])
def test_malformed_packs_are_rejected(change):
    value = pack_data()
    change(value)
    with pytest.raises(ValueError):
        validate_pack(value)


@pytest.mark.parametrize("limit", ["sources", "pages", "page-text", "total-text"])
def test_pack_size_limits_fail_closed(limit):
    value = pack_data()
    if limit == "sources":
        value["sources"] = [{**copy.deepcopy(value["sources"][0]), "id": f"source-{i}"} for i in range(21)]
    elif limit == "pages":
        value["sources"][0].update(pageCount=1201, pages=[{"page": i + 1, "text": "x"} for i in range(1201)])
    elif limit == "page-text":
        value["sources"][0]["pages"][0]["text"] = "x" * 20001
    else:
        value["sources"][0].update(pageCount=151, pages=[{"page": i + 1, "text": "x" * 20000} for i in range(151)])
    with pytest.raises(ValueError):
        validate_pack(value)


def test_references_pin_source_and_page_content_and_reject_empty_or_truncated_text():
    pack = validate_pack(pack_data())
    ref = ref_for("asset")
    block, texts = context_for(pack, [ref])
    assert "Keep entered values" in block
    assert "Unselected unrelated" not in block
    assert texts == {("interaction", 2): pack["sources"][0]["pages"][1]["text"]}
    for key, wrong in (("sourceSha256", "c" * 64), ("textSha256", "c" * 64), ("page", 1), ("sourceId", "writing")):
        with pytest.raises(ValueError):
            context_for(pack, [{**ref, key: wrong}])
    with pytest.raises(ValueError):
        normalize_refs([ref, ref], ["asset"])
    with pytest.raises(ValueError):
        normalize_refs([ref], ["different-asset"])
    pack["sources"][0]["pages"][1]["truncated"] = True
    with pytest.raises(ValueError):
        context_for(pack, [ref])


def test_private_page_search_and_archive_enforcement():
    storage, api, worker, identifier = imported()
    status, result, _ = call(api, "GET", f"/assets/{identifier}/guidelines", owner="designer", query={"q": "returning"})
    assert status == 200 and result["total"] == 1
    assert result["pages"][0]["page"] == 2
    assert "guidelinesKey" not in json.dumps(result)
    assert call(api, "GET", f"/assets/{identifier}/guidelines", owner="other")[0] == 404
    assert call(api, "GET", f"/assets/{identifier}/guidelines", owner="designer", query={"cursor": "-1"})[0] == 400
    assert request(api, "DELETE", f"/assets/{identifier}")[0] == 200
    assert call(api, "GET", f"/assets/{identifier}/guidelines", owner="designer")[0] == 409
    assert storage.get_blob(storage.get("designer", "asset", identifier)["originalKey"])


def test_contract_citations_require_the_selected_physical_page_and_edits_invalidate_approval():
    storage, api, worker, identifier = imported()
    value = cited_contract(identifier)
    status, result = request(api, "POST", "/contracts", value)
    assert status == 201, result
    contract = result["contract"]
    status, result = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})
    assert status == 200, result
    approved = result["contract"]
    for source in (
        {**value["rules"][0]["source"], "page": 1},
        {**value["rules"][0]["source"], "quote": "Show the entered value in the summary."},
        {key: item for key, item in value["rules"][0]["source"].items() if key != "sourceId"},
    ):
        invalid = copy.deepcopy(value)
        invalid["rules"][0]["source"] = source
        assert request(api, "POST", "/contracts", invalid)[0] == 400
    edited = {**approved, "guideRefs": [*approved["guideRefs"], ref_for(identifier, page=3)]}
    assert contract_hash(edited) != contract_hash(approved)
    status, result = request(api, "PUT", f"/contracts/{approved['id']}", edited)
    assert status == 200 and result["contract"]["status"] == "draft"
    assert not result["contract"].get("approval")
    assert request(api, "POST", f"/contracts/{approved['id']}/approve", {"version": approved["version"]})[0] == 409


def test_pack_index_integrity_is_checked_before_search_or_generation():
    storage, api, worker, identifier = imported()
    asset = storage.get("designer", "asset", identifier)
    storage.put_blob(asset["guidelinesKey"], b'{"sources":[]}', "application/json")
    assert call(api, "GET", f"/assets/{identifier}/guidelines", owner="designer")[0] == 400
    assert request(api, "POST", "/contracts", cited_contract(identifier))[0] == 400


def test_project_page_reads_recheck_canonical_membership():
    storage, api, worker = environment()
    _, result = request(api, "POST", "/projects", {"name": "Guides", "requestId": "guides-project"})
    project = result["project"]
    owner = f"project:{project['id']}"
    canonical = storage.get(owner, "project", project["id"])
    members = {**canonical["members"], "reader": {"role": "designer", "displayName": "Reader"}}
    canonical = storage.put(owner, "project", {**canonical, "members": members}, canonical["version"])
    encoded = json.dumps(validate_pack(pack_data())).encode()
    key = key_for(owner, "asset", "project-pack", "guidelines.json")
    storage.put_blob(key, encoded, "application/json")
    storage.put(owner, "asset", {"id": "project-pack", "uploadStatus": "stored", "archived": False,
                                 "guidelinesKey": key, "guidelinesSha256": hashlib.sha256(encoded).hexdigest()})
    event = {"rawPath": "/studio-api/assets/project-pack/guidelines", "headers": {"X-Workspace-Project": project["id"]},
             "requestContext": {"http": {"method": "GET"},
                                "authorizer": {"jwt": {"claims": {"sub": "reader", "token_use": "access"}}}}}
    assert api.handle(event)["statusCode"] == 200
    del members["reader"]
    storage.put(owner, "project", {**canonical, "members": members}, canonical["version"])
    denied = api.handle(event)
    assert denied["statusCode"] == 403
    assert "Keep entered values" not in denied["body"]


def test_selected_guideline_pages_remain_complete_at_the_context_boundary():
    pack = pack_data()
    pack["sources"][0]["pages"] = [{"page": number, "text": ("x" * 19_980) + f" end-of-page-{number}"}
                                  for number in range(1, 4)]
    storage, api, worker, identifier = imported(pack=pack)
    selected = [ref_for(identifier, page=number, pack=pack) for number in range(1, 4)]
    earlier = upload(api, worker, "long-guide.txt", b"unrelated " * 17000)
    snapshots = api._snapshot_assets([storage.get("designer", "asset", earlier), storage.get("designer", "asset", identifier)])
    context, _, _, _, warnings, _ = worker._context("designer", snapshots, guide_refs=selected)
    assert all(f"end-of-page-{number}" in context for number in range(1, 4))
    assert context.index("end-of-page-3") < context.index("unrelated")
    assert not any("synthetic-guidelines" in warning and "앞부분" in warning for warning in warnings)


def test_ai_proposal_receives_only_selected_pages_and_preserves_exact_references():
    calls, result_contract = [], {}

    def model(system, user, images, model_id, max_tokens, trace_id, purpose):
        calls.append((system, user, images))
        return json.dumps(result_contract), {}, {"modelId": model_id}

    storage, api, worker, identifier = imported(model=model)
    result_contract.update(cited_contract(identifier))
    status, queued = request(api, "POST", "/contracts/propose", {
        "assetIds": [identifier], "guideRefs": [ref_for(identifier)], "brief": "Preserve form state",
        "model": "global.openai.gpt-6-astra", "requestId": "selected-guideline-page"})
    assert status == 202, queued
    outcome = worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})
    assert outcome["status"] == "completed", storage.get("designer", "job", queued["job"]["id"])
    assert len(calls) == 1 and "Keep entered values" in calls[0][1]
    assert "Unselected unrelated" not in calls[0][1] and "Write a clear action" not in calls[0][1]
    assert "sourceId" in calls[0][0]
    job = storage.get("designer", "job", queued["job"]["id"])
    saved = storage.get("designer", "contract", job["result"]["contractId"])
    assert saved["guideRefs"] == [ref_for(identifier)] and saved["status"] == "draft"
    changed = {"assetIds": [identifier], "guideRefs": [ref_for(identifier, page=3)], "brief": "Preserve form state",
               "model": "global.openai.gpt-6-astra", "requestId": "selected-guideline-page"}
    assert request(api, "POST", "/contracts/propose", changed)[0] == 409


def test_react_generation_builds_and_verifies_with_frozen_guideline_pages(monkeypatch):
    from workspace.react_runtime import evaluate_react
    browser = Path("/home/atomoh/.cache/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell")
    if not os.environ.get("WORKSPACE_CHROMIUM_PATH") and browser.is_file():
        monkeypatch.setenv("WORKSPACE_CHROMIUM_PATH", str(browser))
    pack = pack_data()
    pack["sources"][0]["pages"][1]["text"] = "Display the sample value 100 in the summary."
    prompts = []

    def model(system, user, images, model_id, max_tokens, trace_id, purpose):
        prompts.append(user)
        source = ('import {Screen,Text} from "@studio/approved-ui";'
                  'export default function App(){return <Screen pageId="entry">'
                  '<Text as="h1">합성 예시</Text><Text testId="summary">100</Text></Screen>;}')
        return json.dumps({"files": {"src/App.tsx": source}}), {}, {"modelId": model_id}

    storage, api, worker, identifier = imported(model=model, pack=pack)
    worker.react_call = evaluate_react
    value = cited_contract(identifier)
    value["guideRefs"] = [ref_for(identifier, pack=pack)]
    value["rules"][0]["source"]["quote"] = pack["sources"][0]["pages"][1]["text"]
    status, created = request(api, "POST", "/contracts", value)
    assert status == 201, created
    contract = created["contract"]
    status, approved = request(api, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]})
    assert status == 200, approved
    status, queued = request(api, "POST", "/runs", {
        "contractId": contract["id"], "contractVersion": approved["contract"]["version"], "outputType": "react",
        "model": "global.openai.gpt-6-astra", "maxRounds": 1, "requestId": "guideline-react-build"})
    assert status == 202, queued
    outcome = worker.handle({"owner": "designer", "jobId": queued["job"]["id"]})
    assert outcome["status"] == "completed", storage.get("designer", "job", queued["job"]["id"])
    run = storage.get("designer", "run", queued["run"]["id"])
    assert run["rounds"][0]["passed"] is True
    assert run["contract"]["guideRefs"] == value["guideRefs"]
    assert run["assetSnapshots"][0]["guidelinesSha256"] == storage.get("designer", "asset", identifier)["guidelinesSha256"]
    assert "Display the sample value 100" in prompts[0] and "Unselected unrelated" not in prompts[0]


def pptx_bytes(external=False, dtd=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("ppt/presentation.xml",
                         '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                         '<p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>')
        mode = ' TargetMode="External"' if external else ""
        archive.writestr("ppt/_rels/presentation.xml.rels",
                         f'<Relationships><Relationship Id="first" Target="slides/slide1.xml"/>'
                         f'<Relationship Id="second" Target="slides/slide2.xml"{mode}/></Relationships>')
        for number in (1, 2):
            prefix = '<!DOCTYPE slide [<!ENTITY secret SYSTEM "file:///etc/passwd">]>' if dtd else ""
            archive.writestr(f"ppt/slides/slide{number}.xml", prefix +
                             '<slide xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                             f'<a:p><a:r><a:t>Logical text {number}</a:t></a:r></a:p></slide>')
    return output.getvalue()


def test_slide_order_uses_presentation_relationships_and_external_or_entity_data_is_rejected():
    assert [page["text"] for page in pptx_pages(pptx_bytes())] == ["Logical text 2", "Logical text 1"]
    for data in (pptx_bytes(external=True), pptx_bytes(dtd=True)):
        with pytest.raises(ValueError):
            pptx_pages(data)


def pdf_bytes():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for number in range(25):
        page = writer.add_blank_page(width=390, height=844)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 800 Td (Guidance page {number + 1}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_local_preparation_keeps_late_pdf_pages_and_hashes_without_copying_original_bytes(tmp_path):
    pdf = pdf_bytes()
    assert len(pdf_pages(pdf)) == 25
    archive_path = tmp_path / "guides.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Guides/design.pdf", pdf)
        archive.writestr("Guides/interaction.pptx", pptx_bytes())
    pack = prepare(archive_path)
    assert pack["sources"][0]["pages"][-1]["text"].startswith("Guidance page 25")
    assert pack["sources"][0]["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert "originalBytes" not in json.dumps(pack)
    with zipfile.ZipFile(tmp_path / "unsafe.zip", "w") as archive:
        archive.writestr("../outside.pdf", pdf)
    with zipfile.ZipFile(tmp_path / "unsafe.zip") as archive, pytest.raises(ValueError):
        archive_entries(archive)
