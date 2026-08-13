from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

import pytest

from ai_creation_canvas.adapters.ark import load_ark_model_declarations
from ai_creation_canvas.adapters.factory import _CHIYUN_PARAMETERS
from tests.server.test_model_assignments import local_clients


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("acceptance_real_media", ROOT / "scripts/acceptance_real_media.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_paid_job_payloads_are_exactly_one_single_image_and_one_5s_480p_video() -> None:
    requests = module.paid_job_requests("doubao-seedream-4-0-250828", "doubao-seedance-2-0-260128", "owned-asset", "job-result.image.0")
    assert len(requests) == 2
    assert requests[0]["operation"] == "image.edit"
    assert requests[0]["params"] == {"size": "1024x1024"}
    assert requests[0]["inputs"] == {"reference_images": ["owned-asset"]}
    assert "n" not in requests[0]["params"]
    assert requests[1]["operation"] == "video.generate"
    assert requests[1]["params"] == {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False, "return_last_frame": False}
    assert requests[1]["inputs"] == {"reference_images": ["job-result.image.0"]}


def test_reviewed_channel_definitions_use_only_code_owned_models_and_origins() -> None:
    definitions = module.acceptance_definitions(
        ("seedream-ark", "seedance-ark"),
        chiyun_origin="https://attacker.example",
        t8star_origin="https://attacker.example",
    )

    assert {item["provider_id"] for item in definitions["providers"]} == {"ark"}
    assert {item["base_url"] for item in definitions["providers"]} == {"https://ark.cn-beijing.volces.com"}
    assert {item["model_id"] for item in definitions["models"]} == {"seedream", "seedance"}
    routes = {item["route_id"]: item for item in definitions["routes"]}
    assert set(routes) == {"seedream-ark", "seedance-ark"}
    assert routes["seedream-ark"]["provider_model_name"] == "doubao-seedream-5-0-pro-260628"
    assert routes["seedance-ark"]["provider_model_name"] == "doubao-seedance-2-5-260628"
    assert routes["seedance-ark"]["operation_contracts"][0]["operation"] == "video.generate"
    encoded = json.dumps(definitions, sort_keys=True)
    for forbidden in ("api_key", "authorization", "secret"):
        assert forbidden not in encoded.lower()


def test_chiyun_channel_ignores_caller_origin_and_uses_code_owned_destination() -> None:
    definitions = module.acceptance_definitions(
        ("banana-chiyun",), chiyun_origin="https://attacker.example", t8star_origin="https://attacker.example",
    )
    assert definitions["providers"][0]["base_url"] == "https://chiyun.work"


def test_each_paid_call_uses_the_minimum_reviewed_request_shape() -> None:
    owned_asset = "owned-asset"
    banana = module.request_for_paid_call(module.PaidCall("smoke", "banana-chiyun", "banana"), owned_asset)
    seedream = module.request_for_paid_call(module.PaidCall("smoke", "seedream-ark", "seedream"), owned_asset)
    seedance = module.request_for_paid_call(module.PaidCall("smoke", "seedance-ark", "seedance"), owned_asset)

    assert banana["operation"] == "image.edit"
    assert banana["params"] == {"aspect_ratio": "1:1", "image_size": "2K"}
    assert banana["inputs"] == {"reference_images": [owned_asset]}
    assert seedream["operation"] == "image.edit"
    assert seedream["params"] == {"size": "1K", "watermark": False, "output_format": "png", "prompt_optimization": "fast"}
    assert seedream["inputs"] == {"reference_images": [owned_asset]}
    assert seedance["operation"] == "video.generate"
    assert seedance["params"] == {"ratio": "16:9", "resolution": "480p", "duration": 5, "generate_audio": False, "watermark": False}
    assert seedance["inputs"] == {}
    assert len({banana["idempotency_key"], seedream["idempotency_key"], seedance["idempotency_key"]}) == 3


def test_real_production_plan_is_exactly_three_cases_per_model_with_twelve_calls() -> None:
    plan = module.real_production_plan()
    assert len(plan) == 12
    assert [(item.model_id, item.sample_index) for item in plan] == [
        (model_id, index)
        for model_id in ("banana", "gpt-image2", "seedream", "seedance")
        for index in (1, 2, 3)
    ]
    assert len({(item.channel_id, item.sample_index) for item in plan}) == 12


def test_acceptance_profiles_cross_check_authoritative_ark_config_and_factory_templates() -> None:
    config = ROOT / "server" / "config" / "ark-models.example.json"
    ark = {item.model_id: item for item in load_ark_model_declarations(config, config.parent)}
    profiles = module.acceptance_model_profiles()["profiles"]
    assert set(profiles) == {"banana", "gpt-image2", "seedream", "seedance"}
    for profile_id, model_id, operation in (
        ("seedream", "doubao-seedream-5-0-pro-260628", "image.edit"),
        ("seedance", "doubao-seedance-2-5-260628", "video.generate"),
    ):
        declaration = ark[model_id]
        profile = profiles[profile_id]
        contract = profile["contract"]
        assert profile["provider_model_name"] == declaration.model_id
        assert operation in declaration.operations and contract["operation"] == operation
        assert contract["parameter_schema"] == declaration.parameter_schema
        assert contract["parameter_mappings"] == declaration.parameter_mappings
        expected_ports = [
            {"port_id": item.port_id, "media_type": item.media_type, "min_items": item.min_items, "max_items": item.max_items}
            for item in declaration.input_ports
        ]
        if profile_id == "seedream":
            next(item for item in expected_ports if item["port_id"] == "reference_images")["min_items"] = 1
        assert contract["input_ports"] == expected_ports

    seedance_properties = profiles["seedance"]["contract"]["parameter_schema"]["properties"]
    assert set(seedance_properties) == {"ratio", "resolution", "duration", "generate_audio", "output_format", "watermark"}
    assert seedance_properties["resolution"]["enum"] == ["480p", "720p"]
    seedance_ports = {item["port_id"]: item for item in profiles["seedance"]["contract"]["input_ports"]}
    assert seedance_ports["reference_images"]["max_items"] == 30
    assert seedance_ports["reference_audio"]["max_items"] == 10

    trusted_chiyun_properties = {name: dict(rule) for name, (_target, rule) in _CHIYUN_PARAMETERS.items()}
    trusted_chiyun_mappings = {name: target for name, (target, _rule) in _CHIYUN_PARAMETERS.items()}
    for profile_id, family in (("gpt-image2", "gpt-image"),):
        profile = profiles[profile_id]
        assert profile["family"] == family
        assert profile["contract"]["parameter_schema"]["properties"] == trusted_chiyun_properties
        assert profile["contract"]["parameter_mappings"] == trusted_chiyun_mappings


def test_acceptance_log_projection_cannot_include_sensitive_request_fields() -> None:
    record = module.sanitized_result_record(
        phase="smoke", logical_model="banana", selected_channel="banana-chiyun",
        status="succeeded", mime="image/png", byte_count=123,
        duration_seconds=1.25, user_id="user-safe",
    )
    assert record == {
        "phase": "smoke",
        "logical_model": "banana",
        "selected_channel": "banana-chiyun",
        "status": "succeeded",
        "mime": "image/png",
        "bytes": 123,
        "duration_seconds": 1.25,
        "user_id": "user-safe",
    }
    serialized = module.render_record(record)
    for forbidden in ("key", "prompt", "cookie", "url", "authorization", "secret"):
        assert forbidden not in serialized.lower()


def test_real_acceptance_configures_nonzero_internal_estimated_rates() -> None:
    calls: list[tuple[str, str, object]] = []

    class Admin:
        def json(self, method, path, payload=None, expected=(200,)):
            calls.append((method, path, payload))
            return {"video_price_fen": 25, "image_price_fen": 120}

    module.configure_acceptance_rates(Admin())

    assert calls == [("PUT", "/api/v1/admin/usage/rates", {"video_price_fen": 25, "image_price_fen": 120})]


def test_failure_class_preserves_submission_unknown_and_partial_summary_counts() -> None:
    failure = module.PaidAcceptanceFailure("submission_unknown")
    assert module._failure_class(failure) == "submission_unknown"
    records = (
        module.sanitized_result_record(
            phase="banana_sample", logical_model="banana", selected_channel="banana-chiyun",
            status="succeeded", mime="image/png", byte_count=10, duration_seconds=1.2, user_id="user-safe",
        ),
        {
            "phase": "banana_sample", "logical_model": "banana", "selected_channel": "banana-t8star",
            "status": "failed", "failure_class": "submission_unknown", "user_id": "user-safe",
        },
        {
            "phase": "banana_sample", "logical_model": "banana", "selected_channel": "unresolved",
            "status": "not_run", "failure_class": "blocked_after_failure", "user_id": "user-safe",
        },
        {
            "phase": "banana_sample", "logical_model": "banana", "selected_channel": "unresolved",
            "status": "not_run", "failure_class": "blocked_after_failure", "user_id": "user-safe",
        },
    )

    summary = module._summary_record(records, user_id="user-safe", attempted_calls=2)

    assert summary["planned_calls"] == 4 and summary["attempted_calls"] == 2
    assert summary["succeeded"] == 1 and summary["failed"] == 1 and summary["not_run"] == 2
    assert summary["channel_distribution"] == {"banana-chiyun": 1, "banana-t8star": 1}
    assert summary["failure_classes"] == {"submission_unknown": 1, "blocked_after_failure": 2}
    assert summary["not_run_classes"] == {"blocked_after_failure": 2}
    assert summary["latency_seconds"] == {"minimum": 1.2, "maximum": 1.2, "total": 1.2}
    assert summary["bytes"] == 10 and summary["user_id"] == "user-safe"
    assert len(summary["outcomes"]) == 4


def test_paid_plan_finalizer_emits_partial_summary_after_a_failure() -> None:
    plan = module.paid_call_plan(("banana-chiyun",), banana_sample_count=2, maximum_paid_calls=3)
    emitted: list[str] = []

    def execute(call) -> dict[str, object]:
        if call.phase == "banana_sample":
            return {"phase": call.phase, "logical_model": "banana", "selected_channel": "banana-chiyun", "status": "failed", "failure_class": "business_4xx", "user_id": "user-safe"}
        return module.sanitized_result_record(phase="smoke", logical_model="banana", selected_channel="banana-chiyun", status="succeeded", mime="image/png", byte_count=1, duration_seconds=1, user_id="user-safe")

    recorder = module.PaidRunRecorder(plan, user_id="user-safe", emit=emitted.append)
    with pytest.raises(RuntimeError, match="paid acceptance call failed"), recorder:
        module.execute_paid_plan(
            plan,
            activate_channel=lambda _: None,
            activate_banana=lambda: None,
            execute=execute,
            recorder=recorder,
        )

    summary = _summary_lines(emitted)[0]
    assert summary["attempted_calls"] == 2
    assert summary["not_run"] == 1
    assert summary["failure_classes"] == {"business_4xx": 1, "blocked_after_failure": 1}


def _summary_lines(emitted: list[str]) -> list[dict[str, object]]:
    decoded = [json.loads(line) for line in emitted]
    return [item for item in decoded if item.get("phase") == "summary"]


def _guarded_failure_dependencies(
    *,
    upload_error: Exception | None = None,
    owner_status: int = 404,
    assigned_models: tuple[str, ...] = ("seedream", "seedance"),
    visible_models: tuple[str, ...] = ("seedream", "seedance"),
):
    class User:
        def upload_reference_png(self):
            if upload_error is not None:
                raise upload_error
            return "owned-asset"

        def json(self, method, path, payload=None, expected=(200,)):
            assert method == "GET" and path == "/api/v1/models"
            return {"models": [{"model_id": item} for item in visible_models]}

    class Admin:
        def request(self, method, path):
            assert method == "GET" and path == "/api/v1/assets/owned-asset"
            return owner_status, {}, b""

        def json(self, method, path, payload=None, expected=(200,)):
            if method == "PUT" and path == "/api/v1/admin/usage/rates":
                return {"video_price_fen": 25, "image_price_fen": 120}
            if method == "PUT":
                return {"model_ids": list(assigned_models)}
            raise RuntimeError("activation-private-message")

    return User(), Admin()


def test_guarded_paid_run_emits_one_redacted_summary_when_upload_fails(tmp_path: Path) -> None:
    plan = module.paid_call_plan(("seedream-ark", "seedance-ark"), banana_sample_count=0, maximum_paid_calls=2)
    private_error = RuntimeError("upload private prompt secret https://sensitive.example")
    user, admin = _guarded_failure_dependencies(upload_error=private_error)
    emitted: list[str] = []

    with pytest.raises(RuntimeError) as caught:
        module.run_guarded_paid_acceptance(
            admin=admin, user=user, data_dir=tmp_path, channel_ids=("seedream-ark", "seedance-ark"),
            model_ids=("seedream", "seedance"), plan=plan, user_id="user-safe", emit=emitted.append,
        )

    assert caught.value is private_error
    summaries = _summary_lines(emitted)
    assert len(summaries) == 1
    assert summaries[0]["attempted_calls"] == 0
    assert summaries[0]["failed"] == 0 and summaries[0]["not_run"] == 2
    assert summaries[0]["failure_classes"] == {"upload_failed": 2}
    assert [item["failure_class"] for item in summaries[0]["outcomes"]] == ["upload_failed", "upload_failed"]
    assert "private prompt" not in json.dumps(summaries[0]).lower()


@pytest.mark.parametrize(
    "dependency_overrides",
    [
        {"owner_status": 200},
        {"assigned_models": ("seedream",)},
        {"visible_models": ("seedream",)},
    ],
    ids=("owner", "assignment", "visibility"),
)
def test_guarded_paid_run_emits_one_summary_when_preflight_fails(tmp_path: Path, dependency_overrides: dict[str, object]) -> None:
    plan = module.paid_call_plan(("seedream-ark", "seedance-ark"), banana_sample_count=0, maximum_paid_calls=2)
    user, admin = _guarded_failure_dependencies(**dependency_overrides)
    emitted: list[str] = []

    with pytest.raises(RuntimeError):
        module.run_guarded_paid_acceptance(
            admin=admin, user=user, data_dir=tmp_path, channel_ids=("seedream-ark", "seedance-ark"),
            model_ids=("seedream", "seedance"), plan=plan, user_id="user-safe", emit=emitted.append,
        )

    summaries = _summary_lines(emitted)
    assert len(summaries) == 1
    assert summaries[0]["failure_classes"] == {"preflight_failed": 2}
    assert summaries[0]["failed"] == 0 and summaries[0]["not_run"] == 2


def test_guarded_paid_run_classifies_activation_failure_without_swallowing_it(tmp_path: Path) -> None:
    plan = module.paid_call_plan(("seedream-ark", "seedance-ark"), banana_sample_count=0, maximum_paid_calls=2)
    user, admin = _guarded_failure_dependencies()
    emitted: list[str] = []

    with pytest.raises(RuntimeError, match="activation-private-message"):
        module.run_guarded_paid_acceptance(
            admin=admin, user=user, data_dir=tmp_path, channel_ids=("seedream-ark", "seedance-ark"),
            model_ids=("seedream", "seedance"), plan=plan, user_id="user-safe", emit=emitted.append,
        )

    summaries = _summary_lines(emitted)
    assert len(summaries) == 1
    assert summaries[0]["attempted_calls"] == 0
    assert summaries[0]["failed"] == 1 and summaries[0]["not_run"] == 1
    assert summaries[0]["failure_classes"] == {"activation_failed": 1, "blocked_after_failure": 1}
    assert "activation-private-message" not in json.dumps(summaries[0])


def test_guarded_paid_run_preserves_unexpected_execution_exception_and_summarizes_once(tmp_path: Path, monkeypatch) -> None:
    plan = module.paid_call_plan(("seedream-ark", "seedance-ark"), banana_sample_count=0, maximum_paid_calls=2)
    user, admin = _guarded_failure_dependencies()
    private_error = RuntimeError("unexpected private prompt secret https://sensitive.example")
    monkeypatch.setattr(module, "_set_model_routes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_poll_and_download", lambda *_args, **_kwargs: (_ for _ in ()).throw(private_error))
    emitted: list[str] = []

    with pytest.raises(RuntimeError) as caught:
        module.run_guarded_paid_acceptance(
            admin=admin, user=user, data_dir=tmp_path, channel_ids=("seedream-ark", "seedance-ark"),
            model_ids=("seedream", "seedance"), plan=plan, user_id="user-safe", emit=emitted.append,
        )

    assert caught.value is private_error
    summaries = _summary_lines(emitted)
    assert len(summaries) == 1
    assert summaries[0]["attempted_calls"] == 1
    assert summaries[0]["failed"] == 1 and summaries[0]["not_run"] == 1
    assert summaries[0]["failure_classes"] == {"acceptance_contract": 1, "blocked_after_failure": 1}
    assert "unexpected private" not in json.dumps(summaries[0]).lower()


def _fallback_summary_from_stderr(capfd) -> dict[str, object]:
    lines = [line for line in capfd.readouterr().err.splitlines() if line]
    assert len(lines) == 1
    return json.loads(lines[0])


def test_summary_construction_failure_preserves_original_exception_and_uses_fixed_fallback(monkeypatch, capfd) -> None:
    plan = module.paid_call_plan(("seedream-ark",), banana_sample_count=0, maximum_paid_calls=1)
    original = RuntimeError("private business prompt")
    recorder = module.PaidRunRecorder(plan, user_id="user-safe", emit=lambda _line: None)
    monkeypatch.setattr(module, "_summary_record", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("summary construction failed")))

    with pytest.raises(RuntimeError) as caught:
        with recorder:
            raise original

    assert caught.value is original
    assert _fallback_summary_from_stderr(capfd) == {
        "failure_class": "summary_pipeline_failed", "phase": "summary", "status": "failed",
    }


