"""Registered KMS verifier for platform-execution/1 receipts; it cannot sign."""
from __future__ import annotations

import base64
import hashlib
import json
import re

from workspace.execution_ledger import register_verifier

KEY_OWNER = "ontology-key-registry"
ALGORITHM = "RSASSA_PKCS1_V1_5_SHA_256"
DOMAIN = b"platform-execution/1:receipt\n"


def receipt_message(receipt):
    body = {key: value for key, value in receipt.items() if key != "signature"}
    return hashlib.sha256(DOMAIN + json.dumps(body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).digest()


def decode_signature(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid signature encoding")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class KmsVerifier:
    def __init__(self, storage, kms, key_ids, *, owner=KEY_OWNER):
        if (not isinstance(key_ids, (list, tuple)) or not 1 <= len(key_ids) <= 4
                or len(set(key_ids)) != len(key_ids)
                or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) for key in key_ids)):
            raise ValueError("A bounded deployment-owned evidence-key registry is required")
        self.storage, self.kms, self.key_ids, self.owner = storage, kms, tuple(key_ids), owner

    def _record(self, key_id):
        if key_id not in self.key_ids:
            raise ValueError("Unregistered evidence key")
        row = self.storage.get(self.owner, "ac_key", key_id)
        now = self.storage.clock()
        if (not row or row.get("id") != key_id or row.get("purpose") != "runtime-evidence"
                or row.get("state") not in {"active", "retired"} or row.get("algorithm") != "RS256"
                or type(row.get("version")) is not int or row["version"] < 1
                or type(row.get("notBefore")) is not int or type(row.get("notAfter")) is not int
                or not row["notBefore"] * 1000 <= now < row["notAfter"] * 1000
                or not isinstance(row.get("keyArn"), str) or ":key/" not in row["keyArn"]
                or not isinstance(row.get("publicKeyHash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["publicKeyHash"])):
            raise ValueError("Evidence key is unavailable")
        return row

    def _kms_current(self, row):
        metadata = self.kms.describe_key(KeyId=row["keyArn"])["KeyMetadata"]
        public = self.kms.get_public_key(KeyId=row["keyArn"])
        if (metadata.get("KeyState") != "Enabled" or metadata.get("KeyUsage") != "SIGN_VERIFY"
                or metadata.get("KeySpec") not in {"RSA_2048", "RSA_3072", "RSA_4096"}
                or public.get("KeyUsage") != "SIGN_VERIFY" or ALGORITHM not in public.get("SigningAlgorithms", [])
                or hashlib.sha256(public["PublicKey"]).hexdigest() != row["publicKeyHash"]):
            raise ValueError("Evidence key is unavailable")

    def verify(self, receipt):
        try:
            row = self._record(receipt.get("keyId"))
            now, issued, expiry = self.storage.clock(), receipt.get("iat"), receipt.get("exp")
            if (type(issued) is not int or type(expiry) is not int
                    or not row["notBefore"] * 1000 <= issued <= now < expiry <= row["notAfter"] * 1000):
                return False
            self._kms_current(row)
            valid = self.kms.verify(KeyId=row["keyArn"], Message=receipt_message(receipt), MessageType="DIGEST",
                Signature=decode_signature(receipt.get("signature")), SigningAlgorithm=ALGORITHM)
            return valid.get("SignatureValid") is True
        except Exception:
            return False

    def revision(self):
        rows = [self._record(key) for key in self.key_ids]
        return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def authorization(self):
        """Key versions and expiry join the actual conditional transaction."""
        rows = [self._record(key) for key in self.key_ids]
        for row in rows:
            self._kms_current(row)
        return {"checks": [{"owner": self.owner, "kind": "ac_key", "id": row["id"],
                            "version": row["version"]} for row in rows],
                "expiresAt": min(row["notAfter"] * 1000 for row in rows)}


register_verifier(KmsVerifier)
