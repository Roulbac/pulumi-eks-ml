import pulumi
import pulumi_kubernetes as k8s

from ..eks import config
from ..eks.cluster import EKSCluster


def create_nvidia_device_plugin(
    name: str,
    k8s_provider: k8s.Provider,
    parent: pulumi.Resource,
    depends_on: list[pulumi.Resource],
    version: str,
    custom_tolerations: list[dict] | None = None,
) -> k8s.helm.v3.Release:
    """Create NVIDIA device plugin Helm release."""
    release_name = "nvidia-device-plugin"
    # Default tolerations for GPU nodes (these are built into the chart but we can extend them)
    default_tolerations = [
        {"key": "CriticalAddonsOnly", "operator": "Exists"},
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},
    ]

    # Merge with any custom tolerations
    all_tolerations = default_tolerations.copy()
    if custom_tolerations:
        all_tolerations.extend(custom_tolerations)

    return k8s.helm.v3.Release(
        f"{name}-nvidia-device-plugin",
        name=release_name,
        chart="nvidia-device-plugin",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://nvidia.github.io/k8s-device-plugin",
        ),
        version=version,
        namespace="kube-system",
        values={
            "devicePlugin": {"enabled": True},
            "gfd": {"enabled": True},
            "tolerations": all_tolerations,
        },
        skip_await=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=depends_on,
        ),
    )


def _collect_custom_tolerations(
    node_pools: list[config.NodePoolConfig],
) -> list[dict]:
    """Collect custom tolerations from node pools for system components."""
    custom_tolerations: list[dict] = []
    for node_pool in node_pools:
        if node_pool.taints:
            for taint in node_pool.taints:
                # Use "Exists" operator for system components to tolerate any value
                toleration = taint.to_toleration(operator="Exists")
                custom_tolerations.append(toleration)
    return custom_tolerations


class NvidiaDevicePluginAddon(pulumi.ComponentResource):
    """NVIDIA device plugin as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    version_key = "nvidia_device_plugin"

    def __init__(
        self,
        name: str,
        opts: pulumi.ResourceOptions,
        custom_tolerations: list[dict] | None = None,
        version: str = config.NVIDIA_DEVICE_PLUGIN_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:NvidiaDevicePluginAddon", name, None, opts)

        self.helm_release = create_nvidia_device_plugin(
            name=name,
            k8s_provider=opts.providers["kubernetes"],
            parent=self,
            depends_on=opts.depends_on or [],
            custom_tolerations=custom_tolerations,
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
    ) -> "NvidiaDevicePluginAddon":
        """Create a NvidiaDevicePluginAddon from an EKSCluster instance."""
        custom_tolerations = _collect_custom_tolerations(cluster.node_pools or [])
        return cls(
            name=f"{cluster.name}-nvidia-device-plugin",
            custom_tolerations=custom_tolerations,
            version=version or config.NVIDIA_DEVICE_PLUGIN_VERSION,
            opts=pulumi.ResourceOptions(
                parent=parent,
                depends_on=[
                    cluster,
                    *(extra_dependencies or []),
                ],
                providers={
                    "kubernetes": cluster.k8s_provider,
                },
            ),
        )
