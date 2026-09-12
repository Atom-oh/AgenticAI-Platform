# MyData private sLLM de-identification

The user requested a MyData privacy scenario using an EKS small model, with the existing aws-fsi-demo implementation as reference and optional SageMaker tuning. The actual reference is /home/atomoh/aws-fsi-demo. The existing fsi-demo-cluster currently serves Qwen/Qwen3-8B; Gemma 4 and DeepSeek are model-family candidates, not already deployed detectors.

## Required behavior

1. The authenticated MyData request de-identifies free-text questions before any Bedrock Guardrails/model call. The internal producer tokenizes known structured fields in code, and the entire prepared payload passes independent inspection before explanation inference.
2. A dedicated EKS privacy gateway calls the existing private vLLM service. Model output is entity data only; deterministic code validates evidence and performs replacement. It cannot rewrite financial facts.
3. Rules and Bedrock Guardrails inspect the complete prepared payload; the optional private NER is explicitly unconfigured. Failed calls, malformed/truncated detections, invalid entities or residual identifiers block the request. The structured-data stage is labelled as code tokenization/independent verification, never as a second sLLM run. No cached-response fallback is presented as sLLM success.
4. S2 does not write or replay the shared event cache. Raw prompts and entity originals are not returned in privacy receipts or logged. Existing trusted-plane identity restoration is explicitly distinguished from irreversible removal of newly detected free-text identifiers; the UI must not claim legal/full anonymization.
5. Model selection uses a server-maintained allowlist. Only a reachable configured model is selectable. Qwen is the initial actual EKS implementation; Gemma/DeepSeek remain explicit unconfigured alternatives until their endpoint and artifact revision are registered.
6. The UI supplies synthetic scenarios, actual model/processor evidence, detected types/counts, latency and pass/block status. Existing explanation-model selection remains distinct from privacy-model selection.
7. A reproducible synthetic evaluation/export path supports optional SageMaker tuning. Preparing a job does not mean training ran, and a trained model is not promoted to EKS without evaluation and a pinned artifact.

## Deployment boundary

Reuse the existing GPU model. Add only a dedicated CPU privacy gateway in a separate EKS namespace, a private load balancer/target binding and a VPC Lambda relay callable by the platform's exact API role. No public model endpoint or new GPU fleet is required. No reference-project data or PII caches are copied. Deployment and PR/review/merge are authorized by the user's standing instructions.

## Interface

Gateway GET /health, GET /models; POST /deidentify {text,model,purpose,requestId}. Successful response: {ok:true,text,evidence}, with evidence limited to model identity/revision, processor, replacement method, types/counts, inspection statuses, sizes, latency and prompt version. Failure: {ok:false,error:{code,message},evidence?}, without input/output excerpts. The API invokes an IAM-protected Lambda relay using MYDATA_PRIVACY_FUNCTION_ARN. The relay has no function URL, accepts only models/deidentify operations, and forwards only to its fixed private endpoint.

## Verification

Unit tests cover schema violations, malformed/truncated model output, all-occurrence redaction, overlap, synthetic identifiers, preservation of amounts/rates/periods, missing model, and residual PII. S2 tests prove ordering before Guardrails, no downstream model on failure, no event-cache read/write/replay and no raw-query grounding. Browser tests cover model readiness, synthetic scenarios, evidence and blocking. Live verification uses synthetic text only and records actual EKS inference and the generated explanation path separately.

## Evidence-driven refinement

Live synthetic checks found that asking Qwen to reclassify an already-tokenized structured payload could misidentify public card-performance/product rules as personal identifiers. The final S2 design uses sLLM for unstructured questions, preserves the producer's typed financial computation/tokenization, and independently checks every outgoing payload. Private gateway support for trusted already-tokenized payloads remains a separate API capability. Generated markers are request-local 128-bit nonnumeric tokens; known legacy markers remain readable. Marker views never exempt arbitrary public input.
