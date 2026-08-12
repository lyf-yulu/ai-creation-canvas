"""Cross-request execution permits; SQL remains the idempotency authority."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import secrets
from typing import AsyncIterator, Protocol


class ExecutionCapacityExceeded(RuntimeError):
    pass


class CoordinationUnavailable(RuntimeError):
    pass


class ExecutionCoordinator(Protocol):
    def acquire(self, job_id: str, user_id: str, provider_id: str, model_id: str): ...


class LocalExecutionCoordinator:
    def __init__(self, *, global_limit: int, provider_limit: int, user_limit: int) -> None:
        _limits(global_limit, provider_limit, user_limit)
        self._limits = (global_limit, provider_limit, user_limit)
        import asyncio
        self._lock = asyncio.Lock()
        self._global = 0
        self._providers: dict[str, int] = {}
        self._users: dict[str, int] = {}

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


class RedisExecutionCoordinator:
    def __init__(self, client, *, namespace: str, global_limit: int, provider_limit: int, user_limit: int, lease_seconds: int = 300) -> None:
        _limits(global_limit, provider_limit, user_limit)
        if not isinstance(namespace, str) or not namespace or len(namespace) > 64 or type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("Redis coordinator settings are invalid")
        self._client, self._namespace = client, namespace
        self._limits, self._lease_ms = (global_limit, provider_limit, user_limit), lease_seconds * 1000

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

    def _keys(self, job_id: str, user_id: str, provider_id: str, model_id: str) -> tuple[str, str, str, str]:
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        prefix = self._namespace
        return (
            f"{prefix}:lease:{digest(job_id)}:{digest(model_id)}",
            f"{prefix}:count:global",
            f"{prefix}:count:provider:{digest(provider_id)}",
            f"{prefix}:count:user:{digest(user_id)}",
        )


def _limits(*values: int) -> None:
    if any(type(value) is not int or not 1 <= value <= 1024 for value in values):
        raise ValueError("execution limits are invalid")


def _decrement(values: dict[str, int], key: str) -> None:
    current = values.get(key, 0)
    if current <= 1:
        values.pop(key, None)
    else:
        values[key] = current - 1
