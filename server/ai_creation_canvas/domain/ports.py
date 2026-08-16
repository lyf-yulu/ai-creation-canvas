"""Provider-neutral capabilities implemented by trusted server-side adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import InvalidUpstreamResult

if TYPE_CHECKING:
    from ai_creation_canvas.comfy.service import ComfyServiceHealth, ComfyWorkflowRequest


class GenerationPort(Protocol):
    """Trusted provider adapter.

    ``submit`` MUST honor ``JobRequest.idempotency_key`` at the upstream
    boundary.  ``poll`` MUST raise :class:`InvalidUpstreamResult` when an
    upstream ``succeeded`` response has no valid opaque result identifier;
    adapter validation failures use ``ValueError`` and transport failures use
    a typed retryable upstream exception rather than ``ValueError``.
    """
    service_id: str

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]: ...

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob: ...

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState: ...


class AssetPort(Protocol):
    service_id: str

    async def upload(self, context: RequestContext, asset: AssetRef) -> AssetRef: ...

    async def get(self, context: RequestContext, asset_id: str) -> AssetRef: ...


class UsagePort(Protocol):
    service_id: str

    async def record(self, context: RequestContext, upstream_job_id: str) -> None: ...


class ComfyWorkflowServicePort(Protocol):
    """Isolated trusted port; it is deliberately not a generic generation adapter."""

    service_id: str

    async def health(self, context: RequestContext) -> ComfyServiceHealth: ...

    async def list_node_types(self, context: RequestContext) -> frozenset[str]: ...

    async def submit(self, context: RequestContext, request: ComfyWorkflowRequest) -> UpstreamJob: ...

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState: ...

    async def cancel(self, context: RequestContext, upstream_job_id: str) -> None: ...
