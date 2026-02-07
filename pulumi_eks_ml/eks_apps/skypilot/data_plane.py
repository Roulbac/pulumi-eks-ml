"""SkyPilot Data Plane resources."""

import base64
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping

import pulumi
import pulumi_kubernetes as k8s
import yaml

from ...eks.cluster import EKSCluster
from ...eks.irsa import IRSA

_SP_SA = "sky-sa"
_SP_USER_SA = "sky-user-sa"
_SP_SYSTEM_NS = "skypilot-system"


# ----------------------------------------------------------------------
# Data Plane User Identity
# ----------------------------------------------------------------------

@dataclass
class SkyPilotDataPlaneUserIdentityRequest:
    """Request specification for a SkyPilot data plane user identity.

    Defines how the SkyPilot user service account in a specific cluster and namespace
    should be configured with AWS IAM permissions.
    """

    # The EKS cluster where the user service account resides
    cluster: EKSCluster
    # The namespace where the user service account will be created
    namespace: str
    # ARNs of any IAM policies to attach to a new IRSA role.
    # Mutually exclusive with `role_arn`.
    irsa_attached_policies: list[str] = field(default_factory=list)
    # ARN of an existing IAM role to bind to the service account.
    # Mutually exclusive with `irsa_attached_policies`.
    role_arn: pulumi.Input[str] | None = None


class SkyPilotDataPlaneUserIdentity(pulumi.ComponentResource):
    """Manages the IAM identity for a SkyPilot data plane user service account.

    This component creates the Kubernetes ServiceAccount used by SkyPilot user workloads
    and associates it with an AWS IAM role. The role is either created as a new IRSA
    role (if policies are provided) or bound from an existing role ARN.
    """

    cluster: EKSCluster
    namespace: str
    service_account: k8s.core.v1.ServiceAccount
    iam_role_arn: pulumi.Output[str]
    service_account_name: pulumi.Output[str]
    context_name: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        namespace: str,
        irsa_attached_policies: list[str] | None = None,
        role_arn: pulumi.Input[str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotDataPlaneUserIdentity", name, None, opts)

        resource_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(
                parent=self,
                provider=cluster.k8s_provider,
            )
        )

        if role_arn and irsa_attached_policies:
            raise ValueError("Provide either role_arn or irsa_attached_policies, not both")

        self.cluster = cluster
        self.namespace = namespace

        self._irsa: IRSA | None = None
        if role_arn is None:
            attached_policies = irsa_attached_policies or []
            self._irsa = IRSA(
                name=f"{name}-irsa",
                role_name=f"{cluster.name}-{namespace}-user-role",
                oidc_provider_arn=cluster.oidc_provider_arn,
                oidc_issuer=cluster.oidc_issuer,
                trust_sa_namespace=namespace,
                trust_sa_name=_SP_USER_SA,
                attached_policies=attached_policies,
                opts=pulumi.ResourceOptions(
                    parent=self,
                    provider=cluster.aws_provider,
                ),
            )
            self.iam_role_arn = self._irsa.iam_role_arn
        else:
            self.iam_role_arn = pulumi.Output.from_input(role_arn)

        sa_opts = resource_opts
        if self._irsa is not None:
            sa_opts = sa_opts.merge(pulumi.ResourceOptions(depends_on=[self._irsa]))

        self.service_account = k8s.core.v1.ServiceAccount(
            f"{name}-user-sa",
            metadata={
                "name": _SP_USER_SA,
                "namespace": namespace,
                "labels": {"parent": "skypilot"},
                "annotations": {
                    "eks.amazonaws.com/role-arn": self.iam_role_arn,
                },
            },
            opts=sa_opts,
        )

        self.service_account_name = self.service_account.metadata.apply(
            lambda metadata: metadata["name"]
        )
        # Context name matches the SkyPilotDataPlaneCredential convention: {cluster}-{namespace}
        self.context_name = pulumi.Output.all(
            self.cluster.cluster_name, namespace
        ).apply(lambda values: f"{values[0]}-{values[1]}")

        self.register_outputs(
            {
                "iam_role_arn": self.iam_role_arn,
                "service_account_name": self.service_account_name,
                "context_name": self.context_name,
            }
        )
        
    @property
    def identity_details(self) -> pulumi.Output[dict]:
        """Returns details about the user identity."""
        return pulumi.Output.all(
            namespace=self.namespace,
            service_account=self.service_account_name,
            role_arn=self.iam_role_arn,
            cluster_name=self.cluster.cluster_name,
            oidc_issuer=self.cluster.oidc_issuer,
            oidc_provider_arn=self.cluster.oidc_provider_arn,
        ).apply(
            lambda args: {
                "namespace": args["namespace"],
                "service_account": args["service_account"],
                "role_arn": args["role_arn"],
                "cluster_name": args["cluster_name"],
                "oidc_issuer": args["oidc_issuer"],
                "oidc_provider_arn": args["oidc_provider_arn"],
            }
        )


class SkyPilotDataPlaneUserIdentityProvisioner(pulumi.ComponentResource):
    """Provisioner for SkyPilot data plane user identities across multiple clusters.

    This component processes a list of identity requests and creates the corresponding
    SkyPilotDataPlaneUserIdentity resources. It exposes a mapping of kubeconfig contexts
    to service account names, which is required by the SkyPilot API server.
    """

    service_accounts_by_context: pulumi.Output[Mapping[str, str]]
    identities: list[SkyPilotDataPlaneUserIdentity]

    def __init__(
        self,
        name: str,
        identity_requests: list[SkyPilotDataPlaneUserIdentityRequest],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__(
            "pulumi-eks-ml:eks:SkyPilotDataPlaneUserIdentityProvisioner", name, None, opts
        )

        if len(identity_requests) == 0:
            raise ValueError("At least one user identity request is required")

        resource_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(parent=self)
        )

        requests_by_cluster = defaultdict(list)
        for request in identity_requests:
            requests_by_cluster[request.cluster.name].append(request)

        self.identities = []
        for cluster_name, requests in requests_by_cluster.items():
            for request in requests:
                self.identities.append(
                    SkyPilotDataPlaneUserIdentity(
                        name=f"{name}-{cluster_name}-{request.namespace}",
                        cluster=request.cluster,
                        namespace=request.namespace,
                        irsa_attached_policies=request.irsa_attached_policies,
                        role_arn=request.role_arn,
                        opts=resource_opts,
                    )
                )

        mappings = [
            pulumi.Output.all(identity.context_name, identity.service_account_name)
            for identity in self.identities
        ]
        self.service_accounts_by_context = pulumi.Output.all(*mappings).apply(
            lambda items: {context: name for context, name in items}
        )

        self.register_outputs(
            {
                "service_accounts_by_context": self.service_accounts_by_context,
            }
        )


# ----------------------------------------------------------------------
# Data Plane Infrastructure
# ----------------------------------------------------------------------

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
                depends_on=[cluster.k8s_provider],
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
