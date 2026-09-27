# Prismatic HQ -- Preview Environment Platform Requirements

Chosen approach: **AWS CDK (Python) provisions an EKS platform; vents are Helm releases deployed from CI, optimized for delivery speed and short cycle time.**
Primary goal: a developer pushes a branch and has working, seeded, isolated URLs in minutes, and every later push lands in about a minute.

Repos:
| Repo | Owns |
|---|---|
| `prismatic-hq/caldera-platform` | CDK app (VPC, EKS, addons, ECR, IAM), the `vent` Helm chart, the `caldera` CLI, reusable workflows, `golden-seeder`, golden DB image build, E2E suite, CloudEvents contracts, local dev (kind + Tilt) |
| `prismatic-hq/tremor-api` | Seismic signal streams and alerts. FastAPI CRUD, Dockerfile, Alembic migrations |
| `prismatic-hq/steward-api` | Resource and operations management (sites, crews, work orders). FastAPI CRUD, Dockerfile, Alembic migrations |

Vocabulary:
- **Vent**: one isolated preview environment = one namespace `vent-<name>` + one Helm release.
- **Eruption**: a feature group. Branches named `feature/<name>` with the same `<name>` in both service repos share one vent.
- **Cooling**: teardown of a vent.
- **Baseline**: the long-lived `dev` environment built from `main` of both repos.
- **Golden DB image**: a Postgres image with the deterministic seed dataset baked into its data directory, tagged by `dataset-version`.
- **Headroom**: low-priority placeholder pods that hold spare node capacity so real vents schedule instantly.

---

## 1. Cycle Time Budget

Targets (estimates until measured; FR-9 measures them on every run):

| Stage | Lever | Target |
|---|---|---|
| CI start | In-cluster runners (ARC) with warm minimum | under 10s |
| Build changed service only | BuildKit cache in ECR, `uv` cache, shared base image, skip if `sha-<commit>` exists | 45-90s |
| Push image | Runner and ECR in the same region/VPC path | 5-15s |
| Resolve feature group + `helm upgrade --install` | `caldera` CLI, in-cluster credentials | 5-10s |
| Schedule pods | Headroom placeholders preempted instantly | under 5s |
| Pull images | Pre-pull DaemonSet for `main`, shared base and golden DB images; a push ships only a small app layer | 1-5s |
| Database ready | Golden DB image on `emptyDir`, no PVC, no restore, no seed step | 5-10s |
| Migrations | Branch migrations only (golden already at `main` head) | 5-15s |
| Readiness | `startupProbe`/`readinessProbe` at 2s period | 5-10s |
| Route live | Gateway API `HTTPRoute` on Envoy Gateway (xDS config push, no load balancer change); wildcard DNS and certificate, no per-vent DNS or certificate | under 2s |
| **New vent, image already built** | | **p90 under 60s** |
| **Push to URL with branch code** | | **p90 under 3 minutes** |
| **Push to an existing vent** | | **p90 under 2 minutes** |
| Teardown | `helm uninstall` + namespace delete | under 60s |

Optimistic start: on push, the workflow creates the vent with the other service's `main` image and the pushed service's previous image (or `main`) immediately, in parallel with the build. When the build finishes, only the image changes. The URL exists before the build completes.

---

## 2. Functional Requirements

### FR-1 Platform (CDK)
| ID | Requirement |
|---|---|
| FR-1.1 | A Python CDK app in `caldera-platform` defines all AWS infrastructure. No console changes. |
| FR-1.2 | `NetworkStack`: VPC across 2 AZs with flow logs, public subnets for the NLB, private subnets for nodes, one NAT gateway (two with the `natGateways` context flag), S3 gateway endpoint. |
| FR-1.3 | `ClusterStack`: EKS created without the default self-managed networking addons (`bootstrapSelfManagedAddons: false`, no `vpc-cni` or `kube-proxy`), EKS Pod Identity, access entries (no `aws-auth` edits), a small managed node group for system addons, Karpenter for everything else. |
| FR-1.3a | Cilium is the CNI, installed by CDK before any node group: ENI IPAM mode (pods get VPC IPs, so the EKS control plane reaches webhooks and APIServices on pods), ENI prefix delegation, kube-proxy replacement, and Hubble with the Hubble UI. Warm IP settings sized so a vent burst never waits on an ENI attach. |
| FR-1.4 | `RegistryStack`: ECR repos for `tremor-api`, `steward-api`, `golden-db` and the build cache, with immutable tags and lifecycle rules (expire `sha-*` after 14 days unless deployed). |
| FR-1.5 | `DnsStack`: Route 53 hosted zone and the Pod Identity roles for external-dns and cert-manager. The wildcard records `*.preview.<domain>` and `*.dev.<domain>` are created by external-dns (Section 4), because the gateway NLB only exists after the AWS Load Balancer Controller creates it. |
| FR-1.6 | `CiAccessStack`: GitHub OIDC provider and roles for runner bootstrap, plus Pod Identity roles for in-cluster runners (ECR push only for service repos). |
| FR-1.7 | `AddonsStack`: CDK installs the addons as Helm charts on the cluster (Section 4), so the whole platform comes from `cdk deploy --all`. |
| FR-1.8 | `cdk destroy --all --force` removes everything `caldera-platform` provisioned, with no manual steps: the EKS control plane, all nodes (managed and Karpenter), the NAT gateway, the gateway NLB, and every resource created at runtime by in-cluster controllers. Design in Section 4b. |
| FR-1.9 | cdk-nag `AwsSolutionsChecks` runs as a CDK Aspect on every `cdk synth`, locally and in CI. Any unsuppressed error fails the synth, so no non-compliant template can be deployed. Design and Well-Architected coverage in Section 4c. |

