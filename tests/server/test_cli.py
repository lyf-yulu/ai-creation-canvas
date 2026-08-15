from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import ai_creation_canvas.__main__ as entrypoint


def test_production_cli_wires_credential_pool_path_under_an_explicit_trusted_root(tmp_path: Path, monkeypatch) -> None:
    received: dict[str, object] = {}
    pools_path = tmp_path / "config" / "credential-pools.yaml"

    def fake_create_app(settings, *, static_dir):
        received["settings"] = settings
        received["static_dir"] = static_dir
        return SimpleNamespace()

    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "deployment-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
        "--credential-pools", str(pools_path),
        "--credential-pools-root", str(pools_path.parent),
    ])
    monkeypatch.setattr(entrypoint, "create_app", fake_create_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint.main()

    settings = received["settings"]
    assert settings.credential_pools_path == pools_path
    assert settings.credential_pools_root == pools_path.parent.resolve()


def test_staged_cli_wires_comfyui_services_under_its_explicit_trusted_root(tmp_path: Path, monkeypatch) -> None:
    received: dict[str, object] = {}
    comfy_path = tmp_path / "config" / "comfyui-services.json"

    def fake_create_app(settings, *, static_dir):
        received["settings"] = settings
        received["static_dir"] = static_dir
        return SimpleNamespace()

    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "deployment-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
        "--comfyui-services", str(comfy_path),
    ])
    monkeypatch.setattr(entrypoint, "create_app", fake_create_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint.main()

    settings = received["settings"]
    assert settings.comfyui_services_config_path == comfy_path
    assert settings.comfyui_services_config_root == comfy_path.parent.resolve()
