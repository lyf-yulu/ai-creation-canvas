"""Strict, server-only loading for grouped provider credential pools."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


_MAX_POOL_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True, repr=False)
class CredentialKey:
    key_id: str
    secret: str
    max_concurrency: int

    def __repr__(self) -> str:
        return f"CredentialKey(max_concurrency={self.max_concurrency!r})"


@dataclass(frozen=True, slots=True, repr=False)
class CredentialPool:
    pool_id: str
    provider_id: str
    group: str
    allowed_families: tuple[str, ...]
    keys: tuple[CredentialKey, ...]
    revision_digest: str

    def __repr__(self) -> str:
        return (
            "CredentialPool("
            f"pool_id={self.pool_id!r}, provider_id={self.provider_id!r}, "
            f"group={self.group!r}, allowed_families={self.allowed_families!r}, "
            f"key_count={len(self.keys)!r}, revision_digest={self.revision_digest!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CredentialPoolSnapshot:
    _pools: Mapping[str, CredentialPool] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_pools", MappingProxyType(dict(self._pools)))

    def __repr__(self) -> str:
        return f"CredentialPoolSnapshot(pool_count={len(self._pools)!r})"

    @classmethod
    def empty(cls) -> CredentialPoolSnapshot:
        return cls()

    def get(self, pool_id: str) -> CredentialPool | None:
        return self._pools.get(pool_id)

    def safe_summaries(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "pool_id": pool.pool_id,
                "provider_id": pool.provider_id,
                "group": pool.group,
                "allowed_families": pool.allowed_families,
                "key_count": len(pool.keys),
                "max_concurrency": sum(key.max_concurrency for key in pool.keys),
                "revision_digest": pool.revision_digest,
            }
            for pool in self._pools.values()
        )


class _CredentialKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key_id: str = Field(alias="id", min_length=1)
    secret: str = Field(alias="api_key", min_length=1)
    max_concurrency: int = Field(ge=1, le=32)


class _CredentialPoolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: str = Field(min_length=1)
    group: str = Field(min_length=1)
    allowed_families: list[str] = Field(min_length=1)
    keys: list[_CredentialKeyInput] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_pool(self) -> _CredentialPoolInput:
        if not self.provider.strip() or not self.group.strip() or any(not family.strip() for family in self.allowed_families):
            raise ValueError("blank credential pool field")
        if len({key.key_id for key in self.keys}) != len(self.keys):
            raise ValueError("duplicate credential key id")
        return self


class _CredentialPoolsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int
    pools: dict[str, _CredentialPoolInput]

    @model_validator(mode="after")
    def validate_document(self) -> _CredentialPoolsInput:
        if self.version != 1 or any(not pool_id.strip() for pool_id in self.pools):
            raise ValueError("invalid credential pools document")
        return self


def _invalid_configuration() -> ValueError:
    return ValueError("credential pools configuration is invalid")


def _reject_duplicate_mapping_fields(payload: str) -> None:
    try:
        document = yaml.compose(payload, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise _invalid_configuration() from None
    if document is None:
        raise _invalid_configuration()

    seen_nodes: set[int] = set()

    def visit(node: yaml.Node) -> None:
        if id(node) in seen_nodes:
            return
        seen_nodes.add(id(node))
        if isinstance(node, yaml.MappingNode):
            seen_keys: set[tuple[str, str]] = set()
            for key_node, value_node in node.value:
                if isinstance(key_node, yaml.ScalarNode):
                    if key_node.tag == "tag:yaml.org,2002:merge" or key_node.value == "<<":
                        raise _invalid_configuration()
                    marker = (key_node.tag, key_node.value)
                    if marker in seen_keys:
                        raise _invalid_configuration()
                    seen_keys.add(marker)
                visit(key_node)
                visit(value_node)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                visit(item)

    visit(document)


class CredentialPoolLoader:
    """Loads a complete pool snapshot without exposing secrets in diagnostics."""

    def __init__(self, path: Path, *, production: bool = False) -> None:
        self._path = Path(path)
        self._production = production
        self._lock = threading.Lock()
        self._snapshot = CredentialPoolSnapshot.empty()
        self._has_loaded = False

    def load(self) -> CredentialPoolSnapshot:
        candidate = self._parse_candidate()
        with self._lock:
            self._snapshot = candidate
            self._has_loaded = True
        return candidate

    def reload(self) -> CredentialPoolSnapshot:
        try:
            candidate = self._parse_candidate()
        except ValueError:
            with self._lock:
                if self._has_loaded:
                    return self._snapshot
            raise
        with self._lock:
            self._snapshot = candidate
            self._has_loaded = True
        return candidate

    def _parse_candidate(self) -> CredentialPoolSnapshot:
        source = self._read_source()
        _reject_duplicate_mapping_fields(source)
        try:
            payload = yaml.safe_load(source)
            document = _CredentialPoolsInput.model_validate(payload)
        except (TypeError, ValueError, yaml.YAMLError):
            raise _invalid_configuration() from None
        pools = {
            pool_id: CredentialPool(
                pool_id=pool_id,
                provider_id=pool.provider,
                group=pool.group,
                allowed_families=tuple(pool.allowed_families),
                keys=tuple(
                    CredentialKey(
                        key_id=key.key_id,
                        secret=key.secret,
                        max_concurrency=key.max_concurrency,
                    )
                    for key in pool.keys
                ),
                revision_digest=_pool_digest(pool_id, pool),
            )
            for pool_id, pool in document.pools.items()
        }
        return CredentialPoolSnapshot(pools)

    def _read_source(self) -> str:
        try:
            initial = self._path.lstat()
            if not stat.S_ISREG(initial.st_mode) or stat.S_ISLNK(initial.st_mode) or initial.st_size > _MAX_POOL_FILE_BYTES:
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
                    or opened.st_size > _MAX_POOL_FILE_BYTES
                    or (self._production and stat.S_IMODE(opened.st_mode) & ~0o600)
                ):
                    raise _invalid_configuration()
                raw = stream.read(_MAX_POOL_FILE_BYTES + 1)
            if len(raw) > _MAX_POOL_FILE_BYTES:
                raise _invalid_configuration()
            return raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            raise _invalid_configuration() from None


def _pool_digest(pool_id: str, pool: _CredentialPoolInput) -> str:
    canonical = json.dumps(
        {"pool_id": pool_id, **pool.model_dump(by_alias=True, mode="json")},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
