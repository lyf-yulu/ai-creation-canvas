"""Current owner's charged generation usage."""

from __future__ import annotations

from typing import Iterable, Mapping

from fastapi import APIRouter, Request

from ai_creation_canvas.api._common import context_for
from ai_creation_canvas.storage.sqlite import CanvasStore


router = APIRouter(prefix="/api/v1/usage")
_WIRE_MONEY_FIELDS = ("video_price_fen", "image_price_fen", "cost_fen")


def usage_summary(jobs: Iterable[Mapping[str, object]]) -> dict[str, object]:
    successful_jobs = 0
    image_count = 0
    video_seconds = 0
    total_cost_fen = 0
    for job in jobs:
        if job["status"] == "succeeded":
            successful_jobs += 1
        image_count += int(job["image_count"])
        video_seconds += int(job["video_seconds"])
        total_cost_fen += int(job["cost_fen"])
    return {
        "successful_jobs": successful_jobs,
        "image_count": image_count,
        "video_seconds": video_seconds,
        "total_cost_fen": str(total_cost_fen),
    }


def _wire_job(job: Mapping[str, object]) -> dict[str, object]:
    wire_job = dict(job)
    for field in _WIRE_MONEY_FIELDS:
        wire_job[field] = str(int(job[field]))
    return wire_job


def owner_usage_projection(store: CanvasStore, user_id: str) -> dict[str, object]:
    usage = store.usage_for_owner(user_id)
    jobs = usage["jobs"]
    return {"summary": usage_summary(jobs), "jobs": tuple(_wire_job(job) for job in jobs)}


def all_usage_projection(store: CanvasStore) -> dict[str, object]:
    users: list[dict[str, object]] = []
    jobs: list[dict[str, object]] = []
    for usage in store.usage_for_all_users():
        user_id = str(usage["user_id"])
        owner_jobs = usage["jobs"]
        users.append({"user_id": user_id, "summary": usage_summary(owner_jobs)})
        jobs.extend({"user_id": user_id, **_wire_job(job)} for job in owner_jobs)
    return {"summary": usage_summary(jobs), "users": users, "jobs": jobs}


@router.get("")
async def usage(request: Request) -> dict[str, object]:
    return owner_usage_projection(request.app.state.canvas_store, context_for(request).user.user_id)
