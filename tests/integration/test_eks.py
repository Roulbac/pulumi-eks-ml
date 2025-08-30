from __future__ import annotations

import json
import uuid
from enum import Enum
from urllib.parse import unquote

import pulumi
import pulumi.automation as auto

from pulumi_eks_ml.eks.irsa import IRSA
from tests.integration.conftest import (
    localstack_provider,
    pulumi_stack_factory,
)


def _decode_policy_document(policy_document):
    if isinstance(policy_document, dict):
        return policy_document
    try:
        return json.loads(policy_document)
    except json.JSONDecodeError:
        return json.loads(unquote(policy_document))


class TestIRSA:
    class _TestIRSAParams(str, Enum):
        OIDC_ISSUER = (
            "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLED539D4633E53DE1B716D3041E"
        )
        OIDC_PROVIDER_ARN = "arn:aws:iam::000000000000:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/EXAMPLED539D4633E53DE1B716D3041E"
        TRUST_SA_NAMESPACE = "kube-system"
        TRUST_SA_NAME = "external-dns"

    @staticmethod
    def _irsa_program() -> None:
        provider = localstack_provider()
        role_name = pulumi.Config("tests").require("irsaRoleName")

        irsa = IRSA(
            "irsa",
            role_name=role_name,
            oidc_provider_arn=TestIRSA._TestIRSAParams.OIDC_PROVIDER_ARN,
            oidc_issuer=TestIRSA._TestIRSAParams.OIDC_ISSUER,
            trust_sa_namespace=TestIRSA._TestIRSAParams.TRUST_SA_NAMESPACE,
            trust_sa_name=TestIRSA._TestIRSAParams.TRUST_SA_NAME,
            opts=pulumi.ResourceOptions(provider=provider),
        )

        pulumi.export("role_name", irsa.iam_role.name)

    @staticmethod
    def test_creates_role_with_expected_trust_policy(iam_client):
        with pulumi_stack_factory() as create_stack:
            role_name = f"irsa-test-{uuid.uuid4().hex[:8]}"
            stack: auto.Stack = create_stack(
                program=TestIRSA._irsa_program,
                config_overrides={"tests:irsaRoleName": role_name},
            )

            stack.up(on_output=None)

            role = iam_client.get_role(RoleName=role_name)["Role"]
            assume_policy = _decode_policy_document(role["AssumeRolePolicyDocument"])

            statement = assume_policy["Statement"][0]
            assert statement == {
                "Effect": "Allow",
                "Principal": {"Federated": TestIRSA._TestIRSAParams.OIDC_PROVIDER_ARN},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{TestIRSA._TestIRSAParams.OIDC_ISSUER}:sub": f"system:serviceaccount:{TestIRSA._TestIRSAParams.TRUST_SA_NAMESPACE}:{TestIRSA._TestIRSAParams.TRUST_SA_NAME}",
                        f"{TestIRSA._TestIRSAParams.OIDC_ISSUER}:aud": "sts.amazonaws.com",
                    }
                },
            }
