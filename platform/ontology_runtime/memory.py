"""Structured continuity events; never an authorization or source authority."""
from __future__ import annotations

from datetime import datetime, timezone

from workspace import ontology_schema as schema

STAGES = frozenset({"admitted", "context", "analyzed", "generated", "compiled", "verified", "completed", "failed"})


class Memory:
    def __init__(self, client, identifier, kms, key_arn, organization):
        self.client, self.identifier = client, identifier
        self.kms, self.key, self.organization = kms, key_arn, organization
        self.namespaces = {}

    def scope(self, claims):
        key = (claims["projectId"], claims["actor"])
        if key not in self.namespaces:
            result = self.kms.generate_mac(KeyId=self.key, MacAlgorithm="HMAC_SHA_256",
                Message=schema.canonical([self.organization, *key]))
            self.namespaces[key] = "actor-" + result["Mac"].hex()[:48]
        return {"actorId": self.namespaces[key], "sessionId": claims["executionId"]}

    def append(self, claims, stage, evidence_hash):
        if stage not in STAGES:
            raise ValueError("Unknown execution stage")
        schema._hash(evidence_hash)
        value = {"schemaVersion": 1, "executionId": claims["executionId"], "attemptId": claims["attemptId"],
                 "resourcesHash": claims["resourcesHash"], "stage": stage, "evidenceHash": evidence_hash}
        result = self.client.create_event(memoryId=self.identifier, **self.scope(claims),
            eventTimestamp=datetime.fromtimestamp(claims["iat"], timezone.utc), payload=[{"json": {"content": value}}],
            extractionMode="SKIP", clientToken=schema.digest(value))
        return {"backend": "agentcore-memory", "eventId": result["event"]["eventId"],
                "eventHash": schema.digest(value), "extraction": "SKIP"}

    def read(self, claims):
        response = self.client.list_events(memoryId=self.identifier, **self.scope(claims),
                                           includePayloads=True, maxResults=30)
        result = []
        for event in response.get("events", []):
            for payload in event.get("payload", []):
                value = payload.get("json", {}).get("content")
                if (isinstance(value, dict) and value.get("schemaVersion") == 1
                        and value.get("executionId") == claims["executionId"]
                        and value.get("attemptId") == claims["attemptId"]
                        and value.get("resourcesHash") == claims["resourcesHash"]
                        and value.get("stage") in STAGES):
                    schema._hash(value.get("evidenceHash"))
                    result.append({"stage": value["stage"], "evidenceHash": value["evidenceHash"]})
        return {"events": result, "truncated": bool(response.get("nextToken")),
                "authority": False}
