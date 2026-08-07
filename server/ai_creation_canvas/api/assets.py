"""Owned local reference-image upload and metadata endpoints."""
from __future__ import annotations

import os
from pathlib import Path
import secrets

from fastapi import APIRouter, File, Form, Request, UploadFile

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.storage.sqlite import CanvasStore

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
    if kind != "reference":
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
    return _asset(item)
