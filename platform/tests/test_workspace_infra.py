import copy

from workspace.check_infra import audit_template


def template():
    return {"Resources": {
        "DesignerWorkspacePrivateFiles": {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "Properties": {
            "PublicAccessBlockConfiguration": {name: True for name in
                ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")},
            "VersioningConfiguration": {"Status": "Enabled"}}},
        "DesignerWorkspaceBrowser": {"Type": "AWS::Lambda::Function", "Properties": {
            "VpcConfig": {"SubnetIds": ["private-subnet"], "SecurityGroupIds": ["closed-group"]}}},
        "DesignerWorkspaceRoute": {"Type": "AWS::ApiGatewayV2::Route", "Properties": {
            "AuthorizationType": "JWT", "AuthorizationScopes": ["access"]}},
    }}


def test_public_originals_and_missing_auth_are_release_blockers():
    good = template()
    assert audit_template(good) == []
    public = copy.deepcopy(good)
    public["Resources"]["Distribution"] = {"Type": "AWS::CloudFront::Distribution", "Properties": {
        "DistributionConfig": {"Origins": [{"DomainName": {"Fn::GetAtt": ["DesignerWorkspacePrivateFiles", "RegionalDomainName"]}}]}}}
    assert any("CloudFront origin" in item for item in audit_template(public))
    anonymous = copy.deepcopy(good)
    anonymous["Resources"]["DesignerWorkspaceRoute"]["Properties"]["AuthorizationType"] = "NONE"
    assert any("JWT" in item for item in audit_template(anonymous))


def test_tls_denial_is_not_mistaken_for_public_grant():
    source = template()
    source["Resources"]["DesignerWorkspaceBucketPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {
        "PolicyDocument": {"Statement": [{"Effect": "Deny", "Principal": "*", "Action": "s3:*",
                                         "Resource": "bucket/*", "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}}}
    assert audit_template(source) == []
    source["Resources"]["DesignerWorkspaceBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]["Effect"] = "Allow"
    assert any("wildcard principal" in item for item in audit_template(source))


def _worker(template, parameter="/bank/intake/denylist", resource=None, actions=("ssm:GetParameter",)):
    template["Resources"]["DesignerWorkspaceWorkerABC"] = {"Type": "AWS::Lambda::Function", "Properties": {
        "Environment": {"Variables": {"INTAKE_DENYLIST_PARAM": parameter, "INTAKE_DEPLOYMENT": "BankPlatform"}}}}
    arn = resource if resource is not None else {"Fn::Join": ["", [
        "arn:", {"Ref": "AWS::Partition"}, ":ssm:ap-northeast-2:", {"Ref": "AWS::AccountId"}, ":parameter", parameter]]}
    template["Resources"]["DesignerWorkspaceWorkerRoleDefaultPolicyABC"] = {"Type": "AWS::IAM::Policy", "Properties": {
        "PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": list(actions) if len(actions) > 1 else actions[0],
                                          "Resource": arn}]}}}
    return template


def test_worker_reads_exactly_the_configured_intake_deny_list_parameter():
    assert audit_template(_worker(template())) == []
    wildcard = _worker(template(), resource="arn:aws:ssm:ap-northeast-2:000000000000:parameter/bank/*")
    assert any("deny-list" in item for item in audit_template(wildcard))
    other = _worker(template(), resource="arn:aws:ssm:ap-northeast-2:000000000000:parameter/bank/other")
    assert any("deny-list" in item for item in audit_template(other))
    broad = _worker(template(), actions=("ssm:GetParameter", "ssm:GetParametersByPath"))
    assert any("deny-list" in item for item in audit_template(broad))
    missing = _worker(template())
    del missing["Resources"]["DesignerWorkspaceWorkerRoleDefaultPolicyABC"]
    assert any("without its read grant" in item for item in audit_template(missing))


INTAKE_KEYS = ["owner#" + __import__("hashlib").sha256(b"intake:deployment").hexdigest()]


def _intake_admin(template):
    resources = template["Resources"]
    resources["IntakeAdminFnABC"] = {"Type": "AWS::Lambda::Function", "Properties": {
        "Handler": "intake.admin_handler.handler", "Role": {"Fn::GetAtt": ["IntakeAdminRoleABC", "Arn"]},
        "Environment": {"Variables": {"INTAKE_DEPLOYMENT": "BankPlatform"}}}}
    resources["IntakeAdminRoleDefaultPolicyABC"] = {"Type": "AWS::IAM::Policy", "Properties": {"PolicyDocument": {
        "Statement": [{"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem"],
                       "Resource": {"Fn::GetAtt": ["DesignerWorkspaceRecords", "Arn"]},
                       "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": INTAKE_KEYS}}}]}}}
    return template


