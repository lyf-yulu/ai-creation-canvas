"""Owned generation submissions and polling."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import re
from typing import Any, Mapping

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_creation_canvas.api._common import context_for, problem
from ai_creation_canvas.domain.models import JobRequest, JobStatus, ModelSpec
from ai_creation_canvas.errors import InvalidUpstreamResult, PortalUpstreamError
from ai_creation_canvas.parameter_schema import validate_parameter_values

router = APIRouter(prefix="/api/v1")
_MAX_DEPTH = 8
_MAX_ITEMS = 64
_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_RESULT_ASSET = re.compile(r"job-result\.([A-Za-z0-9_-]{1,128})\.([0-9]{1,2})\Z")


def _result_ids(item: Mapping[str, object]) -> tuple[str, ...]:
    encoded = item.get("result_ids_json")
    if isinstance(encoded, str):
        try:
            values = json.loads(encoded)
        except ValueError:
            values = None
        if isinstance(values, list) and 1 <= len(values) <= 15 and all(isinstance(value, str) and _RESULT_ID.fullmatch(value) for value in values):
            return tuple(values)
    legacy = item.get("result_id")
    return (legacy,) if isinstance(legacy, str) and _RESULT_ID.fullmatch(legacy) else ()


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
    inputs: dict[str, list[str]] = Field(default_factory=dict)
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

    @field_validator("inputs")
    @classmethod
    def valid_inputs(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(value) > 16:
            raise ValueError("too many input ports")
        for port_id, asset_ids in value.items():
            if not port_id or len(port_id) > 64 or len(asset_ids) > 64 or any(not item or len(item) > 128 for item in asset_ids):
                raise ValueError("typed inputs are invalid")
        return value


def _hash(payload: Submission) -> str:
    canonical = {"operation": payload.operation, "model_id": payload.model_id, "prompt": payload.prompt, "params": payload.params, "asset_ids": payload.asset_ids, "inputs": payload.inputs}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate_parameters(model: ModelSpec, values: dict[str, Any]) -> None:
    schema = model.parameter_schema
    properties = schema.get("properties") if isinstance(schema, dict) else schema.get("properties", {})
    if not isinstance(properties, Mapping) or set(values) - set(properties):
        raise ValueError("parameters are not declared")
    if model.parameter_mappings and set(values) - set(model.parameter_mappings):
        raise ValueError("parameters are not mapped")
    validate_parameter_values(schema, values)


def _response(item: dict[str, object], request: Request) -> dict[str, object]:
    body: dict[str, object] = {"id": item["id"], "status": item["status"]}
    if item["status"] == "succeeded":
        body["result_url"] = f"/api/v1/results/{item['id']}"
        media_type = "video" if str(item.get("operation", "")).startswith("video.") else "image"
        body["results"] = [
            {"url": f"/api/v1/results/{item['id']}/{index}", "asset_id": f"job-result.{item['id']}.{index}", "media_type": media_type}
            for index, _ in enumerate(_result_ids(item))
        ]
    if item["status"] == "failed":
        body["error"] = {"code": item["error_code"] or "TASK_FAILED", "message": "The generation task failed.", "retryable": False, "request_id": getattr(request.state, "request_id", "request"), "phase": "generation"}
    return body


async def _poll(request: Request, context, item: dict[str, object]) -> dict[str, object]:
    if item["status"] in {"succeeded", "failed", "submitting"} or not item.get("upstream_job_id"):
        return item
    adapter = request.app.state.adapter_registry.generation(str(item["service_id"]))
    if getattr(adapter, "requires_portal_cookie", False) and not request.headers.get("cookie"):
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
    try:
        poll_with_cookie = getattr(adapter, "poll_with_cookie", None)
        if callable(poll_with_cookie):
            state = await poll_with_cookie(context, str(item["upstream_job_id"]), request.headers.get("cookie", ""))
        else:
            state = await adapter.poll(context, str(item["upstream_job_id"]))
        result_ids = tuple(result.asset_id for result in state.results)
        if state.status.value == "succeeded":
            if not result_ids or len(result_ids) > 15 or any(not _RESULT_ID.fullmatch(result_id) for result_id in result_ids):
                raise InvalidUpstreamResult("provider success result is invalid")
        return request.app.state.canvas_store._update(str(item["id"]), status=state.status.value, error_code=state.error.code if state.error else None, result_ids=result_ids or None)
    except (InvalidUpstreamResult, ValueError):
        return request.app.state.canvas_store.fail_invalid_upstream_result(
            str(item["id"]), "INVALID_UPSTREAM_RESULT"
        )
    except Exception:
        # A transient poll error is not an upstream terminal state.
        return item


@router.post("/jobs", status_code=201)
async def create_job(payload: Submission, request: Request) -> dict[str, object]:
    context = context_for(request)
    try:
        domain_request = JobRequest(payload.operation, payload.model_id, payload.prompt, payload.idempotency_key, payload.params, tuple(payload.asset_ids), payload.inputs)
    except ValueError:
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.") from None
    try:
        model = await request.app.state.model_catalog.resolve_model(context, domain_request.model_id, cookie_header=request.headers.get("cookie"))
    except ValueError:
        raise problem(request, "MODEL_UNAVAILABLE", "The selected model is unavailable.", status=400) from None
    if domain_request.operation not in model.operations:
        raise problem(request, "MODEL_UNAVAILABLE", "The selected model is unavailable.", status=400)
    try:
        _validate_parameters(model, payload.params)
    except (TypeError, ValueError):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=422) from None
    selected_adapter = request.app.state.adapter_registry.generation(model.service_id)
    reusable_result_services = getattr(selected_adapter, "reusable_result_services", frozenset({model.service_id}))
    if getattr(selected_adapter, "requires_portal_cookie", False) and not request.headers.get("cookie"):
        raise problem(request, "AUTH_REQUIRED", "Sign in is required.", status=401)
    store = request.app.state.canvas_store
    upstream_asset_ids: list[str] = []
    upstream_inputs: dict[str, tuple[str, ...]] = {}
    declared_ports = {port.port_id: port for port in model.input_ports if port.media_type != "text"}
    if payload.inputs and (payload.asset_ids or set(payload.inputs) - set(declared_ports)):
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=422)
    for port_id, asset_ids in payload.inputs.items():
        port = declared_ports[port_id]
        if not port.min_items <= len(asset_ids) <= port.max_items:
            raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=422)
    typed_asset_ids = [(port_id, asset_id) for port_id, asset_ids in payload.inputs.items() for asset_id in asset_ids]
    legacy_asset_ids = [("legacy", asset_id) for asset_id in domain_request.asset_ids]
    for port_id, asset_id in (*typed_asset_ids, *legacy_asset_ids):
        asset, forbidden = store.asset_for_owner(asset_id, context.user.user_id)
        if asset is None and not forbidden:
            matched = _RESULT_ASSET.fullmatch(asset_id)
            if matched is not None:
                source, source_forbidden = store.job_for_owner(matched.group(1), context.user.user_id)
                source_results = _result_ids(source or {})
                index = int(matched.group(2))
                expected_media = "video" if source and str(source.get("operation", "")).startswith("video.") else "image"
                if source_forbidden:
                    forbidden = True
                elif source and source.get("status") == "succeeded" and source.get("service_id") in reusable_result_services and index < len(source_results):
                    asset = {"status": "active", "kind": "reference", "media_type": expected_media, "resolved_result_id": source_results[index]}
        if forbidden:
            raise problem(request, "FORBIDDEN", "You do not have access to this resource.", status=403)
        if asset is None or asset["status"] != "active":
            raise problem(request, "ASSET_INVALID", "The selected asset is invalid.")
        if model.requires_asset_kind is not None:
            if asset["kind"] != model.requires_asset_kind.value or asset.get("service_id") != model.service_id or not isinstance(asset.get("upstream_asset_id"), str):
                raise problem(request, "ASSET_INVALID", "The selected asset is invalid.")
            upstream_asset_ids.append(asset["upstream_asset_id"])
        elif asset["kind"] != "reference":
            raise problem(request, "ASSET_INVALID", "The selected asset is invalid.")
        else:
            upstream_asset_ids.append(str(asset.get("resolved_result_id") or asset_id))
        if port_id != "legacy":
            port = declared_ports[port_id]
            if asset.get("media_type") != port.media_type:
                raise problem(request, "ASSET_INVALID", "The selected asset is invalid.")
    cursor = 0
    for port_id, asset_ids in payload.inputs.items():
        upstream_inputs[port_id] = tuple(upstream_asset_ids[cursor:cursor + len(asset_ids)])
        cursor += len(asset_ids)
    if model.requires_asset_kind is not None and not upstream_asset_ids:
        raise problem(request, "ASSET_INVALID", "The selected asset is invalid.")
    reservation = store.reserve_job(user_id=context.user.user_id, job_id=secrets.token_urlsafe(18), service_id=model.service_id, operation=domain_request.operation.value, idempotency_key=domain_request.idempotency_key, request_hash=_hash(payload))
    if reservation.conflict:
        raise problem(request, "IDEMPOTENCY_CONFLICT", "The idempotency key was already used for a different request.", status=409)
    if not reservation.created:
        return _response(reservation.job, request)
    try:
        adapter = selected_adapter
        submit_with_cookie = getattr(adapter, "submit_with_cookie", None)
        if callable(submit_with_cookie):
            upstream_request = JobRequest(domain_request.operation, domain_request.model_id, domain_request.prompt, domain_request.idempotency_key, domain_request.params, tuple(upstream_asset_ids) if not upstream_inputs else (), upstream_inputs)
            upstream = await submit_with_cookie(context, upstream_request, request.headers.get("cookie", ""))
        else:
            upstream = await adapter.submit(context, JobRequest(domain_request.operation, domain_request.model_id, domain_request.prompt, domain_request.idempotency_key, domain_request.params, tuple(upstream_asset_ids) if not upstream_inputs else (), upstream_inputs))
        item = store.mark_submitted(str(reservation.job["id"]), upstream.upstream_job_id, upstream.state.status.value, str(reservation.job["submission_token"]))
        return _response(item, request)
    except PortalUpstreamError as error:
        if error.retryable:
            store.fail_reservation(str(reservation.job["id"]), "TASK_FAILED", str(reservation.job["submission_token"]))
            raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation service is unavailable.", status=502, retryable=True)
        store.mark_failed(str(reservation.job["id"]), "REQUEST_REJECTED", str(reservation.job["submission_token"]))
        raise problem(request, "REQUEST_REJECTED", "The request was rejected.", status=422)
    except asyncio.CancelledError:
        store.fail_reservation(str(reservation.job["id"]), "TASK_FAILED", str(reservation.job["submission_token"]))
        raise
    except InvalidUpstreamResult:
        store.fail_reservation(str(reservation.job["id"]), "TASK_FAILED", str(reservation.job["submission_token"]))
        raise problem(request, "UPSTREAM_INVALID", "The generation service returned an invalid response.", status=502) from None
    except Exception:
        store.fail_reservation(str(reservation.job["id"]), "TASK_FAILED", str(reservation.job["submission_token"]))
        raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation service is unavailable.", status=502, retryable=True)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, object]:
    context = context_for(request)
    item, forbidden = request.app.state.canvas_store.job_for_owner(job_id, context.user.user_id)
    if forbidden:
        raise problem(request, "JOB_NOT_FOUND", "The job was not found.", status=404)
    if item is None:
        raise problem(request, "JOB_NOT_FOUND", "The job was not found.", status=404)
    return _response(await _poll(request, context, item), request)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict[str, object]:
    context = context_for(request)
    store = request.app.state.canvas_store
    item, forbidden = store.job_for_owner(job_id, context.user.user_id)
    if forbidden or item is None:
        raise problem(request, "JOB_NOT_FOUND", "The job was not found.", status=404)
    if item["status"] == "failed" and item.get("error_code") == "TASK_CANCELLED":
        return _response(item, request)
    if item["status"] == "running":
        raise problem(request, "JOB_NOT_CANCELLABLE", "A running provider task cannot be cancelled.", status=409)
    if item["status"] != "queued" or not item.get("upstream_job_id"):
        raise problem(request, "JOB_NOT_CANCELLABLE", "The job cannot be cancelled.", status=409)
    adapter = request.app.state.adapter_registry.generation(str(item["service_id"]))
    cancel = getattr(adapter, "cancel", None)
    if not callable(cancel):
        raise problem(request, "JOB_NOT_CANCELLABLE", "The job cannot be cancelled.", status=409)
    try:
        await cancel(context, str(item["upstream_job_id"]))
    except PortalUpstreamError as error:
        if not error.retryable:
            raise problem(request, "JOB_NOT_CANCELLABLE", "The job cannot be cancelled.", status=409) from None
        raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation service is unavailable.", status=502, retryable=True) from None
    except Exception:
        raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation service is unavailable.", status=502, retryable=True) from None
    return _response(store.mark_cancelled(job_id), request)
