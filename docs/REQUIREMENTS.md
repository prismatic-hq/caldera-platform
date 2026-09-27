# Prismatic HQ -- Preview Environment Platform Requirements

Repos:
| Repo | Owns | Written by |
|---|---|---|
| `prismatic-hq/caldera-platform` | CDK, addons, ApplicationSets, AppProjects, shared service Helm chart, `golden-seeder` | Humans via PR |
| `prismatic-hq/applications-infra` | Desired state of every service in every environment (dev, staging, prod, single-tenant, vents). Data only. | Automation direct to `main`; humans via PR |
| `prismatic-hq/tremor-api` | Seismic signal streams and alerts: FastAPI CRUD, Dockerfile, migrations | Humans via PR |
| `prismatic-hq/steward-api` | Resource and operations management: FastAPI CRUD, Dockerfile, migrations | Humans via PR |

Vocabulary:
- **Vent**: one isolated preview environment.
- **Eruption**: a feature group. Branches named `feature/<name>` with the same `<name>` in more than one repo share one vent.
- **Cooling**: teardown of a vent.
- **Baseline**: the long-lived `dev` environment built from `main` of every repo.
- **Golden snapshot**: the nightly, deterministic EBS VolumeSnapshot of a seeded Postgres database that every vent starts from.

---

## 1. Functional Requirements

### FR-1 Baseline infrastructure
| ID | Requirement |
|---|---|
| FR-1.1 | AWS CDK (Python) provisions every foundational AWS coupling point: VPC, EKS, IAM (Pod Identity roles per addon), ECR, Route 53 zone, S3 buckets, Secrets Manager entries, GitHub OIDC roles. |
| FR-1.2 | CDK bootstraps Argo CD and hands infrastructure metadata to it through the GitOps Bridge contract (Section 4). After bootstrap, CDK never deploys addons or workloads. |
| FR-1.3 | Argo CD installs all addons (Section 6) and workloads from Git (App of Apps + ApplicationSets). |
| FR-1.4 | Crossplane with the AWS provider manages AWS resources that belong to an application or a vent (Section 7). CDK and Crossplane never manage the same resource. |
| FR-1.5 | Each service is a FastAPI CRUD app backed by Postgres, shipped as a container image with a Dockerfile: `tremor-api` manages seismic signal streams and alerts; `steward-api` manages resources and operations (sites, crews, work orders). |
| FR-1.6 | Both services share one Postgres cluster per environment. Each service owns its own schema and runs its own migrations. |
| FR-1.7 | The baseline `dev` environment always runs `main` of both services. |

### FR-2 Build and publish (GitHub Actions)
| ID | Requirement |
|---|---|
| FR-2.1 | Every push to any branch and every PR update builds, tests, scans and pushes an image to ECR. |
| FR-2.2 | Images are tagged immutably by commit: `sha-<short-sha>`. Mutable convenience tags: `branch-<slug>`, `pr-<number>`, `main`. |
| FR-2.3 | GitHub Actions authenticates to AWS through OIDC. No long-lived AWS keys exist in GitHub. |
| FR-2.4 | PRs from forks build and test but never push images or receive secrets. |
| FR-2.5 | A merge to `main` publishes a `main` image that Kargo picks up as new Freight. |
| FR-2.6 | CI publishes an SBOM and signs each image (cosign, keyless via GitHub OIDC). |
| FR-2.7 | CI builds and publishes the `golden-seeder` image on changes to its source. |

### FR-3 Vent lifecycle (Argo CD ApplicationSets)
| ID | Requirement |
|---|---|
| FR-3.1 | Pushing a non-`main` branch in either service repo creates a vent within the provisioning SLO (NFR-1.1). |
| FR-3.2 | A vent runs the pushed branch for the repo that has one, and `main` for every repo that does not. |
| FR-3.3 | Branches named `feature/<name>` with the same `<name>` in both repos resolve to one vent named `<name>`, running both branches. Any other branch name gets its own vent named after the sanitized branch. |
| FR-3.4 | Branches in both repos that do not share an eruption name produce separate vents, one per branch. |
| FR-3.5 | A new push to a branch redeploys that vent with the new image tag. |
| FR-3.6 | Deleting or merging a branch cools the vent: Argo CD Applications, namespace, database, Crossplane-managed AWS resources, DNS records and secrets are removed. |
| FR-3.7 | A vent that is idle beyond its TTL (default 72h without a push) cools automatically. |
| FR-3.8 | Each vent gets stable URLs: `https://<service>-<vent>.preview.<domain>`. One level under `preview` so a single wildcard certificate covers every vent. |
| FR-3.9 | When a PR exists for the branch, the platform posts the vent URLs and status to the PR (GitHub Deployments API or Argo CD Notifications). |
| FR-3.10 | Vent names are sanitized to DNS-1123 labels (lowercase, `[a-z0-9-]`). `<service>-<vent>` must fit in 63 characters. A name that cannot be sanitized fails with a clear error naming the limit. |

### FR-4 Vent database
| ID | Requirement |
|---|---|
| FR-4.1 | Each vent gets its own CloudNativePG (CNPG) Postgres cluster. No vent can read or write another vent's data or the baseline's data. |
| FR-4.2 | A vent database bootstraps from the golden snapshot version pinned in the vent config (default: latest verified snapshot at vent creation). |
| FR-4.3 | Each service's branch migrations run against the vent database before the service receives traffic (Argo CD PreSync Job). |
| FR-4.4 | A vent database can be reset to its pinned golden snapshot on demand without recreating the vent. |
| FR-4.5 | Production data never enters a vent or the golden snapshot. All golden data is synthetic and fixed. |

