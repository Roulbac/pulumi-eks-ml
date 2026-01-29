import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from ..eks.cluster import EKSCluster
from ..eks import config
from ..eks.irsa import IRSA


def install_aws_load_balancer_controller(
    name: str,
    cluster_name: pulumi.Input[str],
    oidc_provider_arn: pulumi.Input[str],
    oidc_issuer: pulumi.Input[str],
    vpc_id: pulumi.Input[str],
    k8s_provider: k8s.Provider,
    dependencies: list[pulumi.Resource],
    parent: pulumi.Resource,
    version: str,
) -> k8s.helm.v3.Release:
    """Install AWS Load Balancer Controller with IRSA."""
    release_name = "aws-load-balancer-controller"

    # Create inline policy for ALB controller
    # Taken from https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.13.4/docs/install/iam_policy.json
    alb_policy = pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["iam:CreateServiceLinkedRole"],
                    "Resource": "*",
                    "Condition": {
                        "StringEquals": {
                            "iam:AWSServiceName": "elasticloadbalancing.amazonaws.com"
                        }
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:DescribeAccountAttributes",
                        "ec2:DescribeAddresses",
                        "ec2:DescribeAvailabilityZones",
                        "ec2:DescribeInternetGateways",
                        "ec2:DescribeVpcs",
                        "ec2:DescribeVpcPeeringConnections",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeInstances",
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:DescribeTags",
                        "ec2:GetCoipPoolUsage",
                        "ec2:DescribeCoipPools",
                        "ec2:GetSecurityGroupsForVpc",
                        "ec2:DescribeIpamPools",
                        "ec2:DescribeRouteTables",
                        "elasticloadbalancing:DescribeLoadBalancers",
                        "elasticloadbalancing:DescribeLoadBalancerAttributes",
                        "elasticloadbalancing:DescribeListeners",
                        "elasticloadbalancing:DescribeListenerCertificates",
                        "elasticloadbalancing:DescribeSSLPolicies",
                        "elasticloadbalancing:DescribeRules",
                        "elasticloadbalancing:DescribeTargetGroups",
                        "elasticloadbalancing:DescribeTargetGroupAttributes",
                        "elasticloadbalancing:DescribeTargetHealth",
                        "elasticloadbalancing:DescribeTags",
                        "elasticloadbalancing:DescribeTrustStores",
                        "elasticloadbalancing:DescribeListenerAttributes",
                        "elasticloadbalancing:DescribeCapacityReservation",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "cognito-idp:DescribeUserPoolClient",
                        "acm:ListCertificates",
                        "acm:DescribeCertificate",
                        "iam:ListServerCertificates",
                        "iam:GetServerCertificate",
                        "waf-regional:GetWebACL",
                        "waf-regional:GetWebACLForResource",
                        "waf-regional:AssociateWebACL",
                        "waf-regional:DisassociateWebACL",
                        "wafv2:GetWebACL",
                        "wafv2:GetWebACLForResource",
                        "wafv2:AssociateWebACL",
                        "wafv2:DisassociateWebACL",
                        "shield:GetSubscriptionState",
                        "shield:DescribeProtection",
                        "shield:CreateProtection",
                        "shield:DeleteProtection",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:AuthorizeSecurityGroupIngress",
                        "ec2:RevokeSecurityGroupIngress",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["ec2:CreateSecurityGroup"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["ec2:CreateTags"],
                    "Resource": "arn:aws:ec2:*:*:security-group/*",
                    "Condition": {
                        "StringEquals": {"ec2:CreateAction": "CreateSecurityGroup"},
                        "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": ["ec2:CreateTags", "ec2:DeleteTags"],
                    "Resource": "arn:aws:ec2:*:*:security-group/*",
                    "Condition": {
                        "Null": {
                            "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                            "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
                        }
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:AuthorizeSecurityGroupIngress",
                        "ec2:RevokeSecurityGroupIngress",
                        "ec2:DeleteSecurityGroup",
                    ],
                    "Resource": "*",
                    "Condition": {
                        "Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:CreateLoadBalancer",
                        "elasticloadbalancing:CreateTargetGroup",
                    ],
                    "Resource": "*",
                    "Condition": {
                        "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"}
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:CreateListener",
                        "elasticloadbalancing:DeleteListener",
                        "elasticloadbalancing:CreateRule",
                        "elasticloadbalancing:DeleteRule",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:AddTags",
                        "elasticloadbalancing:RemoveTags",
                    ],
                    "Resource": [
                        "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
                        "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
                        "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
                    ],
                    "Condition": {
                        "Null": {
                            "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                            "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
                        }
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:AddTags",
                        "elasticloadbalancing:RemoveTags",
                    ],
                    "Resource": [
                        "arn:aws:elasticloadbalancing:*:*:listener/net/*/*/*",
                        "arn:aws:elasticloadbalancing:*:*:listener/app/*/*/*",
                        "arn:aws:elasticloadbalancing:*:*:listener-rule/net/*/*/*",
                        "arn:aws:elasticloadbalancing:*:*:listener-rule/app/*/*/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:ModifyLoadBalancerAttributes",
                        "elasticloadbalancing:SetIpAddressType",
                        "elasticloadbalancing:SetSecurityGroups",
                        "elasticloadbalancing:SetSubnets",
                        "elasticloadbalancing:DeleteLoadBalancer",
                        "elasticloadbalancing:ModifyTargetGroup",
                        "elasticloadbalancing:ModifyTargetGroupAttributes",
                        "elasticloadbalancing:DeleteTargetGroup",
                    ],
                    "Resource": "*",
                    "Condition": {
                        "Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}
                    },
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:RegisterTargets",
                        "elasticloadbalancing:DeregisterTargets",
                    ],
                    "Resource": "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:SetWebAcl",
                        "elasticloadbalancing:ModifyListener",
                        "elasticloadbalancing:AddListenerCertificates",
                        "elasticloadbalancing:RemoveListenerCertificates",
                        "elasticloadbalancing:ModifyRule",
                    ],
                    "Resource": "*",
                },
            ],
        }
    )

    # Create IRSA for ALB Controller
    alb_irsa = IRSA(
        f"{name}-alb-irsa",
        role_name=f"{name}-alb-role",
        oidc_provider_arn=oidc_provider_arn,
        oidc_issuer=oidc_issuer,
        trust_sa_namespace="kube-system",
        trust_sa_name="aws-load-balancer-controller",
        inline_policies=[
            aws.iam.RoleInlinePolicyArgs(
                name=f"{name}-alb-policy",
                policy=alb_policy,
            )
        ],
        opts=pulumi.ResourceOptions(parent=parent),
    )

    return k8s.helm.v3.Release(
        f"{name}-alb-controller",
        name=release_name,
        chart="aws-load-balancer-controller",
        repository_opts=k8s.helm.v3.RepositoryOptsArgs(
            repo="https://aws.github.io/eks-charts",
        ),
        version=version,
        namespace="kube-system",
        values={
            "clusterName": cluster_name,
            "region": aws.get_region().region,
            "vpcId": vpc_id,
            "serviceAccount": {
                "name": "aws-load-balancer-controller",
                "annotations": {"eks.amazonaws.com/role-arn": alb_irsa.iam_role.arn},
            },
        },
        skip_await=False,
        opts=pulumi.ResourceOptions(
            parent=parent,
            provider=k8s_provider,
            depends_on=[*dependencies, alb_irsa.iam_role],
        ),
    )


