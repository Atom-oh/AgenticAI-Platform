"""Installed `run-round` and `ux-contract` source authority adapters (B0 sharing, Tasks H1-H2).

Acceptance: ONT-04 (source kinds), AUTH-08 (upstream revocation), HAND-02-05
(source-binding parts). Real Storage/CAS, document library and intake admission.
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

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
from workspace.ontology_sources import (PROJECT_AUDIENCE, Sources, contract_reference,  # noqa: E402
                                        run_round_reference)
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


# H2: ux-contract -------------------------------------------------------------

def test_approved_contract_resolves_for_any_current_reader(env, design):
    _, contract = design
    ref = contract_reference(contract)
    assert ref == {"sourceKind": "ux-contract", "sourceId": contract["id"], "revision": str(contract["version"]),
                   "sha256": contract["approval"]["hash"], "audienceRevision": PROJECT_AUDIENCE}
    for actor in ("alice", "bob", "carol", "dana"):
        value = Sources(ctx(env, actor)).resolve(ref, text=True)
        assert value["kind"] == "ux-contract" and value["record"]["id"] == contract["id"]
        assert json.loads(value["text"]) == rules.validate_contract(contract)


def test_draft_contract_is_not_found(env, design):
    product, _ = design
    status, payload = http(env, "POST", "/contracts", {**CONTRACT, "productId": product["id"]}, actor="carol")
    assert status == 201
    draft = payload["contract"]
    ref = {"sourceKind": "ux-contract", "sourceId": draft["id"], "revision": str(draft["version"]),
           "sha256": "a" * 64, "audienceRevision": PROJECT_AUDIENCE}
    assert code(Sources(ctx(env)).resolve, ref) == (404, "not-found")
    assert code(Sources(ctx(env)).resolve, {**ref, "sourceId": "absent"}) == (404, "not-found")
    assert code(Sources(ctx(env)).resolve, ref, historical=True) == (404, "not-found")


def test_contract_wrong_hash_or_tampered_content_is_source_changed(env, design):
    _, contract = design
    ref = contract_reference(contract)
    assert code(Sources(ctx(env)).resolve, {**ref, "sha256": "0" * 64}) == (409, "source-changed")
    owner = f"project:{env.pid}"
    record = env.api.storage.get(owner, "contract", contract["id"])
    env.api.storage.put(owner, "contract", {**record, "title": "tampered"}, record["version"])
    # The stored record version moved on, so the approval no longer names this revision.
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-changed")
    # A forged approval that names the tampered revision fails the recomputed contract hash.
    record = env.api.storage.get(owner, "contract", contract["id"])
    forged = env.api.storage.put(owner, "contract", {**record, "approval": {
        **record["approval"], "version": record["version"] + 1}}, record["version"])
    assert code(Sources(ctx(env)).resolve, {**ref, "revision": str(forged["version"])}) == (409, "source-changed")


def test_changed_product_publication_supersedes_contract_but_history_remains(env, design):
    product, contract = design
    ref = contract_reference(contract)
    republish(env, product)
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-superseded")
    value = Sources(ctx(env)).resolve(ref, historical=True)
    assert value["historical"] is True and value["text"] is None and value["record"]["id"] == contract["id"]
    assert Sources(ctx(env)).authorize(ref) is True
    assert code(Sources(ctx(env)).resolve, ref, text=True, historical=True)[0] == 422


def test_edited_contract_is_superseded_and_only_historically_readable(env, design):
    _, contract = design
    ref = contract_reference(contract)
    status, payload = http(env, "PUT", f"/contracts/{contract['id']}",
                           {"title": "edited", "version": contract["version"]}, actor="carol")
    assert status == 200, payload
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-superseded")
    assert Sources(ctx(env, "bob")).resolve(ref, historical=True)["historical"] is True


def test_foreign_project_contract_is_not_found(env, design):
    _, contract = design
    status, other, _ = call(env.api, "POST", "/projects", {"name": "Other", "requestId": "other"}, actor="alice")
    assert status == 201
    foreign = f"project:{other['project']['id']}"
    env.api.storage.put(foreign, "contract", {**{k: v for k, v in contract.items()
                        if k not in ("version", "createdAt", "updatedAt")}, "id": "foreign-contract",
                        "projectId": other["project"]["id"]})
    ref = {**contract_reference(contract), "sourceId": "foreign-contract", "revision": "1"}
    assert code(Sources(ctx(env)).resolve, ref) == (404, "not-found")


# Fix round 1 (PR #29 review) ------------------------------------------------

def blob(env, run_id, kind, actor="alice", offset=None, route="runs"):
    query = {"kind": kind, **({"round": "1"} if route == "runs" else {}),
             **({"offset": str(offset)} if offset is not None else {})}
    status, payload, _ = call(env.api, "GET", f"/{route}/{run_id}/blob", actor=actor, project=env.pid, query=query)
    return status, payload


def test_round_downloads_apply_the_shared_round_state_permission(env, design):
    """Finding 1: every chunk of every round artifact uses the adapter's content permission."""
    failed = react_run(env, design[1], run_id="run-failed", status="needs_changes", passed=False)
    owner = f"project:{env.pid}"
    row = failed["rounds"][0]
    candidate = key_for(owner, "run", failed["id"], "rounds/1-candidate.json")
    env.api.storage.put_blob_once(candidate, b"{}", "application/json")
    env.api.storage.put(owner, "run", {**failed, "rounds": [{**row, "candidateKey": candidate}]}, failed["version"])
    assert code(Sources(ctx(env, "bob")).resolve, run_round_reference(failed, 1)) == (404, "not-found")
    for kind in ("source", "candidate"):
        for offset in (None, 0, 1):
            assert blob(env, failed["id"], kind, actor="bob", offset=offset)[0] == 404
        assert blob(env, failed["id"], kind, actor="carol")[0] == 200
    reviewable = react_run(env, design[1], run_id="run-review")
    assert blob(env, reviewable["id"], "source", actor="bob")[0] == 200


