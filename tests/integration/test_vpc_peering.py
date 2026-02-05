from __future__ import annotations

import boto3
import pulumi
import pulumi.automation as auto
import pulumi_aws as aws

from pulumi_eks_ml import vpc
from pulumi_eks_ml.vpc.multi_region import VPCPeeringStrategy
from tests.integration.conftest import pulumi_stack_factory


def _get_route_table(ec2_client, route_table_id: str) -> dict:
    return ec2_client.describe_route_tables(RouteTableIds=[route_table_id])[
        "RouteTables"
    ][0]


def _has_peering_route(
    routes: list[dict], destination_cidr: str, peering_id: str
) -> bool:
    return any(
        route.get("DestinationCidrBlock") == destination_cidr
        and route.get("VpcPeeringConnectionId") == peering_id
        for route in routes
    )


class TestHubAndSpokeVPCPeering:
    HUB_REGION = "us-east-1"
    SPOKE_REGIONS = ["us-west-2", "eu-west-1"]

    @staticmethod
    def _hub_and_spoke_program() -> None:
        aws_config = pulumi.Config("aws")
        access_key = aws_config.require("accessKey")
        secret_key = aws_config.require("secretKey")
        hub_provider = aws.Provider(
            "localstack-hub",
            region=TestHubAndSpokeVPCPeering.HUB_REGION,
            access_key=access_key,
            secret_key=secret_key,
            skip_credentials_validation=True,
            skip_metadata_api_check=True,
            skip_requesting_account_id=True,
        )
        spoke_providers = [
            aws.Provider(
                f"localstack-spoke-{region}",
                region=region,
                access_key=access_key,
                secret_key=secret_key,
                skip_credentials_validation=True,
                skip_metadata_api_check=True,
                skip_requesting_account_id=True,
            )
            for region in TestHubAndSpokeVPCPeering.SPOKE_REGIONS
        ]

        hub_vpc = vpc.VPC(
            "hub",
            cidr_block="10.10.0.0/16",
            setup_internet_egress=False,
            num_azs=2,
            opts=pulumi.ResourceOptions(provider=hub_provider),
        )
        spoke_a = vpc.VPC(
            "spoke-a",
            cidr_block="10.20.0.0/16",
            setup_internet_egress=False,
            num_azs=2,
            opts=pulumi.ResourceOptions(provider=spoke_providers[0]),
        )
        spoke_b = vpc.VPC(
            "spoke-b",
            cidr_block="10.30.0.0/16",
            setup_internet_egress=False,
            num_azs=2,
            opts=pulumi.ResourceOptions(provider=spoke_providers[1]),
        )

        peering = VPCPeeringStrategy(
            "hub-spoke",
            vpcs=[hub_vpc, spoke_a, spoke_b],
            topology="hub_and_spoke",
            hub=TestHubAndSpokeVPCPeering.HUB_REGION,
            opts=pulumi.ResourceOptions(provider=hub_provider),
        )

        pulumi.export("hub_cidr", hub_vpc.vpc_cidr_block)
        pulumi.export("hub_private_route_table_id", hub_vpc.private_route_table_id)
        pulumi.export(
            "spoke_cidrs",
            pulumi.Output.from_input([spoke_a.vpc_cidr_block, spoke_b.vpc_cidr_block]),
        )
        pulumi.export(
            "spoke_private_route_table_ids",
            pulumi.Output.from_input(
                [spoke_a.private_route_table_id, spoke_b.private_route_table_id]
            ),
        )
        pulumi.export("peering_connection_ids", peering.peering_connection_ids)

    @staticmethod
    def test_creates_peering_routes_for_hub_and_spokes(ec2_client, localstack_endpoint):
        with pulumi_stack_factory() as create_stack:
            stack: auto.Stack = create_stack(
                program=TestHubAndSpokeVPCPeering._hub_and_spoke_program
            )
            result = stack.up(on_output=None)

            hub_cidr = result.outputs["hub_cidr"].value
            hub_private_route_table_id = result.outputs[
                "hub_private_route_table_id"
            ].value
            spoke_cidrs = result.outputs["spoke_cidrs"].value
            spoke_private_route_table_ids = result.outputs[
                "spoke_private_route_table_ids"
            ].value
            peering_ids = result.outputs["peering_connection_ids"].value

            assert (
                len(peering_ids)
                == len(spoke_cidrs)
                == len(spoke_private_route_table_ids)
            )

            spoke_clients = [
                boto3.client(
                    "ec2",
                    endpoint_url=localstack_endpoint,
                    region_name=region,
                )
                for region in TestHubAndSpokeVPCPeering.SPOKE_REGIONS
            ]

            hub_routes = _get_route_table(ec2_client, hub_private_route_table_id)[
                "Routes"
            ]
            for idx, spoke_cidr in enumerate(spoke_cidrs):
                assert _has_peering_route(hub_routes, spoke_cidr, peering_ids[idx])

            for idx, spoke_route_table_id in enumerate(spoke_private_route_table_ids):
                routes = _get_route_table(spoke_clients[idx], spoke_route_table_id)[
                    "Routes"
                ]
                peering_id = peering_ids[idx]
                
                # Check Spoke -> Hub route
                assert _has_peering_route(routes, hub_cidr, peering_id)



