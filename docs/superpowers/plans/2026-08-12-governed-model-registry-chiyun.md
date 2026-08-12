# Governed Model Registry and Chiyun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an administrator-managed, operation-safe model registry and complete an offline Chiyun `gpt-image-2` image-edit path while preserving server-owned credentials, SQL idempotency, and a Redis-ready execution boundary.

**Architecture:** SQLite stores provider/model/access definitions and remains authoritative. Trusted adapter factories translate persisted definitions into existing `GenerationPort` implementations; React only sees filtered `ModelSpec` projections. An execution coordinator interface provides bounded local coordination and Redis-backed production leases without putting provider secrets or request bodies in Redis.

**Tech Stack:** Python 3.12, FastAPI, SQLite, redis-py 8.0.1, httpx, React, TypeScript, Vitest, Pytest, Chromium.

---

## File structure

- `server/ai_creation_canvas/model_registry.py`: immutable provider/model/operation contracts and safe projections.
- `server/ai_creation_canvas/storage/sqlite.py`: migrations and atomic CRUD for providers, model revisions, access and audit events.
- `server/ai_creation_canvas/adapters/chiyun.py`: Chiyun OpenAI-images adapter only.
- `server/ai_creation_canvas/adapters/factory.py`: allowlisted adapter-type construction; no dynamic imports.
- `server/ai_creation_canvas/coordination.py`: local and Redis execution permits/cache invalidation.
- `server/ai_creation_canvas/catalog.py`: merge static adapters with enabled persisted models and assignments.
- `server/ai_creation_canvas/api/admin.py`: administrator provider/model/access endpoints.
- `server/ai_creation_canvas/api/jobs.py`: operation snapshot and execution-permit integration.
- `server/ai_creation_canvas/config.py`, `server/ai_creation_canvas/__main__.py`, `server/ai_creation_canvas/app.py`: explicit credential/Redis wiring and production startup checks.
- `web/src/api/admin.ts`, `web/src/pages/admin/models.tsx`: provider/model administration and existing access assignment.

### Task 1: Persist governed provider/model/access contracts

**Files:**
- Create: `server/ai_creation_canvas/model_registry.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Test: `tests/server/test_model_registry.py`

- [x] **Step 1: Write RED domain and migration tests**

Add tests that create a `ProviderDefinition` and `ModelDefinition`, reject unknown adapter types, non-HTTPS origins, secrets in public projections, mismatched modality/operation/output, duplicate ports and unsafe parameter mappings. Add SQLite round-trip tests for create/update revision, enabled state, access grant/revoke and audit rows.

- [x] **Step 2: Run RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_model_registry.py`

Expected: collection fails because `model_registry` and store methods do not exist.

- [x] **Step 3: Implement immutable contracts**

Implement bounded dataclasses/enums for provider, model and per-operation contracts. Permit only adapter types from an injected allowlist; require HTTPS origin; store `credential_ref` but omit it, `base_url`, adapter mapping and provider model name from user projections. Convert each operation into an existing `ModelSpec` without guessing from names.

- [x] **Step 4: Implement additive SQLite migration and atomic CRUD**

Create `canvas_providers`, `canvas_models`, `canvas_model_access` and `canvas_admin_audit` with foreign keys, uniqueness constraints and monotonically increasing revisions. Store operation contracts as bounded canonical JSON. Make grant/revoke an immediate transaction; preserve current `canvas_user_models` only as a compatibility source during migration, then resolve new access from `canvas_model_access`.

- [x] **Step 5: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_model_registry.py tests/server/test_local_auth.py`

Expected: all pass.

Commit: `feat: persist governed model definitions`

### Task 2: Add a trusted Chiyun adapter and factory

**Files:**
- Create: `server/ai_creation_canvas/adapters/chiyun.py`
- Create: `server/ai_creation_canvas/adapters/factory.py`
- Modify: `server/ai_creation_canvas/domain/registry.py`
- Test: `tests/contracts/test_chiyun_adapter.py`
- Test: `tests/server/test_adapter_factory.py`

- [x] **Step 1: Write RED exact-request tests**

Use `httpx.MockTransport` to assert `gpt-image-2` submits exactly one `POST /v1/images/edits`, Bearer authentication, ordered `image[]` multipart parts, model, prompt, `n` and normalized OpenAI size. Assert no reference, video operation, too many/oversized/wrong-MIME assets, redirects, oversized responses, malformed JSON/base64, unsafe result URL and raw upstream error messages are rejected.

- [x] **Step 2: Run RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_chiyun_adapter.py tests/server/test_adapter_factory.py`

