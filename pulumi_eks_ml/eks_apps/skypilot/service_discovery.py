"""SkyPilot private DNS service discovery."""

from __future__ import annotations

from typing import Sequence

import pulumi
import pulumi_aws as aws


class SkyPilotServiceDiscovery(pulumi.ComponentResource):
    """Provision a private Route53 hosted zone for SkyPilot."""

    hosted_zone: aws.route53.Zone
    zone_id: pulumi.Output[str]
    zone_name: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        hostname: pulumi.Input[str],
        vpc_ids: Sequence[pulumi.Input[str]],
        vpc_regions: Sequence[pulumi.Input[str]] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotServiceDiscovery", name, None, opts)

        if len(vpc_ids) == 0:
            raise ValueError("At least one VPC ID is required to create a private zone")
        if vpc_regions is not None and len(vpc_regions) != len(vpc_ids):
            raise ValueError("vpc_regions must match vpc_ids length when provided")

        zone_vpcs = []
        for idx, vpc_id in enumerate(vpc_ids):
            vpc_region = vpc_regions[idx] if vpc_regions else None
            zone_vpcs.append(
                aws.route53.ZoneVpcArgs(vpc_id=vpc_id, vpc_region=vpc_region)
            )

        zone_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(parent=self)
        )

        self.hosted_zone = aws.route53.Zone(
            f"{name}-zone",
            name=hostname,
            comment="Private hosted zone for SkyPilot service discovery.",
            vpcs=zone_vpcs,
            opts=zone_opts
        )

        self.zone_id = self.hosted_zone.id
        self.zone_name = self.hosted_zone.name

        self.register_outputs(
            {
                "zone_id": self.zone_id,
                "zone_name": self.zone_name,
            }
        )


