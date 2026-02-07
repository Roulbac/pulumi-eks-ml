## Starter Project

This folder is a minimal Pulumi project that uses `pulumi_eks_ml` to create:

- A single VPC with private subnets and a minimal public subnet for internet egress
- A single EKS cluster
- Recommended addons (Storage CSI, ALB · DNS, Monitoring, NVIDIA, etc...)

Use it as a reference or a starting point for your own topology.

### Architecture

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
    classDef vpc   fill:#dbeafe,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a;
    classDef eks   fill:#ede9fe,stroke:#8b5cf6,stroke-width:2px,color:#4c1d95;
    classDef node  fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#1e293b;
    classDef addon fill:#d1fae5,stroke:#059669,stroke-width:1.5px,color:#064e3b;
    classDef infra fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#92400e;
    classDef ext   fill:#1e293b,stroke:#475569,stroke-width:2px,color:#e2e8f0;

    Internet((Internet)):::ext

    subgraph VPC [VPC]
        direction LR
        NAT[NAT Gateway]:::infra

        subgraph EKS [EKS Cluster]
            direction TB

            subgraph NodePools [Node Pools]
                direction LR
                NP1[General]:::node
                NP2[GPU]:::node
            end

            subgraph Addons [Recommended Addons]
                direction LR
                A1[Storage CSI]:::addon
                A2[ALB · DNS]:::addon
                A3[Monitoring]:::addon
                A4[NVIDIA]:::addon
            end
        end
    end

    Internet <-->|Egress| NAT
    NAT ---|Private Subnets| EKS

    class VPC vpc;
    class EKS eks;

    %% Edge overrides
    %% 0: Internet <--> NAT
    %% 1: NAT --- EKS
    linkStyle 0 stroke:#d97706,stroke-width:2.5px;
    linkStyle 1 stroke:#3b82f6,stroke-width:2px;
```

### How it works

The program in `__main__.py`:

- Reads the AWS region from `aws:region`
- Generates a deployment name from the project + stack
- Builds node pools from `node_pools` config

### Configuration

Update `Pulumi.dev.yaml` (or your stack file) with these keys:

- `aws:region` (required by the AWS provider)
- `node_pools` (array of node pool objects)

Each node pool uses an `instance_type` list and can optionally set
`instance_family` and `instance_category` lists. Example:

**Note**: Pulumi may automatically preprend `starter:` to the keys in your stack's YAML file.

```yaml
config:
  aws:region: us-west-2
  starter:node_pools:
    - name: general
      capacity_type: on-demand
      instance_category: [t]
    - name: gpu
      capacity_type: on-demand
      instance_category: [g]
```

### Run it

### Secret management

Before running the stack, configure a secrets provider (passphrase or a cloud KMS).
A `Pulumi.<stack>.yaml` file in the repo does not create the stack in the backend,
so if this is your first time running `dev`, you still need to initialize it.
This project does not include an `encryptionsalt` in `Pulumi.dev.yaml`, so Pulumi
will prompt you to set up secrets on first init/select.

Helper commands:

```bash
# Passphrase-based encryption (local backend)
export PULUMI_CONFIG_PASSPHRASE="your-strong-passphrase"
pulumi stack init dev

# Or initialize the stack with a cloud secrets provider (example: AWS KMS)
pulumi stack init dev --secrets-provider="awskms://alias/your-kms-key"
```

```bash
pulumi stack select dev
uv run pulumi up
```

### Files

- `__main__.py`: Pulumi program
- `Pulumi.yaml`: Project metadata
- `Pulumi.<stack>.yaml`: Stack configuration
