from __future__ import annotations

import asyncio

import httpx
import pytest

from ai_creation_canvas.adapters.portal.client import PortalClient
from ai_creation_canvas.domain.models import PortalUser, RequestContext


def context_for(user_id: str = "u-a") -> RequestContext:
    return RequestContext(PortalUser(user_id, "Alice", "user"), "request-1", "trace-1")


def make_client(handler) -> PortalClient:
    return PortalClient(
        "https://portal.test",
        allowed_mounts=("/image-service",),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.anyio
async def test_client_uses_only_allowlisted_relative_targets_and_no_redirects():
    client = make_client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="not allowlisted"):
        await client.request(context_for(), "GET", "https://example.invalid/api/config", mount="/image-service")
    with pytest.raises(ValueError, match="relative"):
        await client.request(context_for(), "GET", "//example.invalid/api/config", mount="/image-service")
    with pytest.raises(ValueError, match="path"):
        await client.request(context_for(), "GET", "../api/config", mount="/image-service")
    with pytest.raises(ValueError, match="mount"):
        await client.request(context_for(), "GET", "api/config", mount="/not-allowed")

    response = await client.request(context_for(), "GET", "api/config", mount="/image-service")
    assert response.status_code == 200


@pytest.mark.anyio
async def test_client_forwards_only_current_safe_cookie_and_never_crosses_concurrent_requests():
    seen: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        return httpx.Response(200, json={})

    client = make_client(handler)
    await asyncio.gather(
        client.request(context_for("u-a"), "GET", "api/config", mount="/image-service", cookie_header="session=a"),
        client.request(context_for("u-b"), "GET", "api/config", mount="/image-service", cookie_header="session=b"),
    )
    assert sorted(seen) == ["session=a", "session=b"]
    await client.request(context_for(), "GET", "api/config", mount="/image-service")
    assert seen[-1] is None
    with pytest.raises(ValueError, match="Cookie"):
        await client.request(context_for(), "GET", "api/config", mount="/image-service", cookie_header="ok\r\nbad")


@pytest.mark.anyio
async def test_client_keeps_tls_verification_and_returns_redirect_without_following_it():
    client = make_client(lambda request: httpx.Response(302, headers={"location": "https://example.invalid/"}))
    assert client.verify is True
    response = await client.request(context_for(), "GET", "api/config", mount="/image-service")
    assert response.status_code == 302


def test_client_rejects_disabled_tls_or_a_missing_ca_file(tmp_path):
    with pytest.raises(ValueError, match="cannot be disabled"):
        PortalClient("https://portal.test", allowed_mounts=("/image-service",), verify=False)
    with pytest.raises(ValueError, match="CA file"):
        PortalClient("https://portal.test", allowed_mounts=("/image-service",), verify=tmp_path / "missing.pem")
