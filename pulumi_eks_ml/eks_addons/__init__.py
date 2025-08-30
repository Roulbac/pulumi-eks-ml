"""EKS cluster addons."""

from ..eks.cluster import EKSClusterAddon

from .alb_controller_addon import AlbControllerAddon
from .ebs_csi_addon import EbsCsiAddon
from .efs_csi_addon import EfsCsiAddon
from .fluent_bit_addon import FluentBitAddon
from .metrics_server_addon import MetricsServerAddon
from .nvidia_device_plugin_addon import NvidiaDevicePluginAddon
from .tailscale_subnet_router_addon import TailscaleSubnetRouterAddon

__all__ = [
    "AlbControllerAddon",
    "EbsCsiAddon",
    "EfsCsiAddon",
    "FluentBitAddon",
    "MetricsServerAddon",
    "NvidiaDevicePluginAddon",
    "TailscaleSubnetRouterAddon",
]


def recommended_addons() -> list[type[EKSClusterAddon]]:
    """Get the recommended addons for an ML-ready EKS cluster.

    The addons, installed in order, are:
    - EBS CSI driver
    - EFS CSI driver
    - ALB Controller
    - Metrics Server
    - Fluent Bit
    - NVIDIA Device Plugin
    """
    return [
        EbsCsiAddon,
        EfsCsiAddon,
        AlbControllerAddon,
        MetricsServerAddon,
        FluentBitAddon,
        NvidiaDevicePluginAddon,
    ]
