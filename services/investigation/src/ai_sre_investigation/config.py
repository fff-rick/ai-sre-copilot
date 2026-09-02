"""Runtime configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration without embedded credentials."""

    model_config = SettingsConfigDict(env_prefix="AI_SRE_", extra="ignore")

    service_name: str = "investigation-service"
    environment: str = "development"
    database_url: str | None = Field(default=None, repr=False)
    tool_gateway_target: str = "tool-gateway:9091"
    tool_gateway_token: str | None = Field(default=None, repr=False)


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
