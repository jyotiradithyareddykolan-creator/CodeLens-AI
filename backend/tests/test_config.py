import pytest
from pydantic import ValidationError

from app.core.config import Settings

SAFE_PRODUCTION = {
    "ENVIRONMENT": "production",
    "SECRET_KEY": "x" * 48,
    "DEBUG": False,
    "GITHUB_WEBHOOK_SECRET": "webhook-secret",
}


def make(**overrides) -> Settings:
    # _env_file=None: never read the developer's real .env in tests.
    return Settings(_env_file=None, **overrides)


def test_development_defaults_are_allowed():
    s = make(ENVIRONMENT="development")
    assert s.DATABASE_ECHO is False
    assert s.FRONTEND_URL == "http://localhost:5173"
    assert s.BACKEND_URL == "http://localhost:8000"


def test_public_urls_are_normalised():
    s = make(FRONTEND_URL="https://app.example.test/", BACKEND_URL="https://api.example.test/")
    assert s.FRONTEND_URL == "https://app.example.test"
    assert s.BACKEND_URL == "https://api.example.test"


def test_safe_production_configuration_is_accepted():
    make(**SAFE_PRODUCTION)


@pytest.mark.parametrize(
    "override",
    [
        {"SECRET_KEY": "default-insecure-development-secret-key-32-chars-minimum"},
        {"SECRET_KEY": "short"},
        {"SECRET_KEY": "replace-with-a-very-secure-random-secret-key-32-chars-min"},
        {"DEBUG": True},
        {"DATABASE_ECHO": True},
        {"GITHUB_WEBHOOK_SECRET": None},
    ],
)
def test_unsafe_production_configuration_is_rejected(override):
    with pytest.raises(ValidationError) as exc:
        make(**{**SAFE_PRODUCTION, **override})
    # The error names the problem but never echoes configuration values.
    message = str(exc.value)
    assert "Unsafe production configuration" in message
    assert "input_value" not in message
    assert "webhook-secret" not in message
    assert "x" * 48 not in message
