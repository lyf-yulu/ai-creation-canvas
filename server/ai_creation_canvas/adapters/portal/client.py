"""A narrow, request-scoped client for trusted Portal mounts."""

from __future__ import annotations

import asyncio
import ipaddress
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from ai_creation_canvas.domain.models import RequestContext


_MAX_COOKIE_BYTES = 8192
_MAX_RESPONSE_BYTES = 1_048_576
_DEFAULT_TIMEOUT = 10.0


@dataclass(slots=True)
class _LimiterWaiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[Callable[[], None]]
    state: str = "queued"


class CrossLoopLimiter:
    """A cancellation-safe, FIFO, event-loop-independent concurrency budget."""
    def __init__(self, maximum: int) -> None:
        self._maximum, self._in_use, self._lock = maximum, 0, threading.Lock()
        self._waiters: deque[_LimiterWaiter] = deque()

    async def acquire(self) -> Callable[[], None]:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._discard_invalid_head_locked()
            if self._in_use < self._maximum and not self._waiters:
                self._in_use += 1
                return self._new_release()
            waiter = _LimiterWaiter(loop, loop.create_future())
            self._waiters.append(waiter)
        try:
            return await waiter.future
        except asyncio.CancelledError:
            with self._lock:
                if waiter.state == "queued":
                    waiter.state = "cancelled"
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                elif waiter.state == "granted":
                    waiter.state = "cancelled"
                    self._handoff_or_free_locked()
            raise

    def _new_release(self) -> Callable[[], None]:
        released = False
        release_lock = threading.Lock()

        def release() -> None:
            nonlocal released
            with release_lock:
                if released:
                    return
                released = True
            with self._lock:
                self._handoff_or_free_locked()

        return release

    def _discard_invalid_head_locked(self) -> None:
        while self._waiters:
            waiter = self._waiters[0]
            if waiter.state == "queued" and not waiter.future.cancelled() and not waiter.loop.is_closed():
                return
            self._waiters.popleft()
            waiter.state = "cancelled"

    def _handoff_or_free_locked(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.state != "queued" or waiter.future.cancelled() or waiter.loop.is_closed():
                waiter.state = "cancelled"
                continue
            waiter.state = "granted"
            release = self._new_release()
            try:
                waiter.loop.call_soon_threadsafe(self._resolve_waiter, waiter, release)
            except RuntimeError:
                waiter.state = "cancelled"
                continue
            return
        if self._in_use <= 0:
            raise RuntimeError("limiter permit underflow")
        self._in_use -= 1

    @staticmethod
    def _resolve_waiter(waiter: _LimiterWaiter, release: Callable[[], None]) -> None:
        if waiter.state != "granted" or waiter.future.cancelled():
            return
        if not waiter.future.done():
            waiter.future.set_result(release)


class PortalStream:
    """An owned streaming response; closing it also releases the client permit."""
    def __init__(self, response: httpx.Response, client: httpx.AsyncClient, release) -> None:
        self.response, self._client, self._release, self._closed = response, client, release, False
    @property
    def status_code(self): return self.response.status_code
    @property
    def headers(self): return self.response.headers
    async def aiter_bytes(self):
        async for chunk in self.response.aiter_raw(): yield chunk
    async def aclose(self):
        if not self._closed:
            self._closed = True
            try: await self.response.aclose()
            finally:
                try:
                    await self._client.aclose()
                finally:
                    self._release()


def _decoded(value: str) -> str:
    previous = value
    for _ in range(3):
        current = unquote(previous)
        if current == previous:
            return current
        previous = current
    return previous


def _safe_mount(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.endswith("/"):
        raise ValueError("mount must be an absolute, non-root path")
    if any(char in value for char in "\\?#") or _decoded(value) != value:
        raise ValueError("mount contains unsafe characters")
    parts = value.split("/")[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("mount contains unsafe path components")
    return value


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")):
        raise ValueError("path must be a non-empty relative path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in value:
        raise ValueError("path is not allowlisted: only a relative path without URL components is accepted")
    decoded = _decoded(value)
    if decoded != value or any(part in {"", ".", ".."} for part in decoded.split("/")):
        raise ValueError("path contains unsafe path components")
    return value


def _base_url(value: str, *, allow_loopback_http: bool = False) -> str:
    if not isinstance(value, str) or not value or any(ord(char) <= 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError("Portal base URL must be a string")
    if type(allow_loopback_http) is not bool:
        raise ValueError("allow_loopback_http must be a bool")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Portal base URL must have an http(s) scheme and host")
    host = parsed.hostname
    if any(ord(char) > 127 for char in host):
        raise ValueError("Portal base URL host must be ASCII")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not allow_loopback_http or not loopback:
            raise ValueError("Portal HTTP URL must be explicit loopback")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Portal base URL must not contain a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Portal base URL has an invalid port") from error
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")) if port is not None else f"{parsed.scheme}://{parsed.hostname}"


class PortalClient:
    """Build requests exclusively from a configured base URL, mount, and relative path."""

    def __init__(
        self,
        portal_base_url: str,
        *,
        allowed_mounts: Sequence[str],
        verify: bool | str | Path = True,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        allowed_methods: Sequence[str] = ("GET",),
        allow_loopback_http: bool = False,
        max_concurrency: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = _base_url(portal_base_url, allow_loopback_http=allow_loopback_http)
        self._base = urlsplit(self._base_url)
        self._allowed_mounts = frozenset(_safe_mount(mount) for mount in allowed_mounts)
        if not self._allowed_mounts:
            raise ValueError("at least one allowlisted mount is required")
        self.verify = self._validate_verify(verify)
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0 and 60")
        self._timeout = httpx.Timeout(float(timeout_seconds))
        methods = frozenset(method.upper() for method in allowed_methods if isinstance(method, str))
        if not methods or methods - {"GET", "POST", "HEAD"}:
            raise ValueError("allowed_methods contains unsupported methods")
        self._allowed_methods = methods
        self._transport = transport
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 128:
            raise ValueError("max_concurrency must be a finite positive integer")
        self._semaphore = CrossLoopLimiter(max_concurrency)

    @staticmethod
    def _validate_verify(verify: bool | str | Path) -> bool | str:
        if verify is True:
            return True
        if verify is False:
            raise ValueError("TLS verification cannot be disabled")
        if isinstance(verify, (str, Path)):
            ca_file = Path(verify).expanduser().resolve(strict=False)
            if not ca_file.is_file():
                raise ValueError("TLS CA file must be an existing file")
            return str(ca_file)
        raise ValueError("verify must be True or an explicit CA file")

    def _target(self, mount: str, path: str) -> str:
        safe_mount = _safe_mount(mount)
        if safe_mount not in self._allowed_mounts:
            raise ValueError("mount is not allowlisted")
        safe_path = _safe_path(path)
        target = urlsplit(f"{self._base_url}{safe_mount}/{safe_path}")
        if (
            target.scheme != self._base.scheme
            or target.hostname != self._base.hostname
            or target.port != self._base.port
            or target.username
            or target.password
            or target.fragment
            or not target.path.startswith(f"{safe_mount}/")
        ):
            raise ValueError("target is not allowlisted")
        return urlunsplit((target.scheme, target.netloc, target.path, "", ""))

    @staticmethod
    def _cookie_header(value: str | None) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > _MAX_COOKIE_BYTES:
            raise ValueError("Cookie header is invalid")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Cookie header is invalid")
        return {"Cookie": value}

    async def request(
        self,
        context: RequestContext,
        method: str,
        path: str,
        *,
        mount: str,
        params: Mapping[str, str | int | float | bool] | None = None,
        json: Mapping[str, Any] | None = None,
        cookie_header: str | None = None,
    ) -> httpx.Response:
        if not isinstance(context, RequestContext):
            raise ValueError("context must be a RequestContext")
        verb = method.upper() if isinstance(method, str) else ""
        if verb not in self._allowed_methods:
            raise ValueError("method is not allowed")
        target = self._target(mount, path)
        headers = self._cookie_header(cookie_header)
        release = await self._semaphore.acquire()
        try:
            async with httpx.AsyncClient(
                verify=self.verify, timeout=self._timeout, follow_redirects=False,
                transport=self._transport, headers={}, trust_env=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client:
                request = client.build_request(verb, target, params=params, json=json, headers=headers)
                response = await client.send(request, stream=True)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        await response.aclose()
                        raise ValueError("Portal response exceeds the maximum size")
                return httpx.Response(response.status_code, headers=response.headers, content=bytes(body), request=request)
        finally:
            release()

    async def open_stream(self, context: RequestContext, method: str, path: str, *, mount: str, cookie_header: str | None = None, headers: Mapping[str, str] | None = None) -> PortalStream:
        if not isinstance(context, RequestContext): raise ValueError("context must be a RequestContext")
        verb = method.upper() if isinstance(method, str) else ""
        if verb not in self._allowed_methods: raise ValueError("method is not allowed")
        target = self._target(mount, path)
        merged = self._cookie_header(cookie_header)
        merged["Accept-Encoding"] = "identity"
        if headers: merged.update(headers)
        release = await self._semaphore.acquire()
        client = httpx.AsyncClient(verify=self.verify, timeout=self._timeout, follow_redirects=False, transport=self._transport, headers={}, trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        try:
            response = await client.send(client.build_request(verb, target, headers=merged), stream=True)
            return PortalStream(response, client, release)
        except BaseException:
            try:
                await client.aclose()
            finally:
                release()
            raise
