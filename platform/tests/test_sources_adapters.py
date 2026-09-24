"""Installed `run-round` and `ux-contract` source authority adapters (B0 sharing, Tasks H1-H2).

Acceptance: ONT-04 (source kinds), AUTH-08 (upstream revocation), HAND-02-05
(source-binding parts). Real Storage/CAS, document library and intake admission.
"""
from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_support import api, call  # noqa: F401,E402
from test_intake_admission import env, internal_admitted  # noqa: F401,E402
from test_workspace_react_runtime import CONTRACT  # noqa: E402
from intake import admission  # noqa: E402
from workbench.service import Service  # noqa: E402
from workspace import rules  # noqa: E402
from workspace.collaboration import CollaborationError  # noqa: E402
from workspace.ontology_sources import PROJECT_AUDIENCE, Sources, run_round_reference  # noqa: E402
from workspace.react_artifacts import files_hash  # noqa: E402
from workspace.storage import key_for  # noqa: E402

APP = b"export default function App(){return null}\n"
PRODUCT = {"title": "Synthetic savings", "description": "d", "conditions": [{"id": "c1", "text": "age"}],
           "steps": [{"id": "s1", "title": "input", "description": ""}], "notices": []}


def ctx(env, actor="alice"):
    scope = env.api.collaboration.resolve_scope(actor, env.pid)
    return Service(env.api, scope, {"sub": actor, "exp": int(time.time()) + 3600})


def http(env, method, path, body=None, actor="alice"):
    status, payload, _ = call(env.api, method, path, body, actor=actor, project=env.pid)
    return status, payload


def published_product(env, request="product-1"):
    status, payload = http(env, "POST", "/products", {**PRODUCT, "requestId": request}, actor="bob")
    assert status == 201, payload
    product = payload["product"]
    status, payload = http(env, "POST", f"/products/{product['id']}/publish", {"version": product["version"]},
                           actor="bob")
    assert status == 200, payload
    return payload["product"]


def republish(env, product):
    product = env.api.storage.get(f"project:{env.pid}", "product", product["id"])
    status, payload = http(env, "PUT", f"/products/{product['id']}",
                           {**PRODUCT, "description": "changed", "version": product["version"]}, actor="bob")
    assert status == 200, payload
    status, payload = http(env, "POST", f"/products/{product['id']}/publish",
                           {"version": payload["product"]["version"]}, actor="bob")
    assert status == 200, payload


def approved_contract(env, product):
    status, payload = http(env, "POST", "/contracts", {**CONTRACT, "productId": product["id"]}, actor="carol")
    assert status == 201, payload
    contract = payload["contract"]
    status, payload = http(env, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                           actor="carol")
    assert status == 200, payload
    return payload["contract"]


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for name in sorted(files):
            target.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), files[name])
    return buffer.getvalue()


def react_run(env, contract, *, run_id="run-1", status="completed", passed=True, admissions=None,
              approval=False, blocking=(), output="react"):
    owner = f"project:{env.pid}"
    files = {"src/App.tsx": APP, "package.json": b"{}\n"}
    data = archive(files)
    key = key_for(owner, "run", run_id, "rounds/1-source.zip")
    env.api.storage.put_blob_once(key, data, "application/zip")
    import hashlib
    row = {"number": 1, "passed": passed, "sourceHash": files_hash(files), "sourceKey": key,
           "sourceArchiveSha256": hashlib.sha256(data).hexdigest(), "blockingFindings": list(blocking),
           "catalogHash": contract["catalogHash"]}
    if admissions is not None:
        row["designManifestInput"] = {"admissions": admissions}
    normalized = rules.validate_contract(contract)
    run = {"id": run_id, "projectId": env.pid, "status": status, "outputType": output,
           "contractId": contract["id"], "contractVersion": contract["version"],
           "contractHash": contract["approval"]["hash"], "contract": normalized, "rounds": [row], "bestRound": 1,
           **{k: contract[k] for k in ("productId", "guidelineId", "guidelineAssetId", "ontologyHash",
                                        "catalogHash")}}
    if approval:
        run["approval"] = {"round": 1, "sourceHash": row["sourceHash"], "contractHash": run["contractHash"]}
    return env.api.storage.put(owner, "run", run)


@pytest.fixture
def design(env):
    product = published_product(env)
    contract = approved_contract(env, product)
    return product, contract


