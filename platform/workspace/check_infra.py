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
    issues.extend(_intake_denylist_issues(resources))
    issues.extend(_intake_admin_issues(resources))
    issues.extend(_intake_partition_issues(resources))
    for identifier, resource in resources.items():
        if resource["Type"] == "AWS::CloudFront::Distribution":
            origins = json.dumps(resource.get("Properties", {}).get("DistributionConfig", {}).get("Origins", []))
            if any(bucket in origins for bucket in private_buckets):
                issues.append(f"{identifier}: private originals must not be a CloudFront origin")
    if len(browser_functions) != 1 or len(private_buckets) != 1:
        issues.append("Expected exactly one private workspace bucket and isolated browser function")
    return issues


def _flatten(value):
    """Render a CloudFormation string expression (Fn::Join/Ref) for exact comparison."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "Fn::Join" in value:
        separator, parts = value["Fn::Join"]
        return separator.join(_flatten(part) for part in parts)
    if isinstance(value, dict) and "Ref" in value:
        return "${" + value["Ref"] + "}"
    return json.dumps(value, sort_keys=True)


def _statements(resources, role_marker):
    for identifier, resource in resources.items():
        if resource["Type"] != "AWS::IAM::Policy" or role_marker not in identifier:
            continue
        for statement in resource.get("Properties", {}).get("PolicyDocument", {}).get("Statement", []):
            actions = statement.get("Action", [])
            yield identifier, statement, [actions] if isinstance(actions, str) else actions


def _intake_denylist_issues(resources):
    """The Worker reads exactly the configured deny-list parameter and nothing else (AV1)."""
    issues, names = [], []
    for identifier, resource in resources.items():
        if (identifier.startswith("DesignerWorkspaceWorker") and resource["Type"] == "AWS::Lambda::Function"):
            name = resource.get("Properties", {}).get("Environment", {}).get("Variables", {}).get("INTAKE_DENYLIST_PARAM")
            if name is not None:
                names.append(name)
    for identifier, statement, actions in _statements(resources, "DesignerWorkspaceWorkerRole"):
        if not any(action in ("ssm:GetParameter", "ssm:GetParameters", "ssm:*", "ssm:GetParametersByPath")
                   for action in actions):
            continue
        allowed = statement.get("Resource")
        allowed = allowed if isinstance(allowed, list) else [allowed]
        if actions != ["ssm:GetParameter"] or len(names) != 1 or len(allowed) != 1:
            issues.append(f"{identifier}: the Worker may read only the configured intake deny-list parameter")
            continue
        arn = _flatten(allowed[0])
        if "*" in arn or not arn.endswith(":parameter" + names[0]):
            issues.append(f"{identifier}: the Worker deny-list grant must name exactly {names[0]}")
    if names and not any("ssm:GetParameter" in actions
                         for _, _, actions in _statements(resources, "DesignerWorkspaceWorkerRole")):
        issues.append("DesignerWorkspaceWorker: INTAKE_DENYLIST_PARAM is set without its read grant")
    return issues


def _references(value, identifiers):
    text = json.dumps(value, sort_keys=True)
    return any(f'"{identifier}"' in text for identifier in identifiers)


INTAKE_PARTITION = "owner#" + __import__("hashlib").sha256(b"intake:deployment").hexdigest()


def _intake_admin_issues(resources):
    """IntakeAdminFn is IAM-invoke only (source-admission/1; plan I8)."""
    functions = [identifier for identifier, resource in resources.items()
                 if identifier.startswith("IntakeAdmin") and resource["Type"] == "AWS::Lambda::Function"]
    if not functions:
        return []
    issues = []
    for identifier, resource in resources.items():
        kind, props = resource["Type"], resource.get("Properties", {})
        if kind == "AWS::Lambda::Permission" and _references(props.get("FunctionName"), functions):
            principal = _flatten(props.get("Principal", ""))
            if "apigateway" in principal or principal in ("*", "") or props.get("FunctionUrlAuthType"):
                issues.append(f"{identifier}: IntakeAdminFn must not be invokable by API Gateway or a URL")
        if kind == "AWS::Lambda::Url" and _references(props.get("TargetFunctionArn"), functions):
            issues.append(f"{identifier}: IntakeAdminFn must not have a Function URL")
        if kind in ("AWS::ApiGatewayV2::Integration", "AWS::ApiGateway::Method") and _references(props, functions):
            issues.append(f"{identifier}: IntakeAdminFn must not have an API route")
        if kind == "AWS::IAM::Policy" and identifier.startswith("IntakeAdmin"):
            for statement in props.get("PolicyDocument", {}).get("Statement", []):
                actions = statement.get("Action", [])
                actions = [actions] if isinstance(actions, str) else actions
                if any(action.startswith("lambda:") or action in ("*", "iam:PassRole") for action in actions):
                    issues.append(f"{identifier}: IntakeAdminFn role may not invoke other functions")
                if any(action.startswith("dynamodb:") for action in actions):
                    keys = (statement.get("Condition", {}).get("ForAllValues:StringEquals", {})
                            .get("dynamodb:LeadingKeys"))
                    if keys != [INTAKE_PARTITION]:
                        issues.append(f"{identifier}: IntakeAdminFn table access must be limited to intake:deployment")
    return issues


INTAKE_WRITE_ACTIONS = ("dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                        "dynamodb:BatchWriteItem", "dynamodb:PartiQLInsert", "dynamodb:PartiQLUpdate",
                        "dynamodb:PartiQLDelete")


def _intake_configured(resources):
    """Intake is configured once its admin function or any intake environment is synthesized."""
    for identifier, resource in resources.items():
        if resource["Type"] != "AWS::Lambda::Function":
            continue
        if identifier.startswith("IntakeAdmin"):
            return True
        variables = resource.get("Properties", {}).get("Environment", {}).get("Variables", {})
        if any(name in variables for name in ("INTAKE_DEPLOYMENT", "INTAKE_DENYLIST_PARAM", "INTAKE_DENYLIST_KEY")):
            return True
    return False


def _writes(actions):
    return [action for action in actions
            if action in ("*", "dynamodb:*") or action in INTAKE_WRITE_ACTIONS
            or (action.startswith("dynamodb:") and "*" in action)]


def _names_resource(value):
    """True when a CloudFormation expression names a template resource (not a pseudo parameter)."""
    if isinstance(value, dict):
        if "Fn::GetAtt" in value:
            return True
        if "Ref" in value and not str(value["Ref"]).startswith("AWS::"):
            return True
        return any(_names_resource(item) for item in value.values())
    if isinstance(value, list):
        return any(_names_resource(item) for item in value)
    return False


def _covers_records(resource, records):
    """The statement reaches the workspace table: by reference, or by a wildcard ARN that
    names no other resource (another table's `/index/*` does not cover it)."""
    values = resource if isinstance(resource, list) else [resource]
    return any(_references(value, records) or (not _names_resource(value) and "*" in _flatten(value))
               for value in values)


def _denies_intake_writes(statements, records):
    for statement in statements:
        actions = statement.get("Action", [])
        actions = [actions] if isinstance(actions, str) else actions
        keys = statement.get("Condition", {}).get("ForAnyValue:StringEquals", {}).get("dynamodb:LeadingKeys")
        if (statement.get("Effect") == "Deny" and keys == [INTAKE_PARTITION]
                and (set(INTAKE_WRITE_ACTIONS) <= set(actions) or "dynamodb:*" in actions)
                and _covers_records(statement.get("Resource"), records)):
            return True
    return False


def _intake_partition_issues(resources):
    """Only IntakeAdminFn may write `intake:deployment` (source-admission/1, SRC-06).

    Policies, provenance registrations and reviewer grants share the workspace
    table with project data, so every other identity that can write the table
    must carry an explicit Deny on that partition whenever intake is configured.
    """
    if not _intake_configured(resources):
        return []
    records = [identifier for identifier, resource in resources.items()
               if identifier.startswith("DesignerWorkspaceRecords") and resource["Type"] == "AWS::DynamoDB::Table"]
    records = records or ["DesignerWorkspaceRecords"]
    issues = []
    for identifier, resource in resources.items():
        if resource["Type"] != "AWS::IAM::Policy" or identifier.startswith("IntakeAdmin"):
            continue
        statements = resource.get("Properties", {}).get("PolicyDocument", {}).get("Statement", [])
        writers = False
        for statement in statements:
            actions = statement.get("Action", [])
            actions = [actions] if isinstance(actions, str) else actions
            if statement.get("Effect") != "Allow" or not _writes(actions):
                continue
            if not _covers_records(statement.get("Resource"), records):
                continue
            keys = statement.get("Condition", {}).get("ForAllValues:StringEquals", {}).get("dynamodb:LeadingKeys")
            if isinstance(keys, list) and keys and INTAKE_PARTITION not in keys:
                continue  # already limited to other partitions
            writers = True
        if writers and not _denies_intake_writes(statements, records):
            issues.append(f"{identifier}: a workload that can write the workspace table must be denied "
                          "writes to the intake:deployment administrative partition")
    return issues


def main():
    template = json.loads(Path(sys.argv[1]).read_text())
    issues = audit_template(template)
    print(json.dumps({"passed": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
