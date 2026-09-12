# Optional offline tuning preparation

Use the private EKS baseline first. Tuning is optional and should only be proposed
after held-out evaluation identifies a specific deficiency. This stdlib-only utility
validates local examples and prepares a **CreateTrainingJob request**, without
making AWS calls, uploading data, downloading models, building containers, training,
evaluating a model, or promoting anything to EKS.

Run from the repository root:

```bash
PYTHONPATH=platform python3 -m privacy.training models
PYTHONPATH=platform python3 -m privacy.training validate \
  --train platform/privacy/training/fixtures/train.jsonl \
  --eval platform/privacy/training/fixtures/eval.jsonl \
  --data-policy synthetic \
  --output-dir /tmp/mydata-synthetic-validation
```

The output directory must not already exist; its parent must exist. Files are
created with mode `0600` inside a `0700` directory. Validation emits `manifest.json`
with hashes of the exact file bytes, byte/record/annotation counts, and an operator
declared data classification. It excludes raw text, entity originals, example IDs,
local paths, and raw approval references. Diagnostics contain fixed reasons and
split/line numbers only. Hashes and aggregate counts are provenance, **not proof of
anonymity**; retain them under the dataset's access policy.

The public fixtures are deliberately tiny, synthetic smoke examples using
`김테스트`, `.invalid` email addresses and invented financial facts. They are not a
statistically meaningful tuning or evaluation corpus.

## Dataset contract

One UTF-8 JSON object per line, with exactly these keys:

```json
{"id":"synthetic-001","text":"김테스트의 월 납입액은 300,000원입니다.","entities":[{"type":"PERSON","original":"김테스트"}]}
```

- `id`: opaque ASCII identifier, 1–128 characters, beginning with an alphanumeric
  character and otherwise containing alphanumerics, `.`, `_`, or `-`. Do not use
  customer identifiers.
- `text`: nonblank, valid Unicode, at most 8,192 **UTF-8 bytes**. Tab/newline/CR
  inside a JSON string are allowed; other ASCII control characters are rejected.
- `entities`: an array of at most 64 objects, each containing exactly `type` and
  `original`. Negative examples use `[]`.
- `type`: one of the imported `privacy.gateway.models.TYPES`; the validator does
  not maintain a separate taxonomy.
- `original`: nonempty string, no leading/trailing whitespace, at most 512
  characters, appearing **exactly** in the original text. Annotate a repeated
  original once; the gateway redacts all occurrences. Duplicate originals,
  including conflicting types, are rejected. Do not label amounts, rates, or
  durations as identifiers.

Both splits must be nonempty regular files. Each is limited to 10,000 records and
32 MiB, with at most 64 KiB per JSONL line (including its newline). Blank lines,
malformed UTF-8/JSON, duplicate JSON keys, and extra fields fail validation.

IDs are compared case-insensitively. Text is NFKC-normalized, casefolded and
whitespace-collapsed **only for duplicate detection**. IDs and normalized content
must be unique within each split and disjoint across splits, regardless of labels.
The underlying training text and exact-byte hash are never normalized or rewritten.
This detects exact/normalized leakage, not paraphrases, shared households, or
template variants; curate those splits separately.

`--data-policy synthetic` is the operator's assertion that all records are
synthetic. For previously approved data, use `--data-policy approved
--approval-reference REVIEW-ID`; the nonsecret approval reference is hashed in the
manifest. The utility cannot determine consent, provenance or completeness of
annotations from the text. It does not export or sanitize raw data for you.

## Prepare a request

Copy `config.example.json` to a private working directory and supply real reviewed
values. Its `null` values, empty lists and false approvals are intentionally
non-executable placeholders; no training image is provided or guessed.

```bash
PYTHONPATH=platform python3 -m privacy.training prepare \
  --train /private/approved/train.jsonl \
  --eval /private/approved/eval.jsonl \
  --data-policy approved --approval-reference REVIEW-ID \
  --config /private/approved/training-config.json \
  --output-dir /private/approved/prepared-request
```

`prepare` revalidates both local files, then creates:

| Artifact | Meaning |
| --- | --- |
| `manifest.json` | Validated local dataset hashes and counts. |
| `training-job.json` | API request body, with no extra preparation-only keys. Submitting it would start billable work; this utility never submits it. |
| `preparation.json` | `prepared-offline` status, exact manifest/request byte hashes, canonical config hash, supplied region/VPC, and operator-declared approval status. `training_started`, `evaluated`, and `deployment_approved` remain false. |

All config fields in the template are required; unknown overrides are rejected.

