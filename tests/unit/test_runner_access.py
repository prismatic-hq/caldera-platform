import pytest

from caldera.runner_access import DEPLOYER_ROLE, manifests
from caldera.stacks.ci_access import RUNNER_NAMESPACE, RUNNER_SERVICE_ACCOUNT
from preview_cli.access import DEPLOYER_NAMESPACE, DEPLOYER_SERVICE_ACCOUNT, NAMESPACE_ROLE


def by_kind(kind: str) -> list[dict]:
    return [doc for doc in manifests() if doc["kind"] == kind]


def cluster_role(name: str) -> dict:
    return next(role for role in by_kind("ClusterRole") if role["metadata"]["name"] == name)


def granted(role: dict, resource: str) -> set[str]:
    return {
        verb for rule in role["rules"] if resource in rule["resources"] for verb in rule["verbs"]
    }


def test_cli_and_cdk_agree_on_the_runner_identity() -> None:
    assert (DEPLOYER_NAMESPACE, DEPLOYER_SERVICE_ACCOUNT) == (
        RUNNER_NAMESPACE,
        RUNNER_SERVICE_ACCOUNT,
    )


@pytest.mark.parametrize("resource", ["secrets", "configmaps", "pods", "serviceaccounts"])
def test_cluster_wide_role_grants_nothing_inside_namespaces(resource: str) -> None:
    assert granted(cluster_role(DEPLOYER_ROLE), resource) == set()


def test_cluster_wide_role_manages_namespaces_and_binds_only_the_namespace_admin_role() -> None:
    deployer = cluster_role(DEPLOYER_ROLE)
    bind = next(rule for rule in deployer["rules"] if "bind" in rule["verbs"])

    assert granted(deployer, "namespaces") == {"get", "list", "watch", "create", "patch", "delete"}
    assert granted(deployer, "rolebindings") == {"get", "create", "patch"}
    assert bind == {
        "apiGroups": ["rbac.authorization.k8s.io"],
        "resources": ["clusterroles"],
        "verbs": ["bind"],
        "resourceNames": [NAMESPACE_ROLE],
    }


def test_namespace_admin_role_is_never_bound_cluster_wide() -> None:
    bound = {binding["roleRef"]["name"] for binding in by_kind("ClusterRoleBinding")}

    assert bound == {DEPLOYER_ROLE}
    assert "secrets" in {
        r for rule in cluster_role(NAMESPACE_ROLE)["rules"] for r in rule["resources"]
    }
    assert not [
        rule
        for rule in cluster_role(NAMESPACE_ROLE)["rules"]
        if "rbac.authorization.k8s.io" in rule["apiGroups"]
    ]


def test_admission_policy_confines_the_runner_to_preview_namespaces() -> None:
    (policy,) = by_kind("ValidatingAdmissionPolicy")
    (binding,) = by_kind("ValidatingAdmissionPolicyBinding")
    expressions = " ".join(v["expression"] for v in policy["spec"]["validations"])

    assert policy["spec"]["failurePolicy"] == "Fail"
    assert policy["spec"]["matchConditions"][0]["expression"] == (
        "request.userInfo.username == 'system:serviceaccount:arc-runners:arc-runner'"
    )
    assert "startsWith('preview-')" in expressions
    assert NAMESPACE_ROLE in expressions
    assert "pod-security.kubernetes.io/enforce" in expressions
    assert binding["spec"] == {
        "policyName": policy["metadata"]["name"],
        "validationActions": ["Deny"],
    }


@pytest.mark.parametrize(
    ("resource", "verbs"),
    [
        ("jobs", {"create", "get", "watch", "delete"}),
        ("pods", {"create", "get", "list", "watch", "delete"}),
        ("pods/log", {"get"}),
        ("deployments", {"create", "patch", "get", "watch", "delete"}),
        ("secrets", {"create", "get", "list", "patch", "delete"}),
        ("services", {"create", "patch", "delete"}),
        ("networkpolicies", {"create", "patch", "delete"}),
        ("httproutes", {"create", "patch", "delete"}),
        ("events", {"list", "watch"}),
    ],
)
def test_namespace_admin_covers_release_hooks_and_helm_test_logs(
    resource: str, verbs: set[str]
) -> None:
    assert verbs <= granted(cluster_role(NAMESPACE_ROLE), resource)
