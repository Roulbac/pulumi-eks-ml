"""Karpenter addon for EKS clusters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from . import config
from .irsa import IRSA

if TYPE_CHECKING:
    from .cluster import EKSCluster


def create_karpenter_controller_policy(
    cluster_name: str,
    region: str,
    account_id: str,
    karpenter_node_role_arn: str,
) -> dict:
    """Create comprehensive IAM policy for Karpenter controller."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowScopedEC2InstanceAccessActions",
                "Effect": "Allow",
                "Action": ["ec2:RunInstances", "ec2:CreateFleet"],
                "Resource": [
                    f"arn:aws:ec2:{region}::image/*",
                    f"arn:aws:ec2:{region}::snapshot/*",
                    f"arn:aws:ec2:{region}:*:security-group/*",
                    f"arn:aws:ec2:{region}:*:subnet/*",
                    f"arn:aws:ec2:{region}:*:capacity-reservation/*",
                ],
            },
            {
                "Sid": "AllowScopedEC2LaunchTemplateAccessActions",
                "Effect": "Allow",
                "Action": ["ec2:RunInstances", "ec2:CreateFleet"],
                "Resource": [f"arn:aws:ec2:{region}:*:launch-template/*"],
                "Condition": {
                    "StringEquals": {
                        f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned"
                    },
                    "StringLike": {"aws:ResourceTag/karpenter.sh/nodepool": "*"},
                },
            },
            {
                "Sid": "AllowScopedEC2InstanceActionsWithTags",
                "Effect": "Allow",
                "Action": [
                    "ec2:RunInstances",
                    "ec2:CreateFleet",
                    "ec2:CreateLaunchTemplate",
                ],
                "Resource": [
                    f"arn:aws:ec2:{region}:*:fleet/*",
                    f"arn:aws:ec2:{region}:*:instance/*",
                    f"arn:aws:ec2:{region}:*:volume/*",
                    f"arn:aws:ec2:{region}:*:network-interface/*",
                    f"arn:aws:ec2:{region}:*:launch-template/*",
                    f"arn:aws:ec2:{region}:*:spot-instances-request/*",
                ],
                "Condition": {
                    "StringEquals": {
                        f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned",
                        "aws:RequestTag/eks:eks-cluster-name": cluster_name,
                    },
                    "StringLike": {"aws:RequestTag/karpenter.sh/nodepool": "*"},
                },
            },
            {
                "Sid": "AllowScopedResourceCreationTagging",
                "Effect": "Allow",
                "Action": ["ec2:CreateTags"],
                "Resource": [
                    f"arn:aws:ec2:{region}:*:fleet/*",
                    f"arn:aws:ec2:{region}:*:instance/*",
                    f"arn:aws:ec2:{region}:*:volume/*",
                    f"arn:aws:ec2:{region}:*:network-interface/*",
                    f"arn:aws:ec2:{region}:*:launch-template/*",
                    f"arn:aws:ec2:{region}:*:spot-instances-request/*",
                ],
                "Condition": {
                    "StringEquals": {
                        f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned",
                        "aws:RequestTag/eks:eks-cluster-name": cluster_name,
                        "ec2:CreateAction": [
                            "RunInstances",
                            "CreateFleet",
                            "CreateLaunchTemplate",
                        ],
                    },
                    "StringLike": {"aws:RequestTag/karpenter.sh/nodepool": "*"},
                },
            },
            {
                "Sid": "AllowScopedResourceTagging",
                "Effect": "Allow",
                "Action": ["ec2:CreateTags"],
                "Resource": [f"arn:aws:ec2:{region}:*:instance/*"],
                "Condition": {
                    "StringEquals": {
                        f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned"
                    },
                    "StringLike": {"aws:ResourceTag/karpenter.sh/nodepool": "*"},
                    "StringEqualsIfExists": {
                        "aws:RequestTag/eks:eks-cluster-name": cluster_name
                    },
                    "ForAllValues:StringEquals": {
                        "aws:TagKeys": [
                            "eks:eks-cluster-name",
                            "karpenter.sh/nodeclaim",
                            "Name",
                        ]
                    },
                },
            },
            {
                "Sid": "AllowScopedDeletion",
                "Effect": "Allow",
                "Action": [
                    "ec2:TerminateInstances",
                    "ec2:DeleteLaunchTemplate",
                ],
                "Resource": [
                    f"arn:aws:ec2:{region}:*:instance/*",
                    f"arn:aws:ec2:{region}:*:launch-template/*",
                ],
                "Condition": {
                    "StringEquals": {
                        f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned"
                    },
                    "StringLike": {"aws:ResourceTag/karpenter.sh/nodepool": "*"},
                },
            },
            {
                "Sid": "AllowRegionalReadActions",
                "Effect": "Allow",
                "Action": [
                    "ec2:DescribeAvailabilityZones",
                    "ec2:DescribeImages",
                    "ec2:DescribeInstances",
                    "ec2:DescribeInstanceTypeOfferings",
                    "ec2:DescribeInstanceTypes",
                    "ec2:DescribeLaunchTemplates",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeSpotPriceHistory",
                    "ec2:DescribeSubnets",
                ],
                "Resource": "*",
                "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
            },
            {
                "Sid": "AllowSSMReadActions",
                "Effect": "Allow",
                "Action": ["ssm:GetParameter"],
                "Resource": [f"arn:aws:ssm:{region}::parameter/aws/service/*"],
            },
            {
                "Sid": "AllowPricingReadActions",
                "Effect": "Allow",
                "Action": ["pricing:GetProducts"],
                "Resource": "*",
            },
            {
                "Sid": "AllowPassingInstanceRole",
                "Effect": "Allow",
                "Action": ["iam:PassRole"],
                "Resource": karpenter_node_role_arn,
                "Condition": {
                    "StringEquals": {"iam:PassedToService": ["ec2.amazonaws.com"]}
                },
            },
            {
                "Sid": "AllowScopedInstanceProfileCreationActions",
                "Effect": "Allow",
                "Action": ["iam:CreateInstanceProfile"],
                "Resource": [f"arn:aws:iam::{account_id}:instance-profile/*"],
                "Condition": {
                    "StringEquals": {
                        f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned",
                        "aws:RequestTag/eks:eks-cluster-name": cluster_name,
                        "aws:RequestTag/topology.kubernetes.io/region": region,
                    },
                    "StringLike": {
                        "aws:RequestTag/karpenter.k8s.aws/ec2nodeclass": "*"
                    },
                },
            },
            {
                "Sid": "AllowScopedInstanceProfileTagActions",
                "Effect": "Allow",
                "Action": ["iam:TagInstanceProfile"],
                "Resource": [f"arn:aws:iam::{account_id}:instance-profile/*"],
                "Condition": {
                    "StringEquals": {
                        f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned",
                        "aws:ResourceTag/topology.kubernetes.io/region": region,
                        f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned",
                        "aws:RequestTag/eks:eks-cluster-name": cluster_name,
                        "aws:RequestTag/topology.kubernetes.io/region": region,
                    },
                    "StringLike": {
                        "aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass": "*",
                        "aws:RequestTag/karpenter.k8s.aws/ec2nodeclass": "*",
                    },
                },
            },
            {
                "Sid": "AllowScopedInstanceProfileActions",
                "Effect": "Allow",
                "Action": [
                    "iam:AddRoleToInstanceProfile",
                    "iam:RemoveRoleFromInstanceProfile",
                    "iam:DeleteInstanceProfile",
                ],
                "Resource": [f"arn:aws:iam::{account_id}:instance-profile/*"],
                "Condition": {
                    "StringEquals": {
                        f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned",
                        "aws:ResourceTag/topology.kubernetes.io/region": region,
                    },
                    "StringLike": {
                        "aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass": "*"
                    },
                },
            },
            {
                "Sid": "AllowInstanceProfileReadActions",
                "Effect": "Allow",
                "Action": ["iam:GetInstanceProfile", "iam:ListInstanceProfiles"],
                "Resource": [f"arn:aws:iam::{account_id}:instance-profile/*"],
            },
            {
                "Sid": "AllowAPIServerEndpointDiscovery",
                "Effect": "Allow",
                "Action": ["eks:DescribeCluster"],
                "Resource": [
                    f"arn:aws:eks:{region}:{account_id}:cluster/{cluster_name}"
                ],
            },
        ],
    }


