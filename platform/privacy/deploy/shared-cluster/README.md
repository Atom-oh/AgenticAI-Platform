# Shared EKS prerequisites for MyData

Scope: `fsi-demo-cluster` in Seoul. Shared-cluster maintenance was authorized on
2026-09-13. These files are operational inputs, not a claim that application
deployment or every shared workload has passed verification.

## Load balancer controller

The installed controller is v3.2.1. Its ALB Gateway controller watches
`ListenerSet` v1, but the cluster was missing that CRD, causing repeated cache
sync failures. The cluster also has an Istio waypoint Gateway, so disabling
Gateway features is not the selected repair.

`listenersets-v1.5.0.yaml` is the single unmodified official CRD; its source and
checksum are in `sources.json`. Create it only when absent. Do not apply a whole
Gateway API bundle or replace an existing CRD. This is not a full Gateway API
version upgrade.

```bash
KUBE_CONTEXT=arn:aws:eks:ap-northeast-2:180294183052:cluster/fsi-demo-cluster
kubectl --context "$KUBE_CONTEXT" get crd listenersets.gateway.networking.k8s.io
# When absent, from this directory:
kubectl --context "$KUBE_CONTEXT" create --dry-run=server -f listenersets-v1.5.0.yaml
kubectl --context "$KUBE_CONTEXT" create -f listenersets-v1.5.0.yaml
kubectl --context "$KUBE_CONTEXT" wait --for=condition=Established \
  crd/listenersets.gateway.networking.k8s.io --timeout=60s
kubectl --context "$KUBE_CONTEXT" -n kube-system rollout status \
  deployment/aws-load-balancer-controller --timeout=600s
```

Verify both controller replicas, stable restart counts, ready webhook endpoints,
and unchanged Istio Gateway specifications. Removing the CRD restores the known
failure and can delete ListenerSet objects; it is not an automatic rollback.

## Network policy verification

The installed managed VPC CNI add-on is v1.21.1-eksbuild.8. Enabling
`enableNetworkPolicy` is cluster-wide. Keep standard mode and preserve existing
configuration, policies, versions, and unrelated settings.

Before activation, verify actual Argo CD, runner, OCR and Qwen identities'
required connections. Qwen needs a proven offline restart path, including model
artifacts and credentials. A warm inference request does not prove that path.
Resolve these dependencies before applying the one-key add-on update.

`network-policy-canary.yaml` creates three small CPU-only Deployments, a Service,
and three policies in the existing `bank-platform-mydata` namespace. It uses unique
labels, a digest-pinned image, no credentials, no GPU and no persistent volume.
It does not impersonate production Service labels.

```bash
kubectl --context "$KUBE_CONTEXT" create --dry-run=server -f network-policy-canary.yaml
kubectl --context "$KUBE_CONTEXT" create -f network-policy-canary.yaml
python3 verify_canary.py --expect disabled
# After the separately reviewed managed add-on update and rollout:
python3 verify_canary.py --expect enabled
```

The disabled baseline expects all three paths to connect. The enabled check
expects allowed ingress to connect and both negative paths to time out. Each
client must resolve the current Service IP and prove its own positive connection
before and after the negative checks. The denied client uses port 18081 as its
positive control; the allowed client remains denied on that port. A separate
loopback check proves the second listener is running. DNS, routing, refusal and
probe execution errors fail verification. Repeat on affected nodes and availability zones;
one pair is not evidence of cluster-wide coverage.

Standard mode has a startup allow window. The MyData HTTP server separately
checks the TCP peer against verified NLB addresses before accepting input.
The application check does not provide strict-mode egress or host isolation.

Restoring the add-on flag is not proven to detach existing eBPF filters. Check
the actual dataplane before declaring rollback complete. Do not mass-restart
shared pods or delete policies to obtain a passing result. Remove only the
canary resources when finished; retain the namespace and application resources.

References:

- [AWS VPC CNI configuration](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html)
- [AWS policy considerations](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy.html)
- [LBC v3.2.1 Gateway guide](https://github.com/kubernetes-sigs/aws-load-balancer-controller/blob/v3.2.1/docs/guide/gateway/gateway.md)
