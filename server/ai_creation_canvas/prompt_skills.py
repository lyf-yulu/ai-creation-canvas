"""Bounded, administrator-governed prompt optimization skills."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from collections.abc import Mapping

import httpx


_SKILL_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_MAX_CONFIG_BYTES = 64 * 1024
_FIELDS = {"skill_id", "title", "description", "system_instruction", "source_url", "source_commit", "license"}


@dataclass(frozen=True, slots=True)
class PromptSkill:
    skill_id: str
    title: str
    description: str
    system_instruction: str
    source_url: str
    source_commit: str
    license: str

    def public(self, available: bool) -> dict[str, object]:
        return {
            "skill_id": self.skill_id, "title": self.title, "description": self.description,
            "source_url": self.source_url, "source_commit": self.source_commit,
            "license": self.license, "available": available,
        }


def load_prompt_skills(path: Path | str, root: Path | str) -> tuple[PromptSkill, ...]:
    candidate, trusted = Path(path), Path(root).resolve(strict=False)
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(trusted)
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("prompt skills configuration is invalid") from error
    if not isinstance(raw, Mapping) or set(raw) != {"skills"} or not isinstance(raw["skills"], list) or not 1 <= len(raw["skills"]) <= 16:
        raise ValueError("prompt skills configuration is invalid")
    result: list[PromptSkill] = []
    for item in raw["skills"]:
        if not isinstance(item, Mapping) or set(item) != _FIELDS:
            raise ValueError("prompt skills configuration is invalid")
        values = {name: item[name] for name in _FIELDS}
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError("prompt skills configuration is invalid")
        if (
            not _SKILL_ID.fullmatch(values["skill_id"])
            or not 1 <= len(values["title"]) <= 80
            or not 1 <= len(values["description"]) <= 240
            or not 1 <= len(values["system_instruction"]) <= 2000
            or not values["source_url"].startswith("https://github.com/")
            or len(values["source_url"]) > 240
            or not _COMMIT.fullmatch(values["source_commit"])
            or values["license"] not in {"MIT", "Apache-2.0", "CC0-1.0"}
            or any(char in values["title"] + values["description"] for char in "<>")
        ):
            raise ValueError("prompt skills configuration is invalid")
        result.append(PromptSkill(**values))
    if len({item.skill_id for item in result}) != len(result):
        raise ValueError("prompt skills configuration is invalid")
    return tuple(result)


class PromptSkillService:
    endpoint = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

    def __init__(self, skills: tuple[PromptSkill, ...], *, model_id: str | None = None, api_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not skills:
            raise ValueError("prompt skills are required")
        if (model_id is None) != (api_key is None):
            raise ValueError("prompt skill model and key must be configured together")
        if model_id is not None and (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", model_id) or not isinstance(api_key, str) or len(api_key) < 8):
            raise ValueError("prompt skill service configuration is invalid")
        self._skills = {item.skill_id: item for item in skills}
        self._model_id, self._api_key, self._transport = model_id, api_key, transport

    @property
    def available(self) -> bool:
        return self._model_id is not None

    def catalog(self) -> tuple[dict[str, object], ...]:
        return tuple(item.public(self.available) for item in self._skills.values())

    def skill(self, skill_id: str) -> PromptSkill | None:
        return self._skills.get(skill_id)

    async def optimize(self, skill_id: str, prompt: str) -> str:
        skill = self.skill(skill_id)
        if skill is None:
            raise KeyError(skill_id)
        if not self.available or self._model_id is None or self._api_key is None:
            raise RuntimeError("unavailable")
        payload = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": skill.system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 2048,
        }
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                response = await client.post(self.endpoint, headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}, json=payload)
            if response.status_code != 200 or "application/json" not in response.headers.get("content-type", "").lower():
                raise RuntimeError
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip() or len(content) > 16000:
                raise RuntimeError
            return content.strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, RuntimeError) as error:
            raise RuntimeError("unavailable") from error
