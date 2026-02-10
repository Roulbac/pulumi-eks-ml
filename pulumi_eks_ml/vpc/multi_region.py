from __future__ import annotations

import itertools
from typing import Literal

import pulumi
import pulumi_aws as aws

from .utils import region_to_cidr
from .core import VPC


class VPCPeeringStrategy(pulumi.ComponentResource):
    """VPC peering strategy: hub-and-spoke or full-mesh.

    The "hub_and_spoke" topology is a subset of "full_mesh".
    In "full_mesh", every region peers with every other region.
    In "hub_and_spoke", only the hub region peers with other regions.
    
    This class ensures consistent resource naming and directionality (A->B where region A < region B)
    so that switching between topologies only adds/removes the difference in connections.
    """

    peering_connection_ids: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        vpcs: list[VPC],
        topology: Literal["hub_and_spoke", "full_mesh"],
        hub: str | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:VPCPeeringStrategy", name, None, opts)

        self.peering_connections = []
        self.routes = []

        vpcs_by_region = {vpc.region: vpc for vpc in vpcs}
        regions = list(vpcs_by_region.keys())

        if topology == "hub_and_spoke":
            if not hub:
                raise ValueError("hub argument is required for hub_and_spoke topology")
            if hub not in vpcs_by_region:
                raise ValueError(f"hub region {hub} must be in the list of regions")
        
        match topology:
            case "hub_and_spoke":
                # Only associate the hub with the other regions
                region_pairs = itertools.product([hub], [r for r in regions if r != hub])
            case "full_mesh":
                # Associate every region with every other region in pairs
                region_pairs = itertools.combinations(regions, 2)

        # Iterate over all unique sorted pairs (A, B) where A < B
        for region_a, region_b in region_pairs:
            # Ensure consistent ordering by sorting the regions alphabetically.
            # This will avoid unnecessary setup/teardown with pulumi
            region_a, region_b = sorted([region_a, region_b])

            vpc_a = vpcs_by_region[region_a]
            vpc_b = vpcs_by_region[region_b]

            # Create peering connection from A to B (resides in A's region)
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

    vpc_cidrs: dict[str, str]
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
            raise ValueError(f"The topology 'hub_and_spoke' can be used if and only if a hub region is provided, but got {topology=} and {hub=}")
        
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

        self.peering_strategy = VPCPeeringStrategy(
            f"{name}-strategy",
            vpcs=list(self.vpcs.values()),
            topology=topology,
            hub=hub,
            opts=pulumi.ResourceOptions(depends_on=[*self.vpcs.values()], parent=self),
        )

        self.peering_connection_ids = self.peering_strategy.peering_connection_ids

        self.register_outputs(
            {
                "vpc_cidrs": self.vpc_cidrs,
                "peering_connection_ids": self.peering_connection_ids,
            }
        )
