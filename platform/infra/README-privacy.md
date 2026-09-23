# Private MyData infrastructure

Current code audit: 2026-09-13. This runbook covers `lib/privacy-stack.ts`,
`../privacy/deploy/render.py`, and `../privacy/relay/handler.py`. Follow
[root instructions](../../AGENTS.md), [review context](../../docs/REVIEW_CONTEXT.md),
[SPEC.md](../../SPEC.md), and [platform contracts](../docs/CONTRACTS.md).
Deployment observations below are dated evidence, not current readiness claims.

Shared-cluster maintenance was authorized on 2026-09-13. PR #4 adds startup
TCP-peer enforcement and shared-cluster prerequisite inputs. See the
[shared-cluster runbook](../privacy/deploy/shared-cluster/README.md). Authorization
and merged code do not establish successful deployment.

## Current code

`bin/app.ts` instantiates the additive `BankPlatformPrivacy` stack only when
`privacyVpcId` is supplied. It imports existing VPC/subnet metadata and creates an
internal NLB, target group, relay Lambda, ECR repository and scoped SG/IAM rules.
It does not create an EKS cluster, GPU model, VPC or peering connection.

The request path is authenticated WebSocket S2 → IAM relay → internal NLB →
CPU gateway → private model service. `api/handlers/s2.py` (under `platform/`)
requires this path for the free-text question before cloud Guardrails.
Structured lookup fields instead use internal deterministic tokenization and an
independent scan. Selecting the `gemma` explanation adapter does not replace EKS
processing. Missing relay configuration or invalid privacy evidence blocks S2.

The gateway defaults to `qwen` / `Qwen/Qwen3-8B`. Private `gemma4` and `deepseek`
are disabled placeholders unless configured. `PRIVACY_MODELS_JSON` can supply
reviewed private endpoints; the checked-in manifest uses the Qwen baseline.
`/models` verifies the configured ID against the model service. The current
gateway reports `independentNer="not-configured"`; do not claim a deployed second
NER model. Source: `../privacy/gateway/models.py` and `../privacy/gateway/engine.py`.

## Historical topology inspection (2026-09-12 UTC)

The following values were recorded by the earlier read-only inspection. They
are useful deployment inputs only after revalidation; this audit did not query AWS.

| Setting | Observed value |
| --- | --- |
| Account / region | `180294183052` / `ap-northeast-2` |
| EKS cluster | `fsi-demo-cluster` |
| VPC / CIDR | `vpc-04e77172c67f19814` / `10.0.0.0/16` |
| Private subnet / AZ | `subnet-0381e6c41375cbc53` / `ap-northeast-2a` / `10.0.2.0/24` |
| Private subnet / AZ | `subnet-037c396f41efedba8` / `ap-northeast-2b` / `10.0.3.0/24` |
| Gateway CPU selector | `node-type=graviton,kubernetes.io/arch=arm64` |
| CPU node SG | `sg-0143b07a17cc02c27` |
| Matching CPU nodes | Four Ready `m6g.large` nodes, no taints |
| Existing model | `sllm/qwen-sllm-gpu`, selector `app=qwen-sllm`, one ready replica |
| Model service | ClusterIP `qwen-sllm-svc.sllm:8080`, target port `http` |
| TGB CRD | `targetgroupbindings.elbv2.k8s.aws`, served/stored `v1beta1` |
| LBC | `v3.2.1`, **0/2 ready; both replicas CrashLoopBackOff** |
| VPC CNI policy agent | Present, **`--enable-network-policy=false`** |

Four ARM nodes matched the selector at that historical check. Keep individual
node addresses in restricted operational evidence, not the maintained runbook. `node-type=system` is amd64, so the former combination of
`system` and `arm64` matched no node. Both listed subnets disable public-IP
assignment and use the existing NAT route. The new relay's egress SG still
permits only TCP 8080 to its NLB SG.

Evidence was collected with `aws eks describe-cluster`, `aws ec2 describe-subnets`,
`describe-instances`, `describe-route-tables`, `describe-security-groups`, and
explicit-context `kubectl get` requests for nodes, the controller, CNI daemonset,
TGB CRD, and the existing model Deployment/Service/NetworkPolicies. No inference
request, model contents, secrets, or customer data were read.

## Deployment prerequisites to recheck

Verify the LBC/webhook and NetworkPolicy enforcement before applying the gateway.
If either is still unhealthy, the cluster owner must restore it and establish
working NetworkPolicy enforcement, including startup behavior. Enabling the
cluster's policy agent can affect existing workloads and belongs to that owner.
The new namespace policy is ineffective with the observed disabled agent; the
shared node SG alone does not isolate the gateway from existing cluster pods.
Privileged or host-network workloads remain part of the trusted cluster boundary.

