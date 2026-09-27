"""Tunnel to the private EKS API through a system node and switch kubectl to context `caldera`.

Any principal that may assume the ClusterAdminRole output by the cluster stack can connect.
The tunnel runs in the foreground; use `kubectl ...` from another terminal.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import BotoCoreError, ClientError

CONTEXT = "caldera"
CLUSTER_NAME = re.compile(r"^[0-9A-Za-z][A-Za-z0-9_-]{0,99}$")
SYSTEM_NODE_LABEL = ("prismatic.dev/node-role", "system")
DEFAULT_KUBECONFIG = Path.home() / ".kube" / "config"
REQUIRED_TOOLS = {
    "aws": "install the AWS CLI v2",
    "session-manager-plugin": "install it: brew install --cask session-manager-plugin",
    "kubectx": "run mise install in this repo",
}


class KubeConnectError(Exception):
    pass


@dataclass(frozen=True)
class Endpoint:
    cluster: str
    region: str
    host: str
    certificate_authority: str
    role_arn: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cluster", default="caldera")
    parser.add_argument("--stack", default="CalderaCluster")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--region")
    parser.add_argument(
        "--kubeconfig", type=Path, default=Path(os.getenv("KUBECONFIG") or DEFAULT_KUBECONFIG)
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise KubeConnectError(f"--port must be between 1 and 65535, got {args.port}")
    if not CLUSTER_NAME.match(args.cluster):
        raise KubeConnectError(f"--cluster is not a valid EKS cluster name: {args.cluster!r}")
    return args


def _replace(entries: list[dict], entry: dict) -> list[dict]:
    return [e for e in entries if e.get("name") != entry["name"]] + [entry]


def merge_kubeconfig(existing: dict, endpoint: Endpoint, port: int) -> dict:
    cluster = {
        "name": CONTEXT,
        "cluster": {
            "server": f"https://localhost:{port}",
            "tls-server-name": endpoint.host,
            "certificate-authority-data": endpoint.certificate_authority,
        },
    }
    user = {
        "name": CONTEXT,
        "user": {
            "exec": {
                "apiVersion": "client.authentication.k8s.io/v1beta1",
                "command": "aws",
                "args": [
                    "eks",
                    "get-token",
                    "--cluster-name",
                    endpoint.cluster,
                    "--role-arn",
                    endpoint.role_arn,
                    "--region",
                    endpoint.region,
                ],
                "interactiveMode": "Never",
            }
        },
    }
    context = {"name": CONTEXT, "context": {"cluster": CONTEXT, "user": CONTEXT}}
    config = {"apiVersion": "v1", "kind": "Config", **existing}
    config["clusters"] = _replace(config.get("clusters") or [], cluster)
    config["users"] = _replace(config.get("users") or [], user)
    config["contexts"] = _replace(config.get("contexts") or [], context)
    return config


def pick_node(described: dict, cluster: str) -> str:
    instances = [i for r in described.get("Reservations", []) for i in r.get("Instances", [])]
    if not instances:
        raise KubeConnectError(
            f"no running system node found for cluster {cluster}; "
            "check the system node group in the EKS console"
        )
    return max(instances, key=lambda i: i["LaunchTime"])["InstanceId"]


def require_tools() -> None:
    missing = [
        f"{tool} ({hint})" for tool, hint in REQUIRED_TOOLS.items() if not shutil.which(tool)
    ]
    if missing:
        raise KubeConnectError(f"missing required tools: {'; '.join(missing)}")


def stack_output(cloudformation, stack: str, key: str) -> str:
    try:
        [described] = cloudformation.describe_stacks(StackName=stack)["Stacks"]
    except ClientError as error:
        raise KubeConnectError(f"cannot read stack {stack}: {error}") from error
    outputs = {o["OutputKey"]: o["OutputValue"] for o in described.get("Outputs", [])}
    if key not in outputs:
        raise KubeConnectError(
            f"stack {stack} has no {key} output; deploy it from a version with cluster admin access"
        )
    return outputs[key]


def check_assumable(sts, role_arn: str) -> None:
    try:
        sts.assume_role(RoleArn=role_arn, RoleSessionName="kube-connect", DurationSeconds=900)
    except ClientError as error:
        raise KubeConnectError(
            f"cannot assume {role_arn}: {error.response['Error']['Message']}. Allow "
            "sts:AssumeRole on it for your principal, or list your principal in "
            "clusterAdminPrincipals and redeploy the cluster stack"
        ) from error


def system_node(session: boto3.Session, cluster: str) -> str:
    eks = session.client("eks")
    nodegroups = [
        name
        for name in eks.list_nodegroups(clusterName=cluster)["nodegroups"]
        if eks.describe_nodegroup(clusterName=cluster, nodegroupName=name)["nodegroup"]
        .get("labels", {})
        .get(SYSTEM_NODE_LABEL[0])
        == SYSTEM_NODE_LABEL[1]
    ]
    if not nodegroups:
        raise KubeConnectError(f"cluster {cluster} has no node group labelled {SYSTEM_NODE_LABEL}")
    described = session.client("ec2").describe_instances(
        Filters=[
            {"Name": "tag:eks:cluster-name", "Values": [cluster]},
            {"Name": "tag:eks:nodegroup-name", "Values": nodegroups},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    return pick_node(described, cluster)


def resolve_endpoint(session: boto3.Session, args: argparse.Namespace) -> Endpoint:
    region = session.region_name
    if not region:
        raise KubeConnectError("no AWS region configured: set AWS_REGION or pass --region")
    role_arn = stack_output(session.client("cloudformation"), args.stack, "ClusterAdminRoleArn")
    check_assumable(session.client("sts"), role_arn)
    cluster = session.client("eks").describe_cluster(name=args.cluster)["cluster"]
    return Endpoint(
        cluster=args.cluster,
        region=region,
        host=cluster["endpoint"].removeprefix("https://"),
        certificate_authority=cluster["certificateAuthority"]["data"],
        role_arn=role_arn,
    )


def write_kubeconfig(path: Path, endpoint: Endpoint, port: int) -> None:
    existing = yaml.safe_load(path.read_text()) if path.is_file() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(merge_kubeconfig(existing or {}, endpoint, port)))
    path.chmod(0o600)


def use_context(kubeconfig: Path) -> None:
    try:
        subprocess.run(
            ["kubectx", CONTEXT],
            env={**os.environ, "KUBECONFIG": str(kubeconfig)},
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip() or f"exit code {error.returncode}"
        raise KubeConnectError(f"kubectx {CONTEXT} failed: {detail}") from error


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        require_tools()
        session = boto3.Session(region_name=args.region)
        endpoint = resolve_endpoint(session, args)
        node = system_node(session, args.cluster)
        write_kubeconfig(args.kubeconfig, endpoint, args.port)
        use_context(args.kubeconfig)
    except KubeConnectError as error:
        print(f"kube-connect: {error}", file=sys.stderr)
        return 2
    except (BotoCoreError, ClientError) as error:
        print(f"kube-connect: AWS call failed: {error}", file=sys.stderr)
        return 1
    print(f"context {CONTEXT} -> localhost:{args.port} via {node}; Ctrl-C closes the tunnel")
    print("in another terminal: kubectl get nodes")
    session_parameters = f"host={endpoint.host},portNumber=443,localPortNumber={args.port}"
    return subprocess.run(
        [
            "aws",
            "ssm",
            "start-session",
            "--region",
            endpoint.region,
            "--target",
            node,
            "--document-name",
            "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters",
            session_parameters,
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
