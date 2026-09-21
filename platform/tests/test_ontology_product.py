import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_workbench_core import wb
from test_ontology_sources import context
from workspace.ontology_store import Ontology, CURRENT
from workspace.collaboration import CollaborationError


def product(wb):
    return wb.collab.handle("POST", ["products"], {
        "requestId": "product", "title": "Synthetic product", "description": "No financial transactions",
        "conditions": [{"id": "eligible", "text": "Synthetic eligibility"}],
        "steps": [{"id": "entry", "title": "Start", "description": "Review the example"}],
        "notices": [{"id": "notice", "title": "Synthetic notice", "content": "Read all source content", "required": True}],
    }, {}, "alice", wb.project["id"])[1]["product"]


def publish(wb, value):
    return wb.collab.handle("POST", ["products", value["id"], "publish"],
                           {"version": value["version"]}, {}, "alice", wb.project["id"])[1]


def test_legacy_product_publication_is_preserved_until_explicit_enablement(wb):
    published = publish(wb, product(wb))
    assert "ontologyPublication" not in published["product"]
    assert wb.storage.get(wb.owner, "ontology", CURRENT) is None
    assert published["ontology"]["nodes"]


def test_product_and_canonical_partition_publish_in_one_transaction(wb):
    wb.collab.ontology_enabled = True
    value = product(wb)
    published = publish(wb, value)
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    receipt = published["product"]["ontologyPublication"]
    assert receipt["generation"] == current["generation"]
    view = Ontology(context(wb)).read()
    assert {node["type"] for node in view["nodes"]} == {"Product", "PolicyRule", "Procedure"}
    assert next(node for node in view["nodes"] if node["type"] == "Product")["reviewState"] == "approved"
    assert next(node for node in view["nodes"] if node["type"] == "Procedure")["reviewState"] == "candidate"
    assert published["ontology"]["guidelineId"] == published["guideline"]["id"]
    assert "ontologyHash" in published["product"]
    operations = wb.storage.table().transactions[-1]["TransactItems"]
    assert len(operations) == 6  # product, guideline, legacy projection, source asset, canonical manifest, project fence


def test_failed_canonical_index_cannot_leave_product_published(wb, monkeypatch):
    wb.collab.ontology_enabled = True
    value = product(wb)
    original = wb.storage.put_blob_once
    def fail_index(key, raw, mime):
        if "index-nodes-" in key:
            raise RuntimeError("synthetic publication failure")
        return original(key, raw, mime)
    monkeypatch.setattr(wb.storage, "put_blob_once", fail_index)
    with pytest.raises(RuntimeError):
        publish(wb, value)
    assert wb.storage.get(wb.owner, "product", value["id"])["publishedGuidelineId"] is None
    assert wb.storage.get(wb.owner, "ontology", CURRENT) is None


def test_business_source_partition_cannot_be_overwritten_or_reapproved_as_manual_mapping(wb):
    wb.collab.ontology_enabled = True
    published = publish(wb, product(wb))
    current = wb.storage.get(wb.owner, "ontology", CURRENT)
    store = Ontology(context(wb))
    with pytest.raises(CollaborationError):
        store.publish_candidate("product-" + published["product"]["id"], {},
                                expected_generation=current["generation"], request_id="overwrite")
    with pytest.raises(CollaborationError):
        store.review_node(published["product"]["ontologyPublication"]["nodeId"],
                          expected_generation=current["generation"], revision=1, decision="reviewed",
                          reason="Cannot alter source authority", request_id="review")
