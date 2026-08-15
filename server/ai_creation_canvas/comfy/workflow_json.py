"""Bounded parsing and safe projection of ComfyUI editor and API JSON."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from typing import Any

from ai_creation_canvas.comfy.models import (
    FrozenJsonObject,
    JsonValue,
    ParsedWorkflow,
    PreviewEdge,
    PreviewGraph,
    PreviewNode,
    WorkflowFormat,
    WorkflowValidationError,
)


_MAX_BYTES = 4 * 1024 * 1024
_MAX_NODES = 500
_MAX_LINKS = 2_000
_MAX_DEPTH = 64
_MAX_STRING_BYTES = 64 * 1024
_MAX_API_NODE_ID_CHARS = 64


def _canonical_field_name(value: str) -> str:
    """Normalize case and remove every non-alphanumeric separator for deny checks."""
    return "".join(character for character in value.casefold() if character.isalnum())


_FORBIDDEN_FIELD_NAMES = frozenset(_canonical_field_name(value) for value in {
    "apikey",
    "auth",
    "auth_header",
    "auth_token",
    "authorization",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
    "credential_ref",
    "header",
    "headers",
    "key",
    "password",
    "private_key",
    "public_key",
    "secret",
    "secret_ref",
    "script",
    "scripts",
    "plugin",
    "plugins",
    "code",
    "token",
    "webhook",
    "auth_header_ref",
    "endpoint",
    "base_url",
    "callback_url",
    "service_url",
    "server_url",
    "webhook_url",
    "endpoint_url",
    "service_endpoint",
    "callback_endpoint",
    "base_endpoint",
    "base_endpoint_url",
    "base_url_endpoint",
    "webhook_endpoint",
    "webhook_endpoint_url",
    "webhook_url_endpoint",
    "server_endpoint",
    "server_endpoint_url",
    "server_url_endpoint",
})


def _is_forbidden_field_name(value: str) -> bool:
    """Reject only explicit control and credential field names after canonicalization."""
    return _canonical_field_name(value) in _FORBIDDEN_FIELD_NAMES


def canonical_checksum(value: object) -> str:
    """Return a SHA-256 checksum over deterministic, finite JSON encoding."""
    try:
        encoded = json.dumps(
            _thaw_json(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except UnicodeEncodeError as error:
        raise WorkflowValidationError("WORKFLOW_ENCODING_INVALID") from error
    except (TypeError, ValueError) as error:
        raise WorkflowValidationError("WORKFLOW_JSON_INVALID") from error
    return hashlib.sha256(encoded).hexdigest()


def parse_workflow_json(raw: bytes) -> ParsedWorkflow:
    """Parse one supported ComfyUI format without executing or interpreting it."""
    value = _decode_json_object(raw, max_bytes=_MAX_BYTES)
    _assert_value_limits(value, depth=0)
    if isinstance(value.get("nodes"), list) and isinstance(value.get("links"), list):
        return _parse_editor(value)
    if value and all(key.isdecimal() for key in value):
        return _parse_api(value)
    raise WorkflowValidationError("WORKFLOW_FORMAT_UNSUPPORTED")


def export_workflow(parsed: ParsedWorkflow, format: WorkflowFormat) -> bytes:
    """Encode the stored workflow only in its originally supplied format."""
    try:
        selected_format = WorkflowFormat(format)
    except ValueError as error:
        raise WorkflowValidationError("WORKFLOW_FORMAT_UNAVAILABLE") from error
    if selected_format not in parsed.formats:
        raise WorkflowValidationError("WORKFLOW_FORMAT_UNAVAILABLE")
    return json.dumps(
        _thaw_json(parsed.raw), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8")


def _decode_json_object(raw: bytes, *, max_bytes: int) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise WorkflowValidationError("WORKFLOW_SIZE_EXCEEDED")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowValidationError("WORKFLOW_ENCODING_INVALID") from error
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except WorkflowValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as error:
        raise WorkflowValidationError("WORKFLOW_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise WorkflowValidationError("WORKFLOW_FORMAT_UNSUPPORTED")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowValidationError("WORKFLOW_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise WorkflowValidationError("WORKFLOW_JSON_NONFINITE")


def _assert_value_limits(value: object, *, depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise WorkflowValidationError("WORKFLOW_DEPTH_EXCEEDED")
    if isinstance(value, str):
        if _utf8_length(value) > _MAX_STRING_BYTES:
            raise WorkflowValidationError("WORKFLOW_STRING_TOO_LARGE")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowValidationError("WORKFLOW_JSON_NONFINITE")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_value_limits(key, depth=depth + 1)
            if _is_forbidden_field_name(key):
                raise WorkflowValidationError("WORKFLOW_FIELD_REJECTED")
            _assert_value_limits(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _assert_value_limits(item, depth=depth + 1)
        return
    raise WorkflowValidationError("WORKFLOW_JSON_INVALID")


def _parse_editor(value: dict[str, Any]) -> ParsedWorkflow:
    nodes = value["nodes"]
    links = value["links"]
    if len(nodes) > _MAX_NODES:
        raise WorkflowValidationError("WORKFLOW_NODE_LIMIT_EXCEEDED")
    if len(links) > _MAX_LINKS:
        raise WorkflowValidationError("WORKFLOW_LINK_LIMIT_EXCEEDED")

    preview_nodes: list[PreviewNode] = []
    node_ids: set[int | str] = set()
    node_types: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
        node_id = _editor_node_id(node.get("id"))
        if node_id in node_ids:
            raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
        node_type = _node_type(node.get("type"))
        node_ids.add(node_id)
        node_types.add(node_type)
        preview_nodes.append(
            PreviewNode(
                id=str(node_id),
                type=node_type,
                title=_safe_title(node.get("title")),
                position=_editor_position(node.get("pos")),
            )
        )

    preview_edges: list[PreviewEdge] = []
    link_ids: set[int | str] = set()
    for link in links:
        if not isinstance(link, list) or len(link) < 6:
            raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
        link_id = _editor_node_id(link[0])
        source_id = _editor_node_id(link[1])
        target_id = _editor_node_id(link[3])
        if link_id in link_ids or source_id not in node_ids or target_id not in node_ids:
            raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
        link_ids.add(link_id)
        preview_edges.append(PreviewEdge(source_id=str(source_id), target_id=str(target_id)))

    return _parsed(
        value,
        WorkflowFormat.EDITOR,
        node_count=len(nodes),
        link_count=len(links),
        node_types=frozenset(node_types),
        preview=PreviewGraph(tuple(preview_nodes), tuple(preview_edges), has_editor_layout=True),
    )


def _parse_api(value: dict[str, Any]) -> ParsedWorkflow:
    if len(value) > _MAX_NODES:
        raise WorkflowValidationError("WORKFLOW_NODE_LIMIT_EXCEEDED")
    node_ids = {_api_node_id(node_id) for node_id in value}
    preview_nodes: list[PreviewNode] = []
    node_types: set[str] = set()
    preview_edges: list[PreviewEdge] = []
    for node_id in sorted(node_ids, key=lambda item: (int(item), item)):
        node = value[node_id]
        if not isinstance(node, dict):
            raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
        node_type = _node_type(node.get("class_type"))
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
        node_types.add(node_type)
        preview_nodes.append(PreviewNode(id=node_id, type=node_type, title=None, position=None))
        for input_value in inputs.values():
            source_id = _api_link_source(input_value)
            if source_id is None:
                continue
            if source_id not in node_ids:
                raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
            preview_edges.append(PreviewEdge(source_id=source_id, target_id=node_id))
            if len(preview_edges) > _MAX_LINKS:
                raise WorkflowValidationError("WORKFLOW_LINK_LIMIT_EXCEEDED")

    return _parsed(
        value,
        WorkflowFormat.API,
        node_count=len(value),
        link_count=len(preview_edges),
        node_types=frozenset(node_types),
        preview=PreviewGraph(tuple(preview_nodes), tuple(preview_edges), has_editor_layout=False),
    )


def _parsed(
    value: dict[str, Any],
    format: WorkflowFormat,
    *,
    node_count: int,
    link_count: int,
    node_types: frozenset[str],
    preview: PreviewGraph,
) -> ParsedWorkflow:
    return ParsedWorkflow(
        raw=_freeze_json(value),
        checksum=canonical_checksum(value),
        formats=frozenset({format}),
        node_count=node_count,
        link_count=link_count,
        node_types=node_types,
        preview=preview,
    )


def _editor_node_id(value: object) -> int | str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or (isinstance(value, str) and not value):
        raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
    if isinstance(value, str):
        _assert_safe_preview_text(value)
    return value


def _node_type(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
    _assert_safe_preview_text(value)
    return value


def _editor_position(value: object) -> tuple[int, int] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    x, y = value
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
        return None
    return x, y


def _safe_title(value: object) -> str | None:
    if not isinstance(value, str) or not _is_safe_preview_text(value):
        return None
    return value


def _utf8_length(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise WorkflowValidationError("WORKFLOW_ENCODING_INVALID") from error


def _api_node_id(value: str) -> str:
    if not value.isascii() or not value.isdecimal() or len(value) > _MAX_API_NODE_ID_CHARS:
        raise WorkflowValidationError("WORKFLOW_TOPOLOGY_INVALID")
    return value


def _is_safe_preview_text(value: str) -> bool:
    return "<" not in value and ">" not in value


def _assert_safe_preview_text(value: str) -> None:
    if not _is_safe_preview_text(value):
        raise WorkflowValidationError("WORKFLOW_FIELD_REJECTED")


def _api_link_source(value: object) -> str | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    source_id, output_index = value
    if isinstance(source_id, bool) or not isinstance(source_id, (str, int)):
        return None
    if isinstance(output_index, bool) or not isinstance(output_index, int):
        return None
    return str(source_id)


def _freeze_json(value: object) -> FrozenJsonObject:
    if not isinstance(value, dict):
        raise TypeError("root workflow JSON must be an object")
    frozen = _freeze_json_value(value)
    assert isinstance(frozen, FrozenJsonObject)
    return frozen


def _freeze_json_value(value: object) -> JsonValue:
    if isinstance(value, dict):
        return FrozenJsonObject({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError("value is not JSON-compatible")


def _thaw_json(value: object) -> object:
    if isinstance(value, FrozenJsonObject):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value
