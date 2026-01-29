from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws
import pytest

from pulumi_eks_ml.eks import config
from pulumi_eks_ml.eks.cluster import EKSClusterAddonInstaller
from pulumi_eks_ml.eks.cluster import EKSCluster

_ACCOUNT_ID = "123456789012"
_REGION = "us-west-2"
_OIDC_ISSUER = "oidc.eks.us-west-2.amazonaws.com/id/EXAMPLE"
_OIDC_PROVIDER_ARN = f"arn:aws:iam::{_ACCOUNT_ID}:oidc-provider/{_OIDC_ISSUER}"

_MINIMAL_KUBECONFIG = {
    "apiVersion": "v1",
    "clusters": [{"cluster": {"server": "https://mock.eks.local"}, "name": "mock"}],
    "contexts": [{"context": {"cluster": "mock", "user": "mock"}, "name": "mock"}],
    "current-context": "mock",
    "kind": "Config",
    "users": [{"name": "mock", "user": {"token": "fake"}}],
}


class EKSClusterMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: list[pulumi.runtime.MockResourceArgs] = []

    def reset(self) -> None:
        self.resources.clear()

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append(args)
        resource_type = args.typ
        outputs = dict(args.inputs)

        if resource_type == "eks:index:Cluster":
            outputs.update(
                {
                    "cluster_security_group_id": "sg-cluster",
                    "node_security_group_id": "sg-nodes",
                    "kubeconfig_json": json.dumps(_MINIMAL_KUBECONFIG),
                    "oidc_provider_arn": _OIDC_PROVIDER_ARN,
                    "oidc_issuer": _OIDC_ISSUER,
                    "fargate_profile_id": "fp-123456",
                }
            )
            return "ekscluster-id", outputs

        if resource_type == "aws:ec2/securityGroup:SecurityGroup":
            security_group_id = outputs.get("id") or f"sg-{args.name}"
            outputs.setdefault("id", security_group_id)
            return security_group_id, outputs

        if resource_type == "aws:iam/role:Role":
            role_name = outputs.get("name") or args.name
            outputs.setdefault("name", role_name)
            outputs.setdefault("arn", f"arn:aws:iam::{_ACCOUNT_ID}:role/{role_name}")
            return f"{role_name}-id", outputs

        if resource_type == "aws:eks/fargateProfile:FargateProfile":
            profile_id = outputs.get("id") or f"fp-{args.name}"
            outputs.setdefault("id", profile_id)
            return profile_id, outputs

        if resource_type == "pulumi_kubernetes:Provider":
            return f"provider-{args.name}", outputs

        if resource_type in {
            "kubernetes:core/v1:Namespace",
            "kubernetes:core/v1:ConfigMap",
        }:
            outputs.setdefault("id", f"{args.name}-id")
            return outputs["id"], outputs

        outputs.setdefault("id", f"{args.name}-id")
        return outputs["id"], outputs

    def call(self, args: pulumi.runtime.MockCallArgs):
        if args.token in {
            "aws:getCallerIdentity",
            "aws:index/getCallerIdentity:getCallerIdentity",
        }:
            return {
                "accountId": _ACCOUNT_ID,
                "account_id": _ACCOUNT_ID,
                "arn": f"arn:aws:iam::{_ACCOUNT_ID}:user/mock",
                "userId": "AIDACKCEVSQ6C2EXAMPLE",
            }
        if args.token in {"aws:getRegion", "aws:index/getRegion:getRegion"}:
            return {"name": _REGION, "region": _REGION}
        return {}


mocks = EKSClusterMocks()
pulumi.runtime.set_mocks(mocks)


@pytest.fixture(autouse=True)
def _reset_mocks() -> None:
    mocks.reset()


def _make_recording_addon(
    name: str, events: list[str]
) -> type[pulumi.ComponentResource]:
    class _RecordingAddon(pulumi.ComponentResource):
        def __init__(self, resource_name: str, opts: pulumi.ResourceOptions) -> None:
            super().__init__(
                "pulumi-eks-ml:test:RecordingAddon", resource_name, None, opts
            )
            events.append(name)
            self.register_outputs({})

        @classmethod
        def from_cluster(
            cls,
            cluster,
            parent=None,
            extra_dependencies=None,
            version=None,
        ):
            return cls(
                f"{cluster.name}-{name}",
                opts=pulumi.ResourceOptions(
                    parent=parent,
                    depends_on=[cluster, *(extra_dependencies or [])],
                ),
            )

    return _RecordingAddon


def _create_cluster(
    addon_types: list[type[pulumi.ComponentResource]] | None = None,
) -> EKSCluster:
    cluster = EKSCluster(
        "test",
        vpc_id="vpc-123",
        subnet_ids=["subnet-1", "subnet-2"],
        node_pools=[],
    )
    if addon_types:
        EKSClusterAddonInstaller(
            "test-addons",
            cluster=cluster,
            addon_types=addon_types,
        )
    return cluster


