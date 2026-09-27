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
| `cdk/` | CDK stacks, constructs, Lambda handlers and cdk-nag suppressions |
| `cli/`, `services.yaml` | `preview env resolve\|up\|down\|reset\|test` and the service registry it reads |
| `charts/service/` | Helm chart for one HTTP service: Deployment, Service, optional HTTPRoute |
| `charts/services/` | Umbrella chart that allows a consumer to easily deploy all services for an environment. Works for ephemeral/preview environments, stable environments, etc. |
| `platform/` | PriorityClasses, headroom Deployment, KEDA `ScaledObject` |
| `seeder/`, `images/golden-db/` | Golden dataset and golden DB image |
| `contracts/events/` | CloudEvents envelope, registry, data schemas and examples |
| `e2e/`, `local/` | E2E suite, kind + Tilt |
| `scripts/` | Helpers behind the mise tasks and the golden-image workflow |
| `.github/workflows/` | CI, preview environment up/teardown, golden image |

## Quick Start

Requires Python 3, Docker and the 1Password desktop app with **Settings > Developer > Integrate
with 1Password CLI** on. `setup.py` installs mise, the pinned tools, Python dependencies and git
hooks ([DEVELOPMENT.md](docs/DEVELOPMENT.md)).

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
| `ENV=sandbox.yaml mise run dns:nameservers` | Print the hosted zone's Route 53 nameservers for the registrar |
| `mise run secrets:put` | GitHub App credentials to SSM for the runners, read from 1Password |
| `mise run secrets:github -- --region us-east-2` | GitHub App secrets and `AWS_REGION` on every repo, read from 1Password |
| `mise run secrets:role-arns` | `AWS_ROLE_ARN` on each service repo, from the `CalderaCiAccess` push role outputs |
| `mise run kube:connect` | Tunnel to the private EKS API and `kubectx caldera` |
| `mise run destroy` | Destroy every stack, then `mise run verify:clean` |
| `mise run local:up` / `mise run local:down` | kind cluster with Cilium, KEDA and `platform/` |
| `uv run preview env up ... --dry-run` | Print the Helm command for a preview environment |
| `uv run preview env test --name <env>` | Run the E2E Helm test hook; CI reports it as the `e2e` check |

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
3. `ENV=<name>.yaml mise run dns:nameservers`, then set those as the domain's NS records at its registrar.
4. `mise run secrets:put` after every fresh deploy ([GITHUB_APP.md](docs/GITHUB_APP.md)).
5. `mise run secrets:role-arns` after every deploy that creates or replaces the CI push roles, so the service repos get `AWS_ROLE_ARN`.
6. `mise run kube:connect` for `kubectl` access.

## Measured timings

Measured 2026-09-27 on the dev cluster, with self-hosted runners, from the last 20 tremor-api and
steward-api `ci` runs and their last 20 `preview-teardown` runs. A new release is an `up` whose
`helm upgrade` reports `REVISION: 1`; only 3 runs were new releases. Every `preview` command prints a
`{"timings": ...}` JSON line and writes a stage table to the job summary. Stages: `resolve`
(feature group, GitHub lookups), `lock`, `heads`, `namespace`, `images` (ECR digests, dataset
version), `recover` (only when a release needs recovery), `chart_dependencies`, `helm_upgrade`
(scheduling, image pulls, database, migrations and readiness, as `helm --wait` sees them), `e2e`
(`helm test`), `teardown`.

| Command | Stage | p50 (s) | p90 (s) | Runs |
|---|---|---|---|---|
| up | resolve | 0.5 | 2.2 | 41 |
| up | lock | 0.2 | 0.4 | 37 |
| up | heads | 0.6 | 1.1 | 37 |
| up | namespace | 0.3 | 0.5 | 41 |
| up | images | 1.0 | 1.4 | 41 |
| up | recover | 0.8 | 6.0 | 6 |
| up | chart_dependencies | 0.1 | 0.5 | 41 |
| up | helm_upgrade | 21.8 | 39.6 | 41 |
| up | total | 25.8 | 44.3 | 41 |
| test | e2e | 8.6 | 15.6 | 32 |
| down | lock | 0.5 | 1.3 | 6 |
| down | teardown | 14.8 | 17.3 | 10 |
| down | total | 16.3 | 17.7 | 10 |

| Section 1 target | Target p90 | Measured p90 | Result |
|---|---|---|---|
| New preview environment, image already built (`up` total, new release) | under 60s | 28.6s (3 runs) | met |
| Push to URL with branch code (push to end of the `up` job, new release) | under 3 min | 2m58s (3 runs) | met |
| Push to an existing preview environment (push to end of the `up` job) | under 2 min | 4m16s (25 runs) | missed |
| Teardown (`down` total) | under 60s | 17.7s (10 runs) | met |

The existing-environment miss comes from the pipeline, not `up` (p90 44.3s): `preview / up` starts
a p50 of 78s and a p90 of 3m09s after the push, behind `build`, `preview / resolve` and runner
pickup. Two runs waited about 10 min for a runner.

Push-to-URL times come from the Actions run (push event time to the `up` job's end). To fill the table from the last 20 preview runs of a service repo:

```sh
gh run list --repo prismatic-hq/tremor-api --workflow ci.yml --limit 20 --json databaseId \
  --jq '.[].databaseId' | xargs -I{} gh run view {} --repo prismatic-hq/tremor-api --log \
  | rg -o '\{"timings".*' | jq -rsf scripts/timings.jq
```