class KarpenterAddon(pulumi.ComponentResource):
    """Karpenter addon as a Pulumi ComponentResource."""

    namespace: k8s.core.v1.Namespace
    helm_release: k8s.helm.v3.Release
    karpenter_node_role: aws.iam.Role
    karpenter_role: aws.iam.Role
    node_pools: dict[str, pulumi.Resource]

    version_key = "karpenter"

    def __init__(
        self,
        name: str,
        cluster_name: pulumi.Input[str],
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        node_security_group_id: pulumi.Input[str],
        subnet_ids: pulumi.Input[list[str]],
        opts: pulumi.ResourceOptions,
        node_pool_configs: list[config.NodePoolConfig] | None = None,
        karpenter_version: str = config.KARPENTER_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:KarpenterAddon", name, None, opts)

        self._cluster_name = cluster_name
        self._oidc_provider_arn = oidc_provider_arn
        self._oidc_issuer = oidc_issuer
        self._node_security_group_id = node_security_group_id
        self._subnet_ids = subnet_ids

        self._aws_provider = opts.providers["aws"]
        self._k8s_provider = opts.providers["kubernetes"]

        self.node_pools = {}

        # Create namespace
        self.namespace = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata={"name": config.KARPENTER_NAMESPACE},
            opts=pulumi.ResourceOptions(parent=self, retain_on_delete=True, provider=self._k8s_provider),
        )

        # IAM: controller IRSA and node role
        self._create_iam_roles(name)

        # IAM policies
        self._create_karpenter_policies(name)

        # Helm: install Karpenter controller
        self._install_karpenter_release(name, karpenter_version)

        # Node pools
        for np in node_pool_configs or []:
            self._add_node_pool(np)

        self.register_outputs(
            {
                "namespace": self.namespace,
                "helm_release": self.helm_release,
                "karpenter_node_role_arn": self.karpenter_node_role.arn,
                "karpenter_role_arn": self.karpenter_role.arn,
            }
        )

    def _create_iam_roles(self, name: str) -> None:
        # Node role for Karpenter-provisioned EC2 nodes (EC2 assumes this)
        self.karpenter_node_role = aws.iam.Role(
            f"{name}-node-role",
            assume_role_policy=pulumi.Output.json_dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=self._aws_provider),
        )

        # Controller role via reusable IRSA component (attach controller policy inline)
        invoke_opts = pulumi.InvokeOptions(provider=self._aws_provider)
        controller_inline_policy = pulumi.Output.all(
            cluster_name=self._cluster_name,
            region=aws.get_region(opts=invoke_opts).region,
            account_id=aws.get_caller_identity(opts=invoke_opts).account_id,
            karpenter_node_role_arn=self.karpenter_node_role.arn,
        ).apply(
            lambda a: pulumi.Output.json_dumps(
                create_karpenter_controller_policy(
                    cluster_name=a["cluster_name"],
                    region=a["region"],
                    account_id=a["account_id"],
                    karpenter_node_role_arn=a["karpenter_node_role_arn"],
                )
            )
        )

        karpenter_irsa = IRSA(
            f"{name}-irsa",
            role_name=f"{name}-role",
            oidc_provider_arn=self._oidc_provider_arn,
            oidc_issuer=self._oidc_issuer,
            trust_sa_namespace=config.KARPENTER_NAMESPACE,
            trust_sa_name=config.KARPENTER_SA_NAME,
            inline_policies=[
                aws.iam.RoleInlinePolicyArgs(
                    name=f"{name}-controller-inline",
                    policy=controller_inline_policy,
                )
            ],
            opts=pulumi.ResourceOptions(parent=self, provider=self._aws_provider),
        )
        self.karpenter_role = karpenter_irsa.iam_role

    def _create_karpenter_policies(self, name: str) -> None:
        for policy_arn in config.EKS_NODE_POLICIES:
            aws.iam.RolePolicyAttachment(
                f"{name}-node-policy-{policy_arn.split('/')[-1]}",
                role=self.karpenter_node_role.name,
                policy_arn=policy_arn,
                opts=pulumi.ResourceOptions(parent=self, provider=self._aws_provider),
            )

        self.karpenter_node_access_entry = aws.eks.AccessEntry(
            f"{name}-node-access-entry",
            cluster_name=self._cluster_name,
            principal_arn=self.karpenter_node_role.arn,
            type="EC2_LINUX",
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.karpenter_node_role],
                provider=self._aws_provider,
            ),
        )

    def _install_karpenter_release(self, name: str, version: str) -> None:
        self.helm_release = k8s.helm.v3.Release(
            f"{name}",
            name="karpenter",
            chart="oci://public.ecr.aws/karpenter/karpenter",
            version=version,
            namespace=config.KARPENTER_NAMESPACE,
            values={
                "serviceAccount": {
                    "name": config.KARPENTER_SA_NAME,
                    "annotations": {
                        "eks.amazonaws.com/role-arn": self.karpenter_role.arn
                    },
                },
                "settings": {"clusterName": self._cluster_name},
                "controller": {
                    "resources": {
                        "requests": {"cpu": "1", "memory": "1Gi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    }
                },
                "nodeSelector": {},
                "tolerations": [],
                "affinity": {},
            },
            skip_await=False,
            wait_for_jobs=True,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.karpenter_role, self.namespace],
                provider=self._k8s_provider,
            ),
        )

    def _add_node_pool(self, node_pool: config.NodePoolConfig) -> None:
        node_class_resource = k8s.apiextensions.CustomResource(
            f"{self._cluster_name}-{node_pool.name}-ncls",
            api_version="karpenter.k8s.aws/v1",
            kind="EC2NodeClass",
            metadata={"name": node_pool.name},
            spec={
                "amiSelectorTerms": [{"alias": "al2023@latest"}],
                "role": self.karpenter_node_role.arn,
                "blockDeviceMappings": [
                    {
                        "deviceName": "/dev/xvda",
                        "ebs": {
                            "deleteOnTermination": True,
                            "volumeSize": node_pool.ebs_size,
                            "volumeType": "gp3",
                        },
                    }
                ],
                "securityGroupSelectorTerms": [{"id": self._node_security_group_id}],
                "subnetSelectorTerms": self._subnet_ids.apply(
                    lambda x: [{"id": subnet_id} for subnet_id in x]
                ),
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.helm_release],
                provider=self._k8s_provider,
            ),
        )

        node_pool_spec = {
            "limits": {"cpu": node_pool.vcpu_limit, "memory": node_pool.memory_limit},
            "template": {
                "spec": {
                    "nodeClassRef": {
                        "group": "karpenter.k8s.aws",
                        "name": node_pool.name,
                        "kind": "EC2NodeClass",
                    },
                    "requirements": [
                        {
                            "key": "karpenter.sh/capacity-type",
                            "operator": "In",
                            "values": [node_pool.capacity_type],
                        },
                        {
                            "key": "node.kubernetes.io/instance-type",
                            "operator": "In",
                            "values": [node_pool.instance_type],
                        },
                    ],
                    "expireAfter": "720h",
                }
            },
            "disruption": {
                "consolidationPolicy": "WhenEmptyOrUnderutilized",
                "consolidateAfter": "1m",
            },
        }

        # Apply custom labels if provided
        if node_pool.labels:
            node_pool_spec.setdefault("template", {}).setdefault(
                "metadata", {}
            ).setdefault("labels", {}).update(node_pool.labels)

        # Configure GPU-specific settings
        if node_pool.gpu:
            node_pool_spec.setdefault("template", {}).setdefault(
                "metadata", {}
            ).setdefault("labels", {})["nvidia.com/gpu.present"] = "true"
            node_pool_spec["template"]["spec"]["requirements"].extend(
                [
                    {
                        "key": "karpenter.k8s.aws/instance-gpu-manufacturer",
                        "operator": "In",
                        "values": ["nvidia"],
                    }
                ]
            )

        # Apply custom taints
        custom_taints = []
        if node_pool.taints:
            for taint in node_pool.taints:
                taint_spec = {"key": taint.key, "effect": taint.effect}
                if taint.value:
                    taint_spec["value"] = taint.value
                custom_taints.append(taint_spec)

        # Add default GPU taint if GPU is enabled and no custom taints override it
        if node_pool.gpu:
            has_gpu_taint = any(
                t.key == "nvidia.com/gpu" for t in (node_pool.taints or [])
            )
            if not has_gpu_taint:
                custom_taints.append({"key": "nvidia.com/gpu", "effect": "NoSchedule"})

        if custom_taints:
            node_pool_spec["template"]["spec"]["taints"] = custom_taints

        node_pool_resource = k8s.apiextensions.CustomResource(
            f"{self._cluster_name}-{node_pool.name}-npl",
            api_version="karpenter.sh/v1",
            kind="NodePool",
            metadata={"name": node_pool.name},
            spec=node_pool_spec,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[node_class_resource],
                provider=self._k8s_provider,
            ),
        )
        self.node_pools[node_pool.name] = node_pool_resource

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "KarpenterAddon":
        """Create a KarpenterAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-karpenter",
            cluster_name=cluster.k8s_name,
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
            node_security_group_id=cluster.node_security_group.id,
            subnet_ids=cluster.subnet_ids,
            node_pool_configs=cluster.node_pools,
            karpenter_version=version or config.KARPENTER_VERSION,
            opts=pulumi.ResourceOptions(
                parent=cluster,
                depends_on=[
                    cluster.k8s,
                    cluster.k8s_fargate_profile,
                    cluster.coredns_addon,
                    *(extra_dependencies or []),
                ],
                providers={
                    "kubernetes": cluster.k8s_provider,
                    "aws": cluster.aws_provider,
                },
            ),
        )
