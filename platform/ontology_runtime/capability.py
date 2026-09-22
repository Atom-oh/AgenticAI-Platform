"""KMS execution capabilities verified against a live attempt ledger."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

from workspace import ontology_schema as schema

ISSUER = "platform-ontology-execution-v1"
AUDIENCE = "platform-ontology-lambda-mcp-v1"
ALGORITHM = "RSASSA_PKCS1_V1_5_SHA_256"
CLAIMS = frozenset({"iss", "aud", "actor", "projectId", "executionId", "attemptId", "runtimeSessionId",
    "workloadIdentity", "operations", "resourcesHash", "authorizationExpiresAt", "iat", "exp", "jti"})
LIVE = frozenset({"admitted", "running"})


class AuthorizationDenied(RuntimeError):
    def __init__(self):
        super().__init__("Execution authorization is unavailable or no longer current")


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value):
    if not isinstance(value, str) or not value or len(value) > 16000:
        raise AuthorizationDenied()
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError):
        raise AuthorizationDenied() from None
    if _encode(raw) != value:
        raise AuthorizationDenied()
    return raw


def _claims(value, now):
    try:
        schema._fields(value, CLAIMS)
        if value["iss"] != ISSUER or value["aud"] != AUDIENCE:
            raise ValueError()
        for field in ("actor", "projectId", "executionId", "attemptId", "runtimeSessionId", "workloadIdentity", "jti"):
            schema._text(value[field], 256)
        for field in ("iat", "exp", "authorizationExpiresAt"):
            if type(value[field]) is not int:
                raise ValueError()
        if not value["iat"] <= now < value["exp"] <= min(value["iat"] + 900, value["authorizationExpiresAt"]):
            raise ValueError()
        if len(value["runtimeSessionId"]) < 33 or len(value["jti"]) < 32:
            raise ValueError()
        operations = value["operations"]
        if (not isinstance(operations, list) or not 1 <= len(operations) <= 12
                or any(name not in {"ontology.context", "ontology.source", "ontology.impact",
                    "ontology.publish", "execution.stage", "execution.finish"} for name in operations)
                or len(set(operations)) != len(operations)):
            raise ValueError()
        schema._hash(value["resourcesHash"])
    except (ValueError, TypeError, KeyError):
        raise AuthorizationDenied() from None
    return value


class Capabilities:
    """Signer roles and verifier roles receive different KMS/IAM grants."""
    def __init__(self, kms, key_registry, *, clock=time.time):
        self.kms, self.registry, self.clock = kms, key_registry, clock

    def _key(self, identifier, *, signing=False):
        # The registry lookup is always authoritative, not cached with a token
        # or selected from its jku/x5u fields. Callers fix the allowed key registry.
        record = self.registry(identifier)
        now = int(self.clock())
        if (not isinstance(record, dict) or record.get("purpose") != "execution-capability"
                or record.get("state") not in ({"active"} if signing else {"active", "retired"})
                or record.get("algorithm") != "RS256" or record.get("id") != identifier
                or not record.get("notBefore", now + 1) <= now < record.get("notAfter", 0)):
            raise AuthorizationDenied()
        metadata = self.kms.describe_key(KeyId=record["keyArn"])["KeyMetadata"]
        if metadata.get("KeyState") != "Enabled" or metadata.get("KeyUsage") != "SIGN_VERIFY":
            raise AuthorizationDenied()
        public = self.kms.get_public_key(KeyId=record["keyArn"])
        if (hashlib.sha256(public["PublicKey"]).hexdigest() != record.get("publicKeyHash")
                or ALGORITHM not in public.get("SigningAlgorithms", [])):
            raise AuthorizationDenied()
        return record

    def issue(self, ledger, key_id):
        now = int(self.clock())
        if ledger.get("status") not in LIVE:
            raise AuthorizationDenied()
        key = self._key(key_id, signing=True)
        claims = {name: ledger[name] for name in ("actor", "projectId", "executionId", "attemptId",
            "runtimeSessionId", "workloadIdentity", "operations", "resourcesHash", "authorizationExpiresAt")}
        claims.update(iss=ISSUER, aud=AUDIENCE, iat=now, exp=min(now + 900,
            ledger["authorizationExpiresAt"], ledger["deadline"]), jti=secrets.token_hex(24))
        _claims(claims, now)
        message = _encode(schema.canonical({"alg": "RS256", "typ": "JWT", "kid": key_id})) + "." + _encode(schema.canonical(claims))
        signature = self.kms.sign(KeyId=key["keyArn"], Message=hashlib.sha256(message.encode()).digest(),
                                  MessageType="DIGEST", SigningAlgorithm=ALGORITHM)["Signature"]
        token = message + "." + _encode(signature)
        return token, {"capabilityHash": hashlib.sha256(token.encode()).hexdigest(),
                       "capabilityClaims": claims, "capabilityKeyId": key_id}

    def verify(self, token, load_execution, *, operation, workload, allow_result_ready=False):
        try:
            if not isinstance(token, str) or len(token) > 16000:
                raise AuthorizationDenied()
            header64, claims64, signature64 = token.split(".")
            header, claims = json.loads(_decode(header64)), json.loads(_decode(claims64))
            if set(header) != {"alg", "typ", "kid"} or header["alg"] != "RS256" or header["typ"] != "JWT":
                raise AuthorizationDenied()
            key = self._key(header["kid"])
            value = self.kms.verify(KeyId=key["keyArn"],
                Message=hashlib.sha256((header64 + "." + claims64).encode()).digest(), MessageType="DIGEST",
                Signature=_decode(signature64), SigningAlgorithm=ALGORITHM)
            if value.get("SignatureValid") is not True:
                raise AuthorizationDenied()
            _claims(claims, int(self.clock()))
            ledger = load_execution(claims["executionId"])
            live = LIVE | ({"result-ready"} if allow_result_ready and operation == "execution.finish" else set())
            if (not ledger or ledger.get("status") not in live
                    or ledger.get("capabilityKeyId") != header["kid"]
                    or ledger.get("capabilityHash") != hashlib.sha256(token.encode()).hexdigest()
                    or ledger.get("capabilityClaims") != claims
                    or claims["workloadIdentity"] != workload
                    or operation not in claims["operations"]
                    or int(self.clock()) >= ledger.get("deadline", 0)):
                raise AuthorizationDenied()
            for name in ("actor", "projectId", "executionId", "attemptId", "runtimeSessionId",
                         "workloadIdentity", "resourcesHash", "authorizationExpiresAt", "operations"):
                if ledger.get(name) != claims[name]:
                    raise AuthorizationDenied()
            return claims, ledger
        except AuthorizationDenied:
            raise
        except Exception:
            raise AuthorizationDenied() from None


class Evidence:
    """Separate KMS key and purpose; cannot create accepted execution capabilities."""
    def __init__(self, kms, key_arn, state_provider=None):
        self.kms, self.key, self.state = kms, key_arn, state_provider

    def sign(self, claims, result):
        body = {key: claims[key] for key in ("executionId", "attemptId", "runtimeSessionId", "projectId", "iat")}
        body.update(resultHash=schema.digest(result), inputHash=result["execution"]["inputHash"],
                    purpose="runtime-evidence-v1")
        signature = self.kms.sign(KeyId=self.key, Message=hashlib.sha256(schema.canonical(body)).digest(),
            MessageType="DIGEST", SigningAlgorithm=ALGORITHM)["Signature"]
        return {"claims": body, "signature": _encode(signature)}

    def verify(self, claims, result, receipt):
        try:
            key = self.state() if callable(self.state) else None
            if (not key or key.get("purpose") != "runtime-evidence" or key.get("keyArn") != self.key
                    or key.get("state") not in {"active", "retired"} or key.get("algorithm") != "RS256"
                    or not key.get("notBefore", claims["iat"] + 1) <= claims["iat"] < key.get("notAfter", 0)):
                raise AuthorizationDenied()
            public = self.kms.get_public_key(KeyId=self.key)
            if hashlib.sha256(public["PublicKey"]).hexdigest() != key.get("publicKeyHash"):
                raise AuthorizationDenied()
            expected = {key: claims[key] for key in ("executionId", "attemptId", "runtimeSessionId", "projectId", "iat")}
            expected.update(resultHash=schema.digest(result), inputHash=result["execution"]["inputHash"],
                            purpose="runtime-evidence-v1")
            if receipt["claims"] != expected:
                raise AuthorizationDenied()
            if self.kms.describe_key(KeyId=self.key)["KeyMetadata"]["KeyState"] != "Enabled":
                raise AuthorizationDenied()
            verified = self.kms.verify(KeyId=self.key, Message=hashlib.sha256(schema.canonical(expected)).digest(),
                MessageType="DIGEST", SigningAlgorithm=ALGORITHM, Signature=_decode(receipt["signature"]))
            if verified.get("SignatureValid") is not True:
                raise AuthorizationDenied()
        except Exception:
            raise AuthorizationDenied() from None
