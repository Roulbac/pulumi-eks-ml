"""SkyPilot Data Plane resources."""

import base64
from collections import defaultdict
from dataclasses import dataclass

import pulumi
import pulumi_kubernetes as k8s
import yaml

from ...eks.cluster import EKSCluster
_SP_SA = "sky-sa"
_SP_SYSTEM_NS = "skypilot-system"


@dataclass
class SkyPilotDataPlaneRequest:
    """Request specification for a SkyPilot data plane."""

    # The EKS cluster to create the data plane in
    cluster: EKSCluster
    # The namespace to create the data plane in
    namespace: str


@dataclass(frozen=True)
class SkyPilotDataPlaneCredential:
    """A credential for a SkyPilot data plane."""

    cluster_name: str
    cluster_endpoint: str
    namespace: str
    service_account: str
    ca_cert: str
    token_b64: str

    @property
    def kubeconfig_context(self) -> str:
        """Returns the kubeconfig context name."""
        return f"{self.cluster_name}-{self.namespace}"

    @property
    def username(self) -> str:
        """Returns the username for the kubeconfig user."""
        return f"{self.cluster_name}-{self.namespace}-{self.service_account}"


class SkyPilotDataPlane(pulumi.ComponentResource):
    """
    Represents a Kubernetes infrastructure resource enabling the SkyPilot API server
    to create clusters and workloads.

    This component creates:
      - A dedicated Kubernetes namespace.
      - A service account and the necessary RBAC for SkyPilot's API server to operate in the namespace.
    """

    # For use by the API server
    cluster: EKSCluster
    namespace: k8s.core.v1.Namespace
    service_account: k8s.core.v1.ServiceAccount
    role: k8s.rbac.v1.Role
    role_binding: k8s.rbac.v1.RoleBinding
    cluster_role: k8s.rbac.v1.ClusterRole
    cluster_role_binding: k8s.rbac.v1.ClusterRoleBinding
    service_account_token: k8s.core.v1.Secret

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        namespace: str,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotDataPlane", name, None, opts)

        resource_opts = opts or pulumi.ResourceOptions()

        resource_opts = resource_opts.merge(
            pulumi.ResourceOptions(
                parent=self,
                provider=cluster.k8s_provider,
            )
        )

        self.cluster = cluster
        self.namespace = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata={
                "name": namespace,
                "labels": {"parent": "skypilot"},
            },
            opts=resource_opts,
        )
        self.service_account = k8s.core.v1.ServiceAccount(
            f"{name}-sa",
            metadata={
                "name": _SP_SA,
                "namespace": namespace,
                "labels": {"parent": "skypilot"},
            },
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[self.namespace])
            ),
        )
        self.role = k8s.rbac.v1.Role(
            f"{name}-role",
            metadata={
                "name": f"{_SP_SA}-role",
                "namespace": namespace,
                "labels": {"parent": "skypilot"},
            },
            rules=[
                {
                    "apiGroups": ["*"],
                    "resources": ["*"],
                    "verbs": ["*"],
                }
            ],
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[self.service_account])
            ),
        )
        self.role_binding = k8s.rbac.v1.RoleBinding(
            f"{name}-role-binding",
            metadata={
                "name": f"{_SP_SA}-role-binding",
                "namespace": namespace,
                "labels": {"parent": "skypilot"},
            },
            subjects=[
                {
                    "kind": "ServiceAccount",
                    "name": _SP_SA,
                }
            ],
            role_ref={
                "kind": "Role",
                "name": self.role.metadata.apply(lambda metadata: metadata["name"]),
                "apiGroup": "rbac.authorization.k8s.io",
            },
            opts=resource_opts.merge(
                pulumi.ResourceOptions(
                    depends_on=[self.role],
                )
            ),
        )

        self.cluster_role = k8s.rbac.v1.ClusterRole(
            f"{name}-cluster-role",
            metadata={
                "name": f"{_SP_SA}-{namespace}-cr",
                "labels": {"parent": "skypilot"},
            },
            rules=[
                {
                    "apiGroups": [""],
                    "resources": ["nodes"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": ["node.k8s.io"],
                    "resources": ["runtimeclasses"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["ingressclasses"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["namespaces"],
                    "verbs": ["get", "list", "watch", "update", "patch", "create"],
                },
                {
                    "apiGroups": ["rbac.authorization.k8s.io"],
                    "resources": [
                        "clusterroles",
                        "clusterrolebindings",
                        "roles",
                        "rolebindings",
                    ],
                    "verbs": [
                        "get",
                        "list",
                        "watch",
                        "create",
                        "delete",
                        "update",
                        "patch",
                        "deletecollection",
                    ],
                },
            ],
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[self.role_binding])
            ),
        )

        self.cluster_role_binding = k8s.rbac.v1.ClusterRoleBinding(
            f"{name}-crb",
            metadata={
                "name": f"{_SP_SA}-{namespace}-crb",
                "labels": {"parent": "skypilot"},
            },
            subjects=[
                {
                    "kind": "ServiceAccount",
                    "name": _SP_SA,
                    "namespace": namespace,
                }
            ],
            role_ref={
                "kind": "ClusterRole",
                "name": self.cluster_role.metadata.apply(
                    lambda metadata: metadata["name"]
                ),
                "apiGroup": "rbac.authorization.k8s.io",
            },
            opts=resource_opts.merge(
                pulumi.ResourceOptions(
                    depends_on=[self.cluster_role],
                )
            ),
        )
        self.service_account_token = k8s.core.v1.Secret(
            f"{name}-sa-token",
            metadata={
                "name": f"{_SP_SA}-token",
                "namespace": namespace,
                "annotations": {
                    "kubernetes.io/service-account.name": _SP_SA,
                },
                "labels": {"parent": "skypilot"},
            },
            type="kubernetes.io/service-account-token",
            opts=resource_opts.merge(
                pulumi.ResourceOptions(
                    depends_on=[self.cluster_role_binding],
                )
            ),
        )

    @property
    def credential(self) -> pulumi.Output[SkyPilotDataPlaneCredential]:
        """Returns the credential for this data plane."""
        return pulumi.Output.all(
            cluster_name=self.cluster.cluster_name,
            cluster_endpoint=self.cluster.cluster_endpoint,
            namespace=self.namespace.metadata.apply(lambda metadata: metadata["name"]),
            service_account=self.service_account.metadata.apply(
                lambda metadata: metadata["name"]
            ),
            ca_cert=self.service_account_token.data["ca.crt"],
            token_b64=self.service_account_token.data["token"],
        ).apply(lambda kwargs: SkyPilotDataPlaneCredential(**kwargs))


class SkyPilotFUSEDeviceManager(pulumi.ComponentResource):
    """Configures an EKS cluster to support FUSE device manager for object store mounting."""

    cluster: EKSCluster
    namespace: k8s.core.v1.Namespace
    role: k8s.rbac.v1.Role
    role_binding: k8s.rbac.v1.RoleBinding
    service_account: k8s.core.v1.ServiceAccount
    service_account_token: k8s.core.v1.Secret

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        data_planes: list[SkyPilotDataPlane],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__(
            "pulumi-eks-ml:eks:SkyPilotFUSEDeviceManager", name, None, opts
        )

        resource_opts = opts or pulumi.ResourceOptions()

        resource_opts = resource_opts.merge(
            pulumi.ResourceOptions(
                parent=self,
                provider=cluster.k8s_provider,
            )
        )

        self.cluster = cluster

        self.namespace = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata={
                "name": _SP_SYSTEM_NS,
                "labels": {"parent": "skypilot"},
            },
            opts=resource_opts,
        )

        self.role = k8s.rbac.v1.Role(
            f"{name}-role",
            metadata={
                "name": f"{_SP_SYSTEM_NS}-service-account-role",
                "namespace": _SP_SYSTEM_NS,
                "labels": {"parent": "skypilot"},
            },
            rules=[
                {
                    "apiGroups": ["*"],
                    "resources": ["*"],
                    "verbs": ["*"],
                }
            ],
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[self.namespace])
            ),
        )

        self.role_bindings = []
        for data_plane in data_planes:
            ns_name = data_plane.namespace.metadata.apply(
                lambda metadata: metadata["name"]
            )
            sa_name = data_plane.service_account.metadata.apply(
                lambda metadata: metadata["name"]
            )
            self.role_bindings.append(
                k8s.rbac.v1.RoleBinding(
                    f"{name}-{data_plane._name}-rb",
                    metadata={
                        "name": pulumi.Output.concat(sa_name, "-", ns_name, "-rb"),
                        "namespace": _SP_SYSTEM_NS,
                        "labels": {"parent": "skypilot"},
                    },
                    subjects=[
                        {
                            "kind": "ServiceAccount",
                            "name": sa_name,
                            "namespace": ns_name,
                        }
                    ],
                    role_ref={
                        "kind": "Role",
                        "name": self.role.metadata.apply(
                            lambda metadata: metadata["name"]
                        ),
                        "apiGroup": "rbac.authorization.k8s.io",
                    },
                    opts=resource_opts.merge(
                        pulumi.ResourceOptions(
                            depends_on=[self.role],
                        )
                    ),
                )
            )