class AlbControllerAddon(pulumi.ComponentResource):
    """AWS Load Balancer Controller as a Pulumi ComponentResource."""

    helm_release: k8s.helm.v3.Release
    version_key = "alb_controller"

    def __init__(
        self,
        name: str,
        vpc_id: pulumi.Input[str],
        cluster_name: pulumi.Input[str],
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        opts: pulumi.ResourceOptions,
        version: str = config.ALB_CONTROLLER_VERSION,
    ):
        super().__init__("pulumi-eks-ml:eks:AlbControllerAddon", name, None, opts)

        self.helm_release = install_aws_load_balancer_controller(
            name=name,
            cluster_name=cluster_name,
            oidc_provider_arn=oidc_provider_arn,
            oidc_issuer=oidc_issuer,
            vpc_id=vpc_id,
            k8s_provider=opts.providers["kubernetes"],
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
        extra_dependencies: list[pulumi.Resource] | None = None,
        version: str | None = None,
    ) -> "AlbControllerAddon":
        """Create an AlbControllerAddon from an EKSCluster instance."""
        return cls(
            name=f"{cluster.name}-alb-controller",
            cluster_name=cluster.k8s.eks_cluster.name,
            oidc_provider_arn=cluster.k8s.oidc_provider_arn,
            oidc_issuer=cluster.k8s.oidc_issuer,
            vpc_id=cluster.vpc_id,
            version=version or config.ALB_CONTROLLER_VERSION,
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
