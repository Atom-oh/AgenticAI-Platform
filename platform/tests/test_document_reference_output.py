"""Persisted reference status cannot certify normalized unknown prose aliases."""
import json
from pathlib import Path
import sys

import pytest

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1])]
from test_document_analyses import api, catalog, result, run, source, start
from graph.store import Edge, Node


@pytest.mark.parametrize("unknown", ["Ｅ９９", "E\u200b99", "TEAM-999", "Ｅ９９를", "근거E99에서", "TEAM-999는"])
def test_unknown_normalized_reference_is_rejected_before_persisted_checked_status(api, unknown):
    catalog(api)
    api.graph_store.upsert_nodes([Node("TEAM-1", "Department", {"name": "합성 담당팀"})])
    api.graph_store.upsert_edges([Edge("SCR-1", "OWNED_BY", "TEAM-1")])
    source(api, "REG-1")
    created = start(api)
    def response(system, user):
        return json.dumps({"summary": f"검토 근거 {unknown}", "findings": [
            {"nodeId": "DOC-1", "reason": "원문 검토", "citationIds": ["E1"]},
        ]}), {}, {}
    done, _ = run(api, created, model=response)
    assert done["status"] == "completed"
    value = result(api, created)[1]["result"]
    assert value["verification"]["references"] == "failed"
    assert value["findings"] == []
    assert unknown not in value["summary"]
