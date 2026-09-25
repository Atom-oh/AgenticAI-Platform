import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_schema import node, edge
from test_ontology_sources import asset, context
from workspace import ontology_schema as schema
from workspace.ontology_sources import asset_reference
from workspace.ontology_store import Ontology, CURRENT
from workspace.collaboration import CollaborationError


def candidate(wb):
    image, control = asset(wb, "image"), asset(wb, "control")
    nodes = [node("image", "Foundation", project=wb.project["id"], subtype="icon", sourceRefs=[asset_reference(image)]),
             node("control", project=wb.project["id"], sourceRefs=[asset_reference(control)])]
    relation = edge("use", "control", "image", sourceRefs=[asset_reference(control)])
    return {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": [relation]}


def publish(wb, graph=None, **kwargs):
    graph = candidate(wb) if graph is None else graph
    return Ontology(context(wb)).publish_candidate("collection", graph, expected_generation=kwargs.get("generation"),
                                                 request_id=kwargs.get("request", "req-1"))


def test_workspace_ontology_is_the_only_new_write_authority(wb):
    data = candidate(wb)
    result = publish(wb, data)
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    assert current["generation"] == result["generation"]
    assert wb.storage.get(wb.owner, "wb_index", "current") is None
    view = Ontology(context(wb)).read()
    assert {n["title"] for n in view["nodes"]} == {"image", "control"}
    assert len(view["edges"]) == 1
    assert all(n["reviewState"] == "candidate" and n["provenance"] == "declared" for n in view["nodes"])
    again = publish(wb, data)
    assert again["generation"] == result["generation"]
    assert again["historical"] is False


def test_idempotent_publication_rechecks_the_original_source_authority(wb):
    data = candidate(wb)
    publish(wb, data)
    source = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**source, "accessRevoked": True}, source["version"])
    with pytest.raises(CollaborationError) as error:
        publish(wb, data)
    assert error.value.code == "ontology-source-stale"


def test_manual_coverage_cannot_claim_complete_runtime_dependencies(wb):
    value = candidate(wb)
    value["coverage"] = {"complete": True, "scope": "runtime-complete", "unknown": [], "truncated": False}
    result = publish(wb, value)
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["scope"] == "declared-project-partition"
    assert "unreviewed-design-mappings" in result["coverage"]["unknown"]


def test_declared_marker_cannot_alias_a_trusted_parser_publication(wb):
    value = candidate(wb)
    publish(wb, value)
    with pytest.raises(CollaborationError) as error:
        Ontology(context(wb)).publish_candidate("collection", value, expected_generation=None,
            request_id="req-1", _producer="parser-extracted", _completion_writes=lambda marker: pytest.fail("wrong producer replay"))
    assert error.value.code == "ontology-changed"


def test_partition_cannot_keep_active_edges_to_nodes_it_removes(wb):
    value = candidate(wb)
    first = publish(wb, value)
    value["nodes"] = [value["nodes"][1]]
    value["edges"][0] = schema.seal({**value["edges"][0],
        "dst": {"id": first["identities"]["image"], "revision": 1}})
    with pytest.raises(CollaborationError) as error:
        publish(wb, value, request="remove-target", generation=first["generation"])
    assert error.value.code == "ontology-removed-endpoint"


def test_changed_edge_endpoints_require_a_new_identity_to_preserve_history(wb):
    value = candidate(wb)
    first = publish(wb, value)
    relation = value["edges"][0]
    value["edges"] = [schema.seal({**relation, "src": relation["dst"], "dst": relation["src"]})]
    with pytest.raises(CollaborationError) as error:
        publish(wb, value, request="changed-relation", generation=first["generation"])
    assert error.value.code == "ontology-edge-identity"
    assert Ontology(context(wb)).current()["generation"] == first["generation"]


