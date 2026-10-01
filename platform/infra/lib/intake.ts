import * as crypto from 'crypto';
import * as cdk from 'aws-cdk-lib';
import { Construct, Node } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';

/** DynamoDB partition key of the `intake:deployment` owner (workspace `Storage._key`). */
export const INTAKE_PARTITION = 'owner#' + crypto.createHash('sha256').update('intake:deployment').digest('hex');

/** Item-level write actions that must never reach the administrative partition from a workload. */
export const INTAKE_WRITE_ACTIONS = [
  'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem', 'dynamodb:BatchWriteItem',
  'dynamodb:PartiQLInsert', 'dynamodb:PartiQLUpdate', 'dynamodb:PartiQLDelete',
];

/** Intake is configured once any intake context is supplied (admin function, deny-list or deployment). */
export function intakeConfigured(node: Node): boolean {
  const admin = node.tryGetContext('intakeAdmin');
  return admin === true || admin === 'true' || node.tryGetContext('intakeDenylistParam') !== undefined
    || node.tryGetContext('intakeDeployment') !== undefined;
}

/** The single deployment scope shared by the Workspace API, the Worker and IntakeAdminFn. */
export function intakeDeploymentScope(node: Node, stack: cdk.Stack): string {
  return String(node.tryGetContext('intakeDeployment') ?? stack.stackName);
}

/**
 * source-admission/1: policies, provenance and reviewer grants share the workspace
 * table, so a workload role that can write the table gets an explicit Deny on the
 * `intake:deployment` partition. Reads and ConditionCheckItem fences stay allowed.
 */
export function denyIntakeAdministrationWrites(role: iam.IRole, table: dynamodb.ITable): void {
  role.addToPrincipalPolicy(new iam.PolicyStatement({
    effect: iam.Effect.DENY,
    actions: INTAKE_WRITE_ACTIONS,
    resources: [table.tableArn],
    conditions: { 'ForAnyValue:StringEquals': { 'dynamodb:LeadingKeys': [INTAKE_PARTITION] } },
  }));
}

export interface IntakeAdminProps {
  apiCode: lambda.Code;
  table: dynamodb.ITable;
}

/**
 * source-admission/1 IAM-only administration (policy, provenance, reviewer grants,
 * resolver profiles). Authority is `lambda:InvokeFunction` by the platform security
 * operator. There is no API route, Function URL or apigateway resource policy, and
 * the role reaches only the `intake:deployment` partition of the workspace table.
 * Synthesized behind `-c intakeAdmin=true`; deployment belongs to B1.
 */
export class IntakeAdmin extends Construct {
  public readonly fn: lambda.Function;

  constructor(scope: Construct, id: string, props: IntakeAdminProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);
    const partition = INTAKE_PARTITION;
    const logGroup = new logs.LogGroup(this, 'Logs', {
      retention: logs.RetentionDays.ONE_MONTH, removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    const role = new iam.Role(this, 'Role', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Intake administration: intake:deployment records only',
    });
    logGroup.grantWrite(role);
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query', 'dynamodb:ConditionCheckItem'],
      resources: [props.table.tableArn],
      conditions: { 'ForAllValues:StringEquals': { 'dynamodb:LeadingKeys': [partition] } },
    }));
    const denylistParam = this.node.tryGetContext('intakeDenylistParam');
    if (denylistParam !== undefined) {
      if (typeof denylistParam !== 'string' || !/^\/[A-Za-z0-9_./-]{1,1000}$/.test(denylistParam)) {
        throw new Error('intakeDenylistParam must be an SSM parameter name starting with "/".');
      }
      role.addToPolicy(new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [`arn:${stack.partition}:ssm:${stack.region}:${stack.account}:parameter${denylistParam}`],
      }));
    }
    this.fn = new lambda.Function(this, 'Fn', {
      runtime: lambda.Runtime.PYTHON_3_12, handler: 'intake.admin_handler.handler',
      code: props.apiCode, role, memorySize: 256, timeout: cdk.Duration.seconds(30),
      reservedConcurrentExecutions: 1, logGroup,
      environment: {
        WORKSPACE_TABLE: props.table.tableName,
        INTAKE_DEPLOYMENT: intakeDeploymentScope(this.node, stack),
      },
      description: 'IAM-only source-admission administration; no API route or Function URL',
    });
    new cdk.CfnOutput(this, 'IntakeAdminFunctionName', { value: this.fn.functionName });
  }
}
