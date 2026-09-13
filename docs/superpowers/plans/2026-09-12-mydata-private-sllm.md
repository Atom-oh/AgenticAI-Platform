# Historical implementation record: MyData private sLLM

- Date: 2026-09-12
- Status: Code merged in PR #3 (`5a14725`); scoped deployment not established by that merge
- Active requirements: `SPEC.md` §4-3
- Design history: `docs/superpowers/specs/2026-09-12-mydata-private-sllm.md`
- Operations: `platform/infra/README-privacy.md`

## Completed code scope

Private CPU gateway -> existing EKS Qwen/vLLM -> validated entity evidence and
deterministic replacement. An IAM-only VPC Lambda relay connects the API to a fixed
private endpoint. S2 processes free-text questions before Bedrock; the trusted producer
tokenizes structured fields and independent checks validate the complete payload.
This is not a second sLLM invocation over structured financial facts.

Implemented gateway/schema/financial-preservation checks, private model allowlist,
receipt filtering, relay/S2 ordering and cache restrictions, model readiness UI,
synthetic scenarios, scoped CDK/Kubernetes resources, and synthetic evaluation/training
job preparation. Candidate models and actual SageMaker execution remain separate.

## Review and verification record

The latest reviewed feature HEAD `b3d05e1` had all five platform CI jobs pass and an
independent 53-file review with no unresolved Critical/Major findings. Earlier review
iterations fixed diagnostic re-exposure and multiple financial-span/email regressions.
Do not reinterpret those valid defects as documentation false positives.

Existing GPU inference was exercised with synthetic input. That did not verify the
new Lambda/NLB network route. Shared-cluster CNI/network-policy prerequisites had
wider effects on Argo CD, runners and Qwen startup, so cluster-wide changes were not
inferred from the scoped gateway authorization. Verify prerequisites and the complete
live path before reporting deployment or network-policy enforcement complete.
