"""Configuration scoped to the video worker."""

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import ENV_FILE


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    MINIMAX_API_KEY: SecretStr = SecretStr("")
    MINIMAX_BASE_URL: str = "https://api.minimax.cn"
    I2V_ACCOUNT_CONCURRENCY: int = Field(default=30, ge=1)
    I2V_POLL_INTERVAL_SECONDS: float = Field(default=5, gt=0, le=30)
    I2V_FFMPEG: str = "ffmpeg"
    I2V_LEASE_SECONDS: int = Field(default=60, ge=5, le=300)
    I2V_HEARTBEAT_SECONDS: float = Field(default=15, gt=0, le=90)
    I2V_RECONCILE_MAX_HOURS: int = Field(default=6, ge=1, le=144)

    @model_validator(mode="after")
    def validate_heartbeat(self):
        if self.I2V_HEARTBEAT_SECONDS * 2 >= self.I2V_LEASE_SECONDS:
            raise ValueError("Heartbeat interval must be less than half the lease duration")
        return self


@lru_cache
def worker_settings() -> WorkerSettings:
    return WorkerSettings()
