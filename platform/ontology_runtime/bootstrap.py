"""CloudFormation-only provisioning of the fixed capability and Gateway registry."""
import hashlib
import time

import boto3
from workspace.storage import Storage

OWNER = "ontology-key-registry"


def handler(event, context):
    physical = event.get("PhysicalResourceId", "ontology-key-registry-v1")
    if event["RequestType"] == "Delete":
        # Retain authority history with the retained workspace data.
        return {"PhysicalResourceId": physical}
    properties = event["ResourceProperties"]
    storage, kms = Storage(), boto3.client("kms")
    now = int(time.time())
    for identity, name, purpose in [
            ("cap-v1", "capabilityKeyArn", "execution-capability"),
            ("evidence-v1", "evidenceKeyArn", "runtime-evidence")]:
        arn = properties[name]
        public = kms.get_public_key(KeyId=arn)
        old = storage.get(OWNER, "ac_key", identity)
        if old and (old["keyArn"] != arn or old["state"] != "active"):
            raise ValueError("Key replacement or reactivation requires an explicit rotation procedure")
        record = {"id": identity, "keyArn": arn, "purpose": purpose, "state": "active", "algorithm": "RS256",
                  "notBefore": old["notBefore"] if old else now - 60,
                  "notAfter": max(old.get("notAfter", 0) if old else 0, now + 365 * 86400),
                  "publicKeyHash": hashlib.sha256(public["PublicKey"]).hexdigest()}
        storage.put(OWNER, "ac_key", record, old["version"] if old else None)
    old = storage.get(OWNER, "ac_key", "gateway-binding")
    record = {"id": "gateway-binding", **{key: properties[key] for key in
              ("gatewayId", "targetId", "workloadIdentityArn")}}
    storage.put(OWNER, "ac_key", record, old["version"] if old else None)
    return {"PhysicalResourceId": physical}
