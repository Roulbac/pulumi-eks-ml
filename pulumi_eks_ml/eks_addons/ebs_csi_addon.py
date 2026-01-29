import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.cluster import EKSCluster
from ..eks import config
from ..eks.irsa import IRSA


def install_ebs_csi_driver(
    name: str,
    oidc_provider_arn: pulumi.Input[str],
    oidc_issuer: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    dependencies: list[pulumi.Resource],
    parent: pulumi.Resource,
    version: str,
) -> k8s.helm.v3.Release:
    """Install AWS EBS CSI driver with IRSA."""

    # Create IRSA for EBS CSI controller
    ebs_csi_irsa = IRSA(
        f"{name}-ebs-csi-irsa",
        role_name=f"{name}-ebs-csi-role",
        oidc_provider_arn=oidc_provider_arn,
        oidc_issuer=oidc_issuer,
        trust_sa_namespace="kube-system",
        trust_sa_name="ebs-csi-controller-sa",
        inline_policies=[
            aws.iam.RoleInlinePolicyArgs(
                name=f"{name}-ebs-csi-policy",
                policy=pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ec2:CreateSnapshot",
                                    "ec2:AttachVolume",
                                    "ec2:DetachVolume",
                                    "ec2:ModifyVolume",
                                    "ec2:DescribeAvailabilityZones",
                                    "ec2:DescribeInstances",
                                    "ec2:DescribeSnapshots",
                                    "ec2:DescribeTags",
                                    "ec2:DescribeVolumes",
                                    "ec2:DescribeVolumesModifications",
                                    "ec2:CreateTags",
                                    "ec2:CreateVolume",
                                    "ec2:DeleteVolume",
                                    "ec2:DeleteSnapshot",
                                ],
                                "Resource": "*",
                            }
                        ],
                    }
                ),
            )
        ],
        opts=pulumi.ResourceOptions(parent=parent),
    )

    return k8s.helm.v3.Release(
        f"{name}-ebs-csi",
        name="ebs-csi",
        chart="aws-ebs-csi-driver",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kubernetes-sigs.github.io/aws-ebs-csi-driver",
        ),
        version=version,
        namespace="kube-system",
        values={
            "controller": {
                "serviceAccount": {
                    "name": "ebs-csi-controller-sa",
                    "annotations": {
                        "eks.amazonaws.com/role-arn": ebs_csi_irsa.iam_role.arn
                    },
                },
            },
        },
        skip_await=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[*dependencies, ebs_csi_irsa.iam_role],
        ),
    )


def create_default_gp3_storageclass(
    name: str,
    k8s_provider: k8s.Provider,
    ebs_csi_release: k8s.helm.v3.Release,
    parent: pulumi.Resource,
) -> k8s.storage.v1.StorageClass:
    """Create default gp3 StorageClass."""
    return k8s.storage.v1.StorageClass(
        f"{name}-default-gp3-storageclass",
        metadata={
            "name": "gp3",
            "annotations": {"storageclass.kubernetes.io/is-default-class": "true"},
        },
        provisioner="ebs.csi.aws.com",
        parameters={
            "type": "gp3",
            "encrypted": "true",
            "fsType": "ext4",
        },
        reclaim_policy="Delete",
        volume_binding_mode="WaitForFirstConsumer",
        allow_volume_expansion=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[ebs_csi_release],
        ),
    )


class EbsCsiAddon(pulumi.ComponentResource):
    """AWS EBS CSI driver and gp3 StorageClass as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    storage_class: k8s.storage.v1.StorageClass
    version_key = "ebs_csi"

    def __init__(
        self,
        name: str,
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
        version: str = config.EBS_CSI_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:EbsCsiAddon", name, None, opts)

        self.helm_release = install_ebs_csi_driver(
            name=name,
            oidc_provider_arn=oidc_provider_arn,
            oidc_issuer=oidc_issuer,
            k8s_provider=opts.providers["kubernetes"],
            dependencies=opts.depends_on or [],
            parent=self,
            version=version,
        )

        self.storage_class = create_default_gp3_storageclass(
            name=name,
            k8s_provider=opts.providers["kubernetes"],
            ebs_csi_release=self.helm_release,
            parent=self,
        )

        self.register_outputs(
            {
                "helm_release": self.helm_release,
                "storage_class": self.storage_class,
            }
        )

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        parent: pulumi.Resource | None = None,
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "EbsCsiAddon":
        """Create an EbsCsiAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-ebs-csi",
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
            version=version or config.EBS_CSI_VERSION,
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