def test_round_and_release_downloads_deny_a_revoked_upstream_admission(env, design):
    """Finding 1: the blob routes share the adapter's upstream-lineage check."""
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)], approval=True)
    row = run["rounds"][0]
    owner = f"project:{env.pid}"
    release = env.api.storage.put(owner, "release", {
        "id": "rel-1", "runId": run["id"], "round": 1, "status": "ready", "sourceHash": row["sourceHash"],
        "sourceKey": row["sourceKey"]})
    assert blob(env, run["id"], "source")[0] == 200
    assert blob(env, release["id"], "source", route="releases")[0] == 200
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    assert code(Sources(ctx(env)).resolve, run_round_reference(run, 1)) == (409, "source-upstream-revoked")
    for offset in (None, 1):
        assert blob(env, run["id"], "source", offset=offset)[0] == 404
        assert blob(env, release["id"], "source", offset=offset, route="releases")[0] == 404


def input_asset(env, identifier="input-1", data=b"Synthetic input brief.\n"):
    import hashlib
    owner = f"project:{env.pid}"
    key = key_for(owner, "asset", identifier, "original")
    env.api.storage.put_blob_once(key, data, "text/plain")
    return env.api.storage.put(owner, "asset", {
        "id": identifier, "projectId": env.pid, "name": identifier + ".txt", "purpose": "guide",
        "status": "stored", "uploadStatus": "stored", "parseStatus": "complete", "originalKey": key,
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "importRevision": 1})


@pytest.fixture
def bound(env, design):
    """An approved contract whose `assetIds` and run `assetSnapshots` bind one input asset."""
    product, _ = design
    asset = input_asset(env)
    status, payload = http(env, "POST", "/contracts", {**CONTRACT, "productId": product["id"],
                                                        "assetIds": [asset["id"]]}, actor="carol")
    assert status == 201, payload
    contract = payload["contract"]
    status, payload = http(env, "POST", f"/contracts/{contract['id']}/approve", {"version": contract["version"]},
                           actor="carol")
    assert status == 200, payload
    contract = payload["contract"]
    assert asset["id"] in contract["assetIds"]
    run = react_run(env, contract)
    owner = f"project:{env.pid}"
    snapshots = [{key: row[key] for key in ("id", "version", "importRevision", "sha256", "name", "size") if key in row}
                 for row in (env.api.storage.get(owner, "asset", i) for i in contract["assetIds"])]
    run = env.api.storage.put(owner, "run", {**run, "assetSnapshots": snapshots}, run["version"])
    return asset, contract, run


