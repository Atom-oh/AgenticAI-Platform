# Pinned Qwen artifacts and prefetch identity

This is the bootstrap dependency for the shared FSI cluster's Qwen workload.
It creates no GPU, VPC, endpoint, bucket or application route. The workload
source lives in `Atom-oh/aws-fsi-demo`; coordinate that PR with these resources.
This document describes the declared rollout design. Check the deployed
revision and CNI probe evidence separately; policy files alone do not establish
live network blocking. The captured prerequisite baseline had CNI disabled.

## Artifact publication

`artifact-manifest.json` pins 21 public model files (17,916,636,744 bytes) by
revision, size and SHA-256. The target is Qwen3-8B revision
`b968826d9c46dd6066d109eabc6255188de91218`; the draft is Qwen3-0.6B revision
`c1899de289a04d12100db370d81485cdf75e47ca`. Hashes came from pinned upstream LFS
metadata or downloaded metadata checked against its Git blob identity. They
are not a claim that an earlier, deleted Pod's weights were measured.

The publisher's default mode reads the manifest and prints an offline plan:

```bash
python3 stage_artifacts.py
```

After reviewing the manifest, script, account and private bucket, publish:

```bash
python3 stage_artifacts.py --publish
```

The trusted operator host uses its normal AWS SDK credential chain. Credentials
are never printed or written by this script. The caller must be in account
`180294183052`; every S3 request checks the expected bucket owner.
Check that all four S3 Block Public Access settings are enabled beforehand.

Each file is downloaded through HTTPS into the private, owned directory
`/home/atomoh/.cache/mydata-qwen-artifacts`. No file exceeds 4GB, and only one is
staged at a time. Size/hash mismatch stops before upload. Conditional
`PutObject` requires the destination to be absent; an existing object is accepted
only if its full-object S3 checksum, size and encryption match. A checksum stored
only in object metadata is insufficient.

After S3 validates the uploaded SHA-256 and a checksum-enabled HEAD confirms
the stored file, only this process's own temporary file is removed. A failure
retains that file and stops further staging; inspect it before removing it or
retrying. The script never replaces conflicting model objects. It downloads
public weights only and has no personal-data input.

## Read-only bootstrap role

`artifact-reader.template.json` is a standalone CloudFormation stack input.
Its named role is required by the external GitOps workload. Existing topology
identifiers are deliberately fixed to the verified Seoul FSI cluster; this is
not a cross-account installer.

The trust binds one OIDC provider, the STS audience and the
`sllm/qwen-artifacts` ServiceAccount. The only permission is `s3:GetObject` on
the exact 21 keys, requiring TLS and existing gateway endpoint
`vpce-04a82e15d312f39b8`. It grants no write, list or unrelated service
permissions. GetObject also permits metadata reads for those keys; EC2 IMDS is
restricted separately by workload configuration. No existing bucket policy,
endpoint policy or node role is changed.

Deploy only after latest-HEAD code/content review and CI pass. Validate the
template, review a **CREATE** change set for `FsiQwenArtifacts`, confirm it
contains only this role, then execute it. Read back its trust/permissions before
merging the dependent FSI workload PR. Do not deploy the unrelated FSI CDK
stacks as part of this step.

## Runtime verification

Only the separate CPU prefetch Job can assume this role. It downloads exact
keys and rechecks every byte count and SHA-256 onto a model-only PVC. The
serving Pod is configured without a projected AWS token or declared S3 egress
allowance. Network blocking requires active CNI enforcement. Its local
verification init and vLLM mount that volume read-only. Keep the API model name
`Qwen/Qwen3-8B` unchanged. GitOps must complete the prefetch hook before updating
the existing serving Deployment; a failed hook leaves that Deployment running.

Publication and a successful IAM stack do not prove runtime authorization.
Require the prefetch Job and local verification init to complete, verify real
Qwen readiness/inference, then check the producer's allowed S3/STS/DNS and the
serving Pod's denied S3/public/metadata paths after CNI enforcement. The
producer's regional S3 network allowance is not a bucket-level filter; the Job
accepts no inference requests, mounts no personal-data store and has only
exact-object read permissions. Its labels must never match the inference Service.

Run the offline publisher/IAM contract suite from the repository root:

```bash
python3 -m pytest -q platform/tests/test_qwen_artifact_publisher.py
```

Sources:

- [Pinned Qwen3-8B files](https://huggingface.co/Qwen/Qwen3-8B/tree/b968826d9c46dd6066d109eabc6255188de91218)
- [Pinned Qwen3-0.6B files](https://huggingface.co/Qwen/Qwen3-0.6B/tree/c1899de289a04d12100db370d81485cdf75e47ca)
- [IAM roles for EKS ServiceAccounts](https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html)
- [S3 upload integrity](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html)
