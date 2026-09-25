import * as fs from 'node:fs';
import * as cdk from 'aws-cdk-lib';
import { OntologyStack } from '../lib/ontology-stack';

const app = new cdk.App();
const configPath = app.node.tryGetContext('ontologyConfigFile');
if (typeof configPath !== 'string') throw new Error('Supply an operator-owned ontologyConfigFile');
const configuration = JSON.parse(fs.readFileSync(configPath, 'utf8'));
new OntologyStack(app, configuration.stackName ?? 'BankPlatformOntology', {
  ...configuration, env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-northeast-2' },
  description: 'Separate project ontology execution: AgentCore Runtime, tools, Identity, Memory and verification',
});
