# Pulumi EKS ML Infrastructure

[![Tests](https://github.com/Roulbac/pulumi-eks-ml/actions/workflows/tests.yml/badge.svg)](https://github.com/Roulbac/pulumi-eks-ml/actions/workflows/tests.yml)

**An opinionated infrastructure library for building scalable Machine Learning platforms on AWS.**

---

## 💡 Why This Project?

Building ML infrastructure is complex. It's not just "spinning up a cluster"; it requires stitching together networking, compute, storage, GPU management, ingress, and observability into a cohesive platform.

Traditionally, teams face a choice:
1.  **Monolithic "World" Repos:** Everything in one giant Terraform state or Pulumi stack. Safe at first, but terrifying to update as it grows.
2.  **Fragmented Scripts:** A collection of disconnected scripts that are hard to replicate across environments (Dev vs Prod).

**`pulumi_eks_ml` solves this by treating infrastructure as a composable library.**

-   **Modular:** Instead of one rigid architecture, you get building blocks (VPC, EKS, Karpenter, GPUs) to assemble *your* specific topology.
-   **Multi-Region Native:** Seamlessly peer VPCs across regions for global inference or disaster recovery.
-   **ML-Optimized:** We've pre-baked the hard stuff—GPU drivers, Karpenter autoscaling for Spot instances, and optimized node pools.
-   **Environment Parity:** Define your topology *once* in code, then deploy it identicaly to Dev, Staging, and Prod using simple configuration.

---

## 📦 What's Inside?

The repository provides a Python package (`pulumi_eks_ml`) containing high-level, opinionated components:

### 🌐 Networking (`vpc`)
-   **Hub-and-Spoke Topology**: Connect a central "Hub" VPC to multiple regional "Spoke" VPCs automatically.
-   **Routing**: Handles the complex peering routes and security group rules for you.

### 🧠 Compute (`eks`)
-   **Secure EKS Clusters**: Private endpoints, Fargate control planes, and OIDC identity providers pre-configured.
-   **Karpenter Autoscaling**: The gold standard for ML compute. Automatically provisions GPU/CPU nodes based on pending pod demand. support for Spot instances to reduce costs.

### 🧩 Addons (`eks_addons`)
Ready-to-use integrations that turn a raw cluster into a platform:
-   **NvidiaDevicePlugin**: Enable GPU workloads immediately.
-   **AlbController**: AWS Application Load Balancer management for ingress.
-   **EbsCsi**: AWS EBS CSI driver for block storage.
-   **EfsCsi**: AWS EFS CSI driver for shared file storage (ideal for model weights).
-   **FluentBit**: Ship logs to CloudWatch/S3/ES.
-   **MetricsServer**: Essential for Horizontal Pod Autoscaling (HPA).
-   **Tailscale**: Secure subnet router for private cluster access.

### 🚀 Applications (`eks_apps`)
-   **SkyPilot**: Deploy the multi-cloud job orchestration server with one line of code.

---

## 🏗 How to Organize Your Infrastructure

We recommend an **Independent Project** structure. Treat this repo as a dependency (like a library), and build your actual infrastructure in separate project folders.

### The Model: Projects & Stacks

1.  **Project**: Represents a specific **Topology**. (e.g., "Training Platform", "Model Serving").
2.  **Stack**: Represents an **Environment** for that topology. (e.g., `dev`, `staging`, `prod`).

This ensures that your "Training Platform" is completely isolated from your "Web App", but your `dev` training environment is an exact mirror of `prod`.

### Directory Structure Example

```bash
.
├── pulumi_eks_ml/               # 📦 The Shared Library (Infrastructure Code)
├── pyproject.toml
│
├── projects/                    # 🚀 Your Live Infrastructure
│   │
│   ├── ml-training-platform/    # PROJECT 1: Heavy GPU training
│   │   ├── __main__.py          # Definition: VPC + EKS + GPU Pools
│   │   ├── Pulumi.dev.yaml      # Config: Small instances, 1 region
│   │   └── Pulumi.prod.yaml     # Config: P4d instances, 3 regions
│   │
│   └── model-inference-api/     # PROJECT 2: High-uptime CPU/Inf1 serving
│       ├── __main__.py          # Definition: Multi-region VPC + EKS
│       ├── Pulumi.staging.yaml
│       └── Pulumi.prod.yaml
```

---

## 🛠 Getting Started

### Prerequisites
- [Pulumi CLI](https://www.pulumi.com/docs/get-started/install/)
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### 1. Install Dependencies
Install the library in your environment:

```bash
uv sync --dev
```

### 2. Create Your Project
Create a folder for your new infrastructure topology.

```bash
mkdir -p projects/my-ml-platform && cd projects/my-ml-platform
# Chose uv as the toolchain
uv run pulumi new python --name my-ml-platform --force
# Run 'uv add ../../. --editable' to add 'pulumi_eks_ml' as an editable dependency
uv add ../../. --editable
# Remove the requirements.txt and main.py files (unnecessary)
rm requirements.txt main.py
# Source the project's virtual environment (IMPORTANT!)
source .venv/bin/activate
```

Note that we created a starter project in `projects/starter` that you can use as a reference.

### 3. Initialize Environments
Create stacks for the environments you need to support.

```bash
pulumi stack init dev
pulumi stack init prod
```

### 4. Write Your Infrastructure Code
In `projects/my-ml-platform/__main__.py`, import the library and define your platform.

```python
import pulumi
from pulumi_eks_ml import vpc, eks, eks_addons

# 1. Load Environment Config
cfg = pulumi.Config()
instance_type = cfg.require("gpuInstanceType")
env_name = pulumi.get_stack()

# 2. Define Networking
# Creates a VPC isolated to this environment
my_vpc = vpc.Vpc(f"{env_name}-vpc")

# 3. Define Compute
cluster = eks.EKSCluster(
    f"{env_name}-cluster",
    vpc_id=my_vpc.vpc_id,
    subnet_ids=my_vpc.private_subnet_ids,
    # Define Node Pools
    node_pools=[
        eks.NodePoolConfig(
            name="gpu-workload",
            instance_type=instance_type,  # Injected from stack config!
            capacity_type="spot",         # Save money on training
        )
    ],
)

# 3b. Enable Platform Services (install addons)
addon_installations = eks.cluster.EKSClusterAddonInstaller(
    f"{env_name}-addons",
    cluster=cluster,
    addon_types=eks_addons.recommended_addons(),
)

# 4. Export Outputs
pulumi.export("kubeconfig", cluster.kubeconfig)
```

### 5. Configure & Deploy
Set the variables for your `dev` stack and deploy.

```bash
# Configure Dev
pulumi stack select dev
pulumi config set aws:region us-west-2
pulumi config set gpuInstanceType g5.xlarge

# Deploy
uv run pulumi up
```

---

## 🧪 Testing

We treat infrastructure code like software. The library includes tests you can run locally.

```bash
# Run Unit Tests (Fast, mocked AWS calls)
uv run pytest -vv tests/unit

# Run Integration Tests (Real provisioning against LocalStack - no need to start LocalStack manually)
uv run pytest -vv tests/integration
```

## 📄 License

[MIT](LICENSE)
