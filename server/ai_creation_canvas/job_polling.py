"""Application service for safely persisting one leased provider poll."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from collections.abc import Mapping

from ai_creation_canvas.catalog import ManagedRoutingRuntime
from ai_creation_canvas.coordination import CoordinationUnavailable, ExecutionCapacityExceeded
from ai_creation_canvas.domain.models import JobState, JobStatus, PortalUser, RequestContext
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.managed_jobs import managed_job_adapter


_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_LOG = logging.getLogger(__name__)


class JobPollingService:
    """Resolve the saved adapter, poll it, and CAS the complete result."""

    def __init__(self, store, registry, managed_runtime: ManagedRoutingRuntime | None = None) -> None:
        self._store = store
        self._registry = registry
        self._managed_runtime = managed_runtime

    async def poll_claim(self, item: Mapping[str, object], token: str) -> dict[str, object]:
        job_id = str(item["id"])
        if item.get("submission_token") != token:
            return dict(item)
        context = self._context(str(item["user_id"]), job_id)
        try:
            if item.get("logical_model_id") is not None:
                if self._managed_runtime is None:
                    raise ValueError("managed routing is unavailable")
                async with managed_job_adapter(self._managed_runtime, context, item) as adapter:
                    return await self._poll_adapter(adapter, context, item, token)
            adapter = self._registry.generation(str(item["service_id"]))
            if getattr(adapter, "supports_background_polling", False) is not True:
                return self._release(job_id, token)
            return await self._poll_adapter(adapter, context, item, token)
        except asyncio.CancelledError:
            raise
        except (CoordinationUnavailable, ExecutionCapacityExceeded):
            return self._release(job_id, token)
        except PortalUpstreamError as error:
            if error.retryable:
                return self._release(job_id, token)
            return self._fail(job_id, token, "REQUEST_REJECTED")
        except (InvalidUpstreamResult, TypeError, ValueError):
            return self._fail(job_id, token, "INVALID_UPSTREAM_RESULT")
        except Exception:
            _LOG.warning("generation job polling failed transiently")
            return self._release(job_id, token)

    async def _poll_adapter(
        self,
        adapter,
        context: RequestContext,
        item: Mapping[str, object],
        token: str,
    ) -> dict[str, object]:
        state = await adapter.poll(context, str(item["upstream_job_id"]))
        if not isinstance(state, JobState):
            raise InvalidUpstreamResult("provider poll state is invalid")
        result_ids = tuple(result.asset_id for result in state.results)
        if state.status is JobStatus.SUCCEEDED and (
            not 1 <= len(result_ids) <= 15
            or any(_RESULT_ID.fullmatch(result_id) is None for result_id in result_ids)
            or len(set(result_ids)) != len(result_ids)
        ):
            raise InvalidUpstreamResult("provider success result is invalid")
        error_code = None
        if state.status is JobStatus.FAILED:
            error_code = state.error.code if state.error is not None else "TASK_FAILED"
            if _ERROR_CODE.fullmatch(error_code) is None:
                error_code = "TASK_FAILED"
        retry_after = 5.0 if state.status is JobStatus.RUNNING else 2.0
        updated = self._store.record_polled_job(
            str(item["id"]),
            token=token,
            status=state.status.value,
            error_code=error_code,
            result_ids=result_ids or None,
            retry_after_seconds=retry_after,
        )
        acknowledge = getattr(adapter, "acknowledge_poll_result", None)
        if (
            state.status is JobStatus.SUCCEEDED
            and callable(acknowledge)
            and self._is_exact_success(updated, result_ids)
        ):
            await acknowledge(str(item["upstream_job_id"]))
        return updated

    def _release(self, job_id: str, token: str) -> dict[str, object]:
        return self._store.release_job_lease(job_id, token=token, retry_after_seconds=2.0)

    def _fail(self, job_id: str, token: str, error_code: str) -> dict[str, object]:
        return self._store.record_polled_job(
            job_id,
            token=token,
            status="failed",
            error_code=error_code,
        )

    @staticmethod
    def _context(user_id: str, job_id: str) -> RequestContext:
        return RequestContext(
            PortalUser(user_id, user_id, "user"),
            f"worker-{secrets.token_hex(8)}",
            f"job-{job_id}",
        )

    @staticmethod
    def _is_exact_success(updated: Mapping[str, object], result_ids: tuple[str, ...]) -> bool:
        try:
            persisted = json.loads(str(updated.get("result_ids_json")))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return (
            updated.get("status") == "succeeded"
            and updated.get("submission_token") is None
            and updated.get("result_id") == result_ids[0]
            and isinstance(persisted, list)
            and tuple(persisted) == result_ids
        )
