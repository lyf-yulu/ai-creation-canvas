from __future__ import annotations

import hashlib
import hmac
from collections.abc import MutableMapping
from urllib.parse import quote

import pytest

from ai_creation_canvas.adapters.portal.identity import AuthRequired, verify_portal_identity
from ai_creation_canvas.config import Settings


def signed_headers(
    user_id: str = "u-a",
    username: str = "Alice Example",
    role: str = "user",
    secret: str = "test-secret",
    now: int = 1000,
) -> dict[str, str]:
    payload = f"v2\n{now}\n{user_id}\n{role}\n{quote(username, safe='')}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Portal-Sig-Version": "2",
        "X-Portal-Timestamp": str(now),
        "X-Portal-User-Id": user_id,
        "X-Portal-Username": username,
        "X-Portal-Role": role,
        "X-Portal-Signature": signature,
    }


def test_signature_binds_user_id_username_and_role():
    headers = signed_headers()
    assert verify_portal_identity(headers, "test-secret", now=1000).user_id == "u-a"

    for header, replacement in (
        ("X-Portal-User-Id", "u-b"),
        ("X-Portal-Username", "Mallory"),
        ("X-Portal-Role", "admin"),
    ):
        tampered = dict(headers)
        tampered[header] = replacement
        with pytest.raises(AuthRequired):
            verify_portal_identity(tampered, "test-secret", now=1000)


def test_signature_payload_uses_rfc3986_utf8_encoding_for_unicode_and_spaces():
    headers = signed_headers(username="艾丽斯 a/b")
    assert verify_portal_identity(headers, "test-secret", now=1000).username == "艾丽斯 a/b"


def test_identity_rejects_newline_separator_injection_even_when_signed():
    with pytest.raises(AuthRequired):
        verify_portal_identity(signed_headers(username="alice\nadmin"), "test-secret", now=1000)


@pytest.mark.parametrize("timestamp", ["+1000", " 1000", "1000 ", "1.0", "1e3", "", "9" * 21])
def test_timestamp_must_be_a_short_canonical_decimal_integer(timestamp: str):
    headers = signed_headers()
    headers["X-Portal-Timestamp"] = timestamp
    with pytest.raises(AuthRequired):
        verify_portal_identity(headers, "test-secret", now=1000)


@pytest.mark.parametrize("now", [940, 1060])
def test_timestamp_window_includes_exact_sixty_second_boundaries(now: int):
    assert verify_portal_identity(signed_headers(now=1000), "test-secret", now=now).user_id == "u-a"


@pytest.mark.parametrize("now", [939, 1061])
def test_timestamp_window_rejects_expired_or_future_signatures(now: int):
    with pytest.raises(AuthRequired):
        verify_portal_identity(signed_headers(now=1000), "test-secret", now=now)


def test_header_names_are_case_insensitive_for_plain_mappings():
    lower_headers = {key.lower(): value for key, value in signed_headers().items()}
    assert verify_portal_identity(lower_headers, "test-secret", now=1000).username == "Alice Example"


@pytest.mark.parametrize("field,value", [("X-Portal-User-Id", ""), ("X-Portal-Username", " "), ("X-Portal-User-Id", "a\nadmin")])
def test_identity_fields_must_be_nonempty_and_not_contain_controls(field: str, value: str):
    headers = signed_headers()
    headers[field] = value
    with pytest.raises(AuthRequired):
        verify_portal_identity(headers, "test-secret", now=1000)


@pytest.mark.parametrize("secret", ["", "default", "changeme"])
def test_empty_or_default_identity_secret_is_rejected(secret: str):
    with pytest.raises(ValueError, match="PORTAL_INTERNAL_TOKEN"):
        verify_portal_identity(signed_headers(), secret, now=1000)


def test_test_mode_rejects_production_port_and_production_data_directory(tmp_path):
    with pytest.raises(ValueError, match="production port"):
        Settings(environment="test", port=9090, data_dir=tmp_path, portal_internal_token="test-secret")
    with pytest.raises(ValueError, match="production repository"):
        Settings(
            environment="test",
            port=8992,
            data_dir="/Users/260413a/ai-generation-portable-apps/../ai-generation-portable-apps/state",
            portal_internal_token="test-secret",
        )


def test_production_also_rejects_empty_portal_token(tmp_path):
    with pytest.raises(ValueError, match="PORTAL_INTERNAL_TOKEN"):
        Settings(environment="production", port=8991, data_dir=tmp_path, portal_internal_token="")
