#!/usr/bin/env bash
# Streams Cilium policy drops for traffic leaving the given namespace, via hubble-relay.
namespace="$1"
relay=$(kubectl --context caldera -n kube-system get svc hubble-relay -o jsonpath='{.spec.clusterIP}')
echo "hubble observe --verdict DROPPED --from-namespace $namespace --follow"
exec kubectl --context caldera -n kube-system exec ds/cilium -c cilium-agent -- \
  hubble observe --server "$relay:80" --verdict DROPPED --from-namespace "$namespace" --follow
