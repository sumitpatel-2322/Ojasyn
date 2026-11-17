from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"

_FORBIDDEN_JWT_SECRETS = {
    "ojasyn-super-secret-development-key-change-in-production",
    "secret",
    "changeme",
    "change_me",
    "change_me_replace_with_min_32_char_random_secret",
}


class Settings(BaseSettings):
    """Typed application settings with fail-fast validation for security & DB configuration."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_NAME: str = "Ojasyn QMS API"
    APP_ENV: Literal["development", "test", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = Field(..., min_length=1)
    DB_ECHO: bool = False

    JWT_SIGNING_KEY: Optional[str] = None
    SECRET_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "ojasyn-api"
    JWT_AUDIENCE: str = "ojasyn-clients"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, ge=1, le=1440)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=90)

    UPLOAD_MAX_BYTES: int = Field(default=10 * 1024 * 1024, ge=1024)
    MIME_ALLOWLIST: List[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/tiff",
        ]
    )
    CORS_ALLOWED_ORIGINS: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    GROQ_API_KEY: Optional[str] = None
    LLM_MODEL_NAME: str = "llama-3.3-70b-versatile"
    LLM_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0.0)

    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    LOCAL_STORAGE_ROOT: str = str(_BACKEND_DIR / "vault_storage")

    REDIS_URL: Optional[str] = None

    @field_validator("DATABASE_URL")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise ValueError("DATABASE_URL must not be empty.")
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        resolved_key = (self.JWT_SIGNING_KEY or self.SECRET_KEY or "").strip()
        if not resolved_key:
            raise ValueError(
                "JWT_SIGNING_KEY (or SECRET_KEY) must be configured; hardcoded defaults are prohibited."
            )
        if resolved_key.lower() in _FORBIDDEN_JWT_SECRETS or "change_me" in resolved_key.lower():
            raise ValueError("JWT_SIGNING_KEY must not use an insecure placeholder or hardcoded default.")
        if len(resolved_key) < 32:
            raise ValueError("JWT_SIGNING_KEY must be at least 32 characters long.")

        self.JWT_SIGNING_KEY = resolved_key

        if self.APP_ENV in {"staging", "production"}:
            if "*" in self.CORS_ALLOWED_ORIGINS:
                raise ValueError("CORS_ALLOWED_ORIGINS must not contain '*' in staging/production.")
            if self.DB_ECHO:
                raise ValueError("DB_ECHO must be false in staging/production.")
        return self

    @property
    def jwt_signing_key(self) -> str:
        assert self.JWT_SIGNING_KEY is not None
        return self.JWT_SIGNING_KEY


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
