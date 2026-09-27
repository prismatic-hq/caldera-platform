# Architecture

Requirements and decisions live in [REQUIREMENTS.md](REQUIREMENTS.md). This page is the short tour.

## Vocabulary

- **Preview environment**: one isolated environment = namespace `preview-<name>` + Helm release
  `preview-<name>` of `charts/services`: the services and a Postgres pod from the golden DB
  image on `emptyDir`. URLs are `https://<service>-<name>.preview.<domain>`.
- **Feature group**: `feature/<name>` pushed in more than one service repo shares preview environment
  `<name>`. A service without that branch runs from `main`. Any other branch gets preview environment
  `<service>-<slug>` with the other services on `main`. The services come from the registry
  `services.yaml` (name, repo, image, port); adding a service is one entry there.
- **Teardown**: deleting a branch (merges fire `delete` too) either redeploys the preview environment
  with that service on `main`, when another repo still has the branch, or runs `helm uninstall` and
  deletes the namespace.
- **Baseline**: the long-lived `dev` environment built from `main` of both repos.

## Repositories

| Repo | Role |
|---|---|
| [caldera-platform](https://github.com/prismatic-hq/caldera-platform) | CDK app, `services` and `service` charts, `preview` CLI, reusable workflows, `platform/` manifests, `golden-seeder`, E2E suite, event contracts, local kind setup |
| [tremor-api](https://github.com/prismatic-hq/tremor-api) | Alerts service (FastAPI, `tremor` schema), Dockerfile, Alembic migrations |
| [steward-api](https://github.com/prismatic-hq/steward-api) | Work orders service (FastAPI, `steward` schema), Dockerfile, Alembic migrations |

## Control flow

1. `cdk deploy --all` provisions `NetworkStack`, `ClusterStack` (EKS, Cilium in ENI mode,
   Karpenter), `RegistryStack`, `DnsStack`, `CiAccessStack` and `AddonsStack`. cdk-nag
   `AwsSolutionsChecks` fails the synth on any unacknowledged finding. On `cdk destroy`, the
   drainer (`AddonsStack`) removes NodePools, the Gateway and preview namespaces while the
   controllers still run; the sweepers (`NetworkStack`, `DnsStack`) delete what remains.
2. A push to a non-`main` branch in a service repo runs lint, tests and the image build, pushes
   `sha-<short-sha>` to ECR, and calls `preview-environment.yml`.
3. `preview-environment.yml` runs `preview env up` in one job, concurrency group
   `preview-<repo>-<branch>` (newest push wins); it names the environment, takes its Lease, and runs
   `helm upgrade --install preview-<name> charts/services -n preview-<name> --create-namespace --wait`.
4. The `delete` event calls `preview-environment-teardown.yml`, which runs `preview env down` (never cancelled).
5. The same `preview` commands run on a laptop against EKS or kind (`--context kind-caldera`).

## Charts

- `charts/service`: one HTTP service - Deployment, Service, and optionally an HTTPRoute on a shared
  Gateway plus a NetworkPolicy that lets only the gateway namespace reach it. Deployable on its own.
- `charts/services`: umbrella that depends on `service` and renders one per entry in `services`, plus
  the environment-level pieces: Postgres, its Secret, and default-deny namespace policies. Nothing in
  it is preview-specific; `preview env up` sets `environment.kind=preview`, the `preview-environment`
  PriorityClass and the `prismatic.dev/exposure: preview` route label.

## Isolation

Each preview environment namespace gets a default-deny NetworkPolicy (Cilium enforces it) plus allows for traffic
inside the namespace, ingress from `envoy-gateway-system` to the services, and egress to CoreDNS.
Postgres accepts connections only from service and migration pods, and uses a per-environment password.
Every `HTTPRoute` attaches to one shared Gateway and carries `prismatic.dev/exposure: preview`, so
edge auth is a platform-only change.

## Cycle-time budget

| Path | Target (p90) | Main levers |
|---|---|---|
| New preview environment, image already built | under 60s | headroom pods preempted, golden DB on `emptyDir`, pre-pulled images |
| Push to URL with branch code | under 3 minutes | build only the changed service, BuildKit cache, optimistic start |
| Push to an existing preview environment | under 2 minutes | only the image tag changes; `HTTPRoute` already live |
| Teardown | under 60s | `helm uninstall` + namespace delete |

Capacity: PriorityClasses `preview-headroom` (-10, never preempts), `preview-environment` (100), `baseline` (1000).
A pause-pod headroom Deployment holds spare preview environment capacity, sized by a KEDA `ScaledObject`.

## Known gaps

- One platform per account and region: ECR repositories, cleanup Lambdas and their log groups use
  fixed names (derived from `clusterName`) because CI and the teardown checks rely on them.
- KEDA's `kubernetes-workload` trigger only counts pods in the `ScaledObject`'s own namespace, so
  it cannot see pods in `preview-*` namespaces as FR-7.3 assumes. The cron trigger works; replace the
  workload trigger with a Prometheus or metrics-api count across namespaces before relying on it.
- Event publishing (FR-9) and the golden image build (FR-6.2) are not implemented yet.
- Not in the CDK app yet: the pre-pull DaemonSet (FR-6.5, FR-7.5), oauth2-proxy login (Section 4a),
  NLB access logs, the working-hours warm minimum for ARC runners (`minRunners` is 0) and the
  cycle-time dashboard (FR-9.3). The IP allowlist is off unless `previewAllowlistCidrs` is set.
