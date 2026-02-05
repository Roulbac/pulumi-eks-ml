"""
Pulumi program for deploying a multi-region EKS architecture.

This script sets up:
1. A Hub-and-Spoke VPC topology spanning multiple regions.
2. EKS clusters in each region (hub and spokes).
3. Recommended EKS addons for each cluster.
"""

import pulumi
from pydantic import BaseModel, Field, model_validator
from pulumi_eks_ml import eks, eks_addons, eks_apps, vpc
from pulumi_eks_ml.eks_apps.skypilot import (
    SkyPilotAPIServer,
    SkyPilotDataPlaneProvisioner,
    SkyPilotDataPlaneRequest,
    SkyPilotDataPlaneUserIdentityProvisioner,
    SkyPilotDataPlaneUserIdentityRequest,
)


# ------------------------------------------------------------------------------
# Configuration Models
# ------------------------------------------------------------------------------
class TailscaleConfig(BaseModel):
    enabled: bool = False
    oauth_secret_arn: str | None = None


class DataPlaneConfig(BaseModel):
    name: str
    user_role_arn: str | None = None


class RegionConfig(BaseModel):
    region: str
    node_pools: list[dict]
    sp_data_planes: list[DataPlaneConfig] = Field(default_factory=list)

    @property
    def eks_node_pools(self) -> list[eks.NodePoolConfig]:
        return [eks.NodePoolConfig.from_dict(p) for p in self.node_pools]


class HubConfig(RegionConfig):
    tailscale: TailscaleConfig = Field(default_factory=TailscaleConfig)


class ProjectConfig(BaseModel):
    hub: HubConfig
    spokes: list[RegionConfig] = Field(default_factory=list)
    component_versions: dict = Field(default_factory=dict, alias="versions")

    @model_validator(mode="after")
    def validate_unique_regions(self):
        spoke_regions = [s.region for s in self.spokes]
        if len(spoke_regions) != len(set(spoke_regions)):
            raise ValueError(
                f"Spoke regions must be unique. Found duplicates in: {spoke_regions}"
            )
        if self.hub.region in spoke_regions:
            raise ValueError(
                f"Hub region '{self.hub.region}' cannot overlap with any of the spoke regions."
            )
        return self


# ------------------------------------------------------------------------------
# Load Configuration
# ------------------------------------------------------------------------------
pulumi_config = pulumi.Config()

config = ProjectConfig(
    hub=pulumi_config.require_object("hub"),
    spokes=pulumi_config.get_object("spokes") or [],
    component_versions=pulumi_config.get_object("component_versions") or {},
)

component_versions = eks.ComponentVersions(**config.component_versions)
deployment_name = f"sp-{pulumi.get_stack()}"
all_regions = [config.hub.region] + [s.region for s in config.spokes]

# ------------------------------------------------------------------------------
# Networking
# ------------------------------------------------------------------------------
vpc_network = vpc.VPCPeeredGroup(
    name=f"{deployment_name}-vpcs",
    regions=all_regions,
    topology="hub_and_spoke",
    hub=config.hub.region,
)


# ------------------------------------------------------------------------------
# Cluster Deployment
# ------------------------------------------------------------------------------
def deploy_region(
    rc: RegionConfig,
) -> tuple[eks.EKSCluster, eks.EKSClusterAddonInstaller]:
    cluster = eks.EKSCluster(
        name=f"{deployment_name}-{rc.region}",
        vpc_id=vpc_network.vpcs[rc.region].vpc_id,
        subnet_ids=vpc_network.vpcs[rc.region].private_subnet_ids,
        node_pools=rc.eks_node_pools,
        region=rc.region,
        versions=component_versions,
    )
    addons = eks.EKSClusterAddonInstaller(
        f"{deployment_name}-{rc.region}-addons",
        cluster=cluster,
        addon_types=eks_addons.recommended_addons(),
        versions=component_versions,
    )
    return cluster, addons


