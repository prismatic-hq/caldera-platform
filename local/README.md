# local

kind cluster with the default CNI and kube-proxy disabled, Cilium installed, and `platform/`
applied, so network policies behave as on EKS (FR-10.1). Not run in CI.

```sh
task local:up
uv run caldera vent up --context kind-caldera --repo tremor-api --branch feature/x --sha <sha> \
  --other-missing-branch --other-sha <sha>
task local:down
```

`Tiltfile` is a stub for the inner loop (FR-10.3).
