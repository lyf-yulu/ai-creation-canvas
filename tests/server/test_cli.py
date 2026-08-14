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
        "--trusted-host", "canvas.example",
        "--trusted-host", "portal.example",
    ])
    monkeypatch.setattr(entrypoint, "create_app", fake_create_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint.main()

    settings = received["settings"]
    assert settings.credential_pools_path == pools_path
    assert settings.credential_pools_root == pools_path.parent.resolve()
    assert settings.trusted_hosts == ("canvas.example", "portal.example")


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
        "--trusted-host", "canvas.example",
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
        "--trusted-host", "canvas.example",
        "--host", "127.0.0.2",
    ])
    monkeypatch.setattr(entrypoint, "create_app", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda _app, **kwargs: received.update(kwargs))

    entrypoint.main()

    assert received["host"] == "127.0.0.2"