def test_intake_admin_function_is_iam_invoke_only():
    good = _intake_admin(template())
    assert audit_template(good) == []
    api = _intake_admin(template())
    api["Resources"]["IntakeAdminApiPermission"] = {"Type": "AWS::Lambda::Permission", "Properties": {
        "Action": "lambda:InvokeFunction", "FunctionName": {"Fn::GetAtt": ["IntakeAdminFnABC", "Arn"]},
        "Principal": "apigateway.amazonaws.com"}}
    assert any("API Gateway" in item for item in audit_template(api))
    url = _intake_admin(template())
    url["Resources"]["IntakeAdminUrl"] = {"Type": "AWS::Lambda::Url", "Properties": {
        "AuthType": "AWS_IAM", "TargetFunctionArn": {"Fn::GetAtt": ["IntakeAdminFnABC", "Arn"]}}}
    assert any("Function URL" in item for item in audit_template(url))
    route = _intake_admin(template())
    route["Resources"]["DesignerWorkspaceIntakeIntegration"] = {"Type": "AWS::ApiGatewayV2::Integration",
        "Properties": {"IntegrationUri": {"Fn::GetAtt": ["IntakeAdminFnABC", "Arn"]}}}
    assert any("API route" in item for item in audit_template(route))
    invoke = _intake_admin(template())
    invoke["Resources"]["IntakeAdminRoleDefaultPolicyABC"]["Properties"]["PolicyDocument"]["Statement"].append(
        {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": "arn:aws:lambda:x:1:function:Other"})
    assert any("invoke other functions" in item for item in audit_template(invoke))
    broad = _intake_admin(template())
    broad["Resources"]["IntakeAdminRoleDefaultPolicyABC"]["Properties"]["PolicyDocument"]["Statement"][0].pop("Condition")
    assert any("intake:deployment" in item for item in audit_template(broad))


def test_synthesized_template_with_the_intake_flag_passes(tmp_path):
    """Opt-in offline synth (the CI infra job runs the same command)."""
    import json
    import os
    from pathlib import Path
    import pytest
    template = os.environ.get("INTAKE_SYNTH_TEMPLATE")
    if not template:
        pytest.skip("Set INTAKE_SYNTH_TEMPLATE to a template synthesized with -c intakeAdmin=true")
    data = json.loads(Path(template).read_text())
    resources = data["Resources"]
    functions = [k for k, v in resources.items() if k.startswith("IntakeAdmin") and v["Type"] == "AWS::Lambda::Function"]
    assert len(functions) == 1
    assert resources[functions[0]]["Properties"]["Handler"] == "intake.admin_handler.handler"
    assert resources[functions[0]]["Properties"]["Runtime"] == "python3.12"
    assert audit_template(data) == []
    # Review 2, finding 3: each function carries the same deployment scope.
    scopes = {k: v["Properties"].get("Environment", {}).get("Variables", {}).get("INTAKE_DEPLOYMENT")
              for k, v in resources.items() if v["Type"] == "AWS::Lambda::Function"
              and k.startswith(("DesignerWorkspaceApi", "DesignerWorkspaceWorker", "IntakeAdmin"))}
    assert len(scopes) == 3 and len(set(scopes.values())) == 1 and None not in scopes.values(), scopes
    # Finding 1 (PR #28 review): the API and Worker roles carry the partition Deny,
    # and removing it from either is a release blocker.
    for marker in ("DesignerWorkspaceApiRoleDefaultPolicy", "DesignerWorkspaceWorkerRoleDefaultPolicy"):
        [policy] = [k for k, v in resources.items() if k.startswith(marker) and v["Type"] == "AWS::IAM::Policy"]
        statements = resources[policy]["Properties"]["PolicyDocument"]["Statement"]
        denies = [st for st in statements if st["Effect"] == "Deny"
                  and st.get("Condition", {}).get("ForAnyValue:StringEquals", {}).get("dynamodb:LeadingKeys") == INTAKE_KEYS]
        assert len(denies) == 1 and set(INTAKE_WRITES) <= set(denies[0]["Action"])
        stripped = json.loads(json.dumps(data))
        stripped["Resources"][policy]["Properties"]["PolicyDocument"]["Statement"] = [
            st for st in statements if st["Effect"] != "Deny"]
        assert any(policy in item for item in audit_template(stripped))


# PR #28 review round 1: administrative partition write isolation ---------------

INTAKE_WRITES = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
                 "dynamodb:PartiQLInsert", "dynamodb:PartiQLUpdate", "dynamodb:PartiQLDelete"]


def _workload(template, name, *, deny=True, deny_actions=None, allow_resource=None):
    records = {"Fn::GetAtt": ["DesignerWorkspaceRecords", "Arn"]}
    statements = [{"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"],
                   "Resource": allow_resource or records}]
    if deny:
        statements.append({"Effect": "Deny", "Action": deny_actions or INTAKE_WRITES, "Resource": records,
                           "Condition": {"ForAnyValue:StringEquals": {"dynamodb:LeadingKeys": INTAKE_KEYS}}})
    template["Resources"][name] = {"Type": "AWS::IAM::Policy", "Properties": {
        "PolicyDocument": {"Statement": statements}}}
    return template