Expected: imports fail because the adapter and factory do not exist.

- [x] **Step 3: Implement Chiyun adapter**

Implement `GenerationPort` for `image.edit` only. Load owned images through the existing asset loader, enforce declared ordered input limits and bounded aggregate bytes, build multipart server-side, parse either bounded `b64_json` or HTTPS result URLs, download through a fixed-origin/size/MIME-safe downloader, and store opaque local results using the existing result contract.

- [x] **Step 4: Implement allowlisted factory**

Map only `chiyun_openai_images` to the Chiyun class. Resolve `credential_ref` through an injected server-only `CredentialResolver`; never import module names from database values. Cache adapters by `(provider_id, revision)` and close stale clients on replacement.

- [x] **Step 5: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_chiyun_adapter.py tests/server/test_adapter_factory.py`

Expected: all pass and exact request assertions prove ordered multipart construction.

Commit: `feat: add governed Chiyun image adapter`

### Task 3: Merge persisted models into the catalog and enforce operation snapshots

**Files:**
- Modify: `server/ai_creation_canvas/catalog.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/app.py`
- Test: `tests/server/test_dynamic_model_catalog.py`
- Test: `tests/server/test_jobs_dynamic_models.py`

- [x] **Step 1: Write RED catalog and authorization tests**

Cover admin visibility, user grant/revoke, disabled provider/model, unhealthy credential, image model absent from video operations, direct API operation mismatch, stale browser catalog after revoke, and duplicate model IDs across static/persisted sources. Assert public JSON excludes base URL, credential reference, provider model name and internal mappings.

- [x] **Step 2: Write RED immutable submission tests**

Assert a job persists model revision, provider ID, adapter type, operation and canonical parameter/input snapshot before execution. Updating a model afterward must not change the stored submission. Same user/key/same snapshot returns one job; same key/different snapshot returns 409.

- [x] **Step 3: Run RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_dynamic_model_catalog.py tests/server/test_jobs_dynamic_models.py`

Expected: persisted models are absent and job snapshot columns/methods do not exist.

- [x] **Step 4: Implement catalog merge and snapshot persistence**

Add a persisted-model catalog backed by `CanvasStore` and `AdapterFactory`, then wrap it with existing assignment filtering. Extend `canvas_jobs` additively with bounded canonical `submission_json`, `model_id`, `model_revision`, `provider_id` and `adapter_type`. Resolve permissions again on every POST before reservation; fetch result/poll adapters from the persisted snapshot service binding.

- [x] **Step 5: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_dynamic_model_catalog.py tests/server/test_jobs_dynamic_models.py tests/server/test_jobs_api.py`

Expected: all pass with one SQL reservation for concurrent equal keys.

Commit: `feat: resolve jobs from governed model snapshots`

### Task 4: Add Redis-backed execution coordination with safe local fallback

**Files:**
- Modify: `pyproject.toml`
- Create: `server/ai_creation_canvas/coordination.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/__main__.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Test: `tests/server/test_coordination.py`
- Test: `tests/server/test_config.py`

- [x] **Step 1: Write RED coordinator tests**

Define tests for bounded in-process permits and a Redis client fake covering atomic acquire/release, expiry, provider/user/global scopes, permission-cache invalidation and Redis payloads containing only opaque IDs. Verify release after cancellation and exceptions. Verify production Settings rejects missing Redis URL and startup ping failure; development explicitly uses local coordinator.