| Fields | Requirement |
| --- | --- |
| `training_job_name`, `region` | Valid bounded job name and commercial-region syntax. The utility does not verify regional availability. |
| `training_image` | Supplied **private ECR** URI in that region, ending in `@sha256:` plus 64 lowercase hex digits. Tags, public ECR, Docker Hub, and alternate registry hosts are rejected. |
| `role_arn` | IAM execution-role ARN in the image's account. No IAM users, wildcard resources, or cross-account configuration in this preparation workflow. |
| `trainer_contract_version`, `trainer_contract_approved` | Exact `mydata-entities-jsonl-v1` and JSON `true`, only after the trainer owner approves the exact image digest, model bundle digest, family and instance combination against the contract below. This is an assertion, not a container inspection. |
| `model_family` | `qwen`, `gemma4`, or `deepseek`; candidate metadata only. |
| `base_model_s3_uri`, `base_model_sha256` | S3 object URI ending in `.tar.gz`, and SHA-256 of that exact approved local-model bundle. The digest is supplied by the operator; the utility never obtains the bundle. |
| `train_s3_uri`, `eval_s3_uri` | Separate S3 object URIs ending in `.jsonl`, intended to hold the exact local bytes validated here. |
| `output_s3_uri` | Dedicated, nonempty S3 prefix ending in `/`, disjoint from all inputs. |
| `output_kms_key_arn`, `volume_kms_key_arn` | Explicit KMS **key** ARNs in the same account and region; aliases and service defaults are rejected. |
| `vpc_id`, `private_subnet_ids`, `security_group_ids`, `private_network_approved` | Explicit VPC, 1–16 unique private subnet IDs, 1–5 unique security-group IDs, and JSON `true` after infrastructure review. |
| `instance_type` | Deliberately limited EBS-backed subset: `ml.p3.2xlarge`, `ml.p3.8xlarge`, `ml.p3.16xlarge`, `ml.m5.xlarge`, `ml.m5.2xlarge`. One instance only. These are not claims that a model fits or trains on every listed type; the trainer owner must size and approve it. |
| `volume_size_gb`, `max_runtime_seconds` | Strict integers: 1–4096 GiB and 60–86,400 seconds. These are local preparation caps, not AWS quotas or a spending guarantee. |

S3 URIs use a deliberately restricted ASCII subset: normal DNS-style bucket names
and nonempty object keys composed of letters, digits, `.`, `_`, `-`, `/`. HTTP URLs,
queries, fragments, percent escapes, `.`/`..` path components and overlapping
prefixes are rejected. Avoid personal data in resource names and object keys.

SageMaker's `S3Prefix` input selects all objects beginning with the supplied URI,
even when that URI looks like a single filename. The upload/release owner must
stage exactly one object per channel and prevent additional prefix matches. The
approved trainer must reject extras and verify the hashes before consuming data.
Local validation cannot attest to S3 contents or bucket privacy/encryption.

The request always enables network isolation and inter-container traffic
encryption, specifies both KMS keys, disables managed Spot training and profiling,
and sets the runtime limit. It does not expose arbitrary environment variables,
hyperparameters, entrypoint overrides, extra channels or debug artifact paths.

`VpcConfig` accepts subnet and security-group IDs, **not a VPC ID**. The supplied
`vpc_id` is retained in `preparation.json` for operator review. Offline checks cannot
prove subnet/VPC membership, private routes, restricted security-group rules, S3
endpoint configuration, KMS key policies, IAM trust/permissions, image ownership,
or capacity. `private_network_approved` asserts that these were reviewed; it never
means AWS was queried. Review input S3 encryption and access policy as well as
output encryption before any separately authorized submission.

The EBS subset is intentional: AWS documents that instances with local storage
cannot accept `VolumeKmsKeyId`. In particular, do not substitute `g5`, `g4dn`,
`p3dn`, or `p4d` while claiming this KMS volume contract still holds.

## Required approved trainer/container contract

There is **no trainer implementation or preapproved image in this directory**.
Setting the approval flag requires a reviewed custom container that implements all
of the following. An arbitrary inference image or generic training DLC does not
become compatible by accepting this JSON.

1. Implement SageMaker's training-container lifecycle and default training
   entrypoint. Bake the trainer and all dependencies into the supplied digest.
   Do not expect a script channel, runtime package installation, credentials,
   telemetry, Hugging Face downloads or network access.
2. Require `MYDATA_CONTRACT_VERSION=mydata-entities-jsonl-v1`. Read
   `MYDATA_MODEL_FAMILY` as an explicitly approved adapter choice, not automatic
   architecture detection.
3. Consume these **File-mode** channel paths:

   | Path | Contents and required check |
   | --- | --- |
   | `/opt/ml/input/data/train/` | Exactly one JSONL file; verify exact-byte SHA-256 against `MYDATA_TRAIN_SHA256` and revalidate the dataset contract. |
   | `/opt/ml/input/data/eval/` | Exactly one disjoint JSONL file; verify `MYDATA_EVAL_SHA256`. Never train on it or select checkpoints/hyperparameters with it. |
   | `/opt/ml/input/data/base-model/` | Exactly one `.tar.gz` bundle; verify `MYDATA_BASE_MODEL_SHA256` **before** safe local extraction. Reject escaping paths, links and device entries. Bundle format, architecture, tokenizer, license and offline loader must be approved for this image. |

4. The base bundle must contain all weights, tokenizer/configuration and other
   files required by the chosen trainer. Model IDs, chat templates, loss masks,
   adapter strategy, accelerator requirements and serialization are
   **family/trainer-specific**. The registry supplies no versions or compatibility
   claims for Gemma 4 or DeepSeek; its Qwen baseline reference comes from the MyData
   spec, and does not register a Qwen trainer.
