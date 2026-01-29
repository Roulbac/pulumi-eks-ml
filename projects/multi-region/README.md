## Multi-Region Project

This folder contains a Pulumi project that uses `pulumi_eks_ml` to create a multi-region EKS architecture:

- A **Global Network** connecting multiple regions.
  - Supports **Hub-and-Spoke** or **Full Mesh** topologies via VPC peering.
  - Handles cross-region DNS resolution for seamless service discovery.
- An **EKS cluster** in each region.
- **Recommended addons** installed on every cluster.

Use this as a reference for deploying global or multi-region AI/ML platforms.

### How it works

The program in `__main__.py`:

1.  Reads the list of target regions from `regions`.
2.  Creates a multi-region VPC group using `vpc.VPCPeeredGroup`.
    - Automatically sets up peering connections based on the selected `topology` ("full_mesh" or "hub_and_spoke").
    - Configures route tables and cross-region DNS resolution.
3.  Iterates through all regions to deploy EKS clusters and addons.

### Configuration

Update `Pulumi.dev.yaml` (or your stack file) with these keys:

-   `regions`: List of AWS regions to deploy into.
-   `node_pools`: Array of node pool configurations applied to *all* clusters.
-   `versions`: (Optional) Versions for Kubernetes, addons, etc.

**Note:** You do not need to set `aws:region` as the program explicitly manages providers for each configured region.

Example `Pulumi.dev.yaml` snippet:

```yaml
config:
  # List of regions to deploy to
  regions:
    - us-west-2
    - us-east-1
    - eu-west-1
  
  # Node pools (applied to every cluster)
  node_pools:
    - name: system
      capacity_type: on-demand
      instance_type: t3.medium
      ebs_size: "50Gi"
    - name: gpu-workers
      capacity_type: spot
      instance_type: g5.xlarge
      vcpu_limit: "200"
```

### Run it

#### Secret management

Before running the stack, configure a secrets provider (passphrase or a cloud KMS).

```bash
# Initialize stack (if not already done)
pulumi stack init dev
```

#### Deploy

```bash
# Install dependencies
uv sync

# Select stack
pulumi stack select dev

# Deploy
uv run pulumi up
```

### Files

-   `__main__.py`: Main Pulumi program defining the multi-region infrastructure.
-   `Pulumi.yaml`: Project metadata.
-   `Pulumi.dev.yaml`: Example stack configuration.
