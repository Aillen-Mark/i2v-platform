"""Configuration scoped to the video worker."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import ENV_FILE


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    MINIMAX_API_KEY: SecretStr = SecretStr("")
    MINIMAX_BASE_URL: str = "https://api.minimax.cn"
    I2V_ACCOUNT_CONCURRENCY: int = Field(default=30, ge=1)
    I2V_POLL_INTERVAL_SECONDS: float = Field(default=5, gt=0, le=30)
    I2V_FFMPEG: str = "ffmpeg"


@lru_cache
def worker_settings() -> WorkerSettings:
    return WorkerSettings()
