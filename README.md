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
| `cli/`, `services.yaml` | `preview env resolve\|up\|down\|reset\|test` and the service registry it reads |
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
| `task destroy` | Destroy every stack, then `task verify:clean` |
| `task local:up` / `task local:down` | kind cluster with Cilium, KEDA and `platform/` |
| `uv run preview env up ... --dry-run` | Print the Helm command for a preview environment |

## Deploy

Settings live in `deploy/environments/<name>.yaml`, keyed like CDK context: `domain` (required),
`natGateways` (1 or 2), `budgetEmail` and `acmeEmail` (default `platform@<domain>`),
`previewAllowlistCidrs`. `-c key=value` overrides the file. Run `task bootstrap`,
`task deploy ENV=<name>.yaml` and `task secrets:put`, then point the domain's NS records at the
new hosted zone.

## Measured timings

Not measured yet: fill this in from real runs against the deployed cluster. Every `preview`
command prints a `{"timings": ...}` JSON line and writes a stage table to the job summary.
Stages: `resolve` (feature group, GitHub lookups), `images` (ECR digests, dataset version),
`chart_dependencies`, `helm_upgrade` (scheduling, image pulls, database, migrations and readiness,
as `helm --wait` sees them), `e2e` (`helm test`), `teardown`.

| Command | Stage | p50 (s) | p90 (s) | Runs |
|---|---|---|---|---|
| up | resolve | | | |
| up | images | | | |
| up | chart_dependencies | | | |
| up | helm_upgrade | | | |
| up | total | | | |
| test | e2e | | | |
| down | teardown | | | |

| Section 1 target | Target p90 | Measured p90 |
|---|---|---|
| New preview environment, image already built (`up` total, new release) | under 60s | |
| Push to URL with branch code (push to end of the `up` job) | under 3 min | |
| Push to an existing preview environment (push to end of the `up` job) | under 2 min | |
| Teardown (`down` total) | under 60s | |

Push-to-URL times come from the Actions run (push event time to the `up` job's end). To fill the table from the last 20 preview runs of a service repo:

```sh
gh run list --repo prismatic-hq/tremor-api --workflow ci.yml --limit 20 --json databaseId \
  --jq '.[].databaseId' | xargs -I{} gh run view {} --repo prismatic-hq/tremor-api --log \
  | rg -o '\{"timings".*' | jq -rsf scripts/timings.jq
```
