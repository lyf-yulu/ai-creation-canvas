from ai_creation_canvas.storage.sqlite import CanvasStore


def test_store_creates_owned_job_reservation(tmp_path):
    store = CanvasStore(tmp_path / "data")
    reservation = store.reserve_job(
        user_id="user-a", job_id="job-a", service_id="images", operation="image.generate",
        idempotency_key="key-a", request_hash="a" * 64,
    )
    assert reservation.created is True
    assert reservation.job["id"] == "job-a"
