from ai_creation_canvas.storage.sqlite import CanvasStore
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
