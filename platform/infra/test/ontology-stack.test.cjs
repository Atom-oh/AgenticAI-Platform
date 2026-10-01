require('ts-node/register');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const cdk = require('aws-cdk-lib');
const { Template } = require('aws-cdk-lib/assertions');
const { OntologyStack } = require('../lib/ontology-stack');

test('ontology service roles separate source authority, sandbox tools and capability signing', () => {
  const directory = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'ontology-iac-'));
  try {
    fs.writeFileSync(path.join(directory, 'Dockerfile'), 'FROM scratch\n');
    const stack = new OntologyStack(new cdk.App(), 'OntologyTest', {
      env: { account: '111122223333', region: 'ap-northeast-2' },
      runtimeDirectory: directory, isolatedSubnets: ['subnet-00000000000000001'],
      isolatedSecurityGroups: ['sg-00000000000000001'], resourcePrefix: 'ontology_security_test',
      toolArchive: { bucket: 'synthetic-tools', key: `tools/${'a'.repeat(64)}.tar.gz`, version: 'synthetic-v1',
        archiveHash: 'a'.repeat(64), analyzerCodeHash: 'b'.repeat(64), dependencyLockHash: 'c'.repeat(64) },
    });
    const resources = Template.fromStack(stack).toJSON().Resources;
    const byType = type => Object.values(resources).filter(resource => resource.Type === type);
    const policy = prefix => Object.entries(resources).filter(([key, resource]) =>
      key.startsWith(prefix) && resource.Type === 'AWS::IAM::Policy').flatMap(([, resource]) => resource.Properties.PolicyDocument.Statement);
    const actions = statements => statements.flatMap(statement => [].concat(statement.Action));
    assert.equal(byType('AWS::BedrockAgentCore::Gateway')[0].Properties.AuthorizerType, 'CUSTOM_JWT');
    assert.deepEqual(byType('AWS::BedrockAgentCore::Gateway')[0].Properties.AuthorizerConfiguration.CustomJWTAuthorizer.AllowedScopes, ['ontology/tools']);
    assert.equal(byType('AWS::Cognito::UserPool')[0].Properties.AdminCreateUserConfig.AllowAdminCreateUserOnly, true);
    assert.equal(byType('AWS::BedrockAgentCore::Memory')[0].Properties.MemoryStrategies, undefined);
    assert.equal(byType('AWS::BedrockAgentCore::BrowserCustom')[0].Properties.NetworkConfiguration.NetworkMode, 'VPC');
    assert.equal(byType('AWS::BedrockAgentCore::CodeInterpreterCustom')[0].Properties.NetworkConfiguration.NetworkMode, 'SANDBOX');
    const runtime = actions(policy('RuntimeRole'));
    assert(runtime.includes('bedrock-agentcore:GetResourceOauth2Token'));
    assert(runtime.includes('bedrock-agentcore:InvokeCodeInterpreter'));
    assert(!runtime.some(action => /^(s3:|dynamodb:|lambda:)/.test(action)));
    const secrets = policy('RuntimeRole').filter(statement =>
      [].concat(statement.Action).some(action => action.startsWith('secretsmanager:')));
    assert.equal(secrets.length, 1);
    assert.deepEqual([].concat(secrets[0].Action), ['secretsmanager:GetSecretValue']);
    assert(JSON.stringify(secrets[0].Resource).includes('IdentitySecretMetadata'));
    assert(JSON.stringify(secrets[0].Resource).includes('clientSecretArn.secretArn'));
    assert(![].concat(secrets[0].Resource).includes('*'));
    const identity = policy('RuntimeRole');
    const workloadToken = identity.find(statement => [].concat(statement.Action).includes('bedrock-agentcore:GetWorkloadAccessToken'));
    assert(workloadToken);
    assert(![].concat(workloadToken.Resource).includes('*'));
    assert(JSON.stringify(workloadToken.Resource).includes('GatewayWorkloadIdentity'));
    assert.equal(byType('AWS::BedrockAgentCore::WorkloadIdentity')[0].Properties.Name,
      'ontology_security_test_gateway_m2m');
    const interpreter = policy('InterpreterRole');
    assert.deepEqual(actions(interpreter), ['s3:GetObjectVersion']);
    assert.equal(interpreter[0].Condition.StringEquals['s3:VersionId'], 'synthetic-v1');
    const tools = actions(policy('OntologyTools'));
    assert(tools.includes('kms:Verify'));
    assert(!tools.includes('kms:Sign'));
    assert(!tools.includes('lambda:InvokeFunction'));
    assert(!tools.some(action => action.startsWith('bedrock:')));
    const executionOwner = createHash('sha256').update('ontology-executions').digest('hex');
    for (const prefix of ['OntologyTools', 'ExecutionAuthority']) {
      const statements = policy(prefix);
      assert(!actions(statements).some(action => ['dynamodb:DeleteItem', 'dynamodb:UpdateItem', 'dynamodb:BatchWriteItem',
        's3:DeleteObject', 's3:DeleteObjectVersion'].includes(action)));
      const writes = statements.filter(statement => [].concat(statement.Action).includes('dynamodb:PutItem'));
      assert.equal(writes.length, 1);
      assert.deepEqual(writes[0].Condition['ForAllValues:StringEquals']['dynamodb:LeadingKeys'], [`owner#${executionOwner}`]);
    }
    assert(!tools.includes('s3:PutObject'));
    const sourceWrites = policy('ExecutionAuthority').filter(statement => [].concat(statement.Action).includes('s3:PutObject'));
    assert.equal(sourceWrites.length, 1);
    assert(JSON.stringify(sourceWrites[0].Resource).includes(`workspace/${executionOwner}/`));
    const registryOwner = createHash('sha256').update('ontology-key-registry').digest('hex');
    const bootstrap = policy('KeyRegistryBootstrap').find(statement => [].concat(statement.Action).includes('dynamodb:PutItem'));
    assert.deepEqual(bootstrap.Condition['ForAllValues:StringEquals']['dynamodb:LeadingKeys'], [`owner#${registryOwner}`]);
    assert(runtime.includes('kms:GenerateMac'));
    assert.equal(byType('AWS::BedrockAgentCore::RuntimeEndpoint').length, 1);
    const gateway = actions(policy('GatewayRole'));
    assert.deepEqual(gateway, ['lambda:InvokeFunction']);
    const runtimeSign = policy('RuntimeRole').find(statement => [].concat(statement.Action).includes('kms:Sign'));
    const authoritySign = policy('ExecutionAuthority').find(statement => [].concat(statement.Action).includes('kms:Sign'));
    assert.notDeepEqual(runtimeSign.Resource, authoritySign.Resource);
    const runtimeResource = byType('AWS::BedrockAgentCore::Runtime')[0];
    assert(runtimeResource.DependsOn.some(name => name.startsWith('RuntimeRoleDefaultPolicy')));
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

function _ontologyStack(appContext) {
  const directory = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'ontology-iac-'));
  fs.writeFileSync(path.join(directory, 'Dockerfile'), 'FROM scratch\n');
  const stack = new OntologyStack(new cdk.App({ context: appContext }), 'OntologyTest', {
    env: { account: '111122223333', region: 'ap-northeast-2' },
    runtimeDirectory: directory, isolatedSubnets: ['subnet-00000000000000001'],
    isolatedSecurityGroups: ['sg-00000000000000001'], resourcePrefix: 'ontology_intake_test',
    toolArchive: { bucket: 'synthetic-tools', key: `tools/${'a'.repeat(64)}.tar.gz`, version: 'synthetic-v1',
      archiveHash: 'a'.repeat(64), analyzerCodeHash: 'b'.repeat(64), dependencyLockHash: 'c'.repeat(64) },
  });
  return { directory, resources: Template.fromStack(stack).toJSON().Resources };
}

