from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel, Field


class ServiceHealth(BaseModel):
    status: str = Field(..., description="Service status: healthy, degraded, or unhealthy")
    details: Optional[str] = Field(None, description="Diagnostic details or latency information")


class HealthResponse(BaseModel):
    status: str = Field("healthy", description="Overall platform status")
    app: str = Field("CodeLens AI", description="Application name")
    version: str = Field("0.1.0", description="Current release version")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="UTC check timestamp")
    environment: str = Field("development", description="Environment mode")
    services: Dict[str, ServiceHealth] = Field(default_factory=dict, description="Status of connected backing services")


class ReadyResponse(BaseModel):
    ready: bool = Field(..., description="Readiness state for accepting user traffic")
    checks: Dict[str, bool] = Field(default_factory=dict, description="Readiness checks per dependency")
