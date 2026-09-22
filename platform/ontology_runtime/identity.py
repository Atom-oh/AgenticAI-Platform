"""Use the Identity broker's OAuth credential for the dedicated JWT Gateway."""
from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit

RESERVED = "_executionAuthorization"


def model_schema(wire):
    """Credentials never enter model tool schemas or model-created arguments."""
    if wire.get("name") not in {"ontology___context", "ontology___source"}:
        raise ValueError("Only authorized ontology read tools are model-visible")
    result = copy.deepcopy(wire)
    result["inputSchema"]["properties"].pop(RESERVED, None)
    result["inputSchema"]["required"] = [name for name in result["inputSchema"].get("required", []) if name != RESERVED]
    return result


class Gateway:
    def __init__(self, identity_client, http_client, *, endpoint, workload, provider, scopes, token_provider=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.hostname or not re.fullmatch(
                    r"[a-z0-9-]+\.gateway\.bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com", parsed.hostname)
                or parsed.path != "/mcp"):
            raise ValueError("A fixed dedicated AgentCore Gateway endpoint is required")
        self.identity, self.http, self.endpoint = identity_client, http_client, endpoint
        self.workload, self.provider, self.scopes = workload, provider, tuple(scopes)
        self.token_provider = token_provider

    def call(self, name, arguments, *, capability, operation_id):
        if RESERVED in arguments:
            raise ValueError("Model arguments cannot supply execution authorization")
        workload_token = (self.token_provider() if callable(self.token_provider) else
                          self.identity.get_workload_access_token(workloadName=self.workload)["workloadAccessToken"])
        if not isinstance(workload_token, str) or not workload_token:
            raise RuntimeError("AgentCore workload identity is unavailable")
        credential = self.identity.get_resource_oauth2_token(
            workloadIdentityToken=workload_token,
            resourceCredentialProviderName=self.provider, scopes=list(self.scopes), oauth2Flow="M2M")
        token = credential.get("accessToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("AgentCore Identity did not return a machine credential")
        request = {"jsonrpc": "2.0", "id": operation_id, "method": "tools/call", "params": {
            "name": name, "arguments": {**arguments, RESERVED: {"capability": capability, "operationId": operation_id}}}}
        raw = json.dumps(request, separators=(",", ":")).encode()
        if len(raw) > 600_000:
            raise ValueError("Gateway request exceeds the tool transport limit")
        try:
            with self.http.stream("POST", self.endpoint, content=raw, headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"}, follow_redirects=False, timeout=45) as response:
                if response.status_code != 200:
                    raise RuntimeError("Dedicated Gateway rejected the tool operation")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 524288:
                        raise RuntimeError("Gateway response exceeds the tool transport limit")
                # Gateway Lambda targets return JSON for the fixed non-streaming
                # protocol; any alternate response needs an explicitly verified adapter.
                result = json.loads(data)
        except Exception:
            raise RuntimeError("Dedicated Gateway operation did not complete") from None
        if (not isinstance(result, dict) or not isinstance(result.get("result"), dict)
                or result.get("id") != operation_id or result.get("error") or result["result"].get("isError")):
            raise RuntimeError("Dedicated Gateway did not return successful tool evidence")
        content = result["result"].get("content", [])
        try:
            if len(content) != 1 or content[0].get("type") != "text":
                raise ValueError()
            value = json.loads(content[0]["text"])
            if not isinstance(value, dict) or value.get("error"):
                raise ValueError()
            return value
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Dedicated Gateway tool returned invalid evidence") from None
