# Architecture

Requirements and decisions live in [REQUIREMENTS.md](REQUIREMENTS.md). This page is the short tour.

## Vocabulary

- **Vent**: one isolated preview environment = namespace `vent-<name>` + Helm release `vent-<name>`
  of `charts/vent`: tremor, steward, and a Postgres pod from the golden DB image on `emptyDir`.
  URLs are `https://<service>-<vent>.preview.<domain>`.
- **Eruption**: a feature group. `feature/<name>` pushed in both service repos shares vent `<name>`.
  A service without that branch runs from `main` in the vent. Any other branch gets vent
  `<service>-<slug>` with the other service on `main`.
- **Cooling**: teardown. Deleting a branch (merges fire `delete` too) either redeploys the vent with
  that service on `main`, when the other repo still has the branch, or runs `helm uninstall` and
  deletes the namespace.
- **Baseline**: the long-lived `dev` environment built from `main` of both repos.

## Repositories

| Repo | Role |
|---|---|
| [caldera-platform](https://github.com/prismatic-hq/caldera-platform) | CDK app, `vent` chart, `caldera` CLI, reusable workflows, `platform/` manifests, `golden-seeder`, E2E suite, event contracts, local kind setup |
| [tremor-api](https://github.com/prismatic-hq/tremor-api) | Alerts service (FastAPI, `tremor` schema), Dockerfile, Alembic migrations |
| [steward-api](https://github.com/prismatic-hq/steward-api) | Work orders service (FastAPI, `steward` schema), Dockerfile, Alembic migrations |

## Control flow

1. `cdk deploy --all` provisions `NetworkStack`, `ClusterStack` (EKS, Cilium in ENI mode,
   Karpenter), `RegistryStack`, `DnsStack`, `CiAccessStack` and `AddonsStack`. cdk-nag
   `AwsSolutionsChecks` fails the synth on any unacknowledged finding.
2. A push to a non-`main` branch in a service repo runs lint, tests and the image build, pushes
   `sha-<short-sha>` to ECR, and calls `vent.yml`.
3. `vent.yml` runs `caldera vent resolve` to name the vent, then `caldera vent up` in concurrency
   group `vent-<name>` (newest push wins), which runs
   `helm upgrade --install vent-<name> charts/vent -n vent-<name> --create-namespace --wait`.
4. The `delete` event calls `vent-cooling.yml`, which runs `caldera vent down` (never cancelled).
5. The same `caldera` commands run on a laptop against EKS or kind (`--context kind-caldera`).

## Isolation

Each vent namespace gets a default-deny NetworkPolicy (Cilium enforces it) plus allows for traffic
inside the namespace, ingress from `envoy-gateway-system` to the services, and egress to CoreDNS.
Postgres accepts connections only from service and migration pods, and uses a per-vent password.
Every `HTTPRoute` attaches to one shared Gateway and carries `prismatic.dev/exposure: preview`, so
edge auth is a platform-only change.

## Cycle-time budget

| Path | Target (p90) | Main levers |
|---|---|---|
| New vent, image already built | under 60s | headroom pods preempted, golden DB on `emptyDir`, pre-pulled images |
| Push to URL with branch code | under 3 minutes | build only the changed service, BuildKit cache, optimistic start |
| Push to an existing vent | under 2 minutes | only the image tag changes; `HTTPRoute` already live |
| Teardown | under 60s | `helm uninstall` + namespace delete |

Capacity: PriorityClasses `vent-headroom` (-10, never preempts), `vent` (100), `baseline` (1000).
A pause-pod headroom Deployment holds spare vent capacity, sized by a KEDA `ScaledObject`.

## Known gaps

- KEDA's `kubernetes-workload` trigger only counts pods in the `ScaledObject`'s own namespace, so
  it cannot see pods in `vent-*` namespaces as FR-7.3 assumes. The cron trigger works; replace the
  workload trigger with a Prometheus or metrics-api count across namespaces before relying on it.
- Branch migrations as Helm hook Jobs (FR-5.4), event publishing (FR-9) and the golden image build
  (FR-6.2) are not implemented yet.