class SkyPilotDataPlaneGroup(pulumi.ComponentResource):
    """Configures a group of SkyPilot data planes on a single EKS cluster.

    This includes creating the namespaces, service accounts, and RBAC resources for each
    data plane, as well as installing the FUSE device manager for the cluster.
    """

    def __init__(
        self,
        name: str,
        dp_requests: list[SkyPilotDataPlaneRequest],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotDataPlaneGroup", name, None, opts)

        if len(dp_requests) == 0:
            raise ValueError("At least one data plane request is required")
        # Ensure all requests are for the same cluster
        if not all(
            request.cluster == dp_requests[0].cluster for request in dp_requests
        ):
            raise ValueError("All data plane requests must be for the same cluster")

        self._name = name
        self._cluster = dp_requests[0].cluster
        resource_opts = opts or pulumi.ResourceOptions()
        resource_opts = resource_opts.merge(pulumi.ResourceOptions(parent=self))

        self.data_planes = [
            SkyPilotDataPlane(
                name=f"{name}-{request.namespace}",
                cluster=self._cluster,
                namespace=request.namespace,
                opts=resource_opts,
            )
            for request in dp_requests
        ]

        self._fuse_dm = SkyPilotFUSEDeviceManager(
            name=f"{name}-system",
            cluster=self._cluster,
            data_planes=self.data_planes,
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=self.data_planes)
            ),
        )