### FR-2 Services
| ID | Requirement |
|---|---|
| FR-2.1 | Each service is a FastAPI CRUD app on Postgres: `tremor-api` (alerts), `steward-api` (work orders). Each owns its own schema (`tremor`, `steward`) and Alembic version table in the shared per-environment database. |
| FR-2.2 | Images use one shared base image (Python slim + `uv` runtime deps) so most layers are cached on every node. |
| FR-2.3 | Services start in under 3 seconds and expose `/healthz`, `/readyz`, `/metrics`. |
| FR-2.4 | Services reach each other by Kubernetes Service name inside their own namespace only. |

### FR-3 Build (GitHub Actions)
| ID | Requirement |
|---|---|
| FR-3.1 | Every push to any branch runs lint, unit tests (Postgres via testcontainers) and the image build in parallel jobs. |
| FR-3.2 | Builds use BuildKit with an ECR registry cache (`mode=max`) and skip the build when `sha-<commit>` already exists in ECR. |
| FR-3.3 | Jobs run on Actions Runner Controller scale sets in the cluster, with a minimum of warm idle runners during working hours and scale to zero at night. |
| FR-3.4 | Runners authenticate with EKS Pod Identity. No kubeconfig or AWS keys leave the cluster. |
| FR-3.5 | PRs from forks run lint and tests on GitHub-hosted runners only; they never reach in-cluster runners, ECR or vents. |

### FR-4 Vent lifecycle
| ID | Requirement |
|---|---|
| FR-4.1 | Pushing a non-`main` branch in either service repo creates or updates a vent through the reusable workflow `caldera-platform/.github/workflows/vent.yml`. |
| FR-4.2 | Feature-group resolution (Section 3): `feature/<name>` -> vent `<name>`, using the other repo's `feature/<name>` if it exists, else its `main`. Any other branch -> vent `<repo-short>-<branch-slug>` with the other service on `main`. |
| FR-4.3 | Scenario A/B: branch in one repo only -> vent with that branch plus the other service's latest `main` digest. |
| FR-4.4 | Scenario C1: `feature/<name>` in both repos -> one vent running both branches; the second push updates the existing vent. |
| FR-4.5 | Scenario C2: unrelated branches in both repos -> two vents, each with the other service on `main`. |
| FR-4.6 | Deploy = `caldera vent up`, which runs `helm upgrade --install vent-<name> charts/vent -n vent-<name> --create-namespace --wait` with both image digests and the `dataset-version`. The same command runs in CI and on a laptop. |
| FR-4.7 | URLs: `https://<service>-<vent>.preview.<domain>`, one level under `preview` so one wildcard certificate and one wildcard DNS record cover every vent. Names that exceed the 63-character DNS label fail with a clear error. |
| FR-4.8 | The workflow creates a GitHub Deployment per vent and posts URLs, timings and the E2E result to the commit and PR. |
| FR-4.9 | Branch deleted (GitHub `delete` event; "automatically delete head branches" is on, so merges also fire it): if no branch in the vent remains, `caldera vent down`; otherwise redeploy with the deleted service on `main`. |
| FR-4.10 | A nightly sweeper removes vents whose branches no longer exist or with no push for 72h. |
| FR-4.11 | Deploys and teardowns for one vent never overlap: `concurrency: vent-<name>`, `cancel-in-progress: true` for deploys (the newest push wins), `false` for teardown. |

### FR-5 Vent database
| ID | Requirement |
|---|---|
| FR-5.1 | Each vent runs its own Postgres pod from the golden DB image matching its pinned `dataset-version`. |
| FR-5.2 | The data directory lives on `emptyDir` (memory-backed for small datasets), so there is no PVC, EBS attach or restore on the critical path. |
| FR-5.3 | A database pod restart resets the vent to golden data. This is intended: vents are disposable and deterministic. |
| FR-5.4 | Branch migrations run as a Helm `pre-install`/`pre-upgrade` hook Job per service after the database is ready. |
| FR-5.5 | `caldera vent reset` restarts the database pod to return a vent to golden state in seconds. |
| FR-5.6 | Production data never enters the golden image or any vent. |
| FR-5.7 | Each vent namespace gets a default-deny policy (ingress and egress) enforced by Cilium, plus allows for: traffic inside the namespace, ingress from the Envoy Gateway proxy pods (namespace `envoy-gateway-system`) to the two services only, and egress to CoreDNS. Postgres accepts traffic only from the vent's own service and migration pods. |
| FR-5.8 | Each vent's Postgres sets a per-vent password from a generated Secret at startup (`ALTER ROLE` in the entrypoint), so isolation does not rest on network policy alone even though every vent starts from the same golden image. |

