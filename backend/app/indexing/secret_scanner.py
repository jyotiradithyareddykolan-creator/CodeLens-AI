"""Detect likely credentials in source files so they are never indexed or sent to an LLM.

Policy: a file with any finding is excluded from indexing entirely.

Findings carry only the rule ID and line number. Matched values are never stored,
returned, logged or included in exception messages; ``SecretFinding`` deliberately
has no field for them.

Rules favour precision for well-known token formats and use a guarded heuristic
(keyword + quoted literal + entropy + placeholder filtering) for generic secrets.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Pattern, Tuple

MAX_FINDINGS = 20
# Only scan this much of a file; files are already limited by MAX_FILE_SIZE_BYTES.
MAX_SCAN_CHARS = 2_000_000


@dataclass(frozen=True)
class SecretFinding:
    rule_id: str
    line: int


# (rule_id, compiled pattern). Patterns match the token format, not surrounding text.
TOKEN_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[0-9A-Za-z\-]{10,250}\b")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/T[0-9A-Za-z]+/B[0-9A-Za-z]+/[0-9A-Za-z]{20,}")),
    ("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{32,}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}T3BlbkFJ[A-Za-z0-9_\-]{20,}\b")),
    ("npm_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("sendgrid_api_key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")),
    (
        "database_url_credentials",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp)(?:\+\w+)?://"
            r"([^\s:/@'\"]+):([^\s@'\"]{6,})@[^\s'\"]+"
        ),
    ),
)

# Well-known local-development defaults in connection strings (not real credentials).
DEFAULT_DB_PASSWORDS = frozenset({"postgres", "password", "root", "admin", "guest", "pass", "secret", "mysql"})

SECRET_NAME = r"[A-Za-z0-9_]*(?:password|passwd|pwd|secret|api_?key|apikey|access_?key|auth_?token|access_?token|private_?key|client_?secret|signing_?key)[A-Za-z0-9_]*"
GENERIC_ASSIGNMENT = re.compile(
    rf"""(?ix)
    \b({SECRET_NAME})\b["']?      # identifier, optionally a quoted dict key
    \s*(?::|=|:=|=>)\s*           # assignment / key-value separator
    (?:[rbuf]{{0,2}})?            # optional Python string prefix
    (["'`])([^"'`\s]{{12,256}})\2 # quoted literal value
    """
)

PLACEHOLDER_HINTS = (
    "example", "your", "xxx", "changeme", "change_me", "change-me", "placeholder",
    "dummy", "sample", "fake", "test", "mock", "replace", "insecure", "default",
    "redacted", "todo", "none", "null", "secret", "password",
)
PLACEHOLDER_CHARS = ("<", ">", "${", "{{", "}}", "%s", "%(", "****", "....", "process.env", "os.environ")


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _is_placeholder(value: str) -> bool:
    lower = value.lower()
    if any(hint in lower for hint in PLACEHOLDER_HINTS):
        return True
    if any(chars in value for chars in PLACEHOLDER_CHARS):
        return True
    # Word-like phrases ("my-local-dev-value") rather than random tokens.
    if re.fullmatch(r"[a-z]+(?:[-_. ][a-z0-9]+)+", lower):
        return True
    # Repeated characters ("aaaaaaaaaaaa", "000000000000").
    if len(set(value)) <= 3:
        return True
    return False


def _is_default_db_credential(user: str, password: str) -> bool:
    """Templated (``{pwd}``, ``$PASS``), placeholder or well-known default passwords."""
    if "{" in password or "$" in password or "%" in password:
        return True
    return (
        password.lower() == user.lower()
        or password.lower() in DEFAULT_DB_PASSWORDS
        or _is_placeholder(password)
    )


def _looks_like_secret_value(value: str) -> bool:
    if _is_placeholder(value):
        return False
    classes = sum(
        bool(re.search(pattern, value)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    return classes >= 2 and _shannon_entropy(value) >= 3.5


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(text: str) -> List[SecretFinding]:
    """Return findings (rule ID + 1-based line) for likely secrets in ``text``."""
    text = text[:MAX_SCAN_CHARS]
    findings: List[SecretFinding] = []
    seen = set()

    def add(rule_id: str, offset: int) -> None:
        key = (rule_id, _line_of(text, offset))
        if key not in seen:
            seen.add(key)
            findings.append(SecretFinding(rule_id=key[0], line=key[1]))

    for rule_id, pattern in TOKEN_RULES:
        for match in pattern.finditer(text):
            if rule_id == "database_url_credentials" and _is_default_db_credential(
                match.group(1), match.group(2)
            ):
                continue
            add(rule_id, match.start())
            if len(findings) >= MAX_FINDINGS:
                return findings

    for match in GENERIC_ASSIGNMENT.finditer(text):
        if _looks_like_secret_value(match.group(3)):
            add("generic_secret_assignment", match.start())
            if len(findings) >= MAX_FINDINGS:
                break

    findings.sort(key=lambda f: (f.line, f.rule_id))
    return findings


def first_finding(text: str) -> Optional[SecretFinding]:
    findings = scan_text(text)
    return findings[0] if findings else None
