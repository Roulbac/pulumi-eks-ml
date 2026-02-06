"""SkyPilot API Server addon for EKS."""

from typing import Mapping

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ...eks.cluster import EKSCluster
from ...eks.config import SKYPILOT_API_SERVER_VERSION, EFS_CSI_DEFAULT_SC_NAME
from ...eks.irsa import IRSA
from .config_builder import (
    build_api_service_config,
    build_aws_credentials_secret,
    build_values,
)
from .credentials import SkyPilotAdminCredentials, SkyPilotOAuthCredentials
from .iam import build_api_service_policy


class SkyPilotAPIServer(pulumi.ComponentResource):
    """Component that installs the SkyPilot API server Helm chart.

    - Enforces ALB ingress (internal) with health checks
    - Generates initial Basic Auth credentials and enables user management
    - Stores credentials in AWS Secrets Manager
    - Exposes `admin_username`, `admin_password`, and `admin_secret_arn` outputs
    """

    admin_username: pulumi.Output[str]
    admin_password: pulumi.Output[str]
    admin_secret_arn: pulumi.Output[str]
    api_service_config: pulumi.Output[str]
    ingress_status: pulumi.Output[dict]

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        kubeconfig: pulumi.Input[str],
        ingress_host: pulumi.Input[str],
        ingress_ssl_cert_arn: pulumi.Input[str],
        oidc_issuer_url: pulumi.Input[str],
        oidc_client_id: pulumi.Input[str],
        oidc_client_secret: pulumi.Input[str],
        service_accounts_by_context: pulumi.Input[Mapping[str, str]],
        version: str = SKYPILOT_API_SERVER_VERSION,
        namespace: str = "skypilot",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotApiServer", name, None, opts)

        # Resolve dependencies and provider
        k8s_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(
                parent=self,
                provider=cluster.k8s_provider,
                depends_on=[cluster],
            )
        )
        aws_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(
                parent=self,
                provider=cluster.aws_provider,
            )
        )

        # Namespace for SkyPilot resources
        namespace_res = k8s.core.v1.Namespace(
            f"{name}-ns", metadata={"name": namespace}, opts=k8s_opts
        )

        # ----------------------------------------------------------------------
        # SkyPilot Admin Credentials
        # ----------------------------------------------------------------------
        admin_credentials = SkyPilotAdminCredentials(
            f"{name}-admin-credentials",
            namespace=namespace,
            k8s_opts=k8s_opts,
            aws_opts=aws_opts,
            depends_on=[namespace_res],
            opts=pulumi.ResourceOptions(parent=self),
        )

        kubeconfig_secret = k8s.core.v1.Secret(
            f"{name}-kubeconfig",
            metadata={
                "name": "kube-credentials",
                "namespace": namespace,
            },
            string_data={"config": kubeconfig},
            type="Opaque",
            opts=k8s_opts.merge(pulumi.ResourceOptions(depends_on=[namespace_res])),
        )

        invoke_opts = pulumi.InvokeOptions(provider=cluster.aws_provider)
        account_id = aws.get_caller_identity(opts=invoke_opts).account_id

        api_service_policy = aws.iam.Policy(
            f"{name}-api-service-policy",
            name=f"{cluster.name}-{namespace}-api-service-policy",
            policy=pulumi.Output.json_dumps(build_api_service_policy(account_id)),
            opts=aws_opts,
        )

        api_service_irsa = IRSA(
            name=f"{name}-api-service-irsa",
            role_name=f"{cluster.name}-{namespace}-api-service-role",
            oidc_provider_arn=cluster.oidc_provider_arn,
            oidc_issuer=cluster.oidc_issuer,
            trust_sa_namespace=namespace,
            trust_sa_name="*",
            opts=aws_opts,
        )

        # Create a role policy attachment for the api service policy
        _ = aws.iam.RolePolicyAttachment(
            f"{name}-api-service-admin-policy-attachment",
            role=api_service_irsa.iam_role.name,
            policy_arn=api_service_policy.arn,
            opts=aws_opts,
        )

        aws_credentials_secret = k8s.core.v1.Secret(
            f"{name}-aws-creds",
            metadata={
                "name": "aws-credentials",
                "namespace": namespace,
            },
            string_data={
                "credentials": (
                    pulumi.Output.all(
                        cluster_region=cluster.region,
                        irsa_role_arn=api_service_irsa.iam_role_arn,
                    ).apply(lambda kwargs: build_aws_credentials_secret(**kwargs))
                )
            },
            type="Opaque",
            opts=k8s_opts.merge(pulumi.ResourceOptions(depends_on=[namespace_res])),
        )

        oauth_credentials = SkyPilotOAuthCredentials(
            f"{name}-oauth-credentials",
            namespace=namespace,
            client_id=oidc_client_id,
            client_secret=oidc_client_secret,
            opts=k8s_opts.merge(
                pulumi.ResourceOptions(parent=self, depends_on=[namespace_res])
            ),
        )

        self.api_service_config = pulumi.Output.all(
            service_accounts_by_context=service_accounts_by_context,
        ).apply(lambda kwargs: build_api_service_config(**kwargs))

        values = pulumi.Output.all(
            subnet_ids=cluster.subnet_ids,
            irsa_role_arn=api_service_irsa.iam_role_arn,
            api_service_config=self.api_service_config,
            storage_class_name=EFS_CSI_DEFAULT_SC_NAME,
            ingress_host=ingress_host,
            ingress_ssl_cert_arn=ingress_ssl_cert_arn,
            oauth_issuer_url=oidc_issuer_url,
            oauth_client_secret_name=oauth_credentials.secret_name,
        ).apply(lambda kwargs: build_values(**kwargs))

        # Install the Helm release
        release_name = f"{name}-sp-helm-release"
        self.release = k8s.helm.v3.Release(
            release_name,
            name="skypilot",
            chart="skypilot",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                repo="https://helm.skypilot.co"
            ),
            version=version,
            namespace=namespace,
            values=values,
            skip_await=True,
            opts=k8s_opts.merge(
                pulumi.ResourceOptions(
                    depends_on=[
                        namespace_res,
                        kubeconfig_secret,
                        aws_credentials_secret,
                        oauth_credentials,
                    ],
                )
            ),
        )

        self.ingress = k8s.networking.v1.Ingress.get(
            "skypilot-ingress",
            pulumi.Output.concat(
                self.release.namespace, "/", self.release.name, "-ingress"
            ),
            opts=k8s_opts,
        )
        self.ingress_status = self.ingress.status

        # Expose outputs
        self.admin_username = admin_credentials.username
        self.admin_password = admin_credentials.password
        self.admin_secret_arn = admin_credentials.secret_arn

        self.register_outputs(
            {
                "api_service_config": self.api_service_config,
                "admin_username": self.admin_username,
                "admin_password": self.admin_password,
                "admin_secret_arn": self.admin_secret_arn,
                "ingress_status": self.ingress.status,
            }
        )
