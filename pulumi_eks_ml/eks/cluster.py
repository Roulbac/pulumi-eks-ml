"""Main EKS cluster definition."""

from __future__ import annotations

import json
from textwrap import dedent
from typing import Protocol

import pulumi
import pulumi_aws as aws
import pulumi_eks as eks
import pulumi_kubernetes as k8s

from . import config
from .karpenter import KarpenterAddon


class EKSCluster(pulumi.ComponentResource):
    """Creates an EKS cluster with basic configuration."""

    region: pulumi.Output[str]
    cluster_name: pulumi.Output[str]
    cluster_endpoint: pulumi.Output[str]
    cluster_security_group_id: pulumi.Output[str]
    node_security_group_id: pulumi.Output[str]
    kubeconfig: pulumi.Output[str]
    oidc_provider_arn: pulumi.Output[str]
    oidc_issuer: pulumi.Output[str]
    fargate_profile_id: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Input[str],
        subnet_ids: pulumi.Input[list[str]],
        node_pools: list[config.NodePoolConfig],
        region: str | None = None,
        versions: config.ComponentVersions | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:EKSCluster", name, None, opts)

        self.versions = versions or config.ComponentVersions()

        # Store references
        self.subnet_ids = subnet_ids
        self.vpc_id = vpc_id
        self.name = name
        self.region = region or aws.get_region().region
        self.k8s_name = self.name
        self.node_pools = node_pools

        # Create internal AWS provider for this region
        self._aws_provider = aws.Provider(
            name,
            region=self.region,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Create EKS cluster
        self.k8s = self._create_eks_cluster()
        self.k8s_fargate_profile = self._create_fargate_profile()
        # Bootstrap CoreDNS after Fargate profile is ready
        self.coredns_addon = self._create_coredns_addon()

        # Create Kubernetes provider
        self._k8s_provider = self._create_k8s_provider()

        # Create security groups
        self.node_security_group, self.extra_sg_rules = self._create_security_groups()

        # Configure Fargate logging
        self.observability_namespace, self.aws_logging_configmap = (
            self._configure_fargate_logging()
        )

        self.karpenter = KarpenterAddon.from_cluster(self)
        self.karpenter_addon = self.karpenter

        # Register outputs
        self.cluster_name = self.k8s.kubeconfig_json.apply(lambda _: self.k8s_name)
        self.cluster_endpoint = self.k8s.kubeconfig_json.apply(
            lambda cfg: json.loads(cfg)["clusters"][0]["cluster"]["server"]
        )

        self.cluster_security_group_id = self.k8s.cluster_security_group_id
        self.node_security_group_id = self.k8s.node_security_group_id
        self.kubeconfig = self.k8s.kubeconfig_json
        self.oidc_provider_arn = self.k8s.oidc_provider_arn
        self.oidc_issuer = self.k8s.oidc_issuer
        self.fargate_profile_id = self.k8s.fargate_profile_id

        self.register_outputs(
            {
                "region": self.region,
                "cluster_name": self.cluster_name,
                "cluster_endpoint": self.cluster_endpoint,
                "cluster_security_group_id": self.k8s.cluster_security_group_id,
                "node_security_group_id": self.node_security_group_id,
                "kubeconfig": self.kubeconfig,
                "oidc_provider_arn": self.oidc_provider_arn,
                "oidc_issuer": self.oidc_issuer,
                "fargate_profile_id": self.fargate_profile_id,
            }
        )

    @property
    def aws_provider(self) -> aws.Provider:
        return self._aws_provider

    @property
    def k8s_provider(self) -> k8s.Provider:
        if self._k8s_provider:
            return self._k8s_provider
        raise RuntimeError(
            "Kubernetes provider not created yet. Make sure the underlying EKS cluster is created first."
        )

    def _create_eks_cluster(self) -> eks.Cluster:
        """Create the EKS cluster."""
        return eks.Cluster(
            self.name,
            name=self.k8s_name,
            # VPC and networking
            vpc_id=self.vpc_id,
            subnet_ids=self.subnet_ids,
            # Cluster configuration
            version=self.versions.kubernetes,
            # Skip default node group - use Fargate + Karpenter
            skip_default_node_group=True,
            # Enable cluster endpoint access
            endpoint_private_access=True,
            endpoint_public_access=True,
            # Enable logging
            enabled_cluster_log_types=config.CLUSTER_LOG_TYPES,
            # Create OIDC provider for service account roles
            create_oidc_provider=True,
            # Enable auth with both API and config map
            authentication_mode=eks.AuthenticationMode.API_AND_CONFIG_MAP,
            # Skip default security groups
            skip_default_security_groups=True,
            # Set 'bootstrap_self_managed_addons' to False to avoid bootstrapping CoreDNS
            # We want to bootstrap CoreDNS ourselves AFTER the Fargate profile is ready
            bootstrap_self_managed_addons=False,
            kube_proxy_addon_options=eks.KubeProxyAddonOptionsArgs(
                enabled=True,
                resolve_conflicts_on_create="OVERWRITE",
                resolve_conflicts_on_update="OVERWRITE",
                version=self.versions.kube_proxy,
            ),
            vpc_cni_options=eks.VpcCniOptionsArgs(
                addon_version=self.versions.vpc_cni,
                resolve_conflicts_on_create="OVERWRITE",
                resolve_conflicts_on_update="OVERWRITE",
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=self.aws_provider),
        )

    @property
    def fargate_pod_execution_role(self) -> aws.iam.Role:
        """The IAM role used for Fargate pod execution."""
        return self._fargate_pod_execution_role

    def _create_fargate_profile(self) -> aws.eks.FargateProfile:
        """Create the Fargate profile with pod execution role."""
        # Get AWS account ID and region for the trust policy
        invoke_opts = pulumi.InvokeOptions(provider=self.aws_provider)
        account_id = aws.get_caller_identity(opts=invoke_opts).account_id
        region = aws.get_region(opts=invoke_opts).region

        # Create a pod execution role for Fargate
        self._fargate_pod_execution_role = aws.iam.Role(
            f"{self.name}-fgt-pod-role",
            assume_role_policy=pulumi.Output.json_dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Condition": {
                                "ArnLike": {
                                    "aws:SourceArn": pulumi.Output.concat(
                                        "arn:aws:eks:",
                                        region,
                                        ":",
                                        account_id,
                                        ":fargateprofile/",
                                        self.k8s_name,
                                        "/*",
                                    )
                                }
                            },
                            "Principal": {"Service": "eks-fargate-pods.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=self.aws_provider),
        )

        # Attach the required managed policy for Fargate pod execution
        aws.iam.RolePolicyAttachment(
            f"{self.name}-fgt-pod-role-policy-attach",
            role=self._fargate_pod_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonEKSFargatePodExecutionRolePolicy",
            opts=pulumi.ResourceOptions(
                parent=self._fargate_pod_execution_role, provider=self.aws_provider
            ),
        )

        # Create Fargate profile with the pod execution role
        return aws.eks.FargateProfile(
            f"{self.name}-fgt-profile",
            cluster_name=self.k8s_name,
            pod_execution_role_arn=self._fargate_pod_execution_role.arn,
            selectors=config.FARGATE_KARPENTER_COREDNS_SELECTORS,
            subnet_ids=self.subnet_ids,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self._fargate_pod_execution_role, self.k8s],
                provider=self.aws_provider,
            ),
        )

    def _create_coredns_addon(self) -> dict[str, aws.eks.Addon]:
        """Create managed EKS addons with explicit dependencies."""

        coredns_addon = aws.eks.Addon(
            f"{self.name}-coredns-addon",
            addon_name="coredns",
            addon_version=self.versions.coredns,
            cluster_name=self.k8s_name,
            resolve_conflicts_on_create="OVERWRITE",
            resolve_conflicts_on_update="OVERWRITE",
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[self.k8s_fargate_profile],
                provider=self.aws_provider,
            ),
        )
        return coredns_addon

    def _create_k8s_provider(self) -> k8s.Provider:
        """Create Kubernetes provider using the cluster's kubeconfig."""
        return k8s.Provider(
            f"{self.name}-k8s-provider",
            kubeconfig=self.k8s.kubeconfig_json,
            opts=pulumi.ResourceOptions(parent=self, depends_on=[self.k8s]),
        )

    def _create_security_groups(
        self,
    ) -> tuple[aws.ec2.SecurityGroup, list[aws.ec2.SecurityGroupRule]]:
        """Create security groups for the EKS cluster.

        Returns:
            Tuple of (node_security_group, extra_sg_rules)
        """
        # Create node security group
        node_security_group = aws.ec2.SecurityGroup(
            f"{self.name}-node-sg",
            description="Security group for the EKS nodes",
            vpc_id=self.vpc_id,
            opts=pulumi.ResourceOptions(parent=self, provider=self.aws_provider),
        )
        # Create the ingress/egress rules separately for both node and cluster security groups
        # This allows the ALB controller created rules not to clash with our IaC
        extra_sg_rules = [
            aws.ec2.SecurityGroupRule(
                f"{self.name}-nodesg-allow-all-internal",
                type="ingress",
                security_group_id=node_security_group.id,
                from_port=0,
                to_port=0,
                protocol="-1",
                self=True,
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[node_security_group],
                    provider=self.aws_provider,
                ),
            ),
            aws.ec2.SecurityGroupRule(
                f"{self.name}-nodesg-allow-kubelet-ingress",
                type="ingress",
                security_group_id=node_security_group.id,
                from_port=10250,
                to_port=10250,
                protocol="tcp",
                source_security_group_id=self.k8s.cluster_security_group_id,
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[node_security_group],
                    provider=self.aws_provider,
                ),
            ),
            aws.ec2.SecurityGroupRule(
                f"{self.name}-nodesg-allow-kubelet-http-ingress",
                type="ingress",
                security_group_id=node_security_group.id,
                from_port=443,
                to_port=443,
                protocol="tcp",
                source_security_group_id=self.k8s.cluster_security_group_id,
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[node_security_group],
                    provider=self.aws_provider,
                ),
            ),
            aws.ec2.SecurityGroupRule(
                f"{self.name}-nodesg-allow-alb-webhook-ingress",
                type="ingress",
                security_group_id=node_security_group.id,
                from_port=9443,
                to_port=9443,
                protocol="tcp",
                source_security_group_id=self.k8s.cluster_security_group_id,
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[node_security_group],
                    provider=self.aws_provider,
                ),
            ),
            aws.ec2.SecurityGroupRule(
                f"{self.name}-nodesg-allow-all-outbound",
                type="egress",
                security_group_id=node_security_group.id,
                from_port=0,
                to_port=0,
                protocol="-1",
                cidr_blocks=["0.0.0.0/0"],
                opts=pulumi.ResourceOptions(
                    parent=self,
                    depends_on=[node_security_group],
                    provider=self.aws_provider,
                ),
            ),
        ]

        for idx, (port, protocol, description) in enumerate(
            config.CLUSTER_FROM_NODE_SG_RULES
        ):
            rule = aws.ec2.SecurityGroupRule(
                f"{self.name}-sg-rule-{idx}",
                type="ingress",
                security_group_id=self.k8s.cluster_security_group_id,
                from_port=port,
                to_port=port,
                protocol=protocol,
                source_security_group_id=node_security_group.id,
                description=description,
                opts=pulumi.ResourceOptions(parent=self, provider=self.aws_provider),
            )
            extra_sg_rules.append(rule)

        return node_security_group, extra_sg_rules

    def _configure_fargate_logging(
        self,
    ) -> tuple[k8s.core.v1.Namespace, k8s.core.v1.ConfigMap]:
        """Configure Fargate logging for the EKS cluster."""
        dependencies = [self.k8s, self.k8s_fargate_profile]
        # Create observability namespace
        observability_namespace = k8s.core.v1.Namespace(
            f"{self.name}-observability-ns",
            metadata={"name": "aws-observability"},
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=dependencies,
                provider=self.k8s_provider,
                retain_on_delete=True,
            ),
        )

        # Create AWS logging configmap
        aws_logging_configmap = k8s.core.v1.ConfigMap(
            f"{self.name}-aws-logging-cm",
            metadata={
                "name": "aws-logging",
                "namespace": "aws-observability",
            },
            data={
                "flb_log_cw": "false",
                "filters.conf": dedent("""\
                    [FILTER]
                        Name parser
                        Match *
                        Key_name log
                        Parser crio
                    [FILTER]
                        Name kubernetes
                        Match kube.*
                        Merge_Log On
                        Keep_Log Off
                        Buffer_Size 0
                        Kube_Meta_Cache_TTL 300s
                """),
                "output.conf": dedent("""\
                    [OUTPUT]
                        Name cloudwatch_logs
                        Match   kube.*
                        region region-code
                        log_group_name my-logs
                        log_stream_prefix from-fluent-bit-
                        log_retention_days 60
                        auto_create_group true
                """),
                "parsers.conf": dedent("""\
                    [PARSER]
                        Name crio
                        Format Regex
                        Regex ^(?<time>[^ ]+) (?<stream>stdout|stderr) (?<logtag>P|F) (?<log>.*)$
                        Time_Key    time
                        Time_Format %Y-%m-%dT%H:%M:%S.%L%z
                """),
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[*dependencies, observability_namespace],
                provider=self.k8s_provider,
            ),
        )

        return observability_namespace, aws_logging_configmap