Use the current repository review and deployment policy; the old handoff's
content-score threshold is not a runtime or universal PR requirement. Offline
assertions do not prove deployment readiness. Scope gateway operations to the
additive resources; changes to shared GPU services, node groups, routes or
cluster policy enforcement need their own explicit operational scope.

## Offline checks

Run from the repository root with the already installed dependencies:

```sh
python3 -m pytest -q platform/tests/test_privacy_http.py platform/tests/test_privacy_relay.py platform/tests/test_privacy_deploy.py platform/tests/test_privacy_canary.py
git show HEAD:platform/infra/lib/stack.ts | node platform/privacy/deploy/check-infra.cjs
cd platform/infra
./node_modules/.bin/tsc --noEmit
```

The checker synthesizes the privacy stack without lookups or Docker builds.
The command above uses HEAD as the baseline main-stack source; CI supplies the
PR base instead. At both the baseline and current revision, enabling privacy may
add only the WebSocket and Workspace API environment entries and exact
`lambda:InvokeFunction` permissions. Each enabled template is compared with its
own revision's disabled template. Baseline source modules are loaded from the
named `PR_BASE_SHA` (default HEAD), including all relative
TypeScript dependencies such as `workspace.ts`; stdin must match that revision.
The installed package/compiler configuration must have identical baseline locks,
otherwise the check fails and requires an independent dependency installation.
Both synths use the same explicitly recorded asset fixture; they do not certify
runtime code or deployed artifacts. Other main-stack changes must exactly match
the base and resource inventory in `privacy/deploy/reviewed-main-delta.json`.
An unlisted resource, omitted expected change or different base fails the check.
The manifest and each resource's complete diff still require current AI review
and the bank IAM isolation test; they are not privacy-toggle effects. Results are in
`platform/infra/cdk.out/privacy-check/verified.json`. Existing main-stack runtime
and CDK deprecation warnings are outside this change.

The historical inspection supplied these context values; verify them before a
scoped stack operation:

```text
CDK_DEFAULT_ACCOUNT=180294183052
privacyVpcId=vpc-04e77172c67f19814
privacyVpcCidr=10.0.0.0/16
privacySubnetIds=subnet-0381e6c41375cbc53,subnet-037c396f41efedba8
privacySubnetRouteTableIds=rtb-0d3f6ff29f519ebe1,rtb-075c2e281c3624165
privacyAvailabilityZones=ap-northeast-2a,ap-northeast-2b
privacyTargetSecurityGroupIds=sg-0143b07a17cc02c27
```

Reverify node labels, readiness, attached SGs, controller availability, and subnet
associations immediately before deployment. Scope CloudFormation changes to
`BankPlatformPrivacy`; review the separate API-stack change with its deployed
plane/graph contexts preserved. Do not deploy all stacks or substitute the old
`10.77.0.0/16` plane VPC.

`platform/deploy.sh` does not forward `privacyVpcId` or
`mydataPrivacyFunctionArn`. Supply reviewed contexts through the CDK operation or
its context configuration; do not assume the generic script preserves an
already enabled relay grant. Its default main-stack name is `BankPlatformCore`,
while direct `bin/app.ts` defaults to `BankPlatform`.

## SG, IAM, and target binding contract

The relay has no Function URL or public HTTP route. Its IAM role can write only
its own retained log group and grants Lambda the six documented VPC ENI
operations on all resources, constrained to Seoul. The first deployment failed
Lambda's `DeleteNetworkInterface` preflight with resource/VPC-scoped grants.
The supported grants are therefore not described as VPC-scoped IAM permissions.
A `lambda:SourceFunctionArn` deny prevents the handler itself from using those
EC2 permissions. The function's VPC configuration fixes its two subnets and relay
SG; the Lambda depends on the attached policy before creation.

The NLB is internal, TCP 8080, with only relay-SG ingress. Relay egress references
that NLB SG; NLB egress and its one additive existing-node-SG ingress rule use
TCP 8080 for both target traffic and `/health` probes. The target group uses IP
targets and `preserve_client_ip.enabled=false`. The manifest's ClusterIP Service
and explicit TGB share port 8080 and VPC ID. TGB has no `networking` block: CDK
owns the new SG rules. Its existing controller needs register/deregister and
describe permissions for the new target group.

The new gateway policy allows inbound TCP 8080 only from the NLB ENIs' `/32`
addresses, and outbound only to `sllm/app=qwen-sllm:8080` and cluster DNS. Verify
enforcement and a denied direct-pod request before processing anything beyond
synthetic inputs. HTTP stays inside the existing VPC; this change does not add
application TLS or claim full cluster/host isolation.

