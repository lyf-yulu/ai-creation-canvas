"""Same-origin prompt skill catalog and optimization endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ai_creation_canvas.api._common import problem
from ai_creation_canvas.prompt_skills import PromptSkillService


router = APIRouter(prefix="/api/v1/prompt-skills")


class OptimizePromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=8000)


@router.get("")
async def catalog(request: Request) -> dict[str, object]:
    service: PromptSkillService = request.app.state.prompt_skill_service
    return {"skills": service.catalog()}


@router.post("/{skill_id}/optimize")
async def optimize(skill_id: str, payload: OptimizePromptRequest, request: Request) -> dict[str, str]:
    service: PromptSkillService = request.app.state.prompt_skill_service
    if service.skill(skill_id) is None:
        raise problem(request, "SKILL_NOT_FOUND", "The selected prompt skill is unavailable.", status=404)
    if not payload.prompt.strip():
        raise problem(request, "REQUEST_REJECTED", "The prompt cannot be empty.", status=422)
    try:
        optimized = await service.optimize(skill_id, payload.prompt)
    except RuntimeError:
        raise problem(request, "SKILL_SERVICE_UNAVAILABLE", "Prompt optimization is not enabled by the administrator.", status=503, retryable=True) from None
    return {"skill_id": skill_id, "optimized_prompt": optimized}
