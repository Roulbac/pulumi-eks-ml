"""SkyPilot API server configuration helpers."""

from __future__ import annotations

from textwrap import dedent
from typing import Mapping

import yaml


def build_aws_credentials_secret(cluster_region: str, irsa_role_arn: str) -> str:
    """Build the AWS credentials file content for IRSA."""
    return dedent(
        f"""
        [default]
        role_arn = {irsa_role_arn}
        region = {cluster_region}
        web_identity_token_file = /var/run/secrets/eks.amazonaws.com/serviceaccount/token
        """
    )


def build_api_service_config(service_accounts_by_context: Mapping[str, str]) -> str:
    """Build the SkyPilot API service YAML config payload."""
    return yaml.safe_dump(
        {
            "allowed_clouds": ["aws", "kubernetes"],
            "kubernetes": {
                "allowed_contexts": list(service_accounts_by_context.keys()),
                "context_configs": {
                    k: {"remote_identity": v}
                    for k, v in service_accounts_by_context.items()
                },
                "custom_metadata": {
                    "annotations": {"alb.ingress.kubernetes.io/scheme": "internal"}
                },
            },
            "jobs": {"controller": {"consolidation_mode": True}},
        }
    )


def build_values(
    subnet_ids: list[str],
    irsa_role_arn: str,
    api_service_config: str,
    storage_class_name: str,
    ingress_host: str,
    ingress_ssl_cert_arn: str,
) -> dict:
    """Build the Helm values for the SkyPilot API server chart."""
    values: dict = {
        "ingress": {
            "enabled": True,
            "unified": True,
            "host": ingress_host,
            "ingressClassName": "alb",
            "annotations": {
                "alb.ingress.kubernetes.io/scheme": "internal",
                "alb.ingress.kubernetes.io/target-type": "ip",
                "alb.ingress.kubernetes.io/healthcheck-path": "/api/health",
                "alb.ingress.kubernetes.io/subnets": ",".join(subnet_ids),
                "alb.ingress.kubernetes.io/certificate-arn": ingress_ssl_cert_arn,
                "alb.ingress.kubernetes.io/listen-ports": '[{"HTTPS":443}]',
                "alb.ingress.kubernetes.io/ssl-redirect": "443",
            },
        },
        "ingress-nginx": {"enabled": False},
        "apiService": {
            "initialBasicAuthSecret": "initial-basic-auth",
            "enableUserManagement": True,
            "config": api_service_config,
        },
        "awsCredentials": {
            "enabled": True,
            "useCredentialsFile": True,
        },
        "rbac": {
            "serviceAccountAnnotations": {
                "eks.amazonaws.com/role-arn": irsa_role_arn
            },
        },
        "storage": {
            "enabled": True,
            "storageClassName": storage_class_name,
            "accessMode": "ReadWriteMany",
            "size": "64Gi",
        },
        "kubernetesCredentials": {
            "useApiServerCluster": False,
            "useKubeconfig": True,
            "kubeconfigSecretName": "kube-credentials",
        },
    }

    return values
