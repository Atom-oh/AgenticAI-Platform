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
    for (const prefix of ['OntologyTools', 'ExecutionAuthority', 'ExecutionWatchdog', 'ExecutionReconciler']) {
      const statements = policy(prefix);
      const allowed = statements.filter(statement => statement.Effect === 'Allow');
      assert(!actions(allowed).some(action => ['dynamodb:DeleteItem', 'dynamodb:UpdateItem', 'dynamodb:BatchWriteItem',
        's3:DeleteObjectVersion'].includes(action)));
      const deny = statements.filter(statement => statement.Effect === 'Deny');
      const partitions = deny.flatMap(statement => statement.Condition['ForAnyValue:StringEquals']['dynamodb:LeadingKeys']);
      for (const owner of ['intake:deployment', 'ontology-key-registry']) {
        assert(partitions.includes('owner#' + createHash('sha256').update(owner).digest('hex')));
      }
    }
    assert(tools.includes('s3:PutObject'));
    assert(!actions(policy('ExecutionAuthority')).includes('s3:PutObject'));
    assert.equal(byType('AWS::Lambda::EventSourceMapping').length, 1);
    assert.equal(byType('AWS::Lambda::EventSourceMapping')[0].Properties.BatchSize, 1);
    assert(JSON.stringify(byType('AWS::Lambda::EventSourceMapping')[0].Properties.FunctionName).includes('Version'));
    assert.equal(byType('AWS::Events::Rule').length, 1);
    const definitions = byType('AWS::BedrockAgentCore::GatewayTarget')[0].Properties.TargetConfiguration.Mcp.Lambda.ToolSchema.InlinePayload;
    assert.deepEqual(definitions.map(row => row.Name), ['execution']);
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

function _ontologyStack(appContext, extra = {}) {
  const directory = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'ontology-iac-'));
  fs.writeFileSync(path.join(directory, 'Dockerfile'), 'FROM scratch\n');
  let stack;
  try {
    stack = new OntologyStack(new cdk.App({ context: appContext }), 'OntologyTest', {
      ...extra, ...{
    env: { account: '111122223333', region: 'ap-northeast-2' },
    runtimeDirectory: directory, isolatedSubnets: ['subnet-00000000000000001'],
    isolatedSecurityGroups: ['sg-00000000000000001'], resourcePrefix: 'ontology_intake_test',
    toolArchive: { bucket: 'synthetic-tools', key: `tools/${'a'.repeat(64)}.tar.gz`, version: 'synthetic-v1',
      archiveHash: 'a'.repeat(64), analyzerCodeHash: 'b'.repeat(64), dependencyLockHash: 'c'.repeat(64) },
      } });
  } catch (error) {
    fs.rmSync(directory, { recursive: true, force: true });
    throw error;
  }
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

// PR #34 review 1, finding 5: the main stack's intake functions default their
// scope to the MAIN stack's name. An ontology stack reusing that workspace table
// must resolve the identical scope, never silently its own stack name.
const { intakeDeploymentScope } = require('../lib/intake');

function _mainStackScope(appContext, name = 'BankPlatform') {
  return intakeDeploymentScope(new cdk.App({ context: appContext }).node,
    new cdk.Stack(new cdk.App({ context: appContext }), name));
}

const SHARED = { workspaceTableName: 'BankPlatform-WorkspaceTable', workspaceBucketName: 'bankplatform-workspace' };

test('a shared workspace table with intake configured and no explicit scope is refused', () => {
  for (const context of [{ intakeAdmin: true }, { intakeAdmin: 'true' }, { intakeDenylistParam: '/intake/denylist' }]) {
    assert.throws(() => _ontologyStack(context, SHARED), /intakeDeployment/);
  }
});

test('the default main-stack scope propagated through the ontology config matches on both adapters', () => {
  const main = _mainStackScope({ intakeAdmin: true });
  assert.equal(main, 'BankPlatform');
  const { directory, resources } = _ontologyStack({ intakeAdmin: true }, { ...SHARED, intakeDeployment: main });
  try {
    assert.equal(_environment(resources, 'OntologyTools').INTAKE_DEPLOYMENT, main);
    assert.equal(_environment(resources, 'ExecutionAuthority').INTAKE_DEPLOYMENT, main);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('an explicit intakeDeployment context resolves the same scope in both stacks', () => {
  const context = { intakeAdmin: true, intakeDeployment: 'bank-intake' };
  const main = _mainStackScope(context);
  const { directory, resources } = _ontologyStack(context, SHARED);
  try {
    assert.equal(main, 'bank-intake');
    assert.equal(_environment(resources, 'OntologyTools').INTAKE_DEPLOYMENT, main);
    assert.equal(_environment(resources, 'ExecutionAuthority').INTAKE_DEPLOYMENT, main);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('a configured scope that disagrees with the intakeDeployment context is refused', () => {
  assert.throws(() => _ontologyStack({ intakeDeployment: 'bank-intake' }, { ...SHARED, intakeDeployment: 'other' }),
    /intakeDeployment/);
});

test('a configured scope alone configures intake for the adapters', () => {
  const { directory, resources } = _ontologyStack({}, { ...SHARED, intakeDeployment: 'BankPlatform' });
  try {
    assert.equal(_environment(resources, 'OntologyTools').INTAKE_DEPLOYMENT, 'BankPlatform');
    assert.equal(_environment(resources, 'ExecutionAuthority').INTAKE_DEPLOYMENT, 'BankPlatform');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('a self-contained synthetic table keeps the stack-name default', () => {
  const { directory, resources } = _ontologyStack({ intakeAdmin: true });
  try {
    assert.equal(_environment(resources, 'OntologyTools').INTAKE_DEPLOYMENT, 'OntologyTest');
    assert.equal(_environment(resources, 'ExecutionAuthority').INTAKE_DEPLOYMENT, 'OntologyTest');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
