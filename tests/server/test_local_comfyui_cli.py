from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import ai_creation_canvas.__main__ as entrypoint
from ai_creation_canvas.__main__ import create_local_app
from ai_creation_canvas.errors import AdapterNotFoundError


FIXTURE = Path(__file__).parents[1] / "fixtures" / "comfy" / "core-load-save-workflow.json"


def _write_services(path: Path, *, auth_header_ref: str | None = None, base_url: str = "http://127.0.0.1:8188") -> None:
    path.write_text(json.dumps({"services": [{
        "service_id": "comfy-local", "base_url": base_url,
        "timeout_seconds": 3, "auth_header_ref": auth_header_ref,
    }]}), encoding="utf-8")


def _admin_headers(client: TestClient, password: str) -> dict[str, str]:
    logged_in = client.post("/api/v1/auth/login", json={"username": "canvas-admin", "password": password}).json()
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": logged_in["csrf_token"]},
        json={"current_password": password, "new_password": "new-admin-correct-horse"},
    ).json()
    return {"Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": changed["csrf_token"]}


def test_local_app_loads_trusted_comfy_services_and_allows_lifecycle_enable_without_network(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    config = data_dir / "config" / "comfyui-services.json"
    config.parent.mkdir(parents=True)
    _write_services(config, auth_header_ref="local-reference-not-a-secret")
    app, accounts = create_local_app(
        port=8993, data_dir=data_dir, static_dir=tmp_path / "dist",
        bootstrap_if_empty=True, comfyui_services_config=config,
    )
    assert accounts is not None and accounts.created
    assert tuple(adapter.service_id for adapter in app.state.comfy_workflow_services) == ("comfy-local",)
    assert app.state.adapter_registry.comfy_workflow("comfy-local") is app.state.comfy_workflow_services[0]

    client = TestClient(app, base_url="http://127.0.0.1:8993")
    headers = _admin_headers(client, accounts.admin_password)
    imported = client.post(
        "/api/v1/admin/comfy-workflows/import", headers=headers,
        files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")},
        data={"display_name": "Core", "service_id": "comfy-local"},
    )
    assert imported.status_code == 201, imported.text
    enabled = client.post(
        f"/api/v1/admin/comfy-workflows/{imported.json()['workflow_id']}/enable",
        headers=headers, json={"revision": 1},
    )
    assert enabled.status_code == 200, enabled.text
    response_text = imported.text + enabled.text
    assert "127.0.0.1:8188" not in response_text
    assert "local-reference-not-a-secret" not in response_text


def test_local_app_rejects_unsafe_comfy_service_path(tmp_path: Path) -> None:
    trusted = tmp_path / "data" / "config"
    trusted.mkdir(parents=True)
    target = trusted / "services.json"
    _write_services(target)
    symlink = trusted / "comfyui-services.json"
    symlink.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink"):
        create_local_app(
            port=8993, data_dir=tmp_path / "data", static_dir=tmp_path / "dist", comfyui_services_config=symlink
        )


def test_local_app_rejects_comfy_config_outside_its_data_config_root(tmp_path: Path) -> None:
    external = tmp_path / "external" / "comfyui-services.json"
    external.parent.mkdir()
    _write_services(external)

    with pytest.raises(ValueError, match="local data config root"):
        create_local_app(
            port=8993, data_dir=tmp_path / "data", static_dir=tmp_path / "dist", comfyui_services_config=external
        )


@pytest.mark.parametrize("base_url", ("http://localhost:8188", "https://comfy.example:8188"))
def test_local_app_rejects_non_numeric_loopback_comfy_hosts(tmp_path: Path, base_url: str) -> None:
    config = tmp_path / "data" / "config" / "comfyui-services.json"
    config.parent.mkdir(parents=True)
    _write_services(config, base_url=base_url)

    with pytest.raises(ValueError, match="numeric loopback"):
        create_local_app(
            port=8993, data_dir=tmp_path / "data", static_dir=tmp_path / "dist", comfyui_services_config=config
        )


def test_local_app_retains_production_port_refusal_for_comfy_services(tmp_path: Path) -> None:
    config = tmp_path / "data" / "config" / "comfyui-services.json"
    config.parent.mkdir(parents=True)
    _write_services(config, base_url="http://127.0.0.1:8991")

    with pytest.raises(ValueError, match="ComfyUI services configuration is invalid"):
        create_local_app(
            port=8993, data_dir=tmp_path / "data", static_dir=tmp_path / "dist", comfyui_services_config=config
        )


def test_local_app_without_comfy_config_registers_no_comfy_service(tmp_path: Path) -> None:
    app, _accounts = create_local_app(port=8993, data_dir=tmp_path / "data", static_dir=tmp_path / "dist")

    assert app.state.comfy_workflow_services == ()
    with pytest.raises(AdapterNotFoundError):
        app.state.adapter_registry.comfy_workflow("comfy-local")


def test_run_local_script_only_forwards_a_nonempty_comfy_service_environment_value() -> None:
    script = (Path(__file__).parents[2] / "scripts" / "run-local.sh").read_text(encoding="utf-8")

    assert 'if [ -n "${AICC_COMFYUI_SERVICES:-}" ]; then' in script
    assert 'set -- --comfyui-services "$AICC_COMFYUI_SERVICES"' in script
    assert "else\n    set --\nfi" in script
    assert '--open \\\n    "$@"' in script


def test_serve_local_passes_explicit_comfy_service_path_to_local_app(tmp_path: Path, monkeypatch) -> None:
    received: dict[str, object] = {}

    def fake_create_local_app(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(router=SimpleNamespace(on_startup=[])), None

    config = tmp_path / "comfyui-services.json"
    monkeypatch.setattr(entrypoint, "create_local_app", fake_create_local_app)
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda *_args, **_kwargs: None)

    entrypoint._run_serve_local([
        "--port", "8993", "--data-dir", str(tmp_path / "data"), "--static-dir", str(tmp_path / "dist"),
        "--comfyui-services", str(config),
    ])

    assert received["comfyui_services_config"] == config
