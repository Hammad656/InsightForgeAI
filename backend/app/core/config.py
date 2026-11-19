from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cookie_secure: bool = True
    database_url: str = ""
    migration_database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]
    reporting_database_url: SecretStr = SecretStr("")
    ai_provider: str = "disabled"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    report_storage: Literal["local", "s3"] = "local"
    report_directory: Path = Path(".private-reports")
    report_s3_bucket: str = ""
    report_s3_endpoint: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
