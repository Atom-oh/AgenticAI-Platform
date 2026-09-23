import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'api')]
from agentcore import registry_mirror as mirror


@pytest.mark.parametrize('failure', [None, 'update', 'unconfirmed', 'identity'])
def test_agent_mirror_replaces_old_private_descriptor_before_approval(monkeypatch, failure):
    record = {'name': 'agent_probe', 'recordVersion': 'v1', 'recordType': 'AGENT',
              'description': 'Synthetic', 'status': 'APPROVED',
              'payload': {'systemPrompt': 'PRIVATE_PROMPT_MARKER', 'skillMd': 'PRIVATE_SKILL_MARKER'}}
    current = {'name': 'agent_probe', 'recordVersion': 'v1', 'recordId': 'synthetic',
               'status': 'DRAFT', 'descriptorType': 'CUSTOM',
               'descriptors': {'custom': {'inlineContent': json.dumps(record)}}}
    calls = []

    def update(**request):
        calls.append('update')
        if failure == 'update':
            raise RuntimeError('synthetic failure')
        if failure != 'unconfirmed':
            current.update(name=request['name'], descriptorType=request['descriptorType'], descriptors=request['descriptors'])

    def status(**request):
        calls.append('status')
        assert 'PRIVATE_' not in json.dumps(current['descriptors'])
        current['status'] = request['status']

    monkeypatch.setattr(mirror, 'find_record', lambda *args: {
        'recordId': 'synthetic', 'name': 'agent_probe', 'recordVersion': 'v1', 'status': 'DRAFT'})
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(
        get_registry_record=lambda **kwargs: copy.deepcopy(current),
        update_registry_record=update, update_registry_record_status=status))
    if failure == 'identity':
        current['name'] = 'different_agent'
    if failure:
        with pytest.raises(RuntimeError):
            mirror.mirror(record)
        assert 'status' not in calls
    else:
        result = mirror.mirror(record)
        assert result['status'] == 'APPROVED' and result['action'] == 'updated'
        assert calls == ['update', 'status']
        assert 'PRIVATE_' not in json.dumps(current['descriptors'])


def test_mirror_names_do_not_alias_valid_punctuation_or_long_names():
    names = ['risk-tool', 'risk.tool', 'risk_tool', 'a' * 60 + 'x', 'a' * 60 + 'y']
    identifiers = [mirror._mirror_name(name) for name in names]
    assert len(set(identifiers)) == len(names)
    assert all(len(identifier) == 48 for identifier in identifiers)


def test_legacy_name_collision_is_not_adopted(monkeypatch):
    current = {'recordId': 'other', 'name': 'risk_tool', 'recordVersion': 'v1',
               'descriptors': {'custom': {'inlineContent': json.dumps({
                   'name': 'risk-tool', 'recordVersion': 'v1', 'recordType': 'CUSTOM'})}}}
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(
        list_registry_records=lambda **kwargs: {'registryRecords': [current]},
        get_registry_record=lambda **kwargs: current))
    assert mirror.find_record('risk_tool', 'v1') is None
    assert mirror.find_record('risk-tool', 'v1') == current


@pytest.mark.parametrize('kind,payload', [('MCP', {'tools': []}), ('SKILL', {'skillMd': 'Synthetic skill'})])
def test_typed_descriptor_failure_never_retries_as_custom(monkeypatch, kind, payload):
    attempts = []
    def create(**request):
        attempts.append(request)
        raise RuntimeError('synthetic provider rejection')
    monkeypatch.setattr(mirror, 'find_record', lambda *args: None)
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(create_registry_record=create))
    with pytest.raises(RuntimeError):
        mirror.mirror({'name': 'synthetic', 'recordVersion': 'v1', 'recordType': kind, 'payload': payload})
    assert len(attempts) == 1 and attempts[0]['descriptorType'] != 'CUSTOM'


def test_oversized_descriptor_is_rejected_without_truncation(monkeypatch):
    monkeypatch.setattr(mirror, 'ctl', lambda: pytest.fail('oversized content reached Registry'))
    with pytest.raises(ValueError):
        mirror.mirror({'name': 'synthetic', 'recordVersion': 'v1', 'recordType': 'SKILL',
                       'payload': {'skillMd': 'a' * 60001}})


def test_path_only_skill_does_not_silently_become_custom(monkeypatch):
    monkeypatch.setattr(mirror, 'ctl', lambda: pytest.fail('unsupported Skill reached Registry'))
    with pytest.raises(ValueError):
        mirror.mirror({'name': 'synthetic', 'recordVersion': 'v1', 'recordType': 'SKILL',
                       'payload': {'path': 'skills/synthetic.md'}})


@pytest.mark.parametrize("stage,field", [
    ("pre", "name"), ("pre", "recordVersion"), ("pre", "descriptorType"), ("pre", "descriptors"),
    ("post", "name"), ("post", "recordVersion"), ("post", "descriptorType"),
    ("post", "descriptors"), ("post", "status"),
])
def test_mirror_requires_exact_pre_status_and_post_status_evidence(monkeypatch, stage, field):
    record = {"name": "agent_probe", "recordVersion": "v1", "recordType": "AGENT", "status": "APPROVED"}
    current = {"name": "agent_probe", "recordVersion": "v1", "recordId": "synthetic", "status": "DRAFT",
               "descriptorType": "CUSTOM", "descriptors": {"custom": {"inlineContent": json.dumps(record)}}}
    reads, statuses = [], []
    def get(**kwargs):
        reads.append(True)
        result = copy.deepcopy(current)
        if len(reads) == (2 if stage == "pre" else 3):
            result[field] = {"custom": {"inlineContent": "{}"}} if field == "descriptors" else "WRONG"
        return result
    def update(**request):
        current.update(name=request["name"], descriptorType=request["descriptorType"], descriptors=request["descriptors"])
    def status(**request):
        statuses.append(True)
        current["status"] = request["status"]
    monkeypatch.setattr(mirror, "find_record", lambda *args: {"recordId": "synthetic", "status": "DRAFT"})
    monkeypatch.setattr(mirror, "ctl", lambda: SimpleNamespace(
        get_registry_record=get, update_registry_record=update, update_registry_record_status=status))
    with pytest.raises(RuntimeError):
        mirror.mirror(record)
    assert len(statuses) == (0 if stage == "pre" else 1)
