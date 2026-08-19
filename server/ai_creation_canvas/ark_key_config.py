"""Strict, server-only configuration for the Ark generation API key."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


_MAX_CONFIG_BYTES = 16 * 1024


def _invalid_configuration() -> ValueError:
    return ValueError("ark key configuration is invalid")


class _ArkKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int
    api_key: str = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def validate_document(self):
        if self.version != 1:
            raise _invalid_configuration()
        value = self.api_key
        if value != value.strip() or not value.isprintable() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise _invalid_configuration()
        return self


@dataclass(frozen=True, slots=True)
class ArkKeyConfig:
    api_key: str


def parse_ark_key_config_json(raw: bytes) -> ArkKeyConfig:
    if not raw or len(raw) > _MAX_CONFIG_BYTES:
        raise _invalid_configuration()
    try:
        document = json.loads(raw.decode("utf-8").removeprefix("\ufeff"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid_configuration() from error
    if not isinstance(document, dict):
        raise _invalid_configuration()
    parsed = _ArkKeyInput.model_validate(document)
    return ArkKeyConfig(api_key=parsed.api_key)


class ArkKeyConfigLoader:
    """Re-reads the key file on every access so a web import takes effect immediately."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def current_key(self) -> str | None:
        """Return the imported key, or None while no key file has been imported yet."""
        try:
            raw = self._path.read_bytes()
        except OSError:
            return None
        return parse_ark_key_config_json(raw).api_key
