import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.cluster import EKSCluster
from ..eks import config
from ..eks.irsa import IRSA


def install_aws_for_fluent_bit(
    name: str,
    oidc_provider_arn: pulumi.Input[str],
    oidc_issuer: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    log_group_name: str,
    dependencies: list[pulumi.Resource],
    parent: pulumi.Resource,
    version: str,
) -> k8s.helm.v3.Release:
    """Install AWS for Fluent Bit with IRSA for CloudWatch logging only."""
    release_name = "aws-for-fluent-bit"

    # Create inline policy for CloudWatch logs
    logs_policy = pulumi.Output.all(
        region=aws.get_region().region,
        account_id=aws.get_caller_identity().account_id,
    ).apply(
        lambda a: pulumi.Output.json_dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:DescribeLogStreams",
                            "logs:PutLogEvents",
                            "logs:PutRetentionPolicy",
                        ],
                        "Resource": f"arn:aws:logs:{a['region']}:{a['account_id']}:log-group:*",
                    }
                ],
            }
        )
    )

    # Create IRSA for Fluent Bit
    fluent_bit_irsa = IRSA(
        f"{name}-fluent-bit-irsa",
        role_name=f"{name}-fluent-bit-role",
        oidc_provider_arn=oidc_provider_arn,
        oidc_issuer=oidc_issuer,
        trust_sa_namespace="kube-system",
        trust_sa_name="aws-for-fluent-bit",
        inline_policies=[
            aws.iam.RoleInlinePolicyArgs(
                name=f"{name}-fluent-bit-logs-policy",
                policy=logs_policy,
            )
        ],
        opts=pulumi.ResourceOptions(parent=parent),
    )

    return k8s.helm.v3.Release(
        f"{name}-aws-for-fluent-bit",
        name=release_name,
        chart="aws-for-fluent-bit",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://aws.github.io/eks-charts",
        ),
        version=version,
        namespace="kube-system",
        values={
            "serviceAccount": {
                "name": "aws-for-fluent-bit",
                "annotations": {
                    "eks.amazonaws.com/role-arn": fluent_bit_irsa.iam_role.arn
                },
            },
            "tolerations": [{"operator": "Exists", "effect": "NoSchedule"}],
            # Enable the new CloudWatchLogs plugin (as opposed to old CloudWatch plugin)
            "cloudWatchLogs": {
                "enabled": True,
                "region": aws.get_region().region,
                "logGroupName": log_group_name,
                "logRetentionDays": 30,
            },
            # Disable the old golang plugin
            "cloudWatch": {
                "enabled": False,
            },
            "firehose": {
                "enabled": False,
            },
            "kinesis": {
                "enabled": False,
            },
            "elasticsearch": {
                "enabled": False,
            },
            "opensearch": {
                "enabled": False,
            },
            # Add an affinity that prevents running on fargate
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "eks.amazonaws.com/compute-type",
                                        "operator": "NotIn",
                                        "values": ["fargate"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
        },
        skip_await=True,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[*dependencies, fluent_bit_irsa.iam_role],
        ),
    )


class FluentBitAddon(pulumi.ComponentResource):
    """AWS for Fluent Bit as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    version_key = "fluent_bit"

    def __init__(
        self,
        name: str,
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        log_group_name: str,
        opts: pulumi.ResourceOptions,
        version: str = config.FLUENT_BIT_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:FluentBitAddon", name, None, opts)

        self.helm_release = install_aws_for_fluent_bit(
            name=name,
            oidc_provider_arn=oidc_provider_arn,
            oidc_issuer=oidc_issuer,
            k8s_provider=opts.providers["kubernetes"],
            log_group_name=log_group_name,
            dependencies=opts.depends_on or [],
            parent=self,
            version=version,
        )

        self.register_outputs({"helm_release": self.helm_release})

    @classmethod
    def from_cluster(
        cls,
        cluster: EKSCluster,
        parent: pulumi.Resource | None = None,
        log_group_prefix: str = "/eks/fluentbit/logs/",
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "FluentBitAddon":
        """Create a FluentBitAddon from an EKSCluster instance."""
        log_group_prefix = log_group_prefix.rstrip("/")
        log_group_name = f"{log_group_prefix}/{cluster.k8s_name}"
        return cls(
            name=f"{cluster.name}-fluent-bit",
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
            log_group_name=log_group_name,
            version=version or config.FLUENT_BIT_VERSION,
            opts=pulumi.ResourceOptions(
                parent=parent,
                depends_on=[
                    cluster,
                    *(extra_dependencies or []),
                ],
                providers={
                    "kubernetes": cluster.k8s_provider,
                    "aws": cluster.aws_provider,
                },
            ),
        )
