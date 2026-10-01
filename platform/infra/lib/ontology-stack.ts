import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as customResources from 'aws-cdk-lib/custom-resources';
import { createHash } from 'node:crypto';

export interface OntologyStackProps extends cdk.StackProps {
  runtimeDirectory: string;
  isolatedSubnets: string[];
  isolatedSecurityGroups: string[];
  toolArchive: {
    bucket: string; key: string; version: string; archiveHash: string;
    analyzerCodeHash: string; dependencyLockHash: string;
  };
  workspaceTableName?: string;
  workspaceBucketName?: string;
  resourcePrefix?: string;
}

/** Separate JWT Gateway and execution roles; the bank IAM Gateway is untouched. */
export class OntologyStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: OntologyStackProps) {
    super(scope, id, props);
    if (!props.isolatedSubnets.length || !props.isolatedSecurityGroups.length ||
        props.toolArchive.key !== `tools/${props.toolArchive.archiveHash}.tar.gz` ||
        [props.toolArchive.archiveHash, props.toolArchive.analyzerCodeHash, props.toolArchive.dependencyLockHash]
          .some(value => !/^[a-f0-9]{64}$/.test(value))) {
      throw new Error('Ontology requires isolated networking and an immutable, verified tool archive');
    }
    const prefix = props.resourcePrefix ?? 'bankplatform_ontology';
    const workload = `${prefix}_gateway_m2m`;
    // IAM Runtime invocations use a dedicated machine identity. Runtime-managed
    // identities cannot issue tokens through the manual Identity APIs.
    const gatewayIdentity = new agentcore.CfnWorkloadIdentity(this, 'GatewayWorkloadIdentity', { name: workload });
    const table = props.workspaceTableName ?
      dynamodb.Table.fromTableName(this, 'WorkspaceTable', props.workspaceTableName) :
      new dynamodb.Table(this, 'SyntheticWorkspaceTable', {
        partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
        sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, encryption: dynamodb.TableEncryption.AWS_MANAGED,
        timeToLiveAttribute: 'ttl', removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
    const sources = props.workspaceBucketName ?
      s3.Bucket.fromBucketName(this, 'WorkspaceSources', props.workspaceBucketName) :
      new s3.Bucket(this, 'SyntheticSources', {
        encryption: s3.BucketEncryption.S3_MANAGED, blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true, versioned: true, removalPolicy: cdk.RemovalPolicy.RETAIN,
      });
    const capabilities = new kms.Key(this, 'CapabilityKey', {
      keySpec: kms.KeySpec.RSA_2048, keyUsage: kms.KeyUsage.SIGN_VERIFY,
      description: 'Only the IAM execution authority may sign project capabilities',
    });
    const evidence = new kms.Key(this, 'EvidenceKey', {
      keySpec: kms.KeySpec.RSA_2048, keyUsage: kms.KeyUsage.SIGN_VERIFY,
      description: 'Runtime evidence; never accepted as an execution capability key',
    });
    const memoryNamespaceKey = new kms.Key(this, 'MemoryNamespaceKey', {
      keySpec: kms.KeySpec.HMAC_256, keyUsage: kms.KeyUsage.GENERATE_VERIFY_MAC,
      description: 'Keyed project and actor namespaces for ontology Memory',
    });
    const executionOwner = createHash('sha256').update('ontology-executions').digest('hex');
    const registryOwner = createHash('sha256').update('ontology-key-registry').digest('hex');
    const grantExecutionStore = (fn: lambda.Function, resultWriter: boolean) => {
      table.grantReadData(fn);
      sources.grantRead(fn, 'workspace/*');
      fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['dynamodb:ConditionCheckItem'], resources: [table.tableArn],
      }));
      fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['dynamodb:PutItem'], resources: [table.tableArn],
        conditions: { 'ForAllValues:StringEquals': { 'dynamodb:LeadingKeys': [`owner#${executionOwner}`] } },
      }));
      if (resultWriter) fn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['s3:PutObject'], resources: [sources.arnForObjects(`workspace/${executionOwner}/*`)],
      }));
    };
    const principal = () => new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
      conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
    });
    const interpreterRole = new iam.Role(this, 'InterpreterRole', { assumedBy: principal() });
    interpreterRole.addToPolicy(new iam.PolicyStatement({
      actions: ['s3:GetObjectVersion'],
      resources: [`arn:${this.partition}:s3:::${props.toolArchive.bucket}/${props.toolArchive.key}`],
      conditions: { StringEquals: { 's3:VersionId': props.toolArchive.version } },
    }));
    const interpreter = new agentcore.CfnCodeInterpreterCustom(this, 'Interpreter', {
      name: prefix, executionRoleArn: interpreterRole.roleArn, networkConfiguration: { networkMode: 'SANDBOX' },
    });
    const browserRole = new iam.Role(this, 'BrowserRole', { assumedBy: principal() });
    browserRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ec2:CreateNetworkInterface', 'ec2:DescribeNetworkInterfaces', 'ec2:DescribeSubnets',
        'ec2:DescribeSecurityGroups', 'ec2:DescribeVpcs', 'ec2:DeleteNetworkInterface',
        'ec2:AssignPrivateIpAddresses', 'ec2:UnassignPrivateIpAddresses'],
      resources: ['*'], conditions: { StringEquals: { 'aws:RequestedRegion': this.region } },
    }));
    const browser = new agentcore.CfnBrowserCustom(this, 'Browser', {
      name: prefix, executionRoleArn: browserRole.roleArn, recordingConfig: { enabled: false },
      networkConfiguration: { networkMode: 'VPC', vpcConfig: {
        subnets: props.isolatedSubnets, securityGroups: props.isolatedSecurityGroups,
      } },
    });
    const memory = new agentcore.CfnMemory(this, 'Memory', {
      name: prefix, eventExpiryDuration: 3, description: 'Structured references only; no extraction strategies',
    });
    const pool = new cognito.CfnUserPool(this, 'MachinePool', {
      userPoolName: prefix + '_machine', adminCreateUserConfig: { allowAdminCreateUserOnly: true },
    });
    const domainName = `${prefix.replace(/_/g, '-')}-${this.account}`;
    const domain = new cognito.CfnUserPoolDomain(this, 'MachineDomain', { domain: domainName, userPoolId: pool.ref });
    const resource = new cognito.CfnUserPoolResourceServer(this, 'MachineScope', {
      identifier: 'ontology', name: 'Ontology tools', userPoolId: pool.ref,
      scopes: [{ scopeName: 'tools', scopeDescription: 'Dedicated ontology Gateway workload' }],
    });
    const client = new cognito.CfnUserPoolClient(this, 'MachineClient', {
      userPoolId: pool.ref, clientName: prefix, generateSecret: true,
      allowedOAuthFlowsUserPoolClient: true, allowedOAuthFlows: ['client_credentials'],
      allowedOAuthScopes: ['ontology/tools'], accessTokenValidity: 5,
      tokenValidityUnits: { accessToken: 'minutes' }, supportedIdentityProviders: ['COGNITO'],
    });
    client.addDependency(resource);
    const issuer = `https://cognito-idp.${this.region}.amazonaws.com/${pool.ref}`;
    const oauthDomain = `https://${domainName}.auth.${this.region}.amazoncognito.com`;
    const provider = new agentcore.CfnOAuth2CredentialProvider(this, 'CredentialProvider', {
      name: prefix.replace(/_/g, '-'), credentialProviderVendor: 'CustomOauth2',
      oauth2ProviderConfigInput: { customOauth2ProviderConfig: {
        clientId: client.ref, clientSecret: client.attrClientSecret, clientAuthenticationMethod: 'CLIENT_SECRET_BASIC',
        oauthDiscovery: { authorizationServerMetadata: {
          issuer, authorizationEndpoint: oauthDomain + '/oauth2/authorize',
          tokenEndpoint: oauthDomain + '/oauth2/token', responseTypes: ['token'],
        } },
      } },
    });
    provider.addDependency(domain);
    const secretMetadata = new customResources.AwsCustomResource(this, 'IdentitySecretMetadata', {
      onUpdate: {
        service: '@aws-sdk/client-bedrock-agentcore-control', action: 'GetOauth2CredentialProvider',
        parameters: { name: provider.name },
        physicalResourceId: customResources.PhysicalResourceId.of(`${prefix}-identity-secret-metadata`),
        outputPaths: ['clientSecretArn.secretArn'],
      },
      installLatestAwsSdk: false,
      policy: customResources.AwsCustomResourcePolicy.fromStatements([new iam.PolicyStatement({
        actions: ['bedrock-agentcore:GetOauth2CredentialProvider'],
        resources: [provider.attrCredentialProviderArn,
          `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`],
      })]),
    });
    secretMetadata.node.addDependency(provider);
    const image = new ecrAssets.DockerImageAsset(this, 'ExecutionImage', {
      directory: props.runtimeDirectory, platform: ecrAssets.Platform.LINUX_ARM64,
    });
    const common = { capabilityKeyId: 'cap-v1', evidenceKeyId: 'evidence-v1',
      evidenceKeyArn: evidence.keyArn, workload, workloadIdentityArn: gatewayIdentity.attrWorkloadIdentityArn,
      toolArchiveHash: props.toolArchive.archiveHash };
    const tools = new lambda.DockerImageFunction(this, 'OntologyTools', {
      code: lambda.DockerImageCode.fromEcr(image.repository, {
        tagOrDigest: image.assetHash, entrypoint: ['python', '-m', 'awslambdaric'],
        cmd: ['ontology_runtime.entrypoints.tools_handler'],
      }),
      architecture: lambda.Architecture.ARM_64, memorySize: 1024, timeout: cdk.Duration.seconds(120),
      reservedConcurrentExecutions: 10,
      environment: { WORKSPACE_TABLE: table.tableName, WORKSPACE_BUCKET: sources.bucketName,
        ONTOLOGY_CONFIGURATION: this.toJsonString(common) },
    });
    grantExecutionStore(tools, false);
    tools.addToRolePolicy(new iam.PolicyStatement({
      actions: ['kms:Verify', 'kms:GetPublicKey', 'kms:DescribeKey'], resources: [capabilities.keyArn],
    }));
    const gatewayRole = new iam.Role(this, 'GatewayRole', { assumedBy: principal() });
    tools.grantInvoke(gatewayRole);
    const gateway = new agentcore.CfnGateway(this, 'Gateway', {
      name: prefix.replace(/_/g, '-'), roleArn: gatewayRole.roleArn, protocolType: 'MCP', authorizerType: 'CUSTOM_JWT',
      authorizerConfiguration: { customJwtAuthorizer: {
        discoveryUrl: issuer + '/.well-known/openid-configuration',
        allowedClients: [client.ref], allowedScopes: ['ontology/tools'],
      } },
    });
    const authorization = { type: 'object', properties: { capability: { type: 'string' }, operationId: { type: 'string' } },
      required: ['capability', 'operationId'] };
    const reference = { type: 'object', properties: Object.fromEntries(
      ['sourceKind', 'sourceId', 'revision', 'sha256', 'audienceRevision'].map(name => [name, { type: 'string' }])),
      required: ['sourceKind', 'sourceId', 'revision', 'sha256', 'audienceRevision'] };
    const definitions: { name: string; properties: Record<string, agentcore.CfnGatewayTarget.SchemaDefinitionProperty>; required: string[] }[] = [
      { name: 'context', properties: { nodeIds: { type: 'array', items: { type: 'string' } } }, required: ['nodeIds'] },
      { name: 'source', properties: { sourceRef: reference }, required: ['sourceRef'] },
      { name: 'stage', properties: { stage: { type: 'string' }, receiptHash: { type: 'string' } }, required: ['stage', 'receiptHash'] },
      { name: 'finish', properties: { manifestHash: { type: 'string' } }, required: ['manifestHash'] },
    ];
    const target = new agentcore.CfnGatewayTarget(this, 'Target', {
      gatewayIdentifier: gateway.attrGatewayIdentifier, name: 'ontology',
      targetConfiguration: { mcp: { lambda: { lambdaArn: tools.functionArn, toolSchema: {
        inlinePayload: definitions.map(definition => ({
          name: definition.name, description: `Authorized ontology ${definition.name}`,
          inputSchema: { type: 'object', properties: { ...definition.properties, _executionAuthorization: authorization },
            required: [...definition.required, '_executionAuthorization'] },
        })),
      } } } },
      credentialProviderConfigurations: [{ credentialProviderType: 'GATEWAY_IAM_ROLE' }],
    });
    const runtimeRole = new iam.Role(this, 'RuntimeRole', { assumedBy: principal() });
    image.repository.grantPull(runtimeRole);
    runtimeRole.addToPolicy(new iam.PolicyStatement({ actions: ['kms:Sign'], resources: [evidence.keyArn] }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['kms:GenerateMac'], resources: [memoryNamespaceKey.keyArn],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetResourceOauth2Token'], resources: [provider.attrCredentialProviderArn,
        gatewayIdentity.attrWorkloadIdentityArn,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:token-vault/default`,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetWorkloadAccessToken'], resources: [gatewayIdentity.attrWorkloadIdentityArn,
        `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default`],
    }));
    // Identity's OAuth API authorizes access to its backing secret as a
    // dependent action. Grant only this provider's exact managed-secret ARN.
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [secretMetadata.getResponseField('clientSecretArn.secretArn')],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:StartCodeInterpreterSession', 'bedrock-agentcore:InvokeCodeInterpreter',
        'bedrock-agentcore:StopCodeInterpreterSession'], resources: [interpreter.attrCodeInterpreterArn],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:StartBrowserSession', 'bedrock-agentcore:StopBrowserSession'],
      resources: [browser.attrBrowserArn],
    }));
    // AWS does not support resource-level authorization for this stream action.
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:ConnectBrowserAutomationStream'], resources: ['*'],
      conditions: { StringEquals: { 'aws:RequestedRegion': this.region } },
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:CreateEvent', 'bedrock-agentcore:ListEvents', 'bedrock-agentcore:GetEvent'],
      resources: [memory.attrMemoryArn],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
    }));
    const runtime = new agentcore.CfnRuntime(this, 'Runtime', {
      agentRuntimeName: prefix, roleArn: runtimeRole.roleArn, protocolConfiguration: 'HTTP',
      agentRuntimeArtifact: { containerConfiguration: { containerUri: image.imageUri } },
      networkConfiguration: { networkMode: 'PUBLIC' },
      environmentVariables: { AWS_REGION: this.region, ONTOLOGY_RUNTIME_CONFIGURATION: this.toJsonString({
        gatewayUrl: gateway.attrGatewayUrl, workload, workloadIdentityArn: gatewayIdentity.attrWorkloadIdentityArn,
        provider: provider.name, memoryId: memory.attrMemoryId,
        memoryNamespaceKeyArn: memoryNamespaceKey.keyArn, organization: `${this.account}:${prefix}`,
        evidenceKeyArn: evidence.keyArn,
        interpreter: { ...props.toolArchive, identifier: interpreter.attrCodeInterpreterId,
          region: this.region, architecture: 'arm64', nodeMajor: 24 },
      }) },
    });
    const endpoint = new agentcore.CfnRuntimeEndpoint(this, 'RuntimeEndpoint', {
      agentRuntimeId: runtime.attrAgentRuntimeId, agentRuntimeVersion: runtime.attrAgentRuntimeVersion,
      name: `v${runtime.attrAgentRuntimeVersion}`,
    });
    endpoint.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    // Retain the service-managed identity as deployment evidence; the separate
    // M2M identity above is the one authorized to broker Gateway credentials.
    const runtimeIdentityArn = `arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/default/workload-identity/${runtime.attrAgentRuntimeId}`;
    const authority = new lambda.DockerImageFunction(this, 'ExecutionAuthority', {
      code: lambda.DockerImageCode.fromEcr(image.repository, {
        tagOrDigest: image.assetHash, entrypoint: ['python', '-m', 'awslambdaric'],
        cmd: ['ontology_runtime.entrypoints.authority_handler'],
      }),
      architecture: lambda.Architecture.ARM_64, memorySize: 1024, timeout: cdk.Duration.minutes(15),
      reservedConcurrentExecutions: 2,
      environment: { WORKSPACE_TABLE: table.tableName, WORKSPACE_BUCKET: sources.bucketName,
        ONTOLOGY_CONFIGURATION: this.toJsonString({ ...common, runtimeArn: runtime.attrAgentRuntimeArn,
          runtimeQualifier: endpoint.name }) },
    });
    grantExecutionStore(authority, true);
    authority.addToRolePolicy(new iam.PolicyStatement({
      actions: ['kms:Sign', 'kms:GetPublicKey', 'kms:DescribeKey'], resources: [capabilities.keyArn],
    }));
    const bootstrap = new lambda.DockerImageFunction(this, 'KeyRegistryBootstrap', {
      code: lambda.DockerImageCode.fromEcr(image.repository, {
        tagOrDigest: image.assetHash, entrypoint: ['python', '-m', 'awslambdaric'],
        cmd: ['ontology_runtime.bootstrap.handler'],
      }),
      architecture: lambda.Architecture.ARM_64, memorySize: 256, timeout: cdk.Duration.minutes(2),
      environment: { WORKSPACE_TABLE: table.tableName, WORKSPACE_BUCKET: sources.bucketName },
    });
    bootstrap.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem'], resources: [table.tableArn],
      conditions: { 'ForAllValues:StringEquals': { 'dynamodb:LeadingKeys': [`owner#${registryOwner}`] } },
    }));
    bootstrap.addToRolePolicy(new iam.PolicyStatement({
      actions: ['kms:GetPublicKey', 'kms:DescribeKey'], resources: [capabilities.keyArn, evidence.keyArn],
    }));
    const bootstrapProvider = new customResources.Provider(this, 'KeyRegistryProvider', { onEventHandler: bootstrap });
    new cdk.CustomResource(this, 'KeyRegistry', {
      serviceToken: bootstrapProvider.serviceToken,
      properties: { capabilityKeyArn: capabilities.keyArn, evidenceKeyArn: evidence.keyArn,
        gatewayId: gateway.attrGatewayIdentifier, targetId: target.attrTargetId,
        workloadIdentityArn: gatewayIdentity.attrWorkloadIdentityArn, codeRevision: image.assetHash },
    });
    authority.addToRolePolicy(new iam.PolicyStatement({
      actions: ['kms:Verify', 'kms:GetPublicKey', 'kms:DescribeKey'], resources: [evidence.keyArn],
    }));
    authority.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:InvokeAgentRuntime'], resources: [runtime.attrAgentRuntimeArn, `${runtime.attrAgentRuntimeArn}/*`],
    }));
    // These services validate execution-role permissions during creation.
    // Referencing the Role ARN alone does not depend on its separate IAM Policy.
    for (const [resource, role] of [[interpreter, interpreterRole], [browser, browserRole],
      [gateway, gatewayRole], [runtime, runtimeRole]] as const) {
      const policy = role.node.tryFindChild('DefaultPolicy');
      if (policy) resource.node.addDependency(policy);
    }
    const authorityVersion = authority.currentVersion;
    authorityVersion.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);
    const outputs: Record<string, string> = {
      WorkspaceTable: table.tableName, WorkspaceBucket: sources.bucketName, RuntimeArn: runtime.attrAgentRuntimeArn,
      AuthorityArn: authorityVersion.functionArn, ToolsArn: tools.functionArn, GatewayId: gateway.attrGatewayIdentifier,
      GatewayUrl: gateway.attrGatewayUrl, TargetId: target.attrTargetId, CapabilityKeyArn: capabilities.keyArn,
      EvidenceKeyArn: evidence.keyArn, Workload: workload, RuntimeRoleArn: runtimeRole.roleArn,
      RuntimeWorkloadIdentityArn: runtimeIdentityArn,
      GatewayWorkloadIdentityArn: gatewayIdentity.attrWorkloadIdentityArn,
    };
    for (const [name, value] of Object.entries(outputs)) new cdk.CfnOutput(this, name, { value });
  }
}
