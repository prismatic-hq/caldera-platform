"""RBAC and admission policy that confine the ARC runners to preview-* namespaces."""

from preview_cli.access import (
    DEPLOYER_NAMESPACE,
    DEPLOYER_SERVICE_ACCOUNT,
    LOCK_NAMESPACE,
    NAMESPACE_ROLE,
    POD_SECURITY_LABEL,
)

DEPLOYER_ROLE = "preview-deployer"
LOCK_ROLE = "preview-locks"
SCOPE_POLICY = "preview-deployer-scope"
RBAC_GROUP = "rbac.authorization.k8s.io"
RUNNER_USERNAME = f"system:serviceaccount:{DEPLOYER_NAMESPACE}:{DEPLOYER_SERVICE_ACCOUNT}"
ALL_VERBS = ["get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"]
NAMESPACED_RESOURCES = {
    "": [
        "pods",
        "pods/log",
        "services",
        "secrets",
        "configmaps",
        "serviceaccounts",
        "events",
    ],
    "apps": ["deployments", "statefulsets", "replicasets"],
    "batch": ["jobs"],
    "networking.k8s.io": ["networkpolicies"],
    "gateway.networking.k8s.io": ["httproutes"],
    "cilium.io": ["ciliumnetworkpolicies"],
}


def _cluster_role(name: str, rules: list[dict]) -> dict:
    return {
        "apiVersion": f"{RBAC_GROUP}/v1",
        "kind": "ClusterRole",
        "metadata": {"name": name},
        "rules": rules,
    }


def deployer_role() -> dict:
    return _cluster_role(
        DEPLOYER_ROLE,
        [
            {
                "apiGroups": [""],
                "resources": ["namespaces"],
                "verbs": ["get", "list", "watch", "create", "patch", "delete"],
            },
            {
                "apiGroups": [RBAC_GROUP],
                "resources": ["rolebindings"],
                "verbs": ["get", "create", "patch"],
            },
            {
                "apiGroups": [RBAC_GROUP],
                "resources": ["clusterroles"],
                "verbs": ["bind"],
                "resourceNames": [NAMESPACE_ROLE],
            },
        ],
    )


def namespace_admin_role() -> dict:
    return _cluster_role(
        NAMESPACE_ROLE,
        [
            {"apiGroups": [group], "resources": resources, "verbs": ALL_VERBS}
            for group, resources in NAMESPACED_RESOURCES.items()
        ],
    )


RUNNER_SUBJECTS = [
    {"kind": "ServiceAccount", "name": DEPLOYER_SERVICE_ACCOUNT, "namespace": DEPLOYER_NAMESPACE}
]


def deployer_binding() -> dict:
    return {
        "apiVersion": f"{RBAC_GROUP}/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": DEPLOYER_ROLE},
        "roleRef": {"apiGroup": RBAC_GROUP, "kind": "ClusterRole", "name": DEPLOYER_ROLE},
        "subjects": RUNNER_SUBJECTS,
    }


def lock_access() -> list[dict]:
    """Leases that serialize deploys of one preview environment across repositories."""
    metadata = {"name": LOCK_ROLE, "namespace": LOCK_NAMESPACE}
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": LOCK_NAMESPACE}},
        {
            "apiVersion": f"{RBAC_GROUP}/v1",
            "kind": "Role",
            "metadata": metadata,
            "rules": [
                {
                    "apiGroups": ["coordination.k8s.io"],
                    "resources": ["leases"],
                    "verbs": ["get", "create", "update", "delete"],
                }
            ],
        },
        {
            "apiVersion": f"{RBAC_GROUP}/v1",
            "kind": "RoleBinding",
            "metadata": metadata,
            "roleRef": {"apiGroup": RBAC_GROUP, "kind": "Role", "name": LOCK_ROLE},
            "subjects": RUNNER_SUBJECTS,
        },
    ]


def scope_policy() -> list[dict]:
    """RBAC cannot scope creates by name, so admission limits the runner to preview-* objects."""
    writes = ["CREATE", "UPDATE", "DELETE"]
    runner_subject = (
        f"s.kind == 'ServiceAccount' && s.name == '{DEPLOYER_SERVICE_ACCOUNT}'"
        f" && s.namespace == '{DEPLOYER_NAMESPACE}'"
    )
    policy = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicy",
        "metadata": {"name": SCOPE_POLICY},
        "spec": {
            "failurePolicy": "Fail",
            "matchConstraints": {
                "resourceRules": [
                    {
                        "apiGroups": [""],
                        "apiVersions": ["v1"],
                        "operations": writes,
                        "resources": ["namespaces"],
                    },
                    {
                        "apiGroups": [RBAC_GROUP],
                        "apiVersions": ["v1"],
                        "operations": writes,
                        "resources": ["rolebindings"],
                    },
                ]
            },
            "matchConditions": [
                {
                    "name": "runner",
                    "expression": f"request.userInfo.username == '{RUNNER_USERNAME}'",
                }
            ],
            "variables": [
                {
                    "name": "isNamespace",
                    "expression": "request.resource.resource == 'namespaces'",
                },
                {
                    "name": "target",
                    "expression": (
                        "!variables.isNamespace ? request.namespace"
                        " : request.operation == 'DELETE' ? oldObject.metadata.name"
                        " : object.metadata.name"
                    ),
                },
            ],
            "validations": [
                {
                    "expression": "variables.target.startsWith('preview-')",
                    "messageExpression": (
                        "'the preview deployer may only change preview-* namespaces, not '"
                        " + variables.target"
                    ),
                },
                {
                    "expression": (
                        "!variables.isNamespace || request.operation == 'DELETE' || ("
                        "has(object.metadata.labels)"
                        f" && '{POD_SECURITY_LABEL}' in object.metadata.labels"
                        f" && object.metadata.labels['{POD_SECURITY_LABEL}']"
                        " in ['baseline', 'restricted'])"
                    ),
                    "message": (
                        f"preview namespaces must set {POD_SECURITY_LABEL}"
                        " to baseline or restricted"
                    ),
                },
                {
                    "expression": (
                        "variables.isNamespace || request.operation == 'DELETE' || ("
                        "object.roleRef.kind == 'ClusterRole'"
                        f" && object.roleRef.name == '{NAMESPACE_ROLE}'"
                        f" && object.subjects.all(s, {runner_subject}))"
                    ),
                    "message": f"the preview deployer may only bind {NAMESPACE_ROLE} to itself",
                },
            ],
        },
    }
    binding = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicyBinding",
        "metadata": {"name": SCOPE_POLICY},
        "spec": {"policyName": SCOPE_POLICY, "validationActions": ["Deny"]},
    }
    return [policy, binding]


def manifests() -> list[dict]:
    return [
        deployer_role(),
        deployer_binding(),
        namespace_admin_role(),
        *scope_policy(),
        *lock_access(),
    ]
