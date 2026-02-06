"""SkyPilot Cognito identity provider."""

from __future__ import annotations

import pulumi
import pulumi_aws as aws
import pulumi_random as random


class SkyPilotCognitoIDP(pulumi.ComponentResource):
    """Provision a Cognito user pool with managed login page settings."""

    user_pool_name: pulumi.Output[str]
    user_pool_region: pulumi.Output[str]
    oidc_issuer_url: pulumi.Output[str]
    skypilot_client_id: pulumi.Output[str]
    skypilot_client_secret: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        region: pulumi.Input[str],
        callback_url: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotCognitoIDP", name, None, opts)

        provider = aws.Provider(f"{name}-cognito-provider", region=region, opts=opts)
        resource_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(parent=self, provider=provider)
        )

        self.user_pool = aws.cognito.UserPool(
            f"{name}-pool",
            name=f"{name}-pool",
            username_attributes=["email"],
            auto_verified_attributes=["email"],
            admin_create_user_config=aws.cognito.UserPoolAdminCreateUserConfigArgs(
                allow_admin_create_user_only=True
            ),
            opts=resource_opts,
        )

        self.skypilot_client = aws.cognito.UserPoolClient(
            f"{name}-client",
            name=f"{name}-client",
            user_pool_id=self.user_pool.id,
            generate_secret=True,
            supported_identity_providers=["COGNITO"],
            allowed_oauth_flows_user_pool_client=True,
            allowed_oauth_flows=["code"],
            allowed_oauth_scopes=["email", "openid", "profile"],
            callback_urls=[callback_url],
            opts=resource_opts.merge(
                # Ignore changes to supported_identity_providers
                # just in case post-hoc we add federated identity providers
                # through the AWS console.
                pulumi.ResourceOptions(ignore_changes="supported_identity_providers")
            ),
        )

        domain_prefix = random.RandomString(
            f"{name}-domain-prefix",
            length=10,
            lower=True,
            upper=False,
            numeric=True,
            special=True,
            override_special="-",
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.user_domain_prefix = domain_prefix.result

        # Create a managed login style for the user pool domain.
        self.skypilot_login_app_branding = aws.cognito.ManagedLoginBranding(
            f"{name}-login-branding",
            user_pool_id=self.user_pool.id,
            client_id=self.skypilot_client.id,
            region=region,
            use_cognito_provided_values=True,
            opts=resource_opts,
        )

        self.user_pool_domain = aws.cognito.UserPoolDomain(
            f"{name}-domain",
            domain=self.user_domain_prefix,
            user_pool_id=self.user_pool.id,
            managed_login_version=2,
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[self.skypilot_login_app_branding])
            ),
        )

        self.user_pool_name = self.user_pool.name
        self.user_pool_region = self.user_pool.region
        self.oidc_issuer_url = pulumi.Output.format("https://{}", self.user_pool.endpoint)
        self.skypilot_client_id = pulumi.Output.secret(self.skypilot_client.id)
        self.skypilot_client_secret = pulumi.Output.secret(
            self.skypilot_client.client_secret
        )

        self.register_outputs(
            {
                "user_pool_name": self.user_pool_name,
                "user_pool_region": self.user_pool_region,
                "user_pool_domain": self.user_pool_domain.domain,
                "user_domain_prefix": self.user_domain_prefix,
                "oidc_issuer_url": self.oidc_issuer_url,
                "user_pool_client_id": self.skypilot_client_id,
                "skypilot_client_secret": self.skypilot_client_secret,
            }
        )
