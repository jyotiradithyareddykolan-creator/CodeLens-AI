import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union
from jose import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(
    subject: Union[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    """Creates a signed JWT session token for authenticated CodeLens users."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode = {"exp": expire, "sub": str(subject), "iat": datetime.now(timezone.utc)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodes and validates a session JWT token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None


def verify_github_signature(payload_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    """
    Validates GitHub webhook X-Hub-Signature-256 using HMAC SHA-256.
    Signature format: 'sha256=<hex_digest>'
    """
    if not signature_header or not secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_signature = signature_header[7:]
    if len(expected_signature) != hashlib.sha256().digest_size * 2:
        return False
    try:
        bytes.fromhex(expected_signature)
    except ValueError:
        return False

    mac = hmac.new(secret.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    computed_signature = mac.hexdigest()

    return hmac.compare_digest(computed_signature, expected_signature)


def generate_github_signature(payload_body: bytes, secret: str) -> str:
    """Helper to compute valid GitHub X-Hub-Signature-256 header for testing."""
    mac = hmac.new(secret.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"
