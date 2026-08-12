from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
import json
import time

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


@pytest.mark.parametrize("limited_scope", ["global", "provider", "user", "route"])
def test_local_credential_leases_enforce_every_capacity_scope(limited_scope: str) -> None:
    async def scenario() -> None:
        global_limit = 1 if limited_scope == "global" else 8
        provider_limit = 1 if limited_scope == "provider" else 8
        user_limit = 1 if limited_scope == "user" else 8
        route_limit = 1 if limited_scope == "route" else 8
        candidate = _candidate(route=_route(route_limit=route_limit), pool=_pool(key_limits=(("key-a", 4), ("key-b", 4))))
        coordinator = LocalExecutionCoordinator(global_limit=global_limit, provider_limit=provider_limit, user_limit=user_limit)
        async with coordinator.acquire_credential("job-held", "same-user", candidate):
            user = "same-user" if limited_scope == "user" else "other-user"
            with pytest.raises(ExecutionCapacityExceeded):
                async with coordinator.acquire_credential("job-next", user, candidate):
                    raise AssertionError("unreachable")

    asyncio.run(scenario())


def test_local_per_key_limit_moves_work_to_another_key_in_the_same_pool() -> None:
    async def scenario() -> tuple[str, str, str]:
        coordinator = LocalExecutionCoordinator(global_limit=8, provider_limit=8, user_limit=8)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 1), ("key-b", 3))))
        async with coordinator.acquire_credential("job-1", "user-1", candidate) as first:
            async with coordinator.acquire_credential("job-2", "user-2", candidate) as second:
                async with coordinator.acquire_credential("job-3", "user-3", candidate) as third:
                    return first.key_id, second.key_id, third.key_id

    assert asyncio.run(scenario()) == ("key-a", "key-b", "key-b")


def test_local_multi_key_pool_exhausts_at_the_sum_of_key_limits() -> None:
    async def scenario() -> tuple[str, ...]:
        coordinator = LocalExecutionCoordinator(global_limit=8, provider_limit=8, user_limit=8)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 2), ("key-b", 2))))
        async with AsyncExitStack() as stack:
            leases = [
                await stack.enter_async_context(coordinator.acquire_credential(f"job-{index}", f"user-{index}", candidate))
                for index in range(4)
            ]
            await _assert_next_exhausted(coordinator, candidate)
            return tuple(lease.key_id for lease in leases)

    assert asyncio.run(scenario()) == ("key-a", "key-b", "key-a", "key-b")


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
        self.server_now_ms = 1_000_000
        self.owners: dict[str, tuple[str, int]] = {}
        self.sets: dict[str, dict[str, int]] = {}
        self.scope_expiries: dict[str, int] = {}
        self.fail = False

    async def eval(self, script: str, key_count: int, *parts: object) -> int:
        self.recorded_commands.append((script, key_count, *parts))
        if self.fail:
            raise ConnectionError("fixture outage")
        self._expire_redis_keys()
        keys = [str(item) for item in parts[:key_count]]
        args = parts[key_count:]
        if "credential-acquire-v1" in script:
            return self._acquire(script, keys, args)
        if "credential-release-v1" in script:
            return self._release(keys, args)
        raise AssertionError("unexpected script")

    def _acquire(self, script: str, keys: list[str], args: tuple[object, ...]) -> int:
        assert "redis.call('TIME')" in script
        assert script.count("redis.call('PEXPIRE'") >= 2
        token, ttl = str(args[0]), int(args[1])
        limits = [int(item) for item in args[2:7]]
        candidate_count = int(args[7])
        key_limits = [int(item) for item in args[8:]]
        assert len(keys) == 6 + candidate_count and len(key_limits) == candidate_count
        expires_at = self.server_now_ms + ttl
        for members in self.sets.values():
            for member, expiry in tuple(members.items()):
                if expiry <= self.server_now_ms:
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
            self.scope_expiries[scope] = self.server_now_ms + ttl + 60_000
        return selected + 1

    def _release(self, keys: list[str], args: tuple[object, ...]) -> int:
        token = str(args[0])
        if self.owners.get(keys[0], (None,))[0] != token:
            return 0
        self.owners.pop(keys[0], None)
        for scope in keys[1:]:
            members = self.sets.get(scope)
            if members is not None:
                members.pop(token, None)
                if not members:
                    self.sets.pop(scope, None)
                    self.scope_expiries.pop(scope, None)
        return 1

    def advance(self, milliseconds: int) -> None:
        self.server_now_ms += milliseconds
        self._expire_redis_keys()

    def _expire_redis_keys(self) -> None:
        self.owners = {
            key: value for key, value in self.owners.items()
            if value[1] > self.server_now_ms
        }
        for scope, expiry in tuple(self.scope_expiries.items()):
            if expiry <= self.server_now_ms:
                self.scope_expiries.pop(scope, None)
                self.sets.pop(scope, None)


