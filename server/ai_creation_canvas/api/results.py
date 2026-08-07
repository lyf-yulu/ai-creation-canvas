"""Protected streaming proxy for opaque provider result identifiers."""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from ai_creation_canvas.api._common import context_for, problem

router = APIRouter(prefix="/api/v1")
_MAX = 64 * 1024 * 1024
_MIME = {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"}

class ResultStreamProtocolError(RuntimeError): pass

def parse_range_header(value: str | None) -> tuple[str, int | None, int | None] | None:
    """Validate the single-byte-range grammar before contacting the provider."""
    if value is None: return None
    if not value.startswith("bytes=") or "," in value: raise ValueError("invalid range")
    left, sep, right = value[6:].partition("-")
    if not sep or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()):
        raise ValueError("invalid range")
    if left and right and int(right) < int(left): raise ValueError("invalid range")
    return ("suffix" if not left else "range", int(left) if left else None, int(right) if right else None)

def _content_length(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal() or (len(value) > 1 and value.startswith("0")):
        raise ValueError("invalid content length")
    result = int(value)
    if result > _MAX: raise ValueError("content length exceeds maximum")
    return result

@router.api_route("/results/{job_id}", methods=["GET", "HEAD"])
async def get_result(job_id: str, request: Request):
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.job_for_owner(job_id, context.user.user_id)
    if forbidden: raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if item is None or item["status"] != "succeeded" or not item.get("result_id") or not item.get("upstream_job_id"):
        raise problem(request, "RESULT_UNAVAILABLE", "The generation result is unavailable.", status=404)
    adapter = request.app.state.adapter_registry.generation(str(item["service_id"]))
    open_result = getattr(adapter, "open_result", None)
    if not callable(open_result): raise problem(request, "RESULT_EXPIRED", "The generation result has expired.", status=404)
    range_header = request.headers.get("range")
    try: parse_range_header(range_header)
    except ValueError:
        raise problem(request, "RANGE_NOT_SATISFIABLE", "The requested range is invalid.", status=416)
    try:
        stream = await open_result(context, str(item["result_id"]), cookie_header=request.headers.get("cookie", ""), range_header=range_header, head=request.method == "HEAD")
        length = stream.headers.get("content-length")
        if stream.status_code == 416:
            content_range = stream.headers.get("content-range", "")
            await stream.aclose()
            if not content_range.startswith("bytes */") or not content_range[8:].isdigit(): raise ValueError
            return Response(status_code=416, headers={"Content-Range": content_range, "Accept-Ranges": "bytes"})
        content_type = stream.headers.get("content-type", "").split(";", 1)[0].lower()
        declared = _content_length(length)
        if content_type not in _MIME:
            await stream.aclose(); raise ValueError
        if range_header and stream.status_code != 206: await stream.aclose(); return Response(status_code=416, headers={"Accept-Ranges":"bytes"})
        if not range_header and stream.status_code != 200: await stream.aclose(); raise ValueError
    except Exception:
        raise problem(request, "RESULT_EXPIRED", "The generation result has expired.", status=404) from None
    headers = {key.title(): value for key, value in stream.headers.items() if key.lower() in {"content-length", "content-range", "accept-ranges", "etag"}}
    if request.method == "HEAD":
        await stream.aclose(); return Response(status_code=stream.status_code, media_type=content_type, headers=headers)
    iterator = stream.aiter_bytes()
    try:
        first = await anext(iterator)
        if len(first) > declared or len(first) > _MAX:
            await stream.aclose(); raise ValueError("first chunk exceeds content length")
    except StopAsyncIteration:
        await stream.aclose(); raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation result is unavailable.", status=502, retryable=True) from None
    except Exception:
        await stream.aclose(); raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation result is unavailable.", status=502, retryable=True) from None
    async def body():
        total = len(first)
        try:
            yield first
            async for chunk in iterator:
                total += len(chunk)
                if total > _MAX or total > declared: raise ResultStreamProtocolError("upstream length mismatch")
                yield chunk
            if total != declared: raise ResultStreamProtocolError("upstream length mismatch")
        finally: await stream.aclose()
    return StreamingResponse(body(), status_code=stream.status_code, media_type=content_type, headers=headers)
