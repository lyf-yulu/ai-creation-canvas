# Generation Cost Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let administrators set global image/video prices and let every user see immutable, per-user generation-cost statistics.

**Architecture:** Persist one global rate row and immutable per-job snapshots in the existing SQLite store. Submission derives only non-sensitive media quantities, while every successful completion path captures current rates atomically. FastAPI exposes role-filtered projections to a React Statistics page.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, pytest, React 19, TypeScript, Vitest, Testing Library.

## Global Constraints

- All quantities and prices are non-negative integer RMB fen; display yuan only at the UI boundary.
- A job is charged once only when it first reaches `succeeded`; failures, pending jobs and legacy rows have no cost.
- Rate updates affect only later completions; historic snapshots never change.
- Do not persist prompts, raw parameters, secrets, upstream URLs or raw provider responses in statistics.
- Ordinary users may read only their own usage; admin resources retain the existing hidden `404`, Origin and CSRF protections.
- Preserve unrelated workspace changes and use temporary test data only.

## File Structure

- `server/ai_creation_canvas/storage/sqlite.py` — migration, rate storage, quantities, frozen snapshots and aggregate queries.
- `server/ai_creation_canvas/api/jobs.py` — safe quantity derivation at job creation.
- `server/ai_creation_canvas/api/usage.py` — authenticated owner usage endpoint.
- `server/ai_creation_canvas/api/admin.py` — admin rates and all-user usage endpoints.
- `server/ai_creation_canvas/app.py` — registers the usage router.
- `tests/server/test_task_store.py`, `tests/server/test_activity_api.py`, `tests/server/test_admin_api.py`, `tests/server/test_model_assignments.py` — persistence, API, auth and submission coverage.
- `web/src/api/usage.ts`, `web/src/pages/usage/index.tsx`, `web/src/components/layout/product-shell.tsx`, `web/src/router.tsx` — statistics client/page/navigation.
- `web/src/test/usage-page.test.tsx`, `web/src/test/admin-pages.test.tsx` — role-aware UI coverage.

---

### Task 1: Store rates and frozen per-job costs

**Files:**

- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `tests/server/test_task_store.py`

**Interfaces:**

- Produces `usage_rates() -> dict[str, int]` and `set_usage_rates(*, video_price_fen: int, image_price_fen: int) -> dict[str, int]`.
- Extends `reserve_job(..., video_seconds: int = 0, image_count: int = 0) -> Reservation`.
- Produces `usage_for_owner(user_id: str) -> dict[str, object]` and `usage_for_all_users() -> tuple[dict[str, object], ...]`.
- Changes `_update` and `record_polled_job` to snapshot rates transactionally on success.

- [ ] **Step 1: Write failing persistence tests**

Append these tests to `tests/server/test_task_store.py`:

```python
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
```

Also assert that reopening a legacy database yields zero rates and no charged job rows.

- [ ] **Step 2: Run the tests to verify RED**

Run: `pytest tests/server/test_task_store.py -q`

Expected: FAIL because the new API and reservation arguments do not exist.

- [ ] **Step 3: Implement the smallest persistence layer**

In `_migrate_schema`, create and seed this singleton table:

```python
db.execute("""CREATE TABLE IF NOT EXISTS canvas_usage_rates (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    video_price_fen INTEGER NOT NULL CHECK(video_price_fen>=0),
    image_price_fen INTEGER NOT NULL CHECK(image_price_fen>=0),
    updated_at TEXT NOT NULL
)""")
db.execute("INSERT OR IGNORE INTO canvas_usage_rates VALUES(1,0,0,?)", (_now(),))
```

Add additive `canvas_jobs` columns: `video_seconds INTEGER NOT NULL DEFAULT 0`, `image_count INTEGER NOT NULL DEFAULT 0`, `video_price_fen INTEGER`, `image_price_fen INTEGER`, `cost_fen INTEGER`, `charged_at TEXT`. Add matching defaults to the legacy-table rebuild projection. Reject booleans, non-integers, negatives, video quantities over `86400`, image quantities over `100`, and prices over `1_000_000_000`.

Add `_capture_usage_snapshot(db, job_id, now)`, called after persisted status becomes `succeeded` in both completion methods:

```python
job = db.execute("SELECT video_seconds,image_count,charged_at FROM canvas_jobs WHERE id=?", (job_id,)).fetchone()
if job is None or job["charged_at"] is not None:
    return
rates = db.execute("SELECT video_price_fen,image_price_fen FROM canvas_usage_rates WHERE singleton=1").fetchone()
total = int(job["video_seconds"]) * int(rates["video_price_fen"]) + int(job["image_count"]) * int(rates["image_price_fen"])
db.execute("UPDATE canvas_jobs SET video_price_fen=?,image_price_fen=?,cost_fen=?,charged_at=? WHERE id=? AND charged_at IS NULL", (rates["video_price_fen"], rates["image_price_fen"], total, now, job_id))
```

