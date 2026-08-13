from ai_creation_canvas.storage.sqlite import CanvasStore, StoreInitializationError
import pytest
import sqlite3
import time


def test_store_creates_owned_job_reservation(tmp_path):
    store = CanvasStore(tmp_path / "data")
    reservation = store.reserve_job(
        user_id="user-a", job_id="job-a", service_id="images", operation="image.generate",
        idempotency_key="key-a", request_hash="a" * 64,
    )
    assert reservation.created is True
    assert reservation.job["id"] == "job-a"


def test_store_uses_wal_and_reclaims_expired_lease(tmp_path):
    store = CanvasStore(tmp_path / "data")
    assert sqlite3.connect(store.database).execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    first = store.reserve_job(user_id="u", job_id="j", service_id="s", operation="image.generate", idempotency_key="k", request_hash="a" * 64, lease_seconds=0.1)
    assert store.reserve_job(user_id="u", job_id="other", service_id="s", operation="image.generate", idempotency_key="k", request_hash="a" * 64).created is False
    time.sleep(0.11)
    recovered = store.reserve_job(user_id="u", job_id="other", service_id="s", operation="image.generate", idempotency_key="k", request_hash="a" * 64)
    assert recovered.created and recovered.job["id"] == "j" and recovered.job["submission_token"] != first.job["submission_token"]

