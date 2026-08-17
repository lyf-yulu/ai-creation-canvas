from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

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
        "--trusted-host", "127.0.0.1",
    ])
    monkeypatch.setattr(entrypoint, "create_app", fake_create_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint.main()

    settings = received["settings"]
    assert settings.credential_pools_path == pools_path
    assert settings.credential_pools_root == pools_path.parent.resolve()
    assert settings.trusted_hosts == ("127.0.0.1",)


def test_serve_local_cli_wires_asset_library_config_under_an_explicit_trusted_root(tmp_path: Path, monkeypatch) -> None:
    received: dict[str, object] = {}
    library_path = tmp_path / "config" / "asset-library.json"

    def fake_create_local_app(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(), None

    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "serve-local",
        "--data-dir", str(tmp_path / "data"),
        "--static-dir", str(tmp_path / "dist"),
        "--asset-library-config", str(library_path),
        "--asset-library-config-root", str(library_path.parent),
    ])
    monkeypatch.setattr(entrypoint, "create_local_app", fake_create_local_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint.main()

    assert received["asset_library_config"] == library_path
    assert received["asset_library_config_root"] == library_path.parent


def test_serve_local_cli_rejects_asset_library_config_without_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "serve-local",
        "--data-dir", str(tmp_path / "data"),
        "--static-dir", str(tmp_path / "dist"),
        "--asset-library-config", str(tmp_path / "asset-library.json"),
    ])

    with pytest.raises(ValueError, match="asset library config path requires a trusted root"):
        entrypoint.main()


def test_production_cli_requires_an_explicit_trusted_host_before_creating_the_app(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "deployment-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
    ])
    monkeypatch.setattr(entrypoint, "create_app", lambda *_args, **_kwargs: pytest.fail("must not construct app"))

    with pytest.raises(SystemExit):
        entrypoint.main()


@pytest.mark.parametrize("trusted_hosts", [("canvas.example",), ("192.168.1.20",), ("127.0.0.1", "canvas.example")])
def test_production_cli_rejects_non_portal_trusted_hosts_before_creating_the_app(tmp_path: Path, monkeypatch, trusted_hosts: tuple[str, ...]) -> None:
    arguments = [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "deployment-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
    ]
    for trusted_host in trusted_hosts:
        arguments.extend(("--trusted-host", trusted_host))
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(entrypoint, "create_app", lambda *_args, **_kwargs: pytest.fail("must not construct app"))

    with pytest.raises(SystemExit):
        entrypoint.main()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::1", "not-an-address"])
def test_production_cli_rejects_non_loopback_or_invalid_bind_hosts_before_creating_the_app(tmp_path: Path, monkeypatch, host: str) -> None:
    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--host", host,
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "deployment-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
        "--trusted-host", "127.0.0.1",
    ])
    monkeypatch.setattr(entrypoint, "create_app", lambda *_args, **_kwargs: pytest.fail("must not construct app"))

    with pytest.raises(SystemExit):
        entrypoint.main()


def test_production_cli_defaults_to_loopback_and_forwards_an_explicit_bind_host(tmp_path: Path, monkeypatch) -> None:
    received: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", [
        "ai_creation_canvas",
        "--environment", "production",
        "--port", "8991",
        "--data-dir", str(tmp_path / "data"),
        "--portal-internal-token", "deployment-secret",
        "--portal-base-url", "https://portal.example",
        "--services-config", str(tmp_path / "services.json"),
        "--trusted-host", "127.0.0.1",
        "--host", "127.0.0.2",
    ])
    monkeypatch.setattr(entrypoint, "create_app", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda _app, **kwargs: received.update(kwargs))

    entrypoint.main()

    assert received["host"] == "127.0.0.2"
    assert received["proxy_headers"] is False


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
        "--trusted-host", "127.0.0.1",
        "--comfyui-services", str(comfy_path),
    ])
    monkeypatch.setattr(entrypoint, "create_app", fake_create_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint.main()

    settings = received["settings"]
    assert settings.comfyui_services_config_path == comfy_path
    assert settings.comfyui_services_config_root == comfy_path.parent.resolve()