Aggregate only `charged_at IS NOT NULL`, returning safe job rows with operation, status, quantities, snapshots, cost and charged time; never select request hashes, upstream IDs or result IDs.

- [ ] **Step 4: Run the persistence suite to verify GREEN**

Run: `pytest tests/server/test_task_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit this slice**

```bash
git add server/ai_creation_canvas/storage/sqlite.py tests/server/test_task_store.py
git commit -m "feat: store immutable generation cost snapshots"
```

### Task 2: Wire safe measurements and protected APIs

**Files:**

- Modify: `server/ai_creation_canvas/api/jobs.py`
- Create: `server/ai_creation_canvas/api/usage.py`
- Modify: `server/ai_creation_canvas/api/admin.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `tests/server/test_activity_api.py`
- Modify: `tests/server/test_admin_api.py`
- Modify: `tests/server/test_model_assignments.py`

**Interfaces:**

- Produces `GET /api/v1/usage` with `summary` and current-owner charged `jobs`.
- Produces admin-only `GET /api/v1/admin/usage`, `GET /api/v1/admin/usage/rates`, `PUT /api/v1/admin/usage/rates`.
- Adds `_billing_quantities(operation, params, parameter_schema) -> tuple[int, int]` in `jobs.py`.

- [ ] **Step 1: Write failing API and quantity tests**

Add a test to `tests/server/test_activity_api.py` that creates charged jobs for `user-a` and `user-b`, calls `/api/v1/usage` as `user-a`, and asserts only one job and that `request_hash` and `user-b` are absent. Add to `tests/server/test_admin_api.py`:

```python
def test_only_admin_can_read_and_change_usage_rates(tmp_path):
    app, accounts, admin, user, admin_headers, user_headers = local_clients(tmp_path)
    del app, accounts
    assert user.get("/api/v1/admin/usage").status_code == 404
    assert user.put("/api/v1/admin/usage/rates", headers=user_headers, json={"video_price_fen": 1, "image_price_fen": 2}).status_code == 404
    response = admin.put("/api/v1/admin/usage/rates", headers=admin_headers, json={"video_price_fen": 25, "image_price_fen": 120})
    assert response.status_code == 200
    assert response.json() == {"video_price_fen": 25, "image_price_fen": 120}
```

In `tests/server/test_model_assignments.py`, register a video model whose schema permits `duration=5`, post it, and assert the reservation has `video_seconds == 5`. Repeat with no duration and assert zero quantities.

- [ ] **Step 2: Run the API tests to verify RED**

Run: `pytest tests/server/test_activity_api.py tests/server/test_admin_api.py tests/server/test_model_assignments.py -q`

Expected: FAIL because endpoints and billing quantities do not exist.

- [ ] **Step 3: Implement API behavior**

Create `api/usage.py`:

```python
router = APIRouter(prefix="/api/v1/usage")

@router.get("")
async def usage(request: Request) -> dict[str, object]:
    return request.app.state.canvas_store.usage_for_owner(context_for(request).user.user_id)
```

Add strict `UsageRates(BaseModel)` to `api/admin.py` with `ConfigDict(extra="forbid", strict=True)` and two `Field(ge=0, le=1_000_000_000)` integers. Reuse `_require_admin` for all three new admin resources and return store projections. Register `usage_router` in `create_app`.

In `create_job`, derive quantities after resolving the model and before `reserve_job`. For videos accept only a non-boolean integer `params["duration"]` within declared integer `minimum`/ `maximum`; otherwise use zero. Return `(0, 1)` for image operations. Pass both quantities to `reserve_job` without changing existing response bodies.

- [ ] **Step 4: Run API regression tests to verify GREEN**

