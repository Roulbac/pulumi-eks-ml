"""Admin credentials management for the SkyPilot API server."""

from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s
import pulumi_random as random
from passlib.hash import apr_md5_crypt


class SkyPilotAdminCredentials(pulumi.ComponentResource):
    """Creates admin credentials and secrets for SkyPilot API server."""

    username: pulumi.Output[str]
    password: pulumi.Output[str]
    secret_arn: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        namespace: str,
        k8s_opts: pulumi.ResourceOptions,
        aws_opts: pulumi.ResourceOptions,
        depends_on: list[pulumi.Resource] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotAdminCredentials", name, None, opts)

        web_username = "skypilot"

        random_opts = pulumi.ResourceOptions(parent=self, depends_on=depends_on)
        web_password = random.RandomPassword(
            f"{name}-admin-pw",
            length=16,
            special=False,
            opts=random_opts,
        )
        salt = random.RandomPassword(
            f"{name}-admin-pw-salt",
            length=8,
            special=False,
            opts=random_opts,
        )

        # Build stable htpasswd line using a deterministic salt
        auth_value = pulumi.Output.all(web_password.result, salt.result).apply(
            lambda args: (
                f"{web_username}:{apr_md5_crypt.using(salt=args[1]).hash(args[0])}"
            )
        )

        _ = k8s.core.v1.Secret(
            f"{name}-admin-k8s-creds",
            metadata={
                "name": "initial-basic-auth",
                "namespace": namespace,
            },
            string_data={"auth": auth_value},
            type="Opaque",
            opts=k8s_opts.merge(
                pulumi.ResourceOptions(parent=self, depends_on=depends_on)
            ),
        )

        admin_secret = aws.secretsmanager.Secret(
            f"{name}-admin-secret",
            name_prefix=f"{name}-admin-creds-",
            description="SkyPilot API Server Admin Credentials",
            opts=aws_opts.merge(pulumi.ResourceOptions(parent=self)),
        )

        _ = aws.secretsmanager.SecretVersion(
            f"{name}-admin-secret-version",
            secret_id=admin_secret.id,
            secret_string=pulumi.Output.all(web_password.result).apply(
                lambda args: json.dumps(
                    {
                        "username": web_username,
                        "password": args[0],
                    }
                )
            ),
            opts=aws_opts.merge(pulumi.ResourceOptions(parent=self)),
        )

        self.username = pulumi.Output.from_input(web_username)
        self.password = pulumi.Output.secret(web_password.result)
        self.secret_arn = admin_secret.arn

        self.register_outputs(
            {
                "username": self.username,
                "password": self.password,
                "secret_arn": self.secret_arn,
            }
        )