### FR-6 Golden DB image
| ID | Requirement |
|---|---|
| FR-6.1 | `golden-seeder` (Python) writes a fixed dataset: UUIDv5 keys, fixed timestamps, stable insert order, no randomness. |
| FR-6.2 | The `golden-image` workflow (nightly and on `main` merges that change migrations) starts Postgres in the build, runs `main` migrations for both services and `golden-seeder`, verifies a per-table checksum, stops Postgres cleanly and bakes the data directory into `golden-db:<dataset-version>`. |
| FR-6.3 | `dataset-version` = hash of seeder digest + both Alembic heads. Same inputs must give the same checksum; a mismatch fails the build. |
| FR-6.4 | The image is capped at 1 GB compressed so pulls stay fast; the dataset is designed for tests, not volume. |
| FR-6.5 | The pre-pull DaemonSet keeps the latest golden image on every vent node. |

### FR-7 Capacity and speed
| ID | Requirement |
|---|---|
| FR-7.1 | PriorityClasses: `vent-headroom` (-10, `preemptionPolicy: Never`), `vent` (100), `baseline` (1000), plus system classes for addons. |
| FR-7.2 | A headroom Deployment of pause pods sized to at least 2 vents' requests runs on the vent NodePool. Real vent pods preempt them instantly; the evicted placeholders go Pending and Karpenter adds capacity in the background. |
| FR-7.3 | A KEDA `ScaledObject` sizes the headroom Deployment from two triggers, taking the larger: a `cron` trigger (working-hours floor, zero at night) and a `kubernetes-workload` trigger counting running vent pods (label `app.kubernetes.io/part-of=vent`), so spare capacity grows with active vents without logic in the CLI. |
| FR-7.4 | Karpenter NodePool `vents`: Spot and On-Demand fallback, several instance families, consolidation after 5 minutes of underuse. NodePool `baseline`: On-Demand. Both NodePools set the startup taint `node.cilium.io/agent-not-ready=true:NoExecute`, so no pod lands on a node before Cilium is ready. |
| FR-7.5 | A pre-pull DaemonSet keeps `main` service images, the shared base image and the golden DB image on every vent node. If measured pulls on new nodes become significant, use a Bottlerocket data-volume snapshot with pre-cached images in the Karpenter node class. |
| FR-7.6 | Vent pods set tight requests (for example 100m CPU / 256Mi per service, 250m / 512Mi for Postgres) so one node holds many vents. |

### FR-8 E2E testing
| ID | Requirement |
|---|---|
| FR-8.1 | A pytest E2E suite (run with `pytest-xdist`) exercises both APIs and one cross-service flow (a tremor alert creates a steward work order). |
| FR-8.2 | E2E runs as an in-cluster Job right after the vent is ready, calling services by in-cluster name, and reports a GitHub check that can be required for merge. |
| FR-8.3 | Every run starts from golden data (`caldera vent reset` first when the vent already existed). |
| FR-8.4 | Isolation test: from vent A, connections to vent B's Postgres and services and to `dev` must fail. The test runs in CI on every vent deploy and in the demo, with the dropped flows shown in Hubble. |

### FR-9 Cycle time measurement and events
| ID | Requirement |
|---|---|
| FR-9.1 | The `caldera` CLI records stage timings (Section 1) and emits CloudEvents 1.0 to an EventBridge bus: `vent.environment.requested.v1`, `vent.environment.ready.v1`, `vent.environment.failed.v1`, `vent.environment.cooled.v1`, `data.golden.built.v1`, `test.e2e.completed.v1`. Timings go in the event data. |
| FR-9.2 | Event schemas live in `caldera-platform/contracts/events/` and are validated before publishing. |
| FR-9.3 | A dashboard shows p50/p90 of push-to-URL, new-vent, update and teardown times, headroom hit rate (vents that scheduled without waiting for a node) and image pull time per vent (from kubelet `Pulled` events). |

### FR-10 Local development
| ID | Requirement |
|---|---|
| FR-10.1 | `task local:up` creates a kind cluster with its default CNI disabled and Cilium installed, plus the same `vent` chart, golden DB image and PriorityClasses, so network policies and the isolation test behave the same locally and in CI. |
| FR-10.2 | `caldera vent up --context kind-caldera` runs the full vent lifecycle locally, including feature-group resolution against local branches. |
| FR-10.3 | Tilt provides the inner loop: code changes sync into the running pod and FastAPI reloads in seconds, without an image rebuild. |
| FR-10.4 | CI runs the chart on kind (`ct install`) on every chart change, using the same commands. |

---

## 3. Feature Group Resolution

Implemented in the `caldera` CLI (Python) and called by the reusable workflow.

```
caldera vent up --repo R --branch B --sha S
  if B matches feature/<name>:
      vent = <name>
      other_ref = feature/<name> if it exists in the other repo (GitHub API), else main
  else:
      vent = <R-short>-<slug(B)>
      other_ref = main
  image(R)     = sha-S if built, else previous image of this vent or main (optimistic start)
  image(other) = latest digest for other_ref
  helm upgrade --install vent-<vent> charts/vent -n vent-<vent> ...

caldera vent down --repo R --branch B
  if the other repo still has feature/<name>: redeploy with R on main
  else: helm uninstall + delete namespace
```

Edge cases:
- Second repo pushes `feature/<name>` after the vent exists: the vent updates in place.
- A newer push to the same vent cancels the running deploy (`cancel-in-progress: true`).
- `<repo>-<slug>` names always carry a repo prefix, so they never collide with `feature/<name>` vents.

---

## 4. Platform Addons (installed by CDK)

Kept to what speed, routing and security need.

