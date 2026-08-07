"""Provider-neutral capabilities implemented by trusted server-side adapters."""

from typing import Protocol

from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, ModelSpec, RequestContext, UpstreamJob


class GenerationPort(Protocol):
    """submit MUST honor JobRequest.idempotency_key at the upstream boundary."""
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
