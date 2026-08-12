from __future__ import annotations

import asyncio
from dataclasses import replace
import json

import pytest

from ai_creation_canvas.coordination import (
    CoordinationUnavailable,
    ExecutionCapacityExceeded,
    LocalExecutionCoordinator,
    RedisExecutionCoordinator,
)
from ai_creation_canvas.credential_pools import CredentialKey, CredentialPool
from ai_creation_canvas.domain.models import ModelInputPort, ModelOperation
from ai_creation_canvas.model_registry import OperationContract
from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition
from ai_creation_canvas.routing import RouteCandidate, RouteSelector


def _contract() -> OperationContract:
    return OperationContract(
        ModelOperation.IMAGE_EDIT,
        (ModelInputPort("prompt", "text", 1, 1), ModelInputPort("reference_images", "image", 1, 15)),
        "image",
        {"type": "object", "properties": {}, "additionalProperties": False},
        {},
    )


def _route(*, route_id: str = "banana-route", provider: str = "t8star", pool_id: str = "t8-gemini", route_limit: int = 20, priority: int = 1) -> ModelRouteDefinition:
    return ModelRouteDefinition(
        route_id, "nano-banana", provider, "gemini-image", "chiyun_openai_images",
        pool_id, "nano-banana", (_contract(),), priority, route_limit,
    )


def _pool(*, pool_id: str = "t8-gemini", provider: str = "t8star", group: str = "gemini", key_limits: tuple[tuple[str, int], ...] = (("key-b", 2), ("key-a", 2))) -> CredentialPool:
    return CredentialPool(
        pool_id, provider, group, ("nano-banana",),
        tuple(CredentialKey(key_id, f"api-key-{key_id}-sensitive", limit) for key_id, limit in key_limits),
        "b" * 64,
    )


def _candidate(**changes: object) -> RouteCandidate:
    route = changes.pop("route", _route())
    pool = changes.pop("pool", _pool())
    assert not changes
    return RouteCandidate(route=route, pool=pool)  # type: ignore[arg-type]


async def _assert_next_exhausted(coordinator, candidate: RouteCandidate, *, suffix: str = "blocked") -> None:
    with pytest.raises(ExecutionCapacityExceeded):
        async with coordinator.acquire_credential(f"job-{suffix}", f"user-{suffix}", candidate):
            raise AssertionError("unreachable")


def test_local_credential_leases_use_least_loaded_key_and_stable_key_id_tie_break() -> None:
    async def scenario() -> tuple[str, str, str]:
        coordinator = LocalExecutionCoordinator(global_limit=8, provider_limit=8, user_limit=8)
        candidate = _candidate()
        async with coordinator.acquire_credential("job-1", "user-1", candidate) as first:
            async with coordinator.acquire_credential("job-2", "user-2", candidate) as second:
                async with coordinator.acquire_credential("job-3", "user-3", candidate) as third:
                    assert "sensitive" not in repr(first)
                    return first.key_id, second.key_id, third.key_id

    assert asyncio.run(scenario()) == ("key-a", "key-b", "key-a")


@pytest.mark.parametrize("limited_scope", ["global", "provider", "user", "route", "pool", "key"])
def test_local_credential_leases_enforce_every_capacity_scope(limited_scope: str) -> None:
    async def scenario() -> None:
        global_limit = 1 if limited_scope == "global" else 8
        provider_limit = 1 if limited_scope == "provider" else 8
        user_limit = 1 if limited_scope == "user" else 8
        route_limit = 1 if limited_scope == "route" else 8
        key_limits = (("only-key", 1),) if limited_scope in {"pool", "key"} else (("key-a", 4), ("key-b", 4))
        candidate = _candidate(route=_route(route_limit=route_limit), pool=_pool(key_limits=key_limits))
        coordinator = LocalExecutionCoordinator(global_limit=global_limit, provider_limit=provider_limit, user_limit=user_limit)
        async with coordinator.acquire_credential("job-held", "same-user", candidate):
            user = "same-user" if limited_scope == "user" else "other-user"
            with pytest.raises(ExecutionCapacityExceeded):
                async with coordinator.acquire_credential("job-next", user, candidate):
                    raise AssertionError("unreachable")

    asyncio.run(scenario())


def test_local_credential_lease_releases_after_exception_and_cancellation() -> None:
    async def scenario() -> None:
        coordinator = LocalExecutionCoordinator(global_limit=1, provider_limit=1, user_limit=1)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 1),)))
        with pytest.raises(RuntimeError):
            async with coordinator.acquire_credential("job-error", "user", candidate):
                raise RuntimeError("fixture")
        async with coordinator.acquire_credential("job-after-error", "user", candidate):
            pass

        entered = asyncio.Event()

        async def held() -> None:
            async with coordinator.acquire_credential("job-cancel", "user", candidate):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(held())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with coordinator.acquire_credential("job-after-cancel", "user", candidate):
            pass

    asyncio.run(scenario())


