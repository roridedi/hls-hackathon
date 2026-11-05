from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from um_agentic.contracts import Contract, LineOfBusiness, Role


class ClockPolicy(Contract):
    standard_hours: float = Field(gt=0)
    expedited_hours: float = Field(gt=0)
    pause_for_missing_info: bool = False
    warning_fraction: float = Field(gt=0, lt=1)


class Budget(Contract):
    timeout_seconds: float = Field(gt=0)
    token_budget: int = Field(gt=0)


class DevAccount(Contract):
    actor_id: str
    roles: frozenset[Role]


class Configuration(Contract):
    clock_policies: dict[str, ClockPolicy]
    auto_approve_enabled: dict[LineOfBusiness, bool]
    adverse_reviewer_roles: frozenset[Role]
    agent_timeout_seconds: float = Field(gt=0)
    agent_token_budget: int = Field(gt=0)
    agent_budgets: dict[str, Budget]
    extraction_score_threshold: float = Field(ge=0, le=1)
    approval_removal_threshold: float = Field(gt=0, le=1)
    meaningful_volume: int = Field(ge=1)
    review_cost_estimate: float = Field(ge=0)
    appeal_rights: str = Field(min_length=1)
    dev_accounts: dict[str, DevAccount]

    @model_validator(mode="after")
    def licensed_roles_only(self) -> Configuration:
        if not self.adverse_reviewer_roles <= {Role.PHYSICIAN, Role.PHARMACIST}:
            raise ValueError("Adverse decisions require a licensed clinical role.")
        for line in LineOfBusiness:
            if f"{line}:DEFAULT" not in self.clock_policies:
                raise ValueError(f"A default clock policy is required for {line}.")
        return self

    def clock_policy(self, line: LineOfBusiness, jurisdiction: str) -> ClockPolicy:
        return self.clock_policies.get(
            f"{line}:{jurisdiction}", self.clock_policies[f"{line}:DEFAULT"]
        )

    def budget(self, agent: str) -> Budget:
        return self.agent_budgets.get(
            agent,
            Budget(
                timeout_seconds=self.agent_timeout_seconds,
                token_budget=self.agent_token_budget,
            ),
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UM_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+pysqlite:///./local.db"
    config_path: Path = Path(__file__).parent / "config" / "defaults.json"
    storage_path: Path = Path("var")
    model_backend: Literal["fake", "foundry"] = "fake"
    auth_mode: Literal["development", "entra"] = "development"
    compliance_validated: bool = False
    auto_approve_pharmacy: bool | None = None
    auto_approve_medical: bool | None = None
    foundry_endpoint: str | None = None
    foundry_model: str | None = None
    verifier_model: str | None = None
    model_input_cost_per_million: float | None = Field(default=None, ge=0)
    model_output_cost_per_million: float | None = Field(default=None, ge=0)
    entra_tenant_id: str | None = None
    entra_audience: str | None = None
    entra_group_roles: dict[str, list[Role]] = {}
    azure_storage_account_url: str | None = None
    azure_storage_container: str = "um"
    document_intelligence_endpoint: str | None = None
    fhir_endpoint: str | None = None
    search_endpoint: str | None = None
    search_index: str = "synthetic-policy"
    otlp_endpoint: str | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    @model_validator(mode="after")
    def safe_cloud_configuration(self) -> Settings:
        if self.auth_mode == "entra" and not (self.entra_tenant_id and self.entra_audience):
            raise ValueError("Entra authentication requires a tenant and audience.")
        if self.model_backend == "foundry" and not (self.foundry_endpoint and self.foundry_model):
            raise ValueError("Foundry requires a project endpoint and model deployment.")
        if self.environment == "production":
            if self.auth_mode != "entra" or self.model_backend != "foundry":
                raise ValueError("Production requires Entra and Foundry.")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL.")
            if not self.compliance_validated:
                raise ValueError("Compliance must validate policies before production use.")
        return self

    def configuration(self) -> Configuration:
        values = json.loads(self.config_path.read_text())
        flags = values["auto_approve_enabled"]
        if self.auto_approve_pharmacy is not None:
            flags["PHARMACY"] = self.auto_approve_pharmacy
        if self.auto_approve_medical is not None:
            flags["MEDICAL"] = self.auto_approve_medical
        return Configuration.model_validate(values)
