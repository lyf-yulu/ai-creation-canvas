"""Strict, server-only configuration for the Ark private portrait asset library."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading

from pydantic import BaseModel, ConfigDict, Field, model_validator


_MAX_CONFIG_BYTES = 64 * 1024
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,62}\Z")
_REGION = re.compile(r"[a-z0-9-]{2,32}\Z")
_DEFAULT_PROJECT_NAME = "Seedance2.0"


def _invalid_configuration() -> ValueError:
    return ValueError("asset library configuration is invalid")


def _unique_json_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise _invalid_configuration()
        result[key] = value
    return result


def _clean_secret(name: str, value: str) -> str:
    if value != value.strip() or not value.isprintable() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _invalid_configuration()
    return value


def normalize_asset_library_secret_key(value: str) -> str:
    """Decode console-provided base64 secret keys; keep plain-text keys verbatim."""
    if not value:
        return value
    try:
        decoded = base64.b64decode(value.encode("utf-8"), validate=True)
        if decoded and base64.b64encode(decoded) == value.encode("utf-8"):
            text = decoded.decode("utf-8")
            if text.isprintable() and not any(ord(char) < 32 or ord(char) == 127 for char in text):
                return text
    except (ValueError, UnicodeError, binascii.Error):
        pass
    return value


class _AssetLibraryConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int
    ark_access_key: str = Field(min_length=1, max_length=256)
    ark_secret_key: str = Field(min_length=1, max_length=256)
    tos_access_key: str = Field(min_length=1, max_length=256)
    tos_secret_key: str = Field(min_length=1, max_length=256)
    tos_bucket: str = Field(min_length=3, max_length=63)
    tos_region: str = Field(min_length=2, max_length=32)
    project_name: str = Field(default=_DEFAULT_PROJECT_NAME, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_document(self) -> _AssetLibraryConfigInput:
        if self.version != 1:
            raise _invalid_configuration()
        for name in ("ark_access_key", "ark_secret_key", "tos_access_key", "tos_secret_key", "project_name"):
            _clean_secret(name, getattr(self, name))
        if _BUCKET.fullmatch(self.tos_bucket) is None or _REGION.fullmatch(self.tos_region) is None:
            raise _invalid_configuration()
        return self


@dataclass(frozen=True, slots=True, repr=False)
class AssetLibraryConfig:
    ark_access_key: str
    ark_secret_key: str
    tos_access_key: str
    tos_secret_key: str
    tos_bucket: str
    tos_region: str
    project_name: str

    def __repr__(self) -> str:
        return (
            "AssetLibraryConfig("
            f"has_ark_access={bool(self.ark_access_key)!r}, "
            f"has_tos_access={bool(self.tos_access_key)!r}, "
            f"tos_bucket={self.tos_bucket!r}, "
            f"tos_region={self.tos_region!r}, "
            f"project_name={self.project_name!r})"
        )

    def safe_summary(self) -> dict[str, object]:
        return {
            "has_ark_access": bool(self.ark_access_key),
            "has_tos_access": bool(self.tos_access_key),
            "tos_bucket": self.tos_bucket,
            "tos_region": self.tos_region,
            "project_name": self.project_name,
            "revision_digest": self._digest(),
        }

    def _digest(self) -> str:
        canonical = json.dumps(
            {
                "version": 1,
                "ark_access_key": self.ark_access_key,
                "ark_secret_key": self.ark_secret_key,
                "tos_access_key": self.tos_access_key,
                "tos_secret_key": self.tos_secret_key,
                "tos_bucket": self.tos_bucket,
                "tos_region": self.tos_region,
                "project_name": self.project_name,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def parse_asset_library_config_json(raw: bytes) -> AssetLibraryConfig:
    """Parse the administrator upload contract without accepting YAML syntax."""
    if not isinstance(raw, bytes) or len(raw) > _MAX_CONFIG_BYTES:
        raise _invalid_configuration()
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
        document = _AssetLibraryConfigInput.model_validate(payload)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise _invalid_configuration() from None
    return AssetLibraryConfig(
        ark_access_key=document.ark_access_key,
        ark_secret_key=normalize_asset_library_secret_key(document.ark_secret_key),
        tos_access_key=document.tos_access_key,
        tos_secret_key=normalize_asset_library_secret_key(document.tos_secret_key),
        tos_bucket=document.tos_bucket,
        tos_region=document.tos_region,
        project_name=document.project_name,
    )


class AssetLibraryConfigLoader:
    """Loads the administrator config without exposing secrets in diagnostics."""

    def __init__(self, path: Path | str, *, production: bool = False) -> None:
        self._path = Path(path)
        self._production = production
        self._lock = threading.Lock()
        self._config: AssetLibraryConfig | None = None

    def load(self) -> AssetLibraryConfig:
        candidate = self._parse_candidate()
        with self._lock:
            self._config = candidate
        return candidate

    def reload(self) -> AssetLibraryConfig:
        try:
            candidate = self._parse_candidate()
        except ValueError:
            with self._lock:
                if self._config is not None:
                    return self._config
            raise
        with self._lock:
            self._config = candidate
        return candidate

    def _parse_candidate(self) -> AssetLibraryConfig:
        raw = self._read_source()
        return parse_asset_library_config_json(raw)

    def _read_source(self) -> bytes:
        try:
            initial = self._path.lstat()
            if not stat.S_ISREG(initial.st_mode) or stat.S_ISLNK(initial.st_mode) or initial.st_size > _MAX_CONFIG_BYTES:
                raise _invalid_configuration()
            if self._production and stat.S_IMODE(initial.st_mode) & ~0o600:
                raise _invalid_configuration()
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                current = self._path.lstat()
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or stat.S_ISLNK(current.st_mode)
                    or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino)
                    or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)
                    or opened.st_size > _MAX_CONFIG_BYTES
                    or (self._production and stat.S_IMODE(opened.st_mode) & ~0o600)
                ):
                    raise _invalid_configuration()
                raw = stream.read(_MAX_CONFIG_BYTES + 1)
            if len(raw) > _MAX_CONFIG_BYTES:
                raise _invalid_configuration()
            return raw
        except (OSError, ValueError):
            raise _invalid_configuration() from None


def load_asset_library_config(path: Path | str, root: Path | str, *, production: bool = False) -> AssetLibraryConfig:
    candidate, trusted = Path(path), Path(root).resolve(strict=False)
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > _MAX_CONFIG_BYTES:
            raise _invalid_configuration()
        if production and stat.S_IMODE(candidate.stat().st_mode) & ~0o600:
            raise _invalid_configuration()
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(trusted)
        return parse_asset_library_config_json(resolved.read_bytes())
    except (OSError, UnicodeError, ValueError) as error:
        raise _invalid_configuration() from error
