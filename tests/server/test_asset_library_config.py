from __future__ import annotations

import base64
import json
from pathlib import Path
import stat

import pytest

from ai_creation_canvas.asset_library_config import (
    AssetLibraryConfigLoader,
    normalize_asset_library_secret_key,
    parse_asset_library_config_json,
)
from ai_creation_canvas.asset_library_import import import_asset_library_config


_B64_SK = base64.b64encode(b"decoded-sk-value").decode("utf-8")


def config_json(*, secret: str = _B64_SK) -> bytes:
    return json.dumps(
        {
            "version": 1,
            "ark_access_key": "AK-test-0001",
            "ark_secret_key": secret,
            "tos_access_key": "TOS-AK-test",
            "tos_secret_key": "TOS-SK-test",
            "tos_bucket": "canvas-uploads",
            "tos_region": "cn-beijing",
            "project_name": "Seedance2.0",
        },
        separators=(",", ":"),
    ).encode("utf-8")


def configured_loader(tmp_path: Path) -> tuple[AssetLibraryConfigLoader, Path]:
    target = tmp_path / "asset-library.json"
    target.write_bytes(config_json(secret="old-secret"))
    target.chmod(0o600)
    loader = AssetLibraryConfigLoader(target, production=True)
    loader.load()
    return loader, target


def test_parse_rejects_duplicate_keys_and_non_json() -> None:
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(b'{"version":1,"version":1}')
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(b"version: 1\nark_access_key: x\n")


def test_parse_normalizes_base64_secret_keys_and_keeps_plain_secrets() -> None:
    assert normalize_asset_library_secret_key(_B64_SK) == "decoded-sk-value"
    assert normalize_asset_library_secret_key("plain-text-secret") == "plain-text-secret"
    assert normalize_asset_library_secret_key("") == ""


def test_import_is_atomic_and_never_echoes_secrets(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)

    config = import_asset_library_config(loader, target, tmp_path, config_json())

    assert config.ark_access_key == "AK-test-0001"
    assert config.ark_secret_key == "decoded-sk-value"
    assert config.tos_bucket == "canvas-uploads"
    assert config.project_name == "Seedance2.0"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    summary = loader.load().safe_summary()
    assert summary["has_ark_access"] is True and summary["has_tos_access"] is True
    assert summary["tos_bucket"] == "canvas-uploads" and summary["tos_region"] == "cn-beijing"
    encoded = json.dumps(summary)
    assert "decoded-sk-value" not in encoded and "AK-test-0001" not in encoded
    assert "TOS-SK-test" not in encoded and "TOS-AK-test" not in encoded
    assert "decoded-sk-value" not in repr(config) and "AK-test-0001" not in repr(config)


def test_import_failure_preserves_previous_bytes_and_snapshot(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)
    before = target.read_bytes()

    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        import_asset_library_config(loader, target, tmp_path, b'{"version":1,"version":1}')

    assert target.read_bytes() == before
    assert loader.reload().ark_access_key == "AK-test-0001"


def test_import_rejects_path_escape_and_symlink_target(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)
    outside = tmp_path.parent / "asset-library-outside.json"
    try:
        with pytest.raises(ValueError, match="asset library configuration is invalid"):
            import_asset_library_config(loader, outside, tmp_path, config_json())
    finally:
        outside.unlink(missing_ok=True)
    link = tmp_path / "asset-library-link.json"
    link.symlink_to(target)
    try:
        with pytest.raises(ValueError, match="asset library configuration is invalid"):
            import_asset_library_config(loader, link, tmp_path, config_json())
    finally:
        link.unlink(missing_ok=True)


def test_loader_reload_keeps_last_good_snapshot_on_bad_file(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)
    target.write_text("{broken", encoding="utf-8")

    snapshot = loader.reload()

    assert snapshot.ark_access_key == "AK-test-0001"


def test_loader_rejects_unrestricted_file_mode_in_production(tmp_path: Path) -> None:
    target = tmp_path / "asset-library.json"
    target.write_bytes(config_json())
    target.chmod(0o644)
    loader = AssetLibraryConfigLoader(target, production=True)
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        loader.load()


def test_parse_rejects_control_characters_and_oversized_fields(tmp_path: Path) -> None:
    payload = json.loads(config_json().decode("utf-8"))
    payload["ark_secret_key"] = "bad\nsecret"
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    payload = json.loads(config_json().decode("utf-8"))
    payload["tos_bucket"] = "UPPER-case"
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    payload = json.loads(config_json().decode("utf-8"))
    payload["tos_region"] = "bad region!"
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    payload = json.loads(config_json().decode("utf-8"))
    payload["project_name"] = "x" * 65
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    payload = json.loads(config_json().decode("utf-8"))
    payload["ark_access_key"] = "  spaced-key  "
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(b"x" * (64 * 1024 + 1))


def test_parse_defaults_project_name_and_requires_version(tmp_path: Path) -> None:
    payload = json.loads(config_json().decode("utf-8"))
    del payload["project_name"]
    config = parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    assert config.project_name == "Seedance2.0"
    payload = json.loads(config_json().decode("utf-8"))
    payload["version"] = 2
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
    payload = json.loads(config_json().decode("utf-8"))
    payload["unexpected"] = "field"
    with pytest.raises(ValueError, match="asset library configuration is invalid"):
        parse_asset_library_config_json(json.dumps(payload).encode("utf-8"))
