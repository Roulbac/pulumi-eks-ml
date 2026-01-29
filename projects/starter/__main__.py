"""A Python Pulumi program"""

import pulumi

from pulumi_eks_ml import eks, eks_addons, vpc

main_region = pulumi.Config("aws").require("region")

cfg = pulumi.Config()
deployment_name = f"{pulumi.get_project()}-{pulumi.get_stack()}"
node_pools_config = cfg.require_object("node_pools")

node_pools = [eks.NodePoolConfig.from_dict(pool) for pool in node_pools_config]

vpc_resource = vpc.VPC(
    name=f"{deployment_name}-vpc",
    cidr_block="10.0.0.0/16",
    setup_internet_egress=True,
)

cluster = eks.EKSCluster(
    f"{deployment_name}-cls",
    vpc_id=vpc_resource.vpc_id,
    subnet_ids=vpc_resource.private_subnet_ids,
    node_pools=node_pools,
)

addon_installations = eks.cluster.EKSClusterAddonInstaller(
    f"{deployment_name}-addons",
    cluster=cluster,
    addon_types=eks_addons.recommended_addons(),
)


pulumi.export("vpc_id", vpc_resource.vpc_id)
pulumi.export("cluster_name", cluster.cluster_name)
