from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import uuid

import boto3
import pulumi
import pulumi.automation as auto
import pulumi_aws as aws
import pytest
from testcontainers.localstack import LocalStackContainer

AWS_REGION = "us-east-1"
AWS_ACCESS_KEY_ID = "test"
AWS_SECRET_ACCESS_KEY = "test"
PULUMI_PROJECT_NAME = "pulumi-eks-ml-integration-tests"


@pytest.fixture(scope="session", autouse=True)
def localstack_container() -> LocalStackContainer:
    with LocalStackContainer("localstack/localstack:latest").with_services(
        "ec2", "iam", "sts"
    ) as localstack:
        yield localstack


@pytest.fixture(scope="session", autouse=True)
def localstack_endpoint(localstack_container: LocalStackContainer) -> str:
    return localstack_container.get_url()


@pytest.fixture(scope="session", autouse=True)
def ec2_client(localstack_endpoint: str):
    return boto3.client(
        "ec2",
        endpoint_url=localstack_endpoint,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


@pytest.fixture(scope="session", autouse=True)
def iam_client(localstack_endpoint: str):
    return boto3.client(
        "iam",
        endpoint_url=localstack_endpoint,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )


@pytest.fixture(scope="session", autouse=True)
def localstack_env(localstack_endpoint: str) -> dict[str, str]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID)
        mp.setenv("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY)
        mp.setenv("AWS_REGION", AWS_REGION)
        mp.setenv("AWS_DEFAULT_REGION", AWS_REGION)
        mp.setenv("AWS_ENDPOINT_URL", localstack_endpoint)
        mp.setenv("LOCALSTACK_ENDPOINT", localstack_endpoint)
        mp.setenv("PULUMI_CONFIG_PASSPHRASE", "localstack")
        mp.setenv("PULUMI_SKIP_UPDATE_CHECK", "true")
        yield


@contextmanager
def pulumi_stack_factory():
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)

        created_stacks: list[auto.Stack] = []

        def _create_stack(
            program,
            env_overrides: dict[str, str] | None = None,
            config_overrides: dict[str, str] | None = None,
        ) -> auto.Stack:
            backend_dir = tmp_path / "pulumi-backend"
            backend_dir.mkdir(parents=True, exist_ok=True)
            pulumi_home = tmp_path / "pulumi-home"
            pulumi_home.mkdir(parents=True, exist_ok=True)
            env_vars = {
                **os.environ.copy(),
                **{
                    "PULUMI_BACKEND_URL": f"file://{backend_dir}",
                    "PULUMI_HOME": str(pulumi_home),
                },
                **(env_overrides or {}),
            }
            stack_name = f"test-{uuid.uuid4().hex[:8]}"
            stack = auto.create_or_select_stack(
                stack_name=stack_name,
                project_name=PULUMI_PROJECT_NAME,
                program=program,
                opts=auto.LocalWorkspaceOptions(env_vars=env_vars),
            )
            stack.set_config("aws:region", auto.ConfigValue(value=AWS_REGION))
            stack.set_config("aws:accessKey", auto.ConfigValue(value=AWS_ACCESS_KEY_ID))
            stack.set_config(
                "aws:secretKey", auto.ConfigValue(value=AWS_SECRET_ACCESS_KEY)
            )
            stack.set_config(
                "aws:skipCredentialsValidation", auto.ConfigValue(value="true")
            )
            stack.set_config("aws:skipMetadataApiCheck", auto.ConfigValue(value="true"))
            stack.set_config(
                "aws:skipRequestingAccountId", auto.ConfigValue(value="true")
            )

            for key, value in (config_overrides or {}).items():
                stack.set_config(key, auto.ConfigValue(value=value))

            created_stacks.append(stack)
            return stack

        try:
            yield _create_stack
        finally:
            for stack in created_stacks:
                try:
                    stack.destroy(on_output=None)
                finally:
                    stack.workspace.remove_stack(stack.name)


def localstack_provider(name: str = "localstack") -> aws.Provider:
    config = pulumi.Config("aws")
    return aws.Provider(
        name,
        region=config.require("region"),
        access_key=config.require("accessKey"),
        secret_key=config.require("secretKey"),
        skip_credentials_validation=True,
        skip_metadata_api_check=True,
        skip_requesting_account_id=True,
    )