Run: `pytest tests/server/test_activity_api.py tests/server/test_admin_api.py tests/server/test_model_assignments.py tests/contracts/test_generation_flow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit this slice**

```bash
git add server/ai_creation_canvas/api/jobs.py server/ai_creation_canvas/api/usage.py server/ai_creation_canvas/api/admin.py server/ai_creation_canvas/app.py tests/server/test_activity_api.py tests/server/test_admin_api.py tests/server/test_model_assignments.py
git commit -m "feat: expose protected generation usage APIs"
```

### Task 3: Build the Statistics page

**Files:**

- Create: `web/src/api/usage.ts`
- Create: `web/src/pages/usage/index.tsx`
- Modify: `web/src/components/layout/product-shell.tsx`
- Modify: `web/src/router.tsx`
- Create: `web/src/test/usage-page.test.tsx`
- Modify: `web/src/test/admin-pages.test.tsx`

**Interfaces:**

- Produces `fetchUsage`, `fetchAdminUsage`, `fetchUsageRates`, `updateUsageRates(videoPriceFen, imagePriceFen)`.
- Produces `UsagePage`, loading own usage for every role and all-user/rates data only for admins.
- Adds authenticated `/usage` and a “统计” link visible to every signed-in user.

- [ ] **Step 1: Write failing UI tests**

Create `web/src/test/usage-page.test.tsx` with a normal-user mock response containing `total_cost_fen: "245"`, one job, and assertions for `¥2.45` and absence of “保存价格”. Add an admin test that changes `0.25` and `1.20`, clicks “保存价格”, and asserts:

```ts
expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/admin/usage/rates",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ video_price_fen: 25, image_price_fen: 120 }) }),
);
```

Extend `web/src/test/admin-pages.test.tsx` to assert ordinary users see “统计” while only admins see the rate-save button.

- [ ] **Step 2: Run UI tests to verify RED**

Run: `npm --prefix web test -- --run web/src/test/usage-page.test.tsx web/src/test/admin-pages.test.tsx`

Expected: FAIL because the client, route, page and navigation do not exist.

- [ ] **Step 3: Implement the page and navigation**

Use `apiFetch` in `web/src/api/usage.ts`; expose only safe summary and charged-job types. Keep usage money fields as decimal strings and format them with `BigInt` so values beyond JavaScript's safe-integer range retain exact fen. The page renders cards for successful tasks, images, video seconds and total cost, followed by charged-job rows or an empty state.

For administrators, add decimal-yuan inputs labeled “每秒视频价格（元）” and “每张图片价格（元）”, validate with `/^\\d+(\\.\\d{1,2})?$/`, convert to integer fen, and show an alert without a request for invalid input. Loading or saving failure shows an alert; a save failure preserves displayed prior rates. Add `BarChart3` “统计” to shared navigation and the `/usage` child route.

- [ ] **Step 4: Run UI verification to verify GREEN**

Run: `npm --prefix web test -- --run web/src/test/usage-page.test.tsx web/src/test/admin-pages.test.tsx && npm --prefix web run typecheck`

Expected: PASS.

- [ ] **Step 5: Commit this slice**

```bash
git add web/src/api/usage.ts web/src/pages/usage/index.tsx web/src/components/layout/product-shell.tsx web/src/router.tsx web/src/test/usage-page.test.tsx web/src/test/admin-pages.test.tsx
git commit -m "feat: add generation cost statistics page"
```

### Task 4: Verify the isolated end-to-end flow

**Files:**

- Modify: `tests/integration/test_slice1_product.py`

**Interfaces:**

- Consumes Tasks 1–3 and verifies the approved two-user price-freeze path.

- [ ] **Step 1: Write the integration test**

Create a local-auth flow that sets image/video rates to `120`/`25`, completes one image and one five-second video for the ordinary user, changes rates to `999`/`99`, completes a second image, then asserts costs are `120`, `125`, `999`; the ordinary user receives `404` from `/api/v1/admin/usage`; and the admin aggregate contains the user’s total.

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/integration/test_slice1_product.py -q`

Expected: PASS after Tasks 1–3. Fix the first behavior mismatch before broadening scope.

- [ ] **Step 3: Run focused final verification**

Run: `pytest tests/server/test_task_store.py tests/server/test_activity_api.py tests/server/test_admin_api.py tests/server/test_model_assignments.py tests/integration/test_slice1_product.py -q && npm --prefix web test -- --run web/src/test/usage-page.test.tsx web/src/test/admin-pages.test.tsx && npm --prefix web run typecheck`

Expected: PASS.

- [ ] **Step 4: Commit integration coverage**

```bash
git add tests/integration/test_slice1_product.py
git commit -m "test: cover generation cost statistics flow"
```

## Plan Self-Review

- Task 1 covers global rates, price freezing, exactly-once capture and legacy exclusion.
- Task 2 covers safe quantity derivation, owner isolation, admin-only rates and API validation.
- Task 3 covers both roles’ UI and integer-fen handling.
- Task 4 covers the agreed two-user acceptance path.
- Token accounting, multi-result billing, refunds, exports, per-model prices, quotas and payments remain deliberately out of scope.
