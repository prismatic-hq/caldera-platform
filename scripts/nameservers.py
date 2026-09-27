"""Print the Route 53 nameservers of the configured domain's public hosted zone.

Set these as the domain's NS records at its registrar or parent zone after the first deploy.
"""

import argparse
import os
import sys
from pathlib import Path

import boto3

from cdk.config import ENVIRONMENTS_DIR, load_environment


def domain_for(flag: str | None, env: str | None, environments_dir: Path) -> str:
    domain = flag or load_environment(env, environments_dir).get("domain")
    if not domain:
        raise ValueError(
            "domain is not set: run with ENV=<name>.yaml (a file in deploy/environments) "
            "or pass -- --domain <zone>"
        )
    return str(domain).rstrip(".")


def nameservers(route53, domain: str) -> list[str]:
    listed = route53.list_hosted_zones_by_name(DNSName=domain, MaxItems="100")["HostedZones"]
    zone_ids = [
        zone["Id"]
        for zone in listed
        if zone["Name"] == f"{domain}." and not zone["Config"]["PrivateZone"]
    ]
    if not zone_ids:
        raise LookupError(f"no public hosted zone for {domain}; run mise run deploy first")
    if len(zone_ids) > 1:
        ids = ", ".join(zone_id.removeprefix("/hostedzone/") for zone_id in zone_ids)
        raise LookupError(f"{len(zone_ids)} public hosted zones for {domain}: {ids}")
    return route53.get_hosted_zone(Id=zone_ids[0])["DelegationSet"]["NameServers"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", help="Overrides the domain in the ENV environment file")
    args = parser.parse_args(argv)
    try:
        domain = domain_for(args.domain, os.environ.get("ENV"), ENVIRONMENTS_DIR)
        servers = nameservers(boto3.client("route53"), domain)
    except (ValueError, LookupError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"NS records for {domain}:")
    for server in servers:
        print(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