clusters = {config.hub.region: deploy_region(config.hub)}
for spoke in config.spokes:
    clusters[spoke.region] = deploy_region(spoke)

################## Gather all clusters and addons to define dependencies for SkyPilot ##################
# We only want to start provision SkyPilot resources after all EKS clusters are created and ready.
hub_cluster, hub_addons = clusters[config.hub.region]
cluster_dependencies = [hub_cluster, hub_addons]

for spoke in config.spokes:
    spoke_cluster, spoke_addons = clusters[spoke.region]
    cluster_dependencies.extend([spoke_cluster, spoke_addons])

#########################################################################################################


# ------------------------------------------------------------------------------
# Tailscale (Hub Only)
# ------------------------------------------------------------------------------
if config.hub.tailscale.enabled:
    subnet_router = eks_apps.TailscaleSubnetRouter(
        name=f"{deployment_name}-{config.hub.region}-ts",
        cluster=hub_cluster,
        oauth_secret_arn=config.hub.tailscale.oauth_secret_arn,
        advertised_routes=[vpc_network.vpcs[r].vpc_cidr_block for r in all_regions],
        version=component_versions.tailscale_operator,
        opts=pulumi.ResourceOptions(depends_on=cluster_dependencies),
    )

# ------------------------------------------------------------------------------
# SkyPilot
# ------------------------------------------------------------------------------
dp_requests, identity_requests = [], []

def collect_data_planes(rc: RegionConfig):
    cluster, _ = clusters[rc.region]
    for dp in rc.sp_data_planes:
        dp_requests.append(SkyPilotDataPlaneRequest(cluster=cluster, namespace=dp.name))

        policies = (
            []
            if dp.user_role_arn
            else [
                "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
                "arn:aws:iam::aws:policy/AmazonS3FullAccess",
            ]
        )
        identity_requests.append(
            SkyPilotDataPlaneUserIdentityRequest(
                cluster=cluster,
                namespace=dp.name,
                irsa_attached_policies=policies,
                role_arn=dp.user_role_arn,
            )
        )


# Collect data plane requests for each region
for region_config in [config.hub, *config.spokes]:
    collect_data_planes(region_config)

user_identities = SkyPilotDataPlaneUserIdentityProvisioner(
    name=f"{deployment_name}-sp-user-identities",
    identity_requests=identity_requests,
    opts=pulumi.ResourceOptions(depends_on=cluster_dependencies),
)

dp_provisioner = SkyPilotDataPlaneProvisioner(
    name=f"{deployment_name}-sp-dp-provisioner",
    dp_requests=dp_requests,
    opts=pulumi.ResourceOptions(depends_on=cluster_dependencies),
)

sp = SkyPilotAPIServer(
    name=f"{deployment_name}-sp-api-server",
    cluster=clusters[config.hub.region][0],
    kubeconfig=dp_provisioner.api_server_kube_config,
    service_accounts_by_context=user_identities.service_accounts_by_context,
    opts=pulumi.ResourceOptions(
        depends_on=[
            *cluster_dependencies,
            dp_provisioner,
            user_identities,
        ]
    ),
)

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------
pulumi.export(
    "clusters",
    [
        {
            "vpc_id": vpc_network.vpcs[region].vpc_id,
            "region": region,
            "cluster_name": cluster.cluster_name,
        }
        for region, (cluster, _) in clusters.items()
    ],
)
pulumi.export("skypilot_ingress_status", sp.ingress_status)
pulumi.export("skypilot_api_service_config", sp.api_service_config)
pulumi.export("skypilot_admin_username", sp.admin_username)
pulumi.export("skypilot_admin_password", sp.admin_password)
pulumi.export("skypilot_admin_secret_arn", sp.admin_secret_arn)
# Export data plane details for manual IAM role binding
pulumi.export(
    "skypilot_data_planes",
    pulumi.Output.all(*[i.identity_details for i in user_identities.identities]),
)