def test_archived_input_asset_blocks_current_contract_and_round_use(env, bound):
    """Finding 2: every bound input is reauthorized for current use."""
    asset, contract, run = bound
    assert Sources(ctx(env)).resolve(contract_reference(contract))
    assert Sources(ctx(env)).resolve(run_round_reference(run, 1))
    owner = f"project:{env.pid}"
    env.api.storage.put(owner, "asset", {**asset, "archived": True}, asset["version"])
    assert code(Sources(ctx(env)).resolve, contract_reference(contract)) == (409, "source-upstream-revoked")
    assert code(Sources(ctx(env)).resolve, run_round_reference(run, 1)) == (409, "source-upstream-revoked")
    # Archived but readable inputs keep historical diagnostics.
    assert Sources(ctx(env)).authorize(contract_reference(contract)) is True
    assert Sources(ctx(env)).authorize(run_round_reference(run, 1)) is True


def test_revoked_input_asset_denies_historical_contract_round_and_download(env, bound):
    """Finding 2: historical authorization follows the bound inputs' access revocation."""
    asset, contract, run = bound
    owner = f"project:{env.pid}"
    env.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    assert code(Sources(ctx(env)).authorize, contract_reference(contract)) == (404, "not-found")
    assert code(Sources(ctx(env)).authorize, run_round_reference(run, 1)) == (404, "not-found")
    assert blob(env, run["id"], "source")[0] == 404


def test_run_asset_snapshot_must_match_the_bound_input(env, bound):
    """Finding 2: the round's exact asset snapshot binding is enforced, not only the contract ID list."""
    asset, contract, run = bound
    owner = f"project:{env.pid}"
    forged = [{**row, "sha256": "0" * 64} if row["id"] == asset["id"] else row for row in run["assetSnapshots"]]
    run = env.api.storage.put(owner, "run", {**run, "assetSnapshots": forged}, run["version"])
    assert code(Sources(ctx(env)).resolve, run_round_reference(run, 1)) == (409, "source-upstream-revoked")
    assert code(Sources(ctx(env)).authorize, run_round_reference(run, 1)) == (404, "not-found")


def test_republished_criteria_after_resolution_fail_the_final_recheck(env, design):
    """Finding 6: consulted product/guideline records are fenced through recheck/commit."""
    product, contract = design
    run = react_run(env, contract)
    readers = []
    for ref in (contract_reference(contract), run_round_reference(run, 1)):
        reader = Sources(ctx(env))
        reader.resolve(ref)
        assert {"product", "guideline"} <= {check["kind"] for check in reader.recheck()}
        readers.append(reader)
    republish(env, product)
    for reader in readers:
        with pytest.raises(CollaborationError):
            reader.recheck()


def test_changed_catalog_after_resolution_fails_the_final_recheck(env, design, monkeypatch):
    """Finding 6: the consulted catalog hash joins the package recheck set."""
    from workspace import component_catalog
    _, contract = design
    run = react_run(env, contract)
    readers = [Sources(ctx(env)), Sources(ctx(env))]
    readers[0].resolve(contract_reference(contract))
    readers[1].resolve(run_round_reference(run, 1))
    original = component_catalog.read_catalog()
    monkeypatch.setattr(component_catalog, "read_catalog", lambda: {**original, "hash": "f" * 64})
    for reader in readers:
        assert code(reader.recheck) == (409, "ontology-package-stale")


# Fix round 2 (PR #29 review 2) ----------------------------------------------

def test_criteria_republished_right_after_validation_cannot_be_fenced_as_current(env, design, monkeypatch):
    """Review 2 #9: the exact product/guideline versions used by validation are the fenced ones."""
    import workspace.criteria as criteria
    product, contract = design
    run = react_run(env, contract)
    collaboration = env.api.collaboration
    original = type(collaboration).is_current
    armed = {"on": False}

    def racing(self, scope, value):
        result = original(self, scope, value)
        if armed["on"]:
            armed["on"] = False
            republish(env, product)
        return result
    monkeypatch.setattr(type(collaboration), "is_current", racing)
    for ref in (contract_reference(contract), run_round_reference(run, 1)):
        armed["on"] = True
        reader = Sources(ctx(env))

        def attempt():
            reader.resolve(ref)
            reader.recheck()
        assert code(attempt)[0] == 409
        product = env.api.storage.get(f"project:{env.pid}", "product", product["id"])


def test_historical_delivery_retains_admission_observations(env, design):
    """Review 2 #7: admission records observed by historical checks join the parent reader."""
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    reader = Sources(ctx(env))
    reader.round_delivery(run["id"], 1)
    kinds = {check["kind"] for check in reader.recheck()}
    assert {"adm_decision", "adm_grant", "adm_policy"} <= kinds
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    with pytest.raises(CollaborationError):
        reader.recheck()


