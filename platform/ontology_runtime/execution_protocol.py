"""B0 capability and machine Gateway protocol; no alternate execution store."""
from __future__ import annotations

import hashlib
import json
import secrets

from ontology_runtime.capability import Capabilities, AuthorizationDenied, _encode, _decode, ALGORITHM
from workspace import ontology_schema as schema
from workspace.execution_ledger import supported_execution
from workspace.execution_verifier import KEY_OWNER, receipt_message

ISSUER, AUDIENCE = "platform-execution/1", "platform-execution-lambda/1"
FIELDS = {"iss", "aud", "jti", "iat", "exp", "actor", "projectId", "executionId", "attemptId", "fence",
          "runtimeSessionId", "workloadIdentity", "operation", "manifestHash", "profileHash",
          "backendConfigRevision", "authorizationExpiresAt"}


def binding(job, workload):
    return {"actor": job["actor"], "projectId": job["projectId"], "executionId": job["id"],
            "attemptId": job["attempt"]["id"], "fence": job["fence"],
            "runtimeSessionId": job["attempt"]["sessionId"], "workloadIdentity": workload,
            "operation": job["operation"], "manifestHash": job["manifest"]["hash"],
            "profileHash": job["profile"]["hash"], "backendConfigRevision": job["backendConfigRevision"],
            "authorizationExpiresAt": job["authorizationExpiresAt"] // 1000}


class ExecutionCapabilities:
    def __init__(self, storage, kms, configuration):
        self.storage, self.kms, self.configuration = storage, kms, configuration
        key_id = configuration["capabilityKeyId"]
        self.keys = Capabilities(kms, lambda identifier: storage.get(KEY_OWNER, "ac_key", identifier)
                                 if identifier == key_id else None, clock=lambda: storage.clock() / 1000)

    def issue(self, job):
        if (not supported_execution(job) or job["status"] != "dispatched" or job["operation"] != "source.analyze"
                or job["backendConfigRevision"] != self.configuration["revision"]):
            raise AuthorizationDenied()
        key = self.keys._key(self.configuration["capabilityKeyId"], signing=True)
        now = self.storage.clock() // 1000
        claims = {**binding(job, self.configuration["workloadIdentityArn"]), "iss": ISSUER, "aud": AUDIENCE,
                  "jti": secrets.token_hex(24), "iat": now,
                  "exp": min(now + 900, job["deadlineAt"] // 1000, job["authorizationExpiresAt"] // 1000, key["notAfter"])}
        if claims["exp"] <= now:
            raise AuthorizationDenied()
        header = {"alg": "RS256", "typ": "JWT", "kid": key["id"]}
        message = _encode(schema.canonical(header)) + "." + _encode(schema.canonical(claims))
        signature = self.kms.sign(KeyId=key["keyArn"], Message=hashlib.sha256(message.encode()).digest(),
                                  MessageType="DIGEST", SigningAlgorithm=ALGORITHM)["Signature"]
        token = message + "." + _encode(signature)
        return token, claims, {"owner": KEY_OWNER, "kind": "ac_key", "id": key["id"], "version": key["version"]}

    def verify(self, token, *, allow_success=False):
        try:
            if not isinstance(token, str) or len(token) > 16000:
                raise AuthorizationDenied()
            head, body, signature = token.split(".")
            header, claims = json.loads(_decode(head)), json.loads(_decode(body))
            if (set(header) != {"alg", "typ", "kid"} or header["alg"] != "RS256" or header["typ"] != "JWT"
                    or set(claims) != FIELDS or claims["iss"] != ISSUER or claims["aud"] != AUDIENCE
                    or not isinstance(claims["jti"], str) or len(claims["jti"]) != 48):
                raise AuthorizationDenied()
            key = self.keys.active(header["kid"])
            if self.kms.verify(KeyId=key["keyArn"], Message=hashlib.sha256((head + "." + body).encode()).digest(),
                               MessageType="DIGEST", Signature=_decode(signature), SigningAlgorithm=ALGORITHM).get("SignatureValid") is not True:
                raise AuthorizationDenied()
            now = self.storage.clock()
            if (any(type(claims[field]) is not int for field in ("iat", "exp", "authorizationExpiresAt", "fence"))
                    or not key["notBefore"] <= claims["iat"] <= now // 1000 < claims["exp"]
                    or claims["exp"] > min(claims["iat"] + 900, claims["authorizationExpiresAt"], key["notAfter"])):
                raise AuthorizationDenied()
            owner = "project:" + claims["projectId"]
            job = self.storage.get(owner, "job", claims["executionId"])
            live = {"dispatched", "running"} | ({"succeeded"} if allow_success else set())
            if (not supported_execution(job) or job["status"] not in live or job["operation"] != "source.analyze"
                    or job["backendConfigRevision"] != self.configuration["revision"]
                    or binding(job, self.configuration["workloadIdentityArn"]) != {k: claims[k] for k in binding(job, self.configuration["workloadIdentityArn"])}
                    or now >= min(job["deadlineAt"], job["authorizationExpiresAt"], job["attempt"]["leaseExpiresAt"])):
                raise AuthorizationDenied()
            stored = job["attempt"].get("capability", {})
            if (stored.get("digest") != hashlib.sha256(token.encode()).hexdigest() or stored.get("claims") != claims
                    or stored.get("keyId") != header["kid"] or stored.get("expiresAt") != claims["exp"] * 1000):
                raise AuthorizationDenied()
            project = self.storage.get(owner, "project", job["projectId"])
            if (not project or project.get("status") != "active"
                    or project.get("authorityRevision") != job["authority"]["authorityRevision"]
                    or schema.digest(project.get("members")) != job["authority"]["membershipDigest"]
                    or project["members"].get(job["actor"], {}).get("role") != job["actorRole"]):
                raise AuthorizationDenied()
            return claims, job, key, project
        except AuthorizationDenied:
            raise
        except Exception:
            raise AuthorizationDenied() from None


class ExecutionAccess:
    """The capability and Gateway registry predicates join each real transaction."""
    def __init__(self, capabilities, token, metadata, *, allow_success=False):
        self.capabilities, self.storage, self.token = capabilities, capabilities.storage, token
        self.metadata, self.allow_success = dict(metadata), allow_success

    def authorize(self, owner, identifier):
        claims, job, key, project = self.capabilities.verify(self.token, allow_success=self.allow_success)
        gateway = self.storage.get(KEY_OWNER, "ac_key", "gateway-binding")
        if (owner != "project:" + job["projectId"] or identifier != job["id"] or not gateway
                or gateway.get("workloadIdentityArn") != claims["workloadIdentity"]
                or self.metadata.get("bedrockAgentCoreGatewayId") != gateway.get("gatewayId")
                or self.metadata.get("bedrockAgentCoreTargetId") != gateway.get("targetId")
                or self.metadata.get("bedrockAgentCoreToolName") != "ontology___execution"):
            raise AuthorizationDenied()
        return {"checks": [{"owner": KEY_OWNER, "kind": "ac_key", "id": row["id"], "version": row["version"]}
                           for row in (key, gateway)] + [{"owner": owner, "kind": "project", "id": project["id"], "version": project["version"]}],
                "expiresAt": min(claims["exp"] * 1000, key["notAfter"] * 1000, job["deadlineAt"])}


class ReceiptSigner:
    """Runtime-only signer. The production ledger accepts only the separate verifier."""
    def __init__(self, kms, key_arn, key_id):
        self.kms, self.key_arn, self.key_id = kms, key_arn, key_id

    def sign(self, body):
        body = {**body, "keyId": self.key_id}
        result = self.kms.sign(KeyId=self.key_arn, Message=receipt_message(body), MessageType="DIGEST", SigningAlgorithm=ALGORITHM)
        return {**body, "signature": _encode(result["Signature"])}
