import subprocess
from pathlib import Path

import pytest

from scripts.kube_connect import (
    CONTEXT,
    Endpoint,
    KubeConnectError,
    merge_kubeconfig,
    parse_args,
    pick_node,
    use_context,
)

ENDPOINT = Endpoint(
    cluster="caldera",
    region="us-east-2",
    host="ABC123.gr7.us-east-2.eks.amazonaws.com",
    certificate_authority="Q0FEQVRB",
    role_arn="arn:aws:iam::111122223333:role/CalderaCluster-ClusterAdminRole-XYZ",
)


def test_kubeconfig_points_at_the_local_tunnel_and_keeps_tls_verification() -> None:
    config = merge_kubeconfig({}, ENDPOINT, port=8443)

    [cluster] = config["clusters"]
    assert cluster["name"] == CONTEXT
    assert cluster["cluster"] == {
        "server": "https://localhost:8443",
        "tls-server-name": ENDPOINT.host,
        "certificate-authority-data": ENDPOINT.certificate_authority,
    }
    assert config["contexts"] == [
        {"name": CONTEXT, "context": {"cluster": CONTEXT, "user": CONTEXT}}
    ]


def test_kubeconfig_assumes_the_cluster_admin_role_for_tokens() -> None:
    [user] = merge_kubeconfig({}, ENDPOINT, port=8443)["users"]

    exec_config = user["user"]["exec"]
    assert exec_config["command"] == "aws"
    assert exec_config["args"] == [
        "eks",
        "get-token",
        "--cluster-name",
        "caldera",
        "--role-arn",
        ENDPOINT.role_arn,
        "--region",
        "us-east-2",
    ]


def test_kubeconfig_merge_replaces_only_its_own_entries() -> None:
    existing = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": "kind-caldera",
        "clusters": [
            {"name": "kind-caldera", "cluster": {"server": "https://127.0.0.1:6443"}},
            {"name": CONTEXT, "cluster": {"server": "https://stale"}},
        ],
        "contexts": [{"name": "kind-caldera", "context": {"cluster": "kind-caldera"}}],
        "users": [{"name": "kind-caldera", "user": {}}],
    }

    config = merge_kubeconfig(existing, ENDPOINT, port=9443)

    assert [c["name"] for c in config["clusters"]] == ["kind-caldera", CONTEXT]
    assert config["clusters"][1]["cluster"]["server"] == "https://localhost:9443"
    assert [c["name"] for c in config["contexts"]] == ["kind-caldera", CONTEXT]
    assert [u["name"] for u in config["users"]] == ["kind-caldera", CONTEXT]
    assert config["current-context"] == "kind-caldera"


def reservation(*instances: dict) -> dict:
    return {"Reservations": [{"Instances": list(instances)}]}


def instance(instance_id: str, launched: str) -> dict:
    return {"InstanceId": instance_id, "LaunchTime": launched}


def test_pick_node_prefers_the_most_recently_launched_instance() -> None:
    described = reservation(
        instance("i-old", "2026-09-27T09:00:00+00:00"),
        instance("i-new", "2026-09-27T10:00:00+00:00"),
    )
    assert pick_node(described, "caldera") == "i-new"


def test_pick_node_fails_with_an_actionable_message_when_no_node_runs() -> None:
    with pytest.raises(KubeConnectError, match="no running system node.*caldera"):
        pick_node({"Reservations": []}, "caldera")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        pytest.param(["--port", "0"], "port", id="port too low"),
        pytest.param(["--port", "70000"], "port", id="port too high"),
        pytest.param(["--cluster", "Bad Name"], "cluster", id="invalid cluster name"),
    ],
)
def test_parse_args_rejects_invalid_input(argv: list[str], message: str) -> None:
    with pytest.raises(KubeConnectError, match=message):
        parse_args(argv)


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert (args.cluster, args.stack, args.port) == ("caldera", "CalderaCluster", 8443)


def test_use_context_switches_kubectx_in_the_written_kubeconfig(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "scripts.kube_connect.subprocess.run", lambda args, **kwargs: calls.append((args, kwargs))
    )

    use_context(Path("/tmp/kubeconfig"))

    [(args, kwargs)] = calls
    assert args == ["kubectx", CONTEXT]
    assert kwargs["env"]["KUBECONFIG"] == "/tmp/kubeconfig"
    assert kwargs["check"] is True


def test_use_context_failure_is_actionable(monkeypatch) -> None:
    def failed(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr="error: no context exists\n")

    monkeypatch.setattr("scripts.kube_connect.subprocess.run", failed)

    with pytest.raises(KubeConnectError, match="kubectx caldera failed: error: no context exists"):
        use_context(Path("/tmp/kubeconfig"))
