import pulumi
import pulumi_kubernetes as k8s

from ..eks import config
from ..eks.cluster import EKSCluster


def create_metrics_server(
    name: str,
    k8s_provider: k8s.Provider,
    parent: pulumi.Resource,
    depends_on: list[pulumi.Resource],
    version: str,
) -> k8s.helm.v3.Release:
    """Create metrics server Helm release."""
    release_name = "metrics-server"
    return k8s.helm.v3.Release(
        f"{name}-metrics-server",
        name=release_name,
        chart="metrics-server",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://kubernetes-sigs.github.io/metrics-server",
        ),
        version=version,
        namespace="kube-system",
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=depends_on,
        ),
    )


class MetricsServerAddon(pulumi.ComponentResource):
    """Kubernetes Metrics Server as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    version_key = "metrics_server"

    def __init__(
        self,
        name: str,
        opts: pulumi.ResourceOptions,
        version: str = config.METRICS_SERVER_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:MetricsServerAddon", name, None, opts)

        self.helm_release = create_metrics_server(
            name=name,
            k8s_provider=opts.providers["kubernetes"],
            parent=self,
            depends_on=opts.depends_on or [],
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
    ) -> "MetricsServerAddon":
        """Create a MetricsServerAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-metrics-server",
            version=version or config.METRICS_SERVER_VERSION,
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
