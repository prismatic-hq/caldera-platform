"""Set AWS_ROLE_ARN on each service repo to its GitHub OIDC push role from the CI access stack.

The service CI only publishes images and deploys previews when AWS_ROLE_ARN is set. Re-run after
any deploy that replaces the push roles.
"""

import argparse
import re
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from caldera.config import SERVICES_FILE
from caldera.stacks.ci_access import push_role_output
from preview_cli.registry import ServiceRegistry
from scripts.put_github_secrets import GitHubSetting, put_github_settings, run_gh

ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")


def stack_outputs(cloudformation, stack: str) -> dict[str, str]:
    try:
        [described] = cloudformation.describe_stacks(StackName=stack)["Stacks"]
    except (BotoCoreError, ClientError) as error:
        raise LookupError(f"cannot read stack {stack}: {error}") from error
    return {o["OutputKey"]: o["OutputValue"] for o in described.get("Outputs", [])}


def role_arn_settings(cloudformation, stack: str, repos: list[str]) -> dict[str, GitHubSetting]:
    outputs = stack_outputs(cloudformation, stack)
    settings = {}
    for repo in repos:
        key = push_role_output(repo)
        if key not in outputs:
            raise LookupError(f"stack {stack} has no {key} output; run mise run deploy first")
        if not ROLE_ARN.match(outputs[key]):
            raise ValueError(f"{stack} output {key} is not an IAM role ARN: {outputs[key]!r}")
        settings[repo] = GitHubSetting("variable", "AWS_ROLE_ARN", outputs[key])
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack", default="CalderaCiAccess")
    parser.add_argument("--org", default="prismatic-hq")
    args = parser.parse_args(argv)
    repos = [service.repo for service in ServiceRegistry.load(SERVICES_FILE).services]
    try:
        settings = role_arn_settings(boto3.client("cloudformation"), args.stack, repos)
    except (LookupError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    try:
        written = [
            target
            for repo, setting in settings.items()
            for target in put_github_settings(run_gh, args.org, [repo], [setting])
        ]
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    for target in written:
        print(f"set {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
