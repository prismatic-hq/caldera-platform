#!/usr/bin/env python3
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from caldera.nag_suppressions import apply_suppressions
from caldera.platform import build_platform


def build(app: cdk.App) -> cdk.App:
    build_platform(app)
    apply_suppressions(app)
    cdk.Validations.of(app).add_plugins(
        AwsSolutionsChecks(app, verbose=True, write_suppressions_to_cloud_formation=True)
    )
    return app


if __name__ == "__main__":
    build(cdk.App()).synth()
