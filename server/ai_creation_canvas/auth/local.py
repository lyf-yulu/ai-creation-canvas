"""Local user and session rules backed by the metadata store."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
import time
from typing import Callable

from ai_creation_canvas.auth.passwords import PasswordHasher
from ai_creation_canvas.domain.models import PortalRole, PortalUser
from ai_creation_canvas.storage.sqlite import CanvasStore


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


_DUMMY_PASSWORD_HASH = PasswordHasher.hash("local-auth-dummy-password")


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: PortalUser
    session_token: str
    csrf_token: str
    expires_at: float
    must_change_password: bool


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    admin: PortalUser | None
    user: PortalUser | None
    admin_username: str
    admin_password: str
    user_username: str
    user_password: str
    created: bool


class LocalAuthService:
    def __init__(
        self,
        store: CanvasStore,
        *,
        session_ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(session_ttl_seconds) is not int or session_ttl_seconds < 1:
            raise ValueError("session_ttl_seconds must be positive")
        self._store = store
        self._session_ttl_seconds = session_ttl_seconds
        self._clock = clock

    @staticmethod
    def _user(row: dict[str, object]) -> PortalUser:
        return PortalUser(str(row["user_id"]), str(row["display_name"]), str(row["role"]))

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        role: PortalRole | str,
        *,
        must_change_password: bool,
    ) -> PortalUser:
        normalized = username.strip().casefold()
        if not normalized or len(normalized) > 80 or not display_name.strip() or len(display_name) > 120:
            raise ValueError("user details are invalid")
        parsed_role = PortalRole(role)
        if parsed_role not in {PortalRole.ADMIN, PortalRole.USER}:
            raise ValueError("local role is invalid")
        row = self._store.create_user(
            user_id=secrets.token_urlsafe(18),
            username_normalized=normalized,
            display_name=display_name.strip(),
            password_hash=PasswordHasher.hash(password),
            role=parsed_role.value,
            must_change_password=must_change_password,
        )
        return self._user(row)

    def register_user(self, username: str, display_name: str, password: str) -> PortalUser:
        normalized = username.strip().casefold()
        if not normalized or len(normalized) > 80 or not display_name.strip() or len(display_name) > 120:
            raise ValueError("user details are invalid")
        row = self._store.create_user(
            user_id=secrets.token_urlsafe(18),
            username_normalized=normalized,
            display_name=display_name.strip(),
            password_hash=PasswordHasher.hash(password),
            role=PortalRole.USER.value,
            must_change_password=False,
            enabled=0,
            approval_status="pending",
        )
        return self._user(row)

    def login(self, username: str, password: str) -> IssuedSession:
        normalized = username.strip().casefold() if isinstance(username, str) else ""
        row = self._store.user_by_username(normalized)
        encoded = str(row["password_hash"]) if row is not None else _DUMMY_PASSWORD_HASH
        verified = PasswordHasher.verify(password, encoded)
        if row is None or not verified or row["enabled"] != 1:
            raise ValueError("invalid username or password")
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._session_ttl_seconds
        self._store.create_session(
            token_hash=_token_hash(session_token),
            csrf_token=csrf_token,
            user_id=str(row["user_id"]),
            expires_at=expires_at,
        )
        return IssuedSession(
            user=self._user(row),
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=expires_at,
            must_change_password=bool(row["must_change_password"]),
        )

    def resolve(self, session_token: str) -> PortalUser | None:
        if not isinstance(session_token, str) or not session_token:
            return None
        row = self._store.session_user(_token_hash(session_token), self._clock())
        return self._user(row) if row is not None else None

    def session_details(self, session_token: str) -> dict[str, object] | None:
        if not isinstance(session_token, str) or not session_token:
            return None
        return self._store.session_user(_token_hash(session_token), self._clock())

    def verify_csrf(self, session_token: str, csrf_token: str) -> bool:
        row = self.session_details(session_token)
        return bool(
            row is not None
            and isinstance(csrf_token, str)
            and hmac.compare_digest(str(row["csrf_token"]), csrf_token)
        )

    def logout(self, session_token: str) -> None:
        if isinstance(session_token, str) and session_token:
            self._store.delete_session(_token_hash(session_token))

    def change_initial_password(self, user_id: str, current_password: str, new_password: str) -> None:
        row = self._store.user_by_id(user_id)
        if row is None or row["enabled"] != 1 or not PasswordHasher.verify(current_password, str(row["password_hash"])):
            raise ValueError("invalid current password")
        self._store.update_user_password(user_id, PasswordHasher.hash(new_password))

    def reset_password(self, username: str) -> str:
        normalized = username.strip().casefold() if isinstance(username, str) else ""
        if not normalized:
            raise ValueError("user does not exist")
        password = secrets.token_urlsafe(18)
        try:
            self._store.reset_user_password(normalized, PasswordHasher.hash(password))
        except KeyError:
            raise ValueError("user does not exist") from None
        return password

    def assigned_models(self, user_id: str) -> tuple[str, ...]:
        return self._store.assigned_models(user_id)

    def bootstrap_accounts(self, initial_user_model_ids: tuple[str, ...] = ()) -> BootstrapResult:
        admin_password = secrets.token_urlsafe(18)
        user_password = secrets.token_urlsafe(18)
        admin_id = secrets.token_urlsafe(18)
        user_id = secrets.token_urlsafe(18)
        created = self._store.bootstrap_users(
            admin_id=admin_id,
            admin_password_hash=PasswordHasher.hash(admin_password),
            user_id=user_id,
            user_password_hash=PasswordHasher.hash(user_password),
            initial_user_model_ids=initial_user_model_ids,
        )
        if not created:
            return BootstrapResult(None, None, "canvas-admin", "", "canvas-user", "", False)
        admin_row = self._store.user_by_id(admin_id)
        user_row = self._store.user_by_id(user_id)
        assert admin_row is not None and user_row is not None
        return BootstrapResult(
            self._user(admin_row),
            self._user(user_row),
            "canvas-admin",
            admin_password,
            "canvas-user",
            user_password,
            True,
        )
