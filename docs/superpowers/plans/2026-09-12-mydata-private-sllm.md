# MyData private sLLM Implementation Plan

> For agentic workers: execute the disjoint tasks with subagent-driven development, then independently review the combined diff.

**Goal:** Add an actually executed EKS sLLM privacy stage to MyData, with testable failure behavior and an optional evaluated tuning path.
**Architecture:** Private EKS gateway → existing Qwen vLLM/independent validator. An IAM-only VPC Lambda relay connects the platform API without publishing a model endpoint. S2 de-identifies question and prepared payload before Bedrock and disables personal-result caching.
**Tech Stack:** Python, existing React/Vite UI, Kubernetes, CDK, EKS/vLLM, optional SageMaker job preparation.
**Spec:** docs/superpowers/specs/2026-09-12-mydata-private-sllm.md

## Tasks

- [x] Implement gateway entity validation/redaction, private model allowlist, safe evidence and bounded HTTP service; first write failure/financial-fact preservation tests.
- [x] Implement API relay client and S2 ordering/error/cache changes; first test that raw text cannot reach Guardrails and cached personal events cannot replay.
- [x] Add the S2 privacy-model readiness controls, synthetic scenarios and actual pass/block evidence; verify rendered interactions.
- [x] Add scoped CDK/Kubernetes deployment resources and offline validation of the private network/IAM diff.
- [x] Add synthetic evaluation fixtures and an optional SageMaker training-job preparation workflow; validate its inputs and distinguish prepared/trained/approved states.
- [ ] Run targeted tests, source/type/build checks and independent AI review. Publish PR, fix Critical/Major feedback, wait for CI and merge per standing authorization.
- [ ] Deploy only the scoped resources and API/UI updates. Exercise live synthetic inference plus failure gates; record model identity, evidence, and remaining unconfigured models/training status.

The shared EKS network-policy activation has wider effects on existing Argo CD,
runner and Qwen startup paths. Its scope decision is pending; no cluster-wide
change is inferred from elapsed time. ListenerSet/CNI prerequisite diffs and
read-only evidence are prepared separately. Existing GPU inference was tested
with synthetic input; this is not proof of the new Lambda/NLB network path.
