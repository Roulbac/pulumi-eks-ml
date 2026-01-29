"""Configuration constants and data classes for EKS components."""

import typing
from dataclasses import dataclass



# Karpenter constants
KARPENTER_NAMESPACE = "karpenter"
KARPENTER_SA_NAME = "karpenter"
KARPENTER_VERSION = "1.8.6"

# Addon versions
ALB_CONTROLLER_VERSION = "1.11.0"
FLUENT_BIT_VERSION = "0.1.35"
EBS_CSI_VERSION = "2.54.1"
METRICS_SERVER_VERSION = "3.13.0"
NVIDIA_DEVICE_PLUGIN_VERSION = "0.18.2"
EFS_CSI_VERSION = "3.3.0"

# EKS constants
DEFAULT_KUBERNETES_VERSION = "1.35"
DEFAULT_EBS_SIZE = "128Gi"
DEFAULT_VCPU_LIMIT = "100"
DEFAULT_MEMORY_LIMIT = "100Gi"

# AWS managed policies for EKS nodes
EKS_NODE_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
]

# Security group ports and protocols
CLUSTER_FROM_NODE_SG_RULES = [
    (443, "tcp", "Kubernetes API accessible from node SG"),
    (53, "udp", "CoreDNS accessible from node SG"),
    (53, "tcp", "CoreDNS accessible from node SG"),
    (10250, "tcp", "Kubelet on Fargate nodes accessible from node SG"),
    (9153, "tcp", "Prometheus metrics from Fargate nodes"),
    (8085, "tcp", "Metrics for Karpenter"),
]

# Fargate selectors for system components
FARGATE_KARPENTER_COREDNS_SELECTORS = [
    # CoreDNS is required for the cluster to function - keep this on fargate
    {
        "namespace": "kube-system",
        "labels": {"eks.amazonaws.com/component": "coredns"},
    },
    # Karpenter running on fargate for a "serverless" setup
    {
        "namespace": KARPENTER_NAMESPACE,
        "labels": {"app.kubernetes.io/name": KARPENTER_NAMESPACE},
    },
]

# EKS cluster log types
CLUSTER_LOG_TYPES = [
    "api",
    "audit",
    "authenticator",
    "controllerManager",
    "scheduler",
]


@dataclass
class TaintConfig:
    """Configuration for a Kubernetes taint."""

    key: str
    value: str | None = None
    effect: typing.Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] = "NoSchedule"

    def to_toleration(
        self, operator: typing.Literal["Equal", "Exists"] = "Equal"
    ) -> dict:
        """Convert taint to a toleration dict.

        Args:
            operator: Toleration operator. "Equal" matches key and value, "Exists" matches only key.
        """
        toleration = {"key": self.key, "operator": operator, "effect": self.effect}
        if operator == "Equal" and self.value:
            toleration["value"] = self.value
        return toleration


@dataclass
class NodePoolConfig:
    """Configuration for a Karpenter node pool."""

    name: str
    capacity_type: typing.Literal["spot", "on-demand"]
    instance_type: str
    ebs_size: str = DEFAULT_EBS_SIZE
    vcpu_limit: str = DEFAULT_VCPU_LIMIT
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    # Custom taints and labels
    taints: list[TaintConfig] | None = None
    labels: dict[str, str] | None = None

    @property
    def gpu(self) -> bool:
        """Whether the node pool is a GPU node pool."""
        # AWS NVIDIA GPU instance families always start with 'g' or 'p'
        return self.instance_type.startswith("g") or self.instance_type.startswith("p")

    @classmethod
    def from_dict(cls, data: dict) -> "NodePoolConfig":
        """Create a NodePoolConfig from a JSON/dict payload."""
        taints = None
        if data.get("taints"):
            taints = [TaintConfig(**taint) for taint in data["taints"]]

        return cls(
            name=data["name"],
            capacity_type=data["capacity_type"],
            instance_type=data["instance_type"],
            ebs_size=data.get("ebs_size", DEFAULT_EBS_SIZE),
            vcpu_limit=data.get("vcpu_limit", DEFAULT_VCPU_LIMIT),
            memory_limit=data.get("memory_limit", DEFAULT_MEMORY_LIMIT),
            taints=taints,
            labels=data.get("labels"),
        )


@dataclass
class ComponentVersions:
    """Configuration for component versions."""

    kubernetes: str = DEFAULT_KUBERNETES_VERSION
    karpenter: str = KARPENTER_VERSION
    alb_controller: str = ALB_CONTROLLER_VERSION
    fluent_bit: str = FLUENT_BIT_VERSION
    ebs_csi: str = EBS_CSI_VERSION
    metrics_server: str = METRICS_SERVER_VERSION
    nvidia_device_plugin: str = NVIDIA_DEVICE_PLUGIN_VERSION
    efs_csi: str = EFS_CSI_VERSION
    coredns: str | None = None
    kube_proxy: str | None = None
    vpc_cni: str | None = None