def _redis(client: ScriptRedis, **changes: object) -> RedisExecutionCoordinator:
    values: dict[str, object] = {
        "namespace": "aicc-test",
        "global_limit": 20,
        "provider_limit": 20,
        "user_limit": 20,
        "lease_seconds": 5,
        "credential_hmac_key": b"test-only-opaque-hmac-key",
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


@pytest.mark.parametrize("limited_scope", ["global", "provider", "user", "route"])
def test_redis_credential_leases_enforce_every_capacity_scope(limited_scope: str) -> None:
    async def scenario() -> None:
        client = ScriptRedis()
        route_limit = 1 if limited_scope == "route" else 8
        candidate = _candidate(route=_route(route_limit=route_limit), pool=_pool(key_limits=(("key-a", 4), ("key-b", 4))))
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


def test_redis_per_key_limit_moves_work_to_another_key_in_the_same_pool() -> None:
    async def scenario() -> tuple[str, str, str]:
        client = ScriptRedis()
        coordinator = _redis(client)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 1), ("key-b", 3))))
        async with coordinator.acquire_credential("job-1", "user-1", candidate) as first:
            async with coordinator.acquire_credential("job-2", "user-2", candidate) as second:
                async with coordinator.acquire_credential("job-3", "user-3", candidate) as third:
                    return first.key_id, second.key_id, third.key_id

    assert asyncio.run(scenario()) == ("key-a", "key-b", "key-b")


def test_redis_multi_key_pool_exhausts_at_the_sum_of_key_limits() -> None:
    async def scenario() -> tuple[str, ...]:
        client = ScriptRedis()
        coordinator = _redis(client)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 2), ("key-b", 2))))
        async with AsyncExitStack() as stack:
            leases = [
                await stack.enter_async_context(coordinator.acquire_credential(f"job-{index}", f"user-{index}", candidate))
                for index in range(4)
            ]
            await _assert_next_exhausted(coordinator, candidate)
            return tuple(lease.key_id for lease in leases)

    assert asyncio.run(scenario()) == ("key-a", "key-b", "key-a", "key-b")


def test_shared_redis_server_time_prevents_fast_application_clock_from_pruning_live_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        client = ScriptRedis()
        first = _redis(client, global_limit=1)
        second = _redis(client, global_limit=1)
        candidate = _candidate()
        monkeypatch.setattr(time, "time_ns", lambda: 1)
        async with first.acquire_credential("job-held", "user-1", candidate):
            monkeypatch.setattr(time, "time_ns", lambda: 10**30)
            with pytest.raises(ExecutionCapacityExceeded):
                async with second.acquire_credential("job-next", "user-2", candidate):
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


def test_release_after_owner_ttl_cannot_delete_and_next_acquire_prunes_stale_members() -> None:
    async def scenario() -> tuple[int, tuple[int, ...]]:
        client = ScriptRedis()
        coordinator = _redis(client, global_limit=1, provider_limit=1, user_limit=1)
        candidate = _candidate(pool=_pool(key_limits=(("key-a", 1), ("key-b", 1))))
        expired = coordinator.acquire_credential("job-expired", "user", candidate)
        await expired.__aenter__()
        client.advance(5_001)
        await expired.__aexit__(None, None, None)
        stale_member_count = sum(len(members) for members in client.sets.values())
        async with coordinator.acquire_credential("job-new", "user", candidate):
            live_counts = tuple(len(members) for members in client.sets.values())
        return stale_member_count, live_counts

    stale_count, counts_during_new_lease = asyncio.run(scenario())
    assert stale_count == 6
    assert counts_during_new_lease and set(counts_during_new_lease) == {1}


def test_scope_sets_expire_after_crash_even_when_release_is_unavailable() -> None:
    async def scenario() -> ScriptRedis:
        client = ScriptRedis()
        coordinator = _redis(client)
        candidate = _candidate()
        abandoned = coordinator.acquire_credential("job-crashed", "user", candidate)
        await abandoned.__aenter__()
        client.fail = True
        await abandoned.__aexit__(None, None, None)
        client.fail = False
        assert client.sets
        client.advance(65_001)
        return client

    client = asyncio.run(scenario())
    assert client.owners == {}
    assert client.sets == {}
    assert client.scope_expiries == {}


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
        client.owners[owner_key] = ("replacement-owner", client.server_now_ms + 5_000)
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
