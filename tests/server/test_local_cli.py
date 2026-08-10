from __future__ import annotations

from ai_creation_canvas.__main__ import initialize_local_accounts


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
