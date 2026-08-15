"""Trusted, server-only ComfyUI service adapter contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import TypeAlias

import httpx

from ai_creation_canvas.domain.models import FrozenDict, JobState, JobStatus, RequestContext, UpstreamJob
from ai_creation_canvas.errors import ApiError, InvalidUpstreamResult, PortalUpstreamError


_SERVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_UPSTREAM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
AuthHeaderResolver: TypeAlias = Callable[[str], Mapping[str, str] | None]
_SubmissionKey: TypeAlias = tuple[str, str]
_SubmissionOutcome: TypeAlias = tuple[UpstreamJob | None, Exception | None]


class ComfyServiceStatus(StrEnum):
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"
    MISCONFIGURED = "misconfigured"


@dataclass(frozen=True, slots=True)
class ComfyServiceDeclaration:
    service_id: str
    base_url: str
    timeout_seconds: int
    auth_header_ref: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.service_id, str) or _SERVICE_ID.fullmatch(self.service_id) is None:
            raise ValueError("ComfyUI service ID is invalid")
        if not isinstance(self.base_url, str) or not self.base_url:
            raise ValueError("ComfyUI service base URL is invalid")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60:
            raise ValueError("ComfyUI timeout is invalid")
        if self.auth_header_ref is not None and (
            not isinstance(self.auth_header_ref, str) or not self.auth_header_ref.strip() or len(self.auth_header_ref) > 128
        ):
            raise ValueError("ComfyUI auth header reference is invalid")


@dataclass(frozen=True, slots=True)
class ComfyServiceHealth:
    service_id: str
    status: ComfyServiceStatus | str
    node_types: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.service_id, str) or _SERVICE_ID.fullmatch(self.service_id) is None:
            raise ValueError("ComfyUI service ID is invalid")
        try:
            object.__setattr__(self, "status", ComfyServiceStatus(self.status))
        except ValueError as error:
            raise ValueError("ComfyUI service health status is invalid") from error
        values = frozenset(self.node_types)
        if any(not isinstance(value, str) or not value or len(value) > 128 for value in values):
            raise ValueError("ComfyUI node inventory is invalid")
        object.__setattr__(self, "node_types", values)


@dataclass(frozen=True, slots=True)
class ComfyWorkflowRequest:
    """A server-built API workflow; browser input is intentionally not accepted here."""

    workflow_id: str
    revision: int
    api_workflow: Mapping[str, object] = field(repr=False)
    asset_ids: tuple[str, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_id, str) or not self.workflow_id.strip():
            raise ValueError("ComfyUI workflow ID is invalid")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("ComfyUI workflow revision is invalid")
        if not isinstance(self.api_workflow, Mapping) or not self.api_workflow:
            raise ValueError("ComfyUI API workflow is invalid")
        object.__setattr__(self, "api_workflow", FrozenDict(self.api_workflow))
        asset_ids = tuple(self.asset_ids)
        if any(not isinstance(asset_id, str) or not asset_id.strip() for asset_id in asset_ids):
            raise ValueError("ComfyUI asset IDs are invalid")
        object.__setattr__(self, "asset_ids", asset_ids)
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("ComfyUI idempotency key is invalid")


class ComfyHttpWorkflowService:
    """Narrow ComfyUI client whose destination and credentials are server-owned."""

    def __init__(
        self,
        declaration: ComfyServiceDeclaration,
        *,
        auth_header_resolver: AuthHeaderResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(declaration, ComfyServiceDeclaration):
            raise ValueError("ComfyUI service declaration is invalid")
        headers: Mapping[str, str] | None = None
        self._misconfigured = False
        if declaration.auth_header_ref is not None:
            try:
                headers = auth_header_resolver(declaration.auth_header_ref) if auth_header_resolver is not None else None
            except Exception:
                headers = None
            if not isinstance(headers, Mapping) or not headers or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()
            ):
                self._misconfigured = True
        self.service_id = declaration.service_id
        self._client = None if self._misconfigured else httpx.AsyncClient(
            base_url=declaration.base_url, timeout=httpx.Timeout(float(declaration.timeout_seconds)),
            headers=dict(headers or {}), transport=transport,
        )
        self._submissions: dict[_SubmissionKey, UpstreamJob] = {}
        self._unknown_submissions: set[_SubmissionKey] = set()
        self._in_flight: dict[_SubmissionKey, asyncio.Future[_SubmissionOutcome]] = {}

    async def health(self, context: RequestContext) -> ComfyServiceHealth:
        if self._misconfigured:
            return ComfyServiceHealth(self.service_id, ComfyServiceStatus.MISCONFIGURED)
        try:
            node_types = await self._node_types()
        except PortalUpstreamError as error:
            return ComfyServiceHealth(
                self.service_id,
                ComfyServiceStatus.UNAVAILABLE if error.retryable else ComfyServiceStatus.MISCONFIGURED,
            )
        except InvalidUpstreamResult:
            return ComfyServiceHealth(self.service_id, ComfyServiceStatus.MISCONFIGURED)
        return ComfyServiceHealth(self.service_id, ComfyServiceStatus.HEALTHY, node_types)

    async def list_node_types(self, context: RequestContext) -> frozenset[str]:
        return await self._node_types()

    async def submit(self, context: RequestContext, request: ComfyWorkflowRequest) -> UpstreamJob:
        if not isinstance(request, ComfyWorkflowRequest):
            raise ValueError("ComfyUI workflow request is invalid")
        key = (context.user.user_id, request.idempotency_key)
        submitted = self._submissions.get(key)
        if submitted is not None:
            return submitted
        if key in self._unknown_submissions:
            raise PortalUpstreamError("SUBMISSION_UNKNOWN", retryable=False)
        in_flight = self._in_flight.get(key)
        if in_flight is not None:
            return await self._await_submission(in_flight)
        in_flight = asyncio.get_running_loop().create_future()
        self._in_flight[key] = in_flight
        try:
            response = await self._request(
                "POST", "/prompt", json={"prompt": _thaw_json(request.api_workflow), "client_id": request.idempotency_key}
            )
            payload = self._json_object(response)
            prompt_id = payload.get("prompt_id")
            if not isinstance(prompt_id, str) or _UPSTREAM_ID.fullmatch(prompt_id) is None:
                raise InvalidUpstreamResult("ComfyUI prompt identifier is invalid")
        except asyncio.CancelledError:
            self._unknown_submissions.add(key)
            self._complete_submission(in_flight, PortalUpstreamError("SUBMISSION_UNKNOWN", retryable=False))
            raise
        except PortalUpstreamError as error:
            if error.retryable:
                self._unknown_submissions.add(key)
                error = PortalUpstreamError("SUBMISSION_UNKNOWN", retryable=False)
            self._complete_submission(in_flight, error)
            raise error
        except InvalidUpstreamResult as error:
            self._unknown_submissions.add(key)
            self._complete_submission(in_flight, error)
            raise
        except Exception as error:
            self._complete_submission(in_flight, error)
            raise
        else:
            job = UpstreamJob(self.service_id, prompt_id, JobState(prompt_id, JobStatus.QUEUED))
            self._submissions[key] = job
            self._complete_submission(in_flight, None, job)
            return job
        finally:
            if self._in_flight.get(key) is in_flight:
                self._in_flight.pop(key, None)

    @staticmethod
    async def _await_submission(in_flight: asyncio.Future[_SubmissionOutcome]) -> UpstreamJob:
        job, error = await asyncio.shield(in_flight)
        if error is not None:
            raise error
        assert job is not None
        return job

    @staticmethod
    def _complete_submission(
        in_flight: asyncio.Future[_SubmissionOutcome], error: Exception | None, job: UpstreamJob | None = None
    ) -> None:
        if not in_flight.done():
            in_flight.set_result((job, error))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        prompt_id = self._prompt_id(upstream_job_id)
        response = await self._request("GET", f"/history/{prompt_id}")
        payload = self._json_object(response)
        record = payload.get(prompt_id)
        if record is None:
            return JobState(prompt_id, JobStatus.QUEUED)
        if not isinstance(record, Mapping):
            raise InvalidUpstreamResult("ComfyUI history response is invalid")
        status = record.get("status")
        if not isinstance(status, Mapping) or not isinstance(status.get("status_str"), str):
            raise InvalidUpstreamResult("ComfyUI history status is invalid")
        status_name = status["status_str"].lower()
        if status_name in {"pending", "queued"}:
            return JobState(prompt_id, JobStatus.QUEUED)
        if status_name in {"running", "executing"}:
            return JobState(prompt_id, JobStatus.RUNNING)
        # A completed ComfyUI result still needs the platform asset boundary from
        # the future execution slice, so it cannot be reported as a success yet.
        return JobState(
            prompt_id,
            JobStatus.FAILED,
            error=ApiError("TASK_FAILED", "The generation task failed.", False, context.request_id, "polling"),
        )

    async def cancel(self, context: RequestContext, upstream_job_id: str) -> None:
        prompt_id = self._prompt_id(upstream_job_id)
        await self._request("POST", "/queue", json={"delete": [prompt_id]})

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _node_types(self) -> frozenset[str]:
        payload = self._json_object(await self._request("GET", "/object_info"))
        node_types = frozenset(payload)
        if not node_types or any(not isinstance(value, str) or not value or len(value) > 128 for value in node_types):
            raise InvalidUpstreamResult("ComfyUI node inventory is invalid")
        return node_types

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        if self._client is None:
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=False)
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as error:
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True) from error
        if response.is_success:
            return response
        retryable = response.status_code in {408, 429} or response.status_code >= 500
        raise PortalUpstreamError(
            "UPSTREAM_UNAVAILABLE" if retryable else "REQUEST_REJECTED",
            retryable=retryable,
            status_code=response.status_code,
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> Mapping[str, object]:
        try:
            payload = response.json()
        except ValueError as error:
            raise InvalidUpstreamResult("ComfyUI response is invalid") from error
        if not isinstance(payload, Mapping):
            raise InvalidUpstreamResult("ComfyUI response is invalid")
        return payload

    @staticmethod
    def _prompt_id(value: str) -> str:
        if not isinstance(value, str) or _UPSTREAM_ID.fullmatch(value) is None:
            raise ValueError("ComfyUI prompt identifier is invalid")
        return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
