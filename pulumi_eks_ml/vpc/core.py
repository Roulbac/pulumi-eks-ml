from __future__ import annotations

import pulumi
import pulumi_aws as aws

from .utils import calculate_subnets


class VPC(pulumi.ComponentResource):
    """Creates a VPC with minimal public subnet and maximized private subnets.

    Automatically calculates optimal subnet allocation:
    - 3 maximized private subnets (equal size) across AZs
    - 1 minimal public subnet (/28) for NAT Gateway at the end (if 'setup_internet_egress' is True)
    """

    vpc_id: pulumi.Output[str]
    vpc_cidr_block: pulumi.Output[str]
    public_subnet_id: pulumi.Output[str | None]
    private_subnet_ids: pulumi.Output[list[str]]
    private_subnet_cidrs: pulumi.Output[list[str]]
    private_route_table_id: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        cidr_block: str,
        setup_internet_egress: bool = True,
        num_azs: int = 3,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:VPC", name, None, opts)

        provider = opts and opts.provider or None
        self.region = aws.get_region(opts=pulumi.InvokeOptions(provider=provider)).region
        self.azs = aws.get_availability_zones(
            opts=pulumi.InvokeOptions(provider=provider)
        )

        # Calculate subnet CIDRs automatically
        public_subnet_cidr, private_subnet_cidrs = calculate_subnets(
            cidr_block, num_azs
        )

        # Create VPC
        self.vpc = aws.ec2.Vpc(
            f"{name}-vpc",
            cidr_block=cidr_block,
            enable_dns_hostnames=True,
            enable_dns_support=True,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create private subnets (maximized) - AFTER public subnet
        self.private_subnets: list[aws.ec2.Subnet] = []
        for i, cidr in enumerate(private_subnet_cidrs):
            subnet = aws.ec2.Subnet(
                f"{name}-private-subnet-{i + 1}",
                vpc_id=self.vpc.id,
                cidr_block=cidr,
                availability_zone=self.azs.names[i % len(self.azs.names)],
                opts=pulumi.ResourceOptions(parent=self),
            )
            self.private_subnets.append(subnet)

        # Create routing
        self._setup_routing(name)

        if setup_internet_egress:
            self._setup_internet_egress(name, public_subnet_cidr)

        # Initialize outputs
        self.vpc_id = self.vpc.id
        self.vpc_cidr_block = self.vpc.cidr_block
        self.private_subnet_ids = pulumi.Output.from_input(
            [s.id for s in self.private_subnets]
        )
        self.private_subnet_cidrs = pulumi.Output.from_input(
            [s.cidr_block for s in self.private_subnets]
        )
        self.private_route_table_id = self.private_route_table.id

        if public_subnet := getattr(self, "public_subnet", None):
            self.public_subnet_id = public_subnet.id
        else:
            self.public_subnet_id = None

        # Register outputs
        self.register_outputs(
            {
                "vpc_id": self.vpc.id,
                "vpc_cidr_block": self.vpc_cidr_block,
                "public_subnet_id": self.public_subnet_id,
                "private_subnet_ids": self.private_subnet_ids,
                "private_subnet_cidrs": self.private_subnet_cidrs,
                "private_route_table_id": self.private_route_table_id,
            }
        )

    def _setup_internet_egress(self, name: str, public_subnet_cidr: str):
        # Create Internet Gateway
        self.igw = aws.ec2.InternetGateway(
            f"{name}-igw",
            vpc_id=self.vpc.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        # Create public subnet (minimal)
        self.public_subnet = aws.ec2.Subnet(
            f"{name}-public-subnet",
            vpc_id=self.vpc.id,
            cidr_block=public_subnet_cidr,
            availability_zone=self.azs.names[0],
            opts=pulumi.ResourceOptions(parent=self),
        )
        self.nat_eip = aws.ec2.Eip(
            f"{name}-nat-eip",
            domain="vpc",
            opts=pulumi.ResourceOptions(parent=self),
        )
        # NAT Gateway
        self.nat_gateway = aws.ec2.NatGateway(
            f"{name}-nat-gateway",
            allocation_id=self.nat_eip.id,
            subnet_id=self.public_subnet.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        # Public route to internet
        aws.ec2.Route(
            f"{name}-public-route",
            route_table_id=self.public_route_table.id,
            destination_cidr_block="0.0.0.0/0",
            gateway_id=self.igw.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        # Private route to NAT Gateway
        aws.ec2.Route(
            f"{name}-private-route",
            route_table_id=self.private_route_table.id,
            destination_cidr_block="0.0.0.0/0",
            nat_gateway_id=self.nat_gateway.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        # Associate public subnet
        aws.ec2.RouteTableAssociation(
            f"{name}-public-rta",
            subnet_id=self.public_subnet.id,
            route_table_id=self.public_route_table.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

    def _setup_routing(self, name: str):
        """Set up route tables and associations."""
        # Public route table
        self.public_route_table = aws.ec2.RouteTable(
            f"{name}-public-rt",
            vpc_id=self.vpc.id,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Private route table - depends on NAT Gateway
        self.private_route_table = aws.ec2.RouteTable(
            f"{name}-private-rt",
            vpc_id=self.vpc.id,
            opts=pulumi.ResourceOptions(parent=self),
        )
        # Associate private subnets
        for i, subnet in enumerate(self.private_subnets):
            aws.ec2.RouteTableAssociation(
                f"{name}-private-rta-{i + 1}",
                subnet_id=subnet.id,
                route_table_id=self.private_route_table.id,
                opts=pulumi.ResourceOptions(
                    parent=self, depends_on=[self.private_route_table]
                ),
            )
