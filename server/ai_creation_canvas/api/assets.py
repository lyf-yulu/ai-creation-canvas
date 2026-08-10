"""Owned local reference-image upload and metadata endpoints."""
from __future__ import annotations

import os
from pathlib import Path
import secrets

from fastapi import APIRouter, File, Form, Request, UploadFile

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.domain.models import AssetRef
from ai_creation_canvas.errors import AdapterNotFoundError, PortalUpstreamError, InvalidUpstreamResult

router = APIRouter(prefix="/api/v1")
_MAX_UPLOAD = 10 * 1024 * 1024
_TYPES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _is_valid(mime_type: str, data: bytes, tail: bytes, size: int) -> bool:
    """Perform cheap format framing checks while preserving streaming uploads."""
    if size <= 0:
        return False
    if mime_type == "image/png":
        return data.startswith(_TYPES[mime_type]) and tail.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    if mime_type == "image/jpeg":
        return data.startswith(_TYPES[mime_type]) and tail.endswith(b"\xff\xd9")
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and int.from_bytes(data[4:8], "little") + 8 == size
    return False


def _asset(item: dict[str, object]) -> dict[str, object]:
    return {"asset_id": item["asset_id"], "kind": item["kind"], "status": item["status"], "mime_type": item["mime_type"], "size_bytes": item["size_bytes"], "created_at": item["created_at"]}


@router.post("/assets", status_code=201)
async def upload_asset(request: Request, file: UploadFile = File(...), kind: str = Form("reference")) -> dict[str, object]:
    context = context_for(request)
    if kind not in {"reference", "portrait"}:
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400)
    mime = file.content_type.lower() if isinstance(file.content_type, str) else ""
    if mime not in _TYPES:
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=415)
    stated = request.headers.get("content-length")
    if stated and stated.isdigit() and int(stated) > _MAX_UPLOAD + 1024 * 1024:
        raise problem(request, "ASSET_TOO_LARGE", "The upload is too large.", status=413)
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
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_UPLOAD:
                    raise problem(request, "ASSET_TOO_LARGE", "The upload is too large.", status=413)
                output.write(chunk)
                if len(header) < 16:
                    header.extend(chunk[: 16 - len(header)])
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
                if not callable(upload): raise ValueError
                upstream = await upload(context, AssetRef(asset_id, "portrait", "processing", mime), target, size, request.headers["cookie"])
            except AdapterNotFoundError:
                raise problem(request, "ASSET_INVALID", "The selected asset is invalid.", status=400) from None
            except PortalUpstreamError as error:
                raise problem(request, "UPSTREAM_UNAVAILABLE", "The asset service is unavailable.", status=502 if error.retryable else 422, retryable=error.retryable) from None
            except InvalidUpstreamResult:
                raise problem(request, "UPSTREAM_INVALID", "The asset service returned an invalid response.", status=502) from None
            except Exception:
                raise problem(request, "UPSTREAM_UNAVAILABLE", "The asset service is unavailable.", status=502, retryable=True) from None
            item = store.create_asset(asset_id=asset_id, user_id=context.user.user_id, kind=kind, mime_type=mime, relative_path=relative, size_bytes=size, status=upstream.status.value, service_id="portal-portrait", upstream_asset_id=upstream.asset_id)
        else:
            item = store.create_asset(asset_id=asset_id, user_id=context.user.user_id, kind=kind, mime_type=mime, relative_path=relative, size_bytes=size)
        return _asset(item)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request) -> dict[str, object]:
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.asset_for_owner(asset_id, context.user.user_id)
    if forbidden:
        raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if item is None:
        raise problem(request, "ASSET_NOT_FOUND", "The asset was not found.", status=404)
    if item["kind"] == "portrait" and item["status"] == "processing":
        if not request.headers.get("cookie"):
            raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
        try:
            adapter = request.app.state.adapter_registry.asset(str(item["service_id"]))
            get = getattr(adapter, "get_with_cookie", None)
            if not callable(get) or not isinstance(item.get("upstream_asset_id"), str): raise ValueError
            upstream = await get(context, item["upstream_asset_id"], request.headers["cookie"])
            item = request.app.state.canvas_store.update_asset_status(asset_id, upstream.status.value)
        except PortalUpstreamError as error:
            raise problem(request, "UPSTREAM_UNAVAILABLE", "The asset service is unavailable.", status=502 if error.retryable else 422, retryable=error.retryable) from None
        except InvalidUpstreamResult:
            raise problem(request, "UPSTREAM_INVALID", "The asset service returned an invalid response.", status=502) from None
        except Exception:
            raise problem(request, "UPSTREAM_UNAVAILABLE", "The asset service is unavailable.", status=502, retryable=True) from None
    return _asset(item)
