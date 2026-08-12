"""Cross-request execution permits; SQL remains the idempotency authority."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from ai_creation_canvas.routing import RouteCandidate


class ExecutionCapacityExceeded(RuntimeError):
    pass


class CoordinationUnavailable(RuntimeError):
    pass


class ExecutionCoordinator(Protocol):
    def acquire(self, job_id: str, user_id: str, provider_id: str, model_id: str): ...

    def acquire_credential(self, job_id: str, user_id: str, candidate: RouteCandidate): ...


@dataclass(frozen=True, slots=True, repr=False)
class CredentialLease:
    route_id: str
    pool_id: str
    key_id: str
    secret: str
    key_fingerprint: str
    owner_token: str

    def __repr__(self) -> str:
        return (
            "CredentialLease("
            f"route_id={self.route_id!r}, pool_id={self.pool_id!r}, "
            f"key_id={self.key_id!r}, key_fingerprint={self.key_fingerprint!r})"
        )


class LocalExecutionCoordinator:
    def __init__(self, *, global_limit: int, provider_limit: int, user_limit: int) -> None:
        _limits(global_limit, provider_limit, user_limit)
        self._limits = (global_limit, provider_limit, user_limit)
        import asyncio
        self._lock = asyncio.Lock()
        self._global = 0
        self._providers: dict[str, int] = {}
        self._users: dict[str, int] = {}
        self._routes: dict[str, int] = {}
        self._pools: dict[str, int] = {}
        self._keys: dict[tuple[str, str], int] = {}

    @asynccontextmanager
    async def acquire(self, job_id: str, user_id: str, provider_id: str, model_id: str) -> AsyncIterator[None]:
        del job_id, model_id
        global_limit, provider_limit, user_limit = self._limits
        async with self._lock:
            if self._global >= global_limit or self._providers.get(provider_id, 0) >= provider_limit or self._users.get(user_id, 0) >= user_limit:
                raise ExecutionCapacityExceeded("generation capacity is exhausted")
            self._global += 1
            self._providers[provider_id] = self._providers.get(provider_id, 0) + 1
            self._users[user_id] = self._users.get(user_id, 0) + 1
        try:
            yield
        finally:
            async with self._lock:
                self._global -= 1
                _decrement(self._providers, provider_id)
                _decrement(self._users, user_id)

    @asynccontextmanager
    async def acquire_credential(self, job_id: str, user_id: str, candidate: RouteCandidate) -> AsyncIterator[CredentialLease]:
        del job_id
        if not isinstance(candidate, RouteCandidate):
            raise ValueError("route candidate is required")
        route, pool = candidate.route, candidate.pool
        global_limit, provider_limit, user_limit = self._limits
        pool_limit = sum(key.max_concurrency for key in pool.keys)
        ordered_keys = tuple(sorted(pool.keys, key=lambda item: item.key_id))
        async with self._lock:
            available = [
                key for key in ordered_keys
                if self._keys.get((pool.pool_id, key.key_id), 0) < key.max_concurrency
            ]
            exhausted = (
                self._global >= global_limit
                or self._providers.get(route.provider_id, 0) >= provider_limit
                or self._users.get(user_id, 0) >= user_limit
                or self._routes.get(route.route_id, 0) >= route.max_concurrency
                or self._pools.get(pool.pool_id, 0) >= pool_limit
                or not available
            )
            if exhausted:
                raise ExecutionCapacityExceeded("generation capacity is exhausted")
            selected = min(
                available,
                key=lambda item: (self._keys.get((pool.pool_id, item.key_id), 0), item.key_id),
            )
            self._global += 1
            _increment(self._providers, route.provider_id)
            _increment(self._users, user_id)
            _increment(self._routes, route.route_id)
            _increment(self._pools, pool.pool_id)
            _increment(self._keys, (pool.pool_id, selected.key_id))
        lease = CredentialLease(
            route_id=route.route_id,
            pool_id=pool.pool_id,
            key_id=selected.key_id,
            secret=selected.secret,
            key_fingerprint=hashlib.sha256(selected.secret.encode("utf-8")).hexdigest(),
            owner_token=secrets.token_hex(16),
        )
        try:
            yield lease
        finally:
            async with self._lock:
                self._global -= 1
                _decrement(self._providers, route.provider_id)
                _decrement(self._users, user_id)
                _decrement(self._routes, route.route_id)
                _decrement(self._pools, pool.pool_id)
                _decrement(self._keys, (pool.pool_id, selected.key_id))


_ACQUIRE = """
local owner = KEYS[1]
if redis.call('EXISTS', owner) == 1 then return 0 end
local g = tonumber(redis.call('GET', KEYS[2]) or '0')
local p = tonumber(redis.call('GET', KEYS[3]) or '0')
local u = tonumber(redis.call('GET', KEYS[4]) or '0')
if g >= tonumber(ARGV[3]) or p >= tonumber(ARGV[4]) or u >= tonumber(ARGV[5]) then return 0 end
if redis.call('SET', owner, ARGV[1], 'NX', 'PX', ARGV[2]) == false then return 0 end
for i=2,4 do redis.call('INCR', KEYS[i]); redis.call('PEXPIRE', KEYS[i], ARGV[2]) end
return 1
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
for i=2,4 do
  local value = tonumber(redis.call('GET', KEYS[i]) or '0')
  if value <= 1 then redis.call('DEL', KEYS[i]) else redis.call('DECR', KEYS[i]) end