def test_summary_render_failure_preserves_original_exception_and_uses_fixed_fallback(monkeypatch, capfd) -> None:
    plan = module.paid_call_plan(("seedream-ark",), banana_sample_count=0, maximum_paid_calls=1)
    original = RuntimeError("private business prompt")
    recorder = module.PaidRunRecorder(plan, user_id="user-safe", emit=lambda _line: None)
    real_render = module.render_record

    def fail_summary_render(record: dict[str, object]) -> str:
        if record.get("phase") == "summary":
            raise ValueError("summary render failed")
        return real_render(record)

    monkeypatch.setattr(module, "render_record", fail_summary_render)
    with pytest.raises(RuntimeError) as caught:
        with recorder:
            raise original

    assert caught.value is original
    assert _fallback_summary_from_stderr(capfd)["failure_class"] == "summary_pipeline_failed"


def test_summary_emit_failure_preserves_original_exception_and_uses_independent_fallback(capfd) -> None:
    plan = module.paid_call_plan(("seedream-ark",), banana_sample_count=0, maximum_paid_calls=1)
    original = RuntimeError("private business prompt")
    attempted: list[str] = []

    def fail_emit(line: str) -> None:
        attempted.append(line)
        raise OSError("primary summary sink failed")

    recorder = module.PaidRunRecorder(plan, user_id="user-safe", emit=fail_emit)
    with pytest.raises(RuntimeError) as caught:
        with recorder:
            raise original

    assert caught.value is original
    assert len(attempted) == 1 and json.loads(attempted[0])["phase"] == "summary"
    assert _fallback_summary_from_stderr(capfd)["status"] == "failed"


