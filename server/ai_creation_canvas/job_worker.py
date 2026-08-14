"""Lease-backed scheduling for background generation polling."""

from __future__ import annotations

import asyncio
import logging


_LOG = logging.getLogger(__name__)


class JobWorker:
    """Claim jobs, maintain leases, and delegate provider semantics."""

    def __init__(self, store, polling_service, *, lease_seconds: float = 30.0, idle_seconds: float = 1.0) -> None:
        if lease_seconds <= 0 or idle_seconds <= 0:
            raise ValueError("worker intervals must be positive")
        if not callable(getattr(polling_service, "poll_claim", None)):
            raise ValueError("polling service is invalid")
        self._store = store
        self._polling_service = polling_service
        self._lease_seconds = lease_seconds
        self._idle_seconds = idle_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="canvas-job-worker")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if task.get_loop() is not asyncio.get_running_loop():
            return
        assert self._stopping is not None
        self._stopping.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._stopping = None

    async def _run(self) -> None:
        assert self._stopping is not None
        while not self._stopping.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.warning("generation job worker iteration failed")
                worked = False
            if worked:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._idle_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> bool:
        acknowledgement = self._store.claim_job_acknowledgement(
            lease_seconds=self._lease_seconds,
        )
        if acknowledgement is not None:
            await self._run_acknowledgement(acknowledgement)
            return True
        item = self._store.claim_pollable_job(lease_seconds=self._lease_seconds)
        if item is None:
            return False
        job_id = str(item["id"])
        token = str(item["submission_token"])
        heartbeat = asyncio.create_task(
            self._renew_lease(job_id, token),
            name=f"canvas-job-lease-{job_id}",
        )
        try:
            await self._polling_service.poll_claim(item, token)
        except asyncio.CancelledError:
            try:
                self._store.release_job_lease(job_id, token=token, retry_after_seconds=0)
            except Exception:
                _LOG.warning("generation job lease release failed during shutdown")
            raise
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    async def _run_acknowledgement(self, item) -> None:
        job_id = str(item["id"])
        token = str(item["acknowledgement_token"])
        heartbeat = asyncio.create_task(
            self._renew_acknowledgement(job_id, token),
            name=f"canvas-job-ack-lease-{job_id}",
        )
        try:
            await self._polling_service.acknowledge_claim(item, token)
        except asyncio.CancelledError:
            try:
                self._store.release_job_acknowledgement(
                    job_id,
                    token=token,
                    retry_after_seconds=0,
                )
            except Exception:
                _LOG.warning("generation job acknowledgement release failed during shutdown")
            raise
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _renew_lease(self, job_id: str, token: str) -> None:
        interval = self._lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            if not self._store.renew_job_lease(
                job_id,
                token=token,
                lease_seconds=self._lease_seconds,
            ):
                return

    async def _renew_acknowledgement(self, job_id: str, token: str) -> None:
        interval = self._lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            if not self._store.renew_job_acknowledgement(
                job_id,
                token=token,
                lease_seconds=self._lease_seconds,
            ):
                return
