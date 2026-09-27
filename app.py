#!/usr/bin/env python3
import aws_cdk as cdk

from caldera_platform.platform import build_platform

app = cdk.App()
build_platform(app)
app.synth()
