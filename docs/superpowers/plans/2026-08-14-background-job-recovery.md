# Background Job Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make submitted image and video jobs finish after the browser closes or the single FastAPI process restarts, including managed routes, ordered multi-results, one-time cost capture, and safe errors.

**Architecture:** SQLite remains the durable source of truth and leases one pollable job at a time. A transport-independent `JobPollingService` resolves direct or immutable-snapshot managed adapters, while `JobWorker` only schedules and renews leases. HTTP job reads become read-only consumers of persisted state.

**Tech Stack:** Python 3.12, FastAPI, asyncio, SQLite, httpx adapters, React/TypeScript, Vitest, pytest.

## Global Constraints

- Target a company-internal single instance; do not add Redis Streams, Celery, or another queue.
- Never automatically submit or poll `submission_unknown` without a known upstream task ID.
- Managed polling uses the immutable route snapshot and exact saved key fingerprint; never rotate Key.
- Persist 1–15 ordered result IDs atomically and capture cost at most once.
- Worker identity comes from the server-owned job row, never the browser.
- `.local-real-media-data/`, credentials, provider payloads, and generated files never enter Git.
- Preserve unrelated dirty work and stage exact paths only.
- Before Task 1, if `.venv/bin/python` is absent, create it with `python3.12 -m venv .venv` and install the pinned environment with `.venv/bin/pip install -r requirements.lock`.

---

### Task 1: Durable Poll Lease and Ordered Results

**Files:**
- Modify: `.gitignore`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `tests/server/test_task_store.py`

**Interfaces:**
- Consumes: `CanvasStore.reserve_job(...)`, `mark_submitted(...)`, `canvas_jobs.result_ids_json`, `_capture_usage_snapshot(...)`.
- Produces: `claim_pollable_job(*, lease_seconds)`, `renew_job_lease(...)`, `release_job_lease(...)`, `record_polled_job(..., result_ids, retry_after_seconds)`.

- [ ] **Step 1: Write failing store tests**

Add tests proving direct and managed `queued/running` jobs with upstream IDs are claimable, while `submission_unknown`, terminal, missing-upstream, and leased rows are not. Add a multi-result success assertion:

```python
claim = store.claim_pollable_job(lease_seconds=30)
updated = store.record_polled_job(
    str(claim["id"]), token=str(claim["submission_token"]),
    status="succeeded", result_ids=("result_1", "result_2"),
)
assert json.loads(str(updated["result_ids_json"])) == ["result_1", "result_2"]
assert updated["result_id"] == "result_1"
```

Also reject empty, duplicate, unsafe, and more than fifteen result IDs without changing the row; repeat success and assert one cost snapshot.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_task_store.py
```

Expected: managed jobs are excluded and `result_ids` is unsupported.

- [ ] **Step 3: Implement the minimal store contract**

Remove the direct-only restriction, retain `queued/running` plus non-null upstream guards, validate lease values, and update using job ID plus token. Validate IDs with `_RESULT_ID`, encode the ordered tuple once, and update first/full results in the same transaction. Capture usage only after a successful terminal CAS. Add `/.local-real-media-data/` to `.gitignore`.

- [ ] **Step 4: Run GREEN**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_task_store.py tests/contracts/test_generation_flow.py
git status --short --ignored .local-real-media-data
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore server/ai_creation_canvas/storage/sqlite.py tests/server/test_task_store.py
git commit -m "feat: lease background generation polls"
```

### Task 2: Shared Managed Adapter Resolution

**Files:**
- Create: `server/ai_creation_canvas/managed_jobs.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Create: `tests/server/test_managed_job_adapter.py`
- Modify: `tests/server/test_jobs_model_routes.py`

**Interfaces:**
- Consumes: immutable `route_snapshot_json`, `key_fingerprint`, `ManagedRoutingRuntime`, `RouteCandidate`, `RequestContext`.
- Produces: `validated_job_route(item) -> ModelRouteDefinition` and `managed_job_adapter(runtime, context, item)`.

- [ ] **Step 1: Write failing resolver tests**

Cover legacy-v1, digest-era, and v2 stored snapshots; exact fingerprint selection; inconsistent service/model/route/operation/revision/digest rejection; missing original Key; proof that another compatible Key is never substituted; and fail-closed rejection when the current Provider, logical model, or route is disabled, archived, purged, or no longer matches the trusted preset.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_managed_job_adapter.py tests/server/test_jobs_model_routes.py
```

