import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks import config
from ..eks.cluster import EKSCluster, EKSClusterAddon
from ..eks.irsa import IRSA


def create_external_dns(
    name: str,
    cluster_name: pulumi.Input[str],
    oidc_provider_arn: pulumi.Input[str],
    oidc_issuer: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    aws_provider: aws.Provider,
    depends_on: list[pulumi.Resource],
    parent: pulumi.Resource,
    version: str,
) -> k8s.helm.v3.Release:
    """Create ExternalDNS Helm release with IRSA."""
    release_name = "external-dns"

    irsa = IRSA(
        f"{name}-external-dns-irsa",
        role_name=f"{name}-external-dns-role",
        oidc_provider_arn=oidc_provider_arn,
        oidc_issuer=oidc_issuer,
        trust_sa_namespace="kube-system",
        trust_sa_name="external-dns",
        inline_policies=[
            aws.iam.RoleInlinePolicyArgs(
                name=f"{name}-external-dns-policy",
                policy=pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["route53:ChangeResourceRecordSets"],
                                "Resource": "arn:aws:route53:::hostedzone/*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "route53:ListHostedZones",
                                    "route53:ListResourceRecordSets",
                                    "route53:ListHostedZonesByName",
                                    "route53:GetChange",
                                ],
                                "Resource": "*",
                            },
                        ],
                    }
                ),
            )
        ],
        opts=pulumi.ResourceOptions(
            parent=parent,
            providers={"kubernetes": k8s_provider, "aws": aws_provider},
            depends_on=depends_on,
        ),
    )

    values = {
        # Chart 1.20.0 uses provider.name + extraArgs for AWS specifics.
        "provider": {"name": "aws"},
        "extraArgs": {"aws-zone-type": "private"},
        "serviceAccount": {
            "create": True,
            "name": "external-dns",
            "annotations": {"eks.amazonaws.com/role-arn": irsa.iam_role.arn},
        },
        "txtOwnerId": cluster_name,
    }

    return k8s.helm.v3.Release(
        f"{name}-external-dns",
        name=release_name,
        chart="external-dns",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kubernetes-sigs.github.io/external-dns",
        ),
        version=version,
        namespace="kube-system",
        values=values,
        skip_await=False,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[*depends_on, irsa.iam_role],
        ),
    )


class ExternalDNSAddon(pulumi.ComponentResource, EKSClusterAddon):
    """ExternalDNS as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    version_key = "external_dns"

    def __init__(
        self,
        name: str,
        cluster_name: pulumi.Input[str],
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
        version: str = config.EXTERNAL_DNS_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:ExternalDNSAddon", name, None, opts)

        self.helm_release = create_external_dns(
            name=name,
            cluster_name=cluster_name,
            oidc_provider_arn=oidc_provider_arn,
            oidc_issuer=oidc_issuer,
            k8s_provider=opts.providers["kubernetes"],
            aws_provider=opts.providers["aws"],
            depends_on=opts.depends_on or [],
            parent=self,
            version=version,
        )

        self.register_outputs({"helm_release": self.helm_release})

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        parent: pulumi.Resource | None = None,
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "ExternalDNSAddon":
        """Create an ExternalDNSAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-external-dns",
            cluster_name=cluster.k8s.eks_cluster.name,
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
            version=version or config.EXTERNAL_DNS_VERSION,
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
