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
    ])

    assert received["max_image_upload_bytes"] == 12 * 1024 * 1024
    assert received["max_video_upload_bytes"] == 96 * 1024 * 1024
    assert received["max_audio_upload_bytes"] == 40 * 1024 * 1024


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
    ])

    args = entrypoint._arguments()

    assert (args.max_image_upload_bytes, args.max_video_upload_bytes, args.max_audio_upload_bytes) == (
        11 * 1024 * 1024,
        80 * 1024 * 1024,
        24 * 1024 * 1024,
    )


@pytest.mark.parametrize("value", ["0", "2049", "1.5", "lots"])
def test_upload_limit_flags_reject_out_of_range_or_non_integer_mib(tmp_path, value):
    with pytest.raises(SystemExit):
        entrypoint._run_serve_local([
            "--port", "8993",
            "--data-dir", str(tmp_path / "data"),
            "--static-dir", str(tmp_path / "dist"),
            "--max-video-upload-mib", value,
        ])
