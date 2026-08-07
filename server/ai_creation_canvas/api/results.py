"""Protected streaming proxy for opaque provider result identifiers."""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from ai_creation_canvas.api._common import context_for, problem

router = APIRouter(prefix="/api/v1")
_MAX = 64 * 1024 * 1024
_MIME = {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"}

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
    if range_header and (not range_header.startswith("bytes=") or "," in range_header):
        return Response(status_code=416, headers={"Content-Range": "bytes */*", "Accept-Ranges": "bytes"})
    try:
        stream = await open_result(context, str(item["result_id"]), cookie_header=request.headers.get("cookie", ""), range_header=range_header, head=request.method == "HEAD")
        content_type = stream.headers.get("content-type", "").split(";", 1)[0].lower()
        length = stream.headers.get("content-length")
        if content_type not in _MIME or (length and (not length.isdigit() or int(length) > _MAX)):
            await stream.aclose(); raise ValueError
        if range_header and stream.status_code not in {206, 416}: await stream.aclose(); return Response(status_code=416, headers={"Accept-Ranges":"bytes"})
        if not range_header and stream.status_code != 200: await stream.aclose(); raise ValueError
    except Exception:
        raise problem(request, "RESULT_EXPIRED", "The generation result has expired.", status=404) from None
    headers = {key.title(): value for key, value in stream.headers.items() if key.lower() in {"content-length", "content-range", "accept-ranges", "etag"}}
    if request.method == "HEAD":
        await stream.aclose(); return Response(status_code=stream.status_code, media_type=content_type, headers=headers)
    async def body():
        total = 0
        try:
            async for chunk in stream.aiter_bytes():
                total += len(chunk)
                if total > _MAX: break
                yield chunk
        finally: await stream.aclose()
    return StreamingResponse(body(), status_code=stream.status_code, media_type=content_type, headers=headers)
