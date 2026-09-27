from botocore.stub import ANY

import network_sweeper

CLUSTER = "caldera"
VPC = "vpc-1"
NLB = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/net/gw/1"
TARGETS = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/gw/1"
FOREIGN = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/net/other/2"
CLUSTER_TAG = [{"Key": "elbv2.k8s.aws/cluster", "Value": CLUSTER}]


def stub_load_balancers(elbv2, load_balancers: list[str], target_groups: list[str]) -> None:
    elbv2.add_response(
        "describe_load_balancers",
        {
            "LoadBalancers": [{"LoadBalancerArn": arn, "VpcId": VPC} for arn in load_balancers]
            + [{"LoadBalancerArn": FOREIGN, "VpcId": "vpc-other"}]
        },
    )
    if load_balancers:
        elbv2.add_response(
            "describe_tags",
            {"TagDescriptions": [{"ResourceArn": a, "Tags": CLUSTER_TAG} for a in load_balancers]},
            {"ResourceArns": load_balancers},
        )
    for arn in load_balancers:
        elbv2.add_response("delete_load_balancer", {}, {"LoadBalancerArn": arn})
    elbv2.add_response(
        "describe_target_groups",
        {"TargetGroups": [{"TargetGroupArn": arn, "VpcId": VPC} for arn in target_groups]},
    )
    if target_groups:
        elbv2.add_response(
            "describe_tags",
            {"TagDescriptions": [{"ResourceArn": a, "Tags": CLUSTER_TAG} for a in target_groups]},
            {"ResourceArns": target_groups},
        )


def test_deletes_only_cluster_tagged_load_balancers_in_the_vpc(stubbed) -> None:
    elbv2, stub = stubbed["elbv2"]
    stub_load_balancers(stub, [NLB], [TARGETS])
    stub.add_client_error(
        "delete_target_group",
        service_error_code="ResourceInUse",
        expected_params={"TargetGroupArn": TARGETS},
    )

    left = network_sweeper.sweep_load_balancers(elbv2, VPC, CLUSTER)

    assert left == [f"load balancer {NLB}", f"target group {TARGETS} (ResourceInUse)"]


def test_terminates_live_cluster_instances(stubbed) -> None:
    ec2, stub = stubbed["ec2"]
    stub.add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [{"InstanceId": "i-1"}]}]},
        {"Filters": ANY},
    )
    stub.add_response("terminate_instances", {}, {"InstanceIds": ["i-1"]})

    assert network_sweeper.sweep_instances(ec2, VPC, CLUSTER) == ["instance i-1"]


def test_deletes_available_enis_but_skips_requester_managed_ones(stubbed) -> None:
    ec2, stub = stubbed["ec2"]
    stub.add_response(
        "describe_network_interfaces",
        {
            "NetworkInterfaces": [
                {"NetworkInterfaceId": "eni-cilium", "RequesterManaged": False},
                {"NetworkInterfaceId": "eni-lambda", "RequesterManaged": True},
            ]
        },
        {"Filters": ANY},
    )
    stub.add_response("delete_network_interface", {}, {"NetworkInterfaceId": "eni-cilium"})

    assert network_sweeper.sweep_network_interfaces(ec2, VPC) == []


def test_deletes_every_parameter_under_the_prefix_in_batches_of_ten(stubbed) -> None:
    ssm, stub = stubbed["ssm"]
    names = [f"/prismatic/github-app/key-{i}" for i in range(12)]
    stub.add_response(
        "get_parameters_by_path",
        {"Parameters": [{"Name": name} for name in names]},
        {"Path": "/prismatic", "Recursive": True},
    )
    stub.add_response("delete_parameters", {}, {"Names": names[:10]})
    stub.add_response("delete_parameters", {}, {"Names": names[10:]})

    assert network_sweeper.sweep_parameters(ssm, "/prismatic/") == []


def test_cleanup_waits_for_load_balancers_before_deleting_security_groups(stubbed) -> None:
    ec2, ec2_stub = stubbed["ec2"]
    elbv2, elbv2_stub = stubbed["elbv2"]
    ssm, ssm_stub = stubbed["ssm"]
    stub_load_balancers(elbv2_stub, [NLB], [])
    ec2_stub.add_response("describe_instances", {"Reservations": []}, {"Filters": ANY})
    ec2_stub.add_response("describe_launch_templates", {"LaunchTemplates": []}, {"Filters": ANY})
    ssm_stub.add_response(
        "get_parameters_by_path", {"Parameters": []}, {"Path": ANY, "Recursive": True}
    )

    left = network_sweeper.cleanup(
        {"ClusterName": CLUSTER, "VpcId": VPC, "ParameterPrefix": "/prismatic/"},
        {"ec2": ec2, "elbv2": elbv2, "ssm": ssm},
    )

    assert left == [f"load balancer {NLB}"]
