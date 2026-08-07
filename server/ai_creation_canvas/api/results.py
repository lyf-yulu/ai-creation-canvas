"""Protected generation result proxy; it never accepts a caller supplied URL."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ai_creation_canvas.api._common import context_for, problem

router = APIRouter(prefix="/api/v1")
_MAX_RESULT = 64 * 1024 * 1024
_MIME = {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"}


def _range(value: str | None, length: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise ValueError
    spec = value[6:]
    if "-" not in spec:
        raise ValueError
    left, right = spec.split("-", 1)
    if not left:
        if not right or not right.isdigit(): raise ValueError
        count = int(right)
        if count < 1: raise ValueError
        return max(0, length - count), length - 1
    if not left.isdigit() or (right and not right.isdigit()): raise ValueError
    start = int(left); end = int(right) if right else length - 1
    if start >= length or end < start: raise ValueError
    return start, min(end, length - 1)


@router.api_route("/results/{job_id}", methods=["GET", "HEAD"])
async def get_result(job_id: str, request: Request) -> Response:
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.job_for_owner(job_id, context.user.user_id)
    if forbidden:
        raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if item is None:
        raise problem(request, "JOB_NOT_FOUND", "The job was not found.", status=404)
    if item["status"] != "succeeded" or not item.get("upstream_job_id") or not item.get("result_id"):
        raise problem(request, "RESULT_UNAVAILABLE", "The generation result is unavailable.", status=404)
    try:
        adapter = request.app.state.adapter_registry.generation(str(item["service_id"]))
        fetch = getattr(adapter, "fetch_result")
        try:
            fetched = await fetch(context, str(item["upstream_job_id"]), str(item["result_id"]), request.headers.get("cookie", ""))
        except TypeError:
            fetched = await fetch(context, str(item["upstream_job_id"]), str(item["result_id"]))
        if not isinstance(fetched, tuple) or len(fetched) != 2 or not isinstance(fetched[0], (bytes, bytearray)) or fetched[1] not in _MIME:
            raise ValueError
        body, mime = bytes(fetched[0]), fetched[1]
        if len(body) > _MAX_RESULT:
            raise ValueError
    except Exception:
        raise problem(request, "RESULT_EXPIRED", "The generation result has expired.", status=404) from None
    try:
        selected = _range(request.headers.get("range"), len(body))
    except ValueError:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{len(body)}", "Accept-Ranges": "bytes"})
    headers = {"Accept-Ranges": "bytes"}
    if selected is None:
        headers["Content-Length"] = str(len(body))
        return Response(content=b"" if request.method == "HEAD" else body, media_type=mime, headers=headers)
    start, end = selected
    chunk = body[start:end + 1]
    headers.update({"Content-Range": f"bytes {start}-{end}/{len(body)}", "Content-Length": str(len(chunk))})
    return Response(content=b"" if request.method == "HEAD" else chunk, status_code=206, media_type=mime, headers=headers)
