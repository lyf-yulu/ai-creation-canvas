"""Administrator-only log file browsing and export."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ai_creation_canvas.api._common import problem
from ai_creation_canvas.api.admin import _require_admin
from ai_creation_canvas.logging_setup import logs_dir

router = APIRouter(prefix="/api/v1/admin/logs")

_LOG_NAME_RE = re.compile(r"server\.log(?:\.\d{1,4})?\Z")
_LOG_HEADER_RE = re.compile(r"^\S+ (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_TAIL_BUDGET_BYTES = 4 * 1024 * 1024
_TAIL_CHUNK_BYTES = 64 * 1024
_FILES_MAX_ENTRIES = 20

_NOT_FOUND = "The requested API resource was not found."


def _log_file_name_valid(name: str) -> bool:
    return isinstance(name, str) and len(name) <= 64 and _LOG_NAME_RE.fullmatch(name) is not None


def _resolve_log_file(request: Request, name: str) -> Path:
    if not _log_file_name_valid(name):
        raise problem(request, "API_NOT_FOUND", _NOT_FOUND, status=404)
    root = logs_dir(request.app.state.settings.data_dir).resolve(strict=False)
    candidate = root / name
    try:
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise problem(request, "API_NOT_FOUND", _NOT_FOUND, status=404)
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise problem(request, "API_NOT_FOUND", _NOT_FOUND, status=404) from None
    return resolved


def _tail(path: Path, lines: int) -> tuple[list[str], bool]:
    """Read up to ``lines`` trailing lines without loading the whole file."""
    data = b""
    with open(path, "rb") as handle:
        pos = handle.seek(0, os.SEEK_END)
        while pos > 0 and data.count(b"\n") < lines and len(data) < _TAIL_BUDGET_BYTES:
            take = min(_TAIL_CHUNK_BYTES, pos)
            pos -= take
            handle.seek(pos)
            data = handle.read(take) + data
    text = data.decode("utf-8", "replace")
    parts = text.split("\n")
    if pos > 0 and parts:
        parts.pop(0)  # first element is a mid-line fragment
    if parts and parts[-1] == "":
        parts.pop()
    truncated = len(data) >= _TAIL_BUDGET_BYTES and data.count(b"\n") < lines
    return parts[-lines:], truncated


def _filter_lines(window: list[str], level: str | None, keyword: str | None) -> list[str]:
    minimum = _LEVEL_ORDER[level] if level else 0
    result = []
    for line in window:
        match = _LOG_HEADER_RE.match(line)
        severity = _LEVEL_ORDER[match.group(1)] if match else None
        if minimum and severity is None:
            continue
        if minimum and severity < minimum:
            continue
        if keyword and keyword.lower() not in line.lower():
            continue
        result.append(line)
    return result


@router.get("/files")
async def log_files(request: Request) -> dict[str, object]:
    _require_admin(request)
    root = logs_dir(request.app.state.settings.data_dir)
    entries = []
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in names:
        if not _log_file_name_valid(name):
            continue
        try:
            info = (root / name).lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        entries.append({"name": name, "size": info.st_size, "mtime": info.st_mtime})
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return {"files": entries[:_FILES_MAX_ENTRIES]}


@router.get("/content")
async def log_content(
    request: Request,
    file: str = Query(),
    lines: int = Query(default=500, ge=1, le=2000),
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
) -> dict[str, object]:
    _require_admin(request)
    path = _resolve_log_file(request, file)
    try:
        window, truncated = _tail(path, lines)
    except OSError:
        raise problem(request, "API_NOT_FOUND", _NOT_FOUND, status=404) from None
    return {
        "file": file,
        "lines": lines,
        "window_total": len(window),
        "truncated": truncated,
        "log_lines": _filter_lines(window, level, q),
    }


@router.get("/download")
async def log_download(request: Request, file: str = Query()) -> Response:
    _require_admin(request)
    path = _resolve_log_file(request, file)
    try:
        snapshot = path.read_bytes()
    except OSError:
        raise problem(request, "API_NOT_FOUND", _NOT_FOUND, status=404) from None
    # Snapshot into memory (bounded by rotation, ~10 MB): the log grows while
    # being served because every request appends access lines, so a stat-time
    # content-length on a streaming FileResponse under-counts the body and
    # h11 aborts the transfer mid-stream.
    return Response(
        content=snapshot,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file}"'},
    )
