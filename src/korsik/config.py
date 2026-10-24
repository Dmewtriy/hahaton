from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KORSIK_", env_file=".env", extra="ignore")

    environment: Literal["development", "production", "test"] = "development"
    database_url: str = "sqlite:///./data/korsik.db"
    data_dir: Path = Path("data")
    bootstrap_token: str = ""
    transport: Literal["mock", "max"] = "mock"
    max_token: str = ""
    max_base_url: str = "https://platform-api2.max.ru"
    max_bot_name: str = ""
    max_webhook_secret: str = ""
    max_ca_file: str | None = None
    webapp_public_url: str = ""
    webapp_auth_max_age_seconds: int = 3600
    allow_local_admin_login: bool = True
    ml_mode: Literal["stub", "http"] = "stub"
    ml_base_url: str = "http://localhost:9000"
    ml_token: str = ""
    ml_timeout_seconds: float = 15
    enable_demo: bool = False
    max_upload_bytes: int = 35 * 1024 * 1024
    invitation_hours: int = 168
    worker_interval_seconds: float = 1
    job_lease_seconds: int = 180
    max_job_attempts: int = 5

    @model_validator(mode="after")
    def validate_environment(self):
        if self.webapp_public_url:
            from urllib.parse import urlparse

            url = urlparse(self.webapp_public_url)
            if url.scheme != "https" or not url.hostname or url.username or url.fragment:
                raise ValueError("Mini-app public URL must be an HTTPS URL without credentials or fragment")
        if not 60 <= self.webapp_auth_max_age_seconds <= 3600:
            raise ValueError("Mini-app launch validity must be 60..3600 seconds")
        if len(self.bootstrap_token) < 32:
            raise ValueError(
                "Set KORSIK_BOOTSTRAP_TOKEN (at least 32 characters), or run python -m korsik.cli init"
            )
        if self.transport == "max" and not self.max_token:
            raise ValueError("KORSIK_MAX_TOKEN is required with transport=max")
        if self.environment == "production":
            if self.enable_demo or self.ml_mode != "http" or self.transport != "max":
                raise ValueError("Production requires real MAX/ML and disables demo endpoints")
            if not self.database_url.startswith("postgresql") or len(self.max_webhook_secret) < 32:
                raise ValueError("Production requires PostgreSQL and a strong webhook secret")
            if not self.ml_token:
                raise ValueError("Production requires ML authentication")
        return self