def _rule_snapshot(rule: aws.ec2.SecurityGroupRule) -> pulumi.Output[dict]:
    return pulumi.Output.all(
        rule.type,
        rule.from_port,
        rule.to_port,
        rule.protocol,
        getattr(rule, "self"),
        rule.cidr_blocks,
        rule.source_security_group_id,
        rule.security_group_id,
        rule.description,
    ).apply(
        lambda values: {
            "type": values[0],
            "from_port": values[1],
            "to_port": values[2],
            "protocol": values[3],
            "self": values[4],
            "cidr_blocks": values[5],
            "source_security_group_id": values[6],
            "security_group_id": values[7],
            "description": values[8],
        }
    )


def _parse_policy_document(policy_document: str | dict) -> dict:
    if isinstance(policy_document, dict):
        return policy_document
    return json.loads(policy_document)


def _rule_matches(rule: dict, **criteria: object) -> bool:
    return all(rule.get(key) == value for key, value in criteria.items())


@pulumi.runtime.test
def test_security_group_rules() -> pulumi.Output[None]:
    cluster = _create_cluster()
    rule_outputs = pulumi.Output.all(
        pulumi.Output.all(*[_rule_snapshot(rule) for rule in cluster.extra_sg_rules]),
        cluster.node_security_group.id,
        cluster.k8s.cluster_security_group_id,
    )

    def check(values: list[object]) -> None:
        rules, node_sg_id, cluster_sg_id = values
        expected_count = 5 + len(config.CLUSTER_FROM_NODE_SG_RULES)
        assert len(rules) == expected_count

        assert _rule_matches(
            next(r for r in rules if r["self"]),
            type="ingress",
            from_port=0,
            to_port=0,
            protocol="-1",
            security_group_id=node_sg_id,
        )
        assert _rule_matches(
            next(
                r
                for r in rules
                if r.get("source_security_group_id") == cluster_sg_id
                and r["from_port"] == 10250
            ),
            type="ingress",
            to_port=10250,
            protocol="tcp",
            security_group_id=node_sg_id,
        )
        assert _rule_matches(
            next(
                r
                for r in rules
                if r.get("source_security_group_id") == cluster_sg_id
                and r["from_port"] == 443
            ),
            type="ingress",
            to_port=443,
            protocol="tcp",
            security_group_id=node_sg_id,
        )
        assert _rule_matches(
            next(
                r
                for r in rules
                if r.get("source_security_group_id") == cluster_sg_id
                and r["from_port"] == 9443
            ),
            type="ingress",
            to_port=9443,
            protocol="tcp",
            security_group_id=node_sg_id,
        )
        assert _rule_matches(
            next(r for r in rules if r["type"] == "egress"),
            from_port=0,
            to_port=0,
            protocol="-1",
            cidr_blocks=["0.0.0.0/0"],
            security_group_id=node_sg_id,
        )

        for port, protocol, description in config.CLUSTER_FROM_NODE_SG_RULES:
            assert any(
                _rule_matches(
                    rule,
                    type="ingress",
                    from_port=port,
                    to_port=port,
                    protocol=protocol,
                    source_security_group_id=node_sg_id,
                    security_group_id=cluster_sg_id,
                    description=description,
                )
                for rule in rules
            )

    return rule_outputs.apply(check)


@pulumi.runtime.test
def test_fargate_pod_execution_role_trust_policy() -> pulumi.Output[None]:
    cluster = _create_cluster()

    def check(_) -> None:
        
        role_args = next(
            args
            for args in mocks.resources
            if args.typ == "aws:iam/role:Role"
            and args.name == f"{cluster.name}-fgt-pod-role"
        )

        policy_document = role_args.inputs.get(
            "assume_role_policy", role_args.inputs.get("assumeRolePolicy")
        )
        policy = _parse_policy_document(policy_document)
        source_arn = policy["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"]
        
        # In mocks, cluster.k8s_name is "test" (from _create_cluster)
        expected = (
            f"arn:aws:eks:{_REGION}:{_ACCOUNT_ID}:fargateprofile/test/*"
        )
        assert source_arn == expected

    return cluster.fargate_pod_execution_role.arn.apply(check)


@pulumi.runtime.test
def test_addon_bootstrap_order() -> pulumi.Output[None]:
    events: list[str] = []
    _create_cluster(
        addon_types=[
            _make_recording_addon("first", events),
            _make_recording_addon("second", events),
        ]
    )

    def check(_: str) -> None:
        assert events == ["first", "second"]

    return pulumi.Output.from_input("ready").apply(check)
