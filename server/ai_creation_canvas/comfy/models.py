"""Immutable values shared by the controlled ComfyUI workflow library."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias


JsonValue: TypeAlias = object


class FrozenJsonObject(Mapping[str, JsonValue]):
    """A recursively immutable JSON object retained only for faithful export."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, JsonValue]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: str) -> JsonValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenJsonObject({self._values!r})"


class WorkflowFormat(StrEnum):
    EDITOR = "editor"
    API = "api"


class WorkflowValidationError(ValueError):
    """A stable workflow validation code without input data in its message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PreviewNode:
    id: str
    type: str
    title: str | None
    position: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class PreviewEdge:
    source_id: str
    target_id: str


@dataclass(frozen=True, slots=True)
class PreviewGraph:
    nodes: tuple[PreviewNode, ...]
    edges: tuple[PreviewEdge, ...]
    has_editor_layout: bool


@dataclass(frozen=True, slots=True)
class ParsedWorkflow:
    """Validated raw workflow JSON plus its safe, deterministic public summary."""

    raw: FrozenJsonObject
    checksum: str
    formats: frozenset[WorkflowFormat]
    node_count: int
    link_count: int
    node_types: frozenset[str]
    preview: PreviewGraph
