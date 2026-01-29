"""
Pulumi program for deploying a multi-region EKS architecture.

This script sets up:
1. A Hub-and-Spoke VPC topology spanning multiple regions.
2. EKS clusters in each region (hub and spokes).
3. Recommended EKS addons for each cluster.
"""

import pulumi
from pulumi_eks_ml import eks, eks_addons, vpc

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
config = pulumi.Config()

# Region configuration
regions = config.require_object("regions")

# Project naming
deployment_name = f"{pulumi.get_project()}-{pulumi.get_stack()}"

# Component versions
versions_data = config.get_object("versions") or {}
component_versions = eks.ComponentVersions(**versions_data)

# Node pools configuration
node_pools_data = config.require_object("node_pools")
node_pools = [eks.NodePoolConfig.from_dict(pool) for pool in node_pools_data]

# ------------------------------------------------------------------------------
# Networking Resources
# ------------------------------------------------------------------------------
# Create a multi-region VPC network with full mesh topology
vpc_network = vpc.VPCPeeredGroup(
    name=f"{deployment_name}-vpcs",
    regions=regions,
    topology="full_mesh",
)

# ------------------------------------------------------------------------------
# Cluster Resources
# ------------------------------------------------------------------------------
def create_cluster_and_addons(
    region_name: str,
) -> tuple[eks.EKSCluster, eks.EKSClusterAddonInstaller]:
    """
    Creates an EKS cluster and installs addons for the specified region.
    
    Uses global configuration for networking, node pools, and versions.
    """
    # Create the EKS Cluster
    cluster_resource = eks.EKSCluster(
        f"{deployment_name}-{region_name}-cls",
        vpc_id=vpc_network.vpcs[region_name].vpc_id,
        subnet_ids=vpc_network.vpcs[region_name].private_subnet_ids,
        node_pools=node_pools,
        region=region_name,
        versions=component_versions,
    )

    # Install recommended addons
    addon_installer = eks.EKSClusterAddonInstaller(
        f"{deployment_name}-{region_name}-addons",
        cluster=cluster_resource,
        addon_types=eks_addons.recommended_addons(),
        versions=component_versions,
    )

    return cluster_resource, addon_installer

# Deploy clusters across all configured regions
clusters_with_addons = {
    region: create_cluster_and_addons(region)
    for region in regions
}

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------
cluster_outputs = {
    region: {
        "vpc_id": vpc_network.vpcs[region].vpc_id,
        "cluster_name": cluster.cluster_name,
    }
    for region, (cluster, _) in clusters_with_addons.items()
}

pulumi.export("clusters", cluster_outputs)
