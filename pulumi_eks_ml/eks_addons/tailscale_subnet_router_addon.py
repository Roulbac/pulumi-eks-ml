import json

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.cluster import EKSCluster


def install_tailscale_subnet_router(
    name: str,
    vpc_id: pulumi.Input[str],
    oauth_secret_arn: pulumi.Input[str],
    parent: pulumi.Resource,
    k8s_provider: k8s.Provider,
    depends_on: list[pulumi.Resource],
):
    """Install the Tailscale Operator and configure a subnet router via Operator CRD."""
    # Get OAuth client ID and secret from AWS Secrets Manager
    secret = json.loads(
        aws.secretsmanager.get_secret_version(
            secret_id=oauth_secret_arn,
        ).secret_string
    )

    # Namespace for operator and managed resources
    namespace = k8s.core.v1.Namespace(
        f"{name}-tailscale-ns",
        metadata={"name": "tailscale"},
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            retain_on_delete=True,
            depends_on=[*depends_on],
        ),
    )

    # Install the Tailscale Kubernetes Operator via Helm
    operator_release = k8s.helm.v3.Release(
        f"{name}-tailscale-operator",
        name="tailscale-operator",
        chart="tailscale-operator",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://pkgs.tailscale.com/helmcharts",
        ),
        namespace=namespace.metadata["name"],
        values={
            "oauth": {
                "clientId": secret["CLIENT_ID"],
                "clientSecret": secret["CLIENT_SECRET"],
            }
        },
        skip_await=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[*depends_on, namespace],
        ),
    )

    vpc_cidr = aws.ec2.get_vpc_output(id=vpc_id).cidr_block.apply(str)

    # Create a Connector CRD to act as a subnet router managed by the operator
    connector = k8s.apiextensions.CustomResource(
        f"{name}-tailscale-connector",
        api_version="tailscale.com/v1alpha1",
        kind="Connector",
        metadata={
            "name": f"{name}-subnet-router",
            "namespace": namespace.metadata["name"],
        },
        spec={
            "hostname": f"{name}-subnet-router",
            # Tags must be permitted by your ACLs; see docs
            "tags": ["tag:k8s"],
            # Configure the subnet router
            "subnetRouter": {
                "advertiseRoutes": [vpc_cidr],
            },
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=[operator_release, *depends_on],
        ),
    )

    return operator_release, connector


class TailscaleSubnetRouterAddon(pulumi.ComponentResource):
    """Tailscale subnet router as a Pulumi ComponentResource."""

    operator_release: k8s.helm.v3.Release
    connector: k8s.apiextensions.CustomResource

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Input[str],
        oauth_secret_arn: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
    ):
        super().__init__(
            "pulumi-eks-ml:eks:TailscaleSubnetRouterAddon", name, None, opts
        )

        self.operator_release, self.connector = install_tailscale_subnet_router(
            name=name,
            vpc_id=vpc_id,
            oauth_secret_arn=oauth_secret_arn,
            k8s_provider=opts.providers["kubernetes"],
            parent=self,
            depends_on=opts.depends_on or [],
        )

        self.register_outputs(
            {
                "operator_release": self.operator_release,
                "connector": self.connector,
            }
        )

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        oauth_secret_arn: pulumi.Input[str],
        parent: pulumi.Resource | None = None,
        extra_dependencies: list[pulumi.Resource] | None = None,
    ) -> "TailscaleSubnetRouterAddon":
        """Create a TailscaleSubnetRouterAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-tailscale",
            vpc_id=cluster.vpc_id,
            oauth_secret_arn=oauth_secret_arn,
            opts=pulumi.ResourceOptions(
                parent=parent,
                depends_on=[
                    cluster,
                    *(extra_dependencies or []),
                ],
                providers={
                    "kubernetes": cluster.k8s_provider,
                    "aws": cluster.aws_provider,
                },
            ),
        )