def test_read_page_does_not_inherit_the_atomic_write_authority_limit(wb):
    generation = None
    for part in range(2):
        nodes = []
        for number in range(50):
            identifier = f"asset-{part}-{number}"
            source = asset(wb, identifier)
            nodes.append(node(identifier, project=wb.project["id"], sourceRefs=[asset_reference(source)]))
        result = Ontology(context(wb)).publish_candidate(f"read-{part}", {
            "schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": []},
            expected_generation=generation, request_id=f"read-{part}")
        generation = result["generation"]
    assert len(Ontology(context(wb)).read(limit=100)["nodes"]) == 100


def test_publication_rechecks_source_versions_atomically(wb, monkeypatch):
    data = candidate(wb)
    original = wb.storage.put_blob_once
    changed = False
    def race(key, raw, mime):
        nonlocal changed
        result = original(key, raw, mime)
        if "index-nodes-" in key and not changed:
            changed = True
            row = wb.storage.get(wb.owner, "asset", "image")
            wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
        return result
    monkeypatch.setattr(wb.storage, "put_blob_once", race)
    with pytest.raises(CollaborationError):
        publish(wb, data)
    assert wb.storage.get(wb.owner, "ontology", CURRENT) is None


def test_failed_index_write_cannot_publish_a_partial_manifest(wb, monkeypatch):
    data = candidate(wb)
    original = wb.storage.put_blob_once
    def fail_index(key, raw, mime):
        if "index-adjacency-" in key:
            raise RuntimeError("synthetic storage failure")
        return original(key, raw, mime)
    monkeypatch.setattr(wb.storage, "put_blob_once", fail_index)
    with pytest.raises(RuntimeError):
        publish(wb, data)
    assert wb.storage.get(wb.owner, "ontology", CURRENT) is None


def test_retired_edge_is_only_a_historical_source_seed(wb):
    value = candidate(wb)
    reference = asset_reference(asset(wb, "mapping-only"))
    value["edges"][0] = schema.seal({**value["edges"][0], "sourceRefs": [reference]})
    first = publish(wb, value)
    assert Ontology(context(wb)).source_nodes(reference) == [first["identities"]["control"]]
    publish(wb, {**value, "edges": []}, generation=first["generation"], request="retire")
    assert Ontology(context(wb)).source_nodes(reference) == []
    assert first["identities"]["control"] in Ontology(context(wb)).source_nodes(reference, for_impact=True)


def test_service_contention_returns_for_reauthorization_before_any_retry(wb, monkeypatch):
    from botocore.exceptions import ClientError
    source = asset(wb)
    ctx = context(wb)
    ctx.claims["exp"] = wb.now // 1000 + 1
    calls = []

    def contend(**request):
        calls.append(request)
        wb.now += 2000
        wb.storage.clock = lambda: wb.now
        raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                           "CancellationReasons": [{"Code": "TransactionConflict"}] +
                           [{"Code": "None"}] * (len(request["TransactItems"]) - 1)}, "TransactWriteItems")

    client = wb.storage.table().meta.client
    monkeypatch.setattr(client.exceptions, "TransactionCanceledException", ClientError)
    monkeypatch.setattr(client, "transact_write_items", contend)
    writes = [ctx.write("wb_artifact", {"id": "never-published", "projectId": wb.project["id"]})]
    with pytest.raises(CollaborationError) as error:
        ctx.commit(writes, [ctx.check("asset", source)])
    assert error.value.code == "conflict" and len(calls) == 1
    with pytest.raises(CollaborationError) as expired:
        ctx.commit(writes, [ctx.check("asset", source)])
    assert expired.value.code == "authorization-expired" and len(calls) == 1
    assert wb.storage.get(wb.owner, "wb_artifact", "never-published") is None


