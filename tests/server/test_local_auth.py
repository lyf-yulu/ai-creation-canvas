from __future__ import annotations

import pytest

from ai_creation_canvas.auth.local import LocalAuthService
from ai_creation_canvas.auth.passwords import PasswordHasher
from ai_creation_canvas.domain.models import PortalRole
from ai_creation_canvas.storage.sqlite import CanvasStore


def test_password_hash_is_salted_and_verifies() -> None:
    first = PasswordHasher.hash("correct-horse-battery")
    second = PasswordHasher.hash("correct-horse-battery")

    assert first != second
    assert PasswordHasher.verify("correct-horse-battery", first)
    assert not PasswordHasher.verify("wrong-password-000", first)
    assert not PasswordHasher.verify("correct-horse-battery", "not-a-password-hash")


def test_password_hash_rejects_out_of_bounds_passwords() -> None:
    for password in ("short", "x" * 129):
        try:
            PasswordHasher.hash(password)
        except ValueError as error:
            assert str(error) == "password must contain between 12 and 128 characters"
        else:
            raise AssertionError("invalid password was accepted")


def test_local_session_is_hashed_at_rest_and_expires(tmp_path) -> None:
    now = [1_000.0]
    store = CanvasStore(tmp_path)
    auth = LocalAuthService(store, session_ttl_seconds=60, clock=lambda: now[0])
    user = auth.create_user(
        "user-a",
        "普通用户 A",
        "correct-horse-battery",
        PortalRole.USER,
        must_change_password=True,
    )

    issued = auth.login(" USER-A ", "correct-horse-battery")

    assert issued.session_token not in store.database.read_bytes().decode("latin1")
    resolved = auth.resolve(issued.session_token)
    assert resolved is not None
    assert resolved.user_id == user.user_id
    assert auth.verify_csrf(issued.session_token, issued.csrf_token)
    assert not auth.verify_csrf(issued.session_token, "wrong-csrf-token")

    now[0] = 1_061.0
    assert auth.resolve(issued.session_token) is None


def test_bootstrap_accounts_is_atomic_and_prints_passwords_only_once(tmp_path) -> None:
    auth = LocalAuthService(CanvasStore(tmp_path), session_ttl_seconds=60)

    created = auth.bootstrap_accounts(("demo-image-v1",))
    repeated = auth.bootstrap_accounts(("other-model",))

    assert created.created is True
    assert created.admin_username == "canvas-admin"
    assert len(created.admin_password) >= 12
    assert created.user_username == "canvas-user"
    assert len(created.user_password) >= 12
    assert repeated.created is False
    assert repeated.admin_password == ""
    assert repeated.user_password == ""
    assert auth.assigned_models(created.user.user_id) == ("demo-image-v1",)


def test_disabled_user_cannot_login_and_existing_session_is_revoked(tmp_path) -> None:
    store = CanvasStore(tmp_path)
    auth = LocalAuthService(store, session_ttl_seconds=60)
    user = auth.create_user(
        "user-a",
        "普通用户 A",
        "correct-horse-battery",
        PortalRole.USER,
        must_change_password=False,
    )
    issued = auth.login("user-a", "correct-horse-battery")

    store.set_user_enabled(user.user_id, False)

    assert auth.resolve(issued.session_token) is None
    try:
        auth.login("user-a", "correct-horse-battery")
    except ValueError as error:
        assert str(error) == "invalid username or password"
    else:
        raise AssertionError("disabled user logged in")


def test_unknown_user_still_executes_a_well_formed_password_hash(monkeypatch, tmp_path) -> None:
    auth = LocalAuthService(CanvasStore(tmp_path), session_ttl_seconds=60)
    observed: list[str] = []

    def reject(password: str, encoded: str) -> bool:
        del password
        observed.append(encoded)
        return False

    monkeypatch.setattr(PasswordHasher, "verify", reject)

    with pytest.raises(ValueError, match="invalid username or password"):
        auth.login("unknown-user", "wrong-password-000")

    algorithm, rounds, salt, digest = observed[0].split("$")
    assert algorithm == PasswordHasher.ALGORITHM
    assert int(rounds) == PasswordHasher.ITERATIONS
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(digest)) == 32
