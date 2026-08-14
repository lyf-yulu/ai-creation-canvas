from ai_creation_canvas.storage.sqlite import CanvasStore, StoreInitializationError
import json
import math
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


def test_direct_submission_begin_is_single_owner_cas_and_expires_unknown(tmp_path):
    now = [1_000.0]
    store = CanvasStore(tmp_path / "data", clock=lambda: now[0])
    reservation = store.reserve_job(
        user_id="user-a",
        job_id="job-a",
        service_id="images",
        operation="image.generate",
        idempotency_key="key-a",
        request_hash="a" * 64,
        lease_seconds=30,
    )
    token = str(reservation.job["submission_token"])

    assert reservation.job["submission_state"] == "reserved"
    assert store.begin_direct_submission("job-a", token) is True
    assert store.begin_direct_submission("job-a", token) is False
    assert store.begin_direct_submission("job-a", "stale-token") is False
    in_flight, _ = store.job_for_owner("job-a", "user-a")
    assert in_flight is not None
    assert in_flight["submission_state"] == "in_flight"
    assert in_flight["logical_model_id"] is None
    assert in_flight["route_snapshot_json"] is None

    now[0] += 31
    repeated = store.reserve_job(
        user_id="user-a",
        job_id="other",
        service_id="images",
        operation="image.generate",
        idempotency_key="key-a",
        request_hash="a" * 64,
    )
    assert repeated.created is False
    assert repeated.job["id"] == "job-a"
    assert repeated.job["status"] == "submission_unknown"
    assert repeated.job["submission_state"] == "submission_unknown"
    assert repeated.job["submission_token"] is None


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
    assert sqlite3.connect(store.database).execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canvas_job_acknowledgements'"
    ).fetchone() == (1,)
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


