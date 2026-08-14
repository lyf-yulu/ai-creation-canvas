# Portal Request-Scoped Polling Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve real Cookie-authenticated Portal image, video, and portrait workflows without storing browser cookies, while preventing ambiguous direct submissions from ever being replayed.

**Architecture:** Direct jobs persist an explicit completion mode: `background` for server-recoverable adapters and `request` for Cookie-scoped Portal adapters. Background jobs remain owned by the application worker; request-scoped jobs are leased and polled only by an authenticated owner GET using that request's Cookie. Both paths reuse the same provider-state validation, CAS result persistence, acknowledgement, ownership, and usage contracts. Ambiguous direct submissions become `submission_unknown` and remain non-replayable.

**Tech Stack:** Python 3.12, FastAPI, SQLite, httpx adapters, pytest, existing `JobPollingService` and `CanvasStore` lease/CAS primitives.

---

### Task 1: Make Direct Submission Ambiguity Non-Replayable

**Files:**
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `tests/contracts/test_generation_flow.py`
- Modify: `tests/server/test_submission_unknown.py`

- [ ] **Step 1: Write failing direct-submission tests**

Add tests whose adapter increments `submit_count` before raising each ambiguous outcome: `asyncio.CancelledError`, an unclassified exception, retryable transport failure, `InvalidUpstreamResult`, and `SubmissionError(SUBMISSION_UNKNOWN)`. Repeat the identical HTTP submission and assert:

```python
assert adapter.submit_count == 1
assert first.status_code in {502, 503}
assert repeated.status_code == 201
assert repeated.json()["status"] == "submission_unknown"
assert stored["status"] == "submission_unknown"
assert stored["submission_token"] is None
```

Keep a separate explicit business-rejection test proving a known provider 4xx becomes terminal `failed` and is not classified as unknown. Replace the old test that permits a second provider POST after a read timeout; provider-side idempotency is defense in depth, not the Canvas replay policy.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q \
  tests/contracts/test_generation_flow.py \
  tests/server/test_submission_unknown.py \
  -k "direct and (unknown or cancelled or timeout or malformed or rejected)"
```

Expected: ambiguous cases call `adapter.submit` twice or persist `failed`/reclaimable reservation rather than `submission_unknown`.

- [ ] **Step 3: Centralize direct outcome classification**

In `create_job`, keep capacity/coordination failures that occur before provider submission reclaimable. Once `adapter.submit*` has started:

```python
except asyncio.CancelledError:
    store.mark_submission_unknown(job_id, token)
    raise
except SubmissionError as error:
    if error.disposition is SubmissionDisposition.SUBMISSION_UNKNOWN:
        item = store.mark_submission_unknown(job_id, token, upstream_job_id=error.provider_task_id)
        return _response(item, request)
    # Handle only dispositions that prove no provider task exists as retryable/rejected.
except InvalidUpstreamResult:
    store.mark_submission_unknown(job_id, token)
    raise problem(request, "UPSTREAM_INVALID", "The generation service returned an invalid response.", status=502)
except Exception:
    store.mark_submission_unknown(job_id, token)
    raise problem(request, "UPSTREAM_UNAVAILABLE", "The generation service is unavailable.", status=502, retryable=True)
```

Do not log exception strings. If an exception carries a validated known upstream task ID, persist it; otherwise leave the ID absent and retain `submission_unknown`. A repeated idempotency key must return the stored row without provider I/O.

- [ ] **Step 4: Run GREEN and related contracts**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q \
  tests/contracts/test_generation_flow.py \
  tests/server/test_submission_unknown.py \
  tests/server/test_jobs_model_routes.py
```

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/api/jobs.py tests/contracts/test_generation_flow.py tests/server/test_submission_unknown.py
git commit -m "fix: prevent direct submission replay"
```

### Task 2: Add Cookie-Scoped Portal Completion Mode

**Files:**
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/domain/models.py`
- Modify: `server/ai_creation_canvas/adapters/portal/catalog.py`
- Modify: `server/ai_creation_canvas/adapters/portal/portrait.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/ai_creation_canvas/job_polling.py`
- Modify: `tests/server/test_task_store.py`
- Modify: `tests/server/test_jobs_model_routes.py`
- Modify: `tests/integration/test_core_flows.py`
- Modify: `tests/server/test_portrait_mapping.py`

- [ ] **Step 1: Write failing store and API tests**

Cover these exact contracts:

```python
# New request-scoped jobs are not worker-claimable.
job = reserve_and_submit(completion_mode="request")
assert store.claim_pollable_job() is None

# Only the owner can claim an exact request-scoped job.
claim = store.claim_request_scoped_job(job["id"], user_id="owner", lease_seconds=30)
assert claim is not None
assert store.claim_request_scoped_job(job["id"], user_id="other", lease_seconds=30) is None

# Portal submit remains async and GET drives one cookie-authenticated poll.
created = owner.post("/api/v1/jobs", ..., headers={"Cookie": "portal=owner"})
assert created.status_code == 201
assert created.json()["status"] == "queued"
assert worker.run_once() is False
completed = owner.get(f"/api/v1/jobs/{job_id}", headers={"Cookie": "portal=owner"})
assert completed.json()["status"] == "succeeded"
assert portal.poll_cookies == ["portal=owner"]

# Cross-user GET is hidden before provider I/O.
assert other.get(f"/api/v1/jobs/{job_id}").status_code == 404
assert portal.poll_count == 1
```