Expected: shared module is absent and behavior is private to `api/jobs.py`.

- [ ] **Step 3: Extract the boundary**

Move route snapshot parsing, job/route consistency checks, and the managed credential context manager into `managed_jobs.py`. Pass runtime/context/item explicitly; do not import FastAPI `Request`. Keep accepted historical shapes unchanged and fail closed on future/malformed shapes. Before acquiring a credential, re-read the current Provider, logical model, and route through the governed store/runtime, require enabled and non-archived lifecycle state, and require the route to match the trusted preset without changing the immutable snapshot. Update `api/jobs.py` to call the shared functions.

- [ ] **Step 4: Run GREEN**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_managed_job_adapter.py tests/server/test_jobs_model_routes.py tests/server/test_submission_unknown.py tests/contracts/test_generation_flow.py
```

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/managed_jobs.py server/ai_creation_canvas/api/jobs.py tests/server/test_managed_job_adapter.py tests/server/test_jobs_model_routes.py
git commit -m "refactor: share managed job resolution"
```

### Task 3: Polling Service and Worker Semantics

**Files:**
- Create: `server/ai_creation_canvas/job_polling.py`
- Modify: `server/ai_creation_canvas/job_worker.py`
- Modify: `server/ai_creation_canvas/adapters/ark.py`
- Modify: `server/ai_creation_canvas/adapters/demo.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `tests/server/test_job_worker.py`
- Modify: `tests/contracts/test_ark_adapter.py`
- Modify: `tests/contracts/test_generation_flow.py`
- Modify: `tests/server/test_jobs_model_routes.py`
- Modify: `tests/integration/test_core_flows.py`
- Modify: `tests/integration/test_demo_generation.py`
- Modify: `tests/integration/test_model_centric_routing.py`
- Modify: `tests/integration/test_slice1_product.py`

**Interfaces:**
- Consumes: Task 1 lease API, Task 2 managed adapter resolver, direct `AdapterRegistry`, adapter `JobState.results`.
- Produces: `JobPollingService.poll_claim(item, token)` and lifecycle-managed `JobWorker`.

- [ ] **Step 1: Write failing worker tests**

Cover ordered two-result completion; managed polling through the saved route/fingerprint; missing credential delaying without key rotation; retryable `PortalUpstreamError` releasing the lease; non-retryable error terminating safely; invalid/empty/duplicate/over-limit success failing terminally; `submission_unknown` never claimed; stale token never acknowledging Ark pending; and successful CAS acknowledging once.

Also cover direct and managed queued jobs through `GET /api/v1/jobs/{job_id}` and assert the endpoint returns stored state without calling either provider adapter. Preserve the local stale `submitting/in_flight` to `submission_unknown` transition. Mark the built-in Demo generation adapter as background-pollable and migrate integration fixtures that represent recoverable providers to declare the same capability; integration flows must explicitly advance `app.state.job_worker.run_once()` before reading completion.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_job_worker.py tests/contracts/test_ark_adapter.py
```

Expected: current worker stores one result, cannot resolve managed jobs, and treats every exception as transient.

- [ ] **Step 3: Implement `JobPollingService`**

Create a server-owned `RequestContext` from `user_id`. Resolve direct adapters from the registry and managed adapters from Task 2. Validate every `state.results` ID and call `record_polled_job` with the full tuple. Persist provider terminal failure; classify known retryable coordination/network errors as delayed retries; classify non-retryable provider errors and invalid results as terminal. Never log exception strings.

- [ ] **Step 4: Reduce `JobWorker` to scheduling**

Inject `JobPollingService`. Keep start/stop idempotence, event-loop ownership protection, heartbeat renewal, cancellation release, and loop-level isolation. Construct service/worker once in `create_app`. Call Ark `acknowledge_poll_result` only after the current token successfully persisted matching ordered results. Remove provider polling from the GET path so it only performs the safe local in-flight expiry check and returns persisted state; cancel and result endpoints remain unchanged.