| Addon | Purpose |
|---|---|
| Cilium (installed first) | CNI in ENI mode, NetworkPolicy and CiliumNetworkPolicy enforcement, kube-proxy replacement, Hubble flow visibility. Its Gateway API and Ingress controllers are disabled; Envoy Gateway owns north-south traffic |
| Envoy Gateway | Gateway API implementation for north-south routing (`gatewayClassName: envoy-gateway`); `HTTPRoute` changes apply in about a second; `SecurityPolicy` for external authorization (oauth2-proxy), the optional IP allowlist, JWT and header-based authorization; `ClientTrafficPolicy` for client IP detection |
| Karpenter | Fast node provisioning, Spot, consolidation |
| AWS Load Balancer Controller | One NLB for the gateway (IP targets straight to Envoy proxy pods, proxy protocol v2 so Envoy sees the real client IP) |
| cert-manager | Let's Encrypt wildcard certificates via DNS-01 on Route 53 (issued once, reused by every vent) |
| external-dns | Creates and maintains only the two wildcard records, from a hostname annotation set on the Envoy proxy Service through the `EnvoyProxy` resource (`spec.provider.kubernetes.envoyService.annotations`) (`--source=service`, `--domain-filter=<domain>`, TXT ownership records). It does not watch HTTPRoutes, so no vent ever waits on a DNS change |
| Actions Runner Controller | In-cluster GitHub Actions runners |
| KEDA | Headroom sizing from working-hours schedule and active vent count (FR-7.3). Runners scale through ARC scale sets, not KEDA |
| metrics-server | Resource metrics |
| External Secrets Operator | SSM Parameter Store `SecureString` -> Kubernetes Secrets for externally issued secrets; `Password` generator for secrets created in-cluster |

### 4a. Preview access control (Envoy Gateway)

Why Envoy Gateway on top of Cilium: Cilium stays the CNI and enforces pod-to-pod isolation; Envoy Gateway handles requests at the edge, where it has richer filtering for authentication and authorization (`SecurityPolicy`: client CIDR rules, external authorization, OIDC, JWT claims, header rules) as first-class API objects instead of custom Envoy config.

Access modes (platform values, no chart or service changes):
| Mode | `previewAccess.login.enabled` | `previewAccess.ipAllowlist.enabled` | Result |
|---|---|---|---|
| Default | `false` | `false` | Open: all traffic allowed. Acceptable only because vents hold synthetic data (FR-5.6) |
| Login (target) | `true` | `false` | GitHub org login required; the allowlist is redundant and stays off |
| Locked down | `false` | `true` | Only allowlisted CIDRs; for use if login is not ready and a demo must not be open |
| Defense in depth | `true` | `true` | Both; optional |

IP allowlist (optional, off by default):
- When enabled, a `SecurityPolicy` targeting the shared `Gateway` sets `authorization.defaultAction: Deny` with an `Allow` rule for `principal.clientCIDRs` (office, VPN, reviewer CIDRs).
- It depends on the real client IP reaching Envoy: proxy protocol v2 on the NLB plus a `ClientTrafficPolicy`. Keep proxy protocol on regardless, so access logs show real client IPs.

Login with oauth2-proxy (in scope if time permits):
- oauth2-proxy runs in `preview-auth` with the GitHub provider, restricted to the `prismatic-hq` org, cookie domain `.preview.<domain>` so one login covers every vent; `auth.preview.<domain>` routes to it.
- A `SecurityPolicy` with `extAuth` (HTTP) calls oauth2-proxy for every preview request. GitHub is an OAuth provider, not an OIDC provider, so external authorization through oauth2-proxy is used instead of Envoy Gateway's native `oidc` block. The native block becomes an option if the identity provider changes to an OIDC one (for example Cognito, Okta, or Dex in front of GitHub).
- Authorization rules can then use the identity headers oauth2-proxy returns (user, email, groups), for example limiting a vent to one GitHub team.

Why this stays a platform-only change:
- Every vent `HTTPRoute` attaches to one shared `Gateway` listener (`parentRefs` set by the `vent` chart), and the chart labels each route `prismatic.dev/exposure: preview`. Policies target the Gateway (or the labeled routes) once and cover every vent.
- Services hold no auth logic and the `vent` chart has no auth settings, so adding or changing auth needs no service code, chart or pipeline changes.
- In-cluster callers (the E2E Job, probes) use Service names, not the gateway, so edge auth never breaks tests or health checks.

### 4b. Full teardown with `cdk destroy --all`

Problem: controllers create AWS resources at runtime that CloudFormation does not know about. Left alone, they are orphaned, keep billing, and block VPC, subnet or hosted zone deletion, so `cdk destroy` fails partway.

| Created at runtime by | Resources | Cleanup |
|---|---|---|
| AWS Load Balancer Controller | Gateway NLB, target groups, NLB security groups | Drainer deletes the Envoy proxy Service; the controller removes the NLB, target groups and security groups |
| Karpenter | EC2 instances, launch templates | Drainer deletes NodePools and NodeClaims; Karpenter terminates the instances |
| Cilium (ENI mode) | ENIs on nodes | Terminated with the instances (delete-on-termination); sweeper removes any left `available` |
| external-dns | Wildcard A and TXT records | `--policy=sync`, so deleting the Service deletes the records; sweeper removes any non-SOA/NS record before the zone is deleted |
| cert-manager | DNS-01 TXT challenge records | Removed after issuance; sweeper catches leftovers |
| EKS and Container Insights | CloudWatch log groups (`/aws/eks/<cluster>/cluster`, `/aws/containerinsights/<cluster>/*`) | Pre-created in CDK with `RemovalPolicy.DESTROY`; sweeper deletes any others by prefix |
| CDK kubectl provider | Lambda log groups, Lambda VPC ENIs | Log groups owned by CDK with `DESTROY`; Lambda ENIs are released by AWS (can take many minutes) |

