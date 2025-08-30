import ipaddress

import pytest

from pulumi_eks_ml.vpc import calculate_subnets, region_to_cidr


def _second_octet(cidr: str) -> int:
    return int(cidr.split(".")[1])


def test_region_to_cidr_known_region():
    assert region_to_cidr("us-east-1") == "10.1.0.0/16"


def test_region_to_cidr_unknown_region_is_deterministic():
    cidr_first = region_to_cidr("me-central-1")
    cidr_second = region_to_cidr("me-central-1")

    assert cidr_first == cidr_second

    index = _second_octet(cidr_first)
    assert 100 <= index <= 199


def _expected_private_prefix(cidr_block: str, num_azs: int) -> int:
    vpc_network = ipaddress.IPv4Network(cidr_block, strict=False)
    for prefix in range(vpc_network.prefixlen, 29):
        total_subnets = 1 << (prefix - vpc_network.prefixlen)
        if total_subnets > num_azs:
            return prefix
    raise ValueError("no valid private prefix")


@pytest.mark.parametrize(
    ("cidr_block", "num_azs", "expected_private_prefix"),
    [
        ("10.0.0.0/16", 2, 18),
        ("10.0.0.0/16", 3, 18),
        ("10.0.0.0/16", 4, 19),
        ("10.5.0.0/20", 3, 22),
        ("10.8.0.0/22", 3, 24),
    ],
)
def test_calculate_subnets_even_split_and_maximized(
    cidr_block: str, num_azs: int, expected_private_prefix: int
):
    public_cidr, private_cidrs = calculate_subnets(cidr_block, num_azs)

    assert len(private_cidrs) == num_azs
    assert ipaddress.IPv4Network(public_cidr).prefixlen == 28
    assert all(
        ipaddress.IPv4Network(cidr).prefixlen == expected_private_prefix
        for cidr in private_cidrs
    )
    assert expected_private_prefix == _expected_private_prefix(cidr_block, num_azs)

    vpc_network = ipaddress.IPv4Network(cidr_block)
    public_network = ipaddress.IPv4Network(public_cidr)
    private_networks = [ipaddress.IPv4Network(cidr) for cidr in private_cidrs]

    # Check that the public and private subnets are within the VPC network
    assert public_network.subnet_of(vpc_network)
    assert all(net.subnet_of(vpc_network) for net in private_networks)

    # Check that the public subnet does not overlap with the private subnets
    assert all(not public_network.overlaps(net) for net in private_networks)
    assert len({net.network_address for net in private_networks}) == num_azs

    # Ensure private subnets are the first contiguous blocks
    private_networks_sorted = sorted(
        private_networks, key=lambda net: int(net.network_address)
    )
    assert private_networks_sorted[0].network_address == vpc_network.network_address
    for prev, curr in zip(private_networks_sorted, private_networks_sorted[1:]):
        assert prev.broadcast_address + 1 == curr.network_address

    # Public subnet should be the last /28 in the VPC
    last_public = list(vpc_network.subnets(new_prefix=28))[-1]
    assert public_network == last_public


def test_calculate_subnets_rejects_too_small_vpc():
    with pytest.raises(ValueError, match="too small"):
        calculate_subnets("10.0.0.0/29", 1)


def test_calculate_subnets_rejects_insufficient_private_subnets():
    with pytest.raises(ValueError, match="cannot provide"):
        calculate_subnets("10.0.0.0/28", 1)
