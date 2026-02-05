"""IAM policy helpers for SkyPilot API server."""

from __future__ import annotations


def build_api_service_policy(account_id: str) -> dict:
    """Build IAM policy document for the SkyPilot API service."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": "arn:aws:ec2:*::image/ami-*",
            },
            {
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": [
                    f"arn:aws:ec2:*:{account_id}:instance/*",
                    f"arn:aws:ec2:*:{account_id}:network-interface/*",
                    f"arn:aws:ec2:*:{account_id}:subnet/*",
                    f"arn:aws:ec2:*:{account_id}:volume/*",
                    f"arn:aws:ec2:*:{account_id}:security-group/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:TerminateInstances",
                    "ec2:DeleteTags",
                    "ec2:StartInstances",
                    "ec2:CreateTags",
                    "ec2:StopInstances",
                ],
                "Resource": f"arn:aws:ec2:*:{account_id}:instance/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:Describe*",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateSecurityGroup",
                    "ec2:AuthorizeSecurityGroupIngress",
                ],
                "Resource": f"arn:aws:ec2:*:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": "iam:CreateServiceLinkedRole",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"iam:AWSServiceName": "spot.amazonaws.com"}
                },
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetRole",
                    "iam:PassRole",
                    "iam:CreateRole",
                    "iam:AttachRolePolicy",
                ],
                "Resource": [
                    f"arn:aws:iam::{account_id}:role/skypilot-v1",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iam:GetInstanceProfile",
                    "iam:CreateInstanceProfile",
                    "iam:AddRoleToInstanceProfile",
                ],
                "Resource": f"arn:aws:iam::{account_id}:instance-profile/skypilot-v1",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateImage",
                    "ec2:CopyImage",
                    "ec2:DeregisterImage",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:DeleteSecurityGroup",
                    "ec2:ModifyInstanceAttribute",
                ],
                "Resource": f"arn:aws:ec2:*:{account_id}:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                ],
                "Resource": "arn:aws:s3:::*/*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": "arn:aws:s3:::*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:ListAllMyBuckets",
                "Resource": "*",
            },
        ],
    }
