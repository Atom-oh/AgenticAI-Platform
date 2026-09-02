import * as cdk from 'aws-cdk-lib';
import { BankPlatformStack } from '../lib/stack';

const app = new cdk.App();
new BankPlatformStack(app, 'BankPlatform', {
  env: { region: 'ap-northeast-2' },
  description: 'Agentic AI Platform bank demo (SPEC.md) - S1 GraphRAG vs Vector RAG',
});