def test_revoked_or_archived_sources_disappear_without_restoring_old_graph_access(wb):
    publish(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    view = Ontology(context(wb)).read()
    assert [n["title"] for n in view["nodes"]] == ["control"]
    assert view["edges"] == []
    assert view["coverage"]["complete"] is False


def test_cursors_are_opaque_and_bound_to_actor_scope_and_generation(wb):
    publish(wb)
    first = Ontology(context(wb)).read(limit=1)
    assert first["cursor"].startswith("cursor-")
    saved = wb.storage.get(wb.owner, "ontology_cursor", first["cursor"])
    assert saved["ttl"] == saved["expiresAt"] // 1000
    assert 0 < saved["expiresAt"] - wb.storage.clock() <= 300000
    assert wb.storage.get(wb.owner, "ontology", first["cursor"]) is None
    second = Ontology(context(wb)).read(limit=1, cursor=first["cursor"])
    assert first["nodes"][0]["id"] != second["nodes"][0]["id"]
    with pytest.raises(CollaborationError):
        Ontology(context(wb, "bob")).read(limit=1, cursor=first["cursor"])
    row = wb.storage.get(wb.owner, "project", wb.project["id"])
    row["members"].pop("bob")
    wb.storage.put(wb.owner, "project", row, row["version"])
    with pytest.raises(CollaborationError):
        Ontology(context(wb)).read(limit=1, cursor=first["cursor"])


def test_unrelated_project_edits_do_not_revoke_ontology_pagination(wb):
    publish(wb)
    first = Ontology(context(wb)).read(limit=1)
    project = wb.storage.get(wb.owner, "project", wb.project["id"])
    changed = wb.storage.put(wb.owner, "project", {**project, "name": "New project name"}, project["version"])
    assert changed["authorityRevision"] == project["authorityRevision"]
    second = Ontology(context(wb)).read(limit=1, cursor=first["cursor"])
    assert first["nodes"][0]["id"] != second["nodes"][0]["id"]


def test_forged_project_or_publication_scope_is_not_a_new_canonical_authority(wb):
    data = candidate(wb)
    wrong = copy.deepcopy(data)
    wrong["projectId"] = "other"
    with pytest.raises(CollaborationError):
        publish(wb, wrong)
    data["nodes"][0] = schema.seal({**data["nodes"][0], "scope": {"kind": "published", "publicationId": "fake"}})
    with pytest.raises(CollaborationError):
        publish(wb, data)


def test_index_tampering_is_not_accepted_as_an_empty_graph(wb):
    result = publish(wb)
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    bucket, digest = next(iter(current["indexes"]["nodes"].items()))
    key = wb.storage.key_for(wb.owner, "ontology", "index-nodes-" + bucket, digest + ".json")
    wb.storage.put_blob(key, b"{}", "application/json")
    with pytest.raises(CollaborationError, match="해시"):
        Ontology(context(wb)).read(list(result["identities"].values()))


def test_publication_rewrites_ux_model_references_to_canonical_ids(wb):
    """Task E3: slot allow-lists and condition targets follow the same identities map as usageIds/slots."""
    body, tpl = asset(wb, "org-local"), asset(wb, "tpl")
    ux = {"slots": {"body": {"required": True, "allowed": ["org-local"]}},
          "conditions": [{"id": "c1", "when": "cond:auto", "effect": "include", "target": "org-local"}],
          "layers": {"code": {"importPath": "@studio/approved-ui", "exportName": "Screen", "childrenProp": None,
                              "props": {"body": {"type": "node", "required": True},
                                        "tone": {"type": "enum", "required": False, "values": ["a", "b"]}}}}}
    nodes = [node("org-local", "Organism", project=wb.project["id"], sourceRefs=[asset_reference(body)]),
             node("tpl", "PageTemplate", project=wb.project["id"], sourceRefs=[asset_reference(tpl)],
                  properties={"uxModel": ux})]
    result = publish(wb, {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": []})
    ids = result["identities"]
    stored = Ontology(context(wb)).read([ids["tpl"]])["nodes"][0]
    model = stored["properties"]["uxModel"]
    assert model["slots"]["body"]["allowed"] == [ids["org-local"]] and ids["org-local"] != "org-local"
    assert model["conditions"][0]["target"] == ids["org-local"]
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    ontology = Ontology(context(wb))
    part = ontology._part(current, result["partitionId"])
    schema.validate_graph(part["graph"])


def _review_all(ontology, ids):
    from design_fixtures import review_all
    review_all(ontology, ids)


def test_procedure_snapshot_includes_members_transitions_and_dependencies(wb):
    """Task E4: real publish_candidate, the returned identities applied, and the complete seed stored validly."""
    ontology = Ontology(context(wb))
    from design_fixtures import raw_graph_with, seed_document_ref
    graph = raw_graph_with(seed_document_ref(wb), project_id=wb.project["id"])
    published = ontology.publish_candidate("design-seed", graph, expected_generation=None, request_id="seed-1")
    ids = published["identities"]
    part = ontology._part(wb.storage.get(wb.owner, "ontology", CURRENT), published["partitionId"])
    schema.validate_graph(part["graph"])                       # complete seed, stored partition (E3 Step 4a)
    _review_all(ontology, ids)
    snap = ontology.procedure_snapshot(ids["savings-signup"])
    types = {n["type"] for n in snap["nodes"]}
    assert {"Screen", "PageTemplate", "Organism", "Procedure"} <= types
    assert sum(1 for e in snap["edges"] if e["type"] == "NEXT") == 9
    assert all(e["reviewState"] == "candidate" for e in snap["edges"])      # edges are never node-reviewed
    from design_loop.knowledge import from_snapshot
    k = from_snapshot(snap)
    assert k.complete and len(k.procedures[ids["savings-signup"]]["transitions"]) == 9
    assert k.procedures[ids["savings-signup"]]["entry"] == ids["intro"]     # entryScreenId rewritten
    tpl = k.templates[ids["single-task"]]
    assert all(a in k.assets for slot in tpl["slots"].values() for a in slot["allowed"])  # rewrite applied (Codex #4)
    assert ids["notice-alert"] in k.assets and ids["step-bar"] in k.assets   # slot-only alternatives are read


def test_procedure_snapshot_truncates_at_max_screens(wb):
    ontology = Ontology(context(wb))
    from design_fixtures import extra_screens_graph, seed_document_ref
    graph = extra_screens_graph(21, source_ref=seed_document_ref(wb), project_id=wb.project["id"])
    ids = ontology.publish_candidate("big", graph, expected_generation=None, request_id="big-1")["identities"]
    _review_all(ontology, ids)
    snap = ontology.procedure_snapshot(ids["p-big"])
    assert snap["coverage"]["truncated"] and "too-many-screens" in snap["coverage"]["unknown"]
    assert sum(1 for n in snap["nodes"] if n["type"] == "Screen") == 20


def test_procedure_snapshot_includes_the_procedures_own_policy_dependencies(wb):
    """PR #30 review round 2, #2: a Procedure GOVERNED_BY PolicyRule must be traversed even though no member
    Screen's own dependency closure reaches it (the Procedure node itself was never a closure seed)."""
    from design_fixtures import extra_screens_graph, seed_document_ref
    from design_loop.knowledge import from_snapshot
    ontology = Ontology(context(wb))
    ref = seed_document_ref(wb)
    base = extra_screens_graph(1, source_ref=ref, project_id=wb.project["id"])
    rule_node = schema.seal({"id": "rule-1", "type": "PolicyRule", "scope": base["nodes"][0]["scope"],
                             "title": "필수 규정", "revision": 1, "sourceRefs": [ref], "provenance": "declared",
                             "reviewState": "candidate", "tombstone": False,
                             "properties": {"ruleId": "rule-1", "statement": "필수", "required": True,
                                           "guidelineId": "g1", "severity": "critical"}})
    gov_edge = schema.seal({"id": "gov-1", "type": "GOVERNED_BY", "src": {"id": "p-big", "revision": 1},
                           "dst": {"id": "rule-1", "revision": 1}, "sourceRefs": [ref], "provenance": "declared",
                           "reviewState": "candidate", "tombstone": False})
    graph = {**base, "nodes": base["nodes"] + [rule_node], "edges": base["edges"] + [gov_edge]}
    ids = ontology.publish_candidate("policy", graph, expected_generation=None, request_id="policy-1")["identities"]
    _review_all(ontology, ids)
    snap = ontology.procedure_snapshot(ids["p-big"])
    assert ids["rule-1"] in {n["id"] for n in snap["nodes"]}, "the Procedure's own PolicyRule dependency is missing"
    k = from_snapshot(snap)
    assert ids["rule-1"] in k.rules, k.rules
    assert ids["p-big"] in k.rules[ids["rule-1"]]["targets"]
    assert k.complete, (k.coverage, k.unresolved)


def _slot_graph(project, ref, alternatives, extra_allowed=()):
    from design_fixtures import extra_screens_graph
    graph = extra_screens_graph(1, source_ref=ref, project_id=project)
    base = {k: v for k, v in graph["nodes"][0].items() if k != "contentHash"}
    atoms = [f"alt{i:02d}" for i in range(alternatives)]
    graph["nodes"] += [schema.seal({**base, "id": a, "type": "Atom", "title": a}) for a in atoms]
    graph["nodes"].append(schema.seal({**base, "id": "tpl", "type": "PageTemplate", "title": "tpl", "properties": {
        "uxModel": {"slots": {"body": {"required": True, "allowed": [*atoms, *extra_allowed]}}}}}))
    edge = {k: v for k, v in graph["edges"][0].items() if k != "contentHash"}
    graph["edges"].append(schema.seal({**edge, "id": "use-tpl", "type": "COMPOSES",
                                       "src": {"id": "s00", "revision": 1}, "dst": {"id": "tpl", "revision": 1}}))
    return graph


def test_procedure_snapshot_reads_property_only_alternatives_in_bounded_batches(wb):
    """21+ slot-only alternatives exceed one 20-seed closure batch (review round 12, AF2)."""
    from design_fixtures import seed_document_ref
    from design_loop.knowledge import from_snapshot
    ontology = Ontology(context(wb))
    graph = _slot_graph(wb.project["id"], seed_document_ref(wb), 23)
    ids = ontology.publish_candidate("slots", graph, expected_generation=None, request_id="slots-1")["identities"]
    _review_all(ontology, ids)
    snap = ontology.procedure_snapshot(ids["p-big"])
    present = {n["id"] for n in snap["nodes"]}
    assert all(ids[f"alt{i:02d}"] in present for i in range(23))
    k = from_snapshot(snap)
    assert k.complete, (k.coverage, k.unresolved)
    tight = ontology.procedure_snapshot(ids["p-big"], max_nodes=10)
    assert tight["coverage"]["truncated"]


def test_procedure_snapshot_unreadable_reference_blocks(wb):
    from design_fixtures import seed_document_ref
    from design_loop.knowledge import from_snapshot
    ontology = Ontology(context(wb))
    graph = _slot_graph(wb.project["id"], seed_document_ref(wb), 1, extra_allowed=("ghost-asset",))
    ids = ontology.publish_candidate("ghost", graph, expected_generation=None, request_id="ghost-1")["identities"]
    _review_all(ontology, ids)
    snap = ontology.procedure_snapshot(ids["p-big"])
    assert "unmapped-or-inaccessible" in snap["coverage"]["unknown"]
    assert not from_snapshot(snap).complete


def test_procedure_snapshot_reports_a_deprecated_member(wb):
    from design_fixtures import seed_document_ref, review_all
    ontology = Ontology(context(wb))
    graph = _slot_graph(wb.project["id"], seed_document_ref(wb), 1)
    ids = ontology.publish_candidate("retire", graph, expected_generation=None, request_id="retire-1")["identities"]
    _review_all(ontology, ids)
    screen = ontology.read([ids["s00"]])["nodes"][0]
    ontology.review_node(ids["s00"], expected_generation=ontology.current()["generation"], revision=screen["revision"],
                         decision="deprecated", reason="retired", request_id="retire-s00")
    snap = ontology.procedure_snapshot(ids["p-big"])
    assert "retired-or-rejected-mapping" in snap["coverage"]["unknown"]
    with pytest.raises(CollaborationError) as error:
        ontology.procedure_snapshot(ids["s00"])
    assert error.value.status == 404
