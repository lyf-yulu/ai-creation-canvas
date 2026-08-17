from __future__ import annotations

from datetime import datetime, timezone

from ai_creation_canvas.adapters.ark_assets import (
    openapi_v4_sign,
    tos_presigned_get_url,
    tos_sign_put,
)


FIXED_NOW = datetime(2026, 8, 17, 3, 4, 5, tzinfo=timezone.utc)
_AK, _SK = "AK-TEST", "SK-TEST-0123456789"
_TOS_AK, _TOS_SK = "TOS-AK-TEST", "TOS-SK-TEST"

# One-time offline vectors computed with the upstream reference algorithm.
_OPENAPI_SIGNATURE = "87edefa9b13165c4b46522c7e787069712adb92f5e34a1eb1a864c312886a964"
_TOS_PUT_SIGNATURE = "b78f98e60bd999c25e00d4fd34c29dbc87c5673090f03ceabdd195ac34e09d05"
_TOS_GET_SIGNATURE = "ec616c6bbceecd9b80881711241b0e66388beb47339c880492b91d1f50a4cade"


def test_openapi_v4_sign_vector_is_deterministic_with_injected_clock() -> None:
    authorization, headers = openapi_v4_sign(
        _AK, _SK, "POST", "ark.cn-beijing.volcengineapi.com", "/",
        "Action=CreateAsset&Version=2024-01-01", b'{"GroupId":"asset-grp-1"}', now=FIXED_NOW,
    )
    assert headers["X-Date"] == "20260817T030405Z"
    assert authorization == (
        f"HMAC-SHA256 Credential={_AK}/20260817/cn-beijing/ark/request, "
        "SignedHeaders=content-type;host;x-content-sha256;x-date, "
        f"Signature={_OPENAPI_SIGNATURE}"
    )
    assert _SK not in authorization and _SK not in str(headers)
    assert headers["X-Content-Sha256"] == "3b53f9a770f41bdfc52d40479d94177153a3d96cb0231a0eb43f254358a64cd1"


def test_openapi_v4_sign_is_stable_and_clock_dependent() -> None:
    first, first_headers = openapi_v4_sign(
        _AK, _SK, "POST", "ark.cn-beijing.volcengineapi.com", "/",
        "Action=CreateAsset&Version=2024-01-01", b'{"GroupId":"asset-grp-1"}', now=FIXED_NOW,
    )
    second, second_headers = openapi_v4_sign(
        _AK, _SK, "POST", "ark.cn-beijing.volcengineapi.com", "/",
        "Action=CreateAsset&Version=2024-01-01", b'{"GroupId":"asset-grp-1"}', now=FIXED_NOW,
    )
    later, _ = openapi_v4_sign(
        _AK, _SK, "POST", "ark.cn-beijing.volcengineapi.com", "/",
        "Action=CreateAsset&Version=2024-01-01", b'{"GroupId":"asset-grp-1"}',
        now=datetime(2026, 8, 17, 3, 4, 6, tzinfo=timezone.utc),
    )
    assert first == second and first_headers == second_headers
    assert later != first


def test_openapi_v4_sign_without_payload_omits_content_type() -> None:
    authorization, headers = openapi_v4_sign(
        _AK, _SK, "POST", "ark.cn-beijing.volcengineapi.com", "/",
        "Action=GetAsset&Version=2024-01-01", None, now=FIXED_NOW,
    )
    assert "Content-Type" not in headers
    assert headers["X-Content-Sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert "content-type" not in authorization.split("SignedHeaders=")[1].split(",")[0]


def test_tos_sign_put_headers_are_complete_and_secret_free() -> None:
    body = b"portrait-bytes"
    headers = tos_sign_put(
        _TOS_AK, _TOS_SK, "canvas-uploads", "cn-beijing", "refmedia/deadbeef.png",
        "image/png", body, now=FIXED_NOW,
    )
    assert set(headers) == {"Host", "Content-Type", "x-tos-content-sha256", "x-tos-date", "Authorization"}
    assert headers["Host"] == "canvas-uploads.tos-cn-beijing.volces.com"
    assert headers["x-tos-date"] == "20260817T030405Z"
    assert headers["x-tos-content-sha256"] == "33a50224057b73d0313811c353c59566281434ce673a2c337e76b97595a0889e"
    assert headers["Authorization"] == (
        f"TOS4-HMAC-SHA256 Credential={_TOS_AK}/20260817/cn-beijing/tos/request, "
        "SignedHeaders=content-type;host;x-tos-content-sha256;x-tos-date, "
        f"Signature={_TOS_PUT_SIGNATURE}"
    )
    assert _TOS_SK not in headers["Authorization"]


def test_tos_presigned_get_url_is_deterministic_and_secret_free() -> None:
    url = tos_presigned_get_url(
        _TOS_AK, _TOS_SK, "canvas-uploads", "cn-beijing", "refmedia/deadbeef.png",
        now=FIXED_NOW,
    )
    assert url == (
        "https://canvas-uploads.tos-cn-beijing.volces.com/refmedia/deadbeef.png"
        "?X-Tos-Algorithm=TOS4-HMAC-SHA256"
        "&X-Tos-Credential=TOS-AK-TEST%2F20260817%2Fcn-beijing%2Ftos%2Frequest"
        "&X-Tos-Date=20260817T030405Z"
        "&X-Tos-Expires=43200"
        "&X-Tos-SignedHeaders=host"
        f"&X-Tos-Signature={_TOS_GET_SIGNATURE}"
    )
    assert _TOS_SK not in url