- [ ] **Step 5: Run GREEN**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_job_worker.py tests/contracts/test_ark_adapter.py tests/contracts/test_generation_flow.py tests/server/test_jobs_model_routes.py tests/server/test_submission_unknown.py
```

- [ ] **Step 6: Commit**

```bash
git add server/ai_creation_canvas/job_polling.py server/ai_creation_canvas/job_worker.py server/ai_creation_canvas/adapters/ark.py server/ai_creation_canvas/adapters/demo.py server/ai_creation_canvas/app.py server/ai_creation_canvas/api/jobs.py tests/server/test_job_worker.py tests/contracts/test_ark_adapter.py tests/contracts/test_generation_flow.py tests/server/test_jobs_model_routes.py tests/integration/test_core_flows.py tests/integration/test_demo_generation.py tests/integration/test_model_centric_routing.py tests/integration/test_slice1_product.py
git commit -m "feat: recover generation jobs in background"
```

### Task 4: Frontend Long-Running Job Waiting

**Files:**
- Modify: `web/src/api/jobs.ts`
- Modify: `web/src/test/jobs.test.ts`

**Interfaces:**
- Consumes: persisted state advanced by `JobWorker`.
- Produces: abortable exponential-backoff `waitForJob(...)` consuming the read-only job endpoint completed in Task 3.

- [ ] **Step 1: Verify frontend behavior with tests first**

Tests must prove request time counts toward an explicit deadline, AbortSignal reaches fetch and sleep, no default two-minute timeout exists, invalid intervals reject, and delays back off from 1 to at most 10 seconds.

```bash
npm test --prefix web -- --run src/test/jobs.test.ts
```

- [ ] **Step 2: Implement minimal frontend waiting and run GREEN**

Use `fetchJob(id, signal)`, monotonic `now`, abortable sleep, bounded exponential backoff, and no provider-specific frontend state.

```bash
npm test --prefix web -- --run src/test/jobs.test.ts
npm run typecheck --prefix web
```

- [ ] **Step 3: Commit**

```bash
git add web/src/api/jobs.ts web/src/test/jobs.test.ts
git commit -m "feat: decouple completion from browser polling"
```

### Task 5: Integration, Documentation, and Release Gates

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/operations.md`
- Modify: `docs/verification.md`
- Create: `tests/integration/test_background_job_recovery.py`

**Interfaces:**
- Consumes: completed store, resolver, polling service, worker, and read-only API.
- Produces: deployable single-instance recovery contract and operator guidance.

- [ ] **Step 1: Write the restart integration test**

Use real FastAPI + SQLite with fake direct and managed transports. Submit jobs as two users, stop the first app before completion, advance the fake provider, create a second app on the same data directory, and assert both jobs finish; two ordered results support GET/HEAD/Range for owner only; other users remain hidden; one usage/cost row exists; route snapshot is unchanged; and `submission_unknown` is untouched.

- [ ] **Step 2: Run integration RED, add only missing wiring, then GREEN**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_background_job_recovery.py
```

- [ ] **Step 3: Update operator documentation**

Document single-instance SQLite recovery, known-upstream restart behavior, no unknown replay, and original managed credential requirement. Keep the approved project goals in `AGENTS.md`, fix whitespace, and exclude local paths/secrets.

- [ ] **Step 4: Run complete verification**

```bash
PATH=.venv/bin:$PATH PYTHONPATH=.:server pytest -q
npm test --prefix web -- --run
npm run typecheck --prefix web
npm run build --prefix web
npm run test:browser --prefix web
npm audit --prefix web --omit=dev --audit-level=high
scripts/security-scan.sh
git diff --check
git status --short
```

Expected: all checks pass and `.local-real-media-data/` is absent from status.

- [ ] **Step 5: Commit integration and docs**

```bash
git add AGENTS.md docs/operations.md docs/verification.md tests/integration/test_background_job_recovery.py
git commit -m "docs: verify background job recovery"
```

- [ ] **Step 6: Review before publication**

```bash
git log --oneline --decorate -6
git status --short
git diff origin/main...HEAD --stat
```

Do not push until the user has exercised the local application and explicitly approves publication.
