"""Offline-only execution fakes. Not packaged into any Lambda or Runtime image."""
import hashlib, hmac, json

_KEY = b"offline-test-key-not-a-secret"


class TestKeyVerifier:
    __test__ = False     # not a pytest test class
    key_id = "offline-test"

    def sign(self, body):
        return hmac.new(_KEY, json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()

    def verify(self, receipt):
        body = {k: v for k, v in receipt.items() if k != "signature"}
        return receipt.get("keyId") == self.key_id and hmac.compare_digest(receipt.get("signature", ""), self.sign(body))


def receipt(verifier, *, job, stage, nonce, objects=(), extra=None):
    body = {"schemaVersion": 1, "executionId": job["id"], "attemptId": job["attempt"]["id"],
            "fence": job["fence"], "sessionId": job["attempt"]["sessionId"], "stage": stage, "nonce": nonce,
            "profileHash": job["profile"]["hash"], "admissions": job["admissions"],
            "objects": list(objects), "keyId": verifier.key_id, **(extra or {})}
    return {**body, "signature": verifier.sign(body)}
