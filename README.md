# caldera-platform

Platform for Prismatic HQ preview environments. AWS CDK (Python) provisions EKS with Cilium and
Karpenter; each preview environment is a namespace plus a Helm release deployed from CI by the
`preview` CLI.

Related repos:
- [tremor-api](https://github.com/prismatic-hq/tremor-api): seismic signal streams and alerts
- [steward-api](https://github.com/prismatic-hq/steward-api): sites, crews and work orders

Docs: [REQUIREMENTS.md](docs/REQUIREMENTS.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md),
[GITHUB_APP.md](docs/GITHUB_APP.md).

## Layout

| Path | Contents |
|---|---|
| `caldera/` | CDK stacks and cdk-nag suppressions (`nag_suppressions.py`) |
| `cli/`, `services.yaml` | `preview env resolve\|up\|down\|reset` and the service registry it reads |
| `charts/services/` | Helm chart for one preview environment |
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
| `task synth ENV=sandbox.yaml` | `cdk synth` with cdk-nag `AwsSolutionsChecks` |
| `task bootstrap` | One-time `cdk bootstrap` of the account and region |
| `task deploy ENV=sandbox.yaml` | Deploy every stack with `deploy/environments/sandbox.yaml` |
| `task secrets:put -- --app-id ... --installation-id ... --private-key-file app.pem` | GitHub App credentials to SSM for the runners |
| `task kube:connect` | Tunnel to the private EKS API and set kubectl context `caldera` |
| `task destroy` | Destroy every stack, then `task verify:clean` |
| `task local:up` / `task local:down` | kind cluster with Cilium, KEDA and `platform/` |
| `uv run preview env up ... --dry-run` | Print the Helm command for a preview environment |

## Deploy

Settings live in `deploy/environments/<name>.yaml`, keyed like CDK context: `domain` (required),
`natGateways` (1 or 2), `budgetEmail` and `acmeEmail` (default `platform@<domain>`),
`previewAllowlistCidrs`, `clusterAdminPrincipals`. `-c key=value` overrides the file. Run `task bootstrap`,
`task deploy ENV=<name>.yaml` and `task secrets:put` ([GitHub App setup](docs/GITHUB_APP.md)),
then point the domain's NS records at the new hosted zone.

## Cluster Access

The EKS endpoint is private. Any principal allowed to `sts:AssumeRole` on the `ClusterAdminRole`
output of `CalderaCluster` can connect: by default every IAM principal in the account whose own
policy allows it, or only the ARNs in `clusterAdminPrincipals`. Needs the AWS CLI and
`session-manager-plugin`. Run `task kube:connect` (tunnel stays open), then
`kubectl --context caldera get nodes` in a second terminal.
