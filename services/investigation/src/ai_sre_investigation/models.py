"""Small domain-neutral response models used by the HTTP shell."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Machine-readable service health response."""

    model_config = ConfigDict(frozen=True)

    service: str
    status: Literal["ok", "ready"]
    environment: str
