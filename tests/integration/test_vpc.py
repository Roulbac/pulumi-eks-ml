from __future__ import annotations

import pulumi
import pytest

from pulumi_eks_ml import vpc
from tests.integration.conftest import localstack_provider, pulumi_stack_factory


class TestVPC:
    @staticmethod
    def _vpc_program() -> None:
        provider = localstack_provider()

        cidr_block = pulumi.Config("tests").require("vpcCidrBlock")
        num_azs = int(pulumi.Config("tests").require("numAzs"))

        vpc_resource = vpc.VPC(
            "test-vpc",
            cidr_block=cidr_block,
            num_azs=num_azs,
            opts=pulumi.ResourceOptions(provider=provider),
        )

        pulumi.export("vpc_id", vpc_resource.vpc_id)
        pulumi.export("private_subnet_ids", vpc_resource.private_subnet_ids)
        pulumi.export("public_subnet_id", vpc_resource.public_subnet_id)

    @staticmethod
    @pytest.mark.parametrize(
        "cidr_block,num_azs,expected_subnet_cidrs",
        [
            (
                "10.42.0.0/16",
                3,
                {"10.42.0.0/18", "10.42.64.0/18", "10.42.128.0/18", "10.42.255.240/28"},
            )
        ],
    )
    def test_creates_public_and_private_subnets(
        ec2_client,
        cidr_block,
        num_azs,
        expected_subnet_cidrs,
    ):
        with pulumi_stack_factory() as create_stack:
            # Create stack with custom CIDR block
            stack = create_stack(
                program=TestVPC._vpc_program,
                config_overrides={
                    "tests:vpcCidrBlock": cidr_block,
                    "tests:numAzs": str(num_azs),
                },
            )
            # Up the stack
            result = stack.up(on_output=None)

            vpc_id = result.outputs["vpc_id"].value
            subnets = ec2_client.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )["Subnets"]

            actual_subnet_cidrs = set([subnet["CidrBlock"] for subnet in subnets])
            assert len(actual_subnet_cidrs) == num_azs + 1
            assert actual_subnet_cidrs == expected_subnet_cidrs

    @staticmethod
    def test_smallest_subnet_is_public_and_routes_via_nat(ec2_client):
        with pulumi_stack_factory() as create_stack:
            stack = create_stack(
                program=TestVPC._vpc_program,
                config_overrides={
                    "tests:vpcCidrBlock": "10.99.0.0/16",
                    "tests:numAzs": "2",
                },
            )
            result = stack.up(on_output=None)

            vpc_id = result.outputs["vpc_id"].value
            public_subnet_id = result.outputs["public_subnet_id"].value
            subnets = ec2_client.describe_subnets(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )["Subnets"]
            subnet_by_id = {subnet["SubnetId"]: subnet for subnet in subnets}

            assert public_subnet_id in subnet_by_id

            igw_id = ec2_client.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
            )["InternetGateways"][0]["InternetGatewayId"]

            nat_gateway = ec2_client.describe_nat_gateways(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )["NatGateways"][0]
            assert nat_gateway["SubnetId"] == public_subnet_id

            public_route_table = ec2_client.describe_route_tables(
                Filters=[
                    {"Name": "association.subnet-id", "Values": [public_subnet_id]}
                ]
            )["RouteTables"][0]
            assert any(
                route.get("DestinationCidrBlock") == "0.0.0.0/0"
                and route.get("GatewayId") == igw_id
                for route in public_route_table["Routes"]
            )