def code(call_, *args, **kwargs):
    with pytest.raises(CollaborationError) as error:
        call_(*args, **kwargs)
    return error.value.status, error.value.code


# H1: run-round ---------------------------------------------------------------

def test_run_round_reference_resolves_with_exact_source_text(env, design):
    _, contract = design
    run = react_run(env, contract)
    ref = run_round_reference(run, 1)
    assert ref == {"sourceKind": "run-round", "sourceId": run["id"], "revision": "1",
                   "sha256": run["rounds"][0]["sourceHash"], "audienceRevision": PROJECT_AUDIENCE}
    reader = Sources(ctx(env))
    value = reader.resolve(ref)
    assert value["kind"] == "run-round" and value["text"] is None and value["record"]["number"] == 1
    located = {**ref, "location": {"round": 1, "path": "src/App.tsx"}}
    assert reader.resolve(located, text=True)["text"] == APP.decode()
    assert reader.recheck()
    released = reader.release_source(ref)
    assert set(released["files"]) == {"src/App.tsx", "package.json"}


def test_run_round_wrong_hash_is_source_changed(env, design):
    run = react_run(env, design[1])
    ref = run_round_reference(run, 1)
    assert code(Sources(ctx(env)).resolve, {**ref, "sha256": "0" * 64}) == (409, "source-changed")


def test_missing_round_and_foreign_project_run_are_indistinguishable(env, design):
    run = react_run(env, design[1])
    ref = run_round_reference(run, 1)
    reader = Sources(ctx(env))
    missing_round = code(reader.resolve, {**ref, "revision": "2"})
    missing_run = code(reader.resolve, {**ref, "sourceId": "run-absent"})
    # The same run ID stored in another project's partition is not this project's record.
    status, other, _ = call(env.api, "POST", "/projects", {"name": "Other", "requestId": "other"}, actor="alice")
    assert status == 201
    foreign_owner = f"project:{other['project']['id']}"
    env.api.storage.put(foreign_owner, "run", {**{k: v for k, v in run.items() if k not in
                        ("version", "createdAt", "updatedAt")}, "id": "run-foreign",
                        "projectId": other["project"]["id"]})
    foreign = code(reader.resolve, {**ref, "sourceId": "run-foreign"})
    assert missing_round == missing_run == foreign == (404, "not-found")
    with pytest.raises(CollaborationError) as a:
        reader.resolve({**ref, "sourceId": "run-absent"})
    with pytest.raises(CollaborationError) as b:
        reader.resolve({**ref, "sourceId": "run-foreign"})
    assert a.value.message == b.value.message


def test_membership_change_during_read_fails_through_the_frozen_authority(env, design):
    run = react_run(env, design[1])
    ref = run_round_reference(run, 1)
    reader = Sources(ctx(env))
    reader.resolve(ref)
    owner = f"project:{env.pid}"
    project = env.api.storage.get(owner, "project", env.pid)
    project["members"].pop("dana")
    env.api.storage.put(owner, "project", project, project["version"])
    assert code(reader.resolve, ref) == (409, "ontology-authority-changed")
    removed = Sources(ctx(env, "carol"))
    project = env.api.storage.get(owner, "project", env.pid)
    project["members"].pop("carol")
    env.api.storage.put(owner, "project", project, project["version"])
    assert code(removed.resolve, ref)[0] == 403


def test_round_state_governs_who_may_read_metadata_and_text(env, design):
    contract = design[1]
    draft = react_run(env, contract, run_id="run-draft", status="running", passed=True)
    failed = react_run(env, contract, run_id="run-failed", status="needs_changes", passed=False)
    reviewable = react_run(env, contract, run_id="run-review", status="completed", passed=True)
    approved = react_run(env, contract, run_id="run-approved", status="completed", passed=True, approval=True)
    for run in (draft, failed):
        ref = run_round_reference(run, 1)
        assert code(Sources(ctx(env, "bob")).resolve, ref) == (404, "not-found")
        # A wrong hash is not an existence oracle for a restricted reader.
        assert code(Sources(ctx(env, "bob")).resolve, {**ref, "sha256": "0" * 64}) == (404, "not-found")
        assert code(Sources(ctx(env, "bob")).authorize, ref) == (404, "not-found")
    for actor in ("carol", "dana", "alice"):
        assert Sources(ctx(env, actor)).resolve(run_round_reference(failed, 1))["record"]["number"] == 1
    for run in (reviewable, approved):
        located = {**run_round_reference(run, 1), "location": {"path": "src/App.tsx"}}
        assert Sources(ctx(env, "bob")).resolve(located, text=True)["text"] == APP.decode()
    blocked = react_run(env, contract, run_id="run-blocked", passed=True, blocking=["finding"])
    assert code(Sources(ctx(env, "bob")).resolve, run_round_reference(blocked, 1)) == (404, "not-found")


