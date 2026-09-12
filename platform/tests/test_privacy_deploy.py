"""Deployment cannot accidentally publish a mutable image or another namespace."""
import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).parents[1] / "privacy" / "deploy" / "render.py"
spec = importlib.util.spec_from_file_location("privacy_render", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REPOSITORY = "180294183052.dkr.ecr.ap-northeast-2.amazonaws.com/bank-platform/mydata-privacy-gateway"
OUTPUTS = {
    "PrivacyGatewayRepositoryUri": REPOSITORY,
    "PrivacyTargetGroupArn": "arn:aws:elasticloadbalancing:ap-northeast-2:180294183052:targetgroup/privacy/0123456789abcdef",
    "PrivacyVpcId": "vpc-04e77172c67f19814",
    "PrivacyVpcCidr": "10.0.0.0/16",
}
IMAGE = REPOSITORY + "@sha256:" + "a" * 64
NLB_ADDRESSES = ["10.0.2.10", "10.0.3.10"]


def test_only_target_namespace_and_private_service():
    manifest = module.render(OUTPUTS, IMAGE, nlb_addresses=NLB_ADDRESSES)
    items = manifest["items"]
    for item in items:
        assert item["metadata"].get("namespace", item["metadata"]["name"]) == "bank-platform-mydata"
    service = next(i for i in items if i["kind"] == "Service")
    assert service["spec"]["type"] == "ClusterIP"
    target = next(i for i in items if i["kind"] == "TargetGroupBinding")
    assert target["spec"]["targetGroupARN"] == OUTPUTS["PrivacyTargetGroupArn"]
    assert "networking" not in target["spec"]  # SG rule ownership stays with CDK.
    pod = next(i for i in items if i["kind"] == "Deployment")["spec"]["template"]["spec"]
    # Verified in fsi-demo-cluster: ARM CPU nodes are graviton, not system.
    assert pod["nodeSelector"] == {"node-type": "graviton", "kubernetes.io/arch": "arm64"}
    assert not pod["automountServiceAccountToken"]
    container = pod["containers"][0]
    assert container["image"] == IMAGE
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert "nvidia.com/gpu" not in container["resources"]["limits"]
    policy = next(i for i in items if i["kind"] == "NetworkPolicy")
    assert policy["spec"]["ingress"] == [{
        "from": [{"ipBlock": {"cidr": "10.0.2.10/32"}}, {"ipBlock": {"cidr": "10.0.3.10/32"}}],
        "ports": [{"port": 8080, "protocol": "TCP"}],
    }]


@pytest.mark.parametrize("image", [
    REPOSITORY + ":latest", IMAGE.replace("180294183052", "111111111111"),
    IMAGE.replace("mydata-privacy-gateway", "other"), IMAGE + "\n",
])
def test_rejects_unpinned_or_wrong_repository(image):
    with pytest.raises(ValueError):
        module.render(OUTPUTS, image, nlb_addresses=NLB_ADDRESSES)


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "10.0.0.0/8", "10.0.0.1/16", "10.0.0.0/32"])
def test_rejects_unscoped_network(cidr):
    with pytest.raises(ValueError):
        module.render({**OUTPUTS, "PrivacyVpcCidr": cidr}, IMAGE, nlb_addresses=NLB_ADDRESSES)


@pytest.mark.parametrize("revision", ["", "v1/../model", "qwen\n", "x" * 161])
def test_rendered_revision_is_accepted_by_relay_identifier_contract(revision):
    with pytest.raises(ValueError):
        module.render(OUTPUTS, IMAGE, revision, nlb_addresses=NLB_ADDRESSES)


@pytest.mark.parametrize("addresses", [
    [], ["10.0.2.10"], ["10.0.2.10/24", "10.0.3.10"],
    ["192.0.2.10", "10.0.3.10"], ["10.77.2.10", "10.0.3.10"], ["::1", "10.0.3.10"],
    ["10.0.2.10", "10.0.2.10"], ["10.0.0.0"], ["10.0.255.255"],
])
def test_requires_distinct_nlb_ips_inside_the_existing_vpc(addresses):
    with pytest.raises(ValueError):
        module.render(OUTPUTS, IMAGE, nlb_addresses=addresses)
