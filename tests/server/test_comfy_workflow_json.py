from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_creation_canvas.comfy import (
    WorkflowFormat,
    WorkflowValidationError,
    canonical_checksum,
    export_workflow,
    parse_workflow_json,
)


@pytest.fixture
def core_workflow() -> bytes:
    return (Path(__file__).parents[1] / "fixtures" / "comfy" / "core-load-save-workflow.json").read_bytes()


def test_editor_workflow_round_trips_by_canonical_checksum(core_workflow: bytes) -> None:
    parsed = parse_workflow_json(core_workflow)
    assert parsed.formats == frozenset({WorkflowFormat.EDITOR})
    assert parsed.node_count == 2 and parsed.link_count == 1
    assert parsed.node_types == frozenset({"LoadImage", "SaveImage"})
    assert parsed.preview.has_editor_layout is True
    assert parsed.preview.nodes[0].position == (0, 0)
    assert canonical_checksum(json.loads(export_workflow(parsed, WorkflowFormat.EDITOR))) == parsed.checksum


def test_api_workflow_projects_links_without_editor_layout() -> None:
    parsed = parse_workflow_json(
        b'{"2":{"inputs":{"images":["1",0]},"class_type":"SaveImage"},"1":{"class_type":"LoadImage","inputs":{}}}'
    )
    assert parsed.formats == frozenset({WorkflowFormat.API})
    assert parsed.node_count == 2 and parsed.link_count == 1
    assert parsed.preview.has_editor_layout is False
    assert [(edge.source_id, edge.target_id) for edge in parsed.preview.edges] == [("1", "2")]
    assert export_workflow(parsed, WorkflowFormat.API) == (
        b'{\n  "1": {\n    "class_type": "LoadImage",\n    "inputs": {}\n  },\n'
        b'  "2": {\n    "class_type": "SaveImage",\n    "inputs": {\n      "images": [\n        "1",\n        0\n      ]\n    }\n  }\n}'
    )


def test_rejects_dangling_link_and_sensitive_key() -> None:
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_TOPOLOGY_INVALID"):
        parse_workflow_json(b'{"nodes":[{"id":1,"type":"LoadImage"}],"links":[[1,1,0,2,0,"IMAGE"]]}')
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(b'{"1":{"class_type":"LoadImage","inputs":{"base_URL":"https://bad.example"}}}')


@pytest.mark.parametrize("field", ("url", "URL", "_url_", "u-r_l", "cosurl", "cos_url", "CoS-UrL"))
def test_allows_resource_url_metadata_key_variants_without_projecting_the_value(field: str) -> None:
    url = "https://workflow.example/metadata"
    raw = json.dumps({"1": {"class_type": "LoadImage", "inputs": {field: url}}}).encode()

    parsed = parse_workflow_json(raw)

    assert parsed.formats == frozenset({WorkflowFormat.API})
    assert url not in repr(parsed.preview)
    assert canonical_checksum(json.loads(export_workflow(parsed, WorkflowFormat.API))) == parsed.checksum


@pytest.mark.parametrize(
    "field",
    (
        "base_url", "base-url", "callback_url", "callback-url", "service_url", "endpoint_url", "webhook_url", "endpoint",
        "base endpoint", "webhook\tendpoint", "server.endpoint",
        "service endpoint url", "service-url-endpoint", "callback endpoint url", "callback_url_endpoint", "resource_endpoint",
    ),
)
def test_rejects_control_endpoint_key_variants(field: str) -> None:
    raw = json.dumps({"1": {"class_type": "LoadImage", "inputs": {field: "https://bad.example"}}}).encode()

    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(raw)


def test_locally_supplied_minimax_workflow_round_trips_without_printing_contents() -> None:
    path = Path("/Users/260413a/Downloads/▶▷MiniMaxH3-加速视频流整合.json")
    if not path.is_file():
        pytest.skip("locally supplied MiniMax workflow is unavailable")

    parsed = parse_workflow_json(path.read_bytes())

    assert parsed.formats == frozenset({WorkflowFormat.EDITOR})
    assert canonical_checksum(json.loads(export_workflow(parsed, WorkflowFormat.EDITOR))) == parsed.checksum


def test_locally_supplied_bernini_workflow_round_trips_without_printing_contents() -> None:
    path = Path("/Users/260413a/Downloads/贝尔尼尼Bernini+Studio工作流.json")
    if not path.is_file():
        pytest.skip("locally supplied Bernini workflow is unavailable")
    try:
        parsed = parse_workflow_json(path.read_bytes())
    except WorkflowValidationError:
        pytest.fail("locally supplied Bernini workflow was rejected", pytrace=False)

    assert parsed.formats == frozenset({WorkflowFormat.EDITOR})
    assert canonical_checksum(json.loads(export_workflow(parsed, WorkflowFormat.EDITOR))) == parsed.checksum


@pytest.mark.parametrize("field", ("api_key", "auth_token", "header", "script", "plugin", "code"))
def test_rejects_sensitive_key_names_after_resource_url_relaxation(field: str) -> None:
    raw = json.dumps({"1": {"class_type": "LoadImage", "inputs": {field: "server-only-secret"}}}).encode()

    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(raw)