VPC CNI standard mode permits traffic while a new pod's policies are being
programmed. The gateway therefore checks the actual TCP peer against the same
verified NLB addresses in `PRIVACY_ALLOWED_CLIENT_IPS` before reading a request
body or invoking a model. Missing or invalid configuration denies processing;
forwarding headers cannot override this check. `/health` remains available for
node and NLB probes and exposes no model or input data. This is an additional
application boundary, not a claim of strict-mode or host-level isolation.

The main stack accepts the exact same-account Seoul relay ARN using
`mydataPrivacyFunctionArn`. Only the WebSocket and Workspace API roles receive
the consumer identity-policy grant. Existing account-level administrative permissions are not
rewritten. No new exports, GPU resources, VPCs, or peering are introduced.

## Image and manifest procedure

Build the existing `platform/privacy` Docker context for `linux/arm64`. Its
entrypoint is `python -m privacy.gateway.server`, port 8080, UID/GID 10001. Resolve
an immutable digest in the stack's `PrivacyGatewayRepositoryUri`; the renderer
refuses tags, a different repository, account/region mismatch, and invalid CIDRs.
Record the actual Qwen artifact revision separately from the gateway image.
`unverified` is an explicit unknown revision, not an artifact-pinning claim.

Save the privacy stack outputs to `privacy-outputs.json`. Obtain **all**
NLB ENI private addresses using its output `PrivacyNlbArn`; DNS alone can omit
an unhealthy AZ. These are read-only commands after that NLB exists:

```sh
PRIVACY_NLB_ARN=$(jq -r '.BankPlatformPrivacy.PrivacyNlbArn' privacy-outputs.json)
aws ec2 describe-network-interfaces --region ap-northeast-2 \
  --filters "Name=description,Values=ELB ${PRIVACY_NLB_ARN##*:loadbalancer/}" \
            "Name=vpc-id,Values=vpc-04e77172c67f19814" \
  --query 'NetworkInterfaces[].{Subnet:SubnetId,Address:PrivateIpAddress}' --output json
```

Check that the addresses belong to both expected subnets. Render offline with
the actual verified addresses (never copy synthetic fixture IPs):

```sh
python3 platform/privacy/deploy/render.py \
  --outputs privacy-outputs.json \
  --image "$PRIVACY_IMAGE_DIGEST" \
  --model-revision "$QWEN_ARTIFACT_REVISION" \
  --nlb-addresses "$PRIVACY_NLB_IP_A" "$PRIVACY_NLB_IP_B" \
  > privacy-gateway.rendered.json
```

Only resources in `bank-platform-mydata` are emitted. Apply only that rendered
manifest once prerequisites pass, then check TGB registration, healthy targets,
gateway `/models` readiness, synthetic `/deidentify` evidence, and failure
blocking through the authenticated S2 API. A passing `/health` alone does
not prove Qwen readiness. Regenerate the policy after NLB replacement or subnet
changes. Never log or retain raw invocation payloads/errors.

The relay requires `processor=eks-sllm`, `status=pass`, `method=redaction`, matching
request model, nonempty model/revision/prompt identifiers, integer counts with
`sum(entityCounts)=total`, matching character lengths, zero residuals, an allowed
independent-NER status, and finite nonnegative latency. Zero detections must
preserve text; new `⟨TYPE:nonce⟩` occurrences must match the per-type counts,
and existing markers must survive. Current generated nonces encode 128 random bits
as 32 nonnumeric characters from `a` through `p` (`[a-p]{32}`). Legacy 8- and
32-hex markers remain accepted by the parser; they are not the current generated form. Optional `modelDetections` counts candidate spans before dedup;
`ruleSupplements` counts chosen rule-only spans. PASSPORT is supported. Unknown
receipt fields and all raw errors are excluded; malformed mandatory evidence
blocks the text. There is no rule-only fallback or readiness rewrite.

The gateway detector accepts validated JSON entities or compact `TYPE<TAB>original`
rows; the current prompt requests compact rows and `NONE` for no identifiers.
Redaction preserves financial facts and replaces exact spans with random tokens;
malformed output blocks rather than becoming a rule-only success. The relay
forwards only allowed receipt fields. The API narrows that receipt again before
publishing events. Optional training preparation is described in the
[training contract](../privacy/training/README.md); it does not train or deploy models.

References retained from the earlier infrastructure review:

- AWS Lambda VPC permissions: https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html
- AWS EC2 action/resource/condition reference: https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json
- NLB security groups: https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-security-groups.html
- Installed LBC TGB contract: https://github.com/kubernetes-sigs/aws-load-balancer-controller/blob/v3.2.1/docs/guide/targetgroupbinding/targetgroupbinding.md