def _build_kubeconfig(credentials: list[SkyPilotDataPlaneCredential]) -> str:
    """Build a kubeconfig from a list of cluster entries."""

    clusters_by_name = {}
    for credential in credentials:
        clusters_by_name[credential.cluster_name] = {
            "certificate-authority-data": credential.ca_cert,
            "server": credential.cluster_endpoint,
        }
    clusters = [
        {"name": key, "cluster": value} for key, value in clusters_by_name.items()
    ]
    users = [
        {
            "name": credential.username,
            "user": {"token": base64.b64decode(credential.token_b64).decode("utf-8")},
        }
        for credential in credentials
    ]
    contexts = [
        {
            "name": credential.kubeconfig_context,
            "context": {
                "cluster": credential.cluster_name,
                "user": credential.username,
                "namespace": credential.namespace,
            },
        }
        for credential in credentials
    ]

    return yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "preferences": {},
            "current-context": contexts[0]["name"],
            "contexts": contexts,
            "clusters": clusters,
            "users": users,
        },
        sort_keys=False,
    )


class SkyPilotDataPlaneProvisioner(pulumi.ComponentResource):
    """Sets up multiple data planes (Kubernetes namespaces) that the SkyPilot API server can use to create clusters and deploy workloads."""

    api_server_kube_config: pulumi.Output[str]
    api_server_kube_contexts: pulumi.Output[list[str]]

    def __init__(
        self,
        name: str,
        dp_requests: list[SkyPilotDataPlaneRequest],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__(
            "pulumi-eks-ml:eks:SkyPilotDataPlaneProvisioner", name, None, opts
        )

        dp_requests_by_cluster = defaultdict(list)
        self._dp_groups: list[SkyPilotDataPlaneGroup] = []
        self._credentials: list[pulumi.Output[SkyPilotDataPlaneCredential]] = []

        for request in dp_requests:
            dp_requests_by_cluster[request.cluster.name].append(request)

        for cluster_name, requests in dp_requests_by_cluster.items():
            dp_group = SkyPilotDataPlaneGroup(
                name=f"{name}-{cluster_name}",
                dp_requests=requests,
                opts=pulumi.ResourceOptions(parent=self),
            )
            self._dp_groups.append(dp_group)
            self._credentials.extend([dp.credential for dp in dp_group.data_planes])

        self.api_server_kube_config = pulumi.Output.secret(
            pulumi.Output.all(*self._credentials).apply(_build_kubeconfig)
        )
        self.api_server_kube_contexts = pulumi.Output.all(*self._credentials).apply(
            lambda credentials: [
                credential.kubeconfig_context for credential in credentials
            ]
        )
        self.register_outputs(
            {
                "api_server_kube_config": self.api_server_kube_config,
                "api_server_kube_contexts": self.api_server_kube_contexts,
            }
        )