function _environment(resources, prefix) {
  const [, resource] = Object.entries(resources).find(([key, value]) =>
    key.startsWith(prefix) && value.Type === 'AWS::Lambda::Function');
  return resource.Properties.Environment?.Variables ?? {};
}

test('ontology adapters carry no INTAKE_DEPLOYMENT when intake is not configured', () => {
  const { directory, resources } = _ontologyStack({});
  try {
    assert.equal(_environment(resources, 'OntologyTools').INTAKE_DEPLOYMENT, undefined);
    assert.equal(_environment(resources, 'ExecutionAuthority').INTAKE_DEPLOYMENT, undefined);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('ontology adapters share the same INTAKE_DEPLOYMENT as the rest of the intake module (PR #22 review 2, finding 4)', () => {
  // admission.require()'s current_policy() rejects every policy without a
  // deployment scope; both the Authority and Tools Lambdas must receive the
  // identical value intake.ts/workspace.ts give the intake module's own functions.
  const { directory, resources } = _ontologyStack({ intakeDeployment: 'offline-ontology-test' });
  try {
    assert.equal(_environment(resources, 'OntologyTools').INTAKE_DEPLOYMENT, 'offline-ontology-test');
    assert.equal(_environment(resources, 'ExecutionAuthority').INTAKE_DEPLOYMENT, 'offline-ontology-test');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
