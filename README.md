# caldera-platform

Platform for Prismatic HQ preview environments ("vents"). AWS CDK (Python) provisions the AWS
foundations and hands off to Argo CD through the GitOps Bridge; Argo CD runs everything in-cluster.

Related repos:
- [applications-infra](https://github.com/prismatic-hq/applications-infra): desired state per environment
- [tremor-api](https://github.com/prismatic-hq/tremor-api): seismic signal streams and alerts service
- [steward-api](https://github.com/prismatic-hq/steward-api): resource and operations management service

Docs: [REQUIREMENTS.md](docs/REQUIREMENTS.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Layout

| Path | Contents |
|---|---|
| `caldera_platform/stacks/` | CDK stacks: Network, Cluster, Data, Dns, Registry, CiAccess, AddonIdentity, GitOpsBridge |
| `gitops/` | Argo CD bootstrap, addon ApplicationSets, workload ApplicationSets |
| `charts/prismatic-service/` | Shared Helm chart for the services |
| `contracts/events/` | CloudEvents 1.0 envelope profile, event registry, data schemas |
| `seeder/` | `golden-seeder` package for the golden database snapshot |

## Quick Start

Requires `uv`, `task`, Node.js and Helm.

```sh
task init && task test
```

## Key Commands

| Command | What it does |
|---|---|
| `task init` | Install dependencies and git hooks |
| `task test` | CDK and seeder tests, `helm lint`, event contract validation |
| `task lint` | ruff lint and format check |
| `task build` | `cdk synth` into `cdk.out` |
| `task dev` | List stacks and their dependencies |
| `task up` / `task down` | `cdk deploy --all` / `cdk destroy --all` (needs AWS credentials) |
