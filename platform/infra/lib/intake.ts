import * as crypto from 'crypto';
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';

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
    const partition = 'owner#' + crypto.createHash('sha256').update('intake:deployment').digest('hex');
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
        INTAKE_DEPLOYMENT: String(this.node.tryGetContext('intakeDeployment') ?? stack.stackName),
      },
      description: 'IAM-only source-admission administration; no API route or Function URL',
    });
    new cdk.CfnOutput(this, 'IntakeAdminFunctionName', { value: this.fn.functionName });
  }
}
