"""Owned provider-neutral media upload, metadata, and same-origin content endpoints."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
from typing import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.domain.models import AssetRef
from ai_creation_canvas.errors import AdapterNotFoundError, PortalUpstreamError, InvalidUpstreamResult
from ai_creation_canvas.storage.sqlite import AssetQuotaExceeded

router = APIRouter(prefix="/api/v1")
_CHUNK_SIZE = 64 * 1024
_MAX_PART_HEADER_BYTES = 32 * 1024
_MAX_SINGLE_HEADER_BYTES = 8 * 1024
_MAX_PART_HEADERS = 32
_MIME_MEDIA = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "video/webm": "video",
    "audio/mpeg": "audio",
    "audio/wav": "audio",
}
_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
}
_INPUT_EXTENSIONS = {
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/webp": frozenset({".webp"}),
    "video/mp4": frozenset({".mp4"}),
    "video/webm": frozenset({".webm"}),
    "audio/mpeg": frozenset({".mp3"}),
    "audio/wav": frozenset({".wav"}),
}


class MediaUploadInvalid(MultiPartException):
    """The multipart stream declares media outside the owned upload contract."""


class MediaUploadTooLarge(MultiPartException):
    """The actual file-part bytes exceeded the MIME-specific configured limit."""


class MediaUploadHeadersTooLarge(MultiPartException):
    """Multipart header metadata exceeded its bounded parser budget."""


def _safe_upload_basename(filename: str, mime_type: str) -> str:
    if not isinstance(filename, str) or any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise MediaUploadInvalid("invalid filename")
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem, separator, suffix = basename.rpartition(".")
    if not separator or not stem or not suffix:
        raise MediaUploadInvalid("invalid filename")
    extension = f".{suffix.lower()}"
    if extension not in _INPUT_EXTENSIONS.get(mime_type, frozenset()):
        raise MediaUploadInvalid("invalid filename")
    return basename


class LimitedMediaMultiPartParser(MultiPartParser):
    """Starlette parser with file limits enforced before each spool write."""

    def __init__(self, request: Request) -> None:
        super().__init__(request.headers, request.stream(), max_files=1, max_fields=2, max_part_size=8 * 1024)
        self._request = request
        self.file_mime_type: str | None = None
        self.file_media_type: str | None = None
        self.file_name: str | None = None
        self.file_bytes = 0
        self.file_limit: int | None = None
        self._part_header_bytes = 0
        self._single_header_bytes = 0
        self._part_header_count = 0

    def on_part_begin(self) -> None:
        self._part_header_bytes = 0
        self._single_header_bytes = 0
        self._part_header_count = 0
        super().on_part_begin()

    def _count_header_fragment(self, size: int) -> None:
        self._single_header_bytes += size
        self._part_header_bytes += size
        if self._single_header_bytes > _MAX_SINGLE_HEADER_BYTES or self._part_header_bytes > _MAX_PART_HEADER_BYTES:
            raise MediaUploadHeadersTooLarge("multipart headers exceeded configured limit")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._count_header_fragment(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._count_header_fragment(end - start)
        super().on_header_value(data, start, end)

    def on_header_end(self) -> None:
        self._part_header_count += 1
        if self._part_header_count > _MAX_PART_HEADERS:
            raise MediaUploadHeadersTooLarge("multipart header count exceeded configured limit")
        self._single_header_bytes = 0
        super().on_header_end()

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        upload = self._current_part.file
        if upload is None:
            return
        mime_type = upload.content_type.lower() if isinstance(upload.content_type, str) else ""
        media_type = _MIME_MEDIA.get(mime_type)
        if media_type is None:
            raise MediaUploadInvalid("unsupported media type")
        self.file_name = _safe_upload_basename(upload.filename or "", mime_type)
        self.file_mime_type = mime_type
        self.file_media_type = media_type
        self.file_limit = _upload_limit(self._request, media_type)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            if self.file_limit is None:
                raise MediaUploadInvalid("missing file declaration")
            next_size = self.file_bytes + end - start
            if next_size > self.file_limit:
                raise MediaUploadTooLarge("file exceeded configured media limit")
            self.file_bytes = next_size
        super().on_part_data(data, start, end)

    async def parse(self) -> FormData:
        try:
            return await super().parse()
        except BaseException:
            for temporary in self._files_to_close_on_error:
                temporary.close()
            raise


def _is_valid(mime_type: str, data: bytes, tail: bytes, size: int) -> bool:
    """Apply bounded format framing checks without decoding user-controlled media."""
    if size <= 0:
        return False
    if mime_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n") and tail.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    if mime_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9")
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and int.from_bytes(data[4:8], "little") + 8 == size
    if mime_type == "video/mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp" and 8 <= int.from_bytes(data[:4], "big") <= size
    if mime_type == "video/webm":
        return data.startswith(b"\x1aE\xdf\xa3")
    if mime_type == "audio/mpeg":
        return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    if mime_type == "audio/wav":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE" and int.from_bytes(data[4:8], "little") + 8 == size
    return False


def _asset(item: dict[str, object]) -> dict[str, object]:
    asset_id = str(item["asset_id"])
    return {
        "asset_id": asset_id,
        "kind": item["kind"],
        "status": item["status"],
        "media_type": item["media_type"],
        "mime_type": item["mime_type"],
        "size_bytes": item["size_bytes"],
        "created_at": item["created_at"],
        "content_url": f"/api/v1/assets/{asset_id}/content",
    }


def _upload_limit(request: Request, media_type: str) -> int:
    return int(getattr(request.app.state.settings, f"max_{media_type}_upload_bytes"))


@router.post("/assets", status_code=201)
async def upload_asset(request: Request) -> dict[str, object]:
    context_for(request)
    async with request.app.state.upload_semaphore:
        return await _upload_asset(request)


async def _upload_asset(request: Request) -> dict[str, object]:
    context = context_for(request)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400)
    parser = LimitedMediaMultiPartParser(request)
    try:
        form = await parser.parse()
    except MediaUploadHeadersTooLarge:
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400) from None
    except MediaUploadTooLarge:
        raise problem(request, "ASSET_TOO_LARGE", "The upload is too large.", status=413) from None
    except MediaUploadInvalid:
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=415) from None
    except MultiPartException:
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400) from None
    file = form.get("file")
    kind = form.get("kind", "reference")
    media_type = form.get("media_type", "image")
    if not isinstance(file, UploadFile) or not isinstance(kind, str) or not isinstance(media_type, str):
        await form.close()
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400)
    if kind not in {"reference", "portrait"} or media_type not in {"image", "video", "audio"} or (kind == "portrait" and media_type != "image"):
        await form.close()
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400)
    mime = parser.file_mime_type or ""
    if parser.file_media_type != media_type:
        await form.close()
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=415)
    limit = _upload_limit(request, media_type)
    store = request.app.state.canvas_store
    asset_id = secrets.token_urlsafe(18)
    relative = f"assets/{secrets.token_hex(20)}{_EXTENSIONS[mime]}"
    target = store.data_dir / relative
    temporary = store.assets_dir / f".{secrets.token_hex(20)}.upload"
    size = 0
    header = bytearray()
    tail = bytearray()
    try:
        with temporary.open("xb") as output:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise problem(request, "ASSET_TOO_LARGE", "The upload is too large.", status=413)
                output.write(chunk)
                if len(header) < 64:
                    header.extend(chunk[: 64 - len(header)])
                tail.extend(chunk)
                if len(tail) > 16:
                    del tail[:-16]
        if not _is_valid(mime, bytes(header), bytes(tail), size):
            raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=415)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        if kind == "portrait":
            if not request.headers.get("cookie"):
                raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
            try:
                adapter = request.app.state.adapter_registry.asset("portal-portrait")
                upload = getattr(adapter, "upload_with_cookie", None)
                if not callable(upload):
                    raise ValueError
                upstream = await upload(context, AssetRef(asset_id, "portrait", "processing", mime), target, size, request.headers["cookie"])
            except AdapterNotFoundError:
                raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400) from None
            except PortalUpstreamError as error:
                raise problem(request, "UPSTREAM_UNAVAILABLE" if error.retryable else "REQUEST_REJECTED", "The asset service is unavailable." if error.retryable else "The request was rejected.", status=502 if error.retryable else 422, retryable=error.retryable) from None
            except InvalidUpstreamResult:
                raise problem(request, "UPSTREAM_INVALID", "The asset service returned an invalid response.", status=502) from None
            except Exception:
                raise problem(request, "UPSTREAM_UNAVAILABLE", "The asset service is unavailable.", status=502, retryable=True) from None
            item = store.create_asset(asset_id=asset_id, user_id=context.user.user_id, kind=kind, media_type=media_type, mime_type=mime, relative_path=relative, size_bytes=size, status=upstream.status.value, service_id="portal-portrait", upstream_asset_id=upstream.asset_id, user_quota_bytes=request.app.state.settings.user_asset_quota_bytes, total_quota_bytes=request.app.state.settings.total_asset_quota_bytes)
        else:
            item = store.create_asset(asset_id=asset_id, user_id=context.user.user_id, kind=kind, media_type=media_type, mime_type=mime, relative_path=relative, size_bytes=size, user_quota_bytes=request.app.state.settings.user_asset_quota_bytes, total_quota_bytes=request.app.state.settings.total_asset_quota_bytes)
        return _asset(item)
    except AssetQuotaExceeded:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise problem(request, "ASSET_QUOTA_EXCEEDED", "The asset quota has been reached.", status=413) from None
    except BaseException:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        await form.close()


def _owned_asset(asset_id: str, request: Request) -> dict[str, object]:
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.asset_for_owner(asset_id, context.user.user_id)
    if forbidden:
        raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if item is None:
        raise problem(request, "ASSET_NOT_FOUND", "The asset was not found.", status=404)
    return item


def _asset_root_descriptor(request: Request) -> int:
    store = request.app.state.canvas_store
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(store.assets_dir, root_flags)


def _stage_safe_asset(item: dict[str, object], request: Request) -> str:
    relative = Path(str(item["relative_path"]))
    if relative.parent != Path("assets") or relative.name in {"", ".", ".."}:
        raise ValueError("unsafe asset path")
    root_descriptor = _asset_root_descriptor(request)
    descriptor: int | None = None
    try:
        descriptor = os.open(relative.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_descriptor)
        opened = os.fstat(descriptor)
        current = os.stat(relative.name, dir_fd=root_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
            or opened.st_size != int(item["size_bytes"])
        ):
            raise ValueError("unsafe asset file")
        tombstone = f".{secrets.token_hex(20)}.delete"
        os.rename(relative.name, tombstone, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
        return tombstone
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_descriptor)


def _restore_staged_asset(item: dict[str, object], tombstone: object, request: Request) -> None:
    relative = Path(str(item["relative_path"]))
    if not isinstance(tombstone, str) or relative.parent != Path("assets"):
        raise ValueError("unsafe asset tombstone")
    root_descriptor = _asset_root_descriptor(request)
    try:
        os.rename(tombstone, relative.name, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
    finally:
        os.close(root_descriptor)


def _purge_staged_asset(tombstone: object, request: Request) -> None:
    if not isinstance(tombstone, str) or not tombstone.startswith(".") or not tombstone.endswith(".delete"):
        raise ValueError("unsafe asset tombstone")
    root_descriptor = _asset_root_descriptor(request)
    try:
        os.unlink(tombstone, dir_fd=root_descriptor)
    finally:
        os.close(root_descriptor)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, request: Request) -> Response:
    context = context_for(request)
    store = request.app.state.canvas_store
    try:
        outcome = store.delete_owned_local_reference_asset(
            asset_id,
            context.user.user_id,
            lambda item: _stage_safe_asset(item, request),
            lambda item, tombstone: _restore_staged_asset(item, tombstone, request),
            lambda tombstone: _purge_staged_asset(tombstone, request),
        )
    except (OSError, ValueError):
        raise problem(request, "ASSET_NOT_FOUND", "The asset was not found.", status=404) from None
    if outcome == "forbidden":
        raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if outcome == "unsupported":
        raise problem(request, "ASSET_DELETE_UNSUPPORTED", "This asset cannot be deleted here.", status=409)
    if outcome == "not_found":
        raise problem(request, "ASSET_NOT_FOUND", "The asset was not found.", status=404)
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request) -> dict[str, object]:
    item = _owned_asset(asset_id, request)
    if item["kind"] == "portrait" and item["status"] == "processing":
        if not request.headers.get("cookie"):
            raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
        try:
            adapter = request.app.state.adapter_registry.asset(str(item["service_id"]))
            get = getattr(adapter, "get_with_cookie", None)
            if not callable(get) or not isinstance(item.get("upstream_asset_id"), str):
                raise ValueError
            upstream = await get(context_for(request), item["upstream_asset_id"], request.headers["cookie"])
            item = request.app.state.canvas_store.update_asset_status(asset_id, upstream.status.value)
        except PortalUpstreamError as error:
            raise problem(request, "UPSTREAM_UNAVAILABLE" if error.retryable else "REQUEST_REJECTED", "The asset service is unavailable." if error.retryable else "The request was rejected.", status=502 if error.retryable else 422, retryable=error.retryable) from None
        except InvalidUpstreamResult:
            raise problem(request, "UPSTREAM_INVALID", "The asset service returned an invalid response.", status=502) from None
        except Exception:
            raise problem(request, "UPSTREAM_UNAVAILABLE", "The asset service is unavailable.", status=502, retryable=True) from None
    return _asset(item)


def _safe_asset_fd(item: dict[str, object], request: Request) -> tuple[int, int]:
    store = request.app.state.canvas_store
    root = store.assets_dir.resolve(strict=True)
    candidate = store.data_dir / str(item["relative_path"])
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(resolved, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != int(item["size_bytes"]):
            os.close(descriptor)
            raise ValueError
        return descriptor, info.st_size
    except (OSError, RuntimeError, ValueError):
        raise problem(request, "ASSET_NOT_FOUND", "The asset was not found.", status=404) from None


def _byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise ValueError
    left, separator, right = value[6:].partition("-")
    if not separator or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()):
        raise ValueError
    if left:
        start = int(left)
        if start >= size:
            raise ValueError
        end = min(int(right), size - 1) if right else size - 1
        if end < start:
            raise ValueError
        return start, end
    suffix = int(right)
    if suffix <= 0:
        raise ValueError
    return max(0, size - suffix), size - 1


def _stream_fd(descriptor: int, start: int, length: int) -> Iterator[bytes]:
    with os.fdopen(descriptor, "rb") as source:
        source.seek(start)
        remaining = length
        while remaining:
            chunk = source.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.api_route("/assets/{asset_id}/content", methods=["GET", "HEAD"])
async def get_asset_content(asset_id: str, request: Request):
    item = _owned_asset(asset_id, request)
    descriptor, size = _safe_asset_fd(item, request)
    try:
        selected = _byte_range(request.headers.get("range"), size)
    except ValueError:
        os.close(descriptor)
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes", "Cache-Control": "private, no-store"})
    start, end = selected or (0, size - 1)
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Length": str(length),
    }
    status_code = 206 if selected else 200
    if selected:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        os.close(descriptor)
        return Response(status_code=status_code, media_type=str(item["mime_type"]), headers=headers)
    return StreamingResponse(_stream_fd(descriptor, start, length), status_code=status_code, media_type=str(item["mime_type"]), headers=headers)
