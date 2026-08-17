from __future__ import annotations

import sqlite3

import pytest

from ai_creation_canvas.__main__ import create_local_user, initialize_local_accounts, reset_local_password
from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.storage.sqlite import CanvasStore


def test_initialize_local_accounts_prints_credentials_once(tmp_path) -> None:
    first: list[str] = []
    second: list[str] = []

    created = initialize_local_accounts(tmp_path / "state", output=first.append)
    repeated = initialize_local_accounts(tmp_path / "state", output=second.append)

    assert created is True
    assert first[0] == "Local accounts created. One-time credentials:"
    assert first[1].startswith("canvas-admin: ")
    assert len(first[1].removeprefix("canvas-admin: ")) >= 12
    assert first[2].startswith("canvas-user: ")
    assert len(first[2].removeprefix("canvas-user: ")) >= 12
    assert repeated is False
    assert second == ["Local accounts are already initialized; no passwords were displayed."]


def test_local_cli_bootstraps_demo_once_and_can_reset_one_password(tmp_path) -> None:
    output: list[str] = []
    assert initialize_local_accounts(tmp_path, initial_model_ids=("demo-image-v1",), output=output.append)
    assert any("canvas-admin:" in line for line in output)
    assert any("canvas-user:" in line for line in output)

    output.clear()
    assert not initialize_local_accounts(tmp_path, initial_model_ids=("other-model",), output=output.append)
    assert output == ["Local accounts are already initialized; no passwords were displayed."]
    store = CanvasStore(tmp_path)
    user = store.user_by_username("canvas-user")
    assert user is not None
    assert store.assigned_models(str(user["user_id"])) == ("demo-image-v1",)

    reset_output: list[str] = []
    password = reset_local_password(tmp_path, "canvas-user", output=reset_output.append)
    assert password and reset_output == [f"canvas-user: {password}"]
    issued = LocalAuthService(CanvasStore(tmp_path), session_ttl_seconds=60).login("canvas-user", password)
    assert issued.must_change_password is True


def test_create_local_user_adds_permanent_enabled_account(tmp_path) -> None:
    output: list[str] = []
    normalized = create_local_user(tmp_path, " ADMIN ", "长期管理员", "chengdumijing12", "admin", output=output.append)

    assert normalized == "admin"
    assert output == ["Local account created: admin (role admin)."]
    store = CanvasStore(tmp_path)
    row = store.user_by_username("admin")
    assert row is not None
    assert row["enabled"] == 1
    assert row["must_change_password"] == 0
    assert row["approval_status"] == "approved"
    assert row["role"] == "admin"
    assert "chengdumijing12" not in store.database.read_bytes().decode("latin1")
    issued = LocalAuthService(store, session_ttl_seconds=60).login("admin", "chengdumijing12")
    assert issued.user.role.value == "admin"
    assert issued.must_change_password is False


def test_create_local_user_rejects_duplicates_and_invalid_input(tmp_path) -> None:
    create_local_user(tmp_path, "admin", "长期管理员", "chengdumijing12", "admin", output=lambda _: None)

    with pytest.raises(sqlite3.IntegrityError):
        create_local_user(tmp_path, " ADMIN ", "另一个人", "chengdumijing12", "user", output=lambda _: None)
    with pytest.raises(ValueError):
        create_local_user(tmp_path, "weak", "弱密码", "short", "user", output=lambda _: None)
    with pytest.raises(ValueError):
        create_local_user(tmp_path, "bad-role", "角色", "chengdumijing12", "viewer", output=lambda _: None)
