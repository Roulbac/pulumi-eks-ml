"""SkyPilot API Server addon for EKS via Helm (ALB ingress only).

References:
- Admin deploy guide: https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html
- Helm values spec: https://docs.skypilot.co/en/latest/reference/api-server/helm-values-spec.html#helm-values-spec
"""

from string import Template
import yaml
import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random
from passlib.hash import apr_md5_crypt


from ..eks.cluster import EKSCluster


HELM_VALUES_TEMPLATE = Template("""\
ingress:
  ingressClassName: alb
  annotations:
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/healthcheck-path: /api/health
    alb.ingress.kubernetes.io/subnets: $SUBNET_IDS
  ingressClassName: alb
ingress-nginx:
  enabled: false
apiService:
  initialBasicAuthSecret: $INITIAL_BASIC_AUTH_SECRET
  enableUserManagement: true
""")


class SkyPilotAPIServer(pulumi.ComponentResource):
    """Component that installs the SkyPilot API server Helm chart.

    - Enforces ALB ingress (internal) with health checks
    - Generates initial Basic Auth credentials and enables user management
    - Exposes `admin_username` and `admin_password` outputs
    """

    admin_username: pulumi.Output[str]
    admin_password: pulumi.Output[str]

    def __init__(
        self,
        name: str,
        cluster: EKSCluster,
        subnet_ids: pulumi.Input[list[str]],
        namespace: str = "skypilot",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("pulumi-eks-ml:eks:SkyPilotApiServer", name, None, opts)

        # Resolve dependencies and provider
        resource_opts = (opts or pulumi.ResourceOptions()).merge(
            pulumi.ResourceOptions(
                parent=self,
                provider=cluster.k8s_provider,
                depends_on=[cluster],
            )
        )

        release_name = f"{name}-skypilot"

        # Namespace for SkyPilot resources
        namespace_res = k8s.core.v1.Namespace(
            f"{release_name}-ns",
            metadata={"name": namespace},
            retain_on_delete=True,
            opts=resource_opts,
        )

        # Create basic auth credentials for the dashboard/API server
        web_username = "skypilot"
        web_password = random.RandomPassword(
            f"{release_name}-initial-basic-auth",
            length=16,
            special=False,
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[namespace_res])
            ),
        )
        salt = random.RandomPassword(
            f"{release_name}-initial-basic-auth-salt",
            length=8,
            special=False,
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[namespace_res])
            ),
        )

        # Build stable htpasswd line using a deterministic salt
        auth_value = pulumi.Output.all(web_password.result, salt.result).apply(
            lambda args: f"{web_username}:{apr_md5_crypt.using(salt=args[1]).hash(args[0])}"
        )

        _ = k8s.core.v1.Secret(
            f"{release_name}-initial-basic-auth",
            metadata={
                "name": "initial-basic-auth",
                "namespace": namespace,
            },
            string_data={"auth": auth_value},
            type="Opaque",
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[namespace_res])
            ),
        )

        values_yaml = HELM_VALUES_TEMPLATE.substitute(
            SUBNET_IDS=subnet_ids.apply(lambda x: ",".join(x)),
            INITIAL_BASIC_AUTH_SECRET=auth_value,
        )

        # Install the Helm release
        self.release = k8s.helm.v3.Release(
            f"{release_name}-release",
            name="skypilot",
            chart="skypilot",
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                repo="https://helm.skypilot.co"
            ),
            version="0.10.3",
            namespace=namespace,
            values=yaml.safe_load(values_yaml),
            skip_await=True,
            opts=resource_opts.merge(
                pulumi.ResourceOptions(depends_on=[namespace_res])
            ),
        )

        # Expose the generated password as a secret output
        self.admin_username = web_username
        self.admin_password = pulumi.Output.secret(web_password.result)

        self.register_outputs(
            {
                "admin_username": self.admin_username,
                "admin_password": self.admin_password,
            }
        )