Mechanism:
1. **Drainer** (custom resource in `AddonsStack`, depends on every addon chart and the cluster). CloudFormation deletes dependents first, so the drainer's Delete handler runs while all controllers are still alive. It deletes vent namespaces, the Gateway and the Envoy proxy Service, PVCs, then Karpenter NodePools and NodeClaims, and waits until the NLB, target groups, Karpenter instances and external-dns records are gone (timeout 20 minutes, clear error naming what is left).
2. **Sweeper** (custom resource in `NetworkStack`, depends on the VPC, so it is deleted before the VPC and after every other stack). Its Delete handler removes anything still tagged for the cluster (`elbv2.k8s.aws/cluster`, `karpenter.sh/discovery`, `kubernetes.io/cluster/<name>`): load balancers, target groups, security groups, instances, launch templates, `available` ENIs. A matching sweeper in `DnsStack` empties the hosted zone of non-SOA/NS records, and the `NetworkStack` sweeper deletes SSM parameters under `/prismatic/`.
3. **CDK removal policies**: `RemovalPolicy.DESTROY` on everything; ECR repos with `emptyOnDelete: true`; no Secrets Manager secrets (nothing pending deletion to collide with on redeploy); AWS-managed KMS keys (customer keys cannot be deleted immediately).
4. **Verification**: `task verify:clean` (Python, boto3) lists any resource carrying `prismatic:*` or the cluster tags across EC2, ELB, Route 53, ECR, CloudWatch Logs and EKS, and exits non-zero if anything remains. Run it after every destroy.

What stays by design: the `CDKToolkit` bootstrap stack and its bucket (not part of this app). Re-creating the hosted zone assigns new name servers, so the domain's delegation must be updated after every fresh deploy.

Expected duration: about 20-40 minutes, mostly EKS deletion and Lambda ENI release (estimate).

### 4c. Well-Architected review and cdk-nag

What cdk-nag can and cannot prove:
- cdk-nag checks the synthesized CloudFormation templates against rule packs. `AwsSolutionsChecks` is the closest pack to Well-Architected best practices; it is blocking. `NIST80053R5Checks` runs in report-only mode for extra signal.
- cdk-nag does not see anything created at runtime or inside the cluster: the gateway NLB, Karpenter instances, Helm-rendered Kubernetes objects. Those are covered by the complementary checks below.
- A Well-Architected review is a question-by-question assessment across six pillars in the AWS Well-Architected Tool. cdk-nag is evidence for it, not a substitute. "Pass" is defined here as: zero unsuppressed cdk-nag errors, zero high-risk issues (HRIs) left unacknowledged in the Well-Architected Tool, and every accepted risk documented with its reason.

Expected `AwsSolutionsChecks` findings for this design and how each is resolved:
| Rule | Finding | Resolution |
|---|---|---|
| VPC7 | VPC without flow logs | Fix: flow logs to a CloudWatch log group owned by the stack |
| EKS1 | Public API endpoint | Fix: private endpoint only; CDK kubectl handler placed in the VPC; ARC runners are in-cluster; laptops reach the API through SSM Session Manager port forwarding to a small access instance (or accept EKS1 as a documented risk if that friction is unwanted) |
| EKS2 | Control plane logs not all enabled | Fix: api, audit, authenticator, controllerManager, scheduler, into the pre-created log group |
| IAM4 | AWS managed policies on the EKS cluster and node roles, Lambda execution roles | EKS cluster and node roles: keep the AWS managed policies AWS requires or recommends for EKS; this is the only suppression (policy below). Lambda execution roles (drainer, sweeper, kubectl handler where configurable): fix with inline least-privilege CloudWatch Logs policies instead of `AWSLambdaBasicExecutionRole` |
| IAM5 | Wildcards in Karpenter, Load Balancer Controller, Cilium operator, external-dns, cert-manager, drainer/sweeper and CI policies | Fix: resource ARNs, `aws:ResourceTag` and `aws:RequestTag` conditions, the hosted zone ARN for DNS actions. Actions that AWS documents as not supporting resource-level permissions (for example `ec2:Describe*`, `ecr:GetAuthorizationToken`, `route53:ListHostedZones`) must use `Resource: *`; these exact actions are suppressed per the policy below |
| L1 | Lambda not on the latest runtime | Fix: drainer and sweeper on the latest Python runtime; keep CDK current so its provider Lambdas are too |
| SQS3, SQS4 | Karpenter interruption queue without DLQ or SSL enforcement | Fix: DLQ and `enforceSSL: true` |
| SMG4 | Secrets Manager secrets without rotation | Fix by design: the platform creates no Secrets Manager secrets. Generated secrets (per-vent Postgres passwords, oauth2-proxy cookie secret) are created in-cluster by the External Secrets `Password` generator and never leave the cluster. Externally issued secrets (GitHub App private key, GitHub OAuth client secret) are SSM Parameter Store `SecureString` values under `/prismatic/`, written by `task secrets:put` and read by External Secrets |
| S1, S2, S10 | Any S3 bucket without access logs, public access block or SSL-only | Fix: block public access and `enforceSSL` on every bucket; avoid buckets where a log group works |
| EC26 and related | Unencrypted EBS on nodes | Fix: encrypted gp3 root volumes in the managed node group launch template and the Karpenter `EC2NodeClass`; IMDSv2 required on both |