### FR-5 Golden snapshot build (Argo Workflows CronWorkflow)
| ID | Requirement |
|---|---|
| FR-5.1 | A Python service, `golden-seeder`, writes a known, fixed dataset. It contains no randomness: fixed primary keys (UUIDv5 from a fixed namespace), fixed timestamps, fixed ordering. |
| FR-5.2 | An Argo Workflows CronWorkflow runs nightly: create scratch CNPG cluster -> run `main` migrations for both services -> run `golden-seeder` -> verify -> take VolumeSnapshot -> delete scratch cluster. |
| FR-5.3 | Verification computes a per-table content checksum (rows ordered by primary key). The checksum is stored as an annotation on the snapshot. |
| FR-5.4 | The snapshot is labeled with `dataset-version` = hash of (`golden-seeder` image digest + both services' Alembic head revisions). Same inputs must produce the same checksum; a mismatch fails the Workflow and alerts. |
| FR-5.5 | If the inputs hash matches the latest verified snapshot, the build still runs and must reproduce the same checksum (proves determinism), but no new snapshot is kept. |
| FR-5.6 | The last N (default 7) verified snapshots are retained. Snapshots pinned by a live vent are never deleted. |
| FR-5.7 | The CronWorkflow can also be triggered on demand (Argo Events webhook or `argo submit --from cronwf/...`), for example after a migration merges to `main`. |

### FR-6 E2E testing (Argo Workflows + Argo Events)
| ID | Requirement |
|---|---|
| FR-6.1 | An Argo Events sensor triggers the E2E Workflow on a `vent.environment.ready.v1` CloudEvent (FR-10). |
| FR-6.2 | The E2E Workflow resets the vent database to its pinned snapshot, then runs the suite (API, and UI if present) against the vent URLs. |
| FR-6.3 | The E2E Workflow emits `test.e2e.completed.v1`; a reporter posts it to the commit as a GitHub check run. The check can block merge through branch protection. |
| FR-6.4 | Test artifacts (reports, logs, traces, screenshots) are stored in S3 and linked from the check run. |
| FR-6.5 | Contract tests (Pact) verify tremor-api <-> steward-api compatibility on every PR. |

### FR-7 Progressive delivery (Argo Rollouts + Kargo)
| ID | Requirement |
|---|---|
| FR-7.1 | Every service deploys as an Argo Rollout, including in vents. |
| FR-7.2 | Rollouts run metric-driven analysis from Prometheus: request success rate, p95 latency, pod restarts, and a smoke-test Job. |
| FR-7.3 | Staging and prod use canary with traffic shifting through the Argo Rollouts Gateway API plugin (HTTPRoute weights on the Istio Gateway and waypoint). Failed analysis aborts and rolls back automatically. |
| FR-7.4 | Vents use blue-green with a pre-promotion smoke Job and post-promotion Prometheus analysis, so a broken build shows as Degraded without a slow canary. |
| FR-7.5 | Kargo promotes `main` Freight through stages `dev -> staging -> prod`. Promotion to prod requires manual approval. |
| FR-7.6 | Kargo verification reuses the same AnalysisTemplates, so there is one source of truth for health gates. |

### FR-8 Service mesh and ingress (Istio ambient)
| ID | Requirement |
|---|---|
| FR-8.1 | Istio runs in ambient mode (istio-cni + ztunnel). Sidecar injection is not allowed anywhere. |
| FR-8.2 | Every workload namespace, including vents, must carry `istio.io/dataplane-mode: ambient`. Kyverno enforces this and rejects `istio-injection` labels. |
| FR-8.3 | Mesh-wide `PeerAuthentication` is `STRICT` mTLS. |
| FR-8.4 | Each vent namespace gets a default-deny `AuthorizationPolicy`, plus allow rules for its own services, the ingress gateway, Prometheus and the E2E runner. |
| FR-8.5 | Each vent and baseline namespace gets a waypoint proxy, required for L7 metrics, L7 authorization and Rollouts traffic shifting. |
| FR-8.6 | North-south traffic enters through one shared Istio Gateway (Gateway API `Gateway`), exposed by an NLB, with one `HTTPRoute` per service per vent. |
| FR-8.7 | cert-manager issues a Let's Encrypt wildcard certificate `*.preview.<domain>` (DNS-01 via Route 53) for the Gateway, plus certificates for baseline hosts. |
| FR-8.8 | external-dns creates Route 53 records from `HTTPRoute` hostnames and removes them when a vent cools. |

### FR-9 Desired state repo (`applications-infra`)
| ID | Requirement |
|---|---|
| FR-9.1 | `applications-infra` is the single source of truth for what runs where: every service, every environment (dev, staging, prod, single-tenant, vents). |
| FR-9.2 | It contains data only: environment definitions, per-service pins and values overrides. No ApplicationSets, AppProjects, RBAC or generator logic, so a write to it cannot change how Argo CD behaves or widen permissions. |
| FR-9.3 | Each service deploys exactly one Argo CD Application per environment: `<service>-<env>`. That Application owns the service's Rollout, Service, HTTPRoute, AnalysisTemplates, migration Job, ExternalSecret and Crossplane claims. |
| FR-9.4 | Shared per-environment resources (Namespace, CNPG Cluster, waypoint, AuthorizationPolicy, quotas) belong to one environment Application, `env-<env>`, not to any service. |
| FR-9.5 | Every pin is an image digest plus chart version. Tags appear only as comments for readability. |
| FR-9.6 | Vent files pin every service explicitly, including services running `main`. A service tracking `main` carries `track: main` and automation bumps its pin on each `main` merge. |
| FR-9.7 | Automation writes directly to `main` through a GitHub App. Humans change `applications-infra` only through PRs. |
| FR-9.8 | Each writer touches only its own files (Section 3b), validates the change against a JSON schema before pushing, and retries on push conflicts with rebase (max 5 attempts). |
| FR-9.9 | Every automated commit carries trailers `Source-Repo`, `Source-SHA`, `Environment`, `Actor` for audit. |
| FR-9.10 | A push to `applications-infra` triggers an Argo CD webhook, so sync starts without waiting for polling. |

### FR-10 Event contract (CloudEvents)
| ID | Requirement |
|---|---|
| FR-10.1 | Every platform event is a CloudEvents 1.0 event in structured JSON mode. No component emits or consumes a bespoke event shape. |
| FR-10.2 | Every event validates against the platform envelope profile `contracts/events/envelope.v1.schema.json` (Section 3c) and its `data` validates against the schema named in `dataschema`. |
| FR-10.3 | Every event type is registered in `contracts/events/registry.v1.yaml` with owner, schema, delivery guarantee and status. An unregistered type is rejected. |
| FR-10.4 | Event types carry their major version in `type` (`...vent.environment.ready.v1`). Within a major version, schemas change additively only: new optional fields. Anything else is a new major version. |
| FR-10.5 | Producers validate before publishing. Consumers validate on receipt and send invalid events to a dead-letter subject with the validation error. |
| FR-10.6 | Delivery is at-least-once. Consumers are idempotent on `(source, id)`. |
| FR-10.7 | Events carry the CloudEvents Distributed Tracing extension (`traceparent`), so a vent's lifecycle is one trace in Tempo from branch push to E2E result. |
| FR-10.8 | Python producers and consumers use the CNCF `cloudevents` SDK. GitHub Actions emit through one shared composite action that wraps it. |
| FR-10.9 | A CI contract check in `caldera-platform` validates the registry, every schema and every example event, and fails a PR that makes a breaking change within a major version. |
| FR-10.10 | Sensors trigger on registered CloudEvent `type` values, not on raw Kubernetes object fields or raw GitHub payloads. |

---

## 2. Non-Functional Requirements

| ID | Category | Requirement | Target |
|---|---|---|---|
| NFR-1.1 | Speed | Branch push to a healthy vent | p90 under 10 minutes |
| NFR-1.2 | Speed | Branch delete to fully cooled vent | under 10 minutes |
| NFR-1.3 | Speed | CI build and push per service | under 5 minutes, with layer caching |
| NFR-1.4 | Speed | Vent database ready from snapshot | under 3 minutes |
| NFR-2.1 | Isolation | Vents share no data, secrets or network paths | Namespace per vent, ambient mTLS, default-deny AuthorizationPolicy + NetworkPolicy, per-vent DB credentials |
| NFR-2.2 | Isolation | A vent cannot starve the baseline or other vents | ResourceQuota + LimitRange per vent namespace; vents on a separate Karpenter NodePool |
| NFR-3.1 | Determinism | Golden snapshot rebuilt from the same inputs is byte-identical in content | Checksum match enforced every night (FR-5.5) |
| NFR-3.2 | Determinism | E2E runs start from the same data | DB reset to pinned snapshot before every run |
| NFR-3.3 | Determinism | Flaky test rate | under 1% of runs; flaky tests quarantined, never retried silently |
| NFR-4.1 | Scale | Concurrent vents supported | at least 20, capped by config |
| NFR-4.2 | Cost | Idle cost per vent | tracked per namespace (OpenCost); TTL cooling (FR-3.7); Spot nodes for vents |
| NFR-5.1 | Security | No static cloud credentials in cluster or CI | EKS Pod Identity + GitHub OIDC only |
| NFR-5.2 | Security | Secrets come only from AWS Secrets Manager via External Secrets | None in Git, plaintext or SOPS |
| NFR-5.3 | Security | Preview URLs are not public | oauth2-proxy (GitHub org SSO) through Istio `CUSTOM` authorization |
| NFR-5.4 | Security | Only signed images from the org's ECR run | Kyverno `verifyImages` + registry allowlist |
| NFR-5.5 | Security | Vent AppProject cannot create cluster-scoped or RBAC resources | Argo CD AppProject allow/deny lists |
| NFR-5.6 | Security | All in-mesh traffic encrypted | Ambient mTLS STRICT, verified by a policy test |
| NFR-5.7 | Security | Crossplane IAM role cannot touch CDK-owned resources | IAM permission boundary + tag-based conditions (`managed-by=crossplane`) |
| NFR-6.1 | Reliability | Platform state is fully reproducible from Git + CDK | `cdk deploy` + Argo CD sync recreates the cluster |
| NFR-6.2 | Reliability | Cooling never leaks resources | Finalizers on every Application and Crossplane claim; nightly orphan sweep reports leaks |
| NFR-6.3 | Reliability | Let's Encrypt rate limits never block vent creation | Single wildcard cert for all vents; staging issuer for tests |
| NFR-7.1 | Observability | Every vent has metrics, logs and traces labeled by vent | `prismatic.dev/vent=<name>` on all resources |
| NFR-7.2 | Observability | Mesh and rollout health visible per vent | Grafana dashboards for Istio, Rollouts, CNPG; Kiali for mesh topology |
| NFR-7.3 | Observability | Vent lifecycle events are auditable | Every lifecycle step emits a registered CloudEvent; events retained 30 days in JetStream and archived to S3 |
| NFR-7.4 | Interoperability | Any consumer can read any platform event without custom parsing | 100% of events validate against the CloudEvents 1.0 envelope profile; CI contract check blocks breaking changes |
| NFR-8.1 | Maintainability | Adding a third service needs no platform code change | Services register via a config entry only |
| NFR-8.2 | Maintainability | Addons pinned and auto-updated | Renovate PRs for charts, images, CDK libs |
| NFR-9.1 | Developer experience | Developers need no AWS or kubectl access to use vents | Push a branch, read URLs from the PR |

Note on "100% confidence": E2E at PR time sharply reduces regression risk but cannot prove absence of regressions. State the goal as measurable gates instead: required check passes, coverage floor, contract tests green, Rollout analysis green, zero quarantined tests on critical paths.

---

## 3. Feature Group Resolution -- Design Options

ApplicationSet generators do not natively union branches across two repos and fall back to `main`.

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Git files generator over `applications-infra` (chosen)** | Each service's CI writes/deletes `environments/vents/<vent>/services/<service>.yaml` in `applications-infra`. ApplicationSet Git files generators render one Application per service file and one per `env.yaml`. Missing service = pinned `main` digest with `track: main`. | Pure GitOps; auditable history; simple; handles union and fallback trivially | CI needs a GitHub App token to write to another repo; concurrent writes need a concurrency group + rebase retry |
| B. ApplicationSet plugin generator | Small HTTP service queries GitHub for branches in both repos, groups by `feature/<name>`, returns params | No registry commits; always live | Custom service to build, secure and operate; state lives outside Git |
| C. Matrix/merge of SCM Provider or PR generators | Merge generator keyed on branch name | No custom code | Merge only enriches the base generator's items; no union across repos, no clean `main` fallback |
| D. Header-based routing on the shared baseline | Only changed services deploy; Istio routes by `x-vent` header, others fall through to baseline | Cheapest, fastest; Istio already present | Breaks DB isolation unless DB is also routed; needs header propagation in every service |

Recommendation: A. Mention D as the scale-out path now that Istio is in place.

Trigger note: the assignment says "branch pushed", not "PR opened". Create vents on branch push. PRs add URL comments and required checks.

---

## 3b. `applications-infra` Layout And Writers

```
applications-infra/
  schema/                          # JSON schemas for env.yaml and service files
  environments/
    dev/
      env.yaml                     # cluster, namespace, domain, db mode, kargo stage
      services/
        tremor-api.yaml            # chart version, image digest, values overrides
        steward-api.yaml
    staging/ ...
    prod/ ...
    tenants/
      <tenant>/                    # single-tenant: own namespace or own cluster via env.yaml
        env.yaml
        services/ ...
    vents/
      <vent>/
        env.yaml                   # branches, created-by, ttl, dataset-version, eruption name
        services/
          tremor-api.yaml          # branch digest, or main digest + track: main
          steward-api.yaml
```

Example service file:
```yaml
service: tremor-api
chart:
  repo: oci://<account>.dkr.ecr.<region>.amazonaws.com/charts/prismatic-service
  version: 1.4.0
image:
  repository: <account>.dkr.ecr.<region>.amazonaws.com/tremor-api
  digest: sha256:...               # sha-abc1234, branch feature/checkout-v2
track: branch                      # branch | main
values: {}                         # environment-specific overrides only
```

ApplicationSets (live in `caldera-platform/gitops/`, read `applications-infra`):
| ApplicationSet | Generator | Produces |
|---|---|---|
| `environments` | Git files `environments/**/env.yaml` | `env-<env>`: Namespace (ambient label, quota), CNPG Cluster, waypoint, AuthorizationPolicy |
| `services` | Git files `environments/**/services/*.yaml` | `<service>-<env>`: exactly one Application per service per environment, multi-source (chart from OCI + values from the file) |

Both use finalizers so deleting files cools resources. Vent Applications use the `previews` AppProject; others use `baseline`, `staging`, `prod`, `tenants`.

Writers:
| Writer | Identity | Paths it may change | When |
|---|---|---|---|
| Service CI (GitHub Actions) | GitHub App `prismatic-deployer` | `environments/vents/<vent>/**` for its own service; creates `env.yaml` and pins other services at `main` on first write | Branch push, branch delete |
| Service CI on `main` merge | same App | `track: main` pins in every vent | After `main` image publish |
| Kargo | same App (or its own App) | `environments/{dev,staging,prod,tenants/*}/services/*.yaml` | Promotion |
| Vent sweeper (Argo Workflow) | same App | Delete `environments/vents/<vent>/` past TTL or with no live branch | Nightly |
| Humans | PR + review | Anything, including `values` and `env.yaml` | Config changes |

Guardrails for direct-to-main:
- Ruleset on `main`: PR + review required; bypass list = the GitHub App only.
- One shared writer (a composite GitHub Action + a small Python CLI in `caldera-platform`) does schema validation, path allowlist checks, commit trailers and rebase-retry. Kargo uses its built-in `git-commit`/`git-push` steps with the same schema check.
- Post-push CI on `applications-infra` renders every changed Application (`helm template` + kubeconform) and alerts on failure.
- Vent cooling rule: on branch delete, CI removes that service's file; if no service in the vent still tracks a branch, it removes the whole vent directory.

---

## 3c. Event Contract (CloudEvents 1.0)

Location: `caldera-platform/contracts/events/` (same layout as `ryanmcafee/homelab/contracts/events`):
```
contracts/events/
  envelope.v1.schema.json      # CloudEvents 1.0 narrowing profile
  registry.v1.yaml             # every registered type: owner, schema, delivery, status
  data/<domain>.<entity>.<action>.v1.schema.json
  examples/<type>.json         # one valid example per type, checked in CI
```

Envelope profile (narrows CloudEvents 1.0):
| Attribute | Rule |
|---|---|
| `specversion` | `const "1.0"` |
| `id` | Required, unique per `source`; UUIDv4 by default. Idempotency key with `source`. |
| `source` | `//<org-domain>/<component>`, for example `//prismatic-hq/tremor-api/ci`, `//prismatic-hq/caldera/golden-seeder` |
| `type` | `<reverse-domain>.<domain>.<entity>.<action>.v<N>`, for example `com.prismatichq.vent.environment.ready.v1` (reverse-domain set once the domain is chosen) |
| `subject` | Required. The entity: `vent/<name>`, `service/<name>`, `snapshot/<dataset-version>`, `application/<namespace>/<name>` |
| `time` | Required, RFC 3339 |
| `datacontenttype` | `const "application/json"` |
| `dataschema` | Required. URL of the data schema at a pinned `caldera-platform` ref |
| `environment` (extension) | Required. `dev`, `staging`, `prod`, `tenant-<name>`, `vent-<name>` |
| `traceparent` (extension) | Required. W3C trace context (CloudEvents Distributed Tracing extension) |
| `correlationid` (extension) | Required. Ties every event of one vent lifecycle or one promotion together |
| `additionalProperties` | `false`. Adding an attribute is a coordinated rollout: validators first, producers second. |

Event catalog (v1):
| Type suffix | Producer | Consumers | Subject |
|---|---|---|---|
| `build.image.published.v1` | Service CI | Kargo warehouse notes, audit | `service/<name>` |
| `vent.environment.requested.v1` | Service CI (after `applications-infra` write) | Vent tracker, PR commenter | `vent/<name>` |
| `vent.environment.ready.v1` | Vent tracker sensor (env + all service Applications Healthy) | E2E sensor, PR commenter | `vent/<name>` |
| `vent.environment.failed.v1` | Vent tracker sensor | PR commenter, alerting | `vent/<name>` |
| `vent.environment.cooling.v1` | Service CI or vent sweeper | Audit | `vent/<name>` |
| `vent.environment.cooled.v1` | Vent tracker sensor (all Applications deleted) | Orphan sweep, audit | `vent/<name>` |
| `gitops.application.synced.v1` | Argo CD Notifications (templated CloudEvent body) | Vent tracker | `application/<ns>/<name>` |
| `gitops.application.degraded.v1` | Argo CD Notifications | Vent tracker, alerting | `application/<ns>/<name>` |
| `data.snapshot.built.v1` | Golden snapshot Workflow | Snapshot pruner, audit | `snapshot/<dataset-version>` |
| `data.snapshot.verification-failed.v1` | Golden snapshot Workflow | Alerting | `snapshot/<dataset-version>` |
| `data.database.reset.v1` | E2E Workflow | Audit | `vent/<name>` |
| `test.e2e.completed.v1` | E2E Workflow exit handler | GitHub check reporter, audit | `vent/<name>` |
| `delivery.rollout.promoted.v1` | Argo Rollouts notifications | Kargo verification, audit | `rollout/<ns>/<name>` |
| `delivery.rollout.aborted.v1` | Argo Rollouts notifications | Alerting, PR commenter | `rollout/<ns>/<name>` |
| `delivery.analysis.failed.v1` | Argo Rollouts notifications | Alerting | `analysisrun/<ns>/<name>` |
| `delivery.freight.promoted.v1` | Kargo (promotion step webhook) | Audit, release notes | `stage/<name>` |

Transport:
- Producers outside the cluster (GitHub Actions) POST structured CloudEvents (`Content-Type: application/cloudevents+json`) to an Argo Events webhook EventSource behind the Istio Gateway, authenticated with a GitHub OIDC token validated by an Istio `RequestAuthentication`.
- Argo CD and Argo Rollouts notifications use webhook templates whose body is a structured CloudEvent.
- In-cluster Python producers (Workflows, `golden-seeder`) publish with the `cloudevents` SDK to the webhook EventSource, or to NATS JetStream using the CloudEvents NATS protocol binding.
- Argo Events EventBus runs on NATS JetStream with its own account and subject root, separate from any other bus.

Caveat to handle explicitly: Argo Events wraps every received event in its own CloudEvent (the platform event lands in `data.body`). Sensors therefore filter on `body.type` and validate `body` against the envelope profile. Document this so nobody treats the Argo Events outer envelope as the platform contract.

---

## 4. GitOps Bridge With CDK

CDK owns foundational AWS resources and publishes metadata; Argo CD owns everything in-cluster.

1. CDK stacks: `Network` -> `Cluster` (EKS + Pod Identity agent + Karpenter IAM/SQS interruption queue) -> `Data` (S3 buckets for Workflow artifacts, CNPG backups, Loki, Tempo) -> `Dns` (Route 53 zone) -> `Registry` (ECR repos + lifecycle rules) -> `CiAccess` (GitHub OIDC provider + roles) -> `AddonIdentity` (one Pod Identity role per addon, Section 6) -> `GitOpsBridge`.
2. `GitOpsBridge` installs the Argo CD Helm chart and writes an Argo CD **cluster Secret** with:
   - labels as addon feature flags: `enable_istio: "true"`, `enable_crossplane: "true"`, `enable_karpenter: "true"`, ...
   - annotations as metadata: `aws_account_id`, `aws_region`, `cluster_name`, `vpc_id`, `hosted_zone_id`, `domain`, `acme_email`, `ecr_registry`, `artifact_bucket`, `crossplane_role_arn`, ...
3. Addon ApplicationSets use the **cluster generator** with label selectors and template values from annotations. This matches the upstream gitops-bridge-dev contract, so their addon charts can be reused.
4. A root `bootstrap` Application points at `caldera-platform/gitops/` (same split as homelab: `bootstrap` -> `addons` -> `applications` -> `previews`).

CDK caveats:
- CDK's EKS module applies Helm and manifests through a kubectl Lambda. Keep that surface small: Argo CD and the cluster Secret only.
- EKS Blueprints for CDK is TypeScript-first and its Python bindings lag, so plain `aws_eks` + a thin GitOps Bridge construct is safer.
- Use EKS Pod Identity (not IRSA) for every addon role.

---

## 5. Golden Snapshot Pipeline

```
CronWorkflow golden-snapshot (nightly 02:00 UTC, also on demand)
  1. resolve-inputs   seeder image digest + Alembic heads of tremor/steward main -> dataset-version
  2. scratch-db       CNPG Cluster golden-build-<run> (empty, gp3, snapshot-capable StorageClass)
  3. migrate          tremor-api:main `alembic upgrade head`; steward-api:main `alembic upgrade head`
  4. seed             golden-seeder writes fixed fixtures for both schemas
  5. verify           per-table checksum; compare with last snapshot of same dataset-version
  6. snapshot         CNPG Backup method=volumeSnapshot -> VolumeSnapshot labeled dataset-version, checksum
  7. prune            keep last 7 verified; never delete snapshots pinned by live vents
  exit handler        delete scratch cluster; alert on failure
```

`golden-seeder` design rules:
- Lives in `caldera-platform/seeder/` as a Python package with its own Dockerfile and tests.
- Fixtures are code or versioned data files, never generated at random. No Faker without a fixed seed; prefer explicit records.
- IDs: UUIDv5 over a fixed namespace + natural key. Timestamps: fixed epoch constants.
- Inserts in a stable order in one transaction per schema. Sequences reset to known values after load.
- Idempotent: running twice on an empty DB yields the same checksum; running on a non-empty DB fails fast.
- Coupling trade-off: the seeder knows both schemas. Alternative is each service shipping its own `seed` entrypoint and the Workflow calling both. Start central; move ownership to services if schemas drift often.

Vent bootstrap: CNPG `Cluster.spec.bootstrap.recovery.volumeSnapshots` from the pinned snapshot, then the branch's newer migrations run as a PreSync Job. Reset = recreate the CNPG Cluster from the same snapshot (about 1 minute).

Migration risk: a branch migration that is not backward compatible breaks the other service running `main` in the same vent. Enforce expand/contract migrations and run the other service's contract tests in the vent.

Other database options considered (for the write-up): Barman object-store recovery (slower), Aurora fast clone per vent via Crossplane (realistic but 15-clone limit and slower), RDS snapshot restore (too slow), `CREATE DATABASE ... TEMPLATE` on a shared instance (fast but weak isolation).

---

## 6. Platform Addons -- Install List

All installed by Argo CD from `caldera-platform/gitops/addons`, gated by cluster Secret labels. Sync waves order dependencies.

| Wave | Addon | Purpose | Pod Identity role (CDK) |
|---|---|---|---|
| -3 | Gateway API CRDs, prometheus-operator CRDs, snapshot CRDs | CRDs other addons depend on | -- |
| -2 | EBS CSI driver + snapshot-controller + `VolumeSnapshotClass` | PVCs and golden snapshots | EBS CSI |
| -2 | Karpenter + NodePools (`baseline` On-Demand, `vents` Spot) | Node autoscaling, vent isolation | Karpenter |
| -2 | metrics-server | HPA and `kubectl top` | -- |
| -1 | Istio base, istiod, istio-cni, ztunnel (ambient profile) | Mesh, mTLS | -- |
| -1 | AWS Load Balancer Controller | NLB for the Istio ingress Gateway | AWS LBC |
| -1 | cert-manager | Let's Encrypt certs (DNS-01, Route 53) | cert-manager |
| -1 | External Secrets Operator | Secrets Manager -> Kubernetes Secrets | ESO |
| -1 | external-dns | Route 53 records from HTTPRoutes | external-dns |
| -1 | Kyverno | Policy enforcement | -- |
| -1 | Stakater Reloader | Restart on secret/config change | -- |
| 0 | kube-prometheus-stack (Prometheus, Alertmanager, Grafana) | Metrics, Rollouts analysis source | -- |
| 0 | Loki + Fluent Bit | Logs | Loki (S3) |
| 0 | Tempo + OpenTelemetry Collector | Traces | Tempo (S3) |
| 0 | Kiali | Mesh topology for ambient | -- |
| 0 | OpenCost | Per-vent cost | -- |
| 1 | Crossplane + provider-family-aws (+ needed sub-providers) | App/vent-scoped AWS resources | Crossplane |
| 1 | CloudNativePG operator (+ barman-cloud plugin) | Postgres per environment | CNPG backups (S3) |
| 1 | Argo Workflows | Golden snapshot, E2E, orphan sweep | Argo Workflows (S3 artifacts) |
| 1 | Argo Events + EventBus (NATS JetStream) | Event-driven triggers | -- |
| 1 | Argo Rollouts + Gateway API traffic plugin | Progressive delivery, analysis | -- |
| 1 | Kargo | Stage promotion | Kargo (ECR read) |
| 1 | oauth2-proxy | SSO for preview URLs | -- |
| 1 | Trivy Operator | In-cluster CVE scanning | -- |
| 2 | ClusterIssuers (`letsencrypt-staging`, `letsencrypt-prod`), wildcard `Certificate` | TLS | -- |
| 2 | `ClusterSecretStore` (Secrets Manager) | ESO backend | -- |
| 2 | Istio `Gateway`, mesh `PeerAuthentication` STRICT, oauth2-proxy `extensionProviders` | Ingress + mesh policy | -- |
| 2 | Kyverno policies (ambient label, no sidecars, signed images, ECR only, limits, vent labels) | Guardrails | -- |
| 2 | Crossplane `ProviderConfig` + Compositions/XRDs | AWS resource APIs | -- |
| 2 | AnalysisTemplates (ClusterAnalysisTemplate), WorkflowTemplates, CronWorkflow `golden-snapshot`, Sensors | Shared delivery/test logic | -- |
| 2 | Argo CD Notifications (GitHub) | Commit status, PR comments | -- |
| 3 | Baseline applications (tremor-api, steward-api, CNPG cluster) | `dev` environment | -- |
| 3 | `previews` ApplicationSet + AppProject | Vents | -- |

Renovate runs as a GitHub App (or a CronJob, as in homelab) against all three repos.

Istio ambient notes:
- EKS VPC CNI is compatible with ambient via istio-cni chaining.
- ztunnel gives L4 telemetry only. Request-level metrics (`istio_requests_total` with response codes), L7 authorization and traffic splitting need a waypoint per namespace (FR-8.5).
- Rollouts traffic shifting in ambient: use the Gateway API plugin on `HTTPRoute` (north-south on the Gateway, east-west via GAMMA routes bound to the waypoint), not `VirtualService`.
- Services also expose app-level metrics (`prometheus-fastapi-instrumentator`) as a fallback analysis source.

cert-manager notes:
- Wildcard certs require DNS-01; HTTP-01 cannot issue them.
- Let's Encrypt limits: 50 certificates per registered domain per week and 5 duplicate certificates per week. One wildcard for all vents avoids both. Use `letsencrypt-staging` in CI and demos until the flow is stable.

---

## 7. Crossplane Boundary

| Owner | Scope | Examples |
|---|---|---|
| CDK | Foundational, cluster-lifetime, needed before Argo CD exists | VPC, EKS, node IAM, addon Pod Identity roles, Route 53 zone, ECR, GitHub OIDC, shared S3 buckets |
| Crossplane | Declared in Git next to the app, lifecycle tied to an app or vent | Per-vent S3 prefix/bucket for test artifacts, SQS/SNS a service needs, optional Aurora clone per vent, per-service IAM roles |

Wiring:
- CDK creates the Crossplane Pod Identity role with a permission boundary and tag conditions: it may only create, change or delete resources tagged `managed-by=crossplane`.
- Install Crossplane v2 so managed resources can be namespaced, which lets a vent namespace own its AWS resources and cool with it.
- Install only the provider-family-aws sub-providers actually used (for example `provider-aws-s3`, `provider-aws-iam`, `provider-aws-rds`) to limit CRD count and memory.
- `ProviderConfig` uses Pod Identity credentials.
- Define one Composition per need (for example `XVentArtifacts`), so vent charts request `VentArtifacts` claims instead of raw managed resources.
- Deletion policy `Delete` for vent resources, `Orphan` for anything baseline-critical.
- The `previews` AppProject allows only the vent-scoped claim kinds, never raw managed resources or ProviderConfigs.

---

## 8. Patterns To Reuse From `ryanmcafee/homelab`

- `charts/gitops` split: `bootstrap` -> `addons` -> `applications` -> `previews` ApplicationSet with sync waves.
- `previews` AppProject as the trust boundary: no Secrets, RBAC, ServiceAccounts or cluster-scoped kinds except the Namespace.
- Per-preview Namespace with PodSecurity labels, ResourceQuota and LimitRange (`charts/applications/templates/namespaces.yaml`).
- Istio config + waypoints (`charts/istio-config`, `charts/applications/templates/waypoints.yaml`).
- Finalizers on every generated Application so cooling cascades cleanly.
- Chart-side input validation that fails the render with a named error (`_preview.tpl`).
- PostSync smoke Job per app (reuse as the Rollouts pre-promotion Job).
- GitOps Bridge metadata written by IaC (`terragrunt/modules/gitops-bootstrap`); swap to an Argo CD cluster Secret with labels/annotations for EKS.
- CNPG for Postgres (`charts/paperclip-database`) and cert-manager ClusterIssuers (`charts/cert-manager-cluster-issuer`).
- CloudEvents contract layout (`contracts/events/`: envelope profile, registry, per-type data schemas, versioned types, additive-only rule, contract check in CI).

Differences for AWS: Pod Identity instead of the 1Password operator; External Secrets + Secrets Manager instead of SOPS; NLB via AWS LBC; wildcard cert instead of one cert per preview host; vents keyed by eruption name, not PR number.

---

## 8b. Alternatives Considered

### Alternative 1: CI-driven CDK stack per pull request (ECS Fargate)

How it works:
1. A PR to `main` triggers a GitHub Actions deploy workflow. The runner assumes an AWS role through GitHub OIDC.
2. The workflow sets a stage name `pr-<number>`, starts a GitHub Deployment, runs `cdk diff`, then `cdk deploy --require-approval never --context stage=pr-<number>`.
3. CDK creates one CloudFormation stack per PR: a new VPC (public and private subnets, NAT gateways), an ECS cluster, a Fargate service behind an internet-facing ALB, and a stack output with the ALB DNS name.
4. The workflow reads the output file and finishes the GitHub Deployment with the ALB URL, so the PR shows a link.
5. Closing the PR (merged or not) triggers a destroy workflow: `cdk destroy --force` for that stage, then the GitHub Deployment is deactivated.
6. A separate lint workflow runs `cdk synth` and `cfn-lint` on every PR.

Strengths:
- Very small: one stack, three workflows, no in-cluster controllers.
- Teardown is one command because everything for a PR lives in one stack.
- GitHub Deployments give PR-native status and URLs.
- `cdk diff` before deploy shows the infrastructure change in the logs.
- OIDC role assumption; no long-lived AWS keys.

Why not chosen:
| Gap | Impact against this project's requirements |
|---|---|
| One stack per PR in one repo | No feature groups across repos; no `main` fallback for the other service (FR-3.2 to FR-3.4) |
| No database | No per-vent database, no snapshot, no seeding (FR-4, FR-5) |
| New VPC, NAT gateways, ALB per PR | Slow create (VPC, NAT, ALB each take minutes); NAT hourly cost per preview; default quotas (5 VPCs, 5 Elastic IPs per region) cap concurrent previews at roughly 2 with a 2-AZ VPC (NFR-1.1, NFR-4.1, NFR-4.2) |
| Push-based `cdk deploy` from CI | Desired state lives in workflow runs, not Git; drift is not reconciled; no single view of every environment (FR-9.1, NFR-6.1) |
| No `concurrency` group | Two quick pushes collide; the second deploy fails while the stack is updating |
| Fork PRs get no secrets | Previews fail for fork contributions (acceptable, but must be explicit, FR-2.4) |
| Failed destroy is not retried or swept | Orphaned stacks keep billing (NFR-6.2) |
| Public HTTP ALB, no auth | Preview URLs are public and unencrypted (NFR-5.3) |
| Pre-built image only, no build step | Previews reflect infrastructure changes, not application code changes (FR-2.1) |
| No metric-driven health gate or E2E | Deployment succeeds when CloudFormation succeeds, not when the app is healthy (FR-6, FR-7) |

Ideas adopted from it:
- GitHub Deployments API for vent status and URLs on the PR (FR-3.9).
- One owner object per vent so teardown is a single delete (here: the vent directory in `applications-infra` plus Argo CD finalizers).
- A diff step before apply (Argo CD diff on the rendered Applications in `applications-infra` CI).
- GitHub OIDC for all AWS access from CI (FR-2.3).

---

## 9. Scope Cut For A 24-Hour Assignment

The addon list above is a multi-week platform. For the submission:

Must ship:
- CDK: VPC, EKS, ECR, OIDC role, Route 53, addon Pod Identity roles, GitOps Bridge.
- Addons: EBS CSI + snapshots, Karpenter, Istio ambient + Gateway, AWS LBC, cert-manager, external-dns, ESO, kube-prometheus-stack, CNPG, Argo Workflows, Argo Events, Argo Rollouts, Crossplane + AWS provider (one Composition).
- GitHub Actions build/push + vent registry write/delete.
- Vent ApplicationSet covering all four scenarios, CNPG per vent from golden snapshot, golden-snapshot CronWorkflow, Rollouts blue-green with Prometheus analysis, E2E Workflow reporting a GitHub check.
- Demo of all four scenarios plus cooling.

Install but lightly configure: Kyverno (ambient + no-sidecar policies only), Loki, Tempo, OTel, Kiali, OpenCost, Reloader, oauth2-proxy, Trivy Operator.

Show as designed, not built: Kargo multi-stage promotion to prod, image signing enforcement, TTL cooling, orphan sweep.

Open questions:
1. Does a UI exist, or is "E2E against UI" future scope? The assignment defines two APIs only.
2. Baseline DB: CNPG in-cluster (same engine as vents, cheaper) or Aurora (managed; Crossplane clone path)?
3. `applications-infra` style: DRY (pins + values, chart pulled from OCI; recommended) or fully hydrated manifests (rendered YAML committed; exact diffs, larger commits, render step in every writer)?
4. Which registered domain for Let's Encrypt and Route 53?
5. Single cluster for dev + staging + prod (namespaces) or separate clusters per Kargo stage?
