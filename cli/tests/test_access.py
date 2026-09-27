from preview_cli.access import (
    DEPLOYER_NAMESPACE,
    DEPLOYER_SERVICE_ACCOUNT,
    ENVIRONMENT_KIND_LABEL,
    NAMESPACE_ROLE,
    POD_SECURITY_LABEL,
    namespace_manifests,
)


def test_namespace_is_labelled_as_a_preview_environment_with_pod_security() -> None:
    namespace, _ = namespace_manifests("quake-alerts")

    assert namespace["metadata"]["name"] == "preview-quake-alerts"
    assert namespace["metadata"]["labels"] == {
        ENVIRONMENT_KIND_LABEL: "preview",
        POD_SECURITY_LABEL: "baseline",
        "pod-security.kubernetes.io/warn": "restricted",
    }


def test_role_binding_grants_the_namespace_admin_role_to_the_runner_only() -> None:
    _, binding = namespace_manifests("quake-alerts")

    assert binding["metadata"] == {"name": NAMESPACE_ROLE, "namespace": "preview-quake-alerts"}
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": NAMESPACE_ROLE,
    }
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": DEPLOYER_SERVICE_ACCOUNT,
            "namespace": DEPLOYER_NAMESPACE,
        }
    ]