class TestVPCPeeredGroup:
    HUB_REGION = "us-east-1"
    SPOKE_REGIONS = ["us-west-2"]

    @staticmethod
    def _multi_region_program() -> None:
        hub_region = TestVPCPeeredGroup.HUB_REGION
        spoke_regions = TestVPCPeeredGroup.SPOKE_REGIONS

        multi_region = vpc.VPCPeeredGroup(
            "multi-region",
            regions=[hub_region, *spoke_regions],
            topology="hub_and_spoke",
            hub=hub_region,
        )

        pulumi.export("hub_vpc_id", multi_region.vpcs[hub_region].vpc_id)
        pulumi.export(
            "hub_private_route_table_id",
            multi_region.vpcs[hub_region].private_route_table_id,
        )
        pulumi.export("hub_cidr", multi_region.vpcs[hub_region].vpc_cidr_block)
        pulumi.export(
            "spoke_vpc_ids",
            pulumi.Output.from_input(
                [multi_region.vpcs[region].vpc_id for region in spoke_regions]
            ),
        )
        pulumi.export(
            "spoke_private_route_table_ids",
            pulumi.Output.from_input(
                [
                    multi_region.vpcs[region].private_route_table_id
                    for region in spoke_regions
                ]
            ),
        )
        pulumi.export(
            "spoke_cidrs",
            pulumi.Output.from_input(
                [multi_region.vpcs[region].vpc_cidr_block for region in spoke_regions]
            ),
        )
        pulumi.export(
            "peering_connection_ids",
            multi_region.peering_connection_ids,
        )

    @staticmethod
    def test_creates_hub_and_spoke_across_regions(ec2_client, localstack_endpoint):
        with pulumi_stack_factory() as create_stack:
            stack: auto.Stack = create_stack(
                program=TestVPCPeeredGroup._multi_region_program
            )
            result = stack.up(on_output=None)

            hub_vpc_id = result.outputs["hub_vpc_id"].value
            hub_private_route_table_id = result.outputs[
                "hub_private_route_table_id"
            ].value
            hub_cidr = result.outputs["hub_cidr"].value
            peering_id = result.outputs["peering_connection_ids"].value[0]

            spoke_vpc_id = result.outputs["spoke_vpc_ids"].value[0]
            spoke_private_route_table_id = result.outputs[
                "spoke_private_route_table_ids"
            ].value[0]
            spoke_cidr = result.outputs["spoke_cidrs"].value[0]

            for spoke_region in TestVPCPeeredGroup.SPOKE_REGIONS:
                spoke_client = boto3.client(
                    "ec2",
                    endpoint_url=localstack_endpoint,
                    region_name=spoke_region,
                )

                assert ec2_client.describe_vpcs(VpcIds=[hub_vpc_id])["Vpcs"]
                assert spoke_client.describe_vpcs(VpcIds=[spoke_vpc_id])["Vpcs"]

                peering_ids = [
                    pc["VpcPeeringConnectionId"]
                    for pc in ec2_client.describe_vpc_peering_connections()[
                        "VpcPeeringConnections"
                    ]
                ]
                assert peering_id in peering_ids

                hub_routes = _get_route_table(ec2_client, hub_private_route_table_id)[
                    "Routes"
                ]
                assert _has_peering_route(hub_routes, spoke_cidr, peering_id)

                spoke_routes = _get_route_table(spoke_client, spoke_private_route_table_id)[
                    "Routes"
                ]
                assert _has_peering_route(spoke_routes, hub_cidr, peering_id)
