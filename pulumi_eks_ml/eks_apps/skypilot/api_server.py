"""SkyPilot API Server addon for EKS."""

from __future__ import annotations

import json
from textwrap import dedent
from typing import ClassVar, Mapping

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s
import pulumi_random as random
import yaml
from passlib.hash import apr_md5_crypt

from ...eks.cluster import EKSCluster
from ...eks.config import EFS_CSI_DEFAULT_SC_NAME, SKYPILOT_API_SERVER_VERSION
from ...eks.irsa import IRSA


# ----------------------------------------------------------------------
# Config Helpers
# ----------------------------------------------------------------------

def build_aws_credentials_secret(cluster_region: str, irsa_role_arn: str) -> str:
    """Build the AWS credentials file content for IRSA."""
    return dedent(
        f"""
        [default]
        role_arn = {irsa_role_arn}
        region = {cluster_region}
        web_identity_token_file = /var/run/secrets/eks.amazonaws.com/serviceaccount/token
        """
    )


def build_api_service_config(service_accounts_by_context: Mapping[str, str]) -> str:
    """Build the SkyPilot API service YAML config payload."""
    return yaml.safe_dump(
        {
            "allowed_clouds": ["aws", "kubernetes"],
            "kubernetes": {
                "allowed_contexts": list(service_accounts_by_context.keys()),
                "context_configs": {
                    k: {"remote_identity": v}
                    for k, v in service_accounts_by_context.items()
                },
                "custom_metadata": {
                    "annotations": {"alb.ingress.kubernetes.io/scheme": "internal"}
                },
            },
            "jobs": {"controller": {"consolidation_mode": True}},
        }
    )


def build_values(
    subnet_ids: list[str],
    irsa_role_arn: str,
    api_service_config: str,
    storage_class_name: str,
    ingress_host: str,
    ingress_ssl_cert_arn: str,
    oauth_issuer_url: str | None = None,
    oauth_client_secret_name: str | None = None,
) -> dict:
    """Build the Helm values for the SkyPilot API server chart."""
    values: dict = {
        "ingress": {
            "enabled": True,
            "unified": True,
            "host": ingress_host,
            "ingressClassName": "alb",
            "annotations": {
                "alb.ingress.kubernetes.io/scheme": "internal",
                "alb.ingress.kubernetes.io/target-type": "ip",
                "alb.ingress.kubernetes.io/healthcheck-path": "/api/health",
                "alb.ingress.kubernetes.io/subnets": ",".join(subnet_ids),
                "alb.ingress.kubernetes.io/certificate-arn": ingress_ssl_cert_arn,
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTPS":443}]',
                "alb.ingress.kubernetes.io/ssl-redirect": "443",
            },
        },
        "ingress-nginx": {"enabled": False},
        "apiService": {
            # "initialBasicAuthSecret": "initial-basic-auth",
            # "enableUserManagement": True,
            "config": api_service_config,
        },
        "awsCredentials": {
            "enabled": True,
            "useCredentialsFile": True,
        },
        "rbac": {
            "serviceAccountAnnotations": {
                "eks.amazonaws.com/role-arn": irsa_role_arn
            },
        },
        "storage": {
            "enabled": True,
            "storageClassName": storage_class_name,
            "accessMode": "ReadWriteMany",
            "size": "64Gi",
        },
        "kubernetesCredentials": {
            "useApiServerCluster": False,
            "useKubeconfig": True,
            "kubeconfigSecretName": "kube-credentials",
        },
    }

    if oauth_issuer_url and oauth_client_secret_name:
        values["auth"] = {
            "oauth": {
                "enabled": True,
                "oidc-issuer-url": oauth_issuer_url,
                "client-details-from-secret": oauth_client_secret_name,
            }
        }

    return values


# ----------------------------------------------------------------------
# IAM Helpers
# ----------------------------------------------------------------------

