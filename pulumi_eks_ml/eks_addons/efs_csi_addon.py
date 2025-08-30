import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.cluster import EKSCluster
from ..eks.irsa import IRSA


def install_efs_csi_driver(
    name: str,
    oidc_provider_arn: pulumi.Input[str],
    oidc_issuer: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    dependencies: list[pulumi.Resource],
    parent: pulumi.Resource,
) -> k8s.helm.v3.Release:
    """Install AWS EFS CSI driver with IRSA."""

    # Create inline policy for EFS CSI
    efs_policy = pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticfilesystem:DescribeAccessPoints",
                        "elasticfilesystem:DescribeFileSystems",
                        "elasticfilesystem:DescribeMountTargets",
                        "elasticfilesystem:CreateAccessPoint",
                        "elasticfilesystem:DeleteAccessPoint",
                        "elasticfilesystem:TagResource",
                        "elasticfilesystem:UntagResource",
                    ],
                    "Resource": "*",
                }
            ],
        }
    )

    # Create IRSA for EFS CSI controller
    efs_csi_irsa = IRSA(
        f"{name}-efs-csi-irsa",
        role_name=f"{name}-efs-csi-role",
        oidc_provider_arn=oidc_provider_arn,
        oidc_issuer=oidc_issuer,
        trust_sa_namespace="kube-system",
        trust_sa_name="efs-csi-controller-sa",
        inline_policies=[
            aws.iam.RoleInlinePolicyArgs(
                name=f"{name}-efs-csi-policy",
                policy=efs_policy,
            )
        ],
        opts=pulumi.ResourceOptions(parent=parent),
    )

    return k8s.helm.v3.Release(
        f"{name}-efs-csi",
        chart="aws-efs-csi-driver",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kubernetes-sigs.github.io/aws-efs-csi-driver",
        ),
        version="3.1.2",
        namespace="kube-system",
        values={
            "controller": {
                "serviceAccount": {
                    "name": "efs-csi-controller-sa",
                    "annotations": {
                        "eks.amazonaws.com/role-arn": efs_csi_irsa.iam_role.arn
                    },
                },
            },
        },
        skip_await=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[*dependencies, efs_csi_irsa.iam_role],
        ),
    )


class EfsCsiAddon(pulumi.ComponentResource):
    """AWS EFS CSI driver as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release

    def __init__(
        self,
        name: str,
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
    ):
        super().__init__("pulumi-eks-ml:eks:EfsCsiAddon", name, None, opts)

        self.helm_release = install_efs_csi_driver(
            name=name,
            oidc_provider_arn=oidc_provider_arn,
            oidc_issuer=oidc_issuer,
            k8s_provider=opts.providers["kubernetes"],
            dependencies=opts.depends_on or [],
            parent=self,
        )

        self.register_outputs({"helm_release": self.helm_release})

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        parent: pulumi.Resource | None = None,
        extra_dependencies: list[pulumi.Resource] | None = None,
    ) -> "EfsCsiAddon":
        """Create an EfsCsiAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-efs-csi",
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
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