def test_fail_current_failure_preserves_original_exception_and_uses_fixed_fallback(monkeypatch, capfd) -> None:
    plan = module.paid_call_plan(("seedream-ark",), banana_sample_count=0, maximum_paid_calls=1)
    original = RuntimeError("private provider prompt")
    recorder = module.PaidRunRecorder(plan, user_id="user-safe", emit=lambda _line: None)
    attempts = 0

    def fail_current(*_args, **_kwargs) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("failure recorder failed")

    monkeypatch.setattr(recorder, "fail_current", fail_current)

    with pytest.raises(RuntimeError) as caught, recorder:
        module.execute_paid_plan(
            plan,
            activate_channel=lambda _channel: None,
            activate_banana=lambda: None,
            execute=lambda _call: (_ for _ in ()).throw(original),
            recorder=recorder,
        )

    assert caught.value is original
    assert attempts == 1
    assert _fallback_summary_from_stderr(capfd)["failure_class"] == "summary_pipeline_failed"


def test_all_summary_sinks_can_fail_without_masking_original_exception(monkeypatch) -> None:
    plan = module.paid_call_plan(("seedream-ark",), banana_sample_count=0, maximum_paid_calls=1)
    original = RuntimeError("private business prompt")
    recorder = module.PaidRunRecorder(
        plan,
        user_id="user-safe",
        emit=lambda _line: (_ for _ in ()).throw(OSError("primary sink failed")),
    )
    monkeypatch.setattr(module.os, "write", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fallback sink failed")))

    with pytest.raises(RuntimeError) as caught:
        with recorder:
            raise original

    assert caught.value is original


def test_paid_call_plan_runs_every_channel_smoke_before_the_banana_sample() -> None:
    plan = module.paid_call_plan(
        ("banana-chiyun", "banana-t8star", "seedance-ark"),
        banana_sample_count=2,
        maximum_paid_calls=5,
    )

    assert [(item.phase, item.channel_id, item.model_id) for item in plan] == [
        ("smoke", "banana-chiyun", "banana"),
        ("smoke", "banana-t8star", "banana"),
        ("smoke", "seedance-ark", "seedance"),
        ("banana_sample", None, "banana"),
        ("banana_sample", None, "banana"),
    ]


def test_paid_call_plan_rejects_unknown_channels_or_a_plan_over_twenty() -> None:
    with pytest.raises(ValueError, match="channel"):
        module.paid_call_plan(("banana-unknown",), banana_sample_count=0, maximum_paid_calls=1)
    with pytest.raises(ValueError, match="budget"):
        module.paid_call_plan(("banana-chiyun",), banana_sample_count=20, maximum_paid_calls=20)


def test_banana_batch_is_never_enabled_after_a_failed_channel_smoke() -> None:
    plan = module.paid_call_plan(
        ("banana-chiyun", "seedance-ark"),
        banana_sample_count=2,
        maximum_paid_calls=4,
    )
    events: list[str] = []

    def activate(channel_id: str) -> None:
        events.append(f"activate:{channel_id}")

    def activate_banana() -> None:
        events.append("activate:banana-batch")

    def execute(call) -> dict[str, object]:
        events.append(f"execute:{call.phase}:{call.channel_id or 'auto'}")
        if call.channel_id == "seedance-ark":
            raise RuntimeError("smoke failed")
        return module.sanitized_result_record(
            phase=call.phase, logical_model=call.model_id, selected_channel=call.channel_id or "unresolved",
            status="succeeded", mime="image/png", byte_count=1, duration_seconds=1, user_id="user-safe",
        )

    recorder = module.PaidRunRecorder(plan, user_id="user-safe", emit=lambda _line: None)
    with pytest.raises(RuntimeError, match="smoke failed"), recorder:
        module.execute_paid_plan(plan, activate_channel=activate, activate_banana=activate_banana, execute=execute, recorder=recorder)

    assert events == [
        "activate:banana-chiyun",
        "execute:smoke:banana-chiyun",
        "activate:seedance-ark",
        "execute:smoke:seedance-ark",
    ]


def test_paid_reference_png_meets_provider_minimum_dimensions(tmp_path: Path) -> None:
    image = module.reference_png()
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", image[16:24])
    assert width >= 300 and height >= 300
    path = tmp_path / "reference.png"
    path.write_bytes(image)
    assert module.verify_media_file(path, "image/png", "image") == {"codec_type": "video", "width": 640, "height": 640}


def test_acceptance_project_persists_a_canonical_edit_and_video_graph() -> None:
    project = module._project_document("owned-asset", "image-model", "video-model", 123)
    assert all({"position", "width", "height", "metadata"}.issubset(node) for node in project["nodes"])
    graphs = {node["id"]: node["metadata"]["graph"] for node in project["nodes"]}
    assert graphs["prompt"] == {"schemaVersion": 1, "role": "prompt", "text": "Paid acceptance prompt", "outputPortId": "prompt"}
    assert graphs["reference"]["items"][0] == {"id": "owned-item", "assetId": "owned-asset", "displayName": "reference.png", "mimeType": "image/png", "bytes": 123, "width": 640, "height": 640}
    assert graphs["image-model"]["operation"] == "image.edit"
    assert graphs["video-model"]["parameters"]["resolution"] == "480p"
    assert project["connections"] == [
        {"id": "prompt-image", "fromNodeId": "prompt", "fromPortId": "prompt", "toNodeId": "image-model", "toPortId": "prompt"},
        {"id": "reference-image", "fromNodeId": "reference", "fromPortId": "media", "toNodeId": "image-model", "toPortId": "reference_images"},
        {"id": "prompt-video", "fromNodeId": "prompt", "fromPortId": "prompt", "toNodeId": "video-model", "toPortId": "prompt"},
    ]


def test_acceptance_project_round_trips_through_the_owned_api(tmp_path: Path) -> None:
    _app, _accounts, admin, user, _admin_headers, user_headers = local_clients(tmp_path)
    project = module._project_document("owned-asset", "image-model", "video-model", 123)
    response = user.post("/api/v1/projects", headers=user_headers, json=project)
    assert response.status_code == 201
    assert response.json()["project"]["nodes"] == project["nodes"]
    assert response.json()["project"]["connections"] == project["connections"]
    assert admin.get("/api/v1/projects/paid-acceptance-canvas").status_code == 404


def test_two_paid_poll_flows_keep_the_result_reference_chain_and_stream_checks(monkeypatch) -> None:
    requests = module.paid_job_requests("image-model", "video-model", "owned-asset", "job-result.image-job.0")

    class User:
        def __init__(self): self.posts = []
        def json(self, method, path, payload=None, expected=(200,)):
            if method == "POST":
                self.posts.append(payload)
                job = "image-job" if payload["operation"] == "image.edit" else "video-job"
                repeats = sum(1 for item in self.posts if item["operation"] == payload["operation"])
                return {"id": job, "status": "queued" if repeats == 1 else "succeeded"}
            job = path.rsplit("/", 1)[-1]
            kind = "image" if job == "image-job" else "video"
            return {"id": job, "status": "succeeded", "results": [{"url": f"/api/v1/results/{job}/0", "asset_id": f"job-result.{job}.0", "media_type": kind}]}
        def request(self, method, path, payload=None, extra_headers=None):
            mime = "image/png" if "image-job" in path else "video/mp4"
            if method == "HEAD": return 200, {"content-type": mime}, b""
            assert extra_headers == {"Range": "bytes=0-1023"}
            return 206, {"content-range": "bytes 0-2/3"}, b"abc"
        def download(self, path): return ("image/png" if "image-job" in path else "video/mp4"), 3

    class Other:
        def request(self, method, path): return 404, {}, b""

    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    user = User(); other = Other()
    image = module._poll_and_download(user, other, requests[0], "image")
    assert image.result_asset_id == "job-result.image-job.0"
    video = module._poll_and_download(user, other, requests[1], "video")
    assert video.result_asset_id == "job-result.video-job.0"
    assert len(user.posts) == 4
    assert user.posts[0] == user.posts[1]
    assert user.posts[2] == user.posts[3]
    assert user.posts[2]["inputs"]["reference_images"] == [image.result_asset_id]


def test_runner_consumes_a_mode_0600_multi_key_bundle_once(tmp_path: Path, monkeypatch) -> None:
    key_file = tmp_path / "paid-keys.json"
    key_file.write_text(
        json.dumps({"ARK_API_KEY": "sentinel-ark-key", "CHIYUN_BANANA_API_KEY": "sentinel-chiyun-key"}),
        encoding="utf-8",
    )
    key_file.chmod(0o600)
    monkeypatch.setenv("AICC_ACCEPTANCE_KEY_FILE", str(key_file))
    for name in ("ARK_API_KEY", "CHIYUN_BANANA_API_KEY", "CHIYUN_GPT_IMAGE2_API_KEY", "T8STAR_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    values = module.consume_server_keys()

    assert set(values) == {"ARK_API_KEY", "CHIYUN_BANANA_API_KEY"}
    assert key_file.exists()
    assert "AICC_ACCEPTANCE_KEY_FILE" not in os.environ


def test_acceptance_server_bootstrap_validates_every_selected_route_without_provider_io(tmp_path: Path) -> None:
    channels = ("seedream-ark", "seedance-ark")
    data_dir = tmp_path / "paid-data"
    data_dir.mkdir(mode=0o700)
    pool_file = data_dir / ".credential-pools.json"
    module.write_credential_pool_config(
        pool_file,
        channels,
        {"ARK_API_KEY": "offline-ark-key"},
    )

    app, accounts = module.create_acceptance_app(
        data_dir=data_dir,
        static_dir=tmp_path / "dist",
        port=8998,
        credential_pool_path=pool_file,
        channel_ids=channels,
        chiyun_origin="https://chiyun.example",
        t8star_origin="https://t8star.example",
        maximum_provider_submissions=2,
    )

    assert accounts.created is True
    assert {item.model_id for item in app.state.canvas_store.list_logical_models()} == {"seedream", "seedance"}
    routes = app.state.canvas_store.list_model_routes(include_archived=False)
    assert {item.route_id for item in routes} == set(channels)
    assert all(item.enabled for item in routes)
    for route in routes:
        app.state.managed_routing_runtime.adapter_factory.validate_route(route)
    summaries = app.state.managed_routing_runtime.pools()
    assert set(summaries) == {f"paid-{channel}" for channel in channels}
    assert pool_file.stat().st_mode & 0o777 == 0o600
    assert app.state.managed_routing_runtime.submission_budget.remaining == 2


def test_runner_deletes_only_the_exact_pool_inode_it_created(tmp_path: Path) -> None:
    pool_file = tmp_path / "pool.json"
    owned = module.write_credential_pool_config(
        pool_file,
        ("seedream-ark",),
        {"ARK_API_KEY": "offline-ark-key"},
    )
    assert owned is not None

    replacement = tmp_path / "replacement.json"
    replacement.write_text("replacement-must-survive", encoding="utf-8")
    replacement.chmod(0o600)
    os.replace(replacement, pool_file)
    module._remove_owned_file(owned)
    assert pool_file.read_text(encoding="utf-8") == "replacement-must-survive"

    second = tmp_path / "second-pool.json"
    second_owned = module.write_credential_pool_config(
        second,
        ("seedream-ark",),
        {"ARK_API_KEY": "offline-ark-key"},
    )
    module._remove_owned_file(second_owned)
    assert not second.exists()


def test_real_runner_checks_project_owner_before_first_paid_post(monkeypatch) -> None:
    events: list[str] = []

    class User:
        def upload_reference_png(self): events.append("upload"); return "owned-asset"
        def json(self, method, path, payload=None, expected=(200,)):
            assert method == "POST" and path == "/api/v1/projects"
            events.append("project-post")
            return {"project": payload}

    class Admin:
        def request(self, method, path):
            events.append(f"admin-get:{path}")
            return (403 if path.startswith("/api/v1/assets/") else 404), {}, b""

    def paid(_user, _admin, payload, kind):
        events.append(f"paid:{kind}")
        token = "job-result.image-job.0" if kind == "image" else "job-result.video-job.0"
        return module.sanitized_result_record(
            phase="smoke", logical_model=str(payload["model_id"]), selected_channel=f"{kind}-channel",
            status="succeeded", mime=f"{kind}/fixture", byte_count=3, duration_seconds=1,
            user_id="user-safe",
        ), token, f"/api/v1/results/{kind}-job/0"

    monkeypatch.setattr(module, "_poll_and_download", paid)
    emitted: list[str] = []
    module.run_paid_graph(User(), Admin(), ["image-model", "video-model"], emitted.append)

    assert events == [
        "upload",
        "admin-get:/api/v1/assets/owned-asset",
        "project-post",
        "admin-get:/api/v1/projects/paid-acceptance-canvas",
        "paid:image",
        "paid:video",
    ]


def test_runner_signal_never_deletes_an_external_key_or_pool_locator(tmp_path: Path) -> None:
    key_file = tmp_path / "paid-key"
    key_file.write_text("sentinel-paid-key", encoding="utf-8"); key_file.chmod(0o600)
    pool_file = tmp_path / "paid-pool"
    pool_file.write_text("sentinel-paid-pool", encoding="utf-8"); pool_file.chmod(0o600)
    ready = tmp_path / "ready"
    environment = {**os.environ, "AICC_ACCEPTANCE_KEY_FILE": str(key_file), "AICC_ACCEPTANCE_POOL_FILE": str(pool_file), "AICC_ACCEPTANCE_SIGNAL_PROBE_FILE": str(ready)}
    environment.pop("ARK_API_KEY", None)
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/acceptance_real_media.py"), "--probe-signal-before-key"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if ready.exists(): break
        time.sleep(0.01)
    assert ready.exists()
    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode != 0
    assert key_file.exists()
    assert pool_file.exists()
    assert "sentinel-paid-key" not in stdout + stderr


def test_runner_signal_with_server_child_preserves_shell_owned_key_and_stops_child(tmp_path: Path) -> None:
    key_file = tmp_path / "paid-key"
    key_file.write_text(json.dumps({"ARK_API_KEY": "sentinel-paid-key"}), encoding="utf-8"); key_file.chmod(0o600)
    child_pid_file = tmp_path / "child-pid"
    environment = {**os.environ, "AICC_ACCEPTANCE_KEY_FILE": str(key_file), "AICC_ACCEPTANCE_SIGNAL_PROBE_FILE": str(child_pid_file)}
    for name in ("ARK_API_KEY", "CHIYUN_API_KEY", "T8STAR_API_KEY"):
        environment.pop(name, None)
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/acceptance_real_media.py"), "--probe-signal-server"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(100):
        if child_pid_file.exists(): break
        time.sleep(0.01)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text(encoding="ascii"))
    process.send_signal(signal.SIGHUP)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode != 0
    assert key_file.exists()
    for _ in range(100):
        try: os.kill(child_pid, 0)
        except ProcessLookupError: break
        time.sleep(0.01)
    else: raise AssertionError("server child survived acceptance runner signal")
    assert "sentinel-paid-key" not in stdout + stderr
