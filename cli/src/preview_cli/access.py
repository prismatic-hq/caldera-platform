DEPLOYER_NAMESPACE = "arc-runners"
DEPLOYER_SERVICE_ACCOUNT = "arc-runner"
NAMESPACE_ROLE = "preview-namespace-admin"
ENVIRONMENT_KIND_LABEL = "prismatic.dev/environment-kind"
POD_SECURITY_LABEL = "pod-security.kubernetes.io/enforce"


def namespace_manifests(environment: str) -> list[dict]:
    """The preview namespace plus the RoleBinding that is the runner's only access inside it."""
    name = f"preview-{environment}"
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": {
                ENVIRONMENT_KIND_LABEL: "preview",
                POD_SECURITY_LABEL: "baseline",
                "pod-security.kubernetes.io/warn": "restricted",
            },
        },
    }
    binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": NAMESPACE_ROLE, "namespace": name},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": NAMESPACE_ROLE,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": DEPLOYER_SERVICE_ACCOUNT,
                "namespace": DEPLOYER_NAMESPACE,
            }
        ],
    }
    return [namespace, binding]
