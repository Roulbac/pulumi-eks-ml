from __future__ import annotations

from typing import Literal

import pulumi
import pulumi_aws as aws

from .utils import region_to_cidr
from .core import VPC


class HubAndSpokePeeringStrategy(pulumi.ComponentResource):
    """Hub-and-spoke VPC peering: Hub connects to all spokes. No transitive routing."""

    peering_connection_ids: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        hub_vpc: VPC,  # Hub VPC instance with route table access
        spoke_vpcs: list[VPC],  # List of spoke VPC instances
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:HubAndSpokePeeringStrategy", name, None, opts)

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
            # Accept peering connection from the spoke VPC and enable Accepter-side DNS
            spoke_accepter = aws.ec2.VpcPeeringConnectionAccepter(
                f"{name}-h2s-ac-{spoke_vpc.region}",
                vpc_peering_connection_id=peering.id,
                auto_accept=True,
                region=spoke_vpc.region,
                accepter=aws.ec2.VpcPeeringConnectionAccepterArgs(
                    allow_remote_vpc_dns_resolution=True,
                ),
                opts=pulumi.ResourceOptions(parent=self),
            )
            # Update peering connection to allow DNS resolution from Requester side (Hub)
            aws.ec2.VpcPeeringConnectionAccepter(
                f"{name}-h2s-ac-dns-{spoke_vpc.region}",
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

        # Register outputs
        self.peering_connection_ids = pulumi.Output.from_input(
            [pc.id for pc in self.peering_connections]
        )
        self.register_outputs(
            {
                "peering_connection_ids": self.peering_connection_ids,
            }
        )


class FullMeshPeeringStrategy(pulumi.ComponentResource):
    """Full-mesh VPC peering: Every VPC connects to every other VPC."""

    peering_connection_ids: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        vpcs: list[VPC],
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:FullMeshPeeringStrategy", name, None, opts)

        self.peering_connections = []
        self.routes = []

        # Iterate over all unique pairs of VPCs
        for i, vpc_a in enumerate(vpcs):
            for j, vpc_b in enumerate(vpcs[i + 1:]):
                # Create peering connection from A to B
                # Peering connection resides in A's region
                peering = aws.ec2.VpcPeeringConnection(
                    f"{name}-peering-{vpc_a.region}-to-{vpc_b.region}",
                    vpc_id=vpc_a.vpc_id,
                    region=vpc_a.region,
                    peer_vpc_id=vpc_b.vpc_id,
                    peer_region=vpc_b.region,
                    opts=pulumi.ResourceOptions(parent=self),
                )

                # Accept from B and enable Accepter-side DNS resolution
                accepter = aws.ec2.VpcPeeringConnectionAccepter(
                    f"{name}-ac-{vpc_b.region}-from-{vpc_a.region}",
                    vpc_peering_connection_id=peering.id,
                    auto_accept=True,
                    region=vpc_b.region,
                    accepter=aws.ec2.VpcPeeringConnectionAccepterArgs(
                        allow_remote_vpc_dns_resolution=True,
                    ),
                    opts=pulumi.ResourceOptions(parent=self),
                )

                # Requester-side options (in A's region)
                aws.ec2.VpcPeeringConnectionAccepter(
                    f"{name}-dns-{vpc_a.region}-to-{vpc_b.region}",
                    vpc_peering_connection_id=peering.id,
                    region=vpc_a.region,
                    requester=aws.ec2.VpcPeeringConnectionRequesterArgs(
                        allow_remote_vpc_dns_resolution=True,
                    ),
                    opts=pulumi.ResourceOptions(parent=self, depends_on=[accepter]),
                )


                self.peering_connections.append(peering)

                # Route A -> B
                route_a_to_b = aws.ec2.Route(
                    f"{name}-route-{vpc_a.region}-to-{vpc_b.region}",
                    route_table_id=vpc_a.private_route_table_id,
                    destination_cidr_block=vpc_b.vpc_cidr_block,
                    vpc_peering_connection_id=peering.id,
                    region=vpc_a.region,
                    opts=pulumi.ResourceOptions(parent=self, depends_on=[peering]),
                )
                self.routes.append(route_a_to_b)

                # Route B -> A
                route_b_to_a = aws.ec2.Route(
                    f"{name}-route-{vpc_b.region}-to-{vpc_a.region}",
                    route_table_id=vpc_b.private_route_table_id,
                    destination_cidr_block=vpc_a.vpc_cidr_block,
                    vpc_peering_connection_id=peering.id,
                    region=vpc_b.region,
                    opts=pulumi.ResourceOptions(parent=self, depends_on=[peering]),
                )
                self.routes.append(route_b_to_a)

        # Register outputs
        self.peering_connection_ids = pulumi.Output.from_input(
            [pc.id for pc in self.peering_connections]
        )
        self.register_outputs(
            {
                "peering_connection_ids": self.peering_connection_ids,
            }
        )


class VPCPeeredGroup(pulumi.ComponentResource):
    """A group of VPCs peered together using a specified topology.

    Topologies:
        - "hub_and_spoke": Hub connects to all spokes. No spoke-to-spoke connectivity.
        - "full_mesh": Every VPC connects to every other VPC.
    """

    vpc_cidrs: pulumi.Output[dict[str, str]]
    vpcs: dict[str, VPC]
    peering_connection_ids: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        regions: list[str],
        topology: Literal["hub_and_spoke", "full_mesh"],
        hub: str | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:VPCPeeredGroup", name, None, opts)
        
        if (hub and topology == "full_mesh") or (not hub and topology == "hub_and_spoke"):
            raise ValueError(f"The 'hub' argument can only be used for 'hub_and_spoke' topology, but got {topology=} and {hub=}")
        
        if hub and hub not in regions:
            raise ValueError(f" The hub region {hub=} must be in the list of regions {regions=}, but got {hub=}.")

        self.regions = regions
        self.topology = topology
        self.hub = hub

        # Validation
        if topology == "hub_and_spoke":
            if not hub:
                raise ValueError("hub argument is required for hub_and_spoke topology")
            if hub not in regions:
                raise ValueError(f"hub region {hub} must be in the list of regions")

        self.providers = {
            k: aws.Provider(f"{name}-{k}", region=k) for k in self.regions
        }
        self.vpc_cidrs = {k: region_to_cidr(k) for k in self.regions}

        self.vpcs = {
            region: VPC(
                f"{name}-{region}",
                cidr_block=self.vpc_cidrs[region],
                setup_internet_egress=True,
                opts=pulumi.ResourceOptions(
                    provider=self.providers[region], parent=self
                ),
            )
            for region in self.regions
        }

        self.peering_strategy = None

        match topology:
            case "hub_and_spoke":
                assert self.hub and self.hub in self.regions
                spoke_vpcs = [v for r, v in self.vpcs.items() if r != self.hub]
                self.peering_strategy = HubAndSpokePeeringStrategy(
                    f"{name}-strategy",
                    hub_vpc=self.vpcs[hub],
                    spoke_vpcs=spoke_vpcs,
                    opts=pulumi.ResourceOptions(depends_on=[*self.vpcs.values()], parent=self),
                )
            case "full_mesh":
                self.peering_strategy = FullMeshPeeringStrategy(
                    f"{name}-strategy",
                    vpcs=list(self.vpcs.values()),
                    opts=pulumi.ResourceOptions(depends_on=[*self.vpcs.values()], parent=self),
                )
            case _:
                raise ValueError(f"Invalid topology: {topology}")

        self.peering_connection_ids = self.peering_strategy.peering_connection_ids

        self.register_outputs(
            {
                "vpc_cidrs": self.vpc_cidrs,
                "peering_connection_ids": self.peering_connection_ids,
            }
        )
