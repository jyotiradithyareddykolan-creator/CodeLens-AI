from typing import List, Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET_KEY = "default-insecure-development-secret-key-32-chars-minimum"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Validation errors must never echo configuration values (they include secrets).
        hide_input_in_errors=True,
    )

    # Project metadata
    PROJECT_NAME: str = "CodeLens AI"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    # SQL statement logging is opt-in: bound parameters can contain sensitive data.
    DATABASE_ECHO: bool = False

    # Public URLs used to build OAuth redirects
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Security
    SECRET_KEY: str = INSECURE_DEFAULT_SECRET_KEY
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    OAUTH_STATE_EXPIRE_SECONDS: int = 600

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
    # Public slug of the GitHub App (github.com/apps/<slug>), used for the install link
    GITHUB_APP_SLUG: Optional[str] = None
    GITHUB_CLIENT_ID: Optional[str] = None
    GITHUB_CLIENT_SECRET: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None
    GITHUB_PRIVATE_KEY_PATH: Optional[str] = None
    GITHUB_PRIVATE_KEY: Optional[str] = None

    # GitHub API client behaviour
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_TIMEOUT_SECONDS: float = 30.0
    GITHUB_MAX_RETRIES: int = 4
    # Longest single wait for a rate-limit reset / Retry-After before giving up.
    GITHUB_MAX_RETRY_WAIT_SECONDS: float = 120.0

    # Repository discovery limits (protect workers from pathological repositories)
    INDEX_MAX_CANDIDATE_FILES: int = 50_000
    INDEX_MAX_TREE_REQUESTS: int = 2_000

    # Processing Limits
    MAX_FILE_SIZE_BYTES: int = 1024 * 1024  # 1 MB
    CHUNK_MAX_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50

    @field_validator("FRONTEND_URL", "BACKEND_URL")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def enforce_production_safety(self) -> "Settings":
        """Refuse to start in production with development-grade security settings."""
        if self.ENVIRONMENT != "production":
            return self
        problems = []
        if self.SECRET_KEY == INSECURE_DEFAULT_SECRET_KEY or len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if self.SECRET_KEY.startswith("replace-with"):
            problems.append("SECRET_KEY still contains the .env.example placeholder")
        if self.DEBUG:
            problems.append("DEBUG must be false")
        if self.DATABASE_ECHO:
            problems.append("DATABASE_ECHO must be false")
        if not self.GITHUB_WEBHOOK_SECRET:
            problems.append("GITHUB_WEBHOOK_SECRET must be set")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


settings = Settings()
