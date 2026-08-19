from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.credential_pool_import import import_credential_pool_json
from ai_creation_canvas.credential_pools import CredentialPoolLoader, parse_credential_pool_json
from ai_creation_canvas.domain.registry import AdapterRegistry
from ai_creation_canvas.model_registry import ProviderDefinition
from ai_creation_canvas.storage.sqlite import CanvasStore


ORIGIN = "http://127.0.0.1:45995"


def pool_json(*, pool_id: str = "banana-chiyun", secret: str = "test-secret") -> bytes:
    return json.dumps(
        {
            "version": 1,
            "pools": {
                pool_id: {
                    "provider": "chiyun-banana",
                    "group": "banana",
                    "allowed_families": ["nano-banana"],
                    "keys": [{"id": "key-1", "api_key": secret, "max_concurrency": 2}],
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def configured_loader(tmp_path: Path):
    target = tmp_path / "credential-pools.json"
    target.write_bytes(pool_json(pool_id="old-pool", secret="old-secret"))
    target.chmod(0o600)
    loader = CredentialPoolLoader(target, production=True)
    loader.load()
    return loader, target


def test_json_candidate_rejects_duplicate_keys_and_yaml() -> None:
    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        parse_credential_pool_json(b'{"version":1,"version":1,"pools":{}}')
    with pytest.raises(ValueError, match="credential pools configuration is invalid"):
        parse_credential_pool_json(b"version: 1\npools: {}\n")


def test_import_replaces_valid_json_atomically_and_returns_only_safe_summary(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)

    result = import_credential_pool_json(loader, target, tmp_path, pool_json(secret="new-secret"))

    imported = result.snapshot.get("banana-chiyun")
    assert imported is not None and imported.keys[0].secret == "new-secret"
    assert result.snapshot.get("old-pool") is None
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    encoded = json.dumps(result.safe_summaries)
    assert "new-secret" not in encoded and "key-1" not in encoded


def test_import_failure_preserves_previous_bytes_and_snapshot(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)
    before = target.read_bytes()

    with pytest.raises(ValueError, match="JSON 语法或字段有误"):
        import_credential_pool_json(loader, target, tmp_path, b'{"version":1,"version":1,"pools":{}}')

    assert target.read_bytes() == before
    assert loader.reload().get("old-pool") is not None


def test_import_rejects_untrusted_provider_family_and_oversized_file(tmp_path: Path) -> None:
    loader, target = configured_loader(tmp_path)
    untrusted = json.loads(pool_json())
    untrusted["pools"]["banana-chiyun"]["provider"] = "attacker"

    with pytest.raises(ValueError, match="组合不受支持"):
        import_credential_pool_json(loader, target, tmp_path, json.dumps(untrusted).encode())
    with pytest.raises(ValueError, match="JSON 语法或字段有误"):
        import_credential_pool_json(loader, target, tmp_path, b"{" + b" " * (1024 * 1024) + b"}")


def test_import_rejects_target_or_parent_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    linked = root / "linked"
    linked.symlink_to(real, target_is_directory=True)
    target = linked / "credential-pools.json"
    real_target = real / "credential-pools.json"
    real_target.write_bytes(pool_json())
    real_target.chmod(0o600)
    loader = CredentialPoolLoader(real_target, production=True)
    loader.load()

    with pytest.raises(ValueError, match="服务器配置文件位置不安全"):
        import_credential_pool_json(loader, target, root, pool_json(secret="new-secret"))


def import_clients(tmp_path: Path):
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target = config_dir / "credential-pools.json"
    target.write_bytes(pool_json(secret="old-secret"))
    target.chmod(0o600)
    store = CanvasStore(data_dir)
    store.create_provider_definition(
        ProviderDefinition("chiyun-banana", "Chiyun Banana", "chiyun_gemini_images", "https://chiyun.work", "unused"),
        actor_user_id="bootstrap",
    )
    registry = AdapterRegistry()
    app = create_app(
        Settings(
            "test",
            45995,
            data_dir,
            "unused",
            identity_mode="local",
            allowed_origins=(ORIGIN,),
            credential_pools_path=target,
            credential_pools_root=config_dir,
        ),
        static_dir=tmp_path / "dist",
        canvas_store=store,
        registry=registry,
        model_catalog=ModelCatalog(registry),
    )
    accounts = app.state.local_auth.bootstrap_accounts(())
    admin = TestClient(app, base_url=ORIGIN)
    user = TestClient(app, base_url=ORIGIN)

    def login(client: TestClient, username: str, password: str) -> dict[str, str]:
        first = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
        changed = client.post(
            "/api/v1/auth/change-password",
            headers={"Origin": ORIGIN, "X-CSRF-Token": first["csrf_token"]},
            json={"current_password": password, "new_password": f"new-{username}-correct-horse"},
        ).json()
        return {"Origin": ORIGIN, "X-CSRF-Token": changed["csrf_token"]}

    assert accounts.user is not None
    return (
        app,
        target,
        admin,
        user,
        login(admin, accounts.admin_username, accounts.admin_password),
        login(user, accounts.user_username, accounts.user_password),
    )


def test_admin_import_api_replaces_pool_and_never_returns_secret(tmp_path: Path) -> None:
    app, target, admin, user, admin_headers, user_headers = import_clients(tmp_path)
    del user, user_headers

    response = admin.post(
        "/api/v1/admin/credential-pools/import",
        headers=admin_headers,
        files={"file": ("credential-pools.json", pool_json(secret="new-secret"), "application/json")},
    )

    assert response.status_code == 200
    assert response.json()["pools"][0]["pool_id"] == "banana-chiyun"
    assert response.json()["pools"][0]["key_count"] == 1
    assert "new-secret" not in response.text and "key-1" not in response.text
    assert app.state.credential_pool_loader.reload().get("banana-chiyun").keys[0].secret == "new-secret"
    assert b"new-secret" in target.read_bytes()


def test_import_api_hides_from_users_checks_csrf_and_preserves_old_file_on_invalid_json(tmp_path: Path) -> None:
    app, target, admin, user, admin_headers, user_headers = import_clients(tmp_path)
    del app
    original = target.read_bytes()
    files = {"file": ("credential-pools.json", pool_json(secret="new-secret"), "application/json")}

    hidden = user.post("/api/v1/admin/credential-pools/import", headers=user_headers, files=files)
    forbidden = admin.post("/api/v1/admin/credential-pools/import", files=files)
    invalid = admin.post(
        "/api/v1/admin/credential-pools/import",
        headers=admin_headers,
        files={"file": ("credential-pools.json", b'{"version":1,"version":1,"pools":{}}', "application/json")},
    )

    assert hidden.status_code == 404
    assert forbidden.status_code == 403
    assert invalid.status_code == 400
    assert "old-secret" not in invalid.text
    assert target.read_bytes() == original
