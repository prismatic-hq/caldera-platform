# Architecture

Status: stub. Requirements and design decisions live in [REQUIREMENTS.md](REQUIREMENTS.md).

## Vocabulary

- **Vent**: one isolated preview environment. Own namespace, own CNPG Postgres cluster restored
  from the golden snapshot, own URLs `https://<service>-<vent>.preview.<domain>`.
- **Eruption**: a feature group. Branches named `feature/<name>` with the same `<name>` in more
  than one service repo share one vent named `<name>`.
- **Cooling**: teardown of a vent when its branches are merged or deleted, or its TTL expires.
  Argo CD finalizers remove Applications, namespace, database, DNS records and secrets.
- **Baseline**: the long-lived `dev` environment running `main` of every service.

## Repositories

| Repo | Role |
|---|---|
| [caldera-platform](https://github.com/prismatic-hq/caldera-platform) | CDK stacks, GitOps Bridge, Argo CD addons, ApplicationSets, AppProjects, shared Helm chart, CloudEvents contracts, `golden-seeder` |
| [applications-infra](https://github.com/prismatic-hq/applications-infra) | Desired state: environment files, service pins, values overrides. Data only |
| [tremor-api](https://github.com/prismatic-hq/tremor-api) | Seismic signal streams and alerts (FastAPI, `tremor` schema) |
| [steward-api](https://github.com/prismatic-hq/steward-api) | Sites, crews, work orders (FastAPI, `steward` schema) |

## Control flow

1. CDK provisions AWS foundations, installs Argo CD and writes the cluster Secret (GitOps Bridge).
2. Argo CD syncs `gitops/bootstrap`, which installs addons and the workload ApplicationSets.
3. Service CI builds and pushes an image, then writes the vent's files in `applications-infra`.
4. ApplicationSets render one `env-<vent>` Application and one `<service>-<vent>` Application
   per service file. Argo CD syncs them; a PreSync Job runs the branch's migrations.

## Feature group resolution

Chosen design: Option A in REQUIREMENTS.md Section 3 (Git files generator over `applications-infra`).

1. On a push to `feature/<name>`, the vent is `<name>`. Any other branch name is sanitized to a
   DNS-1123 label and used as the vent name.
2. Service CI writes `environments/vents/<vent>/services/<service>.yaml` pinned to the branch
   image digest with `track: branch`.
3. On first write it also creates `env.yaml` and pins every other service to its current `main`
   digest with `track: main`. So a service with no matching branch runs `main` in that vent.
4. When the same `feature/<name>` is pushed in the other repo, its CI overwrites only its own
   service file with `track: branch`. Both branches now run together in one vent.
5. A merge to `main` bumps every `track: main` pin across all vents.
6. On branch delete or merge, CI removes its service file. If no service in the vent still
   tracks a branch, it removes the whole vent directory and the vent cools.