Also cover temporary Portal poll failure releasing the request lease, terminal failure, invalid result, concurrent owner GET (one poll), missing Cookie returning `AUTH_REQUIRED` without changing the job, and app restart followed by a fresh authenticated GET. Verify Cookie values never appear in SQLite rows, logs, or snapshots.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q \
  tests/server/test_task_store.py \
  tests/server/test_jobs_model_routes.py \
  tests/integration/test_core_flows.py \
  tests/server/test_portrait_mapping.py
```

Expected: production Portal adapters are rejected by the server-completion gate, no completion mode exists, and GET does not call `poll_with_cookie`.

- [ ] **Step 3: Persist a bounded completion mode**

Add an additive SQLite column with a strict application boundary:

```python
CompletionMode = Literal["background", "request"]

def reserve_job(..., completion_mode: CompletionMode = "background") -> Reservation:
    ...

def claim_request_scoped_job(job_id: str, *, user_id: str, lease_seconds: float) -> dict[str, object] | None:
    # BEGIN IMMEDIATE; exact id+owner+queued/running+request mode+known upstream;
    # require an expired/empty lease; set a fresh token and lease_until atomically.
```

`claim_pollable_job` must include `completion_mode='background'`. New submissions always write one of the two modes. The additive migration leaves historical rows unset rather than guessing. Before the worker starts, application startup reconciles nonterminal legacy rows from the trusted adapter registry: managed jobs and adapters with `supports_background_polling` become `background`; adapters with `requires_request_scoped_polling` become `request`; unresolved services remain unset and unclaimable. This reconciliation stores only the mode, never a Cookie. Validate all explicit mode values before SQL writes.

- [ ] **Step 4: Declare the trusted Portal capability**

Set only the code-owned Portal adapters:

```python
class PortalJobsAdapter:
    requires_portal_cookie = True
    requires_request_scoped_polling = True

class PortalPortraitAdapter:
    requires_portal_cookie = True
    requires_request_scoped_polling = True
```

Do not accept this capability from browser JSON, model metadata, remote config, or arbitrary third-party plugins. Do not mark these adapters as `supports_background_polling` or `supports_synchronous_submission`.

- [ ] **Step 5: Route submission and owner GET by mode**

At submission, derive mode only from the trusted adapter object:

```python
request_scoped = getattr(adapter, "requires_request_scoped_polling", False) is True
if not request_scoped and not _adapter_has_server_completion(adapter):
    raise MODEL_UNAVAILABLE
completion_mode = "request" if request_scoped else "background"
```

Store the mode in the reservation. In `GET /jobs/{id}`, first enforce job ownership. For a queued/running request-scoped row, require the current request Cookie, atomically claim that exact row, then call `poll_with_cookie(context, upstream_id, cookie)` through `JobPollingService`. The service must reuse `validated_provider_job_state`, `record_polled_job`, retry/error classification, and acknowledgement behavior. If another request owns the lease, return the current stored row without polling.

Never pass the Cookie to the worker, store, logs, exceptions, route snapshot, usage row, or response. Background rows must retain zero provider I/O in GET.

- [ ] **Step 6: Restore real Portal and portrait integrations**

Remove synchronous test-only subclasses that hid the production capability regression. Use the real `PortalJobsAdapter` and `PortalPortraitAdapter` with Cookie-enforcing fake transports. Prove:

- image/video Portal submission returns queued and owner GET reaches succeeded;
- portrait asset upload and video submission use the owner Cookie and owner GET completes;
- closing/recreating Canvas requires a new authenticated GET and no saved Cookie;
- worker never claims request-scoped rows;
- results and usage remain owner-isolated and atomic.

- [ ] **Step 7: Run GREEN and full server regression**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q \
  tests/server/test_task_store.py \
  tests/server/test_jobs_model_routes.py \
  tests/integration/test_core_flows.py \
  tests/server/test_portrait_mapping.py \
  tests/integration/test_background_job_recovery.py
PATH=.venv/bin:$PATH PYTHONPATH=.:server pytest -q
scripts/security-scan.sh
git diff --check
```

- [ ] **Step 8: Commit**

```bash
git add server/ai_creation_canvas/storage/sqlite.py server/ai_creation_canvas/domain/models.py server/ai_creation_canvas/adapters/portal/catalog.py server/ai_creation_canvas/adapters/portal/portrait.py server/ai_creation_canvas/api/jobs.py server/ai_creation_canvas/job_polling.py tests/server/test_task_store.py tests/server/test_jobs_model_routes.py tests/integration/test_core_flows.py tests/server/test_portrait_mapping.py
git commit -m "feat: restore request-scoped Portal polling"
```

### Task 3: Documentation, Final Verification, and Local Acceptance

**Files:**
- Modify: `docs/operations.md`
- Modify: `docs/verification.md`
- Modify: `docs/superpowers/plans/2026-08-14-background-job-recovery.md`

- [ ] **Step 1: Update documentation**

Document the split contract exactly: background-capable jobs survive browser closure; Cookie Portal jobs require an authenticated page to continue; no Cookie persistence; no worker polling for request-scoped rows; no ambiguous submission replay. Remove any unconditional claim that all providers recover offline.

- [ ] **Step 2: Run release gates**

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

- [ ] **Step 3: Commit documentation**

```bash
git add docs/operations.md docs/verification.md docs/superpowers/plans/2026-08-14-background-job-recovery.md
git commit -m "docs: document Portal polling compatibility"
```

- [ ] **Step 4: Start only the approved local acceptance instance**

Use the repository's existing local test configuration and an unused approved test port. Do not read or copy production Portal state, secrets, logs, or ports. Hand the URL and acceptance checklist to the user. Do not push until the user tests and explicitly approves.
