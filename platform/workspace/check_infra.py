"""Offline release gate for the workspace's synthesized cloud boundaries."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def audit_template(template: dict) -> list[str]:
    resources = template.get("Resources", {})
    issues = []
    private_buckets = []
    browser_functions = []
    for identifier, resource in resources.items():
        if not identifier.startswith("DesignerWorkspace"):
            continue
        kind, props = resource["Type"], resource.get("Properties", {})
        if kind == "AWS::S3::Bucket":
            private_buckets.append(identifier)
            block = props.get("PublicAccessBlockConfiguration", {})
            if not all(block.get(key) is True for key in
                       ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")):
                issues.append(f"{identifier}: public access must be blocked")
            if resource.get("DeletionPolicy") != "Retain" or props.get("VersioningConfiguration", {}).get("Status") != "Enabled":
                issues.append(f"{identifier}: originals require retained versioned storage")
        if kind == "AWS::DynamoDB::Table":
            if props.get("SSESpecification", {}).get("SSEEnabled") is not True:
                issues.append(f"{identifier}: KMS-backed encryption is required")
            if props.get("TimeToLiveSpecification", {}).get("AttributeName") != "ttl":
                issues.append(f"{identifier}: operational job expiry is missing")
        if kind == "AWS::EC2::SecurityGroup":
            if props.get("SecurityGroupIngress"):
                issues.append(f"{identifier}: browser security group must have no ingress")
            egress = props.get("SecurityGroupEgress", [])
            if not egress or any(rule.get("CidrIp") != "255.255.255.255/32" or rule.get("IpProtocol") != "icmp"
                                 for rule in egress):
                issues.append(f"{identifier}: browser egress is not fully closed")
        if kind in ("AWS::EC2::NatGateway", "AWS::EC2::InternetGateway"):
            issues.append(f"{identifier}: renderer must have no internet route")
        if kind == "AWS::EC2::Route" and props.get("DestinationCidrBlock") == "0.0.0.0/0":
            issues.append(f"{identifier}: renderer default route is forbidden")
        if kind == "AWS::Lambda::Url" and props.get("AuthType") == "NONE":
            issues.append(f"{identifier}: unauthenticated function URL")
        if kind == "AWS::ApiGatewayV2::Route" and (props.get("AuthorizationType") != "JWT" or not props.get("AuthorizationScopes")):
            issues.append(f"{identifier}: JWT access-token authorization required")
        if kind == "AWS::Lambda::Function" and "Browser" in identifier:
            browser_functions.append(identifier)
            if not props.get("VpcConfig", {}).get("SubnetIds") or not props.get("VpcConfig", {}).get("SecurityGroupIds"):
                issues.append(f"{identifier}: browser must be in isolated subnets")
            if any(name in props.get("Environment", {}).get("Variables", {}) for name in
                   ("WORKSPACE_BUCKET", "WORKSPACE_TABLE", "CACHE_TABLE", "GUARDRAIL_ID")):
                issues.append(f"{identifier}: browser must not receive application service configuration")
        if kind in ("AWS::IAM::Policy", "AWS::S3::BucketPolicy"):
            policy = props.get("PolicyDocument", {})
            for statement in policy.get("Statement", []):
                # A conditional TLS DENY to everyone restricts access; it is
                # not a public grant. Inspect Allow semantics, not string matches.
                if statement.get("Effect") != "Allow":
                    continue
                principal = statement.get("Principal", {})
                if principal == "*" or (isinstance(principal, dict) and "*" in principal.values()):
                    issues.append(f"{identifier}: wildcard principal grant")
                allowed = statement.get("Resource")
                if (allowed == "*" or (isinstance(allowed, list) and "*" in allowed)) and not statement.get("Condition"):
                    issues.append(f"{identifier}: unconstrained wildcard resource grant")
                if "BrowserRole" in identifier:
                    actions = statement.get("Action", [])
                    if isinstance(actions, str):
                        actions = [actions]
                    if any(not action.startswith(("logs:", "ec2:")) for action in actions):
                        issues.append(f"{identifier}: browser role can access application services")
    for identifier, resource in resources.items():
        if resource["Type"] == "AWS::CloudFront::Distribution":
            origins = json.dumps(resource.get("Properties", {}).get("DistributionConfig", {}).get("Origins", []))
            if any(bucket in origins for bucket in private_buckets):
                issues.append(f"{identifier}: private originals must not be a CloudFront origin")
    if len(browser_functions) != 1 or len(private_buckets) != 1:
        issues.append("Expected exactly one private workspace bucket and isolated browser function")
    return issues


def main():
    template = json.loads(Path(sys.argv[1]).read_text())
    issues = audit_template(template)
    print(json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