def test_non_react_and_archived_runs_are_not_current_round_sources(env, design):
    html = react_run(env, design[1], run_id="run-html", output="html")
    assert code(Sources(ctx(env)).resolve, run_round_reference(html, 1)) == (404, "not-found")
    run = react_run(env, design[1])
    owner = f"project:{env.pid}"
    env.api.storage.put(owner, "run", {**run, "archived": True}, run["version"])
    ref = run_round_reference(run, 1)
    assert code(Sources(ctx(env)).resolve, ref) == (409, "ontology-source-stale")
    assert Sources(ctx(env)).authorize(ref) is True


def test_text_requires_a_file_path_and_detects_archive_tampering(env, design):
    run = react_run(env, design[1])
    ref = run_round_reference(run, 1)
    assert code(Sources(ctx(env)).resolve, ref, text=True) == (400, "ontology-source-location")
    env.api.storage.put_blob(run["rounds"][0]["sourceKey"], archive({"src/App.tsx": b"changed"}), "application/zip")
    located = {**ref, "location": {"path": "src/App.tsx"}}
    assert code(Sources(ctx(env)).resolve, located, text=True) == (409, "ontology-source-integrity")
    assert code(Sources(ctx(env)).release_source, ref) == (409, "ontology-source-integrity")


def test_superseded_contract_or_guideline_revokes_current_round_use(env, design):
    product, contract = design
    run = react_run(env, contract)
    ref = run_round_reference(run, 1)
    republish(env, product)
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-upstream-revoked")
    # Historical diagnostics remain: the guideline and contract revision are still readable.
    assert Sources(ctx(env)).authorize(ref) is True


def test_edited_contract_revokes_current_round_but_keeps_history(env, design):
    _, contract = design
    run = react_run(env, contract)
    ref = run_round_reference(run, 1)
    status, payload = http(env, "PUT", f"/contracts/{contract['id']}",
                           {"title": "edited", "version": contract["version"]}, actor="carol")
    assert status == 200, payload
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-upstream-revoked")
    assert Sources(ctx(env)).authorize(ref) is True
    owner = f"project:{env.pid}"
    record = env.api.storage.get(owner, "contract", contract["id"])
    env.api.storage.put(owner, "contract", {**record, "revisions": []}, record["version"])
    assert code(Sources(ctx(env)).authorize, ref) == (404, "not-found")


def test_revoked_upstream_admission_denies_current_and_historical_reads(env, design):
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    ref = run_round_reference(run, 1)
    reader = Sources(ctx(env))
    reader.resolve(ref)
    assert any(check["kind"] == "adm_decision" for check in reader.recheck())
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-upstream-revoked")
    assert code(Sources(ctx(env)).authorize, ref) == (404, "not-found")


def test_superseded_but_readable_admission_keeps_historical_diagnostics(env, design):
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    ref = run_round_reference(run, 1)
    owner = f"project:{env.pid}"
    document = env.api.storage.get(owner, "document", decision["source"]["sourceId"])
    # A newer approved revision supersedes the admitted one; the old revision stays readable.
    env.api.storage.put(owner, "document", {**document, "approvedRevisionId": document["id"] + "--r999999"},
                        document["version"])
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-upstream-revoked")
    assert Sources(ctx(env)).authorize(ref) is True


def test_admission_binding_must_match_the_recorded_revision_and_hash(env, design):
    decision = internal_admitted(env)
    forged = {**admission.admission_ref(decision), "artifactHash": "0" * 64}
    run = react_run(env, design[1], admissions=[forged])
    ref = run_round_reference(run, 1)
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-upstream-revoked")
    assert code(Sources(ctx(env)).authorize, ref) == (404, "not-found")


def test_round_reference_cannot_carry_its_own_audience(env, design):
    run = react_run(env, design[1])
    ref = {**run_round_reference(run, 1), "allowedRoles": ["planner"]}
    assert code(Sources(ctx(env)).resolve, ref) == (403, "ontology-source-audience")
