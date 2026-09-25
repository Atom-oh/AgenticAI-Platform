"""Offline-only execution fakes. Not packaged into any Lambda or Runtime image."""
import hashlib, hmac, json, sys

_KEY = b"offline-test-key-not-a-secret"


class TestKeyVerifier:
    __test__ = False     # not a pytest test class
    key_id = "offline-test"
    offline = True

    def __init__(self):
        if "pytest" not in sys.modules:
            raise PermissionError("the offline test verifier is constructible only under pytest")
        self.epoch = 1

    def revision(self):
        """The offline key-registry revision; a rotation or revocation increments it."""
        return self.epoch

    def sign(self, body):
        return hmac.new(_KEY, json.dumps(body, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()

    def verify(self, receipt):
        body = {k: v for k, v in receipt.items() if k != "signature"}
        return receipt.get("keyId") == self.key_id and hmac.compare_digest(receipt.get("signature", ""), self.sign(body))


RUNTIME_STAGES = ("context", "verify", "analyze")


def receipt(verifier, *, job, stage, nonce, objects=(), extra=None):
    extra = dict(extra or {})
    if "service" not in extra and stage in RUNTIME_STAGES:      # the attempt's own Runtime session observed it
        extra["service"] = {"kind": "runtime", "sessionId": job["attempt"]["sessionId"]}
    body = {"schemaVersion": 1, "executionId": job["id"], "attemptId": job["attempt"]["id"],
            "fence": job["fence"], "sessionId": job["attempt"]["sessionId"], "stage": stage, "nonce": nonce,
            "profileHash": job["profile"]["hash"], "admissions": job["admissions"],
            "objects": list(objects), "keyId": verifier.key_id, **extra}
    return {**body, "signature": verifier.sign(body)}
