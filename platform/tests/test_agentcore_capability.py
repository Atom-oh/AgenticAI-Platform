import copy
import hashlib
import hmac
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ontology_runtime.capability import AuthorizationDenied, Capabilities


class Kms:
    """Physical KMS substitute; authorization tests exercise real token bytes."""
    enabled = True

    def describe_key(self, **kwargs):
        return {"KeyMetadata": {"KeyState": "Enabled" if self.enabled else "Disabled", "KeyUsage": "SIGN_VERIFY"}}

    def get_public_key(self, **kwargs):
        return {"PublicKey": b"synthetic-public-key", "SigningAlgorithms": ["RSASSA_PKCS1_V1_5_SHA_256"]}

    def sign(self, **kwargs):
        return {"Signature": hmac.digest(b"synthetic-test-key", kwargs["Message"], "sha256")}

    def verify(self, **kwargs):
        return {"SignatureValid": hmac.compare_digest(self.sign(**kwargs)["Signature"], kwargs["Signature"])}


@pytest.fixture
def execution():
    now = [1800000000]
    key = {"id": "cap-v1", "keyArn": "synthetic-kms-key", "purpose": "execution-capability", "state": "active",
           "algorithm": "RS256", "notBefore": now[0] - 1, "notAfter": now[0] + 3600,
           "publicKeyHash": hashlib.sha256(b"synthetic-public-key").hexdigest()}
    kms = Kms()
    caps = Capabilities(kms, lambda identifier: key if identifier == "cap-v1" else None, clock=lambda: now[0])
    ledger = {"executionId": "execution-" + "a" * 32, "attemptId": "attempt-" + "b" * 32,
        "runtimeSessionId": "session-" + "c" * 32, "projectId": "project-a", "actor": "actor-a",
        "workloadIdentity": "ontology-workload", "operations": ["ontology.source", "execution.stage"],
        "resourcesHash": "d" * 64, "status": "running", "authorizationExpiresAt": now[0] + 300,
        "deadline": now[0] + 200, "version": 1}
    token, binding = caps.issue(ledger, key["id"])
    ledger.update(binding)
    return caps, token, ledger, key, kms, now


def verify(execution):
    caps, token, ledger, *_ = execution
    return caps.verify(token, lambda identifier: ledger if identifier == ledger["executionId"] else None,
                       operation="ontology.source", workload="ontology-workload")


def test_signature_and_current_ledger_bind_every_execution_dimension(execution):
    assert verify(execution)[0]["projectId"] == "project-a"
    for field in ("actor", "projectId", "attemptId", "runtimeSessionId", "resourcesHash", "workloadIdentity"):
        changed = list(execution)
        changed[2] = {**execution[2], field: "other-value"}
        with pytest.raises(AuthorizationDenied):
            verify(changed)


def test_expiry_cancellation_and_key_revocation_are_not_cached_authority(execution):
    caps, token, ledger, key, kms, now = execution
    assert verify(execution)
    for status in ("cancelled", "failed", "completed", "result-ready"):
        ledger["status"] = status
        with pytest.raises(AuthorizationDenied):
            verify(execution)
    ledger["status"] = "running"
    key["state"] = "revoked"
    with pytest.raises(AuthorizationDenied):
        verify(execution)
    key["state"] = "active"
    kms.enabled = False
    with pytest.raises(AuthorizationDenied):
        verify(execution)
    kms.enabled = True
    now[0] += 200
    with pytest.raises(AuthorizationDenied):
        verify(execution)


def test_token_mutation_and_out_of_scope_operations_fail(execution):
    caps, token, ledger, *_ = execution
    for changed in [token[:-3] + "aaa", token + ".extra", "", "not-a-token"]:
        with pytest.raises(AuthorizationDenied):
            caps.verify(changed, lambda _: ledger, operation="ontology.source", workload="ontology-workload")
    with pytest.raises(AuthorizationDenied):
        caps.verify(token, lambda _: ledger, operation="execution.finish", workload="ontology-workload")


def test_retired_keys_verify_only_existing_capabilities_and_cannot_issue_new_ones(execution):
    caps, token, ledger, key, *_ = execution
    key["state"] = "retired"
    assert verify(execution)
    with pytest.raises(AuthorizationDenied):
        caps.issue(ledger, key["id"])


def test_evidence_key_cannot_mint_execution_capabilities(execution):
    caps, _, ledger, key, *_ = execution
    key["purpose"] = "runtime-evidence"
    with pytest.raises(AuthorizationDenied):
        caps.issue(ledger, key["id"])
