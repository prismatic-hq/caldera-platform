import pytest

from caldera import runner_access
from caldera.runner_access import RUNNER_USERNAME
from preview_cli.access import DEPLOYER_NAMESPACE, DEPLOYER_SERVICE_ACCOUNT, namespace_manifests

GITHUB_APP_SECRET = "github-app"
BYSTANDER = "bystander"


def must(result) -> str:
    assert result.returncode == 0, result.stderr
    return result.stdout


def denied(result, reason: str) -> None:
    assert result.returncode != 0, result.stdout
    assert reason in result.stderr, result.stderr


@pytest.fixture(scope="module")
def runner(kubectl):
    must(
        kubectl(
            "apply",
            "-f",
            "-",
            documents=[
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": DEPLOYER_NAMESPACE}},
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": BYSTANDER}},
                {
                    "apiVersion": "v1",
                    "kind": "ServiceAccount",
                    "metadata": {"name": DEPLOYER_SERVICE_ACCOUNT, "namespace": DEPLOYER_NAMESPACE},
                },
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": GITHUB_APP_SECRET, "namespace": DEPLOYER_NAMESPACE},
                    "stringData": {"github_app_private_key": "not-a-real-key"},
                },
                *runner_access.manifests(),
            ],
        )
    )
    yield lambda *args, documents=None: kubectl(*args, documents=documents, as_user=RUNNER_USERNAME)
    kubectl("delete", "namespace", "preview-kind-check", "--ignore-not-found", "--wait=true")


@pytest.mark.parametrize("namespace", [DEPLOYER_NAMESPACE, "kube-system", "default"])
def test_runner_cannot_read_secrets_outside_preview_namespaces(runner, namespace: str) -> None:
    assert runner("auth", "can-i", "get", "secrets", "-n", namespace).stdout.strip() == "no"
    assert runner("auth", "can-i", "list", "secrets", "-A").stdout.strip() == "no"


def test_runner_cannot_read_the_github_app_key(runner) -> None:
    denied(runner("get", "secret", GITHUB_APP_SECRET, "-n", DEPLOYER_NAMESPACE), "forbidden")


def test_runner_gets_namespace_access_only_through_its_preview_namespace(runner) -> None:
    must(runner("apply", "-f", "-", documents=namespace_manifests("kind-check")))

    assert runner("auth", "can-i", "get", "secrets", "-n", "preview-kind-check").stdout.strip() == (
        "yes"
    )
    must(runner("create", "secret", "generic", "probe", "-n", "preview-kind-check"))


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (("create", "namespace", "evil"), "may only change preview-* namespaces"),
        (("create", "namespace", "preview-no-pod-security"), "must set pod-security"),
        (("delete", "namespace", BYSTANDER), "may only change preview-* namespaces"),
        (
            (
                "create",
                "rolebinding",
                "grab",
                "-n",
                "kube-system",
                "--clusterrole",
                "preview-namespace-admin",
                f"--serviceaccount={DEPLOYER_NAMESPACE}:{DEPLOYER_SERVICE_ACCOUNT}",
            ),
            "may only change preview-* namespaces",
        ),
        (
            (
                "create",
                "rolebinding",
                "grab",
                "-n",
                "preview-kind-check",
                "--clusterrole",
                "preview-namespace-admin",
                "--serviceaccount=default:default",
            ),
            "may only bind preview-namespace-admin to itself",
        ),
        (
            (
                "create",
                "rolebinding",
                "grab",
                "-n",
                "preview-kind-check",
                "--clusterrole",
                "cluster-admin",
                f"--serviceaccount={DEPLOYER_NAMESPACE}:{DEPLOYER_SERVICE_ACCOUNT}",
            ),
            "forbidden",
        ),
    ],
)
def test_runner_cannot_escape_preview_namespaces(
    runner, args: tuple[str, ...], reason: str
) -> None:
    must(runner("apply", "-f", "-", documents=namespace_manifests("kind-check")))

    denied(runner(*args), reason)
