"""SkyPilot data plane IAM identity resources."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping

import pulumi
import pulumi_kubernetes as k8s

from ...eks.cluster import EKSCluster
from ...eks.irsa import IRSA

_SP_USER_SA = "sky-user-sa"


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
