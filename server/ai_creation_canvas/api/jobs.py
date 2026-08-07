"""Owned generation submissions and polling."""
from __future__ import annotations

import hashlib
import json
import secrets
import re
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.domain.models import JobRequest, JobStatus

router = APIRouter(prefix="/api/v1")
_MAX_DEPTH = 8
_MAX_ITEMS = 64
_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _bounded(value: object, depth: int = 0) -> bool:
    if depth > _MAX_DEPTH:
        return False
    if value is None or isinstance(value, (bool, int, float, str)):
        return not isinstance(value, str) or len(value) <= 4096
    if isinstance(value, list):
        return len(value) <= _MAX_ITEMS and all(_bounded(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= _MAX_ITEMS and all(isinstance(key, str) and len(key) <= 128 and _bounded(item, depth + 1) for key, item in value.items())
    return False


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: str = Field(max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=16000)
    params: dict[str, Any] = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list, max_length=32)
    idempotency_key: str = Field(min_length=1, max_length=256)

    @field_validator("params")
    @classmethod
    def valid_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not _bounded(value):
            raise ValueError("params are too complex")
        return value

    @field_validator("asset_ids")
    @classmethod
    def valid_assets(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 128 for item in value):
            raise ValueError("asset IDs are invalid")
        return value


def _hash(payload: Submission) -> str:
    canonical = {"operation": payload.operation, "model_id": payload.model_id, "prompt": payload.prompt, "params": payload.params, "asset_ids": payload.asset_ids}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _response(item: dict[str, object], request: Request) -> dict[str, object]:
    body: dict[str, object] = {"id": item["id"], "status": item["status"]}
    if item["status"] == "succeeded":
        body["result_url"] = f"/api/v1/results/{item['id']}"
    if item["status"] == "failed":
        body["error"] = {"code": item["error_code"] or "TASK_FAILED", "message": "The generation task failed.", "retryable": False, "request_id": getattr(request.state, "request_id", "request"), "phase": "generation"}
    return body


async def _poll(request: Request, context, item: dict[str, object]) -> dict[str, object]:
    if item["status"] in {"succeeded", "failed", "submitting"} or not item.get("upstream_job_id"):
        return item
    try:
        adapter = request.app.state.adapter_registry.generation(str(item["service_id"]))
        poll_with_cookie = getattr(adapter, "poll_with_cookie", None)
        if callable(poll_with_cookie):
            state = await poll_with_cookie(context, str(item["upstream_job_id"]), request.headers.get("cookie", ""))
        else:
            state = await adapter.poll(context, str(item["upstream_job_id"]))
        result_id = None
        if state.result is not None:
            result_id = state.result.asset_id
            if not _RESULT_ID.fullmatch(result_id):
                return request.app.state.canvas_store.fail_reservation(str(item["id"]), "TASK_FAILED")
        return request.app.state.canvas_store._update(str(item["id"]), status=state.status.value, error_code=state.error.code if state.error else None, result_id=result_id)
    except Exception:
        # A transient poll error is not an upstream terminal state.
        return item


@router.post("/jobs", status_code=201)
async def create_job(payload: Submission, request: Request) -> dict[str, object]:
    context = context_for(request)
    if not request.headers.get("cookie") and any(hasattr(adapter, "submit_with_cookie") for adapter in request.app.state.adapter_registry.generation_adapters()):
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
    try:
        domain_request = JobRequest(payload.operation, payload.model_id, payload.prompt, payload.idempotency_key, payload.params, tuple(payload.asset_ids))
    except ValueError:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.") from None
    catalog = await request.app.state.model_catalog.list_models(context, cookie_header=request.headers.get("cookie"))
    matches = [model for model in catalog.models if model.model_id == domain_request.model_id]
    if len(matches) != 1 or domain_request.operation not in matches[0].operations:
        raise problem(request, "MODEL_UNAVAILABLE", "The selected model is unavailable.", status=400)
    store = request.app.state.canvas_store
    for asset_id in domain_request.asset_ids:
        asset, forbidden = store.asset_for_owner(asset_id, context.user.user_id)
        if forbidden:
            raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
        if asset is None or asset["status"] != "active" or asset["kind"] != "reference":
            raise problem(request, "ASSET_INVALID", "The selected asset is invalid.")
    reservation = store.reserve_job(user_id=context.user.user_id, job_id=secrets.token_urlsafe(18), service_id=matches[0].service_id, operation=domain_request.operation.value, idempotency_key=domain_request.idempotency_key, request_hash=_hash(payload))
    if reservation.conflict:
        raise problem(request, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used for a different request.", status=409)
    if not reservation.created:
        return _response(reservation.job, request)
    try:
        adapter = request.app.state.adapter_registry.generation(matches[0].service_id)
        submit_with_cookie = getattr(adapter, "submit_with_cookie", None)
        if callable(submit_with_cookie):
            upstream = await submit_with_cookie(context, domain_request, request.headers.get("cookie", ""))
        else:
            upstream = await adapter.submit(context, domain_request)
        item = store.mark_submitted(str(reservation.job["id"]), upstream.upstream_job_id, upstream.state.status.value, str(reservation.job["submission_token"]))
        return _response(item, request)
    except Exception:
        item = store.fail_reservation(str(reservation.job["id"]), "TASK_FAILED", str(reservation.job["submission_token"]))
        return _response(item, request)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, object]:
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.job_for_owner(job_id, context.user.user_id)
    if forbidden:
        raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
    if item is None:
        raise problem(request, "JOB_NOT_FOUND", "The job was not found.", status=404)
    return _response(await _poll(request, context, item), request)
