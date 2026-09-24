import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'api')]
from agentcore import registry_mirror as mirror

MANUAL_REGISTRY_CHECK = mirror._manual_registry


@pytest.fixture(autouse=True)
def manual_registry(monkeypatch):
    monkeypatch.setattr(mirror, '_manual_registry', lambda: None)


@pytest.mark.parametrize('failure', [None, 'update', 'unconfirmed', 'identity'])
def test_agent_mirror_replaces_old_private_descriptor_before_approval(monkeypatch, failure):
    record = {'name': 'agent_probe', 'recordVersion': 'v1', 'recordType': 'AGENT',
              'description': 'Synthetic', 'status': 'APPROVED',
              'payload': {'systemPrompt': 'PRIVATE_PROMPT_MARKER', 'skillMd': 'PRIVATE_SKILL_MARKER'}}
    current = {'name': mirror._mirror_name('agent_probe', 'v1'), 'recordVersion': 'v1', 'recordId': 'synthetic',
               'status': 'DRAFT', 'descriptorType': 'CUSTOM',
               'descriptors': {'custom': {'inlineContent': json.dumps(record)}}}
    calls = []

    def update(**request):
        calls.append('update')
        assert set(request['description']) == {'optionalValue'}
        assert set(request['descriptors']) == {'optionalValue'}
        if failure == 'update':
            raise RuntimeError('synthetic failure')
        if failure != 'unconfirmed':
            current.update(name=request.get('name', current['name']), descriptorType=request['descriptorType'], descriptors={'custom': request['descriptors']['optionalValue']['custom']['optionalValue']}, recordVersion='2')

        return {'recordVersion': current['recordVersion']}

    def submit(**request):
        current['status'] = 'PENDING_APPROVAL'
        return {'status': current['status']}

    def status(**request):
        calls.append('status')
        assert 'PRIVATE_' not in json.dumps(current['descriptors'])
        current['status'] = request['status']

    monkeypatch.setattr(mirror, 'find_record', lambda *args: {
        'recordId': 'synthetic', 'name': 'agent_probe', 'recordVersion': 'v1', 'status': 'DRAFT'})
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(
        get_registry_record=lambda **kwargs: copy.deepcopy(current),
        update_registry_record=update, submit_registry_record_for_approval=submit, update_registry_record_status=status))
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
    identifiers = [mirror._mirror_name(name, 'v1') for name in names]
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
    current = {"name": mirror._mirror_name("agent_probe", "v1"), "recordVersion": "v1", "recordId": "synthetic", "status": "DRAFT",
               "descriptorType": "CUSTOM", "descriptors": {"custom": {"inlineContent": json.dumps(record)}}}
    reads, statuses = [], []
    def get(**kwargs):
        reads.append(True)
        result = copy.deepcopy(current)
        if stage == "pre" and len(reads) == 2 or stage == "post" and statuses:
            result[field] = {"custom": {"inlineContent": "{}"}} if field == "descriptors" else "WRONG"
        return result
    def update(**request):
        current.update(name=request.get("name", current["name"]), descriptorType=request["descriptorType"], descriptors={"custom": request["descriptors"]["optionalValue"]["custom"]["optionalValue"]}, recordVersion="2")
        return {'recordVersion': current['recordVersion']}

    def submit(**request):
        current['status'] = 'PENDING_APPROVAL'
        return {'status': current['status']}

    def status(**request):
        statuses.append(True)
        current["status"] = request["status"]
    monkeypatch.setattr(mirror, "find_record", lambda *args: {"recordId": "synthetic", "status": "DRAFT"})
    monkeypatch.setattr(mirror, "ctl", lambda: SimpleNamespace(
        get_registry_record=get, update_registry_record=update, submit_registry_record_for_approval=submit, update_registry_record_status=status))
    with pytest.raises(RuntimeError):
        mirror.mirror(record)
    assert len(statuses) == (0 if stage == "pre" else 1)


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_mirror_waits_for_complete_async_descriptor_before_approval(monkeypatch, operation):
    record = {'name': 'agent_probe', 'recordVersion': 'v1', 'recordType': 'AGENT',
              'description': 'Synthetic', 'status': 'APPROVED'}
    dtype, descriptors = mirror._descriptor(record)
    current = {'recordId': 'synthetic', 'name': mirror._mirror_name(record['name'], 'v1'), 'recordVersion': 'v1',
               'status': 'DRAFT', 'descriptorType': dtype, 'descriptors': descriptors}
    if operation == 'update':
        current['descriptors'] = {'custom': {'inlineContent': json.dumps({**record, 'private': 'PRIVATE_MARKER'})}}
    pending, statuses = [], []
    def create(**request):
        pending.extend(['CREATING', 'CREATING'])
        return {'recordArn': 'synthetic'}
    def update(**request):
        current.update(name=request.get('name', current['name']), descriptorType=request['descriptorType'], descriptors={'custom': request['descriptors']['optionalValue']['custom']['optionalValue']}, recordVersion='2')
        pending.extend(['UPDATING', 'UPDATING'])
        return {'recordVersion': current['recordVersion']}
    def get(**kwargs):
        return {**copy.deepcopy(current), 'status': pending.pop(0) if pending else current['status']}

    def submit(**request):
        current['status'] = 'PENDING_APPROVAL'
        return {'status': current['status']}

    def status(**request):
        assert not pending and current['descriptors'] == descriptors
        statuses.append(True)
        current['status'] = request['status']
    monkeypatch.setattr(mirror.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(mirror, 'find_record', lambda *args: None if operation == 'create' else {'recordId': 'synthetic'})
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(create_registry_record=create,
        update_registry_record=update, get_registry_record=get, submit_registry_record_for_approval=submit, update_registry_record_status=status))
    assert mirror.mirror(record)['status'] == 'APPROVED' and statuses == [True]


