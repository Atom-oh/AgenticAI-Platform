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
