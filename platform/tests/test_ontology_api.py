import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_store import candidate
from workspace.ontology_sources import asset_reference


def call(wb, method, path, body=None, actor="alice", project=None, authenticated=True):
    event = {"rawPath": "/studio-api/ontology" + path, "headers": {
        "X-Workspace-Project": project or wb.project["id"]}, "requestContext": {"http": {"method": method}}}
    if authenticated:
        event["requestContext"]["authorizer"] = {"jwt": {"claims": {
            "sub": actor, "token_use": "access", "exp": wb.now // 1000 + 3600}}}
    if body is not None:
        event["body"] = json.dumps(body)
    result = wb.api.handle(event)
    return result["statusCode"], json.loads(result["body"])


def setup_graph(wb):
    value = candidate(wb)
    status, published = call(wb, "POST", "/partitions", {
        "name": "example", "requestId": "first", "expectedGeneration": None, "graph": value})
    assert status == 201, published
    return published


def test_api_uses_real_jwt_and_project_membership_boundary(wb):
    assert call(wb, "GET", "/schema", authenticated=False)[0] == 401
    assert call(wb, "GET", "/schema", actor="outsider")[0] == 403
    code, value = call(wb, "GET", "/schema")
    assert code == 200 and len(value["levels"]) == 8
    assert value["backend"] == "workspace-project-ontology"
    assert value["analyzerConfigured"] is False


def test_source_change_returns_real_reverse_path_and_generation(wb):
    published = setup_graph(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    status, result = call(wb, "POST", "/impact", {"changeId": "image-change", "kind": "asset",
        "oldSource": asset_reference(row), "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert {item["title"] for item in result["items"]} == {"image", "control"}
    assert result["coverage"]["complete"] is False
    assert all(item["evidenceKind"] == "candidate" for item in result["items"])
    status, context = call(wb, "POST", "/context", {"nodeIds": [published["identities"]["image"]]})
    assert status == 200 and len(context["nodes"]) == 2 and context["hash"]


def test_human_review_requires_role_exact_versions_and_prior_review(wb):
    published = setup_graph(wb)
    identifier = published["identities"]["control"]
    request = {"requestId": "review-1", "expectedGeneration": published["generation"],
               "revision": 1, "decision": "approved", "reason": "Synthetic review"}
    assert call(wb, "POST", f"/nodes/{identifier}/review", request)[0] == 409
    request["decision"] = "reviewed"
    status, reviewed = call(wb, "POST", f"/nodes/{identifier}/review", request)
    assert status == 200, reviewed
    assert reviewed["node"]["reviewState"] == "reviewed"
    stale = {**request, "requestId": "stale"}
    assert call(wb, "POST", f"/nodes/{identifier}/review", stale)[0] == 409
    approved = {**request, "requestId": "approve", "decision": "approved", "revision": 1,
                "expectedGeneration": reviewed["generation"]}
    status, result = call(wb, "POST", f"/nodes/{identifier}/review", approved)
    assert status == 200, result
    assert result["node"]["reviewState"] == "approved" and result["review"]["actor"] == "alice"
    assert result["review"]["sourceRefs"]


def test_bad_fields_and_forged_actor_are_not_project_authority(wb):
    value = candidate(wb)
    status, _ = call(wb, "POST", "/partitions", {
        "name": "example", "requestId": "first", "graph": value, "actor": "admin"})
    assert status == 400
    assert call(wb, "POST", "/context", {"nodeIds": [], "projectId": "other"})[0] == 400


def test_new_impact_source_requires_current_authority_and_empty_results_have_bound_receipts(wb):
    from test_ontology_schema import ref
    published = setup_graph(wb)
    body = {"changeId": "empty-impact", "kind": "asset", "expectedGeneration": published["generation"]}
    status, empty = call(wb, "POST", "/impact", body)
    assert status == 200 and empty["items"] == [] and empty["hash"]
    assert call(wb, "POST", "/impact", {**body, "newSource": ref("missing")})[0] == 404
    source = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**source, "archived": True}, source["version"])
    assert call(wb, "POST", "/impact", {**body, "newSource": asset_reference(source)})[0] == 409


def test_revoked_edge_only_source_retains_a_restricted_impact_boundary(wb):
    from test_ontology_sources import asset, context
    from workspace.ontology_store import Ontology
    from workspace import ontology_schema as schema
    value = candidate(wb)
    reference = asset_reference(asset(wb, "revoked-mapping"))
    value["edges"][0] = schema.seal({**value["edges"][0], "sourceRefs": [reference]})
    result = Ontology(context(wb)).publish_candidate("edge-only", value, expected_generation=None, request_id="edge-only")
    source = wb.storage.get(wb.owner, "asset", reference["sourceId"])
    wb.storage.put(wb.owner, "asset", {**source, "accessRevoked": True}, source["version"])
    status, impact = call(wb, "POST", "/impact", {"changeId": "revoked-edge", "kind": "permission",
        "oldSource": reference, "expectedGeneration": result["generation"]})
    assert status == 200, impact
    assert impact["items"] and all(item["evidenceKind"] == "candidate" for item in impact["items"])
    assert "restricted-source-boundary" in impact["coverage"]["unknown"]
    assert reference["sourceId"] not in json.dumps(impact)


def test_raw_hidden_node_and_missing_node_have_the_same_non_disclosing_response(wb):
    published = setup_graph(wb)
    source = wb.storage.get(wb.owner, "asset", "image")
    wb.storage.put(wb.owner, "asset", {**source, "accessRevoked": True}, source["version"])
    request = {"changeId": "raw-selection", "kind": "asset", "expectedGeneration": published["generation"]}
    hidden = call(wb, "POST", "/impact", {**request, "nodeIds": [published["identities"]["image"]]})
    missing = call(wb, "POST", "/impact", {**request, "nodeIds": ["unknown-node"]})
    assert hidden == missing and hidden[0] == 404
    # The explicit exact old-source path still provides only readable dependents.
    status, diagnostic = call(wb, "POST", "/impact", {**request, "oldSource": asset_reference(source)})
    assert status == 200 and [item["title"] for item in diagnostic["items"]] == ["control"]
    assert "restricted-source-boundary" in diagnostic["coverage"]["unknown"]


def test_retired_source_uses_its_immutable_snapshot_and_not_replacement_dependencies(wb):
    from test_ontology_sources import asset, context
    from test_ontology_schema import node, edge
    from workspace import ontology_schema as schema
    from workspace.ontology_store import Ontology
    original = candidate(wb)
    first = Ontology(context(wb)).publish_candidate("history", original, expected_generation=None, request_id="old")
    old_source = wb.storage.get(wb.owner, "asset", "image")
    replacement = asset_reference(asset(wb, "replacement"))
    added = asset_reference(asset(wb, "new-dependent"))
    changed = {**original, "nodes": [
        schema.seal({**original["nodes"][0], "title": "replacement image", "sourceRefs": [replacement]}),
        original["nodes"][1], node("new-dependent", project=wb.project["id"], sourceRefs=[added])],
        "edges": [*original["edges"], edge("new-use", "new-dependent", "image", sourceRefs=[added])]}
    second = Ontology(context(wb)).publish_candidate("history", changed,
        expected_generation=first["generation"], request_id="new")
    request = {"changeId": "old-source", "kind": "asset", "oldSource": asset_reference(old_source),
               "expectedGeneration": second["generation"]}
    status, result = call(wb, "POST", "/impact", request)
    assert status == 200, result
    assert {item["title"] for item in result["items"]} == {"image", "control"}
    assert all(item["snapshotGeneration"] == first["generation"] and item["staleWitness"] for item in result["items"])
    assert result["sourceSnapshots"][0]["historical"] is True
    wb.storage.put(wb.owner, "asset", {**old_source, "accessRevoked": True}, old_source["version"])
    status, restricted = call(wb, "POST", "/impact", request)
    assert status == 200, restricted
    assert [item["title"] for item in restricted["items"]] == ["control"]
    assert "restricted-source-boundary" in restricted["coverage"]["unknown"]


def test_pattern_approval_requires_two_current_reviewed_usages(wb):
    from test_ontology_sources import asset
    from test_ontology_schema import node
    source = asset(wb, "pattern")
    graph = {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": [
        node("pattern", "Pattern", project=wb.project["id"], sourceRefs=[asset_reference(source)])], "edges": []}
    status, published = call(wb, "POST", "/partitions", {"name": "patterns", "requestId": "pattern",
        "expectedGeneration": None, "graph": graph})
    assert status == 201
    identifier = published["identities"]["pattern"]
    status, reviewed = call(wb, "POST", f"/nodes/{identifier}/review", {
        "requestId": "review-pattern", "expectedGeneration": published["generation"], "revision": 1,
        "decision": "reviewed", "reason": "Review the source"})
    assert status == 200
    status, denied = call(wb, "POST", f"/nodes/{identifier}/review", {
        "requestId": "approve-pattern", "expectedGeneration": reviewed["generation"], "revision": 1,
        "decision": "approved", "reason": "No usage evidence"})
    assert status == 409 and denied["code"] == "ontology-pattern-usages"


def test_pattern_approval_tracks_exact_usage_screen_hashes(wb):
    from test_ontology_sources import asset, context
    from test_ontology_schema import node
    from workspace.ontology_store import Ontology
    nodes = [node(name, kind, project=wb.project["id"], sourceRefs=[asset_reference(asset(wb, name))],
                  properties={"usageIds": ["first", "second"]} if kind == "Pattern" else {})
             for name, kind in [("first", "Screen"), ("second", "Screen"), ("pattern", "Pattern")]]
    result = Ontology(context(wb)).publish_candidate("usage", {
        "schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": []},
        expected_generation=None, request_id="usage")
    ids, generation = result["identities"], result["generation"]
    for name in ["first", "second", "pattern"]:
        result = Ontology(context(wb)).review_node(ids[name], expected_generation=generation, revision=1,
            decision="reviewed", reason="Synthetic usage review", request_id="review-" + name)
        generation = result["generation"]
    result = Ontology(context(wb)).review_node(ids["pattern"], expected_generation=generation, revision=1,
        decision="approved", reason="Two reviewed usages", request_id="approve-pattern")
    assert len(result["node"]["properties"]["usageBindings"]) == 2
    assert len(result["review"]["sourceRefs"]) == 3
    assert ids["pattern"] in {node["id"] for node in Ontology(context(wb)).read()["nodes"]}
    Ontology(context(wb)).review_node(ids["first"], expected_generation=result["generation"], revision=1,
        decision="rejected", reason="Usage no longer valid", request_id="reject-usage")
    assert ids["pattern"] not in {node["id"] for node in Ontology(context(wb)).read()["nodes"]}


@pytest.mark.parametrize("change_usage", [False, True])
def test_partition_replacement_preserves_only_current_pattern_usage_proofs(wb, change_usage):
    from test_ontology_sources import asset, context
    from test_ontology_schema import node
    from workspace.ontology_store import Ontology
    from workspace import ontology_schema as schema
    nodes = [node(name, kind, project=wb.project["id"], sourceRefs=[asset_reference(asset(wb, name))],
                  properties={"usageIds": ["first", "second"]} if kind == "Pattern" else {})
             for name, kind in [("first", "Screen"), ("second", "Screen"), ("pattern", "Pattern"), ("other", "Atom")]]
    graph = {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": []}
    result = Ontology(context(wb)).publish_candidate("reuse", graph, expected_generation=None, request_id="reuse")
    ids = result["identities"]
    for name in ["first", "second", "pattern"]:
        result = Ontology(context(wb)).review_node(ids[name], expected_generation=result["generation"], revision=1,
            decision="reviewed", reason="Synthetic usage review", request_id="review-" + name)
    result = Ontology(context(wb)).review_node(ids["pattern"], expected_generation=result["generation"], revision=1,
        decision="approved", reason="Two reviewed usages", request_id="approve")
    approved = result["node"]
    # Ordinary callers resubmit the source partition without server-owned edges.
    graph["nodes"][-1] = schema.seal({**nodes[-1], "title": "Unrelated mapping edit"})
    if change_usage:
        graph["nodes"][0] = schema.seal({**nodes[0], "title": "Changed usage mapping"})
    Ontology(context(wb)).publish_candidate("reuse", graph,
        expected_generation=result["generation"], request_id="replace")
    view = Ontology(context(wb)).read()
    pattern = next(item for item in view["nodes"] if item["id"] == ids["pattern"])
    proofs = [item for item in view["edges"] if item["src"]["id"] == ids["pattern"]]
    if change_usage:
        assert pattern["reviewState"] == "candidate"
        assert "usageBindings" not in pattern["properties"]
        assert proofs == []
    else:
        assert pattern == approved
        assert len(proofs) == 2 and all(item["reviewState"] == "approved" for item in proofs)


def test_pattern_approval_cannot_exceed_the_edge_limit_with_retained_tombstones(wb):
    from test_ontology_sources import asset, context
    from test_ontology_schema import node, edge
    from workspace.ontology_store import Ontology
    nodes = [node(name, kind, project=wb.project["id"], sourceRefs=[asset_reference(asset(wb, name))],
                  properties={"usageIds": ["first", "second"]} if kind == "Pattern" else {})
             for name, kind in [("first", "Screen"), ("second", "Screen"), ("pattern", "Pattern")]]
    edges = [edge(f"retired-{i}", "first", "second", tombstone=True, sourceRefs=nodes[0]["sourceRefs"])
             for i in range(1000)]
    result = Ontology(context(wb)).publish_candidate("full-review", {
        "schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": edges},
        expected_generation=None, request_id="full-review")
    ids = result["identities"]
    for name in ["first", "second", "pattern"]:
        result = Ontology(context(wb)).review_node(ids[name], expected_generation=result["generation"], revision=1,
            decision="reviewed", reason="Synthetic usage review", request_id="review-" + name)
    status, error = call(wb, "POST", f"/nodes/{ids['pattern']}/review", {"requestId": "over-limit",
        "expectedGeneration": result["generation"], "revision": 1, "decision": "approved", "reason": "Two usages"})
    assert status == 422 and error["code"] == "ontology-capacity"
    assert Ontology(context(wb)).current()["generation"] == result["generation"]


def test_one_opaque_seed_can_expand_to_more_than_twenty_readable_dependents(wb):
    from test_ontology_sources import asset
    from test_ontology_schema import node, edge
    secret = asset(wb, "hidden-source")
    nodes = [node("hidden-root", "Foundation", subtype="icon", project=wb.project["id"],
                  sourceRefs=[asset_reference(secret)])]
    edges = []
    for number in range(25):
        identifier = f"dependent-{number}"
        ref = asset_reference(asset(wb, identifier))
        nodes.append(node(identifier, project=wb.project["id"], sourceRefs=[ref]))
        edges.append(edge(f"edge-{number}", identifier, "hidden-root", sourceRefs=[ref]))
    status, published = call(wb, "POST", "/partitions", {"name": "fanout", "requestId": "fanout",
        "expectedGeneration": None, "graph": {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": nodes, "edges": edges}})
    assert status == 201, published
    wb.storage.put(wb.owner, "asset", {**secret, "accessRevoked": True}, secret["version"])
    status, result = call(wb, "POST", "/impact", {"changeId": "revoked-many", "kind": "asset",
        "oldSource": asset_reference(secret), "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert len(result["items"]) == 25
    assert "hidden-source" not in json.dumps(result)
    assert published["identities"]["hidden-root"] not in json.dumps(result)


def test_archived_image_still_identifies_readable_dependents_as_historical_impact(wb):
    published = setup_graph(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    old = asset_reference(row)
    wb.storage.put(wb.owner, "asset", {**row, "archived": True}, row["version"])
    status, result = call(wb, "POST", "/impact", {"changeId": "archived-image", "kind": "asset",
        "oldSource": old, "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert "control" in {item["title"] for item in result["items"]}
    assert "historical-source-revisions" in result["coverage"]["unknown"]
    assert call(wb, "POST", "/context", {"nodeIds": [published["identities"]["image"]]})[0] == 409


def test_review_keeps_current_relations_and_mapping_changes_keep_stale_witnesses(wb):
    from test_ontology_sources import asset
    from test_ontology_schema import node, edge
    published = setup_graph(wb)
    source = asset(wb, "screen-source")
    external = published["identities"]["control"]
    graph = {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": [
        node("screen", "Screen", project=wb.project["id"], sourceRefs=[asset_reference(source)])],
        "edges": [edge("cross", "screen", external, sourceRefs=[asset_reference(source)])]}
    status, added = call(wb, "POST", "/partitions", {"name": "screens", "requestId": "cross",
        "expectedGeneration": published["generation"], "graph": graph})
    assert status == 201, added
    status, reviewed = call(wb, "POST", f"/nodes/{external}/review", {
        "requestId": "review-cross", "expectedGeneration": added["generation"], "revision": 1,
        "decision": "reviewed", "reason": "Synthetic review"})
    assert status == 200, reviewed
    status, context_result = call(wb, "POST", "/context", {"nodeIds": [external]})
    assert status == 200 and len(context_result["edges"]) == 2
    from workspace import ontology_schema as schema
    from workspace.ontology_store import Ontology
    from test_ontology_sources import context
    store = Ontology(context(wb))
    current = store.current()
    partition = schema.identity("partition", "example")
    original = store._part(current, partition)["graph"]
    changed = {**original, "nodes": [schema.seal({**n, "properties": {
        **n.get("properties", {}), "description": "Changed mapping"}}) if n["id"] == external else n for n in original["nodes"]]}
    status, changed_result = call(wb, "POST", "/partitions", {"name": "example", "requestId": "change-map",
        "expectedGeneration": reviewed["generation"], "graph": changed})
    assert status == 201, changed_result
    status, impact = call(wb, "POST", "/impact", {"changeId": "review-impact", "kind": "design",
        "nodeIds": [external], "expectedGeneration": changed_result["generation"]})
    assert status == 200, impact
    screen = next(item for item in impact["items"] if item["title"] == "screen")
    assert screen["staleWitness"] and screen["evidenceKind"] == "candidate"
    assert len(screen["witnessPath"]) == 2


def test_review_fences_active_external_sources_but_retired_edges_do_not_lock_the_partition(wb):
    from test_ontology_sources import asset, context
    from test_ontology_schema import node, edge
    from workspace.ontology_store import Ontology
    published = setup_graph(wb)
    source = asset(wb, "screen-source")
    graph = {"schemaVersion": 1, "projectId": wb.project["id"], "nodes": [
        node("screen", "Screen", project=wb.project["id"], sourceRefs=[asset_reference(source)])],
        "edges": [edge("external", "screen", published["identities"]["control"], sourceRefs=[asset_reference(source)])]}
    added = Ontology(context(wb)).publish_candidate("external", graph,
        expected_generation=published["generation"], request_id="external")
    control = wb.storage.get(wb.owner, "asset", "control")
    wb.storage.put(wb.owner, "asset", {**control, "accessRevoked": True}, control["version"])
    status, result = call(wb, "POST", f"/nodes/{added['identities']['screen']}/review", {
        "requestId": "denied", "revision": 1, "expectedGeneration": added["generation"],
        "decision": "reviewed", "reason": "An external source is now revoked"})
    assert status == 409 and result["code"] == "ontology-reference-unavailable"
    removed = Ontology(context(wb)).publish_candidate("external", {**graph, "edges": []},
        expected_generation=added["generation"], request_id="remove")
    status, result = call(wb, "POST", f"/nodes/{added['identities']['screen']}/review", {
        "requestId": "allowed", "revision": 1, "expectedGeneration": removed["generation"],
        "decision": "reviewed", "reason": "The unusable relationship is retired"})
    assert status == 200, result


def test_partition_removal_preserves_past_impact_and_does_not_reuse_revisions(wb):
    from workspace.ontology_store import Ontology
    from test_ontology_sources import context
    original = candidate(wb)
    status, first = call(wb, "POST", "/partitions", {
        "name": "lifecycle", "requestId": "initial", "expectedGeneration": None, "graph": original})
    assert status == 201
    reduced = {**original, "nodes": [original["nodes"][1]], "edges": []}
    status, removed = call(wb, "POST", "/partitions", {
        "name": "lifecycle", "requestId": "remove", "expectedGeneration": first["generation"], "graph": reduced})
    assert status == 201, removed
    status, result = call(wb, "POST", "/impact", {"changeId": "removed-image", "kind": "asset",
        "oldSource": original["nodes"][0]["sourceRefs"][0], "expectedGeneration": removed["generation"]})
    assert status == 200, result
    dependent = next(item for item in result["items"] if item["title"] == "control")
    assert dependent["staleWitness"] and dependent["evidenceKind"] == "candidate"
    assert {n["title"] for n in Ontology(context(wb)).read()["nodes"]} == {"control"}
    status, restored = call(wb, "POST", "/partitions", {
        "name": "lifecycle", "requestId": "reintroduce", "expectedGeneration": removed["generation"], "graph": original})
    assert status == 201, restored
    image = next(n for n in Ontology(context(wb)).read()["nodes"] if n["title"] == "image")
    assert image["id"] == first["identities"]["image"] and image["revision"] == 3


def test_revoked_source_is_an_opaque_boundary_not_a_leaked_witness(wb):
    published = setup_graph(wb)
    row = wb.storage.get(wb.owner, "asset", "image")
    old = asset_reference(row)
    wb.storage.put(wb.owner, "asset", {**row, "accessRevoked": True}, row["version"])
    status, result = call(wb, "POST", "/impact", {"changeId": "revoked-image", "kind": "asset",
        "oldSource": old, "expectedGeneration": published["generation"]})
    assert status == 200, result
    assert [item["title"] for item in result["items"]] == ["control"]
    assert published["identities"]["image"] not in json.dumps(result)
    assert "restricted-source-boundary" in result["coverage"]["unknown"]


def test_malformed_impact_seed_types_are_client_errors(wb):
    published = setup_graph(wb)
    for bad in (None, 7, "not-a-list", [None]):
        assert call(wb, "POST", "/impact", {"changeId": "bad", "kind": "asset",
            "nodeIds": bad, "expectedGeneration": published["generation"]})[0] == 400