Suppression policy:
- Default is fix at source. Exactly two suppression categories are allowed, both because AWS itself requires them:
  - IAM4 on the EKS cluster role and the node roles (managed node group and Karpenter), where AWS requires or recommends its managed policies (`AmazonEKSClusterPolicy`, `AmazonEKSWorkerNodePolicy`, `AmazonEC2ContainerRegistryReadOnly`, `AmazonSSMManagedInstanceCore`).
  - IAM5 `Resource::*` only for actions listed in the AWS Service Authorization Reference as not supporting resource-level permissions (for example `ecr:GetAuthorizationToken`, `ec2:Describe*`, `elasticloadbalancing:Describe*`, `route53:ListHostedZones`, `route53:ListHostedZonesByName`). Each suppression names the exact role and action; any action that does support resource ARNs or conditions must be scoped, never suppressed.
- A unit test synthesizes the app and asserts that every suppression is IAM4 on the named EKS roles or IAM5 on an action from the allowed list, so a new suppression outside the policy fails CI.
- Every suppression lives in one file, `caldera/nag_suppressions.py`, uses `NagSuppressions.add_resource_suppressions` on the exact resource with `applies_to` for the exact permission, and carries a reason that links the AWS or CDK documentation.
- CI prints the suppression count; any new suppression requires review in the PR.

Checks beyond cdk-nag (runtime and in-cluster):
| Area | Check |
|---|---|
| Helm-rendered Kubernetes objects (`vent` chart, addon values) | `trivy config` on rendered manifests in CI; failures block the chart change |
| EKS node and control plane configuration | `kube-bench` (CIS EKS benchmark) Job after cluster creation, report stored as an artifact |
| Runtime AWS resources (NLB, Karpenter instances) | Controller settings enforce the same rules: NLB access logs and TLS policy via Load Balancer Controller annotations, IMDSv2 and encrypted volumes via `EC2NodeClass`; AWS Security Hub (AWS Foundational Security Best Practices) optional during the demo window |

Pillar coverage and known gaps (recorded in the Well-Architected Tool as accepted PoC risks):
| Pillar | Covered by | Known gap in this PoC |
|---|---|---|
| Operational excellence | Everything as code, one CLI for CI and local, cycle-time metrics, runbook in README | No on-call or alert routing |
| Security | OIDC and Pod Identity only, private EKS endpoint, Cilium default-deny, encryption at rest, cdk-nag blocking | Preview URLs open by default until oauth2-proxy ships (risk 11) |
| Reliability | 2 AZs, Karpenter capacity, stateless vents that rebuild in seconds | Single NAT gateway (`natGateways: 2` context flag for review mode); vent databases reset on restart by design |
| Performance efficiency | Cycle-time budget measured per run, headroom, small images | None significant for the scope |
| Cost optimization | Spot for vents, scale to zero at night, cost allocation tags, full teardown, AWS Budgets alarm created by CDK | Idle headroom during working hours |
| Sustainability | Spot, consolidation, scale to zero; Graviton (arm64) nodes with multi-arch images | None significant for the scope |

Not installed (roadmap, Section 9): Argo CD, Kargo, Argo Rollouts, Istio, Crossplane, CNPG, Kyverno, full observability stack.

---

## 5. Non-Functional Requirements

| ID | Category | Requirement | Target |
|---|---|---|---|
| NFR-1.1 | Speed | New vent with images already built | p90 under 60s |
| NFR-1.2 | Speed | Push to URL with branch code | p90 under 3 minutes |
| NFR-1.3 | Speed | Push to an existing vent | p90 under 2 minutes |
| NFR-1.4 | Speed | Teardown | under 60s |
| NFR-1.5 | Speed | Headroom hit rate during working hours | 90%+ of vents schedule without waiting for a node |
| NFR-2.1 | Isolation | Vents share no database, credentials or pod network access | Namespace per vent, Cilium-enforced default-deny, per-vent Postgres password, verified by the FR-8.4 isolation test on every deploy |
| NFR-3.1 | Determinism | Same `dataset-version` = same data | Checksum verified at image build |
| NFR-4.1 | Scale | Concurrent vents | 30+, bounded by a config cap and Karpenter limits |
| NFR-4.2 | Cost | Fixed platform cost stated with measured numbers in the README | EKS control plane, system nodes, NLB, NAT |
| NFR-4.3 | Cost | Idle cost | Headroom and runners scale to zero at night; vents on Spot |
| NFR-5.1 | Security | No static cloud credentials | GitHub OIDC for bootstrap, Pod Identity in cluster |
| NFR-5.2 | Security | Fork code never runs on in-cluster runners | Separate runner labels; fork PRs pinned to GitHub-hosted runners |
| NFR-5.3 | Security | Preview URLs require login once oauth2-proxy ships; open by default until then | Envoy Gateway `SecurityPolicy` + oauth2-proxy GitHub org login via `extAuth`; optional IP allowlist; vents hold synthetic data only, so an open vent exposes no customer data (Section 4a) |
| NFR-5.4 | Security | Vent namespaces have no cloud permissions | No Pod Identity associations in `vent-*` namespaces |
| NFR-5.5 | Security | Synthetic data only | FR-5.6, FR-6.1 |
| NFR-6.1 | Reliability | Teardown never leaks | All vent resources in one namespace; sweeper reports stray namespaces |
| NFR-6.3 | Cost | Platform teardown leaves nothing billing | `cdk destroy --all --force` succeeds in one run and `task verify:clean` reports zero leftover resources (Section 4b) |
| NFR-6.2 | Reliability | Spot interruption | Vent pods reschedule and the database resets to golden; acceptable for previews |
| NFR-7.1 | Developer experience | Developers need no AWS or kubectl access to use vents | Push a branch, read URLs, timings and E2E result on the PR |
| NFR-7.2 | Developer experience | Same commands everywhere | `caldera` CLI identical in CI, on a laptop against EKS, and on kind |
| NFR-8.1 | Compliance | Templates meet AWS Solutions best practices | `cdk synth` with `AwsSolutionsChecks` has zero unsuppressed errors; suppressions limited to IAM4 on EKS cluster/node roles and IAM5 on actions without resource-level permissions, enforced by a test |
| NFR-8.2 | Compliance | Well-Architected review | No unacknowledged high-risk issues in the Well-Architected Tool; accepted PoC risks listed in Section 4c |

