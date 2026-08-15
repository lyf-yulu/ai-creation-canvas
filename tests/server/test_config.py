from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_creation_canvas.config import Settings, load_comfyui_service_declarations


def _write_comfy_services(path: Path, services: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"services": services}), encoding="utf-8")


def test_comfy_config_loads_only_complete_trusted_declarations(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    path = root / "comfyui-services.json"
    _write_comfy_services(path, [{"service_id": "comfy-local", "base_url": "https://comfy.example:8188", "timeout_seconds": 3, "auth_header_ref": None}])

    declarations = load_comfyui_service_declarations(path, root)

    assert [(item.service_id, item.base_url, item.timeout_seconds, item.auth_header_ref) for item in declarations] == [
        ("comfy-local", "https://comfy.example:8188", 3, None)
    ]


def test_comfy_config_rejects_symlink_unknown_key_and_production_port(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    with pytest.raises(ValueError, match="ComfyUI services configuration is invalid"):
        load_comfyui_service_declarations(tmp_path / "missing.json", tmp_path)

    target = root / "target.json"
    _write_comfy_services(target, [])
    path = root / "comfyui-services.json"
    path.symlink_to(target)
    with pytest.raises(ValueError, match="ComfyUI services configuration is invalid"):
        load_comfyui_service_declarations(path, root)

    path.unlink()
    _write_comfy_services(path, [{"service_id": "comfy-local", "base_url": "https://comfy.example:8188", "timeout_seconds": 3, "auth_header_ref": None, "unknown": True}])
    with pytest.raises(ValueError, match="ComfyUI services configuration is invalid"):
        load_comfyui_service_declarations(path, root)

    _write_comfy_services(path, [{"service_id": "comfy-local", "base_url": "https://comfy.example:8991", "timeout_seconds": 3, "auth_header_ref": None}])
    with pytest.raises(ValueError, match="ComfyUI services configuration is invalid"):
        load_comfyui_service_declarations(path, root)


def test_settings_requires_a_trusted_root_for_comfyui_service_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ComfyUI services configuration requires a trusted root"):
        Settings("test", 8992, tmp_path / "data", "test-secret", comfyui_services_config_path=tmp_path / "services.json")


def test_credential_pool_path_is_optional_outside_managed_route_validation(tmp_path: Path) -> None:
    settings = Settings("development", 8992, tmp_path / "data", "local-secret")

    assert settings.credential_pools_path is None
    assert settings.credential_pools_root is None


def test_credential_pool_path_must_resolve_under_explicit_trusted_root(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    path = root / "credential-pools.yaml"
    settings = Settings(
        "production", 8991, tmp_path / "data", "deployment-secret",
        credential_pools_path=path,
        credential_pools_root=root,
    )

    assert settings.credential_pools_path == path
    assert settings.credential_pools_root == root.resolve()

    with pytest.raises(ValueError, match="credential pools path must resolve under trusted root"):
        Settings(
            "production", 8991, tmp_path / "data", "deployment-secret",
            credential_pools_path=root / ".." / "outside.yaml",
            credential_pools_root=root,
        )


def test_credential_pool_path_requires_a_trusted_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="credential pools path requires a trusted root"):
        Settings(
            "production", 8991, tmp_path / "data", "deployment-secret",
            credential_pools_path=tmp_path / "credential-pools.yaml",
        )


def test_credential_pool_path_rejects_a_symlink_before_loader_receives_it(tmp_path: Path) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    target = root / "target.yaml"
    target.write_text("version: 1\npools: {}\n", encoding="utf-8")
    link = root / "credential-pools.yaml"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="credential pools path must be a regular non-symlink file"):
        Settings(
            "production", 8991, tmp_path / "data", "deployment-secret",
            credential_pools_path=link,
            credential_pools_root=root,
        )