def test_superseded_admission_fallback_also_retains_its_authority(env, design):
    """Review 2 #7: the superseded-source fallback fences decision, grant and policy too."""
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    owner = f"project:{env.pid}"
    document = env.api.storage.get(owner, "document", decision["source"]["sourceId"])
    env.api.storage.put(owner, "document", {**document, "approvedRevisionId": document["id"] + "--r999999"},
                        document["version"])
    reader = Sources(ctx(env))
    assert reader.authorize(run_round_reference(run, 1)) is True
    assert {"adm_decision", "adm_grant", "adm_policy"} <= {check["kind"] for check in reader.observed.values()}
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    with pytest.raises(CollaborationError):
        reader.recheck()


def test_downloads_recheck_authority_after_reading_each_chunk(env, design, monkeypatch):
    """Review 2 #6: revocation during byte retrieval denies that very chunk."""
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)], approval=True)
    row = run["rounds"][0]
    owner = f"project:{env.pid}"
    env.api.storage.put(owner, "release", {"id": "rel-1", "runId": run["id"], "round": 1, "status": "ready",
                                           "sourceHash": row["sourceHash"], "sourceKey": row["sourceKey"]})
    storage = env.api.storage
    original = storage.get_blob
    state = {"revision": 1}

    def revoking(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key == row["sourceKey"] and state["revision"]:
            env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": state["revision"]})
            state["revision"] = 0
        return data
    monkeypatch.setattr(storage, "get_blob", revoking)
    status, payload = blob(env, run["id"], "source")
    assert status in (404, 409) and not isinstance(payload, bytes)


def test_release_download_rechecks_authority_after_reading_the_chunk(env, design, monkeypatch):
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)], approval=True)
    row = run["rounds"][0]
    owner = f"project:{env.pid}"
    env.api.storage.put(owner, "release", {"id": "rel-1", "runId": run["id"], "round": 1, "status": "ready",
                                           "sourceHash": row["sourceHash"], "sourceKey": row["sourceKey"]})
    storage = env.api.storage
    original = storage.get_blob
    state = {"armed": True}

    def revoking(key, *args, **kwargs):
        data = original(key, *args, **kwargs)
        if key == row["sourceKey"] and state["armed"]:
            state["armed"] = False
            env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
        return data
    monkeypatch.setattr(storage, "get_blob", revoking)
    status, payload = blob(env, "rel-1", "source", route="releases")
    assert status in (404, 409) and not isinstance(payload, bytes)


def test_refinement_base_round_lineage_is_reauthorized(env, design):
    """Review 2 #3: a derivative's base run/round is supplied input with its own lineage."""
    decision = internal_admitted(env)
    base = react_run(env, design[1], run_id="run-base", admissions=[admission.admission_ref(decision)])
    derived = react_run(env, design[1], run_id="run-derived")
    owner = f"project:{env.pid}"
    derived = env.api.storage.put(owner, "run", {**derived, "baseRunId": base["id"], "baseRound": 1,
                                                 "baseSourceHash": base["rounds"][0]["sourceHash"]},
                                  derived["version"])
    ref = run_round_reference(derived, 1)
    reader = Sources(ctx(env))
    reader.resolve(ref)
    assert "adm_decision" in {check["kind"] for check in reader.recheck()}
    assert blob(env, derived["id"], "source")[0] == 200
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    assert code(Sources(ctx(env)).resolve, ref) == (409, "source-upstream-revoked")
    assert code(Sources(ctx(env)).authorize, ref) == (404, "not-found")
    assert blob(env, derived["id"], "source")[0] == 404


def test_refinement_base_binding_and_depth_are_bounded(env, design):
    """Review 2 #3: a changed base binding or an unbounded/cyclic chain fails closed."""
    owner = f"project:{env.pid}"
    base = react_run(env, design[1], run_id="run-base")
    derived = react_run(env, design[1], run_id="run-derived")
    forged = env.api.storage.put(owner, "run", {**derived, "baseRunId": base["id"], "baseRound": 1,
                                                "baseSourceHash": "0" * 64}, derived["version"])
    assert code(Sources(ctx(env)).resolve, run_round_reference(forged, 1)) == (409, "source-upstream-revoked")
    cyclic = env.api.storage.get(owner, "run", base["id"])
    env.api.storage.put(owner, "run", {**cyclic, "baseRunId": base["id"], "baseRound": 1}, cyclic["version"])
    assert code(Sources(ctx(env)).authorize, run_round_reference(cyclic, 1)) == (404, "not-found")


