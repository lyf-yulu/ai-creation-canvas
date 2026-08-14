"""Deterministic, offline image generation adapter for local validation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from importlib.resources import files
import re

from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, ModelSpec, RequestContext, UpstreamJob


_UPSTREAM_ID = re.compile(r"demo_[0-9a-f]{64}\Z")
_RESULT_ID = re.compile(r"demo_result_[0-9a-f]{64}\Z")


class _MemoryResultStream:
    def __init__(self, *, status_code: int, data: bytes, headers: dict[str, str], head: bool = False) -> None:
        self.status_code = status_code
        self.headers = headers
        self._data = b"" if head else data
        self.closed = False

    async def aiter_bytes(self):
        if self._data:
            yield self._data

    async def aclose(self) -> None:
        self.closed = True


class DemoGenerationAdapter:
    """A no-key adapter that returns one fixed package image through the real job path."""

    service_id = "demo-image"
    requires_portal_cookie = False
    supports_background_polling = True

    def __init__(self) -> None:
        self._image = files("ai_creation_canvas").joinpath("static/demo-result.png").read_bytes()
        self._etag = f'"{hashlib.sha256(self._image).hexdigest()}"'

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return (ModelSpec(
            "demo-image-v1",
            self.service_id,
            "本地演示图片",
            ("image.generate",),
            ("text",),
            {
                "type": "object",
                "properties": {
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["square", "portrait", "landscape"],
                        "default": "landscape",
                    },
                },
                "required": ["aspect_ratio"],
                "additionalProperties": False,
            },
        ),)

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        if request.model_id != "demo-image-v1" or request.operation.value != "image.generate" or request.asset_ids:
            raise ValueError("demo request is invalid")
        digest = hashlib.sha256(f"{context.user.user_id}\n{request.idempotency_key}".encode("utf-8")).hexdigest()
        upstream_id = f"demo_{digest}"
        return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, "queued"), datetime.now(UTC))

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        if not _UPSTREAM_ID.fullmatch(upstream_job_id):
            raise ValueError("demo job is invalid")
        result_id = f"demo_result_{upstream_job_id.removeprefix('demo_')}"
        return JobState(upstream_job_id, "succeeded", AssetRef(result_id, "reference", "active", "image/png"))

    async def open_result(
        self,
        context: RequestContext,
        result_id: str,
        *,
        cookie_header: str,
        range_header: str | None = None,
        head: bool = False,
    ) -> _MemoryResultStream:
        del context, cookie_header
        size = len(self._image)
        common = {"content-type": "image/png", "accept-ranges": "bytes", "etag": self._etag}
        if not _RESULT_ID.fullmatch(result_id):
            return _MemoryResultStream(status_code=404, data=b"", headers={**common, "content-length": "0"}, head=head)
        if range_header is None:
            return _MemoryResultStream(status_code=200, data=self._image, headers={**common, "content-length": str(size)}, head=head)
        interval = self._range(range_header, size)
        if interval is None:
            return _MemoryResultStream(status_code=416, data=b"", headers={**common, "content-length": "0", "content-range": f"bytes */{size}"}, head=head)
        start, end = interval
        payload = self._image[start:end + 1]
        return _MemoryResultStream(
            status_code=206,
            data=payload,
            headers={**common, "content-length": str(len(payload)), "content-range": f"bytes {start}-{end}/{size}"},
            head=head,
        )

    @staticmethod
    def _range(value: str, size: int) -> tuple[int, int] | None:
        if not value.startswith("bytes=") or "," in value:
            return None
        left, separator, right = value[6:].partition("-")
        if not separator or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()):
            return None
        if not left:
            suffix = int(right)
            return None if suffix <= 0 else (max(0, size - suffix), size - 1)
        start = int(left)
        if start >= size:
            return None
        end = min(int(right), size - 1) if right else size - 1
        return None if end < start else (start, end)