def test_workload_roles_cannot_write_the_intake_administrative_partition():
    """Finding 1: with intake configured, every non-admin table writer denies intake:deployment writes."""
    names = ("DesignerWorkspaceApiRoleDefaultPolicyABC", "DesignerWorkspaceWorkerRoleDefaultPolicyABC")
    good = _intake_admin(template())
    for name in names:
        _workload(good, name)
    assert audit_template(good) == []
    for name in names:
        missing = _intake_admin(template())
        for other in names:
            _workload(missing, other, deny=other != name)
        assert any(name in item and "intake:deployment" in item for item in audit_template(missing))
    partial = _intake_admin(template())
    _workload(partial, names[0], deny_actions=["dynamodb:PutItem"])
    assert any("intake:deployment" in item for item in audit_template(partial))
    wildcard = _intake_admin(template())
    wildcard["Resources"]["OtherRoleDefaultPolicyABC"] = {"Type": "AWS::IAM::Policy", "Properties": {
        "PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "dynamodb:*", "Resource": "*"}]}}}
    assert any("OtherRoleDefaultPolicyABC" in item and "intake:deployment" in item
               for item in audit_template(wildcard))


def test_intake_partition_rule_applies_whenever_the_worker_is_intake_configured():
    configured = _worker(template())
    configured["Resources"]["DesignerWorkspaceWorkerABC"]["Properties"]["Environment"]["Variables"][
        "INTAKE_DEPLOYMENT"] = "BankPlatform"
    _workload(configured, "DesignerWorkspaceApiRoleDefaultPolicyABC", deny=False)
    assert any("intake:deployment" in item for item in audit_template(configured))
    unconfigured = template()
    _workload(unconfigured, "DesignerWorkspaceApiRoleDefaultPolicyABC", deny=False)
    assert audit_template(unconfigured) == []


# PR #28 review round 2: every intake function receives the same deployment scope ----

def _functions(template, api="BankPlatform", worker="BankPlatform", admin="BankPlatform"):
    resources = template["Resources"]
    for name, value in (("DesignerWorkspaceApiABC", api), ("DesignerWorkspaceWorkerABC", worker)):
        variables = {} if value is None else {"INTAKE_DEPLOYMENT": value}
        resources[name] = {"Type": "AWS::Lambda::Function", "Properties": {"Environment": {"Variables": variables}}}
    _intake_admin(template)
    if admin is None:
        del resources["IntakeAdminFnABC"]["Properties"]["Environment"]
    else:
        resources["IntakeAdminFnABC"]["Properties"]["Environment"]["Variables"]["INTAKE_DEPLOYMENT"] = admin
    return template


def test_every_intake_function_receives_the_same_deployment_scope():
    """Review 2, finding 3: API, Worker and IntakeAdminFn are checked independently."""
    assert audit_template(_functions(template())) == []
    for field in ("api", "worker", "admin"):
        missing = audit_template(_functions(template(), **{field: None}))
        assert any("INTAKE_DEPLOYMENT" in item for item in missing), field
        other = audit_template(_functions(template(), **{field: "Other"}))
        assert any("INTAKE_DEPLOYMENT" in item for item in other), field
