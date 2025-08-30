from __future__ import annotations

import pulumi
import pulumi_aws as aws

from .utils import region_to_cidr
from .core import VPC


class HubAndSpokeVPCPeering(pulumi.ComponentResource):
    """Hub-and-spoke VPC peering: Hub connects to all spokes, enabling spoke-to-spoke via hub. Single peering per spoke, transitive routing through hub."""

    peering_connection_ids: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        hub_vpc: VPC,  # Hub VPC instance with route table access
        spoke_vpcs: list[VPC],  # List of spoke VPC instances
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:HubAndSpokeVPCPeering", name, None, opts)

        self.peering_connections = []
        self.routes = []

        # Create peering from hub to each spoke
        for spoke_vpc in spoke_vpcs:
            # Create peering connection
            peering = aws.ec2.VpcPeeringConnection(
                f"{name}-h2s-peering-{spoke_vpc.region}",
                vpc_id=hub_vpc.vpc_id,
                region=hub_vpc.region,
                peer_vpc_id=spoke_vpc.vpc_id,
                peer_region=spoke_vpc.region,
                opts=pulumi.ResourceOptions(parent=self),
            )
            # Accept peering connection from the spoke VPC
            spoke_accepter = aws.ec2.VpcPeeringConnectionAccepter(
                f"{name}-h2s-peering-accepter-{spoke_vpc.region}",
                vpc_peering_connection_id=peering.id,
                auto_accept=True,
                region=spoke_vpc.region,
                opts=pulumi.ResourceOptions(parent=self),
            )
            # Update peering connection to allow DNS resolution from the hub VPC
            aws.ec2.VpcPeeringConnectionAccepter(
                f"{name}-h2s-peering-accepter-dns-{spoke_vpc.region}",
                vpc_peering_connection_id=peering.id,
                region=hub_vpc.region,
                requester=aws.ec2.VpcPeeringConnectionRequesterArgs(
                    allow_remote_vpc_dns_resolution=True,
                ),
                opts=pulumi.ResourceOptions(parent=self, depends_on=[spoke_accepter]),
            )
            self.peering_connections.append(peering)
            # Route from hub to spoke
            hub_to_spoke_route = aws.ec2.Route(
                f"{name}-h2s-route-{spoke_vpc.region}",
                route_table_id=hub_vpc.private_route_table_id,
                destination_cidr_block=spoke_vpc.vpc_cidr_block,
                vpc_peering_connection_id=peering.id,
                region=hub_vpc.region,
                opts=pulumi.ResourceOptions(parent=self, depends_on=[peering]),
            )
            self.routes.append(hub_to_spoke_route)
            # Route from spoke to hub
            spoke_to_hub_route = aws.ec2.Route(
                f"{name}-s2h-route-{spoke_vpc.region}",
                route_table_id=spoke_vpc.private_route_table_id,
                destination_cidr_block=hub_vpc.vpc_cidr_block,
                vpc_peering_connection_id=peering.id,
                region=spoke_vpc.region,
                opts=pulumi.ResourceOptions(parent=self, depends_on=[peering]),
            )
            self.routes.append(spoke_to_hub_route)

        # Enable spoke-to-spoke communication via hub
        # For each pair of spokes, add routes through hub
        for i, spoke_a in enumerate(spoke_vpcs):
            for j, spoke_b in enumerate(spoke_vpcs):
                if i != j:
                    # Find the peering connection that spoke_a uses to reach hub
                    peering_for_a = self.peering_connections[i]

                    # Add route from spoke_a to spoke_b via hub
                    spoke_to_spoke_route = aws.ec2.Route(
                        f"{name}-s2s-route-{spoke_a.region}-to-{spoke_b.region}",
                        route_table_id=spoke_a.private_route_table_id,
                        destination_cidr_block=spoke_b.vpc_cidr_block,
                        vpc_peering_connection_id=peering_for_a.id,
                        region=spoke_a.region,
                        opts=pulumi.ResourceOptions(
                            parent=self, depends_on=[peering_for_a]
                        ),
                    )
                    self.routes.append(spoke_to_spoke_route)

        # Register outputs
        self.peering_connection_ids = pulumi.Output.from_input(
            [pc.id for pc in self.peering_connections]
        )
        self.hub_vpc_id = hub_vpc.vpc_id
        self.spoke_vpc_ids = pulumi.Output.from_input(
            [vpc.vpc_id for vpc in spoke_vpcs]
        )
        self.register_outputs(
            {
                "peering_connection_ids": self.peering_connection_ids,
            }
        )


class MultiRegionHubAndSpokeVPCs(pulumi.ComponentResource):
    """Multi-region hub-and-spoke VPCs with VPC peering.

    The architecture is:
        - Hub connects to all spokes, enabling spoke-to-spoke via hub.
        - Single peering per spoke, transitive routing through hub.
    """

    vpc_cidrs: pulumi.Output[dict[str, str]]
    hub_vpc_id: pulumi.Output[str]
    spoke_vpc_ids: pulumi.Output[list[str]]
    peering_connection_ids: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        hub_region: str,
        spoke_regions: list[str],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__(
            "pulumi-eks-ml:aws:MultiRegionHubAndSpokeVPCs", name, None, opts
        )
        self.hub_region = hub_region
        self.spoke_regions = spoke_regions
        self.all_regions = [hub_region] + spoke_regions
        self.providers = {
            k: aws.Provider(f"{name}-{k}", region=k) for k in self.all_regions
        }
        self.vpc_cidrs = {k: region_to_cidr(k) for k in self.all_regions}

        self.vpcs = {
            region: VPC(
                f"{name}-{region}",
                cidr_block=self.vpc_cidrs[region],
                setup_internet_egress=True if region == self.hub_region else False,
                opts=pulumi.ResourceOptions(
                    provider=self.providers[region], parent=self
                ),
            )
            for region in self.all_regions
        }
        # Create hub-and-spoke peering
        self.hub_and_spoke = HubAndSpokeVPCPeering(
            f"{name}-vpc-hns-peering",
            hub_vpc=self.vpcs[self.hub_region],
            spoke_vpcs=[self.vpcs[region] for region in self.spoke_regions],
            opts=pulumi.ResourceOptions(depends_on=[*self.vpcs.values()], parent=self),
        )

        self.register_outputs(
            {
                "vpc_cidrs": self.vpc_cidrs,
                "hub_vpc_id": self.vpcs[self.hub_region].vpc_id,
                "spoke_vpc_ids": pulumi.Output.from_input(
                    [self.vpcs[region].vpc_id for region in self.spoke_regions]
                ),
                "peering_connection_ids": self.hub_and_spoke.peering_connection_ids,
            }
        )
