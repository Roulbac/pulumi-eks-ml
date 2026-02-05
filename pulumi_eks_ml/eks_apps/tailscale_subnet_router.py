import json

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.config import TAILSCALE_OPERATOR_VERSION
from ..eks.cluster import EKSCluster


class TailscaleSubnetRouter(pulumi.ComponentResource):
    """Tailscale subnet router as a Pulumi ComponentResource."""

    operator_release: k8s.helm.v3.Release
    connector: k8s.apiextensions.CustomResource

    version_key = "tailscale_operator"

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        oauth_secret_arn: pulumi.Input[str],
        advertised_routes: list[pulumi.Input[str]],
        version: str | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:eks_apps:TailscaleSubnetRouter",name, None, opts)

        # Get OAuth client ID and secret from AWS Secrets Manager
        secret = json.loads(
            aws.secretsmanager.get_secret_version(
                secret_id=oauth_secret_arn,
            ).secret_string
        )

        resource_opts = pulumi.ResourceOptions(
            parent=self,
            provider=cluster.k8s_provider,
        )

        # Namespace for operator and managed resources
        namespace = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata={"name": "tailscale"},
            opts=resource_opts,
        )

        # Create the operator-oauth secret manually
        # The Tailscale operator expects this secret if clientSecret is not provided in values
        oauth_secret = k8s.core.v1.Secret(
            f"{name}-operator-oauth",
            metadata={
                "name": "operator-oauth",
                "namespace": namespace.metadata["name"],
            },
            string_data={
                "client_id": secret["CLIENT_ID"],
                "client_secret": secret["CLIENT_SECRET"],
            },
            opts=resource_opts.merge(pulumi.ResourceOptions(depends_on=[namespace])),
        )

        # Install the Tailscale Kubernetes Operator via Helm
        self.operator_release = k8s.helm.v3.Release(
            f"{name}-operator",
            name="tailscale-operator",
            chart="tailscale-operator",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                repo="https://pkgs.tailscale.com/helmcharts",
            ),
            version=version or TAILSCALE_OPERATOR_VERSION,
            namespace=namespace.metadata["name"],
            skip_await=True,
            opts=resource_opts.merge(pulumi.ResourceOptions(depends_on=[namespace, oauth_secret])),
        )

        # Create a Connector CRD to act as a subnet router managed by the operator
        self.connector = k8s.apiextensions.CustomResource(
            f"{name}-connector",
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
                    "advertiseRoutes": [
                        *advertised_routes,
                    ]
                },
            },
            opts=resource_opts.merge(pulumi.ResourceOptions(depends_on=[self.operator_release])),
        )

        self.register_outputs(
            {
                "operator_release": self.operator_release,
                "connector": self.connector,
            }
        )