def test_twenty_concurrent_local_acquisitions_never_cross_into_cc_group() -> None:
    official_pool = _pool(pool_id="official", provider="google", group="official", key_limits=(("official-key", 20),))
    gemini_pool = _pool(key_limits=(("gemini-key", 20),))
    cc_pool = CredentialPool("t8-cc", "t8star", "cc", ("claude",), (CredentialKey("cc-key", "cc-secret", 20),), "c" * 64)
    routes = (
        _route(route_id="official-route", provider="google", pool_id="official", priority=1),
        _route(route_id="gemini-route", priority=2),
        _route(route_id="cc-route", pool_id="t8-cc", priority=0),
    )
    model = LogicalModelDefinition("nano-banana", "Nano Banana", "Edit", "image", (_contract(),))
    candidates = RouteSelector().candidates(
        model, "image.edit", {}, {"prompt": ("text",), "reference_images": ("image",)},
        routes, {pool.pool_id: pool for pool in (official_pool, gemini_pool, cc_pool)},
    )

    async def scenario() -> list[str]:
        coordinator = LocalExecutionCoordinator(global_limit=32, provider_limit=32, user_limit=32)

        async def one(index: int) -> str:
            candidate = candidates[index % len(candidates)]
            async with coordinator.acquire_credential(f"job-{index}", f"user-{index}", candidate) as lease:
                await asyncio.sleep(0)
                return lease.pool_id

        return await asyncio.gather(*(one(index) for index in range(20)))

    leases = asyncio.run(scenario())
    assert all(pool_id in {"official", "t8-gemini"} for pool_id in leases)


class ScriptRedis:
    """A narrow Redis protocol fake that executes the credential script contract."""

    def __init__(self) -> None:
        self.recorded_commands: list[tuple[object, ...]] = []
        self.now_ms = 1_000_000
        self.owners: dict[str, tuple[str, int]] = {}
        self.sets: dict[str, dict[str, int]] = {}
        self.fail = False

    async def eval(self, script: str, key_count: int, *parts: object) -> int:
        self.recorded_commands.append((script, key_count, *parts))
        if self.fail:
            raise ConnectionError("fixture outage")
        keys = [str(item) for item in parts[:key_count]]
        args = parts[key_count:]
        if "credential-acquire-v1" in script:
            return self._acquire(keys, args)
        if "credential-release-v1" in script:
            return self._release(keys, args)
        raise AssertionError("unexpected script")

    def _acquire(self, keys: list[str], args: tuple[object, ...]) -> int:
        token, now, ttl = str(args[0]), int(args[1]), int(args[2])
        limits = [int(item) for item in args[3:8]]
        candidate_count = int(args[8])
        key_limits = [int(item) for item in args[9:]]
        assert len(keys) == 6 + candidate_count and len(key_limits) == candidate_count
        expires_at = now + ttl
        self.now_ms = now
        self.owners = {key: value for key, value in self.owners.items() if value[1] > now}
        for members in self.sets.values():
            for member, expiry in tuple(members.items()):
                if expiry <= now:
                    members.pop(member)
        if keys[0] in self.owners or any(len(self.sets.get(scope, {})) >= limit for scope, limit in zip(keys[1:6], limits)):
            return 0
        counts = [len(self.sets.get(key, {})) for key in keys[6:]]
        choices = [index for index, count in enumerate(counts) if count < key_limits[index]]
        if not choices:
            return 0
        selected = min(choices, key=lambda index: (counts[index], index))
        self.owners[keys[0]] = (token, expires_at)
        for scope in (*keys[1:6], keys[6 + selected]):
            self.sets.setdefault(scope, {})[token] = expires_at
        return selected + 1

    def _release(self, keys: list[str], args: tuple[object, ...]) -> int:
        token = str(args[0])
        if self.owners.get(keys[0], (None,))[0] != token:
            return 0
        self.owners.pop(keys[0], None)
        for scope in keys[1:]:
            self.sets.setdefault(scope, {}).pop(token, None)
        return 1

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds


def _redis(client: ScriptRedis, **changes: object) -> RedisExecutionCoordinator:
    values: dict[str, object] = {
        "namespace": "aicc-test",
        "global_limit": 20,
        "provider_limit": 20,
        "user_limit": 20,
        "lease_seconds": 5,
        "credential_hmac_key": b"test-only-opaque-hmac-key",
        "clock_ms": lambda: client.now_ms,
    }
    values.update(changes)
    return RedisExecutionCoordinator(client, **values)  # type: ignore[arg-type]


