from __future__ import annotations

import hashlib
import ipaddress


def region_to_cidr(region: str, base_network_format: str = "10.{index}.0.0/16") -> str:
    """
    Map AWS region to a deterministic /16 CIDR block within the base network.

    Uses predefined mapping for known regions, deterministic hash fallback for others.

    Args:
        region: The AWS region
        base_network_format: The base network format

    Returns:
        The CIDR block
    """
    # Known AWS regions with logical grouping
    known_regions = {
        # US East (1-9)
        "us-east-1": 1,
        "us-east-2": 2,
        # US West (10-19)
        "us-west-1": 10,
        "us-west-2": 11,
        # EU (20-39)
        "eu-west-1": 20,
        "eu-west-2": 21,
        "eu-central-1": 22,
        "eu-north-1": 23,
        # Asia Pacific (40-69)
        "ap-southeast-1": 40,
        "ap-southeast-2": 41,
        "ap-northeast-1": 42,
        "ap-northeast-2": 43,
        "ap-south-1": 44,
        # Canada (70-79)
        "ca-central-1": 70,
        # South America (80-89)
        "sa-east-1": 80,
    }

    if region in known_regions:
        return base_network_format.format(index=known_regions[region])

    # Deterministic fallback for unknown regions (avoid collisions with known ranges)
    digest = hashlib.sha1(region.encode("utf-8")).hexdigest()
    fallback_index = 100 + (int(digest, 16) % 100)
    return base_network_format.format(index=fallback_index)


def calculate_subnets(cidr_block: str, num_azs: int) -> tuple[str, list[str]]:
    """Calculate optimal subnet allocation from VPC CIDR block.

    Strategy:
    - Allocate maximum space to private subnets first
    - Reserve minimal public subnet (/28) at the end of address space

    Args:
        cidr_block: VPC CIDR (e.g., "10.0.0.0/16")
        num_azs: Number of AZs to create private subnets for, one subnet per AZ.

    Returns:
        tuple: (public_subnet_cidr, [private_subnet_cidrs])
    """
    vpc_network = ipaddress.IPv4Network(cidr_block, strict=False)

    if vpc_network.prefixlen > 28:
        raise ValueError(
            f"VPC CIDR {cidr_block} is too small to allocate a /28 public subnet."
        )

    if num_azs < 1:
        raise ValueError("num_azs must be at least 1")

    # Public subnet: last /28 block of the VPC
    public_network = list(vpc_network.subnets(new_prefix=28))[-1]

    # Strategy: allocate largest possible equal private subnets from the beginning
    # while reserving the last /28 for public access.
    private_subnets: list[ipaddress.IPv4Network] | None = None
    private_prefix = None
    for prefix in range(vpc_network.prefixlen, 29):
        total_subnets = 1 << (prefix - vpc_network.prefixlen)
        if total_subnets <= num_azs:
            continue
        candidates = list(vpc_network.subnets(new_prefix=prefix))[:num_azs]
        if any(public_network.overlaps(sn) for sn in candidates):
            continue
        private_prefix = prefix
        private_subnets = candidates
        break

    if private_subnets is None or private_prefix is None:
        raise ValueError(
            f"VPC CIDR {cidr_block} cannot provide {num_azs} private subnets "
            "while reserving a /28 public subnet. Use a larger CIDR or fewer AZs."
        )

    private_cidrs = [str(sn) for sn in private_subnets]
    public_cidr = str(public_network)

    return public_cidr, private_cidrs