def build_api_service_policy(account_id: str) -> dict:
    """Build IAM policy document for the SkyPilot API service."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": "arn:aws:ec2:*::image/ami-*",
            },
            {
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": [
                    f"arn:aws:ec2:*:{account_id}:instance/*",
                    f"arn:aws:ec2:*:{account_id}:network-interface/*",
                    f"arn:aws:ec2:*:{account_id}:subnet/*",
                    f"arn:aws:ec2:*:{account_id}:volume/*",
                    f"arn:aws:ec2:*:{account_id}:security-group/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:TerminateInstances",
                    "ec2:DeleteTags",
                    "ec2:StartInstances",
                    "ec2:CreateTags",
                    "ec2:StopInstances",
                ],
                "Resource": f"arn:aws:ec2:*:{account_id}:instance/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:Describe*",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateSecurityGroup",
                    "ec2:AuthorizeSecurityGroupIngress",
                ],
                "Resource": f"arn:aws:ec2:*:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": "iam:CreateServiceLinkedRole",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"iam:AWSServiceName": "spot.amazonaws.com"}
                },
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:PassRole",
                    "iam:CreateRole",
                    "iam:AttachRolePolicy",
                ],
                "Resource": [
                    f"arn:aws:iam::{account_id}:role/skypilot-v1",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetInstanceProfile",
                    "iam:CreateInstanceProfile",
                    "iam:AddRoleToInstanceProfile",
                ],
                "Resource": f"arn:aws:iam::{account_id}:instance-profile/skypilot-v1",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateImage",
                    "ec2:CopyImage",
                    "ec2:DeregisterImage",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:DeleteSecurityGroup",
                    "ec2:ModifyInstanceAttribute",
                ],
                "Resource": f"arn:aws:ec2:*:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                "Resource": "arn:aws:s3:::*/*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": "arn:aws:s3:::*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:ListAllMyBuckets",
                "Resource": "*",
            },
        ],
    }


# ----------------------------------------------------------------------
# Credentials
# ----------------------------------------------------------------------

class SkyPilotAdminCredentials(pulumi.ComponentResource):
    """Creates admin credentials and secrets for SkyPilot API server."""

    username: pulumi.Output[str]
    password: pulumi.Output[str]
    secret_arn: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        namespace: str,
        k8s_opts: pulumi.ResourceOptions,
        aws_opts: pulumi.ResourceOptions,
        depends_on: list[pulumi.Resource] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotAdminCredentials", name, None, opts)

        web_username = "skypilot"

        random_opts = pulumi.ResourceOptions(parent=self, depends_on=depends_on)
        web_password = random.RandomPassword(
            f"{name}-admin-pw",
            length=16,
            special=False,
            opts=random_opts,
        )
        salt = random.RandomPassword(
            f"{name}-admin-pw-salt",
            length=8,
            special=False,
            opts=random_opts,
        )

        # Build stable htpasswd line using a deterministic salt
        auth_value = pulumi.Output.all(web_password.result, salt.result).apply(
            lambda args: (
                f"{web_username}:{apr_md5_crypt.using(salt=args[1]).hash(args[0])}"
            )
        )

        _ = k8s.core.v1.Secret(
            f"{name}-admin-k8s-creds",
            metadata={
                "name": "initial-basic-auth",
                "namespace": namespace,
            },
            string_data={"auth": auth_value},
            type="Opaque",
            opts=k8s_opts.merge(
                pulumi.ResourceOptions(parent=self, depends_on=depends_on)
            ),
        )

        admin_secret = aws.secretsmanager.Secret(
            f"{name}-admin-secret",
            name_prefix=f"{name}-admin-creds-",
            description="SkyPilot API Server Admin Credentials",
            opts=aws_opts.merge(pulumi.ResourceOptions(parent=self)),
        )

        _ = aws.secretsmanager.SecretVersion(
            f"{name}-admin-secret-version",
            secret_id=admin_secret.id,
            secret_string=pulumi.Output.all(web_password.result).apply(
                lambda args: json.dumps(
                    {
                        "username": web_username,
                        "password": args[0],
                    }
                )
            ),
            opts=aws_opts.merge(pulumi.ResourceOptions(parent=self)),
        )

        self.username = pulumi.Output.from_input(web_username)
        self.password = pulumi.Output.secret(web_password.result)
        self.secret_arn = admin_secret.arn

        self.register_outputs(
            {
                "username": self.username,
                "password": self.password,
                "secret_arn": self.secret_arn,
            }
        )


class SkyPilotOAuthCredentials(pulumi.ComponentResource):
    """Creates OAuth client credentials secret for SkyPilot API server."""

    secret_name: pulumi.Output[str]

    # This is the name of the secret that will be created in the namespace.
    _OAUTH_SECRET_NAME: ClassVar[str] = "oauth2-proxy-credentials"

    def __init__(
        self,
        name: str,
        namespace: str,
        client_id: pulumi.Input[str],
        client_secret: pulumi.Input[str],
        depends_on: list[pulumi.Resource] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotOAuthCredentials", name, None, opts)

        oauth_secret_payload = pulumi.Output.all(
            client_id=client_id,
            client_secret=client_secret,
        ).apply(
            lambda args: {
                "client-id": args["client_id"],
                "client-secret": args["client_secret"],
            }
        )

        _ = k8s.core.v1.Secret(
            f"{name}-oauth-credentials",
            metadata={
                "name": SkyPilotOAuthCredentials._OAUTH_SECRET_NAME,
                "namespace": namespace,
            },
            string_data=oauth_secret_payload,
            type="Opaque",
            opts=(opts or pulumi.ResourceOptions()).merge(
                pulumi.ResourceOptions(parent=self, depends_on=depends_on)
            ),
        )

        self.secret_name = pulumi.Output.from_input(SkyPilotOAuthCredentials._OAUTH_SECRET_NAME)

        self.register_outputs({"secret_name": self.secret_name})


# ----------------------------------------------------------------------
# API Server
# ----------------------------------------------------------------------

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
