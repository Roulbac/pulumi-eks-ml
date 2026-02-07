# Multi-Region Project

This folder contains a Pulumi project that uses `pulumi_eks_ml` to create a multi-region EKS architecture:

-   A **Global Network** connecting multiple regions.
    -   Implements a **Full Mesh** topology via VPC peering.
    -   Handles cross-region DNS resolution for seamless service discovery.
-   An **EKS cluster** in each region.
-   **Recommended addons** installed on every cluster.

Use this as a reference for deploying global or multi-region AI/ML platforms where all regions need direct connectivity to each other.

## Architecture

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontFamily": "Inter, ui-sans-serif, system-ui",
    "fontSize": "14px",
    "lineColor": "#94a3b8",
    "textColor": "#1e293b"
  },
  "flowchart": { "curve": "basis" }
}}%%

flowchart LR
    classDef region fill:#dbeafe,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a;
    classDef eks    fill:#ede9fe,stroke:#8b5cf6,stroke-width:2px,color:#4c1d95;
    classDef node   fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#1e293b;
    classDef addon  fill:#d1fae5,stroke:#059669,stroke-width:1.5px,color:#064e3b;

    subgraph R1 [VPC · us-west-2]
        subgraph EKS1 [EKS Cluster]
            direction TB
            NP1[Node Pools]:::node
            AD1[Addons]:::addon
        end
    end

    subgraph R2 [VPC · us-east-1]
        subgraph EKS2 [EKS Cluster]
            direction TB
            NP2[Node Pools]:::node
            AD2[Addons]:::addon
        end
    end

    subgraph R3 [VPC · eu-west-1]
        subgraph EKS3 [EKS Cluster]
            direction TB
            NP3[Node Pools]:::node
            AD3[Addons]:::addon
        end
    end

    R1 <-->|VPC Peering| R2
    R2 <-->|VPC Peering| R3
    R1 <-->|VPC Peering| R3

    class R1,R2,R3 region;
    class EKS1,EKS2,EKS3 eks;

    %% Edge overrides
    %% 0-2: Full mesh VPC peering
    linkStyle 0,1,2 stroke:#6366f1,stroke-width:3.5px;
```

## How it works

The program in `__main__.py`:

1.  Reads the list of target regions from `regions` configuration.
2.  Creates a multi-region VPC group using `vpc.VPCPeeredGroup`.
    -   Configures a **Full Mesh** topology where every region peers with every other region.
    -   Configures route tables and cross-region DNS resolution.
3.  Iterates through all configured regions to deploy EKS clusters.
4.  Installs recommended addons (like Karpenter, Metrics Server, etc.) on each cluster.

## Configuration

Update `Pulumi.dev.yaml` (or your stack file) with these keys:

-   `regions`: List of AWS regions to deploy into.
-   `node_pools`: Array of node pool configurations applied to *all* clusters.
-   `versions`: (Optional) Versions for Kubernetes, addons, etc.

**Note:** You do not need to set `aws:region` as the program explicitly manages providers for each configured region.

### Example `Pulumi.dev.yaml`

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
      instance_category: ["t"]
      ebs_size: "50Gi"
    - name: gpu-workers
      capacity_type: spot
      instance_category: ["g"]
```

## Run it

### Secret management

Before running the stack, configure a secrets provider (passphrase or a cloud KMS).

```bash
# Initialize stack (if not already done)
pulumi stack init dev
```

### Deploy

```bash
# Install dependencies
uv sync

# Select stack
pulumi stack select dev

# Deploy
uv run pulumi up
```

## Outputs

After deployment, the stack will export:

-   `clusters`: A map of region names to cluster details, including:
    -   `vpc_id`: The ID of the VPC in that region.
    -   `cluster_name`: The name of the EKS cluster.

## Files

-   `__main__.py`: Main Pulumi program defining the multi-region infrastructure.
-   `Pulumi.yaml`: Project metadata.
-   `Pulumi.dev.yaml`: Example stack configuration.