5. Convert each training example into the reviewed detector's input/target format:
   source `text` as untrusted user content; target exactly
   `{"entities":[{"type":"…","original":"…"}]}`. Do not teach a free-form financial
   text rewrite. Include the gateway taxonomy and approved detector prompt in the
   trainer; `id` is bookkeeping, never a target. A trainer requiring another schema
   needs an explicitly reviewed conversion, not a claim of universal JSONL support.
6. Keep logs and `/opt/ml/output/data/` limited to aggregate metrics, digests and
   reviewed metadata. Never emit sample text, IDs, originals, decoded generations,
   training examples, or raw failure payloads. Network isolation does not sanitize
   logs. Disable debug tensors and third-party trackers in the actual trainer.
7. Save only reviewed deployable artifacts in `/opt/ml/model/`; never copy the
   dataset there. Trained weights can memorize identifiers, so treat the artifact
   as sensitive and keep it encrypted and access-controlled. A hash mismatch,
   unsupported bundle, malformed model output or incompatible container must fail
   before training, without falling back to downloads or another model.

SageMaker stages S3 inputs and uploads outputs separately from the network-isolated
container, using its execution role. The container itself cannot fetch missing
dependencies or call AWS.

## Held-out evaluation and tuning gate

1. Curate approved train and **final held-out eval** splits, grouped by
   person/household/source/template where relevant. Keep paraphrases and numeric
   variants in the same group. Freeze exact hashes before measuring the baseline.
   If tuning needs a development set, split it from training; never use final eval
   for gradient updates, early stopping, prompt selection or checkpoint selection.
   Repeatedly using final eval to choose improvements turns it into development
   data; reserve a new untouched final holdout.
2. Run the actual private EKS baseline through the parent's inference/evaluation
   path, using the frozen examples. Record model artifact/revision, detector prompt
   version, processor and aggregate pass/block metrics. This preparation utility
   does not run those calls. The public fixtures only smoke-test preparation.
3. **Entity recall:** expand each gold `(type, original)` into all exact occurrences
   in source text and evaluate `(type, start, end)` matches. Recall is matched gold
   occurrences divided by all gold occurrences, reported overall and by type.
   Example: `김테스트` appearing twice is two gold occurrences, although JSONL labels
   it once. Wrong types, omitted occurrences and malformed/truncated model results
   are misses. A zero-gold denominator is `N/A`, not 100%.
4. Measure the model's raw detector recall separately from the complete gateway's
   removal coverage: supplemental rules must not hide a model miss. Also report
   precision/false positives and preservation of negative examples. Score only
   inside the trusted evaluation process; never publish originals or per-example
   records in reports.
5. **Numeric preservation:** check non-PII financial facts against the original
   text: amount/currency tokens (`300,000원`), rates (`3.5%`) and periods
   (`12개월`) must retain exact spelling, multiplicity and order. Exclude only
   manually approved gold identifier spans from this comparison; account numbers,
   phone numbers and dates of birth are not financial facts to retain. Do not
   whitelist numeric spans based on the model's own predictions. Include
   adversarial examples where it incorrectly labels an amount as `ACCOUNT`.
6. Report successful-output numeric preservation as unchanged-output count divided
   by successful-output count, **and report blocked/failed count over the entire
   fixed eval denominator**. A block protects the boundary but is not a successful
   detection or preserved answer; never silently drop failures. No successful
   outputs means preservation is `N/A`. Include negative examples with no PII.
7. Agree per-type recall/precision and availability targets before evaluation;
   require no financial-fact changes in successful outputs and no raw identifier
   escape. If the baseline passes, leave tuning unused. If it fails, document the
   deficiency and compare a separately approved candidate under the same frozen
   evaluation conditions, with sufficient per-type coverage.
8. Prepared, trained, evaluated, and approved-for-EKS are separate states. Successful
   local preparation proves none of the latter three. Promotion requires a real
   completed job, pinned output-artifact digest, reviewed license/container
   compatibility and held-out results before the parent registers an EKS model.

## Verification and AWS references

```bash
PYTHONPATH=platform python3 -m pytest -q platform/tests/test_privacy_training.py
```

Tests exercise malformed records and entities, split leakage independent of labels,
bounded parsing, pinning/encryption/network configuration, and the executable CLI
under Python `-S` (without third-party site packages).

Primary AWS references consulted through the documentation MCP:

- [CreateTrainingJob request fields and network isolation](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html)
- [TrainingImage digest format and File mode](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_AlgorithmSpecification.html)
- [ResourceConfig and the VolumeKmsKeyId local-storage restriction](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_ResourceConfig.html)
- [VpcConfig fields](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_VpcConfig.html)
- [Network isolation and service-managed S3 transfer](https://docs.aws.amazon.com/sagemaker/latest/dg/mkt-algo-model-internet-free.html)
- [SageMaker input and output storage paths](https://docs.aws.amazon.com/sagemaker/latest/dg/model-train-storage-env-var-summary.html)
