# Pulumi EKS ML Infrastructure

[![Tests](https://github.com/Roulbac/pulumi-eks-ml/actions/workflows/tests.yml/badge.svg)](https://github.com/Roulbac/pulumi-eks-ml/actions/workflows/tests.yml)

**An opinionated library for multi-tenant, multi-region Machine Learning platforms on AWS.**

This repository provides a modular set of Pulumi components (`pulumi_eks_ml`) to spin up multi-tenant, multi-region ML infrastructure with minimal pain.


## 💡 Philosophy

This project treats infrastructure as a **composable library**. Instead of one giant deployment, you get modular building blocks (VPC, EKS, GPU Node Pools) that you can assemble into your own topology.

Whether it's a single cluster for testing or a global mesh for distributed workloads, you can define your architecture once in Python, then deploy identical copies across different environments thanks to Pulumi stacks.

## Architectural examples with `pulumi_eks_ml`

| Project | Description | Architecture |
|---------|-------------|:------------:|
| [**Starter**](./projects/starter/) | Single VPC, single EKS cluster with recommended addons. | [diagram](./projects/starter/README.md#architecture) |
| [**EKS Multi-Region**](./projects/multi-region/) | Full-mesh VPC peering across regions, each with an EKS cluster. | [diagram](./projects/multi-region/README.md#architecture) |
| [**SkyPilot Multi-Tenant**](./projects/skypilot-multi-tenant/) | Hub-and-Spoke multi-region network with multi-tenant SkyPilot API server, Cognito auth, Tailscale VPN, and isolated data planes. | [diagram](./projects/skypilot-multi-tenant/README.md#architecture) |

## ⚡ Quickstart

Use the starter project as the fastest path to a working EKS cluster.

```python
# __main__.py
import pulumi

from pulumi_eks_ml import eks, eks_addons, vpc

main_region = pulumi.Config("aws").require("region")
cfg = pulumi.Config()
deployment_name = f"{pulumi.get_project()}-{pulumi.get_stack()}"
node_pools_config = cfg.require_object("node_pools")

node_pools = [eks.NodePoolConfig.from_dict(pool) for pool in node_pools_config]

vpc_resource = vpc.VPC(
    name=f"{deployment_name}-vpc",
    cidr_block="10.0.0.0/16",
    setup_internet_egress=True,
)

cluster = eks.EKSCluster(
    f"{deployment_name}-cls",
    vpc_id=vpc_resource.vpc_id,
    subnet_ids=vpc_resource.private_subnet_ids,
    node_pools=node_pools,
)

eks.cluster.EKSClusterAddonInstaller(
    f"{deployment_name}-addons",
    cluster=cluster,
    addon_types=eks_addons.recommended_addons(),
)

pulumi.export("vpc_id", vpc_resource.vpc_id)
pulumi.export("cluster_name", cluster.cluster_name)
```

```bash
uv sync --dev
cd projects/starter
pulumi stack init dev
pulumi config set aws:region us-west-2
uv run pulumi up
```

## 🚀 Key Features

-   **ML-Optimized Compute**: Pre-configured EKS clusters with **Karpenter** for autoscaling (Spot/On-Demand) and NVIDIA GPU drivers ready to go.
-   **Global Networking**: Easy **Multi-Region** connectivity with Hub-and-Spoke or Full Mesh VPC peering topologies.
-   **Opinionated Add-ons for ML**: Built-in support for ALB Controller, EBS/EFS CSI drivers, FluentBit, Metrics Server, etc...
-  **Secure network with Tailscale**: Secure network with Tailscale for VPN access, in additional to public/private subnet isolation.
-   **SkyPilot Multi-Tenant Platform**: Opinionated deployment of [SkyPilot](https://skypilot.readthedocs.io/) for multi-tenant, multi-region AI workloads.

## 📂 Repository Structure

-   `pulumi_eks_ml/`: The core Python library containing reusable infrastructure components.
-   `projects/`: Reference implementations and live infrastructure code.
    -   `starter/`: A simple single-region EKS cluster.
    -   `multi-region/`: A full-mesh global network connecting clusters across regions.
    -   `skypilot-multi-tenant/`: A SkyPilot platform with isolated data planes for multiple teams.

## 🛠 Getting Started

### Prerequisites

-   [Pulumi CLI](https://www.pulumi.com/docs/get-started/install/)
-   Python 3.12+
-   [uv](https://github.com/astral-sh/uv) (recommended)

### 1. Install & Setup

```bash
# Clone the repo
git clone https://github.com/Roulbac/pulumi-eks-ml.git
cd pulumi-eks-ml

# Install dependencies
uv sync --dev
```

### 2. Deploy a Project

Navigate to one of the reference projects to see it in action.

```bash
cd projects/starter

# Initialize your stack (e.g., dev)
pulumi stack init dev

# Deploy
uv run pulumi up
```

For custom infrastructure, create a new folder in `projects/`, import `pulumi_eks_ml`, and define your topology (see `projects/starter/__main__.py` for a template).

## 🧪 Testing

We include both unit and integration tests (using LocalStack).

```bash
# Run Unit Tests
uv run pytest -vv tests/unit

# Run Integration Tests
uv run pytest -vv tests/integration
```

## 📄 License

[MIT](LICENSE)