- [x] **Step 2: Run RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_coordination.py tests/server/test_config.py`

Expected: coordinator types and Redis settings do not exist.

- [x] **Step 3: Implement coordinator port and Redis implementation**

Pin `redis==8.0.1`. Implement `ExecutionCoordinator.acquire(job_id, user_id, provider_id, model_id)` as an async context manager. Local mode uses ordered bounded semaphores. Redis mode uses an atomic Lua script with TTL-backed global/provider/user counters and an owner token, plus compare-and-delete release. Keys contain hashed stable IDs; values never contain prompts, media, credentials or request bodies.

- [x] **Step 4: Integrate execution permits**

Acquire after SQL reservation and immediately before provider submission; always release in `finally`. SQL reservation remains the idempotency authority. Redis failure in production returns retryable service-unavailable without marking a second provider submission; development local coordinator remains bounded.

- [x] **Step 5: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_coordination.py tests/server/test_config.py tests/server/test_jobs_dynamic_models.py`

Expected: all pass, including cancellation and same-key concurrency probes.

Commit: `feat: coordinate generation execution with Redis`

### Task 5: Add administrator Provider/Model APIs and React management

**Files:**
- Modify: `server/ai_creation_canvas/api/admin.py`
- Modify: `web/src/api/admin.ts`
- Modify: `web/src/pages/admin/models.tsx`
- Test: `tests/server/test_admin_model_registry.py`
- Test: `web/src/test/admin-model-registry.test.tsx`

- [x] **Step 1: Write RED admin API tests**

Cover admin-only create/update/enable provider and model, adapter allowlist, safe public response, optimistic revision conflict, audit events, credential health without secret disclosure, model access grant/revoke and ordinary-user 404 behavior.

- [x] **Step 2: Write RED React tests**

Cover Provider form, operation template selection, `gpt-image-2` model creation, safe validation errors, enabled state, user assignment/revocation and confirmation that no Key/Base URL/internal mapping input is offered to ordinary users.

- [x] **Step 3: Run RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_admin_model_registry.py && npm test --prefix web -- --run src/test/admin-model-registry.test.tsx`

Expected: new endpoints and controls are absent.

- [x] **Step 4: Implement minimal admin workflow**

Add strict Pydantic request objects with `extra=forbid`. Use operation templates registered by the server rather than accepting arbitrary mappings. Add React sections for provider health, model creation/editing and existing user assignment. Keep credential configuration deployment-owned: administrator selects an existing credential reference name but never reads the secret value.

- [x] **Step 5: Run GREEN and commit**

Run the Step 3 command plus `npm run typecheck --prefix web`.

Expected: all pass.

Commit: `feat: administer providers models and access`

### Task 6: End-to-end offline acceptance and release verification

**Files:**
- Create: `tests/integration/test_chiyun_model_registry.py`
- Modify: `docs/operations.md`
- Modify: `docs/verification.md`
- Create: `docs/superpowers/reports/2026-08-12-chiyun-model-registry-report.md`

- [ ] **Step 1: Write and run an isolated integration test**

Build a real FastAPI app with SQLite, local coordinator, mock Chiyun transport and owned PNG assets. Exercise administrator Provider/model creation, user grant, `/models`, `image.edit`, exact multipart, result GET/HEAD/Range, revoke, rejected second submission, cross-user 404 and same-key concurrency.

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_chiyun_model_registry.py`

Expected: pass without network or credentials.

- [ ] **Step 2: Run full gates**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q`

Run: `npm run verify:release --prefix web`

Run: `npm audit --prefix web --omit=dev --audit-level=high`

Run: `scripts/security-scan.sh`

Run: `git diff --check`

Expected: all pass; production audit reports zero high vulnerabilities.

- [ ] **Step 3: Start an isolated user acceptance instance**

Use a fresh Git-ignored data directory and a new non-production port; do not touch 8997 or 9001. Configure a mock credential resolver and mock Chiyun transport. Verify administrator create/grant/revoke and ordinary user ordered-reference generation in Chromium, then leave the login page open for user acceptance.

- [ ] **Step 4: Document operational boundaries and commit**

Document production Redis requirement, credential references, adapter allowlist, concurrency defaults, health checks, rollback and the fact that the acceptance instance is offline. Record tests and deferred real paid call without storing credentials, prompts or provider payloads.

Commit: `docs: report governed Chiyun verification`