---

## 6. Key Decisions And Trade-offs

| Decision | Chosen | Trade-off accepted |
|---|---|---|
| Compute | EKS + Karpenter | Higher fixed cost and ops than ECS; vents start in seconds, not minutes |
| CNI | Cilium in ENI mode instead of the AWS VPC CNI | You own CNI upgrades and EKS version compatibility, and AWS support does not cover Cilium; in return policies are enforced by default, kube-proxy is replaced by eBPF, Hubble shows every flow, and kind runs the same CNI locally |
| Environment unit | Namespace + Helm release | Softer isolation than a stack per vent; creation and teardown under a minute |
| Database | Golden data baked into a Postgres image on `emptyDir` | Not the managed engine; data resets on restart; dataset size capped; zero restore or seed time |
| Capacity | Headroom placeholders at negative priority | Pays for idle spare capacity during working hours; removes node wait from the critical path |
| Images | Pre-pull DaemonSet + shared base image + small app layers | Pre-pull uses node disk for images a vent may never need; pulls in seconds |
| Routing | Cilium for CNI + Envoy Gateway for north-south, wildcard DNS (external-dns, wildcard records only) and wildcard certificate | Two networking components instead of one; richer request-level auth and authorization at the edge; no per-vent DNS or certificate delay |
| Preview auth | Open by default; oauth2-proxy login is the access control; IP allowlist optional and off | Until login ships, preview URLs are reachable by anyone who knows them (synthetic data only, unguessable only by obscurity); one shared gateway listener keeps auth changes platform-only |
| cdk-nag findings | Fix at source; suppress only IAM4 on the EKS cluster and node roles and IAM5 `Resource: *` for actions AWS documents as not supporting resource-level permissions | Secrets move to in-cluster generation and SSM Parameter Store to clear SMG4 without rotation Lambdas; a test pins the suppression list to these two categories |
| Runners | In-cluster ARC runners | Cluster runs CI code, so fork code is excluded; no runner cold start, warm caches, no credentials leave the cluster |
| Deploy model | Push-based Helm from CI | No drift reconciliation; fastest path, and the chart carries over to GitOps later |
| Feature groups | Branch name convention | Relies on naming discipline; transparent, no extra service |

---

## 7. Alternatives Considered

### 7.1 ECS Fargate + Aurora copy-on-write clone per vent

A CDK stack per vent: two Fargate services behind a shared ALB and an Aurora clone of a golden cluster.
Strengths: CDK-native, managed database engine, low operational load, low fixed cost.
Rejected because provisioning speed is a deal breaker: new vent about 8-15 minutes (clone + Serverless v2 instance + CloudFormation + ECS settling), image update about 3-5 minutes, teardown about 10-15 minutes, a 15-clone limit per source cluster, and no way to run the vent lifecycle locally.

### 7.2 CI-driven CDK stack per pull request with a new VPC per stack

A PR triggers `cdk deploy` of a stack with its own VPC, NAT gateways, ECS cluster, Fargate service and public ALB; closing the PR runs `cdk destroy`.
Rejected: one repo only, no database, slow and costly per PR, default VPC and Elastic IP quotas cap it at about 2 concurrent previews, public HTTP only.
Adopted: GitHub Deployments for PR URLs, one owner object per environment for one-command teardown, OIDC.

### 7.3 Other options

| | Chosen: EKS + Helm, speed-tuned | ECS + Aurora clone | ECS + shared RDS template DB | Lambda + Aurora Serverless v2 | EC2 + docker compose | Full GitOps platform |
|---|---|---|---|---|---|---|
| New vent | ~1 min | ~8-15 min | ~3-5 min | ~5-10 min | ~2-4 min | ~5-10 min |
| Update | ~1-2 min incl. build | ~3-5 min | ~3-5 min | ~2-4 min | ~2 min | ~2-5 min |
| Runs locally end to end | Yes (kind) | No | No | Partly | Yes | Yes (kind) |
| Fixed cost | Medium | Low-medium | Low-medium | Low | Lowest | High |
| Moving parts | Some | Few | Few | Few-medium | Fewest | Very many |

