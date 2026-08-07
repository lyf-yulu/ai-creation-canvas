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
from typing import Iterator


def _now() -> str:
    return datetime.now(UTC).isoformat()


_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class Reservation:
    job: dict[str, object]
    created: bool
    conflict: bool = False


class CanvasStore:
    """Metadata-only SQLite persistence with short immediate transactions."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
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
            connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
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

    def _init(self) -> None:
        # journal_mode changes the database file and must not be run inside a txn.
        with self._connection() as db:
            mode = db.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError("SQLite WAL could not be enabled")
            db.execute("PRAGMA secure_delete = ON")
        with self._connection(immediate=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS canvas_assets (
                asset_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                mime_type TEXT NOT NULL, status TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS canvas_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, service_id TEXT NOT NULL,
                upstream_job_id TEXT, operation TEXT NOT NULL, status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                error_code TEXT, result_id TEXT, submission_token TEXT, lease_until REAL, attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_jobs)")}
            if "result_ref" in columns:
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
                    db.execute("UPDATE canvas_jobs SET result_id=NULL WHERE id=?", (row["id"],))

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, object] | None:
        return dict(row) if row is not None else None

    def create_asset(self, *, asset_id: str, user_id: str, kind: str, mime_type: str, relative_path: str, size_bytes: int) -> dict[str, object]:
        now = _now()
        with self._connection(immediate=True) as db:
            db.execute("INSERT INTO canvas_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (asset_id, user_id, kind, mime_type, "active", relative_path, size_bytes, now, now))
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id = ?", (asset_id,)).fetchone()
        assert row is not None
        return dict(row)

    def asset_for_owner(self, asset_id: str, user_id: str) -> tuple[dict[str, object] | None, bool]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id = ?", (asset_id,)).fetchone()
        item = self._row(row)
        return (item, bool(item and item["user_id"] != user_id))

    def reserve_job(self, *, user_id: str, job_id: str, service_id: str, operation: str, idempotency_key: str, request_hash: str, lease_seconds: float = 30.0) -> Reservation:
        now = _now()
        token = os.urandom(16).hex()
        lease_until = time.time() + lease_seconds
        with self._connection(immediate=True) as db:
            existing = db.execute("SELECT * FROM canvas_jobs WHERE user_id = ? AND idempotency_key = ?", (user_id, idempotency_key)).fetchone()
            if existing is not None:
                item = dict(existing)
                if item["request_hash"] != request_hash:
                    return Reservation(item, False, True)
                if item["status"] == "submitting" and float(item.get("lease_until") or 0) <= time.time():
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
            db.execute("UPDATE canvas_jobs SET error_code=?, lease_until=?, updated_at=? WHERE id=?", (error_code, time.time() - 1, _now(), job_id))
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def mark_failed(self, job_id: str, error_code: str, token: str | None = None) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise KeyError(job_id)
            if token is not None and row["submission_token"] != token: return dict(row)
            db.execute("UPDATE canvas_jobs SET status='failed', error_code=?, submission_token=NULL, lease_until=NULL, updated_at=? WHERE id=?", (error_code, _now(), job_id))
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

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
