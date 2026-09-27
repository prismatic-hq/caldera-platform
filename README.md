# caldera-platform

Platform for Prismatic HQ preview environments. AWS CDK (Python) provisions EKS with Cilium and
Karpenter; each preview environment is a namespace plus a Helm release deployed from CI by the
`preview` CLI.

Related repos:
- [tremor-api](https://github.com/prismatic-hq/tremor-api): seismic signal streams and alerts
- [steward-api](https://github.com/prismatic-hq/steward-api): sites, crews and work orders

Docs:
- [REQUIREMENTS.md](docs/REQUIREMENTS.md) and [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [DEVELOPMENT.md](docs/DEVELOPMENT.md): mise tools and tasks
- [GITHUB_APP.md](docs/GITHUB_APP.md): GitHub App for runners and service checkouts
- [CLUSTER_ACCESS.md](docs/CLUSTER_ACCESS.md): `kubectl` access to the private EKS API

## Layout

| Path | Contents |
|---|---|
| `caldera/` | CDK stacks, constructs, Lambda handlers and cdk-nag suppressions |
| `cli/`, `services.yaml` | `preview env resolve\|up\|down\|reset\|test` and the service registry it reads |
| `charts/services/` | Helm chart for one preview environment |
| `platform/` | PriorityClasses, headroom Deployment, KEDA `ScaledObject` |
| `seeder/`, `images/golden-db/` | Golden dataset and golden DB image |
| `contracts/events/` | CloudEvents envelope, registry, data schemas and examples |
| `e2e/`, `local/` | E2E suite, kind + Tilt |
| `scripts/` | Helpers behind the mise tasks and the golden-image workflow |
| `.github/workflows/` | CI, preview environment up/teardown, golden image |

## Quick Start

Requires Python 3 and Docker. `setup.py` installs mise, the pinned tools, Python dependencies and git hooks.

```sh
python3 setup.py    # then, with mise on PATH: mise run test
```

## Key Commands

| Command | What it does |
|---|---|
| `mise run init` | `uv sync --locked` and pre-commit hooks |
| `mise run test` | pytest, helm unittest, ct lint, kubeconform, trivy config |
| `ENV=sandbox.yaml mise run synth` | `cdk synth` with cdk-nag `AwsSolutionsChecks` |
| `mise run bootstrap` | One-time `cdk bootstrap` of the account and region |
| `ENV=sandbox.yaml mise run deploy` | Deploy every stack with `deploy/environments/sandbox.yaml` |
| `mise run secrets:put -- --app-id ... --installation-id ... --private-key-file app.pem` | GitHub App credentials to SSM for the runners |
| `mise run kube:connect` | Tunnel to the private EKS API, kubectl context `caldera` |
| `mise run destroy` | Destroy every stack, then `mise run verify:clean` |
| `mise run local:up` / `mise run local:down` | kind cluster with Cilium, KEDA and `platform/` |
| `uv run preview env up ... --dry-run` | Print the Helm command for a preview environment |

## Deploy

Settings live in `deploy/environments/<name>.yaml`, keyed like CDK context. `-c key=value` overrides the file.

| Key | Default |
|---|---|
| `domain` | Required |
| `natGateways` | `1` (1 or 2) |
| `budgetEmail`, `acmeEmail` | `platform@<domain>` |
| `previewAllowlistCidrs` | Empty |
| `clusterAdminPrincipals` | Account root ([CLUSTER_ACCESS.md](docs/CLUSTER_ACCESS.md)) |

Steps:

1. `mise run bootstrap` (once per account and region).
2. `ENV=<name>.yaml mise run deploy`.
3. Point the domain's NS records at the new hosted zone.
4. `mise run secrets:put -- ...` after every fresh deploy ([GITHUB_APP.md](docs/GITHUB_APP.md)).
5. `mise run kube:connect` for `kubectl` access.
