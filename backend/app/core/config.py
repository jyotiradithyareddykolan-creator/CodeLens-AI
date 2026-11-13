from typing import List, Optional
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Project metadata
    PROJECT_NAME: str = "CodeLens AI"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Security
    SECRET_KEY: str = "default-insecure-development-secret-key-32-chars-minimum"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]

    # Database
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "codelens_ai"
    
    DATABASE_URL: Optional[str] = None
    ASYNC_DATABASE_URL: Optional[str] = None

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str], info) -> str:
        if isinstance(v, str) and v:
            return v
        values = info.data
        user = values.get("POSTGRES_USER", "postgres")
        pwd = values.get("POSTGRES_PASSWORD", "postgres")
        host = values.get("POSTGRES_HOST", "localhost")
        port = values.get("POSTGRES_PORT", 5432)
        db = values.get("POSTGRES_DB", "codelens_ai")
        return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"

    @field_validator("ASYNC_DATABASE_URL", mode="before")
    @classmethod
    def assemble_async_db_connection(cls, v: Optional[str], info) -> str:
        if isinstance(v, str) and v:
            return v
        values = info.data
        user = values.get("POSTGRES_USER", "postgres")
        pwd = values.get("POSTGRES_PASSWORD", "postgres")
        host = values.get("POSTGRES_HOST", "localhost")
        port = values.get("POSTGRES_PORT", 5432)
        db = values.get("POSTGRES_DB", "codelens_ai")
        return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}"

    # Redis & Celery
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # AI & Embeddings (Gemini)
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_EMBEDDING_MODEL: str = "models/text-embedding-004"
    GEMINI_EMBEDDING_DIMENSIONS: int = 768
    GEMINI_LLM_MODEL: str = "gemini-1.5-flash"

    # GitHub App Integration
    GITHUB_APP_ID: Optional[str] = None
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None
    GITHUB_PRIVATE_KEY_PATH: Optional[str] = None
    GITHUB_PRIVATE_KEY: Optional[str] = None

    # Processing Limits
    MAX_FILE_SIZE_BYTES: int = 1024 * 1024  # 1 MB
    CHUNK_MAX_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50


settings = Settings()
