from dataclasses import dataclass, field

from botocore.stub import ANY

import drainer

CLUSTER = "caldera"
VPC = "vpc-1"
ZONE = "Z123"
PROPERTIES = {
    "ClusterName": CLUSTER,
    "VpcId": VPC,
    "HostedZoneId": ZONE,
    "RecordNames": ["*.preview.prismatic.dev", "*.dev.prismatic.dev"],
}


@dataclass
class FakeKubernetes:
    objects: dict[str, list[dict]]
    deleted: list[str] = field(default_factory=list)

    def items(self, path: str) -> list[dict]:
        return self.objects.get(path, [])

    def delete(self, path: str) -> None:
        self.deleted.append(path)


def named(name: str, namespace: str | None = None, **spec: object) -> dict:
    metadata = {"name": name, **({"namespace": namespace} if namespace else {})}
    return {"metadata": metadata, "spec": spec}


def cluster_objects() -> dict[str, list[dict]]:
    return {
        "/api/v1/namespaces": [named("preview-quake-alerts"), named("kube-system")],
        drainer.GATEWAYS: [named("preview", "envoy-gateway-system")],
        "/api/v1/services": [
            named("envoy-preview", "envoy-gateway-system", type="LoadBalancer"),
            named("kube-dns", "kube-system", type="ClusterIP"),
        ],
        "/api/v1/persistentvolumeclaims": [],
        drainer.NODEPOOLS: [named("preview-environments")],
        drainer.NODECLAIMS: [named("preview-environments-x7k2p")],
    }


def stub_no_load_balancers(stub) -> None:
    stub.add_response("describe_load_balancers", {"LoadBalancers": []})


def stub_records(stub, records: list[dict]) -> None:
    stub.add_response(
        "list_resource_record_sets",
        {"ResourceRecordSets": records, "IsTruncated": False, "MaxItems": "300"},
        {"HostedZoneId": ZONE},
    )


def clients(kube: FakeKubernetes, stubbed: dict) -> dict:
    return {"kube": kube, **{name: stubbed[name][0] for name in ("ec2", "elbv2", "route53")}}


def test_first_phase_waits_for_dns_records_and_keeps_node_pools(stubbed) -> None:
    kube = FakeKubernetes(cluster_objects())
    stub_no_load_balancers(stubbed["elbv2"][1])
    stub_records(
        stubbed["route53"][1],
        [
            {
                "Name": "\\052.preview.prismatic.dev.",
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": "1.2.3.4"}],
            }
        ],
    )

    left = drainer.cleanup(PROPERTIES, clients(kube, stubbed))

    assert left == ["A \\052.preview.prismatic.dev."]
    assert kube.deleted == [
        "/api/v1/namespaces/preview-quake-alerts",
        "/apis/gateway.networking.k8s.io/v1/namespaces/envoy-gateway-system/gateways/preview",
        "/api/v1/namespaces/envoy-gateway-system/services/envoy-preview",
    ]


def test_second_phase_deletes_node_pools_and_waits_for_instances(stubbed) -> None:
    kube = FakeKubernetes(cluster_objects())
    stub_no_load_balancers(stubbed["elbv2"][1])
    stub_records(stubbed["route53"][1], [])
    stubbed["ec2"][1].add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [{"InstanceId": "i-karpenter"}]}]},
        {"Filters": ANY},
    )

    left = drainer.cleanup(PROPERTIES, clients(kube, stubbed))

    assert left == ["instance i-karpenter"]
    assert kube.deleted[-2:] == [
        f"{drainer.NODEPOOLS}/preview-environments",
        f"{drainer.NODECLAIMS}/preview-environments-x7k2p",
    ]
