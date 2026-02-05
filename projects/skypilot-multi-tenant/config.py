"""Configuration models and loaders for the SkyPilot multi-tenant project."""

from __future__ import annotations

import pulumi
from pydantic import BaseModel, Field, model_validator

from pulumi_eks_ml import eks


class TailscaleConfig(BaseModel):
    enabled: bool = False
    oauth_secret_arn: str | None = None


class DataPlaneConfig(BaseModel):
    name: str
    user_role_arn: str | None = None


class RegionConfig(BaseModel):
    region: str
    node_pools: list[dict]
    sp_data_planes: list[DataPlaneConfig] = Field(default_factory=list)

    @property
    def eks_node_pools(self) -> list[eks.NodePoolConfig]:
        return [eks.NodePoolConfig.from_dict(p) for p in self.node_pools]


class HubConfig(RegionConfig):
    tailscale: TailscaleConfig = Field(default_factory=TailscaleConfig)


class ProjectConfig(BaseModel):
    hub: HubConfig
    spokes: list[RegionConfig] = Field(default_factory=list)
    component_versions: dict = Field(default_factory=dict, alias="versions")

    @model_validator(mode="after")
    def validate_unique_regions(self):
        spoke_regions = [s.region for s in self.spokes]
        if len(spoke_regions) != len(set(spoke_regions)):
            raise ValueError(
                f"Spoke regions must be unique. Found duplicates in: {spoke_regions}"
            )
        if self.hub.region in spoke_regions:
            raise ValueError(
                f"Hub region '{self.hub.region}' cannot overlap with any of the spoke regions."
            )
        return self


def load_project_config(pulumi_config: pulumi.Config) -> ProjectConfig:
    """Load and validate project configuration."""
    return ProjectConfig(
        hub=pulumi_config.require_object("hub"),
        spokes=pulumi_config.get_object("spokes") or [],
        component_versions=pulumi_config.get_object("component_versions") or {},
    )


def get_all_regions(config: ProjectConfig) -> list[str]:
    """Return the hub + spokes regions in order."""
    return [config.hub.region] + [s.region for s in config.spokes]
