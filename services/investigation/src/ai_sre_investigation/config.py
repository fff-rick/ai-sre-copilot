"""Runtime configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration without embedded credentials."""

    model_config = SettingsConfigDict(env_prefix="AI_SRE_", extra="ignore", env_ignore_empty=True)

    service_name: str = "investigation-service"
    environment: str = "development"
    database_url: str | None = Field(default=None, repr=False)
    tool_gateway_target: str = "tool-gateway:9091"
    tool_gateway_token: str | None = Field(default=None, repr=False)
    model_base_url: str | None = None
    model_api_key: str | None = Field(default=None, repr=False)
    model_id: str | None = None
    model_timeout_seconds: float = Field(default=60, gt=0, le=300)
    embedding_base_url: str | None = None
    embedding_api_key: str | None = Field(default=None, repr=False)
    embedding_model_id: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=8, le=4_096)
    embedding_timeout_seconds: float = Field(default=30, gt=0, le=300)
    mutation_allowed_namespace: str = "ai-sre-test"
    remediation_validation_delay_seconds: float = Field(default=2, ge=0, le=60)


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