end
return 1
"""


_CREDENTIAL_ACQUIRE = """-- credential-acquire-v1
local owner = KEYS[1]
local token = ARGV[1]
local ttl = tonumber(ARGV[2])
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
local expires = now + ttl
local scope_ttl = ttl + 60000
local fixed_limit_count = 5
local candidate_count = tonumber(ARGV[8])

if redis.call('EXISTS', owner) == 1 then return 0 end
for i=2,#KEYS do redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now) end
for i=1,fixed_limit_count do
  if redis.call('ZCARD', KEYS[i + 1]) >= tonumber(ARGV[i + 2]) then return 0 end
end

local selected = 0
local selected_count = nil
for i=1,candidate_count do
  local count = redis.call('ZCARD', KEYS[6 + i])
  local limit = tonumber(ARGV[8 + i])
  if count < limit and (selected_count == nil or count < selected_count) then
    selected = i
    selected_count = count
  end
end
if selected == 0 then return 0 end
if redis.call('SET', owner, token, 'NX', 'PX', ttl) == false then return 0 end
for i=2,6 do
  redis.call('ZADD', KEYS[i], expires, token)
  redis.call('PEXPIRE', KEYS[i], scope_ttl)
end
redis.call('ZADD', KEYS[6 + selected], expires, token)
redis.call('PEXPIRE', KEYS[6 + selected], scope_ttl)
return selected
"""


_CREDENTIAL_RELEASE = """-- credential-release-v1
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
for i=2,#KEYS do redis.call('ZREM', KEYS[i], ARGV[1]) end
return 1
"""


class RedisExecutionCoordinator:
    def __init__(
        self,
        client,
        *,
        namespace: str,
        global_limit: int,
        provider_limit: int,
        user_limit: int,
        lease_seconds: int = 300,
        credential_hmac_key: bytes | None = None,
    ) -> None:
        _limits(global_limit, provider_limit, user_limit)
        if not isinstance(namespace, str) or not namespace or len(namespace) > 64 or type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("Redis coordinator settings are invalid")
        if credential_hmac_key is not None and (not isinstance(credential_hmac_key, bytes) or not credential_hmac_key):
            raise ValueError("credential HMAC key must be non-empty bytes")
        self._client, self._namespace = client, namespace
        self._limits, self._lease_ms = (global_limit, provider_limit, user_limit), lease_seconds * 1000
        self._credential_hmac_key = credential_hmac_key

    async def healthcheck(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception as error:
            raise CoordinationUnavailable("Redis is unavailable") from error

    @asynccontextmanager
    async def acquire(self, job_id: str, user_id: str, provider_id: str, model_id: str) -> AsyncIterator[None]:
        token = secrets.token_hex(16)
        keys = self._keys(job_id, user_id, provider_id, model_id)
        try:
            acquired = await self._client.eval(_ACQUIRE, 4, *keys, token, self._lease_ms, *self._limits)
        except Exception as error:
            raise CoordinationUnavailable("Redis is unavailable") from error
        if acquired != 1:
            raise ExecutionCapacityExceeded("generation capacity is exhausted")
        try:
            yield
        finally:
            try:
                await self._client.eval(_RELEASE, 4, *keys, token)
            except Exception:
                # Every permit has a bounded TTL; a release outage cannot create an unbounded lock.
                pass

    @asynccontextmanager
    async def acquire_credential(self, job_id: str, user_id: str, candidate: RouteCandidate) -> AsyncIterator[CredentialLease]:
        if not isinstance(candidate, RouteCandidate):
            raise ValueError("route candidate is required")
        if self._credential_hmac_key is None:
            raise CoordinationUnavailable("credential HMAC key is unavailable")
        route, pool = candidate.route, candidate.pool
        ordered_keys = tuple(sorted(pool.keys, key=lambda item: item.key_id))
        owner_token = secrets.token_hex(16)
        opaque = self._credential_opaque
        owner_key = f"{self._namespace}:cl:o:{opaque('owner', job_id, route.route_id)}"
        scope_keys = (
            f"{self._namespace}:cl:g:{opaque('global')}",
            f"{self._namespace}:cl:p:{opaque('provider', route.provider_id)}",
            f"{self._namespace}:cl:r:{opaque('route', route.route_id)}",
            f"{self._namespace}:cl:u:{opaque('user', user_id)}",
            f"{self._namespace}:cl:l:{opaque('pool', pool.pool_id)}",
        )
        key_scopes = tuple(
            f"{self._namespace}:cl:k:{opaque('credential', pool.pool_id, item.key_id)}"
            for item in ordered_keys
        )
        keys = (owner_key, *scope_keys, *key_scopes)
        pool_limit = sum(item.max_concurrency for item in ordered_keys)
        limits = (
            self._limits[0], self._limits[1], route.max_concurrency,
            self._limits[2], pool_limit,
        )
        try:
            selected_index = await self._client.eval(
                _CREDENTIAL_ACQUIRE,
                len(keys),
                *keys,
                owner_token,
                self._lease_ms,
                *limits,
                len(ordered_keys),
                *(item.max_concurrency for item in ordered_keys),
            )
        except Exception as error:
            raise CoordinationUnavailable("Redis is unavailable") from error
        if not isinstance(selected_index, int) or not 1 <= selected_index <= len(ordered_keys):
            raise ExecutionCapacityExceeded("generation capacity is exhausted")
        selected = ordered_keys[selected_index - 1]
        lease = CredentialLease(
            route_id=route.route_id,
            pool_id=pool.pool_id,
            key_id=selected.key_id,
            secret=selected.secret,
            key_fingerprint=opaque("fingerprint", selected.secret),
            owner_token=owner_token,
        )
        try:
            yield lease
        finally:
            try:
                await self._client.eval(_CREDENTIAL_RELEASE, len(keys), *keys, owner_token)
            except Exception:
                # The owner key and membership entries expire; never release without token comparison.
                pass

    def _keys(self, job_id: str, user_id: str, provider_id: str, model_id: str) -> tuple[str, str, str, str]:
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        prefix = self._namespace
        return (
            f"{prefix}:lease:{digest(job_id)}:{digest(model_id)}",
            f"{prefix}:count:global",
            f"{prefix}:count:provider:{digest(provider_id)}",
            f"{prefix}:count:user:{digest(user_id)}",
        )

    def _credential_opaque(self, *parts: str) -> str:
        assert self._credential_hmac_key is not None
        payload = b"\0".join(part.encode("utf-8") for part in parts)
        return hmac.new(self._credential_hmac_key, payload, hashlib.sha256).hexdigest()


def _limits(*values: int) -> None:
    if any(type(value) is not int or not 1 <= value <= 1024 for value in values):
        raise ValueError("execution limits are invalid")


def _increment(values: dict, key: object) -> None:
    values[key] = values.get(key, 0) + 1


def _decrement(values: dict, key: object) -> None:
    current = values.get(key, 0)
    if current <= 1:
        values.pop(key, None)
    else:
        values[key] = current - 1
