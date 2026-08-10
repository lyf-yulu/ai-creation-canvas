"""Protected streaming proxy for opaque provider result identifiers."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.errors import DomainError

router = APIRouter(prefix="/api/v1")
_MAX = 64 * 1024 * 1024
_MIME = {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"}

class ResultStreamProtocolError(RuntimeError): pass


def _upstream_status_problem(request: Request, status_code: int):
    """Expose status-class retryability without forwarding provider details."""
    retryable = status_code in {408, 425, 429} or 500 <= status_code <= 599
    return problem(
        request,
        "UPSTREAM_UNAVAILABLE",
        "The generation result is unavailable.",
        status=502,
        retryable=retryable,
    )

def parse_range_header(value: str | None) -> tuple[str, int | None, int | None] | None:
    """Validate the single-byte-range grammar before contacting the provider."""
    if value is None: return None
    if not value.startswith("bytes=") or "," in value: raise ValueError("invalid range")
    left, sep, right = value[6:].partition("-")
    if not sep or (not left and not right) or (left and not left.isdecimal()) or (right and not right.isdecimal()):
        raise ValueError("invalid range")
    if (not left and right == "0") or (left and right and int(right) < int(left)): raise ValueError("invalid range")
    return ("suffix" if not left else "range", int(left) if left else None, int(right) if right else None)

def _content_length(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal() or (len(value) > 1 and value.startswith("0")):
        raise ValueError("invalid content length")
    result = int(value)
    if result > _MAX: raise ValueError("content length exceeds maximum")
    return result


def validate_partial_response(
    requested: tuple[str, int | None, int | None], content_range: str | None, declared_length: int
) -> None:
    """Ensure a 206 response represents precisely the range requested by the client."""
    if not isinstance(content_range, str) or not content_range.startswith("bytes "):
        raise ValueError("partial response has an invalid Content-Range")
    interval, slash, total_text = content_range[6:].partition("/")
    start_text, dash, end_text = interval.partition("-")
    if (
        not slash
        or not dash
        or not start_text.isdecimal()
        or not end_text.isdecimal()
        or not total_text.isdecimal()
    ):
        raise ValueError("partial response has an invalid Content-Range")
    start, end, total = int(start_text), int(end_text), int(total_text)
    if total <= 0 or start > end or end >= total or end - start + 1 != declared_length:
        raise ValueError("partial response does not match its declared length")
    kind, requested_start, requested_end = requested
    if kind == "suffix":
        assert requested_start is None and requested_end is not None
        if end != total - 1 or start != max(0, total - requested_end):
            raise ValueError("partial response does not match the requested range")
        return
    assert requested_start is not None
    expected_end = min(requested_end if requested_end is not None else total - 1, total - 1)
    if start != requested_start or end != expected_end:
        raise ValueError("partial response does not match the requested range")


async def _close(stream) -> None:
    """Release an upstream stream even when validation or request cancellation interrupts us."""
    task = asyncio.create_task(stream.aclose())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
        raise

@router.api_route("/results/{job_id}", methods=["GET", "HEAD"])
async def get_result(job_id: str, request: Request):
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.job_for_owner(job_id, context.user.user_id)
    if forbidden: raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if item is None or item["status"] != "succeeded" or not item.get("result_id") or not item.get("upstream_job_id"):
        raise problem(request, "RESULT_UNAVAILABLE", "The generation result is unavailable.", status=404)
    adapter = request.app.state.adapter_registry.generation(str(item["service_id"]))
    if getattr(adapter, "requires_portal_cookie", False) and not request.headers.get("cookie"):
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
    open_result = getattr(adapter, "open_result", None)
    if not callable(open_result): raise problem(request, "RESULT_EXPIRED", "The generation result has expired.", status=404)
    range_header = request.headers.get("range")
    try: requested_range = parse_range_header(range_header)
    except ValueError:
        raise problem(request, "RANGE_NOT_SATISFIABLE", "The requested range is invalid.", status=416)
    stream = None
    try:
        stream = await open_result(context, str(item["result_id"]), cookie_header=request.headers.get("cookie", ""), range_header=range_header, head=request.method == "HEAD")
        length = stream.headers.get("content-length")
        if stream.status_code == 416:
            content_range = stream.headers.get("content-range", "")
            await _close(stream)
            stream = None
            if requested_range is None:
                raise _upstream_status_problem(request, 416)
            if not content_range.startswith("bytes */") or not content_range[8:].isdigit(): raise ValueError
            return Response(status_code=416, headers={"Content-Range": content_range, "Accept-Ranges": "bytes"})
        if stream.status_code == 404:
            await _close(stream)
            stream = None
            raise problem(request, "RESULT_EXPIRED", "The generation result has expired.", status=404, phase="generation")
        expected_status = 206 if requested_range is not None else 200
        if stream.status_code != expected_status:
            status_code = stream.status_code
            await _close(stream)
            stream = None
            raise _upstream_status_problem(request, status_code)
        content_type = stream.headers.get("content-type", "").split(";", 1)[0].lower()
        declared = _content_length(length)
        if content_type not in _MIME:
            raise ValueError
        if stream.headers.get("content-encoding", "identity").lower() != "identity": raise ValueError
        if stream.status_code == 206:
            assert requested_range is not None
            validate_partial_response(requested_range, stream.headers.get("content-range"), declared)
    except asyncio.CancelledError:
        if stream is not None: await _close(stream)
        raise
    except DomainError:
        raise
    except Exception:
        if stream is not None: await _close(stream)
        raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation result is unavailable.", status=502, retryable=True) from None
    headers = {key.title(): value for key, value in stream.headers.items() if key.lower() in {"content-length", "content-range", "accept-ranges", "etag"}}
    if request.method == "HEAD":
        await _close(stream); return Response(status_code=stream.status_code, media_type=content_type, headers=headers)
    iterator = stream.aiter_bytes()
    try:
        first = await anext(iterator)
        if len(first) > declared or len(first) > _MAX:
            await _close(stream); raise ValueError("first chunk exceeds content length")
    except StopAsyncIteration:
        await _close(stream); raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation result is unavailable.", status=502, retryable=True) from None
    except asyncio.CancelledError:
        await _close(stream); raise
    except Exception:
        await _close(stream); raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation result is unavailable.", status=502, retryable=True) from None
    async def body():
        total = len(first)
        try:
            yield first
            async for chunk in iterator:
                total += len(chunk)
                if total > _MAX or total > declared: raise ResultStreamProtocolError("upstream length mismatch")
                yield chunk
            if total != declared: raise ResultStreamProtocolError("upstream length mismatch")
        finally: await _close(stream)
    return StreamingResponse(body(), status_code=stream.status_code, media_type=content_type, headers=headers)
