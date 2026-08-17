"""Controlled Volcano Ark image and video adapter.

The adapter is deliberately provider-facing only: browser callers see the
existing model/job/result contracts, while the API key and short-lived Ark
result URLs stay in the server's protected data directory.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from ai_creation_canvas.domain.models import AssetRef, JobRequest, JobState, JobStatus, ModelInputPort, ModelOperation, ModelSpec, RequestContext, UpstreamJob
from ai_creation_canvas.errors import ApiError, InvalidUpstreamResult, LocalRecoveryUnavailable, PortalUpstreamError
from ai_creation_canvas.adapters.retry import SubmissionDisposition, SubmissionError, UnknownSubmissionResult, error_from_response, error_from_transport, local_rejection
from ai_creation_canvas.parameter_schema import validate_parameter_schema, validate_parameter_values


_ARK_URL = "https://ark.cn-beijing.volces.com"
_LIBRARY_REF = re.compile(r"asset://asset-[A-Za-z0-9_-]{1,100}\Z")
_RESULT_ID = re.compile(r"ark_result_[0-9a-f]{64}\Z")
_CHIYUN_RESULT_ID = re.compile(r"chiyun_result_[0-9a-f]{64}\Z")
_CONTENT_TASK_ID = re.compile(r"cgt-[A-Za-z0-9_-]{1,120}\Z")
_MAX_RESULT_BYTES = 256 * 1024 * 1024
_IMAGE_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})
_AUDIO_MIME = frozenset({"audio/wav", "audio/mpeg"})
_VIDEO_MIME = frozenset({"video/mp4", "video/webm"})
_MAX_AUDIO_BYTES = 15 * 1024 * 1024
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_CONFIG_BYTES = 64 * 1024
_ARK_PARAMETER_TARGETS = frozenset({
    "size", "quality", "n", "strength", "watermark", "output_format",
    "optimize_prompt_options.mode", "sequential_image_generation", "sequential_image_generation_options.max_images",
    "ratio", "duration", "resolution", "generate_audio", "camera_fixed", "return_last_frame",
})


@dataclass(frozen=True, slots=True)
class ArkModelDeclaration:
    model_id: str
    service_id: str
    display_name: str
    operations: tuple[ModelOperation | str, ...]
    parameter_schema: Mapping[str, object]
    input_ports: tuple[ModelInputPort, ...] = (ModelInputPort("prompt", "text", 1, 1),)
    parameter_mappings: Mapping[str, str] = field(default_factory=dict)
    provider_model_name: str | None = None

    def __post_init__(self) -> None:
        provider_model_name = self.model_id if self.provider_model_name is None else self.provider_model_name
        if not isinstance(provider_model_name, str) or not provider_model_name or len(provider_model_name) > 128:
            raise ValueError("Ark provider model name is invalid")
        object.__setattr__(self, "provider_model_name", provider_model_name)
        schema_properties = self.parameter_schema.get("properties") if isinstance(self.parameter_schema, Mapping) else None
        if not isinstance(schema_properties, Mapping) or set(self.parameter_mappings) != set(schema_properties):
            raise ValueError("every Ark parameter requires an explicit provider mapping")
        validate_parameter_schema(self.parameter_schema)
        if any(target not in _ARK_PARAMETER_TARGETS for target in self.parameter_mappings.values()):
            raise ValueError("Ark parameter mapping is unsupported")
        if len(set(self.parameter_mappings.values())) != len(self.parameter_mappings):
            raise ValueError("Ark provider parameter targets must be unique")
        ModelSpec(self.model_id, self.service_id, self.display_name, self.operations, ("text",), self.parameter_schema, None, self.input_ports, self.parameter_mappings)


class _FileStream:
    def __init__(self, path: Path, mime: str, *, offset: int = 0, length: int | None = None, head: bool = False) -> None:
        self.status_code = 200 if offset == 0 and length is None else 206
        size = path.stat().st_size
        self.headers = {"content-type": mime, "content-length": str(size if length is None else length), "accept-ranges": "bytes"}
        if self.status_code == 206:
            assert length is not None
            self.headers["content-range"] = f"bytes {offset}-{offset + length - 1}/{size}"
        self._path, self._offset, self._length, self._head = path, offset, length, head

    async def aiter_bytes(self):
        if self._head:
            return
        remaining = self._length
        with self._path.open("rb") as source:
            source.seek(self._offset)
            while remaining is None or remaining:
                chunk = await asyncio.to_thread(source.read, min(64 * 1024, remaining) if remaining is not None else 64 * 1024)
                if not chunk:
                    return
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    async def aclose(self) -> None:
        return None


class ArkGenerationAdapter:
    """Maps a data-only Ark model declaration into existing generation ports."""

    requires_portal_cookie = False
    supports_background_polling = True

    def __init__(self, *, api_key: str, data_dir: Path | str, models: tuple[ArkModelDeclaration, ...], transport: httpx.AsyncBaseTransport | None = None, asset_loader: Callable[[str], tuple[bytes, str]] | None = None, reusable_result_services: frozenset[str] | None = None) -> None:
        if not isinstance(api_key, str) or len(api_key.strip()) < 8:
            raise ValueError("Ark API key is unavailable")
        if not models or len({model.model_id for model in models}) != len(models) or len({model.service_id for model in models}) != 1:
            raise ValueError("Ark model declarations are invalid")
        self._api_key, self._models, self._transport, self._asset_loader = api_key, {model.model_id: model for model in models}, transport, asset_loader
        self.service_id = models[0].service_id
        self.reusable_result_services = reusable_result_services or frozenset({self.service_id})
        root = Path(data_dir) / "ark-results"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("Ark result root is unsafe")
        os.chmod(root, 0o700)
        self._root, self._index_path = root, root / "pending.json"
        self._index_lock_path = root / "pending.lock"

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]:
        del context
        return tuple(
            ModelSpec(model.model_id, model.service_id, model.display_name, model.operations, ("text",), model.parameter_schema, None, model.input_ports, model.parameter_mappings)
            for model in sorted(self._models.values(), key=lambda item: item.model_id)
        )

    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        typed_error: SubmissionError | None = None
        adapter_template = f"ark.{request.operation.value}"
        if adapter_template not in {"ark.image.generate", "ark.image.edit", "ark.video.generate"}:
            adapter_template = "ark.image.generate"
        try:
            return await self._submit(context, request)
        except SubmissionError:
            raise
        except InvalidUpstreamResult:
            typed_error = UnknownSubmissionResult(adapter_template)
        except ValueError as error:
            typed_error = local_rejection(error, adapter_template)
        except OSError:
            typed_error = SubmissionError(
                SubmissionDisposition.SUBMISSION_UNKNOWN,
                "LOCAL_STATE_UNAVAILABLE",
                adapter_template=adapter_template,
            )
        raise typed_error

    async def _submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob:
        del context
        declaration = self._models.get(request.model_id)
        if declaration is None or request.operation not in tuple(ModelOperation(value) for value in declaration.operations) or request.asset_ids:
            raise ValueError("Ark request is invalid")
        params = self._validated_params(declaration, request.params)
        provider_params = _compile_provider_parameters(declaration.parameter_mappings, params)
        if request.operation in {ModelOperation.IMAGE_GENERATE, ModelOperation.IMAGE_EDIT}:
            references = self._image_references(declaration, request)
            if request.operation is ModelOperation.IMAGE_EDIT and not references or request.operation is ModelOperation.IMAGE_GENERATE and references:
                raise ValueError("Ark image operation does not match its inputs")
            payload = {"model": declaration.provider_model_name, "prompt": request.prompt, **({"image": references} if references else {}), **provider_params, "response_format": "url"}
            response = await self._api(
                "POST",
                "/api/v3/images/generations",
                json=payload,
                submission_template=f"ark.{request.operation.value}",
            )
            data = self._json(response)
            items = data.get("data")
            if not isinstance(items, list) or not 1 <= len(items) <= 15 or any(not isinstance(item, Mapping) or not isinstance(item.get("url"), str) for item in items):
                raise InvalidUpstreamResult("Ark image response is invalid")
            upstream_id = "ark_image_" + hashlib.sha256(f"{request.model_id}\n{request.idempotency_key}".encode()).hexdigest()
            await self._record_pending(upstream_id, tuple(str(item["url"]) for item in items), "image")
            return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, JobStatus.QUEUED), datetime.now(UTC))
        if request.operation is ModelOperation.VIDEO_GENERATE:
            content = self._video_content(declaration, request)
            payload = {"model": declaration.provider_model_name, "content": content, **provider_params}
            if len(json.dumps(payload).encode("utf-8")) > _MAX_REQUEST_BYTES:
                raise ValueError("Ark video request is too large")
            response = await self._api(
                "POST",
                "/api/v3/contents/generations/tasks",
                json=payload,
                submission_template="ark.video.generate",
            )
            body = self._json(response)
            upstream_id = body.get("id")
            if not isinstance(upstream_id, str) or not upstream_id.startswith("cgt-"):
                raise InvalidUpstreamResult("Ark video response is invalid")
            return UpstreamJob(self.service_id, upstream_id, JobState(upstream_id, JobStatus.QUEUED), datetime.now(UTC))
        raise ValueError("Ark operation is unsupported")

    def _image_references(self, declaration: ArkModelDeclaration, request: JobRequest) -> list[str]:
        unknown = set(request.inputs) - {"reference_images"}
        values = tuple(request.inputs.get("reference_images", ()))
        port = next((candidate for candidate in declaration.input_ports if candidate.port_id == "reference_images"), None)
        if unknown or values and (port is None or len(values) > port.max_items) or values and self._asset_loader is None:
            raise ValueError("Ark image inputs are invalid")
        return [self._asset_data_url(asset_id, _IMAGE_MIME, 20 * 1024 * 1024) for asset_id in values]

    def _asset_data_url(self, asset_id: str, allowed_mime: frozenset[str], maximum: int, *, video_image: bool = False) -> str:
        if self._asset_loader is None:
            raise ValueError("Ark asset loader is unavailable")
        body, mime = self._asset_loader(asset_id)
        if mime not in allowed_mime or not body or len(body) > maximum:
            raise ValueError("Ark media input is invalid")
        if video_image:
            dimensions = _image_dimensions(body, mime)
            if dimensions is None or not 300 <= dimensions[0] <= 6000 or not 300 <= dimensions[1] <= 6000 or not 0.4 <= dimensions[0] / dimensions[1] <= 2.5:
                raise ValueError("Ark video image dimensions are invalid")
        return f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"

    def _video_content(self, declaration: ArkModelDeclaration, request: JobRequest) -> list[dict[str, object]]:
        declared = {port.port_id: port for port in declaration.input_ports}
        supported = {"first_frame", "last_frame", "reference_images", "reference_audio"}
        if set(request.inputs) - supported:
            # Video references require the provider asset-upload flow; never pretend to submit them.
            raise ValueError("Ark video input requires an unsupported asset flow")
        content: list[dict[str, object]] = [{"type": "text", "text": request.prompt}]
        roles = (("first_frame", "first_frame"), ("last_frame", "last_frame"), ("reference_images", "reference_image"))
        for port_id, role in roles:
            values = tuple(request.inputs.get(port_id, ()))
            port = declared.get(port_id)
            if values and (port is None or len(values) > port.max_items):
                raise ValueError("Ark video image inputs are invalid")
            for asset_id in values:
                if isinstance(asset_id, str) and asset_id.startswith("asset://"):
                    # Private asset-library references are the official way to use
                    # library portraits; render the URI verbatim, never as local bytes.
                    if _LIBRARY_REF.fullmatch(asset_id) is None:
                        raise ValueError("Ark video input requires an unsupported asset flow")
                    url = asset_id
                else:
                    url = self._asset_data_url(asset_id, _IMAGE_MIME, 20 * 1024 * 1024, video_image=True)
                content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
        audio_values = tuple(request.inputs.get("reference_audio", ()))
        audio_port = declared.get("reference_audio")
        if audio_values and (audio_port is None or len(audio_values) > audio_port.max_items or len(content) == 1):
            # Seedance 2.0 does not accept an audio-only media reference request.
            raise ValueError("Ark video audio inputs are invalid")
        for asset_id in audio_values:
            content.append({"type": "audio_url", "audio_url": {"url": self._asset_data_url(asset_id, _AUDIO_MIME, _MAX_AUDIO_BYTES)}, "role": "reference_audio"})
        return content

    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        try:
            return await self._poll(context, upstream_job_id)
        except SubmissionError:
            raise
        except OSError as error:
            raise LocalRecoveryUnavailable() from error

    async def _poll(self, context: RequestContext, upstream_job_id: str) -> JobState:
        del context
        pending = await self._pending(upstream_job_id)
        if pending is not None:
            results = tuple([await self._download(upstream_job_id if index == 0 else f"{upstream_job_id}\n{index}", url, pending["kind"]) for index, url in enumerate(pending["urls"])])
            return JobState(upstream_job_id, JobStatus.SUCCEEDED, results=results)
        if not _CONTENT_TASK_ID.fullmatch(upstream_job_id):
            raise ValueError("Ark job is invalid")
        response = await self._api("GET", f"/api/v3/contents/generations/tasks/{upstream_job_id}")
        body = self._json(response)
        status = body.get("status")
        if status in {"queued", "running"}:
            return JobState(upstream_job_id, JobStatus(status))
        if status == "failed":
            return JobState(upstream_job_id, JobStatus.FAILED, error=ApiError("TASK_FAILED", "The generation task failed.", False, "ark", "generation"))
        if status != "succeeded" or not isinstance(body.get("content"), Mapping) or not isinstance(body["content"].get("video_url"), str):
            raise InvalidUpstreamResult("Ark video poll response is invalid")
        return JobState(upstream_job_id, JobStatus.SUCCEEDED, await self._download(upstream_job_id, str(body["content"]["video_url"]), "video"))

    async def cancel(self, context: RequestContext, upstream_job_id: str) -> None:
        """Cancel an Ark content task that the caller has already proven is queued."""
        del context
        if not _CONTENT_TASK_ID.fullmatch(upstream_job_id):
            raise ValueError("Ark job is invalid")
        await self._api("DELETE", f"/api/v3/contents/generations/tasks/{upstream_job_id}")

    async def acknowledge_poll_result(self, upstream_job_id: str) -> None:
        """Discard image recovery metadata only after the job store commits success."""
        try:
            await self._clear_pending(upstream_job_id)
        except OSError as error:
            raise LocalRecoveryUnavailable() from error

    async def open_result(self, context: RequestContext, result_id: str, *, cookie_header: str, range_header: str | None = None, head: bool = False):
        del context, cookie_header
        if not _RESULT_ID.fullmatch(result_id):
            return _missing_stream()
        media = self._root / result_id
        metadata = self._root / f"{result_id}.json"
        if not media.is_file() or not metadata.is_file():
            return _missing_stream()
        try:
            mime = json.loads(metadata.read_text(encoding="utf-8"))["mime"]
        except (OSError, ValueError, KeyError, TypeError):
            return _missing_stream()
        size = media.stat().st_size
        if range_header is None:
            return _FileStream(media, mime, head=head)
        interval = _range(range_header, size)
        if interval is None:
            return _range_missing_stream(size)
        start, end = interval
        return _FileStream(media, mime, offset=start, length=end - start + 1, head=head)

    async def _api(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
        submission_template: str | None = None,
    ) -> httpx.Response:
        submission_error: SubmissionError | None = None
        try:
            async with httpx.AsyncClient(base_url=_ARK_URL, transport=self._transport, timeout=httpx.Timeout(30), follow_redirects=False, trust_env=False) as client:
                kwargs: dict[str, object] = {"headers": {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}}
                if json is not None:
                    kwargs["json"] = json
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            if submission_template is not None:
                submission_error = error_from_transport(error, submission_template)
            elif isinstance(error, httpx.TimeoutException):
                raise PortalUpstreamError("UPSTREAM_TIMEOUT", retryable=True) from error
            else:
                raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True) from error
        if submission_error is not None:
            raise submission_error
        if response.status_code in {408, 429} or response.status_code >= 500:
            if submission_template is not None:
                raise error_from_response(response, submission_template)
            raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=True, status_code=response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            if submission_template is not None:
                raise error_from_response(response, submission_template)
            raise PortalUpstreamError("REQUEST_REJECTED", retryable=False, status_code=response.status_code)
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Mapping[str, object]:
        try:
            body = response.json()
        except ValueError as error:
            raise InvalidUpstreamResult("Ark response is not JSON") from error
        if not isinstance(body, Mapping):
            raise InvalidUpstreamResult("Ark response is not an object")
        return body

    @staticmethod
    def _validated_params(declaration: ArkModelDeclaration, values: Mapping[str, object]) -> dict[str, object]:
        try:
            return validate_parameter_values(declaration.parameter_schema, values)
        except ValueError as error:
            raise ValueError("Ark parameters are invalid") from error

    async def _record_pending(self, job_id: str, urls: tuple[str, ...], kind: str) -> None:
        if not 1 <= len(urls) <= (15 if kind == "image" else 1):
            raise InvalidUpstreamResult("Ark result count is invalid")
        for url in urls:
            self._safe_result_url(url)
        with self._locked_index():
            values = self._read_index(); values[job_id] = {"urls": list(urls), "kind": kind}; self._write_index(values)

    async def _pending(self, job_id: str) -> dict[str, object] | None:
        with self._locked_index():
            item = self._read_index().get(job_id)
        if not isinstance(item, dict) or item.get("kind") not in {"image", "video"}:
            return None
        urls = item.get("urls")
        if urls is None and isinstance(item.get("url"), str):
            urls = [item["url"]]
        maximum = 15 if item.get("kind") == "image" else 1
        return {"urls": urls, "kind": item["kind"]} if isinstance(urls, list) and 1 <= len(urls) <= maximum and all(isinstance(url, str) for url in urls) else None

    async def _clear_pending(self, job_id: str) -> None:
        with self._locked_index():
            values = self._read_index()
            if values.pop(job_id, None) is not None:
                self._write_index(values)

    def _read_index(self) -> dict[str, dict[str, object]]:
        if not self._index_path.exists(): return {}
        try:
            value = json.loads(self._index_path.read_text(encoding="utf-8"))
        except ValueError: return {}
        return value if isinstance(value, dict) else {}

    @contextmanager
    def _locked_index(self):
        descriptor = os.open(
            self._index_lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.chmod(self._index_lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _write_index(self, values: Mapping[str, object]) -> None:
        temporary = self._root / f".pending.{os.getpid()}.{os.urandom(8).hex()}.tmp"
        try:
            temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._index_path)
        finally:
            _safe_unlink(temporary)

    @staticmethod
    def _safe_result_url(value: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".volces.com") or parsed.username or parsed.password or parsed.fragment:
            raise InvalidUpstreamResult("Ark result URL is invalid")

    async def _download(self, upstream_job_id: str, url: str, kind: str) -> AssetRef:
        self._safe_result_url(url)
        result_id = "ark_result_" + hashlib.sha256(upstream_job_id.encode()).hexdigest()
        media, metadata, temporary = self._root / result_id, self._root / f"{result_id}.json", self._root / f".{result_id}.tmp"
        if media.is_file() and metadata.is_file():
            mime = json.loads(metadata.read_text(encoding="utf-8"))["mime"]
            return AssetRef(result_id, "reference", "active", mime)
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(60), follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, headers={}) as response:
                    if response.status_code != 200:
                        raise PortalUpstreamError("UPSTREAM_UNAVAILABLE", retryable=response.status_code >= 500 or response.status_code in {408, 429}, status_code=response.status_code)
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime not in (_IMAGE_MIME if kind == "image" else _VIDEO_MIME):
                        raise InvalidUpstreamResult("Ark result MIME is invalid")
                    total = 0
                    with temporary.open("wb") as destination:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > _MAX_RESULT_BYTES:
                                raise InvalidUpstreamResult("Ark result is too large")
                            destination.write(chunk)
            os.chmod(temporary, 0o600); os.replace(temporary, media)
            metadata_temporary = metadata.with_suffix(".tmp")
            try:
                metadata_temporary.write_text(json.dumps({"mime": mime}), encoding="utf-8")
                os.chmod(metadata_temporary, 0o600)
                os.replace(metadata_temporary, metadata)
            finally:
                _safe_unlink(metadata_temporary)
            return AssetRef(result_id, "reference", "active", mime)
        except OSError as error:
            _safe_unlink(media)
            _safe_unlink(metadata)
            raise LocalRecoveryUnavailable() from error
        finally:
            _safe_unlink(temporary)


def _range(value: str, size: int) -> tuple[int, int] | None:
    if not value.startswith("bytes=") or "," in value: return None
    left, separator, right = value[6:].partition("-")
    if not separator or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()): return None
    if not left:
        suffix = int(right); return None if suffix <= 0 else (max(0, size - suffix), size - 1)
    start = int(left)
    if start >= size: return None
    end = min(int(right), size - 1) if right else size - 1
    return None if end < start else (start, end)


def _image_dimensions(body: bytes, mime: str) -> tuple[int, int] | None:
    if mime == "image/png" and len(body) >= 24 and body.startswith(b"\x89PNG\r\n\x1a\n") and body[12:16] == b"IHDR":
        width, height = int.from_bytes(body[16:20], "big"), int.from_bytes(body[20:24], "big")
        return (width, height) if width and height else None
    if mime == "image/webp" and len(body) >= 30 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        kind, data = body[12:16], body[20:]
        if kind == b"VP8X" and len(data) >= 10:
            return (1 + int.from_bytes(data[4:7], "little"), 1 + int.from_bytes(data[7:10], "little"))
        if kind == b"VP8 " and len(data) >= 10 and data[3:6] == b"\x9d\x01\x2a":
            return (int.from_bytes(data[6:8], "little") & 0x3FFF, int.from_bytes(data[8:10], "little") & 0x3FFF)
        if kind == b"VP8L" and len(data) >= 5 and data[0] == 0x2F:
            bits = int.from_bytes(data[1:5], "little")
            return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
        return None
    if mime == "image/jpeg" and len(body) >= 4 and body.startswith(b"\xff\xd8\xff"):
        offset = 2
        frame_markers = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
        while offset + 4 <= len(body):
            if body[offset] != 0xFF:
                offset += 1
                continue
            marker = body[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(body):
                return None
            length = int.from_bytes(body[offset:offset + 2], "big")
            if length < 2 or offset + length > len(body):
                return None
            if marker in frame_markers and length >= 7:
                height = int.from_bytes(body[offset + 3:offset + 5], "big")
                width = int.from_bytes(body[offset + 5:offset + 7], "big")
                return (width, height) if width and height else None
            offset += length
    return None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _missing_stream():
    stream = _FileStream.__new__(_FileStream); stream.status_code, stream.headers = 404, {"content-length": "0"}; stream.aiter_bytes = _empty  # type: ignore[attr-defined]
    return stream


def _range_missing_stream(size: int):
    stream = _FileStream.__new__(_FileStream); stream.status_code, stream.headers = 416, {"content-length": "0", "content-range": f"bytes */{size}"}; stream.aiter_bytes = _empty  # type: ignore[attr-defined]
    return stream


async def _empty():
    if False:
        yield b""


def load_ark_model_declarations(path: Path | str, root: Path | str) -> tuple[ArkModelDeclaration, ...]:
    """Load only bounded data declarations from an administrator-owned file."""
    trusted_root = Path(root).resolve(strict=False)
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(trusted_root)
        body = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(body, Mapping) or set(body) != {"models"} or not isinstance(body["models"], list) or not body["models"]:
            raise ValueError
        declarations = []
        for item in body["models"]:
            if not isinstance(item, Mapping) or set(item) != {"model_id", "service_id", "display_name", "operations", "parameter_schema", "input_ports", "parameter_mappings"}:
                raise ValueError
            model_id, service_id, display_name, operations, parameter_schema = (item["model_id"], item["service_id"], item["display_name"], item["operations"], item["parameter_schema"])
            if not all(isinstance(value, str) and 1 <= len(value) <= 128 for value in (model_id, service_id, display_name)):
                raise ValueError
            if not model_id.isascii() or not service_id.isascii() or not model_id.replace("-", "").replace("_", "").isalnum() or not service_id.replace("-", "").replace("_", "").isalnum():
                raise ValueError
            if not isinstance(operations, list) or not operations or any(value not in {"image.generate", "image.edit", "video.generate"} for value in operations):
                raise ValueError
            if not isinstance(parameter_schema, Mapping) or parameter_schema.get("type") != "object" or parameter_schema.get("additionalProperties") is not False or not isinstance(parameter_schema.get("properties"), Mapping):
                raise ValueError
            if len(parameter_schema["properties"]) > 16 or any(not isinstance(key, str) or not isinstance(rule, Mapping) or rule.get("type") not in {"string", "integer", "number", "boolean"} for key, rule in parameter_schema["properties"].items()):
                raise ValueError
            validate_parameter_schema(parameter_schema)
            raw_ports = item["input_ports"]
            raw_mappings = item["parameter_mappings"]
            if not isinstance(raw_ports, list) or not 1 <= len(raw_ports) <= 16 or not isinstance(raw_mappings, Mapping):
                raise ValueError
            ports = []
            for port in raw_ports:
                if not isinstance(port, Mapping) or set(port) - {"port_id", "media_type", "min_items", "max_items", "asset_kind"} or not {"port_id", "media_type", "min_items", "max_items"}.issubset(port):
                    raise ValueError
                asset_kind = port.get("asset_kind")
                if asset_kind is not None and asset_kind != "library":
                    raise ValueError
                ports.append(ModelInputPort(port["port_id"], port["media_type"], port["min_items"], port["max_items"], asset_kind=asset_kind))
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in raw_mappings.items()):
                raise ValueError
            declarations.append(ArkModelDeclaration(model_id, service_id, display_name, tuple(operations), parameter_schema, tuple(ports), raw_mappings))
        if len({item.model_id for item in declarations}) != len(declarations):
            raise ValueError
        return tuple(declarations)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Ark model configuration is invalid") from error


def _compile_provider_parameters(mappings: Mapping[str, str], values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in values.items():
        path = mappings[name].split(".")
        if len(path) == 1:
            result[path[0]] = value
            continue
        parent, child = path
        container = result.setdefault(parent, {})
        if not isinstance(container, dict) or child in container:
            raise ValueError("Ark parameter mapping is invalid")
        container[child] = value
    return result


def build_ark_adapters(*, api_key: str, data_dir: Path | str, config_path: Path | str, config_root: Path | str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[ArkGenerationAdapter, ...]:
    grouped: dict[str, list[ArkModelDeclaration]] = {}
    for declaration in load_ark_model_declarations(config_path, config_root):
        grouped.setdefault(declaration.service_id, []).append(declaration)
    return tuple(
        ArkGenerationAdapter(api_key=api_key, data_dir=data_dir, models=tuple(items), transport=transport, asset_loader=_local_asset_loader(Path(data_dir)), reusable_result_services=frozenset(grouped))
        for _, items in sorted(grouped.items())
    )


def _local_asset_loader(data_dir: Path) -> Callable[[str], tuple[bytes, str]]:
    database = data_dir / "canvas.sqlite3"
    assets_root = data_dir / "assets"

    def load(asset_id: str) -> tuple[bytes, str]:
        if not isinstance(asset_id, str) or not asset_id or len(asset_id) > 128:
            raise ValueError("Ark asset ID is invalid")
        if _RESULT_ID.fullmatch(asset_id):
            candidate, metadata = data_dir / "ark-results" / asset_id, data_dir / "ark-results" / f"{asset_id}.json"
            if candidate.is_symlink() or metadata.is_symlink() or not candidate.is_file() or not metadata.is_file() or not 0 < candidate.stat().st_size <= 20 * 1024 * 1024:
                raise ValueError("Ark result asset is invalid")
            try:
                mime = json.loads(metadata.read_text(encoding="utf-8"))["mime"]
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError("Ark result asset is invalid") from None
            if mime not in _IMAGE_MIME:
                raise ValueError("Ark result asset is invalid")
            return candidate.read_bytes(), mime
        if _CHIYUN_RESULT_ID.fullmatch(asset_id):
            candidate = data_dir / "chiyun-results" / asset_id
            if candidate.is_symlink() or not candidate.is_file() or not 0 < candidate.stat().st_size <= 32 * 1024 * 1024:
                raise ValueError("Chiyun result asset is invalid")
            body = candidate.read_bytes()
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Chiyun result asset is invalid")
            return body, "image/png"
        with sqlite3.connect(database) as connection:
            row = connection.execute("SELECT relative_path,mime_type,status,kind,size_bytes FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None or row[2] != "active" or row[3] != "reference" or row[1] not in _IMAGE_MIME | _AUDIO_MIME or not isinstance(row[4], int) or not 0 < row[4] <= 20 * 1024 * 1024:
            raise ValueError("Ark asset is invalid")
        relative = Path(str(row[0]))
        if len(relative.parts) != 2 or relative.parts[0] != "assets" or relative.name in {"", ".", ".."}:
            raise ValueError("Ark asset path is invalid")
        candidate = assets_root / relative.name
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size != row[4]:
            raise ValueError("Ark asset file is invalid")
        return candidate.read_bytes(), str(row[1])

    return load