def test_pollable_job_lease_excludes_concurrent_workers_and_expired_lease_is_reclaimed(tmp_path):
    class Clock:
        value = 1_000.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    store = CanvasStore(tmp_path / "data", clock=clock)
    store.reserve_job(
        user_id="u",
        job_id="j",
        service_id="s",
        operation="video.generate",
        idempotency_key="k",
        request_hash="h",
    )
    store.mark_submitted("j", "upstream-j", "running")

    first = store.claim_pollable_job(lease_seconds=30)
    assert first is not None
    assert first["id"] == "j"
    assert store.claim_pollable_job(lease_seconds=30) is None

    clock.value += 31
    reclaimed = store.claim_pollable_job(lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed["id"] == "j"
    assert reclaimed["submission_token"] != first["submission_token"]

    stale = store.record_polled_job("j", token=str(first["submission_token"]), status="succeeded", result_id="stale_result")
    assert stale.applied is False
    assert stale.job["status"] == "running"
    fresh = store.record_polled_job("j", token=str(reclaimed["submission_token"]), status="succeeded", result_id="fresh_result")
    assert fresh.applied is True
    assert fresh.job["status"] == "succeeded"
    assert fresh.job["result_id"] == "fresh_result"


def test_pollable_job_lease_claims_direct_and_managed_jobs_but_excludes_ineligible_rows(tmp_path):
    class Clock:
        value = 1_000.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    store = CanvasStore(tmp_path / "data", clock=clock)

    def submitted(job_id, status, *, managed=False):
        reservation = store.reserve_job(
            user_id="u",
            job_id=job_id,
            service_id="s",
            operation="video.generate",
            idempotency_key=f"key-{job_id}",
            request_hash=f"hash-{job_id}",
            logical_model_id=f"model-{job_id}" if managed else None,
        )
        return store.mark_submitted(
            job_id,
            f"upstream-{job_id}",
            status,
            str(reservation.job["submission_token"]),
            result_ids=(f"result-{job_id}",) if status == "succeeded" else None,
        )

    for job_id, status, managed in (
        ("direct-queued", "queued", False),
        ("direct-running", "running", False),
        ("managed-queued", "queued", True),
        ("managed-running", "running", True),
    ):
        submitted(job_id, status, managed=managed)

    unknown = store.reserve_job(
        user_id="u",
        job_id="submission-unknown",
        service_id="s",
        operation="video.generate",
        idempotency_key="key-submission-unknown",
        request_hash="hash-submission-unknown",
    )
    store.mark_submission_unknown("submission-unknown", str(unknown.job["submission_token"]))
    submitted("terminal", "succeeded")
    store.reserve_job(
        user_id="u",
        job_id="missing-upstream",
        service_id="s",
        operation="video.generate",
        idempotency_key="key-missing-upstream",
        request_hash="hash-missing-upstream",
    )
    store._update("missing-upstream", status="queued")
    submitted("leased", "running")
    with store._connection(immediate=True) as db:
        db.execute(
            "UPDATE canvas_jobs SET submission_token='held', lease_until=? WHERE id='leased'",
            (clock.value + 30,),
        )

    claimed_ids = []
    for _ in range(4):
        claim = store.claim_pollable_job(lease_seconds=30)
        assert claim is not None
        claimed_ids.append(claim["id"])

    assert set(claimed_ids) == {
        "direct-queued",
        "direct-running",
        "managed-queued",
        "managed-running",
    }
    assert store.claim_pollable_job(lease_seconds=30) is None


def test_polled_success_persists_ordered_multi_results_and_charges_once(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    reservation = store.reserve_job(
        user_id="user-a",
        job_id="multi-result",
        service_id="video",
        operation="video.generate",
        idempotency_key="multi-result-key",
        request_hash="m" * 64,
        video_seconds=5,
    )
    store.mark_submitted(
        "multi-result", "upstream-multi-result", "running", str(reservation.job["submission_token"])
    )

    claim = store.claim_pollable_job(lease_seconds=30)
    assert claim is not None
    updated = store.record_polled_job(
        str(claim["id"]),
        token=str(claim["submission_token"]),
        status="succeeded",
        result_ids=("result_1", "result_2"),
    )

    assert updated.applied is True
    assert json.loads(str(updated.job["result_ids_json"])) == ["result_1", "result_2"]
    assert updated.job["result_id"] == "result_1"
    store.record_polled_job(
        str(claim["id"]),
        token=str(claim["submission_token"]),
        status="succeeded",
        result_ids=("result_1", "result_2"),
    )
    usage = store.usage_for_owner("user-a")
    assert usage["total_cost_fen"] == 125
    assert len(usage["jobs"]) == 1


def test_success_cas_persists_acknowledgement_work_and_only_current_ack_token_clears_it(tmp_path):
    class Clock:
        value = 1_000.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    data_dir = tmp_path / "data"
    store = CanvasStore(data_dir, clock=clock)
    reservation = store.reserve_job(
        user_id="u",
        job_id="ack-job",
        service_id="recoverable",
        operation="image.generate",
        idempotency_key="ack-key",
        request_hash="a" * 64,
    )
    store.mark_submitted(
        "ack-job",
        "upstream-ack-job",
        "queued",
        str(reservation.job["submission_token"]),
    )
    claim = store.claim_pollable_job(lease_seconds=30)
    assert claim is not None

    written = store.record_polled_job(
        "ack-job",
        token=str(claim["submission_token"]),
        status="succeeded",
        result_ids=("result_ack",),
        acknowledgement_required=True,
    )

    assert written.applied is True
    reopened = CanvasStore(data_dir, clock=clock)
    acknowledgement = reopened.claim_job_acknowledgement(lease_seconds=30)
    assert acknowledgement is not None
    assert acknowledgement["id"] == "ack-job"
    first_token = str(acknowledgement["acknowledgement_token"])
    assert reopened.claim_job_acknowledgement(lease_seconds=30) is None
    clock.value += 31
    reclaimed = reopened.claim_job_acknowledgement(lease_seconds=30)
    assert reclaimed is not None
    token = str(reclaimed["acknowledgement_token"])
    assert token != first_token
    assert reopened.complete_job_acknowledgement("ack-job", token=first_token) is False
    assert reopened.complete_job_acknowledgement("ack-job", token="stale-token") is False
    assert reopened.complete_job_acknowledgement("ack-job", token=token) is True
    assert reopened.claim_job_acknowledgement(lease_seconds=30) is None


@pytest.mark.parametrize(
    "result_ids",
    (
        (),
        ("duplicate", "duplicate"),
        ("unsafe/result",),
        tuple(f"result_{index}" for index in range(16)),
    ),
)
def test_record_polled_job_rejects_invalid_result_ids_without_changing_the_leased_row(tmp_path, result_ids):
    store = CanvasStore(tmp_path / "data")
    reservation = store.reserve_job(
        user_id="u",
        job_id="invalid-results",
        service_id="s",
        operation="image.generate",
        idempotency_key="invalid-results-key",
        request_hash="i" * 64,
    )
    store.mark_submitted(
        "invalid-results", "upstream-invalid-results", "running", str(reservation.job["submission_token"])
    )
    claim = store.claim_pollable_job(lease_seconds=30)
    assert claim is not None
    before, forbidden = store.job_for_owner("invalid-results", "u")
    assert before is not None and forbidden is False

    with pytest.raises(ValueError, match="result IDs are invalid"):
        store.record_polled_job(
            "invalid-results",
            token=str(claim["submission_token"]),
            status="succeeded",
            result_ids=result_ids,
        )

    after, forbidden = store.job_for_owner("invalid-results", "u")
    assert after == before
    assert forbidden is False


def test_record_polled_job_rejects_success_without_results_without_changing_or_charging(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    reservation = store.reserve_job(
        user_id="u",
        job_id="missing-success-result",
        service_id="video",
        operation="video.generate",
        idempotency_key="missing-success-result-key",
        request_hash="r" * 64,
        video_seconds=5,
    )
    store.mark_submitted(
        "missing-success-result",
        "upstream-missing-success-result",
        "running",
        str(reservation.job["submission_token"]),
    )
    claim = store.claim_pollable_job(lease_seconds=30)
    assert claim is not None
    before, forbidden = store.job_for_owner("missing-success-result", "u")
    assert before is not None and forbidden is False

    with pytest.raises(ValueError, match="successful poll results are required"):
        store.record_polled_job(
            "missing-success-result",
            token=str(claim["submission_token"]),
            status="succeeded",
        )

    after, forbidden = store.job_for_owner("missing-success-result", "u")
    assert after == before
    assert forbidden is False
    assert store.usage_for_owner("u")["total_cost_fen"] == 0


@pytest.mark.parametrize("retry_after_seconds", (True, math.nan, math.inf, -math.inf, -0.1))
def test_record_polled_job_rejects_invalid_retry_after_without_changing_the_leased_row(tmp_path, retry_after_seconds):
    store = CanvasStore(tmp_path / "data")
    reservation = store.reserve_job(
        user_id="u",
        job_id="invalid-record-retry-after",
        service_id="s",
        operation="image.generate",
        idempotency_key="invalid-record-retry-after-key",
        request_hash="r" * 64,
    )
    store.mark_submitted(
        "invalid-record-retry-after",
        "upstream-invalid-record-retry-after",
        "running",
        str(reservation.job["submission_token"]),
    )
    claim = store.claim_pollable_job(lease_seconds=30)
    assert claim is not None
    before, _ = store.job_for_owner("invalid-record-retry-after", "u")

    with pytest.raises(ValueError, match="retry_after_seconds is invalid"):
        store.record_polled_job(
            "invalid-record-retry-after",
            token=str(claim["submission_token"]),
            status="running",
            retry_after_seconds=retry_after_seconds,
        )

    after, _ = store.job_for_owner("invalid-record-retry-after", "u")
    assert after == before


@pytest.mark.parametrize("retry_after_seconds", (True, math.nan, math.inf, -math.inf, -0.1))
def test_release_job_lease_rejects_invalid_retry_after_without_changing_the_leased_row(tmp_path, retry_after_seconds):
    store = CanvasStore(tmp_path / "data")
    reservation = store.reserve_job(
        user_id="u",
        job_id="invalid-release-retry-after",
        service_id="s",
        operation="image.generate",
        idempotency_key="invalid-release-retry-after-key",
        request_hash="r" * 64,
    )
    store.mark_submitted(
        "invalid-release-retry-after",
        "upstream-invalid-release-retry-after",
        "running",
        str(reservation.job["submission_token"]),
    )
    claim = store.claim_pollable_job(lease_seconds=30)
    assert claim is not None
    before, _ = store.job_for_owner("invalid-release-retry-after", "u")

    with pytest.raises(ValueError, match="retry_after_seconds is invalid"):
        store.release_job_lease(
            "invalid-release-retry-after",
            token=str(claim["submission_token"]),
            retry_after_seconds=retry_after_seconds,
        )

    after, _ = store.job_for_owner("invalid-release-retry-after", "u")
    assert after == before


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


def test_synchronous_submission_success_captures_current_rates(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    reserved = store.reserve_job(user_id="user-a", job_id="sync", service_id="image", operation="image.generate", idempotency_key="sync-key", request_hash="s" * 64, image_count=1)
    store.mark_submitted(
        "sync", "up-sync", "succeeded", str(reserved.job["submission_token"]),
        result_ids=("sync-result",),
    )
    store.set_usage_rates(video_price_fen=99, image_price_fen=999)
    usage = store.usage_for_owner("user-a")
    assert usage["total_cost_fen"] == 120
    assert len(usage["jobs"]) == 1
    job = usage["jobs"][0]
    assert job["operation"] == "image.generate"
    assert job["status"] == "succeeded"
    assert job["image_count"] == 1
    assert job["image_price_fen"] == 120
    assert job["cost_fen"] == 120
    assert job["charged_at"] is not None


def test_success_without_a_billable_quantity_keeps_every_snapshot_empty(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    reserved = store.reserve_job(
        user_id="user-a",
        job_id="unmetered-video",
        service_id="video",
        operation="video.generate",
        idempotency_key="unmetered-key",
        request_hash="u" * 64,
    )

    completed = store.mark_submitted(
        "unmetered-video",
        "up-unmetered",
        "succeeded",
        str(reserved.job["submission_token"]),
        result_ids=("unmetered-result",),
    )

    assert completed["video_seconds"] == completed["image_count"] == 0
    assert completed["video_price_fen"] is None
    assert completed["image_price_fen"] is None
    assert completed["cost_fen"] is None
    assert completed["charged_at"] is None
    assert store.usage_for_owner("user-a") == {
        "user_id": "user-a",
        "total_cost_fen": 0,
        "jobs": (),
    }


@pytest.mark.parametrize("scope", ("owner", "all_users"))
def test_usage_totals_can_exceed_sqlite_signed_integer_range(tmp_path, scope):
    store = CanvasStore(tmp_path / scope)
    with store._connection(immediate=True) as db:
        db.execute(
            """
            WITH digits(d) AS (
                VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)
            ), numbers(n) AS (
                SELECT a.d + 10*b.d + 100*c.d + 1000*d.d + 10000*e.d + 100000*f.d
                FROM digits a CROSS JOIN digits b CROSS JOIN digits c
                CROSS JOIN digits d CROSS JOIN digits e CROSS JOIN digits f
            )
            INSERT INTO canvas_jobs (
                id,user_id,service_id,operation,status,idempotency_key,request_hash,
                video_seconds,image_count,video_price_fen,image_price_fen,cost_fen,
                charged_at,created_at,updated_at
            )
            SELECT
                printf('maximum-video-%06d', n),'user-a','video','video.generate','succeeded',
                printf('maximum-key-%06d', n),'hash',86400,0,1000000000,0,86400000000000,
                'charged','created','updated'
            FROM numbers WHERE n < 106752
            """
        )

    usage = (
        store.usage_for_owner("user-a")
        if scope == "owner"
        else store.usage_for_all_users()[0]
    )

    assert usage["total_cost_fen"] == 9_223_372_800_000_000_000
    assert len(usage["jobs"]) == 106_752


def test_duplicate_successful_poll_callback_keeps_one_cost_snapshot(tmp_path):
    store = CanvasStore(tmp_path / "data")
    store.set_usage_rates(video_price_fen=25, image_price_fen=120)
    reserved = store.reserve_job(user_id="user-a", job_id="video", service_id="video", operation="video.generate", idempotency_key="duplicate-key", request_hash="d" * 64, video_seconds=5)
    store.mark_submitted("video", "up-video", "running", str(reserved.job["submission_token"]))
    claim = store.claim_pollable_job()
    assert claim is not None
    token = str(claim["submission_token"])
    store.record_polled_job("video", token=token, status="succeeded", result_id="result")
    store.set_usage_rates(video_price_fen=99, image_price_fen=999)
    store.record_polled_job("video", token=token, status="succeeded", result_id="result")
    usage = store.usage_for_owner("user-a")
    assert usage["total_cost_fen"] == 125
    assert len(usage["jobs"]) == 1
    assert usage["jobs"][0]["video_price_fen"] == 25