Times are estimates until measured.

### 7.4 Header-based routing on the shared baseline

Rejected: baseline services would still use the baseline database, breaking the per-environment database requirement.

### 7.5 Warm pool of fully provisioned vents

Pre-created namespaces with services and databases, claimed on push. Not needed: with headroom and a baked golden image a vent starts in under a minute from nothing, and claiming adds renaming and state-tracking logic. Revisit if measured p90 misses NFR-1.1.

---

## 8. Demo Plan (video)

1. Show `dev` running `main` of both services and the cycle-time dashboard.
2. Push `feature/quake-alerts` in tremor-api only -> vent with steward on `main` (scenario A); show the URL live before the build finishes.
3. Push `fix-crew-sync` in steward-api only -> vent `steward-fix-crew-sync` (scenario B).
4. Push `feature/quake-alerts` in steward-api -> the existing vent updates to both branches (scenario C1).
5. Push `feature/tsunami` in tremor-api and `feature/lava-flow` in steward-api -> two vents (scenario C2).
6. Show isolation (data written in one vent is absent elsewhere) and `caldera vent reset`.
7. Show headroom preemption: placeholders evicted, vent pods running, Karpenter adding a node in the background.
8. Delete branches -> vents cool in under a minute.
9. Run the same `caldera vent up` on kind locally.
10. Walk the code: CDK stacks, `vent` chart, `caldera` CLI, `vent.yml`, golden image build.

---

## 9. Roadmap: GitOps Platform

Adopt when the service count, team count or release process outgrows push-based Helm: roughly 5+ services, several teams, progressive delivery with automated rollback, or auditors asking for one versioned record of what runs where.

- Argo CD bootstrapped by CDK (GitOps Bridge); a desired-state repo written by automation; Git files ApplicationSets with one Application per service per environment.
- `helm-charts` repo publishing the `vent` chart split into `prismatic-service` and `prismatic-environment`, OCI in ECR.
- Kargo promotion `dev -> staging -> prod`; Argo Rollouts with Prometheus analysis.
- Istio ambient, Crossplane, Kyverno, kube-prometheus-stack; Argo Events + Workflows with CloudEvents on NATS JetStream.

Migration path: the chart, golden DB image, E2E suite, CloudEvents contracts and resolver carry over; the resolver writes pins to the desired-state repo instead of running `helm upgrade`. Headroom, pre-pull, ARC and the golden image stay as they are.

---

## 10. Scope For The 24-Hour Submission

Must ship: FR-1 to FR-5, FR-6.1 to FR-6.3, FR-7.1 and FR-7.2, FR-8.1 and FR-8.2, FR-4.9 teardown, NFR-5.1 to NFR-5.4, the demo, measured timings in the README.

Ship if time allows: oauth2-proxy login for preview URLs (Section 4a), ARC runners (fall back to GitHub-hosted runners with OIDC), pre-pull DaemonSet, KEDA schedules, CloudEvents, dashboard, Tilt, sweeper.

Written up only: Section 9.

---

## 11. Open Questions And Risks

1. Domain and hosted zone for `*.preview.<domain>`.
2. EKS bootstrap takes about 15-20 minutes; create the cluster first and keep it running through the build day.
3. Headroom sizing: too small misses NFR-1.5, too large wastes money. Start at 2 vents and tune from the dashboard.
4. Karpenter consolidation may repack nodes holding only headroom; confirm placeholders keep one spare node warm as intended.
5. In-cluster runners execute repository code with ECR push rights; restrict them to non-fork events in the two service repos and `caldera-platform`.
6. Golden image size must stay under the cap as fixtures grow.
7. If ARC is not ready in time, GitHub-hosted runners add runner start-up and cold-cache time; measure and report both.
8. Cilium bootstrap order: the cluster must be created without the VPC CNI and Cilium installed before nodes join, or nodes start with the wrong CNI. Verify EKS Pod Identity (link-local agent address) and the AWS Load Balancer Controller work with kube-proxy replacement on day one.
9. Check the Cilium release against the EKS Kubernetes version before every cluster upgrade.
10. Cilium and Envoy Gateway together: disable Cilium's Gateway API and Ingress controllers so only Envoy Gateway programs `Gateway` resources, and confirm proxy protocol v2 end to end (NLB target group attribute + `ClientTrafficPolicy`) before enabling the optional IP allowlist.
11. Previews are open by default. If oauth2-proxy does not ship, preview URLs are public for the demo; the golden dataset must stay free of anything sensitive, and the locked-down mode is one value flip away.
12. Private EKS endpoint (EKS1) means only in-VPC callers reach the API: ARC runners and the CDK kubectl handler work; the GitHub-hosted runner fallback in Section 10 and laptop `caldera vent up` against EKS need SSM port forwarding. If ARC slips, either accept EKS1 as a documented risk for the demo or run deploy jobs through SSM.
13. SSM `SecureString` parameters cannot be created by CloudFormation, so `task secrets:put` writes them; the sweeper deletes everything under `/prismatic/` on destroy, so they must be written again after each fresh deploy.
