"""A narrow, request-scoped client for trusted Portal mounts."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from ai_creation_canvas.domain.models import RequestContext


_MAX_COOKIE_BYTES = 8192
_MAX_RESPONSE_BYTES = 1_048_576
_DEFAULT_TIMEOUT = 10.0


class PortalStream:
    """An owned streaming response; closing it also releases the client permit."""
    def __init__(self, response: httpx.Response, client: httpx.AsyncClient, release) -> None:
        self.response, self._client, self._release, self._closed = response, client, release, False
    @property
    def status_code(self): return self.response.status_code
    @property
    def headers(self): return self.response.headers
    async def aiter_bytes(self):
        async for chunk in self.response.aiter_bytes(): yield chunk
    async def aclose(self):
        if not self._closed:
            self._closed = True
            try: await self.response.aclose()
            finally:
                await self._client.aclose(); self._release()


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
        if not methods or methods - {"GET", "POST"}:
            raise ValueError("allowed_methods contains unsupported methods")
        self._allowed_methods = methods
        self._transport = transport
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 128:
            raise ValueError("max_concurrency must be a finite positive integer")
        self._semaphore = asyncio.Semaphore(max_concurrency)

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
        async with self._semaphore, httpx.AsyncClient(
            verify=self.verify,
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
            headers={},
            trust_env=False,
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

    async def open_stream(self, context: RequestContext, method: str, path: str, *, mount: str, cookie_header: str | None = None, headers: Mapping[str, str] | None = None) -> PortalStream:
        if not isinstance(context, RequestContext): raise ValueError("context must be a RequestContext")
        verb = method.upper() if isinstance(method, str) else ""
        if verb not in self._allowed_methods: raise ValueError("method is not allowed")
        target = self._target(mount, path)
        merged = self._cookie_header(cookie_header)
        if headers: merged.update(headers)
        await self._semaphore.acquire()
        client = httpx.AsyncClient(verify=self.verify, timeout=self._timeout, follow_redirects=False, transport=self._transport, headers={}, trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0))
        try:
            response = await client.send(client.build_request(verb, target, headers=merged), stream=True)
            return PortalStream(response, client, self._semaphore.release)
        except Exception:
            await client.aclose(); self._semaphore.release(); raise
