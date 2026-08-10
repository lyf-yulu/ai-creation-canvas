from __future__ import annotations

from ai_creation_canvas.__main__ import initialize_local_accounts, reset_local_password
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