def test_legacy_result_ref_is_rebuilt_without_legacy_column(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    db = sqlite3.connect(data / "canvas.sqlite3")
    db.execute("CREATE TABLE canvas_jobs (id TEXT PRIMARY KEY,user_id TEXT,service_id TEXT,upstream_job_id TEXT,operation TEXT,status TEXT,idempotency_key TEXT,request_hash TEXT,error_code TEXT,result_ref TEXT,created_at TEXT,updated_at TEXT)")
    db.execute("INSERT INTO canvas_jobs VALUES ('j','u','s','up','op','succeeded','k','h',NULL,'opaque_id','t','t')")
    db.commit(); db.close()
    store = CanvasStore(data)
    row, _ = store.job_for_owner("j", "u")
    assert row and row["result_id"] == "opaque_id"
    assert "result_ref" not in {item[1] for item in sqlite3.connect(store.database).execute("PRAGMA table_info(canvas_jobs)")}
    assert store.usage_rates() == {"video_price_fen": 0, "image_price_fen": 0}
    assert store.usage_for_owner("u") == {"user_id": "u", "total_cost_fen": 0, "jobs": ()}

def test_stale_reservation_token_cannot_overwrite_reclaimed_job(tmp_path):
    store = CanvasStore(tmp_path / "data")
    first = store.reserve_job(user_id="u", job_id="j", service_id="s", operation="op", idempotency_key="k", request_hash="h", lease_seconds=0.001)
    time.sleep(0.01)
    second = store.reserve_job(user_id="u", job_id="other", service_id="s", operation="op", idempotency_key="k", request_hash="h")
    stale = store.mark_submitted("j", "old", "queued", first.job["submission_token"])
    assert stale["upstream_job_id"] is None
    fresh = store.mark_submitted("j", "new", "queued", second.job["submission_token"])
    assert fresh["upstream_job_id"] == "new"

def test_active_lease_and_reopened_store_preserve_one_reservation(tmp_path):
    path = tmp_path / "data"
    first = CanvasStore(path).reserve_job(user_id="u", job_id="j", service_id="s", operation="op", idempotency_key="k", request_hash="h")
    reopened = CanvasStore(path).reserve_job(user_id="u", job_id="other", service_id="s", operation="op", idempotency_key="k", request_hash="h")
    assert first.created and not reopened.created and reopened.job["id"] == "j"


def test_legacy_migration_physically_scrubs_signed_urls_but_keeps_opaque_ids(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    database = data / "canvas.sqlite3"
    secret = "https://provider.test/private/result?signature=unique-signed-secret"
    db = sqlite3.connect(database)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE canvas_jobs (id TEXT PRIMARY KEY,user_id TEXT,service_id TEXT,upstream_job_id TEXT,operation TEXT,status TEXT,idempotency_key TEXT,request_hash TEXT,error_code TEXT,result_ref TEXT,created_at TEXT,updated_at TEXT)")
    db.execute("INSERT INTO canvas_jobs VALUES ('bad','u','s','up','op','succeeded','k1','h',NULL,?,'t','t')", (secret,))
    db.execute("INSERT INTO canvas_jobs VALUES ('good','u','s','up','op','succeeded','k2','h',NULL,'opaque_result_1','t','t')")
    db.commit(); db.close()

    store = CanvasStore(data)
    with store._connection() as verify:
        assert verify.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert "result_ref" not in {row[1] for row in verify.execute("PRAGMA table_info(canvas_jobs)")}
        assert verify.execute("SELECT result_id FROM canvas_jobs WHERE id='bad'").fetchone()[0] is None
        assert verify.execute("SELECT result_id FROM canvas_jobs WHERE id='good'").fetchone()[0] == "opaque_result_1"
    for path in (database, database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")):
        if path.exists():
            assert secret.encode() not in path.read_bytes()


def test_pending_legacy_scrub_recovers_after_a_crash_between_schema_commit_and_vacuum(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    database = data / "canvas.sqlite3"
    secret = "https://provider.test/private/result?signature=crash-window-secret"
    db = sqlite3.connect(database)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE canvas_jobs (id TEXT PRIMARY KEY,user_id TEXT,service_id TEXT,upstream_job_id TEXT,operation TEXT,status TEXT,idempotency_key TEXT,request_hash TEXT,error_code TEXT,result_ref TEXT,created_at TEXT,updated_at TEXT)")
    db.execute("INSERT INTO canvas_jobs VALUES ('bad','u','s','up','op','succeeded','k','h',NULL,?,'t','t')", (secret,))
    db.commit(); db.close()

    def crash_after_schema_commit(stage):
        if stage == "after_schema_commit":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        CanvasStore(data, migration_hook=crash_after_schema_commit)
    pending = sqlite3.connect(database)
    assert pending.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()[0] == "1"
    pending.close()

    CanvasStore(data)
    verify = sqlite3.connect(database)
    assert verify.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()[0] == "0"
    assert verify.execute("SELECT result_id FROM canvas_jobs WHERE id='bad'").fetchone()[0] is None
    verify.close()
    for path in (database, database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")):
        if path.exists():
            assert secret.encode() not in path.read_bytes()


def test_busy_checkpoints_preserve_pending_marker_until_a_later_startup_can_scrub(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    database = data / "canvas.sqlite3"
    secret = "https://provider.test/private/result?signature=checkpoint-busy-secret"
    db = sqlite3.connect(database)
    db.execute("CREATE TABLE canvas_jobs (id TEXT PRIMARY KEY,user_id TEXT,service_id TEXT,upstream_job_id TEXT,operation TEXT,status TEXT,idempotency_key TEXT,request_hash TEXT,error_code TEXT,result_ref TEXT,created_at TEXT,updated_at TEXT)")
    db.execute("INSERT INTO canvas_jobs VALUES ('bad','u','s','up','op','succeeded','k','h',NULL,?,'t','t')", (secret,))
    db.commit(); db.close()

    with pytest.raises(StoreInitializationError, match="checkpoint"):
        CanvasStore(data, checkpoint_hook=lambda phase, attempt: (1, 9, 0))
    pending = sqlite3.connect(database)
    assert pending.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()[0] == "1"
    pending.close()

    CanvasStore(data)
    verify = sqlite3.connect(database)
    assert verify.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()[0] == "0"
    verify.close()
    for path in (database, database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")):
        if path.exists():
            assert secret.encode() not in path.read_bytes()


def test_checkpoint_busy_once_retries_before_clearing_the_pending_marker(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    db = sqlite3.connect(data / "canvas.sqlite3")
    db.execute("CREATE TABLE canvas_jobs (id TEXT PRIMARY KEY,user_id TEXT,service_id TEXT,upstream_job_id TEXT,operation TEXT,status TEXT,idempotency_key TEXT,request_hash TEXT,error_code TEXT,result_ref TEXT,created_at TEXT,updated_at TEXT)")
    db.execute("INSERT INTO canvas_jobs VALUES ('bad','u','s','up','op','succeeded','k','h',NULL,'https://provider.test/?checkpoint-once','t','t')")
    db.commit(); db.close()
    calls: list[tuple[str, int]] = []

    def busy_once(phase, attempt):
        calls.append((phase, attempt))
        return (1, 1, 0) if len(calls) == 1 else None

    store = CanvasStore(data, checkpoint_hook=busy_once)
    assert calls[:2] == [("before_vacuum", 0), ("before_vacuum", 1)]
    with store._connection() as verify:
        assert verify.execute("SELECT value FROM canvas_meta WHERE key='legacy_result_scrub_pending'").fetchone()[0] == "0"


def test_success_captures_current_rates_once(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    reserved = store.reserve_job(user_id="user-a", job_id="video", service_id="video", operation="video.generate", idempotency_key="video-key", request_hash="v" * 64, video_seconds=5)
    store.mark_submitted("video", "up-video", "running", str(reserved.job["submission_token"]))
    claim = store.claim_pollable_job()
    assert claim is not None
    store.record_polled_job("video", token=str(claim["submission_token"]), status="succeeded", result_id="result")
    store.set_usage_rates(video_price_fen=99, image_price_fen=999)
    usage = store.usage_for_owner("user-a")
    assert usage["total_cost_fen"] == 125
    assert usage["jobs"][0]["video_price_fen"] == 25


def test_failed_and_repeated_completion_do_not_charge(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=10, image_price_fen=20)
    reserved = store.reserve_job(user_id="user-a", job_id="image", service_id="image", operation="image.generate", idempotency_key="image-key", request_hash="i" * 64, image_count=1)
    store.mark_submitted("image", "up-image", "running", str(reserved.job["submission_token"]))
    claim = store.claim_pollable_job()
    assert claim is not None
    store.record_polled_job("image", token=str(claim["submission_token"]), status="failed", error_code="TASK_FAILED")
    assert store.usage_for_owner("user-a")["total_cost_fen"] == 0
