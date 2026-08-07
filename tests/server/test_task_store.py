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
