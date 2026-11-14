"""Secret scanner tests.

Fake credentials are assembled at runtime from fragments so this test file itself
does not contain strings that look like real secrets to scanners.
"""

from pathlib import Path

import pytest

from app.indexing.secret_scanner import SecretFinding, scan_text

BACKEND = Path(__file__).resolve().parents[1]


def j(*parts: str) -> str:
    return "".join(parts)


FAKE_GITHUB = j("gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
FAKE_AWS = j("AK", "IA", "Z7Q3K9TXW2M4P8RB")
FAKE_GOOGLE = j("AI", "za", "Sy", "B7x9Qm3Kp2Lw8Nv4Rt6Yh1Jc5Fd0Gs2Az")
FAKE_PRIVATE_KEY = j("-----BEGIN ", "RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----")
FAKE_GENERIC = j("q8Z", "!vR2", "mK9#xL4", "pW7tY3")
FAKE_DB_PASSWORD = j("Zq7", "xR2m", "Kp9w")


def rules(text: str):
    return [f.rule_id for f in scan_text(text)]


@pytest.mark.parametrize(
    "text,rule",
    [
        (f'TOKEN = "{FAKE_GITHUB}"\n', "github_token"),
        (f"aws_key = '{FAKE_AWS}'\n", "aws_access_key_id"),
        (f'const key = "{FAKE_GOOGLE}";\n', "google_api_key"),
        (f"KEY = '''{FAKE_PRIVATE_KEY}'''\n", "private_key"),
        (f'db_password = "{FAKE_GENERIC}"\n', "generic_secret_assignment"),
        (f'config = {{"client_secret": "{FAKE_GENERIC}"}}\n', "generic_secret_assignment"),
        (f"const apiKey = `{FAKE_GENERIC}`;\n", "generic_secret_assignment"),
        (f'url = "postgresql://app:{FAKE_DB_PASSWORD}@db.internal:5432/prod"\n', "database_url_credentials"),
    ],
)
def test_detects_common_secret_formats(text, rule):
    assert rule in rules(text)


def test_reports_correct_line_numbers():
    text = "a = 1\nb = 2\n" + f'token = "{FAKE_GITHUB}"\n'
    assert scan_text(text) == [SecretFinding(rule_id="github_token", line=3)]


@pytest.mark.parametrize(
    "text",
    [
        'password = os.environ["DB_PASSWORD"]\n',
        "api_key = settings.GEMINI_API_KEY\n",
        'SECRET_KEY = "replace-with-a-very-secure-random-secret-key"\n',
        'INSECURE_DEFAULT_SECRET_KEY = "default-insecure-development-secret-key-32-chars-minimum"\n',
        'password = "your-password-here"\n',
        'api_key = "<YOUR_API_KEY>"\n',
        'token = "${GITHUB_TOKEN}"\n',
        'client_secret = "test-client-secret"\n',
        'password = "aaaaaaaaaaaaaaaa"\n',
        'password_field = "password_confirmation"\n',
        'DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/codelens_ai"\n',
        'url = f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{db}"\n',
        "def verify_password(password: str, hashed: str) -> bool:\n    return check(password, hashed)\n",
        'headers = {"Authorization": f"Bearer {access_token}"}\n',
    ],
)
def test_ignores_placeholders_references_and_ordinary_code(text):
    assert scan_text(text) == []


def test_findings_never_contain_secret_values():
    text = f'token = "{FAKE_GITHUB}"\npassword = "{FAKE_GENERIC}"\n'
    findings = scan_text(text)
    assert findings
    rendered = repr(findings) + str(findings)
    assert FAKE_GITHUB not in rendered
    assert FAKE_GENERIC not in rendered
    assert set(SecretFinding.__dataclass_fields__) == {"rule_id", "line"}


@pytest.mark.parametrize(
    "relative",
    [
        "app/core/config.py",
        "app/core/security.py",
        "app/api/v1/auth.py",
        "app/services/github_service.py",
        "app/indexing/secret_scanner.py",
        "tests/test_auth.py",
        "tests/test_secret_scanner.py",
    ],
)
def test_no_false_positives_on_this_projects_own_code(relative):
    text = (BACKEND / relative).read_text(encoding="utf-8")
    assert scan_text(text) == [], relative
