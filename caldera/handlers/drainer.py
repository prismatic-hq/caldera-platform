"""Removes everything in-cluster controllers created in AWS, while the controllers still run.

Phase 1 deletes preview namespaces, Gateways, LoadBalancer Services and PVCs, then waits for
the Load Balancer Controller and external-dns to remove the NLB and DNS records. Phase 2
deletes Karpenter NodePools and NodeClaims and waits for the instances to terminate.
"""

import base64
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.signers import RequestSigner

import cfn
from network_sweeper import LIVE_INSTANCE_STATES, tagged_elbv2_arns

TOKEN_PREFIX = "k8s-aws-v1."
PREVIEW_PREFIX = "preview-"
NODEPOOLS = "/apis/karpenter.sh/v1/nodepools"
NODECLAIMS = "/apis/karpenter.sh/v1/nodeclaims"
GATEWAYS = "/apis/gateway.networking.k8s.io/v1/gateways"


def eks_token(cluster: str, region: str) -> str:
    session = boto3.session.Session()
    sts = session.client("sts", region_name=region)
    signer = RequestSigner(
        sts.meta.service_model.service_id,
        region,
        "sts",
        "v4",
        session.get_credentials(),
        session.events,
    )
    url = signer.generate_presigned_url(
        {
            "method": "GET",
            "url": f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
            "body": {},
            "headers": {"x-k8s-aws-id": cluster},
            "context": {},
        },
        region_name=region,
        expires_in=60,
        operation_name="",
    )
    return TOKEN_PREFIX + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


@dataclass
class Kubernetes:
    endpoint: str
    token: str
    context: ssl.SSLContext

    @classmethod
    def connect(cls, cluster: str, region: str) -> "Kubernetes":
        description = boto3.client("eks", region_name=region).describe_cluster(name=cluster)
        ca = base64.b64decode(description["cluster"]["certificateAuthority"]["data"]).decode()
        return cls(
            description["cluster"]["endpoint"],
            eks_token(cluster, region),
            ssl.create_default_context(cadata=ca),
        )

    def request(self, method: str, path: str) -> dict:
        request = urllib.request.Request(
            self.endpoint + path,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return {}
            raise RuntimeError(
                f"{method} {path}: HTTP {error.code} {error.read()[:300]!r}"
            ) from error

    def items(self, path: str) -> list[dict]:
        return self.request("GET", path).get("items", [])

    def delete(self, path: str) -> None:
        self.request("DELETE", path)


def _namespaced(kind_path: str, item: dict) -> str:
    metadata = item["metadata"]
    group_path, plural = kind_path.rsplit("/", 1)
    return f"{group_path}/namespaces/{metadata['namespace']}/{plural}/{metadata['name']}"


def delete_ingress_and_previews(kube: Kubernetes) -> None:
    for namespace in kube.items("/api/v1/namespaces"):
        if namespace["metadata"]["name"].startswith(PREVIEW_PREFIX):
            kube.delete(f"/api/v1/namespaces/{namespace['metadata']['name']}")
    for gateway in kube.items(GATEWAYS):
        kube.delete(_namespaced(GATEWAYS, gateway))
    for service in kube.items("/api/v1/services"):
        if service.get("spec", {}).get("type") == "LoadBalancer":
            kube.delete(_namespaced("/api/v1/services", service))
    for claim in kube.items("/api/v1/persistentvolumeclaims"):
        kube.delete(_namespaced("/api/v1/persistentvolumeclaims", claim))


def delete_node_capacity(kube: Kubernetes) -> None:
    for pool in kube.items(NODEPOOLS):
        kube.delete(f"{NODEPOOLS}/{pool['metadata']['name']}")
    for claim in kube.items(NODECLAIMS):
        kube.delete(f"{NODECLAIMS}/{claim['metadata']['name']}")


def remaining_load_balancers(elbv2: Any, vpc_id: str, cluster: str) -> list[str]:
    return [
        f"load balancer {arn}" for arn in tagged_elbv2_arns(elbv2, "load balancer", vpc_id, cluster)
    ]


def remaining_records(route53: Any, zone_id: str, names: list[str]) -> list[str]:
    wanted = {name.replace("*", "\\052").rstrip(".") + "." for name in names}
    paginator = route53.get_paginator("list_resource_record_sets")
    return [
        f"{record['Type']} {record['Name']}"
        for page in paginator.paginate(HostedZoneId=zone_id)
        for record in page["ResourceRecordSets"]
        if record["Type"] in ("A", "AAAA", "CNAME")
        and record["Name"].replace("*", "\\052") in wanted
    ]


def remaining_instances(ec2: Any, vpc_id: str, cluster: str) -> list[str]:
    filters = [
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": f"tag:kubernetes.io/cluster/{cluster}", "Values": ["owned"]},
        {"Name": "tag-key", "Values": ["karpenter.sh/nodepool"]},
        {"Name": "instance-state-name", "Values": LIVE_INSTANCE_STATES},
    ]
    return [
        f"instance {instance['InstanceId']}"
        for page in ec2.get_paginator("describe_instances").paginate(Filters=filters)
        for reservation in page["Reservations"]
        for instance in reservation["Instances"]
    ]


def cleanup(properties: dict[str, Any], clients: dict[str, Any] | None = None) -> list[str]:
    cluster, vpc_id = properties["ClusterName"], properties["VpcId"]
    clients = clients or {
        "kube": Kubernetes.connect(cluster, properties["Region"]),
        **{name: boto3.client(name) for name in ("ec2", "elbv2", "route53")},
    }
    kube = clients["kube"]
    delete_ingress_and_previews(kube)
    left = remaining_load_balancers(clients["elbv2"], vpc_id, cluster)
    left += remaining_records(
        clients["route53"], properties["HostedZoneId"], properties["RecordNames"]
    )
    if left:
        return left
    delete_node_capacity(kube)
    return remaining_instances(clients["ec2"], vpc_id, cluster)


def handler(event: dict, context: Any) -> None:
    cfn.run(event, context, cleanup)
