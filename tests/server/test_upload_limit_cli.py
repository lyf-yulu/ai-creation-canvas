from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import ai_creation_canvas.__main__ as entrypoint


def test_serve_local_maps_bounded_mib_flags_to_settings_bytes(tmp_path, monkeypatch):
    received = {}

    def fake_create_local_app(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(router=SimpleNamespace(on_startup=[])), None

    monkeypatch.setattr(entrypoint, "create_local_app", fake_create_local_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint._run_serve_local([
        "--port", "8993",
        "--data-dir", str(tmp_path / "data"),
        "--static-dir", str(tmp_path / "dist"),
        "--max-image-upload-mib", "12",
        "--max-video-upload-mib", "96",
        "--max-audio-upload-mib", "40",
        "--upload-concurrency", "7",
        "--user-asset-quota-mib", "3072",
        "--total-asset-quota-mib", "12288",
    ])

    assert received["max_image_upload_bytes"] == 12 * 1024 * 1024
    assert received["max_video_upload_bytes"] == 96 * 1024 * 1024
    assert received["max_audio_upload_bytes"] == 40 * 1024 * 1024
    assert received["upload_concurrency"] == 7
    assert received["user_asset_quota_bytes"] == 3072 * 1024 * 1024
    assert received["total_asset_quota_bytes"] == 12288 * 1024 * 1024


def test_serve_local_defaults_to_a_loopback_bind_and_origin(tmp_path, monkeypatch):
    received = {}

    def fake_create_local_app(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(router=SimpleNamespace(on_startup=[])), None

    monkeypatch.setattr(entrypoint, "create_local_app", fake_create_local_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda _app, **kwargs: received.update(run=kwargs))

    entrypoint._run_serve_local([
        "--port", "8993",
        "--data-dir", str(tmp_path / "data"),
        "--static-dir", str(tmp_path / "dist"),
    ])

    assert received["public_origins"] == ()
    assert received["run"] == {"host": "127.0.0.1", "port": 8993}


def test_serve_local_rejects_a_non_loopback_bind_without_a_public_origin(tmp_path):
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local([
            "--host", "0.0.0.0",
            "--port", "8993",
            "--data-dir", str(tmp_path / "data"),
            "--static-dir", str(tmp_path / "dist"),
        ])


def test_serve_local_rejects_a_lan_public_origin_with_a_different_port(tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint, "create_local_app", lambda **_kwargs: pytest.fail("must not construct app"))

    with pytest.raises(SystemExit):
        entrypoint._run_serve_local([
            "--host", "0.0.0.0",
            "--public-origin", "http://192.168.1.20:8992",
            "--port", "8993",
            "--data-dir", str(tmp_path / "data"),
            "--static-dir", str(tmp_path / "dist"),
        ])


def test_serve_local_wires_a_lan_bind_and_exact_public_origin(tmp_path, monkeypatch):
    received = {}

    def fake_create_local_app(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(router=SimpleNamespace(on_startup=[])), None

    monkeypatch.setattr(entrypoint, "create_local_app", fake_create_local_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda _app, **kwargs: received.update(run=kwargs))

    entrypoint._run_serve_local([
        "--host", "0.0.0.0",
        "--public-origin", "http://192.168.1.20:8992",
        "--port", "8992",
        "--data-dir", str(tmp_path / "data"),
        "--static-dir", str(tmp_path / "dist"),
    ])

    assert received["public_origins"] == ("http://192.168.1.20:8992",)
    assert received["run"] == {"host": "0.0.0.0", "port": 8992}


def test_serve_local_canonicalizes_a_lan_public_origin_before_app_construction(tmp_path, monkeypatch):
    received = {}

    def fake_create_local_app(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(router=SimpleNamespace(on_startup=[])), None

    monkeypatch.setattr(entrypoint, "create_local_app", fake_create_local_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint._run_serve_local([
        "--host", "0.0.0.0",
        "--public-origin", "HTTP://CANVAS.LOCAL:80",
        "--port", "80",
        "--data-dir", str(tmp_path / "data"),
        "--static-dir", str(tmp_path / "dist"),
    ])

    assert received["public_origins"] == ("http://canvas.local",)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--host", "::1"],
        ["--public-origin", "https://192.168.1.20:8992"],
        ["--public-origin", "http://8.8.8.8:8992"],
        ["--public-origin", "http://0.0.0.0:8992"],
        ["--public-origin", "http://canvas.example:8992"],
    ],
)
def test_serve_local_rejects_non_lan_bind_or_public_origin(tmp_path, arguments):
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local([
            *arguments,
            "--port", "8993",
            "--data-dir", str(tmp_path / "data"),
            "--static-dir", str(tmp_path / "dist"),
        ])


def test_serve_local_rejects_browser_opening_for_a_non_loopback_bind(tmp_path):
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local([
            "--host", "0.0.0.0",
            "--public-origin", "http://canvas.local:8993",
            "--open",
            "--port", "8993",
            "--data-dir", str(tmp_path / "data"),
            "--static-dir", str(tmp_path / "dist"),
        ])


def test_production_parser_exposes_the_same_upload_limit_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "production-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
        "--max-image-upload-mib", "11",
        "--max-video-upload-mib", "80",
        "--max-audio-upload-mib", "24",
        "--upload-concurrency", "6",
        "--user-asset-quota-mib", "4096",
        "--total-asset-quota-mib", "16384",
    ])

    args = entrypoint._arguments()

    assert (args.max_image_upload_bytes, args.max_video_upload_bytes, args.max_audio_upload_bytes) == (
        11 * 1024 * 1024,
        80 * 1024 * 1024,
        24 * 1024 * 1024,
    )
    assert (args.upload_concurrency, args.user_asset_quota_bytes, args.total_asset_quota_bytes) == (6, 4096 * 1024 * 1024, 16384 * 1024 * 1024)


@pytest.mark.parametrize("value", ["0", "2049", "1.5", "lots"])
def test_upload_limit_flags_reject_out_of_range_or_non_integer_mib(tmp_path, value):
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local([
            "--port", "8993",
            "--data-dir", str(tmp_path / "data"),
            "--static-dir", str(tmp_path / "dist"),
            "--max-video-upload-mib", value,
        ])


@pytest.mark.parametrize(("flag", "value"), [("--upload-concurrency", "0"), ("--upload-concurrency", "33"), ("--user-asset-quota-mib", "0"), ("--total-asset-quota-mib", "0")])
def test_upload_resource_flags_are_bounded(tmp_path, flag, value):
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local(["--port", "8993", "--data-dir", str(tmp_path / "data"), "--static-dir", str(tmp_path / "dist"), flag, value])
