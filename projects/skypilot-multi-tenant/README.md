# SkyPilot Multi-Tenant Project

This folder contains a Pulumi project that uses `pulumi_eks_ml` to create a multi-region, multi-tenant SkyPilot architecture.

It deploys:
- A **Global Network** with Hub-and-Spoke topology.
- **EKS Clusters** in a Hub region and multiple Spoke regions.
- **SkyPilot API Server** hosted in the Hub cluster.
- **SkyPilot Data Planes** (namespaces) provisioned across Hub and Spoke clusters.
- **IAM Roles & Policies** for secure data plane access (IRSA).

Use this project to stand up a production-ready SkyPilot platform that serves multiple tenants/teams across different regions.

## How it works

The program in `__main__.py`:

1.  **Networking**: Creates a global VPC group peered in a Hub-and-Spoke topology.
2.  **Clusters**: Deploys EKS clusters in the Hub and all Spoke regions with recommended addons (Karpenter, etc.).
3.  **SkyPilot Provisioning**:
    -   **API Server**: Deploys the SkyPilot API server in the Hub cluster.
    -   **Data Planes**: Creates namespaces and Service Accounts in specified clusters to act as SkyPilot execution environments.
    -   **User Identities**: Configures IRSA (IAM Roles for Service Accounts) for each data plane, allowing fine-grained permissions (e.g., S3 access) for different tenants.
4.  **Tailscale** (Optional): Can deploy a Tailscale subnet router in the Hub for secure private access.

## Configuration

Update `Pulumi.dev.yaml` (or your stack file) with the following structure:

-   `hub`: Configuration for the primary region.
    -   `region`: AWS region (e.g., `us-west-2`).
    -   `node_pools`: List of node pools for the cluster.
-   `data_planes`: List of data planes (tenants) to provision in this region.
    -   `tailscale`: (Optional) Tailscale configuration.
-   `spokes`: List of spoke region configurations.
    -   `region`: AWS region.
    -   `node_pools`: Node pools for this region.
-   `data_planes`: List of data planes to provision here.

### Example `Pulumi.dev.yaml`

```yaml
config:
  # Hub Region Configuration
  hub:
    region: us-west-2
    node_pools:
      - name: system
        capacity_type: on-demand
        instance_type: [t3.medium]
        ebs_size: "50Gi"
      - name: gpu-workers
        capacity_type: spot
        instance_type: [g5.xlarge]
    # Define SkyPilot tenants/data planes for the Hub
    data_planes:
      - name: team-a-dev
        # Optional: Bring your own IAM role ARN
        # user_role_arn: arn:aws:iam::123456789012:role/TeamARole
      - name: team-b-prod

    # Optional: Enable Tailscale Subnet Router
    tailscale:
      enabled: false
      # oauth_secret_arn: arn:aws:secretsmanager:us-west-2:123...

  # Spoke Regions Configuration
  spokes:
    - region: us-east-1
      node_pools:
        - name: system
          capacity_type: on-demand
          instance_type: [t3.medium]
      data_planes:
        - name: team-a-latency-sensitive

    - region: eu-west-1
      node_pools:
        - name: gpu-workers
          capacity_type: spot
          instance_type: [g5.2xlarge]
      data_planes: [] # Just compute capacity, no specific tenant isolation required yet
```

## Run it

### Secret Management
Ensure you have a secrets provider configured (passphrase or cloud KMS).

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

-   `skypilot_api_service_config`: The endpoint URL and configuration for the SkyPilot API.
-   `skypilot_admin_username` / `password`: Credentials for the SkyPilot API.
-   `skypilot_ingress_status`: Status of the LoadBalancer/Ingress.
-   `clusters`: Details of provisioned EKS clusters.
-   `skypilot_data_planes`: Details of provisioned data planes and their IAM roles.
