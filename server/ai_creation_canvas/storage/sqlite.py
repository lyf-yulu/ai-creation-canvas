"""Small SQLite store.  It stores metadata only; user content remains in files."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import threading
import time
import re
from typing import Callable, Iterator


def _now() -> str:
    return datetime.now(UTC).isoformat()


_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_INIT_ATTEMPTS = 4
_INIT_BACKOFF_SECONDS = 0.02
_CHECKPOINT_ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class Reservation:
    job: dict[str, object]
    created: bool
    conflict: bool = False


class StoreInitializationError(RuntimeError):
    """Startup cannot safely expose a database whose scrub has not completed."""


class CanvasStore:
    """Metadata-only SQLite persistence with short immediate transactions."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        migration_hook: Callable[[str], None] | None = None,
        checkpoint_hook: Callable[[str, int], tuple[int, int, int] | None] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self._clock = clock
        self._migration_hook = migration_hook
        self._checkpoint_hook = checkpoint_hook
        self._lock = threading.RLock()
        self._prepare_root()
        self.assets_dir = self.data_dir / "assets"
        self.assets_dir.mkdir(mode=0o700, exist_ok=True)
        if self.assets_dir.is_symlink():
            raise ValueError("asset root must not be a symlink")
        os.chmod(self.assets_dir, 0o700)
        self.database = self.data_dir / "canvas.sqlite3"
        if self.database.exists() and self.database.is_symlink():
            raise ValueError("database must not be a symlink")
        self._init()

    def _prepare_root(self) -> None:
        # Do this before resolve(): resolve would hide a lexical symlink component.
        cursor = Path(self.data_dir.anchor) if self.data_dir.is_absolute() else Path(".")
        for component in self.data_dir.parts[1 if self.data_dir.is_absolute() else 0:]:
            cursor = cursor / component
            if cursor.exists() and cursor.is_symlink():
                raise ValueError("data root must not contain symlinks")
        if self.data_dir.exists() and self.data_dir.is_symlink():
            raise ValueError("data root must not be a symlink")
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.data_dir.is_symlink() or not self.data_dir.is_dir():
            raise ValueError("data root is unsafe")
        os.chmod(self.data_dir, 0o700)

    @contextmanager
    def _connection(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self.database.is_symlink():
                raise ValueError("database must not be a symlink")
            connection = self._new_connection(timeout=5)
            try:
                if immediate:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
                if immediate:
                    connection.commit()
            except Exception:
                if immediate:
                    connection.rollback()
                raise
            finally:
                connection.close()

    def _new_connection(self, *, timeout: float) -> sqlite3.Connection:
        if self.database.is_symlink():
            raise ValueError("database must not be a symlink")
        connection = sqlite3.connect(self.database, timeout=timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {max(1, int(timeout * 1000))}")
            connection.execute("PRAGMA secure_delete = ON")
            if connection.execute("PRAGMA secure_delete").fetchone()[0] != 1:
                raise RuntimeError("SQLite secure deletion could not be enabled")
        except BaseException:
            connection.close()
            raise
        return connection

    def _init(self) -> None:
        """Migrate and physically scrub legacy result URLs with crash recovery."""
        last_busy: sqlite3.OperationalError | None = None
        for attempt in range(_INIT_ATTEMPTS):
            connection: sqlite3.Connection | None = None
            try:
                with self._lock:
                    connection = self._new_connection(timeout=_INIT_BACKOFF_SECONDS)
                    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                    if str(mode).lower() != "wal":
                        raise RuntimeError("SQLite WAL could not be enabled")
                    connection.execute("BEGIN EXCLUSIVE")
                    scrub_pending = self._migrate_schema(connection)
                    connection.commit()
                    if scrub_pending:
                        if self._migration_hook is not None:
                            self._migration_hook("after_schema_commit")
                        self._scrub_pending_connection(connection)
                return
            except sqlite3.OperationalError as error:
                last_busy = error
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                if not self._is_busy(error) or attempt == _INIT_ATTEMPTS - 1:
                    raise RuntimeError("SQLite initialization could not acquire an exclusive scrub lock") from error
                time.sleep(_INIT_BACKOFF_SECONDS * (2**attempt))
            except BaseException:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                if connection is not None:
                    connection.close()
        raise RuntimeError("SQLite initialization could not acquire an exclusive scrub lock") from last_busy

    @staticmethod
    def _is_busy(error: sqlite3.OperationalError) -> bool:
        message = str(error).lower()
        return "locked" in message or "busy" in message

    def _migrate_schema(self, db: sqlite3.Connection) -> bool:
        """Run all schema work inside the secure, exclusive migration transaction."""
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_assets (
                asset_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                mime_type TEXT NOT NULL, status TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                service_id TEXT, upstream_asset_id TEXT
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_users (
                user_id TEXT PRIMARY KEY, username_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','user')),
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                must_change_password INTEGER NOT NULL CHECK(must_change_password IN (0,1)),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_sessions (
                token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL,
                user_id TEXT NOT NULL REFERENCES canvas_users(user_id) ON DELETE CASCADE,
                expires_at REAL NOT NULL, created_at TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_user_models (
                user_id TEXT NOT NULL REFERENCES canvas_users(user_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, model_id)
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, service_id TEXT NOT NULL,
                upstream_job_id TEXT, operation TEXT NOT NULL, status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                error_code TEXT, result_id TEXT, submission_token TEXT, lease_until REAL, attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )""")
        marker = db.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()
        scrub_pending = bool(marker is not None and marker[0] == "1")
        columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_jobs)")}
        asset_columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_assets)")}
        for name in ("service_id", "upstream_asset_id"):
            if name not in asset_columns:
                db.execute(f"ALTER TABLE canvas_assets ADD COLUMN {name} TEXT")
        if "result_ref" in columns:
            scrub_pending = True
            # Copy only opaque IDs, then rebuild so URLs cannot remain in a legacy column.
            # Remove the legacy column itself.  SQLite has no portable DROP COLUMN
            # for older supported versions; rebuild only from non-sensitive fields.
            # Fixed canonical projection; absent legacy columns get inert defaults.
            legacy_result = "result_ref"
            projection = [
                "id", "user_id", "service_id", "upstream_job_id", "operation", "status",
                "idempotency_key", "request_hash", "error_code" if "error_code" in columns else "NULL",
                "result_id" if "result_id" in columns else legacy_result,
                "submission_token" if "submission_token" in columns else "NULL",
                "lease_until" if "lease_until" in columns else "NULL",
                "attempt" if "attempt" in columns else "0", "created_at", "updated_at",
            ]
            db.execute("ALTER TABLE canvas_jobs RENAME TO canvas_jobs_legacy")
            db.execute("""CREATE TABLE canvas_jobs (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, service_id TEXT NOT NULL,
                    upstream_job_id TEXT, operation TEXT NOT NULL, status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, error_code TEXT,
                    result_id TEXT, submission_token TEXT, lease_until REAL,
                    attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(user_id,idempotency_key))""")
            db.execute("INSERT INTO canvas_jobs SELECT " + ",".join(projection) + " FROM canvas_jobs_legacy")
            db.execute("DROP TABLE canvas_jobs_legacy")
            columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_jobs)")}
            # Migration is intentionally additive; old data has no sensitive payload.
        for name, spec in (("result_id", "TEXT"), ("submission_token", "TEXT"), ("lease_until", "REAL"), ("attempt", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                db.execute(f"ALTER TABLE canvas_jobs ADD COLUMN {name} {spec}")
        for row in db.execute("SELECT id, result_id FROM canvas_jobs WHERE result_id IS NOT NULL"):
            if not isinstance(row["result_id"], str) or not _RESULT_ID.fullmatch(row["result_id"]):
                scrub_pending = True
                db.execute("UPDATE canvas_jobs SET result_id=NULL WHERE id=?", (row["id"],))
        if scrub_pending:
            db.execute(
                "INSERT INTO canvas_meta(key,value) VALUES ('legacy_result_scrub_pending','1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'"
            )
        return scrub_pending

    def _scrub_pending_connection(self, db: sqlite3.Connection) -> None:
        """Physically remove freed pages before clearing the durable pending marker."""
        if db.execute("PRAGMA secure_delete").fetchone()[0] != 1:
            raise RuntimeError("SQLite secure deletion could not be enabled")
        self._checkpoint_truncate_or_raise(db, "before_vacuum")
        db.execute("VACUUM")
        self._checkpoint_truncate_or_raise(db, "after_vacuum")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "INSERT INTO canvas_meta(key,value) VALUES ('legacy_result_scrub_pending','0') "
                "ON CONFLICT(key) DO UPDATE SET value='0'"
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def _checkpoint_truncate_or_raise(self, db: sqlite3.Connection, phase: str) -> None:
        """Require a completed truncating checkpoint before advancing scrub state."""
        for attempt in range(_CHECKPOINT_ATTEMPTS):
            row = self._checkpoint_hook(phase, attempt) if self._checkpoint_hook is not None else None
            if row is None:
                row = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is None or len(row) != 3:
                raise StoreInitializationError("SQLite checkpoint returned an invalid status")
            busy, log, checkpointed = row
            if type(busy) is not int or type(log) is not int or type(checkpointed) is not int:
                raise StoreInitializationError("SQLite checkpoint returned an invalid status")
            if busy == 0:
                return
            time.sleep(_INIT_BACKOFF_SECONDS * (2**attempt))
        raise StoreInitializationError("SQLite checkpoint remained busy; pending scrub was not completed")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, object] | None:
        return dict(row) if row is not None else None

    def create_asset(self, *, asset_id: str, user_id: str, kind: str, mime_type: str, relative_path: str, size_bytes: int, status: str = "active", service_id: str | None = None, upstream_asset_id: str | None = None) -> dict[str, object]:
        now = _now()
        with self._connection(immediate=True) as db:
            db.execute("INSERT INTO canvas_assets (asset_id,user_id,kind,mime_type,status,relative_path,size_bytes,created_at,updated_at,service_id,upstream_asset_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (asset_id, user_id, kind, mime_type, status, relative_path, size_bytes, now, now, service_id, upstream_asset_id))
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id = ?", (asset_id,)).fetchone()
        assert row is not None
        return dict(row)

    def create_user(self, *, user_id: str, username_normalized: str, display_name: str, password_hash: str, role: str, must_change_password: bool) -> dict[str, object]:
        now = _now()
        with self._connection(immediate=True) as db:
            db.execute(
                "INSERT INTO canvas_users (user_id,username_normalized,display_name,password_hash,role,enabled,must_change_password,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?,?)",
                (user_id, username_normalized, display_name, password_hash, role, int(must_change_password), now, now),
            )
            row = db.execute("SELECT * FROM canvas_users WHERE user_id=?", (user_id,)).fetchone()
        assert row is not None
        return dict(row)

    def bootstrap_users(self, *, admin_id: str, admin_password_hash: str, user_id: str, user_password_hash: str, initial_user_model_ids: tuple[str, ...]) -> bool:
        if len(initial_user_model_ids) > 128 or any(not isinstance(item, str) or not item or len(item) > 128 for item in initial_user_model_ids):
            raise ValueError("initial model assignments are invalid")
        now = _now()
        with self._connection(immediate=True) as db:
            if db.execute("SELECT 1 FROM canvas_users LIMIT 1").fetchone() is not None:
                return False
            db.execute("INSERT INTO canvas_users VALUES (?,?,?,?,?,1,1,?,?)", (admin_id, "canvas-admin", "管理员", admin_password_hash, "admin", now, now))
            db.execute("INSERT INTO canvas_users VALUES (?,?,?,?,?,1,1,?,?)", (user_id, "canvas-user", "普通用户", user_password_hash, "user", now, now))
            db.executemany(
                "INSERT INTO canvas_user_models (user_id,model_id,created_at) VALUES (?,?,?)",
                ((user_id, model_id, now) for model_id in sorted(set(initial_user_model_ids))),
            )
        return True

    def user_by_username(self, username_normalized: str) -> dict[str, object] | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_users WHERE username_normalized=?", (username_normalized,)).fetchone()
        return self._row(row)

    def user_by_id(self, user_id: str) -> dict[str, object] | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_users WHERE user_id=?", (user_id,)).fetchone()
        return self._row(row)

    def set_user_enabled(self, user_id: str, enabled: bool) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            db.execute("UPDATE canvas_users SET enabled=?,updated_at=? WHERE user_id=?", (int(enabled), _now(), user_id))
            if not enabled:
                db.execute("DELETE FROM canvas_sessions WHERE user_id=?", (user_id,))
            row = db.execute("SELECT * FROM canvas_users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        return dict(row)

    def update_user_password(self, user_id: str, password_hash: str) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            db.execute(
                "UPDATE canvas_users SET password_hash=?,must_change_password=0,updated_at=? WHERE user_id=?",
                (password_hash, _now(), user_id),
            )
            db.execute("DELETE FROM canvas_sessions WHERE user_id=?", (user_id,))
            row = db.execute("SELECT * FROM canvas_users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        return dict(row)

    def create_session(self, *, token_hash: str, csrf_token: str, user_id: str, expires_at: float) -> None:
        with self._connection(immediate=True) as db:
            db.execute("DELETE FROM canvas_sessions WHERE expires_at<=?", (time.time(),))
            db.execute(
                "INSERT INTO canvas_sessions (token_hash,csrf_token,user_id,expires_at,created_at) VALUES (?,?,?,?,?)",
                (token_hash, csrf_token, user_id, expires_at, _now()),
            )

    def session_user(self, token_hash: str, now: float) -> dict[str, object] | None:
        with self._connection(immediate=True) as db:
            db.execute("DELETE FROM canvas_sessions WHERE expires_at<=?", (now,))
            row = db.execute(
                "SELECT u.*,s.csrf_token,s.expires_at FROM canvas_sessions s JOIN canvas_users u ON u.user_id=s.user_id WHERE s.token_hash=? AND u.enabled=1 AND s.expires_at>?",
                (token_hash, now),
            ).fetchone()
        return self._row(row)

    def delete_session(self, token_hash: str) -> None:
        with self._connection(immediate=True) as db:
            db.execute("DELETE FROM canvas_sessions WHERE token_hash=?", (token_hash,))

    def assigned_models(self, user_id: str) -> tuple[str, ...]:
        with self._connection() as db:
            rows = db.execute("SELECT model_id FROM canvas_user_models WHERE user_id=? ORDER BY model_id", (user_id,)).fetchall()
        return tuple(str(row["model_id"]) for row in rows)

    def replace_model_assignments(self, user_id: str, model_ids: tuple[str, ...]) -> tuple[str, ...]:
        if len(model_ids) > 128 or len(model_ids) != len(set(model_ids)) or any(not isinstance(item, str) or not item or len(item) > 128 for item in model_ids):
            raise ValueError("model assignments are invalid")
        with self._connection(immediate=True) as db:
            if db.execute("SELECT 1 FROM canvas_users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise KeyError(user_id)
            db.execute("DELETE FROM canvas_user_models WHERE user_id=?", (user_id,))
            now = _now()
            db.executemany(
                "INSERT INTO canvas_user_models (user_id,model_id,created_at) VALUES (?,?,?)",
                ((user_id, model_id, now) for model_id in sorted(model_ids)),
            )
        return self.assigned_models(user_id)

    def list_users(self) -> tuple[dict[str, object], ...]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT user_id,username_normalized,display_name,role,enabled,must_change_password,created_at,updated_at FROM canvas_users ORDER BY role,username_normalized"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def list_assets_for_owner(self, user_id: str, limit: int = 100) -> tuple[dict[str, object], ...]:
        safe_limit = min(max(limit, 1), 100)
        with self._connection() as db:
            rows = db.execute(
                "SELECT asset_id,kind,mime_type,status,size_bytes,created_at,updated_at FROM canvas_assets WHERE user_id=? ORDER BY created_at DESC,asset_id DESC LIMIT ?",
                (user_id, safe_limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def list_jobs_for_owner(self, user_id: str, limit: int = 100) -> tuple[dict[str, object], ...]:
        safe_limit = min(max(limit, 1), 100)
        with self._connection() as db:
            rows = db.execute(
                "SELECT id,service_id,operation,status,error_code,created_at,updated_at FROM canvas_jobs WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (user_id, safe_limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def asset_for_owner(self, asset_id: str, user_id: str) -> tuple[dict[str, object] | None, bool]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id = ?", (asset_id,)).fetchone()
        item = self._row(row)
        return (item, bool(item and item["user_id"] != user_id))

    def update_asset_status(self, asset_id: str, status: str) -> dict[str, object]:
        ranks = {"processing": 0, "active": 1, "failed": 1}
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone()
            if row is None: raise KeyError(asset_id)
            old = dict(row)
            if old["status"] in {"active", "failed"} or ranks.get(status, -1) < ranks.get(str(old["status"]), 0): return old
            db.execute("UPDATE canvas_assets SET status=?,updated_at=? WHERE asset_id=?", (status, _now(), asset_id))
            return dict(db.execute("SELECT * FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone())

    def reserve_job(self, *, user_id: str, job_id: str, service_id: str, operation: str, idempotency_key: str, request_hash: str, lease_seconds: float = 30.0) -> Reservation:
        now = _now()
        token = os.urandom(16).hex()
        lease_until = self._clock() + lease_seconds
        with self._connection(immediate=True) as db:
            existing = db.execute("SELECT * FROM canvas_jobs WHERE user_id = ? AND idempotency_key = ?", (user_id, idempotency_key)).fetchone()
            if existing is not None:
                item = dict(existing)
                if item["request_hash"] != request_hash:
                    return Reservation(item, False, True)
                if item["status"] == "submitting" and float(item.get("lease_until") or 0) <= self._clock():
                    db.execute("UPDATE canvas_jobs SET submission_token=?, lease_until=?, attempt=attempt+1, error_code=NULL, updated_at=? WHERE id=? AND submission_token IS ?", (token, lease_until, now, item["id"], item.get("submission_token")))
                    item = dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (item["id"],)).fetchone())
                    return Reservation(item, True)
                return Reservation(item, False)
            db.execute("INSERT INTO canvas_jobs (id,user_id,service_id,operation,status,idempotency_key,request_hash,submission_token,lease_until,attempt,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (job_id, user_id, service_id, operation, "submitting", idempotency_key, request_hash, token, lease_until, 1, now, now))
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        return Reservation(dict(row), True)

    def mark_submitted(self, job_id: str, upstream_job_id: str, status: str, token: str | None = None) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise KeyError(job_id)
            if token is not None and row["submission_token"] != token:
                return dict(row)
            db.execute("UPDATE canvas_jobs SET status=?, upstream_job_id=?, submission_token=NULL, lease_until=NULL, updated_at=? WHERE id=?", (status, upstream_job_id, _now(), job_id))
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def fail_reservation(self, job_id: str, error_code: str = "TASK_FAILED", token: str | None = None) -> dict[str, object]:
        # Retain a short-lived reservation that can be reclaimed, without losing the key.
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise KeyError(job_id)
            if token is not None and row["submission_token"] != token: return dict(row)
            db.execute("UPDATE canvas_jobs SET error_code=?, lease_until=?, updated_at=? WHERE id=?", (error_code, self._clock() - 1, _now(), job_id))
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def mark_failed(self, job_id: str, error_code: str, token: str | None = None) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise KeyError(job_id)
            if token is not None and row["submission_token"] != token: return dict(row)
            db.execute("UPDATE canvas_jobs SET status='failed', error_code=?, submission_token=NULL, lease_until=NULL, updated_at=? WHERE id=?", (error_code, _now(), job_id))
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def fail_invalid_upstream_result(self, job_id: str, error_code: str = "INVALID_UPSTREAM_RESULT") -> dict[str, object]:
        """CAS a polled queued/running job to terminal failure exactly once."""
        with self._connection(immediate=True) as db:
            db.execute(
                "UPDATE canvas_jobs SET status='failed', error_code=?, submission_token=NULL, lease_until=NULL, updated_at=? "
                "WHERE id=? AND status IN ('queued', 'running')",
                (error_code, _now(), job_id),
            )
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def _update(self, job_id: str, *, status: str, upstream_job_id: str | None = None, error_code: str | None = None, result_id: str | None = None, result_ref: str | None = None) -> dict[str, object]:
        ranks = {"uploading": 0, "submitting": 1, "queued": 2, "running": 3, "succeeded": 4, "failed": 4}
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            old = dict(row)
            if old["status"] in {"succeeded", "failed"} or ranks.get(status, -1) < ranks.get(str(old["status"]), 0):
                return old
            now = _now()
            result_id = result_id if result_id is not None else result_ref
            db.execute("UPDATE canvas_jobs SET status=?, upstream_job_id=COALESCE(?, upstream_job_id), error_code=COALESCE(?, error_code), result_id=COALESCE(?, result_id), updated_at=? WHERE id=?", (status, upstream_job_id, error_code, result_id, now, job_id))
            updated = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def job_for_owner(self, job_id: str, user_id: str) -> tuple[dict[str, object] | None, bool]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        item = self._row(row)
        return (item, bool(item and item["user_id"] != user_id))
