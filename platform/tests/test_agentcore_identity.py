"""The machine credential comes from the exact admitted Gateway workload."""
import json
from types import SimpleNamespace

import pytest

from ontology_runtime.identity import Gateway


class Identity:
    def __init__(self):
        self.names = []

    def get_workload_access_token(self, *, workloadName):
        self.names.append(workloadName)
        return {"workloadAccessToken": "synthetic-workload-token"}

    def get_resource_oauth2_token(self, **arguments):
        assert arguments == {
            "workloadIdentityToken": "synthetic-workload-token",
            "resourceCredentialProviderName": "dedicated-provider",
            "scopes": ["ontology/tools"], "oauth2Flow": "M2M"}
        return {"accessToken": "synthetic-machine-credential"}


class Response:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self):
        yield json.dumps({"id": "stage-context", "result": {
            "content": [{"type": "text", "text": '{"stage":"context"}'}]}}).encode()


def gateway(identity, http, **options):
    return Gateway(identity, http, endpoint="https://ontology-example.gateway.bedrock-agentcore.ap-northeast-2.amazonaws.com/mcp",
                   workload="dedicated_gateway_m2m", provider="dedicated-provider", scopes=["ontology/tools"], **options)


def test_machine_token_uses_the_registered_name_and_is_not_in_tool_arguments():
    identity = Identity()

    def stream(method, endpoint, **arguments):
        request = json.loads(arguments["content"])
        assert arguments["headers"]["Authorization"] == "Bearer synthetic-machine-credential"
        assert "synthetic-machine-credential" not in arguments["content"].decode()
        assert request["params"]["arguments"]["_executionAuthorization"] == {
            "capability": "synthetic-signed-capability", "operationId": "stage-context"}
        return Response()

    result = gateway(identity, SimpleNamespace(stream=stream)).call(
        "ontology___stage", {"stage": "context"}, capability="synthetic-signed-capability", operation_id="stage-context")
    assert identity.names == ["dedicated_gateway_m2m"]
    assert result == {"stage": "context"}


def test_missing_explicitly_selected_injected_token_never_uses_another_identity():
    identity = Identity()
    with pytest.raises(RuntimeError, match="workload identity is unavailable"):
        gateway(identity, None, token_provider=lambda: None).call(
            "ontology___stage", {}, capability="synthetic", operation_id="stage-context")
    assert identity.names == []
