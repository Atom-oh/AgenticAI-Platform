# platform/tests/design_fixtures.py
"""Shared synthetic fixtures for the design engine tests (engine plan, Task E4)."""
import copy, hashlib, json, sys  # noqa: E401,F401
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import ontology_schema as schema  # noqa: E402

SEED = ROOT / "seed" / "design_poc"


def raw_graph():
    return json.loads((SEED / "ontology.json").read_text(encoding="utf-8"))


def sealed_graph(approved=True):
    raw = raw_graph()
    state = "approved" if approved else "candidate"
    nodes = [schema.seal({**n, "reviewState": state}) for n in raw["nodes"]]
    revisions = {n["id"]: n["revision"] for n in nodes}
    # Edges stay "candidate", exactly as publish_candidate stores them; only nodes are reviewed.
    edges = [schema.seal({**e, "reviewState": "candidate",
                          "src": {"id": e["src"], "revision": revisions[e["src"]]},
                          "dst": {"id": e["dst"], "revision": revisions[e["dst"]]}}) for e in raw["edges"]]
    return {"schemaVersion": 1, "projectId": raw["projectId"], "generation": "gen-1", "nodes": nodes, "edges": edges,
            "coverage": {"complete": False, "unknown": ["outside-snapshot-not-certified"],
                         "scope": "procedure-snapshot", "truncated": False}}


def _rescope(value, project_id):
    return {**value, "scope": {"kind": "project", "projectId": project_id}} if project_id else value


def raw_graph_with(source_ref, *, project_id=None, raw=None):
    """Publishable candidate graph: sealed nodes/edges, local ids, the given source ref everywhere.

    `project_id` re-scopes the synthetic seed to the test project, because publication accepts only
    nodes of the current project. `raw` replaces the seed file with an (edited) raw graph."""
    raw = raw_graph() if raw is None else raw
    nodes = [schema.seal({**_rescope(n, project_id), "sourceRefs": [source_ref], "reviewState": "candidate"})
             for n in raw["nodes"]]
    rev = {n["id"]: n["revision"] for n in nodes}
    edges = [schema.seal({**e, "sourceRefs": [source_ref], "reviewState": "candidate",
                          "src": {"id": e["src"], "revision": rev[e["src"]]},
                          "dst": {"id": e["dst"], "revision": rev[e["dst"]]}}) for e in raw["edges"]]
    return {"schemaVersion": 1, "projectId": project_id or raw["projectId"], "nodes": nodes, "edges": edges}


def approved_document(wb, *, title, text):
    """Create and approve one synthetic library document through the documents API test helpers."""
    from test_documents_library import approve, finalize, upload
    project = wb.project["id"]
    uploaded = upload(wb.api, data=text.encode("utf-8"), project=project, title=title)
    finalized = finalize(wb.api, uploaded, project=project)
    status, approved = approve(wb.api, finalized, project=project)
    assert status == 200, approved
    document = wb.storage.get(wb.owner, "document", uploaded["document"]["id"])
    return document, approved["revision"]


def seed_document_ref(wb):
    """Create and approve one synthetic library document, then return its exact document-revision source ref
    (revision/audienceRevision as strings)."""
    document, revision = approved_document(wb, title="데모 적금 상품 설명", text="합성 문서")
    # The library resolves revisions by record id (e.g. "<documentId>--r000001"), not the display ordinal, and
    # the audience lives on the document (review round 4, X9; documents/library.py:212, ontology_sources.py:154).
    return {"sourceKind": "document-revision", "sourceId": document["id"], "revision": revision["id"],
            "sha256": revision["sha256"], "audienceRevision": str(document["aclVersion"])}


def extra_screens_graph(count, *, source_ref, project_id="project-1"):
    nodes = [{"id": "p-big", "type": "Procedure", "scope": {"kind": "project", "projectId": project_id},
              "title": "큰 절차", "revision": 1, "sourceRefs": [source_ref], "provenance": "declared",
              "reviewState": "candidate", "tombstone": False}]
    edges = []
    for i in range(count):
        nodes.append({**nodes[0], "id": f"s{i:02d}", "type": "Screen", "title": f"화면 {i}"})
        edges.append({"id": f"po{i:02d}", "type": "PART_OF", "src": {"id": f"s{i:02d}", "revision": 1},
                      "dst": {"id": "p-big", "revision": 1}, "sourceRefs": [source_ref], "provenance": "declared",
                      "reviewState": "candidate", "tombstone": False})
    return {"schemaVersion": 1, "projectId": project_id,
            "nodes": [schema.seal(n) for n in nodes], "edges": [schema.seal(e) for e in edges]}


def review_all(ontology, identities, decisions=("reviewed", "approved")):
    """Review then approve every published node exactly once, as the owner."""
    for local in sorted(identities):
        node = ontology.read([identities[local]])["nodes"][0]
        for decision in decisions:
            ontology.review_node(identities[local], expected_generation=ontology.current()["generation"],
                                 revision=node["revision"], decision=decision, reason="seed",
                                 request_id=f"{decision}-{local}")


def published_knowledge(wb, *, raw=None, request_id="seed-1"):
    """Publish the (optionally edited) seed through the real `publish_candidate`, review every node, and return
    `(Knowledge, identities)` read through `procedure_snapshot` + `from_snapshot`: canonical ids throughout."""
    from test_ontology_sources import context
    from workspace.ontology_store import Ontology
    from design_loop.knowledge import from_snapshot
    ontology = Ontology(context(wb))
    graph = raw_graph_with(seed_document_ref(wb), project_id=wb.project["id"], raw=raw)
    ids = ontology.publish_candidate("design-seed", graph, expected_generation=None, request_id=request_id)["identities"]
    review_all(ontology, ids)
    return from_snapshot(ontology.procedure_snapshot(ids["savings-signup"])), ids


def pages():
    """Admitted derivative pages of the synthetic seed (B0 intake shape; Task E7)."""
    return json.loads((SEED / "pages.json").read_text(encoding="utf-8"))


def knowledge(**kw):
    from design_loop.knowledge import from_snapshot
    return from_snapshot(sealed_graph(**kw))