def test_redis_credential_acquire_is_atomic_least_used_and_releases() -> None:
    async def scenario() -> tuple[tuple[str, str, str], ScriptRedis]:
        client = ScriptRedis()
        coordinator = _redis(client)
        candidate = _candidate()
        async with coordinator.acquire_credential("job-1", "user-1", candidate) as first:
            async with coordinator.acquire_credential("job-2", "user-2", candidate) as second:
                async with coordinator.acquire_credential("job-3", "user-3", candidate) as third:
                    result = (first.key_id, second.key_id, third.key_id)
        return result, client

    result, client = asyncio.run(scenario())
    assert result == ("key-a", "key-b", "key-a")
    assert len(client.recorded_commands) == 6
    assert all("credential-acquire-v1" in str(call[0]) for call in client.recorded_commands[:3])


@pytest.mark.parametrize("limited_scope", ["global", "provider", "user", "route", "pool", "key"])
def test_redis_credential_leases_enforce_every_capacity_scope(limited_scope: str) -> None:
    async def scenario() -> None:
        client = ScriptRedis()
        route_limit = 1 if limited_scope == "route" else 8
        key_limits = (("only-key", 1),) if limited_scope in {"pool", "key"} else (("key-a", 4), ("key-b", 4))
        candidate = _candidate(route=_route(route_limit=route_limit), pool=_pool(key_limits=key_limits))
        coordinator = _redis(
            client,
            global_limit=1 if limited_scope == "global" else 8,
            provider_limit=1 if limited_scope == "provider" else 8,
            user_limit=1 if limited_scope == "user" else 8,
        )
        async with coordinator.acquire_credential("job-held", "same-user", candidate):
            user = "same-user" if limited_scope == "user" else "other-user"
            with pytest.raises(ExecutionCapacityExceeded):
                async with coordinator.acquire_credential("job-next", user, candidate):
                    raise AssertionError("unreachable")

    asyncio.run(scenario())


def test_redis_expired_lease_is_pruned_before_capacity_and_key_selection() -> None:
    async def scenario() -> tuple[str, str]:
        client = ScriptRedis()
        coordinator = _redis(client, global_limit=1, provider_limit=1, user_limit=1)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 1),)))
        abandoned = coordinator.acquire_credential("job-abandoned", "user", candidate)
        first = await abandoned.__aenter__()
        client.advance(5_001)
        async with coordinator.acquire_credential("job-recovered", "user", candidate) as second:
            result = first.key_id, second.key_id
        await abandoned.__aexit__(None, None, None)
        return result

    assert asyncio.run(scenario()) == ("key-a", "key-a")


def test_redis_release_compares_owner_token_before_removing_lease() -> None:
    async def scenario() -> ScriptRedis:
        client = ScriptRedis()
        coordinator = _redis(client)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 1),)))
        context = coordinator.acquire_credential("job", "user", candidate)
        await context.__aenter__()
        acquire_call = client.recorded_commands[0]
        key_count = int(acquire_call[1])
        owner_key = str(acquire_call[2])
        client.owners[owner_key] = ("replacement-owner", client.now_ms + 5_000)
        await context.__aexit__(None, None, None)
        return client

    client = asyncio.run(scenario())
    assert any(token == "replacement-owner" for token, _ in client.owners.values())
    assert any(members for members in client.sets.values())


def test_redis_outage_is_fail_closed_for_acquire() -> None:
    async def scenario() -> None:
        client = ScriptRedis()
        client.fail = True
        with pytest.raises(CoordinationUnavailable, match="Redis"):
            async with _redis(client).acquire_credential("job", "user", _candidate()):
                raise AssertionError("unreachable")

    asyncio.run(scenario())


def test_redis_commands_never_contain_raw_identity_group_family_prompt_or_secret() -> None:
    async def scenario() -> ScriptRedis:
        client = ScriptRedis()
        coordinator = _redis(client)
        async with coordinator.acquire_credential("job-raw", "user-raw", _candidate()) as lease:
            assert lease.secret == "api-key-key-a-sensitive"
        return client

    client = asyncio.run(scenario())
    encoded = json.dumps(client.recorded_commands)
    for forbidden in (
        "job-raw", "user-raw", "t8star", "banana-route", "t8-gemini", "key-a",
        "gemini", "nano-banana", "prompt", "api-key", "sensitive",
    ):
        assert forbidden not in encoded


def test_redis_credential_hmac_key_is_required_for_credential_leasing() -> None:
    async def scenario() -> None:
        client = ScriptRedis()
        coordinator = RedisExecutionCoordinator(
            client, namespace="aicc-test", global_limit=2, provider_limit=2,
            user_limit=2, lease_seconds=5,
        )
        with pytest.raises(CoordinationUnavailable, match="HMAC"):
            async with coordinator.acquire_credential("job", "user", _candidate()):
                raise AssertionError("unreachable")

    asyncio.run(scenario())
