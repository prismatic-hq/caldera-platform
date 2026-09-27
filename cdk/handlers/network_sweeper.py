"""Deletes AWS resources that in-cluster controllers left in the VPC, before the VPC is deleted."""

from typing import Any

import boto3
from botocore.exceptions import ClientError

import cfn

LIVE_INSTANCE_STATES = ["pending", "running", "stopping", "stopped", "shutting-down"]
RETRYABLE_ERRORS = {"DependencyViolation", "ResourceInUse", "InvalidNetworkInterface.InUse"}


def _try_delete(label: str, delete: Any, **kwargs: Any) -> str | None:
    try:
        delete(**kwargs)
    except ClientError as error:
        if error.response["Error"]["Code"] not in RETRYABLE_ERRORS:
            raise
        return f"{label} ({error.response['Error']['Code']})"
    return None


def _cluster_tagged(tags: list[dict], cluster: str) -> bool:
    return any(
        (tag["Key"] == "elbv2.k8s.aws/cluster" and tag["Value"] == cluster)
        or tag["Key"] == f"kubernetes.io/cluster/{cluster}"
        for tag in tags
    )


def tagged_elbv2_arns(elbv2: Any, kind: str, vpc_id: str, cluster: str) -> list[str]:
    describe, key, arn_key = {
        "load balancer": ("describe_load_balancers", "LoadBalancers", "LoadBalancerArn"),
        "target group": ("describe_target_groups", "TargetGroups", "TargetGroupArn"),
    }[kind]
    arns = [
        item[arn_key]
        for page in elbv2.get_paginator(describe).paginate()
        for item in page[key]
        if item.get("VpcId") == vpc_id
    ]
    tagged = []
    for start in range(0, len(arns), 20):
        descriptions = elbv2.describe_tags(ResourceArns=arns[start : start + 20])
        tagged += [
            d["ResourceArn"]
            for d in descriptions["TagDescriptions"]
            if _cluster_tagged(d["Tags"], cluster)
        ]
    return tagged


def sweep_load_balancers(elbv2: Any, vpc_id: str, cluster: str) -> list[str]:
    left = []
    for arn in tagged_elbv2_arns(elbv2, "load balancer", vpc_id, cluster):
        elbv2.delete_load_balancer(LoadBalancerArn=arn)
        left.append(f"load balancer {arn}")
    for arn in tagged_elbv2_arns(elbv2, "target group", vpc_id, cluster):
        failure = _try_delete(f"target group {arn}", elbv2.delete_target_group, TargetGroupArn=arn)
        if failure:
            left.append(failure)
    return left


def sweep_instances(ec2: Any, vpc_id: str, cluster: str) -> list[str]:
    filters = [
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": f"tag:kubernetes.io/cluster/{cluster}", "Values": ["owned"]},
        {"Name": "instance-state-name", "Values": LIVE_INSTANCE_STATES},
    ]
    instances = [
        instance["InstanceId"]
        for page in ec2.get_paginator("describe_instances").paginate(Filters=filters)
        for reservation in page["Reservations"]
        for instance in reservation["Instances"]
    ]
    if instances:
        ec2.terminate_instances(InstanceIds=instances)
    return [f"instance {instance}" for instance in instances]


def sweep_launch_templates(ec2: Any, cluster: str) -> list[str]:
    filters = [{"Name": "tag:karpenter.k8s.aws/cluster", "Values": [cluster]}]
    for page in ec2.get_paginator("describe_launch_templates").paginate(Filters=filters):
        for template in page["LaunchTemplates"]:
            ec2.delete_launch_template(LaunchTemplateId=template["LaunchTemplateId"])
    return []


def sweep_security_groups(ec2: Any, vpc_id: str, cluster: str) -> list[str]:
    filters = [{"Name": "vpc-id", "Values": [vpc_id]}]
    groups = [
        group
        for page in ec2.get_paginator("describe_security_groups").paginate(Filters=filters)
        for group in page["SecurityGroups"]
        if _cluster_tagged(group.get("Tags", []), cluster)
    ]
    failures = [
        _try_delete(
            f"security group {g['GroupId']}", ec2.delete_security_group, GroupId=g["GroupId"]
        )
        for g in groups
    ]
    return [failure for failure in failures if failure]


def sweep_network_interfaces(ec2: Any, vpc_id: str) -> list[str]:
    filters = [
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": "status", "Values": ["available"]},
    ]
    interfaces = [
        interface["NetworkInterfaceId"]
        for page in ec2.get_paginator("describe_network_interfaces").paginate(Filters=filters)
        for interface in page["NetworkInterfaces"]
        if not interface.get("RequesterManaged")
    ]
    failures = [
        _try_delete(f"ENI {eni}", ec2.delete_network_interface, NetworkInterfaceId=eni)
        for eni in interfaces
    ]
    return [failure for failure in failures if failure]


def sweep_parameters(ssm: Any, prefix: str) -> list[str]:
    paginator = ssm.get_paginator("get_parameters_by_path")
    names = [
        parameter["Name"]
        for page in paginator.paginate(Path=prefix.rstrip("/"), Recursive=True)
        for parameter in page["Parameters"]
    ]
    for start in range(0, len(names), 10):
        ssm.delete_parameters(Names=names[start : start + 10])
    return []


def cleanup(properties: dict[str, Any], clients: dict[str, Any] | None = None) -> list[str]:
    clients = clients or {name: boto3.client(name) for name in ("ec2", "elbv2", "ssm")}
    ec2, cluster, vpc_id = clients["ec2"], properties["ClusterName"], properties["VpcId"]
    left = sweep_load_balancers(clients["elbv2"], vpc_id, cluster)
    left += sweep_instances(ec2, vpc_id, cluster)
    left += sweep_launch_templates(ec2, cluster)
    if not left:
        left += sweep_security_groups(ec2, vpc_id, cluster)
        left += sweep_network_interfaces(ec2, vpc_id)
    left += sweep_parameters(clients["ssm"], properties["ParameterPrefix"])
    return left


def handler(event: dict, context: Any) -> None:
    cfn.run(event, context, cleanup)
