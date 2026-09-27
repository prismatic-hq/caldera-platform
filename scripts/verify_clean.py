import argparse
import sys
from collections.abc import Iterable

import boto3

RESOURCE_TYPES = ["ec2", "elasticloadbalancing", "route53", "ecr", "logs", "eks"]
CLUSTER_VALUE_TAGS = ("elbv2.k8s.aws/cluster", "karpenter.sh/discovery")
PLATFORM_TAG_PREFIX = "prismatic:"


def is_platform_tag(key: str, value: str, cluster: str) -> bool:
    return (
        key.startswith(PLATFORM_TAG_PREFIX)
        or key == f"kubernetes.io/cluster/{cluster}"
        or (key in CLUSTER_VALUE_TAGS and value == cluster)
    )


def tagged_leftovers(tagging, cluster: str) -> list[str]:
    pages = tagging.get_paginator("get_resources").paginate(ResourceTypeFilters=RESOURCE_TYPES)
    return [
        resource["ResourceARN"]
        for page in pages
        for resource in page["ResourceTagMappingList"]
        if any(is_platform_tag(t["Key"], t["Value"], cluster) for t in resource.get("Tags", []))
    ]


def log_group_leftovers(logs, cluster: str) -> list[str]:
    prefixes = (f"/aws/eks/{cluster}/", f"/aws/containerinsights/{cluster}/")
    return [
        group["logGroupName"]
        for prefix in prefixes
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix)
        for group in page["logGroups"]
    ]


def cluster_leftovers(eks, cluster: str) -> list[str]:
    return [f"eks cluster {cluster}"] if cluster in eks.list_clusters()["clusters"] else []


def leftovers(tagging, logs, eks, cluster: str) -> list[str]:
    return sorted(
        set(tagged_leftovers(tagging, cluster))
        | set(log_group_leftovers(logs, cluster))
        | set(cluster_leftovers(eks, cluster))
    )


def report(found: Iterable[str]) -> int:
    found = list(found)
    for item in found:
        print(f"leftover: {item}")
    print(f"{len(found)} leftover resource(s)")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if platform resources remain after destroy")
    parser.add_argument("--cluster-name", default="caldera")
    parser.add_argument("--region")
    args = parser.parse_args(argv)
    session = boto3.Session(region_name=args.region)
    return report(
        leftovers(
            session.client("resourcegroupstaggingapi"),
            session.client("logs"),
            session.client("eks"),
            args.cluster_name,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