class EKSClusterAddon(Protocol):
    """Protocol for EKS cluster addons."""

    # Optional version key to look up in ComponentVersions
    version_key: str | None

    @classmethod
    def from_cluster(
        cls,
        cluster: "EKSCluster",
        parent: pulumi.Resource | None = None,
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "EKSClusterAddon":
        """Create an EKSClusterAddon from an EKSCluster instance."""


class EKSClusterAddonInstaller(pulumi.ComponentResource):
    """Creates a collection of EKS cluster addons."""

    addons: list[EKSClusterAddon]
    cluster: EKSCluster

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        addon_types: list[type[EKSClusterAddon]],
        versions: config.ComponentVersions | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__(
            "pulumi-eks-ml:aws:EKSClusterAddonInstaller",
            name,
            None,
            pulumi.ResourceOptions(depends_on=[cluster]).merge(opts),
        )

        self.cluster = cluster
        self.addons = []

        for addon_type in addon_types:
            prev = self.addons and [self.addons[-1]] or None

            # Determine version
            version = None
            if versions:
                key = getattr(addon_type, "version_key", None)
                if key and hasattr(versions, key):
                    version = getattr(versions, key)

            addon = addon_type.from_cluster(
                self.cluster,
                parent=self,
                extra_dependencies=prev,
                version=version,
            )
            self.addons.append(addon)

        self.register_outputs(
            {
                "addons": self.addons,
            }
        )
