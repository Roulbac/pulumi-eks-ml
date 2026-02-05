import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.cluster import EKSCluster, EKSClusterAddon
from ..eks import config
from ..eks.irsa import IRSA


def install_efs_csi_driver(
    name: str,
    oidc_provider_arn: pulumi.Input[str],
    oidc_issuer: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    dependencies: list[pulumi.Resource],
    parent: pulumi.Resource,
    version: str,
) -> k8s.helm.v3.Release:
    """Install AWS EFS CSI driver with IRSA."""

    # Create IRSA for EFS CSI controller
    efs_csi_irsa = IRSA(
        f"{name}-efs-csi-irsa",
        role_name=f"{name}-efs-csi-role",
        oidc_provider_arn=oidc_provider_arn,
        oidc_issuer=oidc_issuer,
        trust_sa_namespace="kube-system",
        trust_sa_name="efs-csi-controller-sa",
        attached_policies=[
            "arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy",
        ],
        opts=pulumi.ResourceOptions(parent=parent),
    )

    return k8s.helm.v3.Release(
        f"{name}-efs-csi",
        name="efs-csi",
        chart="aws-efs-csi-driver",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kubernetes-sigs.github.io/aws-efs-csi-driver",
        ),
        version=version,
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


def create_default_efs_fs_and_sc(
    name: str,
    subnet_ids: pulumi.Input[list[str]],
    node_security_group_id: pulumi.Input[str],
    aws_provider: aws.Provider,
    k8s_provider: k8s.Provider,
    parent: pulumi.Resource,
    dependencies: list[pulumi.Resource],
) -> tuple[aws.efs.FileSystem, k8s.storage.v1.StorageClass]:
    """Create default EFS FileSystem and StorageClass. The default storage class can be used for dynamic provisioning of PVs."""
    aws_opts = pulumi.ResourceOptions(
        provider=aws_provider,
        parent=parent,
        depends_on=dependencies,
    )
    efs_fs = aws.efs.FileSystem(
        f"{name}-efs-fs",
        encrypted=True,
        performance_mode="generalPurpose",
        throughput_mode="bursting",
        opts=aws_opts,
    )

    def create_mount_targets(
        subnet_ids: list[str],
        node_security_group_id: str,
    ) -> list[aws.efs.MountTarget]:
        mount_targets = []
        for subnet_id in subnet_ids:
            mount_targets.append(
                aws.efs.MountTarget(
                    f"{name}-efs-mt-{subnet_id}",
                    file_system_id=efs_fs.id,
                    subnet_id=subnet_id,
                    security_groups=[node_security_group_id],
                    opts=aws_opts.merge(pulumi.ResourceOptions(parent=efs_fs)),
                )
            )
        return mount_targets

    pulumi.Output.all(
        subnet_ids=subnet_ids, node_security_group_id=node_security_group_id
    ).apply(lambda kwargs: create_mount_targets(**kwargs))

    efs_sc = k8s.storage.v1.StorageClass(
        f"{name}-efs-sc",
        metadata={"name": config.EFS_CSI_DEFAULT_SC_NAME},
        provisioner="efs.csi.aws.com",
        parameters={
            "provisioningMode": "efs-ap",
            "fileSystemId": efs_fs.id,
            "directoryPerms": "700",
            "uid": "0",
            "gid": "0",
            "basePath": "/dynamic",
            "subPathPattern": "${.PVC.namespace}/${.PVC.name}",
        },
        reclaim_policy="Delete",
        volume_binding_mode="Immediate",
        allow_volume_expansion=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[efs_fs, *dependencies],
        ),
    )
    return efs_fs, efs_sc


class EFSCSIAddon(pulumi.ComponentResource, EKSClusterAddon):
    """AWS EFS CSI driver as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    default_sc: k8s.storage.v1.StorageClass
    default_fs: aws.efs.FileSystem

    version_key = "efs_csi"

    def __init__(
        self,
        name: str,
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        subnet_ids: pulumi.Input[list[str]],
        node_security_group_id: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
        version: str = config.EFS_CSI_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:EFSCSIAddon", name, None, opts)

        self.helm_release = install_efs_csi_driver(
            name=name,
            oidc_provider_arn=oidc_provider_arn,
            oidc_issuer=oidc_issuer,
            k8s_provider=opts.providers["kubernetes"],
            dependencies=opts.depends_on or [],
            parent=self,
            version=version,
        )

        self.default_fs, self.default_sc = create_default_efs_fs_and_sc(
            name=name,
            subnet_ids=subnet_ids,
            node_security_group_id=node_security_group_id,
            aws_provider=opts.providers["aws"],
            k8s_provider=opts.providers["kubernetes"],
            parent=self,
            dependencies=opts.depends_on or [],
        )

        self.register_outputs(
            {
                "helm_release": self.helm_release,
                "default_sc": self.default_sc,
                "default_fs": self.default_fs,
            }
        )

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        parent: pulumi.Resource | None = None,
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "EFSCSIAddon":
        """Create an EFSCSIAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-efs-csi",
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
            subnet_ids=cluster.subnet_ids,
            node_security_group_id=cluster.node_security_group_id,
            version=version or config.EFS_CSI_VERSION,
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
