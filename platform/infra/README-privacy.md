# Private MyData infrastructure handoff

This is an additive stack and a manifest renderer. No deployment, image push,
commit, or merge was performed during the INFRA+RELAY check. The parent owns the
API, gateway, integration review, and deployment.

## Verified existing topology (read-only, 2026-09-12 UTC)

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

Matching ARM node addresses are `10.0.2.102`, `10.0.3.143`, `10.0.3.178`,
and `10.0.3.26`. `node-type=system` is amd64, so the former combination of
`system` and `arm64` matched no node. Both listed subnets disable public-IP
assignment and use the existing NAT route. The new relay's egress SG still
permits only TCP 8080 to its NLB SG.

Evidence was collected with `aws eks describe-cluster`, `aws ec2 describe-subnets`,
`describe-instances`, `describe-route-tables`, `describe-security-groups`, and
explicit-context `kubectl get` requests for nodes, the controller, CNI daemonset,
TGB CRD, and the existing model Deployment/Service/NetworkPolicies. No inference
request, model contents, secrets, or customer data were read.

## Deployment prerequisites

Do not deploy the gateway while either controller or policy prerequisite is
unresolved. The cluster owner must restore the LBC and webhook, and establish
working NetworkPolicy enforcement, including startup behavior. Enabling the
cluster's policy agent can affect existing workloads and belongs to that owner.
The new namespace policy is ineffective with the observed disabled agent; the
shared node SG alone does not isolate the gateway from existing cluster pods.
Privileged or host-network workloads remain part of the trusted cluster boundary.

The parent must also complete latest-HEAD AI review, required CI, merge, and
content review score >=85 before deployment. Offline assertions are not evidence
that these external gates passed. Keep the existing GPU service, node groups,
plane stack, routes, and data unchanged.

## Offline checks

Run from the repository root with the already installed dependencies:

```sh
python3 -m pytest -q platform/tests/test_privacy_relay.py platform/tests/test_privacy_deploy.py
git show HEAD:platform/infra/lib/stack.ts | node platform/privacy/deploy/check-infra.cjs
cd platform/infra
./node_modules/.bin/tsc --noEmit
```

The checker synthesizes the privacy stack without lookups or Docker builds. It
also compares the current main stack with HEAD in memory: privacy disabled must
be identical, and enabled may add only the WebSocket Lambda environment entry
and exact `lambda:InvokeFunction` permission. Results are in
`platform/infra/cdk.out/privacy-check/verified.json`. Existing main-stack runtime
and CDK deprecation warnings are outside this change.

Use these explicit context values for the future scoped stack operation:

```text
CDK_DEFAULT_ACCOUNT=180294183052
privacyVpcId=vpc-04e77172c67f19814
privacyVpcCidr=10.0.0.0/16
privacySubnetIds=subnet-0381e6c41375cbc53,subnet-037c396f41efedba8
privacyAvailabilityZones=ap-northeast-2a,ap-northeast-2b
privacyTargetSecurityGroupIds=sg-0143b07a17cc02c27
```

Reverify node labels, readiness, attached SGs, controller availability, and subnet
associations immediately before deployment. Scope CloudFormation changes to
`BankPlatformPrivacy`; review the separate API-stack change with its deployed
plane/graph contexts preserved. Do not deploy all stacks or substitute the old
`10.77.0.0/16` plane VPC.

## SG, IAM, and target binding contract

The relay has no Function URL or public HTTP route. Its IAM role can write only
its own retained log group and manage Lambda ENIs: creation references the exact
subnets/relay SG; delete/assign/unassign reference network interfaces in this VPC.
Describe actions require a wildcard resource and are region-conditioned. A
`lambda:SourceFunctionArn` deny prevents the handler itself from using those EC2
permissions. The Lambda depends on the attached policy before creation.

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

The main stack accepts the exact same-account Seoul relay ARN using
`mydataPrivacyFunctionArn`. Only the WebSocket API role receives the new
identity-policy grant. Existing account-level administrative permissions are not
rewritten. No new exports, GPU resources, VPCs, or peering are introduced.

## Image and manifest handoff after the gates pass

Build the existing `platform/privacy` Docker context for `linux/arm64`. Its
entrypoint is `python -m privacy.gateway.server`, port 8080, UID/GID 10001. Resolve
an immutable digest in the stack's `PrivacyGatewayRepositoryUri`; the renderer
refuses tags, a different repository, account/region mismatch, and invalid CIDRs.
Record the actual Qwen artifact revision separately from the gateway image.
`unverified` is an explicit unknown revision, not an artifact-pinning claim.

Save the future privacy stack outputs to `privacy-outputs.json`. Obtain **all**
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
blocking through the authenticated parent API. A passing `/health` alone does
not prove Qwen readiness. Regenerate the policy after NLB replacement or subnet
changes. Never log or retain raw invocation payloads/errors.

The relay requires `processor=eks-sllm`, `status=pass`, `method=redaction`, matching
request model, nonempty model/revision/prompt identifiers, integer counts with
`sum(entityCounts)=total`, matching character lengths, zero residuals, an allowed
independent-NER status, and finite nonnegative latency. Zero detections must
preserve text; new `⟨TYPE:random8hex⟩` occurrences must match the per-type counts,
and existing markers must survive. Optional `modelDetections` counts candidate spans before dedup;
`ruleSupplements` counts chosen rule-only spans. PASSPORT is supported. Unknown
receipt fields and all raw errors are excluded; malformed mandatory evidence
blocks the text. There is no rule-only fallback or readiness rewrite.

References used for the infrastructure review:

- AWS Lambda VPC permissions: https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html
- AWS EC2 action/resource/condition reference: https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json
- NLB security groups: https://docs.aws.amazon.com/elasticloadbalancing/latest/network/load-balancer-security-groups.html
- Installed LBC TGB contract: https://github.com/kubernetes-sigs/aws-load-balancer-controller/blob/v3.2.1/docs/guide/targetgroupbinding/targetgroupbinding.md
