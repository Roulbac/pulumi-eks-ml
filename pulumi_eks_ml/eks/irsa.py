"""IRSA (IAM Roles for Service Accounts) utilities and component."""

from __future__ import annotations

import pulumi
import pulumi_aws as aws


def _build_irsa_assume_role_policy(
    oidc_provider_arn: str,
    oidc_issuer: str,
    trust_sa_namespace: str,
    trust_sa_name: str,
) -> dict:
    """Build the IRSA assume role policy document."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": oidc_provider_arn},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{oidc_issuer}:sub": (
                            f"system:serviceaccount:{trust_sa_namespace}:{trust_sa_name}"
                        ),
                        f"{oidc_issuer}:aud": "sts.amazonaws.com",
                    }
                },
            }
        ],
    }


class IRSA(pulumi.ComponentResource):
    """Creates an IAM role for a service account (IRSA)."""

    iam_role_arn: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        role_name: str,
        oidc_provider_arn: pulumi.Input[str],
        oidc_issuer: pulumi.Input[str],
        trust_sa_namespace: str,
        trust_sa_name: str,
        inline_policies: list[aws.iam.RoleInlinePolicyArgs] | None = None,
        attached_policies: list[str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("pulumi-eks-ml:aws:IRSA", name, None, opts)

        # Create IAM role
        self.iam_role = aws.iam.Role(
            f"{name}-role",
            name=role_name,
            assume_role_policy=pulumi.Output.all(
                oidc_provider_arn=oidc_provider_arn,
                oidc_issuer=oidc_issuer,
            ).apply(
                lambda args: pulumi.Output.json_dumps(
                    _build_irsa_assume_role_policy(
                        oidc_provider_arn=args["oidc_provider_arn"],
                        oidc_issuer=args["oidc_issuer"],
                        trust_sa_namespace=trust_sa_namespace,
                        trust_sa_name=trust_sa_name,
                    )
                )
            ),
            inline_policies=inline_policies,
            opts=opts,
        )

        # Attach managed policies
        self.policy_attachments = []
        for policy_arn in attached_policies or []:
            attachment = aws.iam.RolePolicyAttachment(
                f"{name}-policy-attachment-{policy_arn.split('/')[-1]}",
                role=self.iam_role.name,
                policy_arn=policy_arn,
                opts=opts,
            )
            self.policy_attachments.append(attachment)

        # Register outputs
        self.iam_role_arn = self.iam_role.arn
        self.register_outputs(
            {
                "iam_role_arn": self.iam_role_arn,
            }
        )
