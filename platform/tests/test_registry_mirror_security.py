import copy
import json
from types import SimpleNamespace

import pytest

from agentcore import registry_mirror as mirror


@pytest.mark.parametrize("failure", [None, "update", "unconfirmed", "identity"])
def test_agent_mirror_replaces_old_private_descriptor_before_approval(monkeypatch, failure):
    record = {"name": "agent_probe", "recordVersion": "v1", "recordType": "AGENT",
              "description": "Synthetic", "status": "APPROVED",
              "payload": {"systemPrompt": "PRIVATE_PROMPT_MARKER", "skillMd": "PRIVATE_SKILL_MARKER"}}
    current = {"name": "agent_probe", "recordVersion": "v1", "recordId": "synthetic",
               "status": "DRAFT", "descriptorType": "CUSTOM",
               "descriptors": {"custom": {"inlineContent": json.dumps(record)}}}
    calls = []

    def update(**request):
        calls.append("update")
        if failure == "update":
            raise RuntimeError("synthetic failure")
        if failure != "unconfirmed":
            current.update(descriptorType=request["descriptorType"], descriptors=request["descriptors"])

    def status(**request):
        calls.append("status")
        assert "PRIVATE_" not in json.dumps(current["descriptors"])
        current["status"] = request["status"]

    monkeypatch.setattr(mirror, "find_record", lambda *args: {
        "recordId": "synthetic", "name": "agent_probe", "recordVersion": "v1", "status": "DRAFT"})
    monkeypatch.setattr(mirror, "ctl", lambda: SimpleNamespace(
        get_registry_record=lambda **kwargs: copy.deepcopy(current),
        update_registry_record=update, update_registry_record_status=status))
    if failure == "identity":
        current["name"] = "different_agent"
    if failure:
        with pytest.raises(RuntimeError):
            mirror.mirror(record)
        assert "status" not in calls
    else:
        result = mirror.mirror(record)
        assert result["status"] == "APPROVED" and result["action"] == "updated"
        assert calls == ["update", "status"]
        assert "PRIVATE_" not in json.dumps(current["descriptors"])
