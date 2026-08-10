from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from ai_creation_canvas.adapters.portal.client import CrossLoopLimiter, PortalClient, PortalStream
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


def test_client_shares_its_concurrency_budget_across_asyncio_run_threads():
    active = 0
    maximum = 0
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        entered.set()
        await asyncio.to_thread(release.wait)
        with lock:
            active -= 1
        return httpx.Response(200, json={})

    client = PortalClient(
        "https://portal.test", allowed_mounts=("/image-service",), max_concurrency=1,
        transport=httpx.MockTransport(handler),
    )

    def run_request() -> None:
        try:
            asyncio.run(client.request(context_for(), "GET", "api/config", mount="/image-service"))
        except BaseException as error:  # Test captures cross-loop binding failures.
            errors.append(error)

    first = threading.Thread(target=run_request)
    second = threading.Thread(target=run_request)
    first.start()
    assert entered.wait(1)
    second.start()
    release.set()
    first.join(1)
    second.join(1)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert maximum == 1


@pytest.mark.anyio
async def test_cancelled_waiter_does_not_leak_a_cross_loop_limiter_permit():
    limiter = CrossLoopLimiter(1)
    first_release = await limiter.acquire()
    waiter = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    first_release()
    second_release = await asyncio.wait_for(limiter.acquire(), timeout=0.2)
    second_release()
    second_release()


@pytest.mark.anyio
async def test_cancelled_open_stream_releases_its_permit_for_the_next_request():
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await finish.wait()
        return httpx.Response(200, content=b"ok")

    client = PortalClient(
        "https://portal.test", allowed_mounts=("/image-service",), max_concurrency=1,
        transport=httpx.MockTransport(handler),
    )
    opening = asyncio.create_task(client.open_stream(context_for(), "GET", "api/results/a", mount="/image-service"))
    await entered.wait()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    finish.set()
    response = await asyncio.wait_for(client.request(context_for(), "GET", "api/config", mount="/image-service"), timeout=0.2)
    assert response.status_code == 200


@pytest.mark.anyio
async def test_stream_close_exception_still_releases_its_idempotent_permit():
    limiter = CrossLoopLimiter(1)
    release = await limiter.acquire()

    class BrokenResponse:
        status_code = 200
        headers = {}
        async def aiter_raw(self):
            if False:
                yield b""
        async def aclose(self):
            raise RuntimeError("close failed")

    class Client:
        async def aclose(self):
            pass

    stream = PortalStream(BrokenResponse(), Client(), release)
    with pytest.raises(RuntimeError, match="close failed"):
        await stream.aclose()
    next_release = await asyncio.wait_for(limiter.acquire(), timeout=0.2)
    next_release()
