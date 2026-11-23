"""应用配置：从仓库根目录的 .env 读取（pydantic-settings）。

约定：
- 所有连接地址使用 127.0.0.1，禁止 localhost。
- AES_FERNET_KEY 为空或非法时，首次加载自动生成 32 字节 url-safe base64 密钥并回写 .env。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> 仓库根
ROOT_DIR: Path = Path(__file__).resolve().parents[3]
ENV_FILE: Path = ROOT_DIR / ".env"


def _is_valid_fernet_key(value: str) -> bool:
    """Fernet 密钥必须是 32 字节 url-safe base64 编码。"""
    if not value:
        return False
    try:
        Fernet(value.encode("utf-8"))
        return True
    except (ValueError, TypeError, Exception):  # noqa: BLE001 - 非法密钥统一视为无效
        return False


def ensure_fernet_key(env_file: Path = ENV_FILE) -> str:
    """确保 .env 中存在可用的 AES_FERNET_KEY，缺失或非法时就地生成并回写。"""
    env_value = os.environ.get("AES_FERNET_KEY", "").strip()
    if _is_valid_fernet_key(env_value):
        return env_value

    if not env_file.exists():
        raise RuntimeError(f"缺少环境文件：{env_file}，请先执行 copy .env.example .env")

    text = env_file.read_text(encoding="utf-8")
    new_key = Fernet.generate_key().decode("utf-8")

    lines = text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("AES_FERNET_KEY="):
            current = stripped.split("=", 1)[1].strip()
            if _is_valid_fernet_key(current):
                return current
            lines[index] = f"AES_FERNET_KEY={new_key}"
            replaced = True
            break

    if not replaced:
        lines.append(f"AES_FERNET_KEY={new_key}")

    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    os.environ["AES_FERNET_KEY"] = new_key
    return new_key


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "i2v-platform"
    APP_ENV: str = "local"
    DEBUG: bool = True
    SQL_ECHO: bool = False
    # 日志级别：INFO / WARNING / ERROR（测试或生产按需调高，减少噪声）
    LOG_LEVEL: str = "INFO"

    # MySQL
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 13306
    MYSQL_DATABASE: str = "i2v_platform"
    MYSQL_USER: str = "i2v"
    MYSQL_PASSWORD: str = "i2v_pwd_2026"
    # 仅测试库引导（建库 / 授权）时使用，业务连接一律走 MYSQL_USER
    MYSQL_ROOT_PASSWORD: str = "root_pwd_2026"

    # Redis
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 16379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # MinIO / S3 兼容
    MINIO_ENDPOINT: str = "127.0.0.1:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin123"
    MINIO_SECURE: bool = False
    MINIO_REGION: str = "us-east-1"
    MINIO_BUCKET_PUBLIC: str = "i2v-public"
    MINIO_BUCKET_VIDEOS: str = "i2v-videos"
    # 下载 CDN：留空则直接用 MinIO 端点；填了就把签名 URL 的 host 换掉（签名参数不变）
    CDN_BASE_URL: str = ""
    # 成片下载签名有效期（手册红线：严格 30 分钟）
    DOWNLOAD_URL_TTL: int = 1800

    # 安全
    AES_FERNET_KEY: str = ""
    # 初始密码可查看时长：M2 手册要求导入/重置后 7 天内可查看（168 小时）
    INIT_PWD_VIEW_HOURS: int = 168
    # bcrypt cost：10 时单次约 56ms、16 进程并行下 1000 个初始密码约 5.5~6.7s，
    # 是「Excel 导入 1000 行 ≤10 秒」与「登录 ≤800ms」两个指标的可行交点（见 decisions.md A7）。
    BCRYPT_ROUNDS: int = 10
    # 批量哈希进程池 worker 数（0 = 按 CPU 自动，上限 16）
    HASH_POOL_WORKERS: int = 16
    # 会话（Redis 单点，同账号后登录踢掉前一个）
    SESSION_TTL_SECONDS: int = 86400
    # 登录保护
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCK_MINUTES: int = 5
    # 账号留空时自动生成的规则：前缀 + 6 位递增序号（C2026000001）
    AUTO_ACCOUNT_PREFIX: str = "C2026"
    AUTO_ACCOUNT_DIGITS: int = 6

    # 初始管理员
    ADMIN_INIT_USERNAME: str = "admin"
    INIT_ADMIN_PASSWORD: str = "Admin@2026!"
    ADMIN_INIT_PASSWORD: str = ""

    # Excel 导入
    EXCEL_IMPORT_MAX_ROWS: int = 5000

    # Mock Worker
    MOCK_WORKER: bool = True
    MOCK_SUCCESS_RATE: float = 0.8
    MOCK_QUEUE_DELAY: float = 1.0
    MOCK_RUN_DELAY: float = 1.0
    # 真实 Worker 的 Celery 任务入口（MOCK_WORKER=false 时由 handoff_sync 调用 .delay）
    CELERY_TASK_MODULE: str = "worker.tasks"
    CELERY_TASK_FUNC: str = "enqueue_video_generation"

    # 内容审核
    MODERATION_PROVIDER: str = "mock"
    MODERATION_ENDPOINT: str = ""
    MODERATION_TIMEOUT: float = 10.0

    @field_validator("AES_FERNET_KEY")
    @classmethod
    def _validate_fernet_key(cls, value: str) -> str:
        if value and not _is_valid_fernet_key(value):
            raise ValueError("AES_FERNET_KEY 必须是 32 字节 url-safe base64 的 Fernet 密钥")
        return value

    # ---- 派生连接串 ----
    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def minio_endpoint_url(self) -> str:
        scheme = "https" if self.MINIO_SECURE else "http"
        return f"{scheme}://{self.MINIO_ENDPOINT}"

    @property
    def admin_password(self) -> str:
        """兼容 decisions.md 的 ADMIN_INIT_PASSWORD 与手册的 INIT_ADMIN_PASSWORD 两种命名。"""
        return self.INIT_ADMIN_PASSWORD or self.ADMIN_INIT_PASSWORD or "Admin@2026!"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    ensure_fernet_key()
    return Settings()
