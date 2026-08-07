"""Small SQLite store.  It stores metadata only; user content remains in files."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import threading
from typing import Iterator


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        with self._connection(immediate=True) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS canvas_assets (
                asset_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                mime_type TEXT NOT NULL, status TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS canvas_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, service_id TEXT NOT NULL,
                upstream_job_id TEXT, operation TEXT NOT NULL, status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                error_code TEXT, result_ref TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )""")

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

    def reserve_job(self, *, user_id: str, job_id: str, service_id: str, operation: str, idempotency_key: str, request_hash: str) -> Reservation:
        now = _now()
        with self._connection(immediate=True) as db:
            existing = db.execute("SELECT * FROM canvas_jobs WHERE user_id = ? AND idempotency_key = ?", (user_id, idempotency_key)).fetchone()
            if existing is not None:
                item = dict(existing)
                return Reservation(item, False, item["request_hash"] != request_hash)
            db.execute("INSERT INTO canvas_jobs (id,user_id,service_id,operation,status,idempotency_key,request_hash,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (job_id, user_id, service_id, operation, "submitting", idempotency_key, request_hash, now, now))
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        return Reservation(dict(row), True)

    def mark_submitted(self, job_id: str, upstream_job_id: str, status: str) -> dict[str, object]:
        return self._update(job_id, status=status, upstream_job_id=upstream_job_id)

    def fail_reservation(self, job_id: str, error_code: str = "TASK_FAILED") -> dict[str, object]:
        return self._update(job_id, status="failed", error_code=error_code)

    def _update(self, job_id: str, *, status: str, upstream_job_id: str | None = None, error_code: str | None = None, result_ref: str | None = None) -> dict[str, object]:
        ranks = {"uploading": 0, "submitting": 1, "queued": 2, "running": 3, "succeeded": 4, "failed": 4}
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            old = dict(row)
            if old["status"] in {"succeeded", "failed"} or ranks.get(status, -1) < ranks.get(str(old["status"]), 0):
                return old
            now = _now()
            db.execute("UPDATE canvas_jobs SET status=?, upstream_job_id=COALESCE(?, upstream_job_id), error_code=COALESCE(?, error_code), result_ref=COALESCE(?, result_ref), updated_at=? WHERE id=?", (status, upstream_job_id, error_code, result_ref, now, job_id))
            updated = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def job_for_owner(self, job_id: str, user_id: str) -> tuple[dict[str, object] | None, bool]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        item = self._row(row)
        return (item, bool(item and item["user_id"] != user_id))