@pytest.fixture
def queued_export(env, design, monkeypatch):
    """A queued Git export of an approved round whose lineage carries an admission."""
    import workspace.git_service as git_service
    from workspace.git_service import connection_hash
    from workspace.worker import Worker
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)], approval=True)
    row = run["rounds"][0]
    owner = f"project:{env.pid}"
    storage = env.api.storage
    storage.put(owner, "release", {"id": "rel-1", "runId": run["id"], "round": 1, "status": "ready",
                                   "sourceHash": row["sourceHash"], "sourceKey": row["sourceKey"],
                                   "approvalHash": "a" * 64})
    connection = {"id": "local-test", "provider": "local", "repository": "tests/design", "visibility": "internal"}
    storage.put(owner, "gitexport", {"id": "export-1", "releaseId": "rel-1", "connectionId": connection["id"],
                                     "connectionHash": connection_hash(connection), "sourceHash": row["sourceHash"],
                                     "actor": "alice", "projectId": env.pid, "status": "queued"})
    job = storage.put(owner, "job", {"id": "export-1", "task": "git", "input": {"exportId": "export-1"}})
    # Criteria/approval checks are covered elsewhere; this fixture isolates the lineage gate.
    monkeypatch.setattr(git_service, "resolve_generation_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(git_service, "approved_artifacts", lambda *args, **kwargs: None)
    delivered = []

    def factory(conn, token):
        def export_release(*args, **kwargs):
            delivered.append(args)
            return {"status": "committed", "sourceHash": row["sourceHash"], "connectionId": conn["id"],
                    "commitSha": "b" * 40}
        return type("Exporter", (), {"export_release": staticmethod(export_release)})()
    worker = Worker(storage=storage, git_connections=lambda: {connection["id"]: connection},
                    git_exporter_factory=factory)
    return SimpleNamespace(worker=worker, owner=owner, job=job, row=row, delivered=delivered)


def test_queued_export_reauthorizes_lineage_at_worker_execution(env, queued_export):
    """Review 2 #1: revocation after enqueueing blocks the worker before any delivery."""
    from workspace.git_service import process_export
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    with pytest.raises(ValueError):
        process_export(queued_export.worker, queued_export.owner, queued_export.job)
    assert queued_export.delivered == []


def test_queued_export_rechecks_lineage_immediately_before_delivery(env, queued_export, monkeypatch):
    """Review 2 #1: revocation while the worker reads the source still blocks delivery."""
    from workspace.git_service import process_export
    worker = queued_export.worker
    original = worker._read

    def revoking(owner, key, *args, **kwargs):
        data = original(owner, key, *args, **kwargs)
        if key == queued_export.row["sourceKey"]:
            env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
        return data
    monkeypatch.setattr(worker, "_read", revoking)
    with pytest.raises(ValueError):
        process_export(worker, queued_export.owner, queued_export.job)
    assert queued_export.delivered == []


def test_queued_export_with_current_lineage_is_delivered(env, queued_export):
    from workspace.git_service import process_export
    result = process_export(queued_export.worker, queued_export.owner, queued_export.job)
    assert result["status"] == "committed" and len(queued_export.delivered) == 1


def test_content_bearing_json_routes_apply_the_same_source_authority(env, bound):
    """Review 2 #5: contract/run detail and listings treat revoked lineage as missing."""
    asset, contract, run = bound
    owner = f"project:{env.pid}"
    assert http(env, "GET", f"/contracts/{contract['id']}")[0] == 200
    assert http(env, "GET", f"/runs/{run['id']}")[0] == 200
    env.api.storage.put(owner, "asset", {**asset, "accessRevoked": True}, asset["version"])
    for path in (f"/contracts/{contract['id']}", f"/runs/{run['id']}"):
        status, payload = http(env, "GET", path)
        assert status == 404 and asset["id"] not in json.dumps(payload)
    for path, field, hidden in (("/contracts", "contracts", contract["id"]), ("/runs", "runs", run["id"])):
        status, listed = http(env, "GET", path)
        assert status == 200 and hidden not in json.dumps(listed), listed


def test_run_detail_omits_rounds_whose_admission_was_revoked(env, design):
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    status, payload = http(env, "GET", f"/runs/{run['id']}")
    assert status == 200 and [row["number"] for row in payload["run"]["rounds"]] == [1]
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    status, payload = http(env, "GET", f"/runs/{run['id']}")
    assert status == 200 and payload["run"]["rounds"] == [] and decision["id"] not in json.dumps(payload)


def test_listings_use_opaque_cursors_that_skip_hidden_rows(env, bound):
    """Review 2 #5: listing continuation is opaque and bound to the caller."""
    asset, contract, run = bound
    for index in range(3):
        react_run(env, contract, run_id=f"run-extra-{index}")
    status, first = call(env.api, "GET", "/runs", actor="alice", project=env.pid, query={"limit": "2"})[:2]
    assert status == 200 and len(first["runs"]) == 2 and first["cursor"].startswith("pagecur-")
    assert "run-" not in first["cursor"] and "sk" not in first["cursor"]
    status, second = call(env.api, "GET", "/runs", actor="alice", project=env.pid,
                          query={"limit": "2", "cursor": first["cursor"]})[:2]
    assert status == 200 and {r["id"] for r in second["runs"]}.isdisjoint({r["id"] for r in first["runs"]})
    assert call(env.api, "GET", "/runs", actor="bob", project=env.pid,
                query={"limit": "2", "cursor": first["cursor"]})[0] == 409


def test_commit_attempt_guard_rechecks_admission_expiry(env, design, monkeypatch):
    """Review 2 #7: retained admission observations are rechecked inside every commit attempt."""
    from workbench.service import check_source_deadlines
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    reader = Sources(ctx(env))
    reader.resolve(run_round_reference(run, 1))
    checks = reader.recheck()
    check_source_deadlines(env.api.storage, checks)
    stored = env.api.storage.get(f"project:{env.pid}", "adm_decision", decision["id"])
    monkeypatch.setattr(env.api.storage, "clock", lambda: stored["expiresAt"] + 1)
    assert code(check_source_deadlines, env.api.storage, checks) == (409, "source-upstream-revoked")


# Fix round 3 (PR #29 review 3) ----------------------------------------------

def test_historical_admitted_round_rechecks_without_recursion(env, design):
    """Review 3 #6: admission verification is separate from historical-reference replay."""
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    ref = run_round_reference(run, 1)
    reader = Sources(ctx(env))
    assert reader.authorize(ref) is True
    kinds = {check["kind"] for check in reader.recheck()}
    assert {"adm_decision", "adm_grant"} <= kinds
    env.admin({"op": "revoke_grant", "id": "grant-1", "expectedRevision": 1})
    with pytest.raises(CollaborationError):
        reader.recheck()


def test_ontology_impact_with_an_admitted_round_source(env, design):
    """Review 3 #6: historical diagnostics over an admitted-round source work."""
    from test_ontology_schema import node
    from workspace.ontology_api import route
    from workspace.ontology_store import Ontology
    decision = internal_admitted(env)
    run = react_run(env, design[1], admissions=[admission.admission_ref(decision)])
    ref = run_round_reference(run, 1)
    graph = {"schemaVersion": 1, "projectId": env.pid, "edges": [],
             "nodes": [node("screen-r", "Screen", project=env.pid, sourceRefs=[ref])]}
    published = Ontology(ctx(env)).publish_candidate("rounds", graph, expected_generation=None, request_id="rounds")
    service = ctx(env)
    status, result = route(env.api, service.scope, service.claims, "POST", ["impact"], {
        "changeId": "round-change", "kind": "design", "oldSource": ref,
        "expectedGeneration": published["generation"]}, {})
    assert status == 200 and result["coverage"]["complete"] is False


def test_historical_contract_resolution_returns_only_the_retained_revision(env, design):
    """Review 3 #2: historical metadata comes exclusively from the authorized retained revision."""
    _, contract = design
    ref = contract_reference(contract)
    status, payload = http(env, "PUT", f"/contracts/{contract['id']}",
                           {"title": "edited newer content", "version": contract["version"]}, actor="carol")
    assert status == 200, payload
    record = Sources(ctx(env)).resolve(ref, historical=True)["record"]
    assert record["title"] == contract["title"] and record["version"] == contract["version"]
    assert "edited newer content" not in json.dumps(record)
    assert record.get("approval", {}).get("hash") == ref["sha256"]
