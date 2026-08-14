"""Small SQLite store for metadata and bounded project documents; media remains in files."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time
import re
from typing import Callable, Iterator


def _now() -> str:
    return datetime.now(UTC).isoformat()


_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_INIT_ATTEMPTS = 4
_INIT_BACKOFF_SECONDS = 0.02
_CHECKPOINT_ATTEMPTS = 4
_TOMBSTONE_NAME = re.compile(r"\.[0-9a-f]{40}\.delete\Z")
_TOMBSTONE_JOURNAL_NAME = re.compile(r"\.([0-9a-f]{40})\.delete\.journal\Z")
_PORTRAIT_RECOVERY_NAME = re.compile(r"\.portrait-recovery-([A-Za-z0-9_-]{1,128})\.pending\Z")
_MODEL_ROUTING_MIGRATION_MARKER = "model_routing_legacy_migration_v1"
_MAX_USAGE_PRICE_FEN = 1_000_000_000
_MAX_VIDEO_SECONDS = 86_400
_MAX_IMAGE_COUNT = 100


def _legacy_route_id(model_id: str) -> str:
    candidate = f"legacy-{model_id}"
    if len(candidate) <= 128 and _RESULT_ID.fullmatch(candidate) is not None:
        return candidate
    return f"legacy-{hashlib.sha256(model_id.encode('utf-8')).hexdigest()}"


class AssetQuotaExceeded(ValueError):
    """An atomic asset insert would exceed an administrator-owned quota."""


@dataclass(frozen=True, slots=True)
class Reservation:
    job: dict[str, object]
    created: bool
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class PolledJobWrite:
    job: dict[str, object]
    applied: bool


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
        asset_delete_hook: Callable[[str], None] | None = None,
        portrait_finalize_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self._clock = clock
        self._migration_hook = migration_hook
        self._checkpoint_hook = checkpoint_hook
        self._asset_delete_hook = asset_delete_hook
        self._portrait_finalize_hook = portrait_finalize_hook
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
        self._recover_asset_deletions()
        self._recover_portrait_finalizations()
        self._fail_stale_portrait_reservations()

    def _fsync_assets_dir(self) -> None:
        descriptor = os.open(self.assets_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _recover_asset_deletions(self) -> None:
        for journal in self.assets_dir.iterdir():
            matched = _TOMBSTONE_JOURNAL_NAME.fullmatch(journal.name)
            if matched is None:
                continue
            tombstone = self.assets_dir / f".{matched.group(1)}.delete"
            try:
                journal_info = journal.lstat()
                if not stat.S_ISREG(journal_info.st_mode) or journal.is_symlink() or journal_info.st_size > 512:
                    continue
                asset_id, basename = journal.read_text(encoding="ascii").splitlines()
                if not _RESULT_ID.fullmatch(asset_id) or not basename or basename != Path(basename).name or any(ord(char) < 32 for char in basename):
                    continue
                with self._connection() as db:
                    row = db.execute("SELECT relative_path FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone()
                if row is not None and str(row["relative_path"]) != f"assets/{basename}":
                    continue
                if tombstone.is_symlink() or (tombstone.exists() and not stat.S_ISREG(tombstone.lstat().st_mode)):
                    continue
                original = self.assets_dir / basename
                if row is not None:
                    if tombstone.is_file() and not original.exists():
                        os.replace(tombstone, original)
                    elif tombstone.exists() or not original.is_file() or original.is_symlink():
                        continue
                else:
                    if tombstone.is_file() and not tombstone.is_symlink():
                        tombstone.unlink()
                journal.unlink()
                self._fsync_assets_dir()
            except (OSError, UnicodeError, ValueError):
                continue

    def _tombstone_bytes(self) -> int:
        total = 0
        for candidate in self.assets_dir.iterdir():
            if not _TOMBSTONE_NAME.fullmatch(candidate.name):
                continue
            try:
                info = candidate.lstat()
                if stat.S_ISREG(info.st_mode) and not candidate.is_symlink():
                    total += info.st_size
            except OSError:
                continue
        return total

    def _recover_portrait_finalizations(self) -> None:
        for candidate in self.assets_dir.iterdir():
            matched = _PORTRAIT_RECOVERY_NAME.fullmatch(candidate.name)
            if matched is None:
                continue
            try:
                info = candidate.lstat()
                if not stat.S_ISREG(info.st_mode) or candidate.is_symlink() or info.st_size > 512:
                    continue
                service_id, upstream_asset_id, status = candidate.read_text(encoding="ascii").splitlines()
                if service_id != "portal-portrait" or not _RESULT_ID.fullmatch(upstream_asset_id) or status not in {"processing", "active", "failed"}:
                    continue
                with self._connection(immediate=True) as db:
                    cursor = db.execute("UPDATE canvas_assets SET service_id=?,upstream_asset_id=?,status=?,updated_at=? WHERE asset_id=? AND kind='portrait' AND upstream_asset_id IS NULL", (service_id, upstream_asset_id, status, _now(), matched.group(1)))
                if cursor.rowcount == 1:
                    candidate.unlink()
            except (OSError, UnicodeError, ValueError):
                continue

    def _fail_stale_portrait_reservations(self) -> None:
        with self._connection(immediate=True) as db:
            db.execute("UPDATE canvas_assets SET status='failed',updated_at=? WHERE kind='portrait' AND upstream_asset_id IS NULL AND status='processing'", (_now(),))

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
                media_type TEXT NOT NULL, mime_type TEXT NOT NULL, status TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
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
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_providers (
                provider_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                adapter_type TEXT NOT NULL, base_url TEXT NOT NULL,
                credential_ref TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_models (
                model_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES canvas_providers(provider_id),
                provider_model_name TEXT NOT NULL, display_name TEXT NOT NULL, introduction TEXT NOT NULL,
                modality TEXT NOT NULL CHECK(modality IN ('image','video','audio','text')),
                operation_contracts_json TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                revision INTEGER NOT NULL CHECK(revision >= 1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_model_access (
                user_id TEXT NOT NULL REFERENCES canvas_users(user_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL REFERENCES canvas_models(model_id) ON DELETE CASCADE,
                granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT,
                PRIMARY KEY(user_id, model_id)
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_admin_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id TEXT NOT NULL,
                action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_projects (
                project_id TEXT NOT NULL, user_id TEXT NOT NULL,
                title TEXT NOT NULL, document_json TEXT NOT NULL,
                version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, project_id)
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_logical_models (
                model_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, introduction TEXT NOT NULL,
                modality TEXT NOT NULL CHECK(modality IN ('image','video','audio','text')),
                operation_contracts_json TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                archived_at TEXT, revision INTEGER NOT NULL CHECK(revision >= 1),
                runtime_purged INTEGER NOT NULL DEFAULT 0 CHECK(runtime_purged IN (0,1)),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_model_routes (
                route_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL REFERENCES canvas_logical_models(model_id),
                provider_id TEXT, provider_model_name TEXT, adapter_type TEXT,
                credential_pool_ref TEXT, family TEXT, operation_contracts_json TEXT NOT NULL,
                priority INTEGER, max_concurrency INTEGER,
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), archived_at TEXT,
                revision INTEGER NOT NULL CHECK(revision >= 1),
                runtime_purged INTEGER NOT NULL DEFAULT 0 CHECK(runtime_purged IN (0,1)),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
        marker = db.execute(
            "SELECT value FROM canvas_meta WHERE key=?", (_MODEL_ROUTING_MIGRATION_MARKER,)
        ).fetchone()
        if marker is None:
            legacy_rows = db.execute(
                "SELECT m.*,p.adapter_type,p.credential_ref,p.enabled AS provider_enabled "
                "FROM canvas_models m JOIN canvas_providers p ON p.provider_id=m.provider_id "
                "ORDER BY m.model_id"
            ).fetchall()
            for row in legacy_rows:
                model_id = str(row["model_id"])
                enabled = int(bool(row["enabled"]))
                db.execute(
                    "INSERT INTO canvas_logical_models("
                    "model_id,display_name,introduction,modality,operation_contracts_json,enabled,"
                    "archived_at,revision,runtime_purged,created_at,updated_at"
                    ") VALUES (?,?,?,?,?,?,NULL,?,0,?,?)",
                    (
                        model_id,
                        row["display_name"],
                        row["introduction"],
                        row["modality"],
                        row["operation_contracts_json"],
                        enabled,
                        row["revision"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                db.execute(
                    "INSERT INTO canvas_model_routes("
                    "route_id,model_id,provider_id,provider_model_name,adapter_type,credential_pool_ref,"
                    "family,operation_contracts_json,priority,max_concurrency,enabled,archived_at,revision,"
                    "runtime_purged,created_at,updated_at"
                    ") VALUES (?,?,?,?,?,?,?,?,100,1,?,NULL,?,0,?,?)",
                    (
                        _legacy_route_id(model_id),
                        model_id,
                        row["provider_id"],
                        row["provider_model_name"],
                        row["adapter_type"],
                        row["credential_ref"],
                        row["provider_model_name"],
                        row["operation_contracts_json"],
                        int(bool(row["enabled"]) and bool(row["provider_enabled"])),
                        row["revision"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            db.execute(
                "INSERT INTO canvas_meta(key,value) VALUES (?,?)",
                (_MODEL_ROUTING_MIGRATION_MARKER, "1"),
            )
        # Model access spans local and externally authenticated users and now
        # targets logical models. Keep IDs as data and enforce existence in the
        # store methods instead of retaining legacy foreign keys to canvas_models.
        access_foreign_keys = db.execute("PRAGMA foreign_key_list(canvas_model_access)").fetchall()
        if access_foreign_keys:
            db.execute("ALTER TABLE canvas_model_access RENAME TO canvas_model_access_legacy")
            db.execute("""CREATE TABLE canvas_model_access (
                    user_id TEXT NOT NULL, model_id TEXT NOT NULL,
                    granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT,
                    PRIMARY KEY(user_id, model_id)
                )""")
            db.execute("INSERT INTO canvas_model_access SELECT user_id,model_id,granted_by,granted_at,revoked_at FROM canvas_model_access_legacy")
            db.execute("DROP TABLE canvas_model_access_legacy")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, service_id TEXT NOT NULL,
                upstream_job_id TEXT, operation TEXT NOT NULL, status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                error_code TEXT, result_id TEXT, result_ids_json TEXT, submission_token TEXT, lease_until REAL, attempt INTEGER NOT NULL DEFAULT 0,
                model_id TEXT, model_revision INTEGER, provider_id TEXT, adapter_type TEXT, route_id TEXT, submission_json TEXT,
                logical_model_id TEXT, logical_model_revision INTEGER, route_revision INTEGER,
                pool_revision_digest TEXT, key_fingerprint TEXT, submission_state TEXT, route_snapshot_json TEXT,
                video_seconds INTEGER NOT NULL DEFAULT 0, image_count INTEGER NOT NULL DEFAULT 0,
                video_price_fen INTEGER, image_price_fen INTEGER, cost_fen INTEGER, charged_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(user_id, idempotency_key)
            )""")
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_usage_rates (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                video_price_fen INTEGER NOT NULL CHECK(video_price_fen>=0),
                image_price_fen INTEGER NOT NULL CHECK(image_price_fen>=0),
                updated_at TEXT NOT NULL
            )""")
        db.execute("INSERT OR IGNORE INTO canvas_usage_rates VALUES(1,0,0,?)", (_now(),))
        marker = db.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()
        scrub_pending = bool(marker is not None and marker[0] == "1")
        columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_jobs)")}
        asset_columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_assets)")}
        for name in ("service_id", "upstream_asset_id"):
            if name not in asset_columns:
                db.execute(f"ALTER TABLE canvas_assets ADD COLUMN {name} TEXT")
        if "media_type" not in asset_columns:
            db.execute("ALTER TABLE canvas_assets ADD COLUMN media_type TEXT")
            db.execute("UPDATE canvas_assets SET media_type=CASE WHEN mime_type LIKE 'video/%' THEN 'video' WHEN mime_type LIKE 'audio/%' THEN 'audio' ELSE 'image' END")
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
                    result_id TEXT, result_ids_json TEXT, submission_token TEXT, lease_until REAL,
                    attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(user_id,idempotency_key))""")
            projection.insert(10, "NULL")
            db.execute("INSERT INTO canvas_jobs SELECT " + ",".join(projection) + " FROM canvas_jobs_legacy")
            db.execute("DROP TABLE canvas_jobs_legacy")
            columns = {row[1] for row in db.execute("PRAGMA table_info(canvas_jobs)")}
            # Migration is intentionally additive; old data has no sensitive payload.
        for name, spec in (("result_id", "TEXT"), ("result_ids_json", "TEXT"), ("submission_token", "TEXT"), ("lease_until", "REAL"), ("attempt", "INTEGER NOT NULL DEFAULT 0"), ("model_id", "TEXT"), ("model_revision", "INTEGER"), ("provider_id", "TEXT"), ("adapter_type", "TEXT"), ("route_id", "TEXT"), ("submission_json", "TEXT"), ("logical_model_id", "TEXT"), ("logical_model_revision", "INTEGER"), ("route_revision", "INTEGER"), ("pool_revision_digest", "TEXT"), ("key_fingerprint", "TEXT"), ("submission_state", "TEXT"), ("route_snapshot_json", "TEXT"), ("video_seconds", "INTEGER NOT NULL DEFAULT 0"), ("image_count", "INTEGER NOT NULL DEFAULT 0"), ("video_price_fen", "INTEGER"), ("image_price_fen", "INTEGER"), ("cost_fen", "INTEGER"), ("charged_at", "TEXT")):
            if name not in columns:
                db.execute(f"ALTER TABLE canvas_jobs ADD COLUMN {name} {spec}")
        for row in db.execute("SELECT id, result_id FROM canvas_jobs WHERE result_id IS NOT NULL"):
            if not isinstance(row["result_id"], str) or not _RESULT_ID.fullmatch(row["result_id"]):
                scrub_pending = True
                db.execute("UPDATE canvas_jobs SET result_id=NULL WHERE id=?", (row["id"],))
        db.execute("""CREATE TABLE IF NOT EXISTS canvas_job_acknowledgements (
                job_id TEXT PRIMARY KEY REFERENCES canvas_jobs(id) ON DELETE CASCADE,
                token TEXT, lease_until REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
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

    def create_asset(self, *, asset_id: str, user_id: str, kind: str, mime_type: str, relative_path: str, size_bytes: int, media_type: str | None = None, status: str = "active", service_id: str | None = None, upstream_asset_id: str | None = None, user_quota_bytes: int | None = None, total_quota_bytes: int | None = None) -> dict[str, object]:
        now = _now()
        safe_media_type = media_type or mime_type.split("/", 1)[0]
        if safe_media_type not in {"image", "video", "audio"}:
            raise ValueError("media_type is invalid")
        with self._connection(immediate=True) as db:
            user_bytes = int(db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM canvas_assets WHERE user_id=?", (user_id,)).fetchone()[0])
            total_bytes = int(db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM canvas_assets").fetchone()[0]) + self._tombstone_bytes()
            if (user_quota_bytes is not None and user_bytes + size_bytes > user_quota_bytes) or (total_quota_bytes is not None and total_bytes + size_bytes > total_quota_bytes):
                raise AssetQuotaExceeded("asset quota exceeded")
            db.execute("INSERT INTO canvas_assets (asset_id,user_id,kind,media_type,mime_type,status,relative_path,size_bytes,created_at,updated_at,service_id,upstream_asset_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (asset_id, user_id, kind, safe_media_type, mime_type, status, relative_path, size_bytes, now, now, service_id, upstream_asset_id))
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id = ?", (asset_id,)).fetchone()
        assert row is not None
        return dict(row)

    def finalize_portrait_asset(self, asset_id: str, *, service_id: str, upstream_asset_id: str, status: str) -> dict[str, object]:
        if service_id != "portal-portrait" or not _RESULT_ID.fullmatch(upstream_asset_id) or status not in {"processing", "active", "failed"}:
            raise ValueError("portrait finalization is invalid")
        with self._connection(immediate=True) as db:
            if self._portrait_finalize_hook is not None:
                self._portrait_finalize_hook("before_update")
            cursor = db.execute("UPDATE canvas_assets SET service_id=?,upstream_asset_id=?,status=?,updated_at=? WHERE asset_id=? AND kind='portrait' AND upstream_asset_id IS NULL", (service_id, upstream_asset_id, status, _now(), asset_id))
            if cursor.rowcount != 1:
                raise KeyError(asset_id)
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone()
        assert row is not None
        return dict(row)

    def delete_reserved_portrait_asset(self, asset_id: str, user_id: str) -> bool:
        with self._connection(immediate=True) as db:
            cursor = db.execute("DELETE FROM canvas_assets WHERE asset_id=? AND user_id=? AND kind='portrait' AND upstream_asset_id IS NULL", (asset_id, user_id))
        return cursor.rowcount == 1

    def record_portrait_finalize_recovery(self, asset_id: str, *, upstream_asset_id: str, status: str) -> Path:
        if not _RESULT_ID.fullmatch(asset_id) or not _RESULT_ID.fullmatch(upstream_asset_id) or status not in {"processing", "active", "failed"}:
            raise ValueError("portrait recovery is invalid")
        destination = self.assets_dir / f".portrait-recovery-{asset_id}.pending"
        temporary = self.assets_dir / f".{secrets.token_hex(20)}.recovery"
        try:
            with temporary.open("x", encoding="ascii") as output:
                output.write(f"portal-portrait\n{upstream_asset_id}\n{status}\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

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

    def _audit(self, db: sqlite3.Connection, *, actor_user_id: str, action: str, target_type: str, target_id: str) -> None:
        if not all(isinstance(value, str) and value and len(value) <= 128 for value in (actor_user_id, action, target_type, target_id)):
            raise ValueError("audit event is invalid")
        db.execute(
            "INSERT INTO canvas_admin_audit(actor_user_id,action,target_type,target_id,created_at) VALUES (?,?,?,?,?)",
            (actor_user_id, action, target_type, target_id, _now()),
        )

    @staticmethod
    def _require_revision(expected_revision: int) -> None:
        from ai_creation_canvas.model_routing import RevisionConflict

        if type(expected_revision) is not int or expected_revision < 1:
            raise RevisionConflict("revision conflict")

    @staticmethod
    def _logical_from_row(row: sqlite3.Row | None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition

        if row is None:
            return None
        if bool(row["runtime_purged"]):
            return CanvasStore._logical_stub(row)
        return LogicalModelDefinition.from_record(dict(row))

    @staticmethod
    def _route_from_row(row: sqlite3.Row | None):
        from ai_creation_canvas.model_routing import ModelRouteDefinition

        if row is None:
            return None
        if bool(row["runtime_purged"]):
            return CanvasStore._route_stub(row)
        return ModelRouteDefinition.from_record(dict(row))

    @staticmethod
    def _logical_stub(row: sqlite3.Row):
        from ai_creation_canvas.model_routing import HistoricalAuditStub

        return HistoricalAuditStub(
            object_id=str(row["model_id"]),
            object_type="model",
            display_name=str(row["display_name"]),
            modality=str(row["modality"]),
            model_id=None,
            enabled=False,
            archived_at=str(row["archived_at"]),
            revision=int(row["revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _route_stub(row: sqlite3.Row):
        from ai_creation_canvas.model_routing import HistoricalAuditStub

        return HistoricalAuditStub(
            object_id=str(row["route_id"]),
            object_type="route",
            display_name=None,
            modality=None,
            model_id=str(row["model_id"]),
            enabled=False,
            archived_at=str(row["archived_at"]),
            revision=int(row["revision"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def create_logical_model(self, definition, *, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition

        if (
            not isinstance(definition, LogicalModelDefinition)
            or definition.revision != 1
            or definition.archived_at is not None
        ):
            raise ValueError("logical model definition is invalid")
        now = _now()
        try:
            with self._connection(immediate=True) as db:
                db.execute(
                    "INSERT INTO canvas_logical_models("
                    "model_id,display_name,introduction,modality,operation_contracts_json,enabled,"
                    "archived_at,revision,runtime_purged,created_at,updated_at"
                    ") VALUES (?,?,?,?,?,?,NULL,1,0,?,?)",
                    (
                        definition.model_id,
                        definition.display_name,
                        definition.introduction,
                        definition.modality.value,
                        definition.contracts_json(),
                        int(definition.enabled),
                        now,
                        now,
                    ),
                )
                if actor_user_id is not None:
                    self._audit(db, actor_user_id=actor_user_id, action="logical_model.create", target_type="logical_model", target_id=definition.model_id)
                row = db.execute(
                    "SELECT * FROM canvas_logical_models WHERE model_id=?", (definition.model_id,)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise ValueError("logical model already exists") from error
        assert row is not None
        return LogicalModelDefinition.from_record(dict(row))

    def logical_model(self, model_id: str):
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (model_id,)
            ).fetchone()
        return self._logical_from_row(row)

    def list_logical_models(self, *, include_archived: bool = True):
        query = "SELECT * FROM canvas_logical_models"
        if not include_archived:
            query += " WHERE archived_at IS NULL AND runtime_purged=0"
        query += " ORDER BY model_id"
        with self._connection() as db:
            rows = db.execute(query).fetchall()
        return tuple(self._logical_from_row(row) for row in rows)

    def update_logical_model(self, definition, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition, RevisionConflict, validate_route_model

        self._require_revision(expected_revision)
        if not isinstance(definition, LogicalModelDefinition) or definition.revision != expected_revision:
            raise RevisionConflict("logical model revision conflict")
        with self._connection(immediate=True) as db:
            current = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (definition.model_id,)
            ).fetchone()
            if current is None:
                raise KeyError(definition.model_id)
            if int(current["revision"]) != expected_revision:
                raise RevisionConflict("logical model revision conflict")
            if bool(current["runtime_purged"]):
                raise ValueError("logical model runtime was purged")
            current_archived = current["archived_at"]
            if definition.archived_at != current_archived:
                raise ValueError("use an explicit logical model lifecycle transition")
            routes = db.execute(
                "SELECT * FROM canvas_model_routes WHERE model_id=? AND runtime_purged=0 ORDER BY route_id",
                (definition.model_id,),
            ).fetchall()
            for route in routes:
                validate_route_model(self._route_from_row(route), definition)
            cursor = db.execute(
                "UPDATE canvas_logical_models SET display_name=?,introduction=?,modality=?,"
                "operation_contracts_json=?,enabled=?,revision=revision+1,updated_at=? "
                "WHERE model_id=? AND revision=? AND runtime_purged=0",
                (
                    definition.display_name,
                    definition.introduction,
                    definition.modality.value,
                    definition.contracts_json(),
                    int(definition.enabled),
                    _now(),
                    definition.model_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("logical model revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="logical_model.update", target_type="logical_model", target_id=definition.model_id)
            row = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (definition.model_id,)
            ).fetchone()
        assert row is not None
        return LogicalModelDefinition.from_record(dict(row))

    def set_logical_model_enabled(self, model_id: str, *, enabled: bool, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition, RevisionConflict

        self._require_revision(expected_revision)
        if type(enabled) is not bool:
            raise ValueError("logical model enabled state is invalid")
        with self._connection(immediate=True) as db:
            current = db.execute("SELECT archived_at,runtime_purged FROM canvas_logical_models WHERE model_id=?", (model_id,)).fetchone()
            if current is None:
                raise KeyError(model_id)
            if current["archived_at"] is not None or bool(current["runtime_purged"]):
                raise RevisionConflict("logical model lifecycle transition conflict")
            cursor = db.execute(
                "UPDATE canvas_logical_models SET enabled=?,revision=revision+1,updated_at=? WHERE model_id=? AND revision=? AND archived_at IS NULL AND runtime_purged=0",
                (int(enabled), _now(), model_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("logical model revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action=f"logical_model.{'enable' if enabled else 'disable'}", target_type="logical_model", target_id=model_id)
            row = db.execute("SELECT * FROM canvas_logical_models WHERE model_id=?", (model_id,)).fetchone()
        assert row is not None
        return LogicalModelDefinition.from_record(dict(row))

    def archive_logical_model(self, model_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            current = db.execute("SELECT revision,archived_at,runtime_purged FROM canvas_logical_models WHERE model_id=?", (model_id,)).fetchone()
            if current is None:
                raise KeyError(model_id)
            if int(current["revision"]) != expected_revision or current["archived_at"] is not None or bool(current["runtime_purged"]):
                raise RevisionConflict("logical model revision conflict")
            cursor = db.execute(
                "UPDATE canvas_logical_models SET enabled=0,archived_at=?,revision=revision+1,updated_at=? "
                "WHERE model_id=? AND revision=? AND archived_at IS NULL AND runtime_purged=0",
                (_now(), _now(), model_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("logical model revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="logical_model.archive", target_type="logical_model", target_id=model_id)
            row = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (model_id,)
            ).fetchone()
        assert row is not None
        return LogicalModelDefinition.from_record(dict(row))

    def restore_logical_model(self, model_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            current = db.execute("SELECT revision,archived_at,runtime_purged FROM canvas_logical_models WHERE model_id=?", (model_id,)).fetchone()
            if current is None:
                raise KeyError(model_id)
            if int(current["revision"]) != expected_revision or current["archived_at"] is None or bool(current["runtime_purged"]):
                raise RevisionConflict("logical model revision conflict")
            cursor = db.execute(
                "UPDATE canvas_logical_models SET enabled=0,archived_at=NULL,revision=revision+1,updated_at=? "
                "WHERE model_id=? AND revision=? AND archived_at IS NOT NULL AND runtime_purged=0",
                (_now(), model_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("logical model revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="logical_model.restore", target_type="logical_model", target_id=model_id)
            row = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (model_id,)
            ).fetchone()
        assert row is not None
        return LogicalModelDefinition.from_record(dict(row))

    def create_model_route(self, definition, *, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition, validate_route_model

        if (
            not isinstance(definition, ModelRouteDefinition)
            or definition.revision != 1
            or definition.archived_at is not None
        ):
            raise ValueError("model route definition is invalid")
        now = _now()
        try:
            with self._connection(immediate=True) as db:
                model_row = db.execute(
                    "SELECT * FROM canvas_logical_models WHERE model_id=? AND runtime_purged=0",
                    (definition.model_id,),
                ).fetchone()
                if model_row is None:
                    raise KeyError(definition.model_id)
                validate_route_model(definition, LogicalModelDefinition.from_record(dict(model_row)))
                db.execute(
                    "INSERT INTO canvas_model_routes("
                    "route_id,model_id,provider_id,provider_model_name,adapter_type,credential_pool_ref,family,"
                    "operation_contracts_json,priority,max_concurrency,enabled,archived_at,revision,runtime_purged,"
                    "created_at,updated_at"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,1,0,?,?)",
                    (
                        definition.route_id,
                        definition.model_id,
                        definition.provider_id,
                        definition.provider_model_name,
                        definition.adapter_type,
                        definition.credential_pool_ref,
                        definition.family,
                        definition.contracts_json(),
                        definition.priority,
                        definition.max_concurrency,
                        int(definition.enabled),
                        now,
                        now,
                    ),
                )
                if actor_user_id is not None:
                    self._audit(db, actor_user_id=actor_user_id, action="model_route.create", target_type="model_route", target_id=definition.route_id)
                row = db.execute(
                    "SELECT * FROM canvas_model_routes WHERE route_id=?", (definition.route_id,)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise ValueError("model route already exists") from error
        assert row is not None
        return ModelRouteDefinition.from_record(dict(row))

    def model_route(self, route_id: str):
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (route_id,)
            ).fetchone()
        return self._route_from_row(row)

    def list_model_routes(self, *, model_id: str | None = None, include_archived: bool = True):
        clauses: list[str] = []
        values: list[object] = []
        if model_id is not None:
            clauses.append("model_id=?")
            values.append(model_id)
        if not include_archived:
            clauses.append("archived_at IS NULL AND runtime_purged=0")
        query = "SELECT * FROM canvas_model_routes"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY route_id"
        with self._connection() as db:
            rows = db.execute(query, values).fetchall()
        return tuple(self._route_from_row(row) for row in rows)

    def update_model_route(self, definition, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import LogicalModelDefinition, ModelRouteDefinition, RevisionConflict, validate_route_model

        self._require_revision(expected_revision)
        if not isinstance(definition, ModelRouteDefinition) or definition.revision != expected_revision:
            raise RevisionConflict("model route revision conflict")
        with self._connection(immediate=True) as db:
            current = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (definition.route_id,)
            ).fetchone()
            if current is None:
                raise KeyError(definition.route_id)
            if int(current["revision"]) != expected_revision:
                raise RevisionConflict("model route revision conflict")
            if bool(current["runtime_purged"]):
                raise ValueError("model route runtime was purged")
            if definition.model_id != current["model_id"]:
                raise ValueError("model route model_id is immutable")
            if definition.archived_at != current["archived_at"]:
                raise ValueError("use an explicit model route lifecycle transition")
            model_row = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=? AND runtime_purged=0",
                (definition.model_id,),
            ).fetchone()
            if model_row is None:
                raise KeyError(definition.model_id)
            validate_route_model(definition, LogicalModelDefinition.from_record(dict(model_row)))
            cursor = db.execute(
                "UPDATE canvas_model_routes SET provider_id=?,provider_model_name=?,adapter_type=?,"
                "credential_pool_ref=?,family=?,operation_contracts_json=?,priority=?,max_concurrency=?,"
                "enabled=?,revision=revision+1,updated_at=? WHERE route_id=? AND revision=? AND runtime_purged=0",
                (
                    definition.provider_id,
                    definition.provider_model_name,
                    definition.adapter_type,
                    definition.credential_pool_ref,
                    definition.family,
                    definition.contracts_json(),
                    definition.priority,
                    definition.max_concurrency,
                    int(definition.enabled),
                    _now(),
                    definition.route_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("model route revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="model_route.update", target_type="model_route", target_id=definition.route_id)
                if int(current["priority"]) != definition.priority:
                    self._audit(db, actor_user_id=actor_user_id, action="model_route.priority_change", target_type="model_route", target_id=definition.route_id)
            row = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (definition.route_id,)
            ).fetchone()
        assert row is not None
        return ModelRouteDefinition.from_record(dict(row))

    def set_model_route_enabled(self, route_id: str, *, enabled: bool, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import ModelRouteDefinition, RevisionConflict

        self._require_revision(expected_revision)
        if type(enabled) is not bool:
            raise ValueError("model route enabled state is invalid")
        with self._connection(immediate=True) as db:
            current = db.execute("SELECT archived_at,runtime_purged FROM canvas_model_routes WHERE route_id=?", (route_id,)).fetchone()
            if current is None:
                raise KeyError(route_id)
            if current["archived_at"] is not None or bool(current["runtime_purged"]):
                raise RevisionConflict("model route lifecycle transition conflict")
            cursor = db.execute(
                "UPDATE canvas_model_routes SET enabled=?,revision=revision+1,updated_at=? WHERE route_id=? AND revision=? AND archived_at IS NULL AND runtime_purged=0",
                (int(enabled), _now(), route_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("model route revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action=f"model_route.{'enable' if enabled else 'disable'}", target_type="model_route", target_id=route_id)
            row = db.execute("SELECT * FROM canvas_model_routes WHERE route_id=?", (route_id,)).fetchone()
        assert row is not None
        return ModelRouteDefinition.from_record(dict(row))

    def archive_model_route(self, route_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import ModelRouteDefinition, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            current = db.execute("SELECT revision,archived_at,runtime_purged FROM canvas_model_routes WHERE route_id=?", (route_id,)).fetchone()
            if current is None:
                raise KeyError(route_id)
            if int(current["revision"]) != expected_revision or current["archived_at"] is not None or bool(current["runtime_purged"]):
                raise RevisionConflict("model route revision conflict")
            cursor = db.execute(
                "UPDATE canvas_model_routes SET enabled=0,archived_at=?,revision=revision+1,updated_at=? "
                "WHERE route_id=? AND revision=? AND archived_at IS NULL AND runtime_purged=0",
                (_now(), _now(), route_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("model route revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="model_route.archive", target_type="model_route", target_id=route_id)
            row = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (route_id,)
            ).fetchone()
        assert row is not None
        return ModelRouteDefinition.from_record(dict(row))

    def restore_model_route(self, route_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import ModelRouteDefinition, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            current = db.execute("SELECT revision,archived_at,runtime_purged FROM canvas_model_routes WHERE route_id=?", (route_id,)).fetchone()
            if current is None:
                raise KeyError(route_id)
            if int(current["revision"]) != expected_revision or current["archived_at"] is None or bool(current["runtime_purged"]):
                raise RevisionConflict("model route revision conflict")
            cursor = db.execute(
                "UPDATE canvas_model_routes SET enabled=0,archived_at=NULL,revision=revision+1,updated_at=? "
                "WHERE route_id=? AND revision=? AND archived_at IS NOT NULL AND runtime_purged=0",
                (_now(), route_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("model route revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="model_route.restore", target_type="model_route", target_id=route_id)
            row = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (route_id,)
            ).fetchone()
        assert row is not None
        return ModelRouteDefinition.from_record(dict(row))

    @staticmethod
    def _route_references_in(db: sqlite3.Connection, route_id: str) -> tuple[str, ...]:
        route = db.execute(
            "SELECT model_id FROM canvas_model_routes WHERE route_id=?", (route_id,)
        ).fetchone()
        if route is None:
            return ()
        model_id = str(route["model_id"])
        if route_id == _legacy_route_id(model_id):
            rows = db.execute(
                "SELECT id FROM canvas_jobs WHERE route_id=? OR (route_id IS NULL AND model_id=?) ORDER BY id",
                (route_id, model_id),
            )
        else:
            rows = db.execute(
                "SELECT id FROM canvas_jobs WHERE route_id=? ORDER BY id", (route_id,)
            )
        return tuple(f"job:{row['id']}" for row in rows)

    def route_references(self, route_id: str) -> tuple[str, ...]:
        with self._connection() as db:
            return self._route_references_in(db, route_id)

    @staticmethod
    def _logical_model_references_in(db: sqlite3.Connection, model_id: str) -> tuple[str, ...]:
        references: list[str] = []
        references.extend(
            f"job:{row['id']}"
            for row in db.execute(
                "SELECT id FROM canvas_jobs WHERE model_id=? ORDER BY id", (model_id,)
            )
        )
        references.extend(
            f"access:{row['user_id']}"
            for row in db.execute(
                "SELECT user_id FROM canvas_model_access WHERE model_id=? ORDER BY user_id", (model_id,)
            )
        )
        references.extend(
            f"assignment:{row['user_id']}"
            for row in db.execute(
                "SELECT user_id FROM canvas_user_models WHERE model_id=? ORDER BY user_id", (model_id,)
            )
        )
        references.extend(
            f"route:{row['route_id']}"
            for row in db.execute(
                "SELECT route_id FROM canvas_model_routes WHERE model_id=? ORDER BY route_id", (model_id,)
            )
        )
        return tuple(sorted(references))

    def logical_model_references(self, model_id: str) -> tuple[str, ...]:
        with self._connection() as db:
            return self._logical_model_references_in(db, model_id)

    def delete_model_route(self, route_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import DeleteResult, ObjectReferenced, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            row = db.execute(
                "SELECT revision,runtime_purged FROM canvas_model_routes WHERE route_id=?", (route_id,)
            ).fetchone()
            if row is None:
                raise KeyError(route_id)
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict("model route revision conflict")
            if bool(row["runtime_purged"]):
                raise ObjectReferenced("historical model route audit stub cannot be deleted")
            references = self._route_references_in(db, route_id)
            if references:
                raise ObjectReferenced("model route is referenced: " + ", ".join(references))
            cursor = db.execute(
                "DELETE FROM canvas_model_routes WHERE route_id=? AND revision=?",
                (route_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("model route revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="model_route.delete", target_type="model_route", target_id=route_id)
        return DeleteResult(deleted=True)

    def delete_logical_model(self, model_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import DeleteResult, ObjectReferenced, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            row = db.execute(
                "SELECT revision,runtime_purged FROM canvas_logical_models WHERE model_id=?", (model_id,)
            ).fetchone()
            if row is None:
                raise KeyError(model_id)
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict("logical model revision conflict")
            if bool(row["runtime_purged"]):
                raise ObjectReferenced("historical logical model audit stub cannot be deleted")
            references = self._logical_model_references_in(db, model_id)
            if references:
                raise ObjectReferenced("logical model is referenced: " + ", ".join(references))
            cursor = db.execute(
                "DELETE FROM canvas_logical_models WHERE model_id=? AND revision=?",
                (model_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflict("logical model revision conflict")
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="logical_model.delete", target_type="logical_model", target_id=model_id)
        return DeleteResult(deleted=True)

    def purge_model_route_runtime(self, route_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import ObjectReferenced, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (route_id,)
            ).fetchone()
            if row is None:
                raise KeyError(route_id)
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict("model route revision conflict")
            if bool(row["runtime_purged"]):
                raise ValueError("model route runtime was purged")
            if not self._route_references_in(db, route_id):
                raise ObjectReferenced("unreferenced model route must be physically deleted")
            now = _now()
            db.execute(
                "UPDATE canvas_model_routes SET provider_id=NULL,provider_model_name=NULL,adapter_type=NULL,"
                "credential_pool_ref=NULL,family=NULL,operation_contracts_json='[]',priority=NULL,"
                "max_concurrency=NULL,enabled=0,archived_at=COALESCE(archived_at,?),revision=revision+1,"
                "runtime_purged=1,updated_at=? WHERE route_id=? AND revision=?",
                (now, now, route_id, expected_revision),
            )
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="model_route.purge_runtime", target_type="model_route", target_id=route_id)
            updated = db.execute(
                "SELECT * FROM canvas_model_routes WHERE route_id=?", (route_id,)
            ).fetchone()
        assert updated is not None
        return self._route_stub(updated)

    def purge_logical_model_runtime(self, model_id: str, *, expected_revision: int, actor_user_id: str | None = None):
        from ai_creation_canvas.model_routing import ObjectReferenced, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (model_id,)
            ).fetchone()
            if row is None:
                raise KeyError(model_id)
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict("logical model revision conflict")
            if bool(row["runtime_purged"]):
                raise ValueError("logical model runtime was purged")
            if not self._logical_model_references_in(db, model_id):
                raise ObjectReferenced("unreferenced logical model must be physically deleted")
            now = _now()
            routes = db.execute(
                "SELECT route_id,runtime_purged FROM canvas_model_routes WHERE model_id=? ORDER BY route_id",
                (model_id,),
            ).fetchall()
            for route in routes:
                route_id = str(route["route_id"])
                if bool(route["runtime_purged"]):
                    continue
                if self._route_references_in(db, route_id):
                    db.execute(
                        "UPDATE canvas_model_routes SET provider_id=NULL,provider_model_name=NULL,adapter_type=NULL,"
                        "credential_pool_ref=NULL,family=NULL,operation_contracts_json='[]',priority=NULL,"
                        "max_concurrency=NULL,enabled=0,archived_at=COALESCE(archived_at,?),revision=revision+1,"
                        "runtime_purged=1,updated_at=? WHERE route_id=? AND runtime_purged=0",
                        (now, now, route_id),
                    )
                    if actor_user_id is not None:
                        self._audit(db, actor_user_id=actor_user_id, action="model_route.purge_runtime", target_type="model_route", target_id=route_id)
                else:
                    db.execute("DELETE FROM canvas_model_routes WHERE route_id=?", (route_id,))
                    if actor_user_id is not None:
                        self._audit(db, actor_user_id=actor_user_id, action="model_route.delete", target_type="model_route", target_id=route_id)
            db.execute(
                "UPDATE canvas_logical_models SET introduction='',operation_contracts_json='[]',enabled=0,"
                "archived_at=COALESCE(archived_at,?),revision=revision+1,runtime_purged=1,updated_at=? "
                "WHERE model_id=? AND revision=?",
                (now, now, model_id, expected_revision),
            )
            if actor_user_id is not None:
                self._audit(db, actor_user_id=actor_user_id, action="logical_model.purge_runtime", target_type="logical_model", target_id=model_id)
            updated = db.execute(
                "SELECT * FROM canvas_logical_models WHERE model_id=?", (model_id,)
            ).fetchone()
        assert updated is not None
        return self._logical_stub(updated)

    def create_provider_definition(self, definition, *, actor_user_id: str):
        from ai_creation_canvas.model_registry import ProviderDefinition, provider_from_record
        if not isinstance(definition, ProviderDefinition) or definition.revision != 1:
            raise ValueError("provider definition is invalid")
        now = _now()
        try:
            with self._connection(immediate=True) as db:
                db.execute(
                    "INSERT INTO canvas_providers(provider_id,display_name,adapter_type,base_url,credential_ref,enabled,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (definition.provider_id, definition.display_name, definition.adapter_type, definition.base_url, definition.credential_ref, int(definition.enabled), 1, now, now),
                )
                self._audit(db, actor_user_id=actor_user_id, action="provider.create", target_type="provider", target_id=definition.provider_id)
                row = db.execute("SELECT * FROM canvas_providers WHERE provider_id=?", (definition.provider_id,)).fetchone()
        except sqlite3.IntegrityError as error:
            raise ValueError("provider already exists") from error
        assert row is not None
        return provider_from_record(dict(row))

    def provider_definition(self, provider_id: str):
        from ai_creation_canvas.model_registry import provider_from_record
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_providers WHERE provider_id=?", (provider_id,)).fetchone()
        return provider_from_record(dict(row)) if row is not None else None

    def list_provider_definitions(self):
        from ai_creation_canvas.model_registry import provider_from_record
        with self._connection() as db:
            rows = db.execute("SELECT * FROM canvas_providers ORDER BY provider_id").fetchall()
        return tuple(provider_from_record(dict(row)) for row in rows)

    def provider_references(self, provider_id: str) -> tuple[str, ...]:
        with self._connection() as db:
            references = [
                f"route:{row['route_id']}"
                for row in db.execute("SELECT route_id FROM canvas_model_routes WHERE provider_id=? ORDER BY route_id", (provider_id,))
            ]
            references.extend(
                f"model:{row['model_id']}"
                for row in db.execute("SELECT model_id FROM canvas_models WHERE provider_id=? ORDER BY model_id", (provider_id,))
            )
        return tuple(references)

    def delete_provider_definition(self, provider_id: str, *, expected_revision: int, actor_user_id: str):
        from ai_creation_canvas.model_routing import DeleteResult, ObjectReferenced, RevisionConflict

        self._require_revision(expected_revision)
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT revision FROM canvas_providers WHERE provider_id=?", (provider_id,)).fetchone()
            if row is None:
                raise KeyError(provider_id)
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict("provider revision conflict")
            routes = db.execute("SELECT route_id FROM canvas_model_routes WHERE provider_id=?", (provider_id,)).fetchall()
            models = db.execute("SELECT model_id FROM canvas_models WHERE provider_id=?", (provider_id,)).fetchall()
            if routes or models:
                raise ObjectReferenced("provider is referenced")
            cursor = db.execute("DELETE FROM canvas_providers WHERE provider_id=? AND revision=?", (provider_id, expected_revision))
            if cursor.rowcount != 1:
                raise RevisionConflict("provider revision conflict")
            self._audit(db, actor_user_id=actor_user_id, action="provider.delete", target_type="provider", target_id=provider_id)
        return DeleteResult(deleted=True)

    def update_provider_definition(self, definition, *, expected_revision: int, actor_user_id: str):
        from ai_creation_canvas.model_registry import ProviderDefinition, provider_from_record
        if not isinstance(definition, ProviderDefinition) or type(expected_revision) is not int or expected_revision < 1 or definition.revision != expected_revision:
            raise ValueError("provider revision conflict")
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "UPDATE canvas_providers SET display_name=?,adapter_type=?,base_url=?,credential_ref=?,enabled=?,revision=revision+1,updated_at=? WHERE provider_id=? AND revision=?",
                (definition.display_name, definition.adapter_type, definition.base_url, definition.credential_ref, int(definition.enabled), _now(), definition.provider_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("provider revision conflict")
            self._audit(db, actor_user_id=actor_user_id, action="provider.update", target_type="provider", target_id=definition.provider_id)
            row = db.execute("SELECT * FROM canvas_providers WHERE provider_id=?", (definition.provider_id,)).fetchone()
        assert row is not None
        return provider_from_record(dict(row))

    def create_model_definition(self, definition, *, actor_user_id: str):
        from ai_creation_canvas.model_registry import GovernedModelDefinition
        if not isinstance(definition, GovernedModelDefinition) or definition.revision != 1:
            raise ValueError("model definition is invalid")
        now = _now()
        try:
            with self._connection(immediate=True) as db:
                if db.execute("SELECT 1 FROM canvas_providers WHERE provider_id=?", (definition.provider_id,)).fetchone() is None:
                    raise KeyError(definition.provider_id)
                db.execute(
                    "INSERT INTO canvas_models(model_id,provider_id,provider_model_name,display_name,introduction,modality,operation_contracts_json,enabled,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (definition.model_id, definition.provider_id, definition.provider_model_name, definition.display_name, definition.introduction, definition.modality.value, definition.contracts_json(), int(definition.enabled), 1, now, now),
                )
                self._audit(db, actor_user_id=actor_user_id, action="model.create", target_type="model", target_id=definition.model_id)
                row = db.execute("SELECT * FROM canvas_models WHERE model_id=?", (definition.model_id,)).fetchone()
        except sqlite3.IntegrityError as error:
            raise ValueError("model already exists") from error
        assert row is not None
        return GovernedModelDefinition.from_record(dict(row))

    def model_definition(self, model_id: str):
        from ai_creation_canvas.model_registry import GovernedModelDefinition
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_models WHERE model_id=?", (model_id,)).fetchone()
        return GovernedModelDefinition.from_record(dict(row)) if row is not None else None

    def update_model_definition(self, definition, *, expected_revision: int, actor_user_id: str):
        from ai_creation_canvas.model_registry import GovernedModelDefinition
        if not isinstance(definition, GovernedModelDefinition) or type(expected_revision) is not int or expected_revision < 1 or definition.revision != expected_revision:
            raise ValueError("model revision conflict")
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "UPDATE canvas_models SET provider_id=?,provider_model_name=?,display_name=?,introduction=?,modality=?,operation_contracts_json=?,enabled=?,revision=revision+1,updated_at=? WHERE model_id=? AND revision=?",
                (definition.provider_id, definition.provider_model_name, definition.display_name, definition.introduction, definition.modality.value, definition.contracts_json(), int(definition.enabled), _now(), definition.model_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("model revision conflict")
            self._audit(db, actor_user_id=actor_user_id, action="model.update", target_type="model", target_id=definition.model_id)
            row = db.execute("SELECT * FROM canvas_models WHERE model_id=?", (definition.model_id,)).fetchone()
        assert row is not None
        return GovernedModelDefinition.from_record(dict(row))

    def list_model_definitions(self, *, enabled_only: bool = False):
        from ai_creation_canvas.model_registry import GovernedModelDefinition
        query = "SELECT * FROM canvas_models" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY model_id"
        with self._connection() as db:
            rows = db.execute(query).fetchall()
        return tuple(GovernedModelDefinition.from_record(dict(row)) for row in rows)

    def grant_model_access(self, user_id: str, model_id: str, *, actor_user_id: str) -> None:
        with self._connection(immediate=True) as db:
            model_exists = db.execute("SELECT 1 FROM canvas_models WHERE model_id=?", (model_id,)).fetchone() is not None or db.execute("SELECT 1 FROM canvas_logical_models WHERE model_id=? AND runtime_purged=0", (model_id,)).fetchone() is not None
            local_user = db.execute("SELECT 1 FROM canvas_users WHERE user_id=?", (user_id,)).fetchone() is not None
            logical_model = db.execute("SELECT 1 FROM canvas_logical_models WHERE model_id=? AND runtime_purged=0", (model_id,)).fetchone() is not None
            if not model_exists or (not local_user and not logical_model):
                raise KeyError((user_id, model_id))
            now = _now()
            db.execute(
                "INSERT INTO canvas_model_access(user_id,model_id,granted_by,granted_at,revoked_at) VALUES (?,?,?,?,NULL) ON CONFLICT(user_id,model_id) DO UPDATE SET granted_by=excluded.granted_by,granted_at=excluded.granted_at,revoked_at=NULL",
                (user_id, model_id, actor_user_id, now),
            )
            self._audit(db, actor_user_id=actor_user_id, action="model_access.grant", target_type="user_model", target_id=f"{user_id}:{model_id}")

    def revoke_model_access(self, user_id: str, model_id: str, *, actor_user_id: str) -> None:
        with self._connection(immediate=True) as db:
            cursor = db.execute("UPDATE canvas_model_access SET revoked_at=? WHERE user_id=? AND model_id=? AND revoked_at IS NULL", (_now(), user_id, model_id))
            if cursor.rowcount != 1:
                raise KeyError((user_id, model_id))
            self._audit(db, actor_user_id=actor_user_id, action="model_access.revoke", target_type="user_model", target_id=f"{user_id}:{model_id}")

    def governed_assigned_models(self, user_id: str) -> tuple[str, ...]:
        with self._connection() as db:
            rows = db.execute("SELECT model_id FROM canvas_model_access WHERE user_id=? AND revoked_at IS NULL ORDER BY model_id", (user_id,)).fetchall()
        return tuple(str(row["model_id"]) for row in rows)

    def replace_governed_model_access(self, user_id: str, model_ids: tuple[str, ...], *, actor_user_id: str) -> tuple[str, ...]:
        if len(model_ids) > 128 or len(model_ids) != len(set(model_ids)):
            raise ValueError("model access is invalid")
        with self._connection(immediate=True) as db:
            if db.execute("SELECT 1 FROM canvas_users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise KeyError(user_id)
            if model_ids:
                placeholders = ",".join("?" for _ in model_ids)
                found = {
                    str(row[0])
                    for row in db.execute(
                        f"SELECT model_id FROM canvas_models WHERE model_id IN ({placeholders}) "
                        f"UNION SELECT model_id FROM canvas_logical_models WHERE runtime_purged=0 AND model_id IN ({placeholders})",
                        (*model_ids, *model_ids),
                    )
                }
                if found != set(model_ids):
                    raise KeyError("model")
            current = {str(row[0]) for row in db.execute("SELECT model_id FROM canvas_model_access WHERE user_id=? AND revoked_at IS NULL", (user_id,))}
            desired = set(model_ids)
            now = _now()
            for model_id in sorted(desired - current):
                db.execute("INSERT INTO canvas_model_access(user_id,model_id,granted_by,granted_at,revoked_at) VALUES (?,?,?,?,NULL) ON CONFLICT(user_id,model_id) DO UPDATE SET granted_by=excluded.granted_by,granted_at=excluded.granted_at,revoked_at=NULL", (user_id, model_id, actor_user_id, now))
                self._audit(db, actor_user_id=actor_user_id, action="model_access.grant", target_type="user_model", target_id=f"{user_id}:{model_id}")
            for model_id in sorted(current - desired):
                db.execute("UPDATE canvas_model_access SET revoked_at=? WHERE user_id=? AND model_id=?", (now, user_id, model_id))
                self._audit(db, actor_user_id=actor_user_id, action="model_access.revoke", target_type="user_model", target_id=f"{user_id}:{model_id}")
        return tuple(model_id for model_id in model_ids)

    def replace_user_model_access(
        self,
        user_id: str,
        static_model_ids: tuple[str, ...],
        governed_model_ids: tuple[str, ...],
        *,
        actor_user_id: str,
    ) -> tuple[str, ...]:
        """Atomically replace both catalog assignments and governed access."""
        all_ids = (*static_model_ids, *governed_model_ids)
        if (
            len(all_ids) > 128
            or len(all_ids) != len(set(all_ids))
            or any(not isinstance(item, str) or not item or len(item) > 128 for item in all_ids)
        ):
            raise ValueError("model access is invalid")
        with self._connection(immediate=True) as db:
            if db.execute("SELECT 1 FROM canvas_users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise KeyError(user_id)
            if governed_model_ids:
                placeholders = ",".join("?" for _ in governed_model_ids)
                found = {
                    str(row[0])
                    for row in db.execute(
                        f"SELECT model_id FROM canvas_models WHERE model_id IN ({placeholders}) "
                        f"UNION SELECT model_id FROM canvas_logical_models WHERE runtime_purged=0 AND model_id IN ({placeholders})",
                        (*governed_model_ids, *governed_model_ids),
                    )
                }
                if found != set(governed_model_ids):
                    raise KeyError("model")
            current_static = {
                str(row[0])
                for row in db.execute("SELECT model_id FROM canvas_user_models WHERE user_id=?", (user_id,))
            }
            current_governed = {
                str(row[0])
                for row in db.execute(
                    "SELECT model_id FROM canvas_model_access WHERE user_id=? AND revoked_at IS NULL", (user_id,)
                )
            }
            desired_static, desired_governed = set(static_model_ids), set(governed_model_ids)
            now = _now()
            for model_id in sorted(current_static - desired_static):
                db.execute("DELETE FROM canvas_user_models WHERE user_id=? AND model_id=?", (user_id, model_id))
                self._audit(db, actor_user_id=actor_user_id, action="model_assignment.revoke", target_type="user_model", target_id=f"{user_id}:{model_id}")
            for model_id in sorted(desired_static - current_static):
                db.execute("INSERT INTO canvas_user_models(user_id,model_id,created_at) VALUES (?,?,?)", (user_id, model_id, now))
                self._audit(db, actor_user_id=actor_user_id, action="model_assignment.grant", target_type="user_model", target_id=f"{user_id}:{model_id}")
            for model_id in sorted(desired_governed - current_governed):
                db.execute(
                    "INSERT INTO canvas_model_access(user_id,model_id,granted_by,granted_at,revoked_at) VALUES (?,?,?,?,NULL) "
                    "ON CONFLICT(user_id,model_id) DO UPDATE SET granted_by=excluded.granted_by,granted_at=excluded.granted_at,revoked_at=NULL",
                    (user_id, model_id, actor_user_id, now),
                )
                self._audit(db, actor_user_id=actor_user_id, action="model_access.grant", target_type="user_model", target_id=f"{user_id}:{model_id}")
            for model_id in sorted(current_governed - desired_governed):
                db.execute("UPDATE canvas_model_access SET revoked_at=? WHERE user_id=? AND model_id=?", (now, user_id, model_id))
                self._audit(db, actor_user_id=actor_user_id, action="model_access.revoke", target_type="user_model", target_id=f"{user_id}:{model_id}")
        return tuple(all_ids)

    def admin_audit_events(self) -> tuple[dict[str, object], ...]:
        with self._connection() as db:
            rows = db.execute("SELECT * FROM canvas_admin_audit ORDER BY event_id").fetchall()
        return tuple(dict(row) for row in rows)

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

    def reset_user_password(self, username_normalized: str, password_hash: str) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT user_id FROM canvas_users WHERE username_normalized=?", (username_normalized,)).fetchone()
            if row is None:
                raise KeyError(username_normalized)
            user_id = str(row["user_id"])
            db.execute(
                "UPDATE canvas_users SET password_hash=?,must_change_password=1,updated_at=? WHERE user_id=?",
                (password_hash, _now(), user_id),
            )
            db.execute("DELETE FROM canvas_sessions WHERE user_id=?", (user_id,))
            updated = db.execute("SELECT * FROM canvas_users WHERE user_id=?", (user_id,)).fetchone()
        assert updated is not None
        return dict(updated)

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

    def admin_usage_by_user(self) -> tuple[dict[str, object], ...]:
        """Return bounded, secret-free job counters grouped by the stored owner."""
        with self._connection() as db:
            rows = db.execute(
                """SELECT u.user_id,u.username_normalized,u.display_name,
                    COUNT(j.id) AS jobs,
                    COALESCE(SUM(CASE WHEN j.status='succeeded' THEN 1 ELSE 0 END),0) AS succeeded,
                    COALESCE(SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END),0) AS failed,
                    COALESCE(SUM(CASE WHEN j.status IN ('submitting','queued','running') THEN 1 ELSE 0 END),0) AS active,
                    COALESCE(SUM(CASE WHEN j.operation IN ('image.generate','image.edit') THEN 1 ELSE 0 END),0) AS image,
                    COALESCE(SUM(CASE WHEN j.operation='video.generate' THEN 1 ELSE 0 END),0) AS video
                FROM canvas_users AS u
                LEFT JOIN canvas_jobs AS j ON j.user_id=u.user_id
                GROUP BY u.user_id,u.username_normalized,u.display_name
                ORDER BY jobs DESC,u.username_normalized"""
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _validated_usage_value(value: int, *, name: str, maximum: int) -> int:
        if type(value) is not int or value < 0 or value > maximum:
            raise ValueError(f"{name} is invalid")
        return value

    def usage_rates(self) -> dict[str, int]:
        with self._connection() as db:
            row = db.execute(
                "SELECT video_price_fen,image_price_fen FROM canvas_usage_rates WHERE singleton=1"
            ).fetchone()
        assert row is not None
        return {
            "video_price_fen": int(row["video_price_fen"]),
            "image_price_fen": int(row["image_price_fen"]),
        }

    def set_usage_rates(self, *, video_price_fen: int, image_price_fen: int) -> dict[str, int]:
        video_price_fen = self._validated_usage_value(
            video_price_fen, name="video_price_fen", maximum=_MAX_USAGE_PRICE_FEN
        )
        image_price_fen = self._validated_usage_value(
            image_price_fen, name="image_price_fen", maximum=_MAX_USAGE_PRICE_FEN
        )
        with self._connection(immediate=True) as db:
            db.execute(
                "UPDATE canvas_usage_rates SET video_price_fen=?,image_price_fen=?,updated_at=? WHERE singleton=1",
                (video_price_fen, image_price_fen, _now()),
            )
        return {"video_price_fen": video_price_fen, "image_price_fen": image_price_fen}

    @staticmethod
    def _usage_jobs(db: sqlite3.Connection, user_id: str) -> tuple[dict[str, object], ...]:
        rows = db.execute(
            "SELECT operation,status,COALESCE(logical_model_id,model_id) AS model_id,route_id,"
            "video_seconds,image_count,video_price_fen,image_price_fen,cost_fen,charged_at "
            "FROM canvas_jobs WHERE user_id=? AND charged_at IS NOT NULL "
            "ORDER BY charged_at DESC,id DESC",
            (user_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def usage_for_owner(self, user_id: str) -> dict[str, object]:
        with self._connection() as db:
            jobs = self._usage_jobs(db, user_id)
        return {
            "user_id": user_id,
            "total_cost_fen": sum(int(job["cost_fen"]) for job in jobs),
            "jobs": jobs,
        }

    def usage_for_all_users(self) -> tuple[dict[str, object], ...]:
        with self._connection() as db:
            user_ids = db.execute(
                "SELECT user_id FROM canvas_users UNION SELECT DISTINCT user_id FROM canvas_jobs ORDER BY user_id"
            ).fetchall()
            usage = []
            for row in user_ids:
                user_id = str(row["user_id"])
                jobs = self._usage_jobs(db, user_id)
                usage.append({
                    "user_id": user_id,
                    "total_cost_fen": sum(int(job["cost_fen"]) for job in jobs),
                    "jobs": jobs,
                })
        return tuple(usage)

    def list_assets_for_owner(self, user_id: str, limit: int = 100) -> tuple[dict[str, object], ...]:
        safe_limit = min(max(limit, 1), 100)
        with self._connection() as db:
            rows = db.execute(
                "SELECT asset_id,kind,media_type,mime_type,status,size_bytes,created_at,updated_at FROM canvas_assets WHERE user_id=? ORDER BY created_at DESC,asset_id DESC LIMIT ?",
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

    def create_project(self, *, user_id: str, project_id: str, title: str, document_json: str) -> tuple[dict[str, object], bool, bool]:
        """Return row, created, conflict. Replaying an identical create is safe."""
        now = _now()
        with self._connection(immediate=True) as db:
            existing = db.execute("SELECT * FROM canvas_projects WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
            if existing is not None:
                row = dict(existing)
                return row, False, str(row["document_json"]) != document_json
            db.execute(
                "INSERT INTO canvas_projects(project_id,user_id,title,document_json,version,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
                (project_id, user_id, title, document_json, now, now),
            )
            row = db.execute("SELECT * FROM canvas_projects WHERE user_id=? AND project_id=?", (user_id, project_id)).fetchone()
        assert row is not None
        return dict(row), True, False

    def list_projects_for_owner(self, user_id: str, limit: int = 1000) -> tuple[dict[str, object], ...]:
        safe_limit = min(max(limit, 1), 1000)
        with self._connection() as db:
            rows = db.execute(
                "SELECT project_id,title,document_json,version,created_at,updated_at FROM canvas_projects WHERE user_id=? ORDER BY updated_at DESC,project_id DESC LIMIT ?",
                (user_id, safe_limit),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def project_for_owner(self, project_id: str, user_id: str) -> dict[str, object] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT project_id,title,document_json,version,created_at,updated_at FROM canvas_projects WHERE user_id=? AND project_id=?",
                (user_id, project_id),
            ).fetchone()
        return self._row(row)

    def update_project(self, *, user_id: str, project_id: str, title: str, document_json: str, expected_version: int) -> dict[str, object] | None:
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "UPDATE canvas_projects SET title=?,document_json=?,version=version+1,updated_at=? WHERE user_id=? AND project_id=? AND version=?",
                (title, document_json, _now(), user_id, project_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            row = db.execute(
                "SELECT project_id,title,document_json,version,created_at,updated_at FROM canvas_projects WHERE user_id=? AND project_id=?",
                (user_id, project_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def delete_project(self, *, user_id: str, project_id: str) -> bool:
        with self._connection(immediate=True) as db:
            cursor = db.execute("DELETE FROM canvas_projects WHERE user_id=? AND project_id=?", (user_id, project_id))
        return cursor.rowcount == 1

    def asset_for_owner(self, asset_id: str, user_id: str) -> tuple[dict[str, object] | None, bool]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id = ?", (asset_id,)).fetchone()
        item = self._row(row)
        return (item, bool(item and item["user_id"] != user_id))

    def delete_owned_local_reference_asset(self, asset_id: str, user_id: str, stage_file: Callable[[dict[str, object]], object], restore_file: Callable[[dict[str, object], object], None], purge_file: Callable[[object], None]) -> str:
        """Atomically tombstone a local file around the short metadata transaction."""
        item: dict[str, object] | None = None
        tombstone: object | None = None
        try:
            with self._connection(immediate=True) as db:
                row = db.execute("SELECT * FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone()
                if row is None:
                    return "not_found"
                item = dict(row)
                if item["user_id"] != user_id:
                    return "forbidden"
                if item["kind"] != "reference" or item.get("service_id") is not None or item.get("upstream_asset_id") is not None:
                    return "unsupported"
                tombstone = stage_file(item)
                cursor = db.execute("DELETE FROM canvas_assets WHERE asset_id=? AND user_id=?", (asset_id, user_id))
                if cursor.rowcount != 1:
                    raise RuntimeError("asset delete lost its ownership lock")
                if self._asset_delete_hook is not None:
                    self._asset_delete_hook("before_commit")
        except BaseException:
            if item is not None and tombstone is not None:
                restore_file(item, tombstone)
            raise
        if tombstone is not None:
            try:
                purge_file(tombstone)
            except OSError:
                pass  # A metadata-free tombstone is safe for a later maintenance sweep.
        return "deleted"

    def update_asset_status(self, asset_id: str, status: str) -> dict[str, object]:
        ranks = {"processing": 0, "active": 1, "failed": 1}
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone()
            if row is None: raise KeyError(asset_id)
            old = dict(row)
            if old["status"] in {"active", "failed"} or ranks.get(status, -1) < ranks.get(str(old["status"]), 0): return old
            db.execute("UPDATE canvas_assets SET status=?,updated_at=? WHERE asset_id=?", (status, _now(), asset_id))
            return dict(db.execute("SELECT * FROM canvas_assets WHERE asset_id=?", (asset_id,)).fetchone())

    def reserve_job(self, *, user_id: str, job_id: str, service_id: str, operation: str, idempotency_key: str, request_hash: str, lease_seconds: float = 30.0, model_id: str | None = None, model_revision: int | None = None, provider_id: str | None = None, adapter_type: str | None = None, submission_json: str | None = None, logical_model_id: str | None = None, logical_model_revision: int | None = None, video_seconds: int = 0, image_count: int = 0) -> Reservation:
        video_seconds = self._validated_usage_value(
            video_seconds, name="video_seconds", maximum=_MAX_VIDEO_SECONDS
        )
        image_count = self._validated_usage_value(
            image_count, name="image_count", maximum=_MAX_IMAGE_COUNT
        )
        now = _now()
        token = os.urandom(16).hex()
        lease_until = self._clock() + lease_seconds
        with self._connection(immediate=True) as db:
            existing = db.execute("SELECT * FROM canvas_jobs WHERE user_id = ? AND idempotency_key = ?", (user_id, idempotency_key)).fetchone()
            if existing is not None:
                item = dict(existing)
                if item["request_hash"] != request_hash:
                    return Reservation(item, False, True)
                lease_expired = float(item.get("lease_until") or 0) <= self._clock()
                if item["status"] == "submitting" and item.get("submission_state") == "in_flight" and lease_expired:
                    db.execute(
                        "UPDATE canvas_jobs SET status='submission_unknown',submission_state='submission_unknown',"
                        "error_code='SUBMISSION_UNKNOWN',submission_token=NULL,lease_until=NULL,updated_at=? "
                        "WHERE id=? AND submission_token IS ? AND status='submitting' AND submission_state='in_flight'",
                        (now, item["id"], item.get("submission_token")),
                    )
                    item = dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (item["id"],)).fetchone())
                    return Reservation(item, False)
                if item["status"] == "submitting" and item.get("submission_state") in {None, "reserved"} and lease_expired:
                    db.execute("UPDATE canvas_jobs SET submission_token=?, lease_until=?, attempt=attempt+1, error_code=NULL, updated_at=? WHERE id=? AND submission_token IS ?", (token, lease_until, now, item["id"], item.get("submission_token")))
                    item = dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (item["id"],)).fetchone())
                    return Reservation(item, True)
                return Reservation(item, False)
            db.execute("INSERT INTO canvas_jobs (id,user_id,service_id,operation,status,idempotency_key,request_hash,submission_token,lease_until,attempt,model_id,model_revision,provider_id,adapter_type,submission_json,logical_model_id,logical_model_revision,submission_state,video_seconds,image_count,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (job_id, user_id, service_id, operation, "submitting", idempotency_key, request_hash, token, lease_until, 1, model_id, model_revision, provider_id, adapter_type, submission_json, logical_model_id, logical_model_revision, "reserved" if logical_model_id is not None else None, video_seconds, image_count, now, now))
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        return Reservation(dict(row), True)

    def mark_submitted(
        self,
        job_id: str,
        upstream_job_id: str,
        status: str,
        token: str | None = None,
        *,
        result_ids: tuple[str, ...] | None = None,
        error_code: str | None = None,
    ) -> dict[str, object]:
        if status not in {"queued", "running", "succeeded", "failed"}:
            raise ValueError("submitted status is invalid")
        if result_ids is not None and (
            not 1 <= len(result_ids) <= 15
            or any(not isinstance(item, str) or _RESULT_ID.fullmatch(item) is None for item in result_ids)
            or len(set(result_ids)) != len(result_ids)
        ):
            raise ValueError("result IDs are invalid")
        if status == "succeeded" and result_ids is None:
            raise ValueError("successful submission results are required")
        if status != "succeeded" and result_ids is not None:
            raise ValueError("result IDs are invalid")
        if status == "failed":
            if not isinstance(error_code, str) or _ERROR_CODE.fullmatch(error_code) is None:
                error_code = "TASK_FAILED"
        elif error_code is not None:
            raise ValueError("submitted error code is invalid")
        encoded = json.dumps(result_ids, separators=(",", ":")) if result_ids is not None else None
        result_id = result_ids[0] if result_ids is not None else None
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise KeyError(job_id)
            if token is not None and row["submission_token"] != token:
                return dict(row)
            now = _now()
            db.execute(
                "UPDATE canvas_jobs SET status=?,upstream_job_id=?,error_code=?,result_id=?,result_ids_json=?,"
                "submission_state=CASE WHEN submission_state IS NULL THEN NULL ELSE 'submitted' END,"
                "submission_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                (status, upstream_job_id, error_code, result_id, encoded, now, job_id),
            )
            if status == "succeeded":
                self._capture_usage_snapshot(db, job_id, now)
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def record_routing_snapshot(self, job_id: str, token: str, *, logical_model_id: str, logical_model_revision: int, route_id: str, route_revision: int, pool_revision_digest: str, key_fingerprint: str, route_snapshot_json: str) -> dict[str, object]:
        if not all(isinstance(value, str) and value for value in (logical_model_id, route_id, pool_revision_digest, key_fingerprint, route_snapshot_json)):
            raise ValueError("routing snapshot is invalid")
        if len(route_snapshot_json.encode("utf-8")) > 64 * 1024 or "secret" in route_snapshot_json.lower() or "key_id" in route_snapshot_json.lower():
            raise ValueError("routing snapshot is unsafe")
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["submission_token"] != token or row["status"] != "submitting" or row["submission_state"] not in {"reserved", "in_flight"}:
                return dict(row)
            cursor = db.execute(
                "UPDATE canvas_jobs SET logical_model_id=?,logical_model_revision=?,route_id=?,route_revision=?,pool_revision_digest=?,key_fingerprint=?,route_snapshot_json=?,submission_state='in_flight',updated_at=? "
                "WHERE id=? AND submission_token=? AND status='submitting' AND submission_state IN ('reserved','in_flight')",
                (logical_model_id, logical_model_revision, route_id, route_revision, pool_revision_digest, key_fingerprint, route_snapshot_json, _now(), job_id, token),
            )
            if cursor.rowcount != 1:
                return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def mark_submission_unknown(
        self,
        job_id: str,
        token: str,
        error_code: str = "SUBMISSION_UNKNOWN",
        *,
        upstream_job_id: str | None = None,
    ) -> dict[str, object]:
        if upstream_job_id is not None and (not isinstance(upstream_job_id, str) or not upstream_job_id):
            raise ValueError("upstream job ID is invalid")
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["submission_token"] != token:
                return dict(row)
            db.execute(
                "UPDATE canvas_jobs SET status='submission_unknown',submission_state='submission_unknown',"
                "error_code=?,upstream_job_id=COALESCE(?,upstream_job_id),submission_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                (error_code, upstream_job_id, _now(), job_id),
            )
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def mark_submission_rejected(self, job_id: str, token: str, error_code: str = "REQUEST_REJECTED") -> dict[str, object]:
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["submission_token"] != token:
                return dict(row)
            db.execute("UPDATE canvas_jobs SET status='failed',submission_state='rejected',error_code=?,submission_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (error_code, _now(), job_id))
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def mark_safe_retry(self, job_id: str, token: str, *, lease_seconds: float = 30.0) -> dict[str, object]:
        """Return a verified not-submitted attempt to its short pre-I/O lease."""
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["submission_token"] != token or row["submission_state"] != "in_flight":
                return dict(row)
            db.execute(
                "UPDATE canvas_jobs SET submission_state='reserved',lease_until=?,updated_at=? WHERE id=? AND submission_token=? AND submission_state='in_flight'",
                (self._clock() + lease_seconds, _now(), job_id, token),
            )
            return dict(db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone())

    def expire_in_flight(self, job_id: str) -> dict[str, object]:
        with self._connection(immediate=True) as db:
            db.execute(
                "UPDATE canvas_jobs SET status='submission_unknown',submission_state='submission_unknown',"
                "error_code='SUBMISSION_UNKNOWN',submission_token=NULL,lease_until=NULL,updated_at=? "
                "WHERE id=? AND status='submitting' AND submission_state='in_flight' AND lease_until<=?",
                (_now(), job_id, self._clock()),
            )
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def claim_pollable_job(self, *, lease_seconds: float = 30.0) -> dict[str, object] | None:
        """Lease one submitted job for the background poll worker."""
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be positive")
        token = os.urandom(16).hex()
        now = self._clock()
        with self._connection(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM canvas_jobs "
                "WHERE status IN ('queued','running') AND upstream_job_id IS NOT NULL "
                "AND (lease_until IS NULL OR lease_until<=?) "
                "ORDER BY updated_at,id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            cursor = db.execute(
                "UPDATE canvas_jobs SET submission_token=?,lease_until=?,updated_at=? "
                "WHERE id=? AND status IN ('queued','running') AND upstream_job_id IS NOT NULL "
                "AND (lease_until IS NULL OR lease_until<=?)",
                (token, now + float(lease_seconds), _now(), row["id"], now),
            )
            if cursor.rowcount != 1:
                return None
            claimed = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (row["id"],)).fetchone()
        assert claimed is not None
        return dict(claimed)

    def record_polled_job(
        self,
        job_id: str,
        *,
        token: str,
        status: str,
        error_code: str | None = None,
        result_id: str | None = None,
        result_ids: tuple[str, ...] | None = None,
        retry_after_seconds: float = 2.0,
        acknowledgement_required: bool = False,
    ) -> PolledJobWrite:
        """Commit one leased poll result; stale workers cannot overwrite a reclaim."""
        ranks = {"queued": 2, "running": 3, "succeeded": 4, "failed": 4}
        if status not in ranks:
            raise ValueError("polled status is invalid")
        if (
            not isinstance(retry_after_seconds, (int, float))
            or isinstance(retry_after_seconds, bool)
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds is invalid")
        if result_ids is not None:
            if (
                not 1 <= len(result_ids) <= 15
                or any(not isinstance(item, str) or _RESULT_ID.fullmatch(item) is None for item in result_ids)
                or len(set(result_ids)) != len(result_ids)
            ):
                raise ValueError("result IDs are invalid")
            if result_id is not None and result_id != result_ids[0]:
                raise ValueError("result IDs are invalid")
            result_id = result_ids[0]
            encoded = json.dumps(result_ids, separators=(",", ":"))
        elif result_id is not None:
            if not isinstance(result_id, str) or _RESULT_ID.fullmatch(result_id) is None:
                raise ValueError("result IDs are invalid")
            encoded = json.dumps((result_id,), separators=(",", ":"))
        else:
            encoded = None
        if status == "succeeded" and result_id is None:
            raise ValueError("successful poll results are required")
        if not isinstance(acknowledgement_required, bool):
            raise ValueError("acknowledgement requirement is invalid")
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            old = dict(row)
            if old.get("submission_token") != token or old["status"] not in {"queued", "running"}:
                return PolledJobWrite(old, False)
            if ranks[status] < ranks[str(old["status"])]:
                status = str(old["status"])
            terminal = status in {"succeeded", "failed"}
            next_poll = None if terminal else self._clock() + float(retry_after_seconds)
            now = _now()
            cursor = db.execute(
                "UPDATE canvas_jobs SET status=?,error_code=COALESCE(?,error_code),"
                "result_id=COALESCE(?,result_id),result_ids_json=COALESCE(?,result_ids_json),"
                "submission_token=NULL,lease_until=?,updated_at=? WHERE id=? AND submission_token=?",
                (status, error_code, result_id, encoded, next_poll, now, job_id, token),
            )
            applied = cursor.rowcount == 1
            if applied and status == "succeeded":
                self._capture_usage_snapshot(db, job_id, now)
                if acknowledgement_required:
                    db.execute(
                        "INSERT OR IGNORE INTO canvas_job_acknowledgements(job_id,token,lease_until,created_at,updated_at) "
                        "VALUES (?,NULL,NULL,?,?)",
                        (job_id, now, now),
                    )
            updated = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
        assert updated is not None
        return PolledJobWrite(dict(updated), applied)

    def claim_job_acknowledgement(self, *, lease_seconds: float = 30.0) -> dict[str, object] | None:
        """Lease durable provider acknowledgement work independently of polling."""
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be positive")
        token = os.urandom(16).hex()
        now = self._clock()
        with self._connection(immediate=True) as db:
            row = db.execute(
                "SELECT j.id FROM canvas_job_acknowledgements a "
                "JOIN canvas_jobs j ON j.id=a.job_id "
                "WHERE j.status='succeeded' AND (a.lease_until IS NULL OR a.lease_until<=?) "
                "ORDER BY a.updated_at,a.job_id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            cursor = db.execute(
                "UPDATE canvas_job_acknowledgements SET token=?,lease_until=?,updated_at=? "
                "WHERE job_id=? AND (lease_until IS NULL OR lease_until<=?)",
                (token, now + float(lease_seconds), _now(), row["id"], now),
            )
            if cursor.rowcount != 1:
                return None
            claimed = db.execute(
                "SELECT j.*,a.token AS acknowledgement_token,a.lease_until AS acknowledgement_lease_until "
                "FROM canvas_job_acknowledgements a JOIN canvas_jobs j ON j.id=a.job_id "
                "WHERE a.job_id=?",
                (row["id"],),
            ).fetchone()
        assert claimed is not None
        return dict(claimed)

    def renew_job_acknowledgement(self, job_id: str, *, token: str, lease_seconds: float = 30.0) -> bool:
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be positive")
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "UPDATE canvas_job_acknowledgements SET lease_until=?,updated_at=? WHERE job_id=? AND token=?",
                (self._clock() + float(lease_seconds), _now(), job_id, token),
            )
        return cursor.rowcount == 1

    def release_job_acknowledgement(
        self,
        job_id: str,
        *,
        token: str,
        retry_after_seconds: float = 2.0,
    ) -> bool:
        if (
            not isinstance(retry_after_seconds, (int, float))
            or isinstance(retry_after_seconds, bool)
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds is invalid")
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "UPDATE canvas_job_acknowledgements SET token=NULL,lease_until=?,updated_at=? "
                "WHERE job_id=? AND token=?",
                (self._clock() + float(retry_after_seconds), _now(), job_id, token),
            )
        return cursor.rowcount == 1

    def complete_job_acknowledgement(self, job_id: str, *, token: str) -> bool:
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "DELETE FROM canvas_job_acknowledgements WHERE job_id=? AND token=?",
                (job_id, token),
            )
        return cursor.rowcount == 1

    def renew_job_lease(self, job_id: str, *, token: str, lease_seconds: float = 30.0) -> bool:
        if (
            not isinstance(lease_seconds, (int, float))
            or isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be positive")
        with self._connection(immediate=True) as db:
            cursor = db.execute(
                "UPDATE canvas_jobs SET lease_until=?,updated_at=? "
                "WHERE id=? AND submission_token=? AND status IN ('queued','running')",
                (self._clock() + float(lease_seconds), _now(), job_id, token),
            )
        return cursor.rowcount == 1

    def release_job_lease(self, job_id: str, *, token: str, retry_after_seconds: float = 2.0) -> dict[str, object]:
        if (
            not isinstance(retry_after_seconds, (int, float))
            or isinstance(retry_after_seconds, bool)
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds is invalid")
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["submission_token"] == token and row["status"] in {"queued", "running"}:
                db.execute(
                    "UPDATE canvas_jobs SET submission_token=NULL,lease_until=?,updated_at=? WHERE id=? AND submission_token=?",
                    (self._clock() + float(retry_after_seconds), _now(), job_id, token),
                )
            updated = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def fail_reservation(self, job_id: str, error_code: str = "TASK_FAILED", token: str | None = None) -> dict[str, object]:
        # Retain a short-lived reservation that can be reclaimed, without losing the key.
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None: raise KeyError(job_id)
            if token is not None and row["submission_token"] != token: return dict(row)
            db.execute("UPDATE canvas_jobs SET error_code=?, lease_until=?, submission_state=CASE WHEN submission_state='in_flight' THEN 'reserved' ELSE submission_state END, updated_at=? WHERE id=?", (error_code, self._clock() - 1, _now(), job_id))
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

    def mark_cancelled(self, job_id: str) -> dict[str, object]:
        """Record a successful queued-provider cancellation despite a stale running poll."""
        with self._connection(immediate=True) as db:
            db.execute(
                "UPDATE canvas_jobs SET status='failed',error_code='TASK_CANCELLED',updated_at=? "
                "WHERE id=? AND status IN ('queued','running')",
                (_now(), job_id),
            )
            row = db.execute("SELECT * FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    @staticmethod
    def _capture_usage_snapshot(db: sqlite3.Connection, job_id: str, now: str) -> None:
        job = db.execute(
            "SELECT video_seconds,image_count,charged_at FROM canvas_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if job is None or job["charged_at"] is not None:
            return
        if int(job["video_seconds"]) == 0 and int(job["image_count"]) == 0:
            return
        rates = db.execute(
            "SELECT video_price_fen,image_price_fen FROM canvas_usage_rates WHERE singleton=1"
        ).fetchone()
        assert rates is not None
        total = (
            int(job["video_seconds"]) * int(rates["video_price_fen"])
            + int(job["image_count"]) * int(rates["image_price_fen"])
        )
        db.execute(
            "UPDATE canvas_jobs SET video_price_fen=?,image_price_fen=?,cost_fen=?,charged_at=? "
            "WHERE id=? AND charged_at IS NULL",
            (rates["video_price_fen"], rates["image_price_fen"], total, now, job_id),
        )

    def _update(self, job_id: str, *, status: str, upstream_job_id: str | None = None, error_code: str | None = None, result_id: str | None = None, result_ref: str | None = None, result_ids: tuple[str, ...] | None = None) -> dict[str, object]:
        ranks = {"uploading": 0, "submitting": 1, "queued": 2, "running": 3, "succeeded": 4, "failed": 4}
        with self._connection(immediate=True) as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            old = dict(row)
            if old["status"] in {"succeeded", "failed"} or ranks.get(status, -1) < ranks.get(str(old["status"]), 0):
                return old
            now = _now()
            if result_ids is not None:
                if not 1 <= len(result_ids) <= 15 or len(set(result_ids)) != len(result_ids) or any(not _RESULT_ID.fullmatch(item) for item in result_ids):
                    raise ValueError("result IDs are invalid")
                result_id = result_ids[0]
                result_ids_json = json.dumps(result_ids, separators=(",", ":"))
            else:
                result_id = result_id if result_id is not None else result_ref
                result_ids_json = None
            db.execute("UPDATE canvas_jobs SET status=?, upstream_job_id=COALESCE(?, upstream_job_id), error_code=COALESCE(?, error_code), result_id=COALESCE(?, result_id), result_ids_json=COALESCE(?, result_ids_json), updated_at=? WHERE id=?", (status, upstream_job_id, error_code, result_id, result_ids_json, now, job_id))
            if status == "succeeded":
                self._capture_usage_snapshot(db, job_id, now)
            updated = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def job_for_owner(self, job_id: str, user_id: str) -> tuple[dict[str, object] | None, bool]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM canvas_jobs WHERE id = ?", (job_id,)).fetchone()
        item = self._row(row)
        return (item, bool(item and item["user_id"] != user_id))