@pytest.mark.parametrize('configuration', [None, {}, {'autoApproval': True}])
def test_unsafe_registry_approval_mode_is_rejected_before_mirror_writes(monkeypatch, configuration):
    monkeypatch.setattr(mirror, '_manual_registry', MANUAL_REGISTRY_CHECK)
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(
        get_registry=lambda **kwargs: {'status': 'READY', 'approvalConfiguration': configuration or {}},
        create_registry_record=lambda **kwargs: pytest.fail('unreviewed auto-approval registry was used')))
    with pytest.raises(mirror.MirrorUnsupported):
        mirror.mirror({'name': 'synthetic', 'recordVersion': 'v1', 'recordType': 'AGENT', 'status': 'REJECTED'})


def test_local_version_identity_is_independent_of_service_revision(monkeypatch):
    name = 'version_probe'
    current = {'recordId': 'synthetic', 'name': mirror._mirror_name(name, 'v1'), 'recordVersion': '19'}
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(list_registry_records=lambda **kwargs: {'registryRecords': [current]}))
    assert mirror.find_record(name, 'v1') == current
    assert mirror.find_record(name, 'v2') is None
    assert mirror._mirror_name(name, 'v1') != mirror._mirror_name(name, 'v2')


def test_immutable_archive_is_retained_without_claiming_metadata_replacement(monkeypatch):
    record = {'name': 'archive_probe', 'recordVersion': 'v1', 'recordType': 'AGENT', 'status': 'DEPRECATED'}
    current = {'recordId': 'synthetic', 'name': mirror._mirror_name(record['name'], 'v1'), 'recordVersion': '19',
               'status': 'DEPRECATED', 'descriptorType': 'CUSTOM',
               'descriptors': {'custom': {'inlineContent': json.dumps({**record, 'private': 'PRIVATE_MARKER'})}}}
    monkeypatch.setattr(mirror, 'find_record', lambda *args: current)
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(get_registry_record=lambda **kwargs: current))
    result = mirror.mirror(record)
    assert result['archived'] and result['status'] == 'DEPRECATED' and result['metadataCurrent'] is False
    assert 'PRIVATE_' not in json.dumps(result)


def test_legacy_active_mirror_is_retired_before_clean_replacement(monkeypatch):
    record = {'name': 'legacy_probe', 'recordVersion': 'v1', 'recordType': 'AGENT', 'status': 'APPROVED'}
    old = {'recordId': 'old', 'name': mirror._old_hash_name(record['name']), 'recordVersion': 'v1',
           'status': 'APPROVED', 'descriptorType': 'CUSTOM',
           'descriptors': {'custom': {'inlineContent': json.dumps({**record, 'private': 'PRIVATE_MARKER'})}}}
    rows = {'old': old}
    def create(**request):
        assert old['status'] == 'DEPRECATED' and request['recordVersion'] == '1'
        assert 'PRIVATE_' not in json.dumps(request)
        rows['new'] = {**request, 'recordId': 'new', 'recordVersion': '1', 'status': 'DRAFT'}
        return {'recordArn': 'new'}
    def status(**request):
        rows[request['recordId']]['status'] = request['status']
    def submit(**request):
        rows[request['recordId']]['status'] = 'PENDING_APPROVAL'
    monkeypatch.setattr(mirror, 'find_record', lambda *args: old)
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(create_registry_record=create,
        get_registry_record=lambda **kwargs: copy.deepcopy(rows[kwargs['recordId']]),
        update_registry_record_status=status, submit_registry_record_for_approval=submit))
    result = mirror.mirror(record)
    assert result['recordId'] == 'new' and result['status'] == 'APPROVED'
    assert old['status'] == 'DEPRECATED' and 'PRIVATE_' in json.dumps(old['descriptors'])


def test_retiring_legacy_mirror_does_not_create_a_replacement_archive(monkeypatch):
    record = {'name': 'retirement_probe', 'recordVersion': 'v1', 'recordType': 'AGENT', 'status': 'DEPRECATED'}
    old = {'recordId': 'old', 'name': mirror._old_hash_name(record['name']), 'recordVersion': 'v1',
           'status': 'APPROVED', 'descriptorType': 'CUSTOM',
           'descriptors': {'custom': {'inlineContent': json.dumps(record)}}}
    def status(**request):
        old['status'] = request['status']
    monkeypatch.setattr(mirror, 'find_record', lambda *args: old)
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(
        get_registry_record=lambda **kwargs: copy.deepcopy(old), update_registry_record_status=status))
    result = mirror.mirror(record)
    assert result['archived'] and result['recordId'] == 'old' and old['status'] == 'DEPRECATED'


@pytest.mark.parametrize('kind', ['MCP', 'AGENT_SKILLS'])
@pytest.mark.parametrize('has_current', [False, True])
def test_active_unmapped_typed_legacy_never_reports_completed_sync(monkeypatch, kind, has_current):
    name, version = 'typed_probe', 'v1'
    legacy = {'recordId': 'old', 'name': mirror._old_hash_name(name), 'recordVersion': version,
              'status': 'APPROVED', 'descriptorType': kind, 'descriptors': {}}
    rows = [legacy]
    if has_current:
        rows.append({'recordId': 'new', 'name': mirror._mirror_name(name, version), 'status': 'DEPRECATED'})
    monkeypatch.setattr(mirror, 'ctl', lambda: SimpleNamespace(
        list_registry_records=lambda **kwargs: {'registryRecords': rows},
        get_registry_record=lambda **kwargs: legacy))
    with pytest.raises(mirror.LegacyMirrorUnresolved):
        mirror.find_record(name, version)
    legacy['status'] = 'DEPRECATED'
    assert (mirror.find_record(name, version) is not None) is has_current
