"""
Pulumi program for deploying a multi-region EKS architecture.

This script sets up a multi-region, multi-tenant SkyPilot architecture.
The architecture is made of:
- A Hub-and-Spoke VPC topology spanning multiple regions.
- EKS clusters in separate regions
- A tailscale subnet router so users can interact with the SkyPilot API server via Tailscale as a VPN.
- A set of isolated dataplanes (namespaces on EKS clusters) where SkyPilot workloads can run.
"""

import pulumi

from pulumi_eks_ml import eks, eks_addons, eks_apps, vpc
from pulumi_eks_ml.eks_apps.skypilot import (
    SkyPilotAPIServer,
    SkyPilotCognitoIDP,
    SkyPilotDataPlaneProvisioner,
    SkyPilotDataPlaneRequest,
    SkyPilotDataPlaneUserIdentityProvisioner,
    SkyPilotDataPlaneUserIdentityRequest,
    SkyPilotServiceDiscovery,
)

from config import get_all_regions, load_project_config

_DEFAULT_USER_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
]


def create_cluster_with_addons(
    deployment_name: str,
    region_name: str,
    node_pools: list[eks.NodePoolConfig],
    vpc_network: vpc.VPCPeeredGroup,
    component_versions: eks.ComponentVersions,
) -> tuple[eks.EKSCluster, eks.EKSClusterAddonInstaller]:
    cluster = eks.EKSCluster(
        name=f"{deployment_name}-{region_name}",
        vpc_id=vpc_network.vpcs[region_name].vpc_id,
        subnet_ids=vpc_network.vpcs[region_name].private_subnet_ids,
        node_pools=node_pools,
        region=region_name,
        versions=component_versions,
    )
    addons = eks.EKSClusterAddonInstaller(
        f"{deployment_name}-{region_name}-addons",
        cluster=cluster,
        addon_types=eks_addons.recommended_addons(),
        versions=component_versions,
    )
    return cluster, addons


# ------------------------------------------------------------------------------
# Load Configuration
# ------------------------------------------------------------------------------
pulumi_config = pulumi.Config()
config = load_project_config(pulumi_config)

component_versions = eks.ComponentVersions(**config.component_versions)
deployment_name = f"{pulumi.get_project()}-{pulumi.get_stack()}"
all_regions = get_all_regions(config)

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
clusters: dict[str, tuple[eks.EKSCluster, eks.EKSClusterAddonInstaller]] = {}

hub_cluster, hub_addons = create_cluster_with_addons(
    deployment_name=deployment_name,
    region_name=config.hub.region,
    node_pools=config.hub.eks_node_pools,
    vpc_network=vpc_network,
    component_versions=component_versions,
)
clusters[config.hub.region] = (hub_cluster, hub_addons)

for spoke in config.spokes:
    clusters[spoke.region] = create_cluster_with_addons(
        deployment_name=deployment_name,
        region_name=spoke.region,
        node_pools=spoke.eks_node_pools,
        vpc_network=vpc_network,
        component_versions=component_versions,
    )

# We only want to provision SkyPilot after all EKS clusters are ready.
cluster_dependencies: list[pulumi.Resource] = [hub_cluster, hub_addons]
for spoke in config.spokes:
    spoke_cluster, spoke_addons = clusters[spoke.region]
    cluster_dependencies.extend([spoke_cluster, spoke_addons])


# ------------------------------------------------------------------------------
# Tailscale (Hub Only)
# ------------------------------------------------------------------------------
eks_apps.TailscaleSubnetRouter(
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
dp_requests: list[SkyPilotDataPlaneRequest] = []
identity_requests: list[SkyPilotDataPlaneUserIdentityRequest] = []

for region_config in [config.hub, *config.spokes]:
    cluster, _ = clusters[region_config.region]
    for dp in region_config.skypilot.data_planes:
        dp_requests.append(SkyPilotDataPlaneRequest(cluster=cluster, namespace=dp.name))

        policies = [] if dp.user_role_arn else _DEFAULT_USER_POLICIES
        identity_requests.append(
            SkyPilotDataPlaneUserIdentityRequest(
                cluster=cluster,
                namespace=dp.name,
                irsa_attached_policies=policies,
                role_arn=dp.user_role_arn,
            )
        )

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

sp_service_discovery = SkyPilotServiceDiscovery(
    name=f"{deployment_name}-sp-service-discovery",
    hostname=config.hub.skypilot.ingress_host,
    vpc_ids=[vpc_network.vpcs[region].vpc_id for region in all_regions],
    vpc_regions=all_regions,
    opts=pulumi.ResourceOptions(
        # You'll need to manually delete the private hosted zone in Route 53 if you ran 'pulumi destroy'
        # That's because it may still contain DNS records managed outside Pulumi.
        retain_on_delete=True, 
        depends_on=[*cluster_dependencies, vpc_network]
    ),
)

sp_cognito_idp = SkyPilotCognitoIDP(
    name=f"{deployment_name}-sp-cognito",
    region=config.hub.region,
    callback_url=f"https://{config.hub.skypilot.ingress_host}/oauth2/callback",
    opts=pulumi.ResourceOptions(
        depends_on=[*cluster_dependencies],
    ),
)

hub_cluster, _ = clusters[config.hub.region]

sp = SkyPilotAPIServer(
    name=f"{deployment_name}-sp-api-server",
    cluster=hub_cluster,
    ingress_host=config.hub.skypilot.ingress_host,
    ingress_ssl_cert_arn=config.hub.skypilot.ingress_ssl_cert_arn,
    default_user_role=config.hub.skypilot.default_user_role,
    oidc_issuer_url=sp_cognito_idp.oidc_issuer_url,
    oidc_client_id=sp_cognito_idp.skypilot_client_id,
    oidc_client_secret=sp_cognito_idp.skypilot_client_secret,
    kubeconfig=dp_provisioner.api_server_kube_config,
    service_accounts_by_context=user_identities.service_accounts_by_context,
    opts=pulumi.ResourceOptions(
        depends_on=[
            *cluster_dependencies,
            dp_provisioner,
            user_identities,
            sp_service_discovery,
            sp_cognito_idp,
        ]
    ),
)

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------
pulumi.export("hub_vpc_cidr", vpc_network.vpcs[config.hub.region].vpc_cidr_block)
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
pulumi.export("skypilot_api_service_config", sp.api_service_config)
pulumi.export("skypilot_admin_username", sp.admin_username)
pulumi.export("skypilot_admin_password", sp.admin_password)
pulumi.export("skypilot_admin_secret_arn", sp.admin_secret_arn)
pulumi.export(
    "skypilot_data_planes",
    pulumi.Output.all(*[i.identity_details for i in user_identities.identities]),
)
