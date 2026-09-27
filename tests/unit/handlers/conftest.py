from collections.abc import Iterator

import boto3
import pytest
from botocore.stub import Stubber


@pytest.fixture
def stubbed() -> Iterator[dict[str, tuple[object, Stubber]]]:
    session = boto3.Session(
        region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    clients = {}
    for name in ("ec2", "elbv2", "ssm", "route53"):
        client = session.client(name)
        stubber = Stubber(client)
        stubber.activate()
        clients[name] = (client, stubber)
    yield clients
    for _, stubber in clients.values():
        stubber.assert_no_pending_responses()
