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


PARAM = "/bank-platform/public-denylist"
PARAM_ARN = {"Fn::Join": ["", ["arn:aws:ssm:", {"Ref": "AWS::Region"}, ":", {"Ref": "AWS::AccountId"},
                               ":parameter/bank-platform/public-denylist"]]}


def publisher_template():
    source = template()
    for fn in ("WsFn", "StudioLoopFn"):
        source["Resources"][f"{fn}ABCD1234"] = {"Type": "AWS::Lambda::Function", "Properties": {
            "Role": {"Fn::GetAtt": [f"{fn}ServiceRoleEF01", "Arn"]},
            "Environment": {"Variables": {"PUBLIC_DENYLIST_PARAM": PARAM}}}}
        source["Resources"][f"{fn}ServiceRoleDefaultPolicy99"] = {"Type": "AWS::IAM::Policy", "Properties": {
            "Roles": [{"Ref": f"{fn}ServiceRoleEF01"}],
            "PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": PARAM_ARN}]}}}
    return source


def test_legacy_public_writers_read_exactly_the_private_deny_list():
    # Both legacy publishers (engine plan E2 3a, review round 21, AO2) receive the parameter name and
    # ssm:GetParameter on exactly that parameter ARN; the release gate fails otherwise.
    good = publisher_template()
    assert audit_template(good, require_publishers=True) == []
    assert any("publisher" in item for item in audit_template(template(), require_publishers=True))
    no_env = copy.deepcopy(good)
    no_env["Resources"]["WsFnABCD1234"]["Properties"]["Environment"]["Variables"].pop("PUBLIC_DENYLIST_PARAM")
    assert any("WsFn" in item and "PUBLIC_DENYLIST_PARAM" in item for item in audit_template(no_env, require_publishers=True))
    no_grant = copy.deepcopy(good)
    no_grant["Resources"]["StudioLoopFnServiceRoleDefaultPolicy99"]["Properties"]["PolicyDocument"]["Statement"] = []
    assert any("StudioLoopFn" in item and "ssm:GetParameter" in item for item in audit_template(no_grant, require_publishers=True))
    wide = copy.deepcopy(good)
    wide["Resources"]["WsFnServiceRoleDefaultPolicy99"]["Properties"]["PolicyDocument"]["Statement"][0]["Resource"] = \
        {"Fn::Join": ["", ["arn:aws:ssm:", {"Ref": "AWS::Region"}, ":", {"Ref": "AWS::AccountId"}, ":parameter/bank-platform/*"]]}
    assert any("WsFn" in item and "ssm:GetParameter" in item for item in audit_template(wide, require_publishers=True))