@pytest.mark.parametrize(
    "field",
    ("api key", "auth\ttoken", "credential.ref", "service u r l", "endpoint\nu-r_l", "s.c.r.i.p.t"),
)
def test_rejects_separator_obfuscated_control_and_sensitive_key_names(field: str) -> None:
    raw = json.dumps({"1": {"class_type": "LoadImage", "inputs": {field: "server-only-secret"}}}).encode()

    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(raw)


@pytest.mark.parametrize("field", (" api_key ", "\tSeCrEt\n", "\tcredential\n"))
def test_rejects_whitespace_wrapped_sensitive_keys(field: str) -> None:
    raw = json.dumps({"1": {"class_type": "LoadImage", "inputs": {field: "server-only-secret"}}}).encode()

    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(raw)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b'{"nodes":[],"nodes":[],"links":[]}', "WORKFLOW_JSON_DUPLICATE_KEY"),
        (b'\xff', "WORKFLOW_ENCODING_INVALID"),
        (b'{"1":{"class_type":"LoadImage","inputs":{"value":NaN}}}', "WORKFLOW_JSON_NONFINITE"),
        (b'{"1":{"class_type":"LoadImage","inputs":{"value":1e999}}}', "WORKFLOW_JSON_NONFINITE"),
        (b'[]', "WORKFLOW_FORMAT_UNSUPPORTED"),
    ],
)
def test_rejects_invalid_json_inputs_with_stable_codes(raw: bytes, code: str) -> None:
    with pytest.raises(WorkflowValidationError, match=code):
        parse_workflow_json(raw)


def test_rejects_value_and_topology_limits() -> None:
    too_deep = b'{"1":{"class_type":"LoadImage","inputs":{"value":' + (b'[' * 65) + b'0' + (b']' * 65) + b'}}}'
    too_long = json.dumps({"1": {"class_type": "LoadImage", "inputs": {"value": "x" * (64 * 1024 + 1)}}}).encode()
    too_many_nodes = json.dumps({str(index): {"class_type": "LoadImage", "inputs": {}} for index in range(501)}).encode()
    too_many_links = json.dumps({
        "1": {"class_type": "LoadImage", "inputs": {}},
        "2": {"class_type": "SaveImage", "inputs": {str(index): ["1", 0] for index in range(2001)}},
    }).encode()
    for raw, code in [
        (too_deep, "WORKFLOW_DEPTH_EXCEEDED"),
        (too_long, "WORKFLOW_STRING_TOO_LARGE"),
        (too_many_nodes, "WORKFLOW_NODE_LIMIT_EXCEEDED"),
        (too_many_links, "WORKFLOW_LINK_LIMIT_EXCEEDED"),
        (b" " * (4 * 1024 * 1024 + 1), "WORKFLOW_SIZE_EXCEEDED"),
    ]:
        with pytest.raises(WorkflowValidationError, match=code):
            parse_workflow_json(raw)


def test_preview_omits_widgets_nested_values_and_html_titles() -> None:
    parsed = parse_workflow_json(
        b'{"nodes":[{"id":1,"type":"LoadImage","pos":[1.5,2],"title":"<img src=x>","widgets_values":["secret prompt"],"inputs":[],"outputs":[]}],"links":[]}'
    )
    node = parsed.preview.nodes[0]
    assert node.id == "1" and node.type == "LoadImage"
    assert node.title is None and node.position is None
    assert "secret prompt" not in repr(parsed.preview)


def test_export_rejects_a_format_not_present(core_workflow: bytes) -> None:
    parsed = parse_workflow_json(core_workflow)
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FORMAT_UNAVAILABLE"):
        export_workflow(parsed, WorkflowFormat.API)


def test_parsed_workflow_raw_json_is_recursively_immutable(core_workflow: bytes) -> None:
    parsed = parse_workflow_json(core_workflow)
    with pytest.raises(TypeError):
        parsed.raw._values["nodes"] = ()
    with pytest.raises(TypeError):
        parsed.raw["nodes"][0]._values["type"] = "Changed"


def test_rejects_lone_surrogate_with_a_stable_encoding_error() -> None:
    raw = br'{"1":{"class_type":"LoadImage","inputs":{"value":"\ud800"}}}'
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_ENCODING_INVALID"):
        parse_workflow_json(raw)
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_ENCODING_INVALID"):
        canonical_checksum({"value": "\ud800"})


def test_rejects_an_api_node_id_that_exceeds_the_safe_decimal_bound() -> None:
    raw = b'{"' + (b"1" * 5_000) + b'":{"class_type":"LoadImage","inputs":{}}}'
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_TOPOLOGY_INVALID"):
        parse_workflow_json(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"nodes":[{"id":"<svg/onload=1>","type":"LoadImage"}],"links":[]}',
        b'{"nodes":[{"id":1,"type":"<img src=x>"}],"links":[]}',
    ],
)
def test_rejects_html_delimiters_in_preview_projected_node_strings(raw: bytes) -> None:
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(raw)
