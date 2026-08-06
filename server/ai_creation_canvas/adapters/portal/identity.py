"""Verification of the Portal's version 2 signed identity headers."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
import unicodedata
from collections.abc import Mapping
from urllib.parse import quote

from ai_creation_canvas.config import _validate_token
from ai_creation_canvas.domain.models import PortalRole, PortalUser
from ai_creation_canvas.errors import ApiError, DomainError


_TIMESTAMP = re.compile(r"0|[1-9][0-9]{0,19}\Z")
_HEADER_NAMES = (
    "x-portal-sig-version",
    "x-portal-timestamp",
    "x-portal-user-id",
    "x-portal-username",
    "x-portal-role",
    "x-portal-signature",
)
_MAX_FIELD_LENGTH = 128


class AuthRequired(DomainError):
    """Raised for any invalid or absent signed Portal identity."""

    def __init__(self, request_id: str = "identity") -> None:
        super().__init__(
            ApiError(
                code="AUTH_REQUIRED",
                message="Sign in is required.",
                retryable=False,
                request_id=request_id,
                phase="authentication",
            )
        )


def _normalise_headers(headers: Mapping[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() in _HEADER_NAMES and isinstance(value, str):
            key = name.lower()
            if key in values and values[key] != value:
                raise AuthRequired()
            values[key] = value
    return values


def _safe_identity_text(value: str) -> bool:
    return bool(value and value.strip() and len(value) <= _MAX_FIELD_LENGTH and not any(unicodedata.category(char).startswith("C") for char in value))


def _signature_payload(timestamp: str, user_id: str, role: str, username: str) -> bytes:
    return f"v2\n{timestamp}\n{user_id}\n{role}\n{quote(username, safe='')}".encode("utf-8")


def verify_portal_identity(
    headers: Mapping[str, str],
    secret: str,
    *,
    now: int | float | None = None,
    max_age_seconds: int = 60,
) -> PortalUser:
    """Return a verified identity, never exposing which check failed to callers."""
    _validate_token(secret)
    if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool) or max_age_seconds < 1:
        raise ValueError("max_age_seconds must be positive")
    values = _normalise_headers(headers)
    version = values.get("x-portal-sig-version", "")
    timestamp = values.get("x-portal-timestamp", "")
    user_id = values.get("x-portal-user-id", "")
    username = values.get("x-portal-username", "")
    role = values.get("x-portal-role", "")
    signature = values.get("x-portal-signature", "")
    if (
        version != "2"
        or not _TIMESTAMP.fullmatch(timestamp)
        or not _safe_identity_text(user_id)
        or not _safe_identity_text(username)
        or role not in {member.value for member in PortalRole}
        or not re.fullmatch(r"[0-9a-fA-F]{64}", signature)
    ):
        raise AuthRequired()
    current_time = time.time() if now is None else now
    if (
        not isinstance(current_time, (int, float))
        or isinstance(current_time, bool)
        or not math.isfinite(current_time)
        or abs(current_time - int(timestamp)) > max_age_seconds
    ):
        raise AuthRequired()
    expected = hmac.new(secret.encode("utf-8"), _signature_payload(timestamp, user_id, role, username), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AuthRequired()
    try:
        return PortalUser(user_id=user_id, username=username, role=role)
    except ValueError as error:
        raise AuthRequired() from error
