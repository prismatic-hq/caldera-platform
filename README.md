# caldera-platform

Platform for Prismatic HQ preview environments. AWS CDK (Python) provisions EKS with Cilium and
Karpenter; each preview environment is a namespace plus a Helm release deployed from CI by the
`preview` CLI.

Related repos:
- [tremor-api](https://github.com/prismatic-hq/tremor-api): seismic signal streams and alerts
- [steward-api](https://github.com/prismatic-hq/steward-api): sites, crews and work orders

Docs: [REQUIREMENTS.md](docs/REQUIREMENTS.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md),
[GITHUB_APP.md](docs/GITHUB_APP.md), [CLUSTER_ACCESS.md](docs/CLUSTER_ACCESS.md) (kubectl access).

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

Requires Python 3 and Docker; `setup.py` installs mise and every other tool ([DEVELOPMENT.md](docs/DEVELOPMENT.md)).

```sh
python3 setup.py    # then, with mise on PATH: mise run test
```

## Key Commands

| Command | What it does |
|---|---|
| `mise run test` | pytest, helm unittest, ct lint, kubeconform, trivy config |
| `ENV=sandbox.yaml mise run synth` | `cdk synth` with cdk-nag `AwsSolutionsChecks` |
| `mise run bootstrap` | One-time `cdk bootstrap` of the account and region |
| `ENV=sandbox.yaml mise run deploy` | Deploy every stack with `deploy/environments/sandbox.yaml` |
| `mise run secrets:put -- --app-id ... --installation-id ... --private-key-file app.pem` | GitHub App credentials to SSM for the runners |
| `mise run destroy` | Destroy every stack, then `mise run verify:clean` |
| `mise run local:up` / `mise run local:down` | kind cluster with Cilium, KEDA and `platform/` |
| `uv run preview env up ... --dry-run` | Print the Helm command for a preview environment |

## Deploy

Settings live in `deploy/environments/<name>.yaml`, keyed like CDK context: `domain` (required),
`natGateways` (1 or 2), `budgetEmail` and `acmeEmail` (default `platform@<domain>`),
`previewAllowlistCidrs`. `-c key=value` overrides the file. Run `mise run bootstrap`,
`ENV=<name>.yaml mise run deploy` and `mise run secrets:put` ([GitHub App setup](docs/GITHUB_APP.md)),
then point the domain's NS records at the new hosted zone.
