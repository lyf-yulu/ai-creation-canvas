from __future__ import annotations

import asyncio

import pytest

from ai_creation_canvas.coordination import ExecutionCapacityExceeded, LocalExecutionCoordinator, RedisExecutionCoordinator


def test_local_coordinator_bounds_concurrency_and_releases_on_error() -> None:
    async def scenario() -> None:
        coordinator = LocalExecutionCoordinator(global_limit=1, provider_limit=1, user_limit=1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def first() -> None:
            try:
                async with coordinator.acquire("job-a", "user-a", "provider-a", "model-a"):
                    entered.set()
                    await release.wait()
                    raise RuntimeError("fixture")
            except RuntimeError:
                pass

        task = asyncio.create_task(first())
        await entered.wait()
        with pytest.raises(ExecutionCapacityExceeded):
            async with coordinator.acquire("job-b", "user-b", "provider-b", "model-b"):
                raise AssertionError("unreachable")
        release.set()
        await task
        async with coordinator.acquire("job-c", "user-a", "provider-a", "model-a"):
            pass

    asyncio.run(scenario())


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.acquire_result = 1

    async def eval(self, *args: object) -> int:
        self.calls.append(args)
        return self.acquire_result if len(self.calls) % 2 == 1 else 1

    async def ping(self) -> bool:
        return True


def test_redis_coordinator_uses_only_hashed_opaque_keys_and_releases() -> None:
    async def scenario() -> FakeRedis:
        client = FakeRedis()
        coordinator = RedisExecutionCoordinator(client, namespace="aicc-test", global_limit=8, provider_limit=4, user_limit=2, lease_seconds=30)
        assert await coordinator.healthcheck() is True
        async with coordinator.acquire("job-visible", "user-secret-name", "provider-private", "model-private"):
            pass
        return client

    client = asyncio.run(scenario())
    joined = repr(client.calls)
    assert "job-visible" not in joined
    assert "user-secret-name" not in joined
    assert "provider-private" not in joined
    assert "model-private" not in joined
    assert len(client.calls) == 2


def test_redis_coordinator_rejects_capacity_without_running_body() -> None:
    async def scenario() -> None:
        client = FakeRedis()
        client.acquire_result = 0
        coordinator = RedisExecutionCoordinator(client, namespace="aicc-test", global_limit=1, provider_limit=1, user_limit=1, lease_seconds=30)
        with pytest.raises(ExecutionCapacityExceeded):
            async with coordinator.acquire("job", "user", "provider", "model"):
                raise AssertionError("unreachable")
        assert len(client.calls) == 1

    asyncio.run(scenario())
