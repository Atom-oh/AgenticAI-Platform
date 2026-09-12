#!/usr/bin/env python3
"""Render only the new namespace resources; does not contact AWS or Kubernetes."""
import argparse
import ipaddress
import json
import re
from pathlib import Path
from string import Template


def render(outputs, image, model_revision="unverified", *, nlb_addresses=()):
    values = outputs.get("BankPlatformPrivacy", outputs)
    image_match = re.fullmatch(
        r"(\d{12})\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/"
        r"bank-platform/mydata-privacy-gateway@sha256:[0-9a-f]{64}", image)
    tg = values.get("PrivacyTargetGroupArn", "")
    tg_match = re.fullmatch(
        r"arn:aws:elasticloadbalancing:ap-northeast-2:(\d{12}):targetgroup/[A-Za-z0-9-]+/[0-9a-f]+", tg)
    if not image_match or not tg_match or image_match[1] != tg_match[1]:
        raise ValueError("Need same-account Seoul ECR digest and CDK target-group ARN")
    if values.get("PrivacyGatewayRepositoryUri") != image.split("@")[0]:
        raise ValueError("Image must belong to the CDK-created gateway repository")
    vpc_id = values.get("PrivacyVpcId", "")
    if not re.fullmatch(r"vpc-[0-9a-f]{8,17}", vpc_id):
        raise ValueError("Invalid VPC ID")
    network = ipaddress.ip_network(values.get("PrivacyVpcCidr", ""), strict=True)
    if (network.version != 4 or not network.subnet_of(ipaddress.ip_network("10.0.0.0/8"))
            or not 16 <= network.prefixlen <= 28):
        raise ValueError("Need a scoped private FSI VPC CIDR")
    # With preserve_client_ip disabled, the gateway sees the NLB's node IPs.
    # Require every NLB ENI address, rather than trusting a VPC-wide ingress rule
    # or DNS that can omit an unhealthy availability zone. No network lookup here.
    if not isinstance(nlb_addresses, (list, tuple)) or not 2 <= len(nlb_addresses) <= 4:
        raise ValueError("Need verified NLB private IPv4 addresses")
    addresses = [ipaddress.ip_address(value) for value in nlb_addresses]
    if (len(set(addresses)) != len(addresses)
            or any(a.version != 4 or a not in network
                   or a in (network.network_address, network.broadcast_address) for a in addresses)):
        raise ValueError("NLB addresses must be distinct hosts inside the FSI VPC")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,159}", model_revision)
            or ".." in model_revision):
        raise ValueError("Invalid model revision")
    template = Template(Path(__file__).with_name("gateway.manifest.json").read_text())
    manifest = json.loads(template.substitute(
        IMAGE=image, TARGET_GROUP_ARN=tg, VPC_ID=vpc_id,
        NLB_ADDRESS=str(addresses[0]), MODEL_REVISION=model_revision))
    policy = next(item for item in manifest["items"] if item["kind"] == "NetworkPolicy")
    policy["spec"]["ingress"][0]["from"] = [
        {"ipBlock": {"cidr": f"{address}/32"}} for address in sorted(addresses)
    ]
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", required=True, type=Path, help="CDK --outputs-file JSON")
    parser.add_argument("--image", required=True, help="Gateway ECR image pinned by sha256 digest")
    parser.add_argument("--model-revision", default="unverified",
                        help="Actual Qwen artifact revision, not the gateway image digest")
    parser.add_argument("--nlb-addresses", required=True, nargs="+",
                        help="All NLB private IPv4 addresses, verified from its EC2 ENIs")
    args = parser.parse_args()
    try:
        manifest = render(json.loads(args.outputs.read_text()), args.image, args.model_revision,
                          nlb_addresses=args.nlb_addresses)
    except (ValueError, KeyError, TypeError, OSError):
        parser.exit(2, "Invalid private gateway deployment configuration.\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
