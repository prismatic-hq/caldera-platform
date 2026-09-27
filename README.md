# caldera-platform

Platform for Prismatic HQ preview environments. AWS CDK (Python) provisions EKS with Cilium and
Karpenter; each preview environment is a namespace plus a Helm release deployed from CI by the
`preview` CLI.

Related repos:
- [tremor-api](https://github.com/prismatic-hq/tremor-api): seismic signal streams and alerts
- [steward-api](https://github.com/prismatic-hq/steward-api): sites, crews and work orders

Docs: [REQUIREMENTS.md](docs/REQUIREMENTS.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Layout

| Path | Contents |
|---|---|
| `caldera/` | CDK stacks and cdk-nag suppressions (`nag_suppressions.py`) |
| `cli/`, `services.yaml` | `preview env resolve\|up\|down\|reset` and the service registry it reads |
| `charts/preview-environment/` | Helm chart for one preview environment |
| `platform/` | PriorityClasses, headroom Deployment, KEDA `ScaledObject` |
| `seeder/`, `images/golden-db/` | Golden dataset and golden DB image |
| `contracts/events/` | CloudEvents envelope, registry, data schemas and examples |
| `e2e/`, `local/`, `scripts/` | E2E suite, kind + Tilt, `verify_clean.py` |

## Quick Start

Requires `uv`, `task`, Docker, Helm and Node.js.

```sh
task init && task test
```

## Key Commands

| Command | What it does |
|---|---|
| `task test` | pytest, helm unittest, ct lint, kubeconform, trivy config |
| `task synth` | `cdk synth` with cdk-nag `AwsSolutionsChecks` |
| `task local:up` / `task local:down` | kind cluster with Cilium, KEDA and `platform/` |
| `task verify:clean` | Fail if AWS resources remain after `cdk destroy` |
| `uv run preview env up ... --dry-run` | Print the Helm command for a preview environment |
