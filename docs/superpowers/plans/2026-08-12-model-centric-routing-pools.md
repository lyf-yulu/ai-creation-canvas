# Model-Centric Routing and Credential Pools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current one-provider/one-model binding with logical models that can route through multiple official or third-party routes while rotating only among keys in an explicitly compatible credential pool.

**Architecture:** SQLite remains authoritative for logical models, routes, access, audit and idempotent job snapshots. A repository-external or Git-ignored YAML credential file is validated into an immutable in-memory snapshot; Redis atomically leases route/key capacity without storing secrets. FastAPI exposes only safe administrator summaries and logical model capabilities, while React edits logical models and routes rather than provider-specific user models.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, PyYAML, redis-py 8.0.1, httpx, React, TypeScript, Vitest, Pytest, Chromium.

## Global Constraints

- Users select only a logical model and its declared parameters; Provider, route, group, pool and key remain server-side.
- API keys never enter SQLite, browser APIs, Redis, logs, job submission JSON or audit payloads.
- A key can be selected only from the route's exact `(provider_id, group, family)` credential pool.
- Provider, route and adapter values come from server allowlists; no dynamic imports, scripts, custom headers or arbitrary parameter mapping code.
- SQLite is the idempotency authority; Redis stores only opaque hashed identifiers, counters, leases, circuit state and expirations.
- Ambiguous provider submission failures never trigger automatic cross-key or cross-route replay.
- Objects follow `enabled → archived → conditional delete`; historical references retain a non-executable audit stub.
- Production startup with enabled managed routes requires Redis and a valid credential-pool file.
- Existing ports `8997`, `9001` and `9002` and their data remain untouched; all acceptance uses a new ignored data directory and unused port.
- No real credential or paid provider request is used until offline acceptance is complete and the user separately approves a bounded paid test.

---

## File structure

- `server/ai_creation_canvas/credential_pools.py`: safe YAML loading, immutable pool/key snapshots and atomic reload.
- `server/ai_creation_canvas/model_routing.py`: logical-model, route and lifecycle contracts plus compatibility decisions.
- `server/ai_creation_canvas/storage/sqlite.py`: additive schema, migration, references, audit stubs and immutable route snapshots.
- `server/ai_creation_canvas/adapters/factory.py`: route-scoped allowlisted adapter construction with an injected credential lease.
- `server/ai_creation_canvas/routing.py`: compatible route selection and retry classification.
- `server/ai_creation_canvas/coordination.py`: local and Redis route/key lease acquisition.
- `server/ai_creation_canvas/catalog.py`: logical-model capability and access projections.
- `server/ai_creation_canvas/api/admin.py`: safe CRUD/lifecycle APIs for logical models, routes and summaries.
- `server/ai_creation_canvas/api/jobs.py`: logical-model resolution, route/key lease, immutable submission and unknown-state behavior.
- `server/ai_creation_canvas/config.py`, `server/ai_creation_canvas/__main__.py`, `server/ai_creation_canvas/app.py`: credential file and production wiring.
- `web/src/api/admin.ts`: typed administrator API client.
- `web/src/pages/admin/models.tsx`: logical-model list/editor, route editor and lifecycle actions.
- `web/src/components/admin/model-editor.tsx`: focused logical-model form.
- `web/src/components/admin/model-route-editor.tsx`: focused route and pool form.
- `web/src/components/admin/object-lifecycle-actions.tsx`: disable/archive/restore/delete confirmation UI.

---

### Task 1: Load immutable grouped credential pools

**Files:**
- Create: `server/ai_creation_canvas/credential_pools.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/__main__.py`
- Test: `tests/server/test_credential_pools.py`
- Test: `tests/server/test_config.py`

**Interfaces:**
- Produces: `CredentialKey(key_id: str, secret: str, max_concurrency: int)`.
- Produces: `CredentialPool(pool_id: str, provider_id: str, group: str, allowed_families: tuple[str, ...], keys: tuple[CredentialKey, ...], revision_digest: str)`.
- Produces: `CredentialPoolSnapshot.get(pool_id: str) -> CredentialPool | None` and `safe_summaries() -> tuple[dict[str, object], ...]`.
- Produces: `CredentialPoolLoader(path: Path).load() -> CredentialPoolSnapshot` and `reload() -> CredentialPoolSnapshot` with last-known-good atomic replacement.
- Consumes later: `Settings.credential_pools_path: Path | None` and `Settings.credential_pools_root: Path | None`.

- [x] **Step 1: Write RED parser and filesystem-boundary tests**

Add fixtures for one official pool, T8Star `gemini` and T8Star `cc`. Assert unique pool/key IDs, non-empty provider/group/family, 1–64 keys, per-key concurrency 1–32, duplicate/unknown YAML fields rejected, symlink rejected, file mode broader than `0600` rejected in production, and `safe_summaries()` contains counts but neither `api_key` nor key IDs.

```python
def test_t8_groups_are_distinct_and_safe(tmp_path: Path) -> None:
    path = write_pool_file(tmp_path, mode=0o600)
    snapshot = CredentialPoolLoader(path, production=True).load()
    assert snapshot.get("t8-gemini").allowed_families == ("nano-banana",)
    assert snapshot.get("t8-cc").allowed_families == ("claude",)
    encoded = json.dumps(snapshot.safe_summaries())
    assert "secret-gemini" not in encoded and "gemini-key-1" not in encoded
```

- [x] **Step 2: Run RED tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_credential_pools.py tests/server/test_config.py`

Expected: collection fails because `credential_pools` and credential-pool settings do not exist.

- [x] **Step 3: Implement strict immutable loading**

Use `yaml.safe_load`, Pydantic `extra="forbid"`, `Path.lstat`, `stat.S_ISREG`, symlink rejection, a bounded 1 MiB file, canonical SHA-256 revision digest and immutable dataclasses. Accept only this schema:

```yaml
version: 1
pools:
  t8-gemini:
    provider: t8star
    group: gemini
    allowed_families: [nano-banana]
    keys:
      - id: key-01
        api_key: deployment-secret
        max_concurrency: 2
```

Keep the secret only on `CredentialKey.secret`; define `__repr__` to omit it. `reload()` must parse a complete candidate before acquiring the snapshot lock and must retain the previous snapshot on failure.

- [x] **Step 4: Wire explicit CLI/settings validation**

Add `--credential-pools PATH`. Resolve it under an explicit root, reject traversal, and require it when production contains enabled managed routes. Development may omit it and receives an empty snapshot.

- [x] **Step 5: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_credential_pools.py tests/server/test_config.py tests/server/test_cli.py`

Expected: all tests pass without printing pool contents.

Commit:

```bash
git add server/ai_creation_canvas/credential_pools.py server/ai_creation_canvas/config.py server/ai_creation_canvas/__main__.py tests/server/test_credential_pools.py tests/server/test_config.py
git commit -m "feat: load grouped credential pools"
```

### Task 2: Persist logical models, routes and lifecycle state

**Files:**
- Create: `server/ai_creation_canvas/model_routing.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Test: `tests/server/test_model_routing.py`
- Test: `tests/server/test_model_routing_migration.py`

**Interfaces:**
- Produces: `LogicalModelDefinition(model_id, display_name, introduction, modality, operation_contracts, enabled, archived_at, revision)`.
- Produces: `ModelRouteDefinition(route_id, model_id, provider_id, provider_model_name, adapter_type, credential_pool_ref, family, operation_contracts, priority, max_concurrency, enabled, archived_at, revision)`.
- Produces: `RouteCompatibility(route_id: str, operation: ModelOperation, provider_id: str, pool_id: str, priority: int)`.
- Produces store methods `create/update/archive/restore/delete_logical_model`, equivalent route methods, `route_references(route_id)`, and `logical_model_references(model_id)`.
- Consumes: Task 1 pool `(provider_id, group, allowed_families)` for route validation, but does not store secrets.

- [x] **Step 1: Write RED domain invariants**

Test modality/operation isolation, bounded ports/schema/mappings, route/model contract compatibility, exact Provider/family/pool matching, strict revisions, archived objects disabled, and no route may silently omit an operation parameter it claims to support.

```python
def test_cc_pool_cannot_back_nano_banana_route() -> None:
    with pytest.raises(ValueError, match="family"):
        validate_route_pool(nano_banana_route(), pool(provider="t8star", group="cc", families=("claude",)))
```

- [x] **Step 2: Write RED additive migration and lifecycle tests**

Assert new tables `canvas_logical_models`, `canvas_model_routes` and lifecycle columns are additive. Migrate each current `canvas_models` record once into one logical model plus one route; preserve model ID and `canvas_model_access`; keep old tables. Test repeated startup is byte-stable and that an existing Chiyun model becomes an `image.edit` logical model.

Test conditional deletion:

```python
assert store.delete_model_route("unused-route", expected_revision=1).deleted is True
with pytest.raises(ObjectReferenced):
    store.delete_logical_model("used-model", expected_revision=1)
stub = store.purge_logical_model_runtime("used-model", expected_revision=1)
assert stub.enabled is False and stub.archived_at is not None
assert "credential_pool_ref" not in json.dumps(stub.audit_projection())
```

- [x] **Step 3: Run RED tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_model_routing.py tests/server/test_model_routing_migration.py`

Expected: imports and schema assertions fail.

- [x] **Step 4: Implement contracts and additive SQLite operations**

Use canonical bounded JSON and `BEGIN IMMEDIATE` for revision updates and lifecycle transitions. Store references using foreign keys where possible and explicit task/assignment checks where historical rows intentionally survive. A historical audit stub retains only IDs, display name, modality, revision and timestamps; it clears provider model name, Base URL linkage and pool reference.

- [x] **Step 5: Implement idempotent old-model migration**

Write a schema marker after successful migration. Convert current `credential_ref` to `credential_pool_ref` with the same identifier and mark the route unhealthy until Task 1 supplies that pool. Static Ark declarations remain read-only and are not duplicated here.

- [x] **Step 6: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_model_routing.py tests/server/test_model_routing_migration.py tests/server/test_model_registry.py tests/server/test_local_auth.py`

Commit:

```bash
git add server/ai_creation_canvas/model_routing.py server/ai_creation_canvas/storage/sqlite.py tests/server/test_model_routing.py tests/server/test_model_routing_migration.py
git commit -m "feat: persist logical models and routes"
```

### Task 3: Select only compatible routes and pool keys

**Files:**
- Create: `server/ai_creation_canvas/routing.py`
- Modify: `server/ai_creation_canvas/coordination.py`
- Test: `tests/server/test_model_route_selection.py`
- Test: `tests/server/test_route_key_coordination.py`

**Interfaces:**
- Produces: `RouteCandidate(route: ModelRouteDefinition, pool: CredentialPool)`.
- Produces: `RouteSelector.candidates(model, operation, params, inputs, routes, pools) -> tuple[RouteCandidate, ...]` sorted by priority then route ID.
- Produces: `CredentialLease(route_id: str, pool_id: str, key_id: str, secret: str, key_fingerprint: str, owner_token: str)` as an async context manager result.
- Produces: `ExecutionCoordinator.acquire_credential(job_id, user_id, candidate) -> AsyncContextManager[CredentialLease]` for local and Redis implementations.

- [ ] **Step 1: Write RED compatibility and ordering tests**

Create one Nano Banana official route, one T8Star `gemini` route and a T8Star `cc` route. Assert only the first two are candidates, parameters unsupported by one route remove only that route, disabled/archived/unhealthy routes are excluded, and stable priority ordering is deterministic.

- [ ] **Step 2: Write RED local and Redis key-lease tests**

Assert least-in-use selection within one pool, stable key-ID tie breaking, per-key/pool/route/provider/user/global limits, compare-and-delete release, TTL expiry and no secret/group/prompt in Redis keys or values. Add 20 concurrent acquisitions and prove `cc` is never observed for a Banana candidate.

```python
assert all(lease.pool_id in {"official", "t8-gemini"} for lease in leases)
assert "api-key" not in json.dumps(redis.recorded_commands)
```

- [ ] **Step 3: Run RED tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_model_route_selection.py tests/server/test_route_key_coordination.py`

Expected: selector and credential lease APIs are absent.

- [ ] **Step 4: Implement pure route filtering**

Revalidate route contracts against the logical model on every selection. Compare actual operation, named input counts/media and submitted parameter names/types/ranges. Return no candidate instead of weakening the model contract.

- [ ] **Step 5: Implement atomic key leasing**

Extend local coordination with bounded counters under one async lock. Extend Redis coordination with one Lua acquire script that checks global/provider/route/user/pool/key counters and selects the least-used compatible key; values use only HMAC-SHA256 opaque IDs and owner tokens. Return the secret by looking up the chosen key ID in the in-memory snapshot after Redis selection.

- [ ] **Step 6: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_model_route_selection.py tests/server/test_route_key_coordination.py tests/server/test_coordination.py`

Commit:

```bash
git add server/ai_creation_canvas/routing.py server/ai_creation_canvas/coordination.py tests/server/test_model_route_selection.py tests/server/test_route_key_coordination.py
git commit -m "feat: lease compatible model route keys"
```

### Task 4: Build route-scoped trusted adapters and retry decisions

**Files:**
- Modify: `server/ai_creation_canvas/adapters/factory.py`
- Modify: `server/ai_creation_canvas/adapters/chiyun.py`
- Modify: `server/ai_creation_canvas/adapters/ark.py`
- Create: `server/ai_creation_canvas/adapters/retry.py`
- Test: `tests/server/test_route_adapter_factory.py`
- Test: `tests/contracts/test_route_retry_contracts.py`

**Interfaces:**
- Produces: `RouteAdapterFactory.build(route: ModelRouteDefinition, lease: CredentialLease) -> GenerationPort`.
- Produces: `SubmissionDisposition` enum values `NOT_SUBMITTED`, `REJECTED`, `TEMPORARY_UNAVAILABLE`, `SUBMISSION_UNKNOWN`, `ACCEPTED`.
- Produces: `classify_submission_error(error: Exception, adapter_type: str) -> SubmissionDisposition`.
- Consumes: Task 3 `CredentialLease.secret`; adapter lifetime cannot outlive the lease/submission call.

- [ ] **Step 1: Write RED allowlist and exact-request tests**

Cover current verified Ark image/video templates and Chiyun/OpenAI Images edit template. Assert image routes cannot build video adapters, a route cannot override Base URL/header/mapping outside its template, and the lease secret appears only in the outbound Authorization value.

- [ ] **Step 2: Write RED retry classification tests**

For every enabled adapter template, classify DNS/connect-before-send, explicit 429, explicit 5xx with provider task ID, 401/403, invalid model/parameter, content moderation and read timeout after request bytes are sent. Assert only `NOT_SUBMITTED` and explicit `TEMPORARY_UNAVAILABLE` with provider confirmation can try another key; read timeout is `SUBMISSION_UNKNOWN`.

- [ ] **Step 3: Run RED tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_route_adapter_factory.py tests/contracts/test_route_retry_contracts.py`

- [ ] **Step 4: Refactor factory to consume route and lease**

Remove provider-wide adapter caching for managed routes because it pins a secret. Cache only immutable protocol metadata keyed by `(route_id, route_revision)`; construct or bind a short-lived authenticated client for each submission lease. Keep polling/results bound to the immutable route snapshot without retaining the API key where the provider result contract does not require it.

- [ ] **Step 5: Implement explicit retry dispositions**

Adapters raise a typed submission exception carrying disposition, retryable flag and a safe error code. Raw provider bodies remain inaccessible to API responses and logs.

- [ ] **Step 6: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_route_adapter_factory.py tests/contracts/test_route_retry_contracts.py tests/contracts/test_chiyun_adapter.py tests/contracts/test_ark_adapter.py`

Commit:

```bash
git add server/ai_creation_canvas/adapters/factory.py server/ai_creation_canvas/adapters/chiyun.py server/ai_creation_canvas/adapters/ark.py server/ai_creation_canvas/adapters/retry.py tests/server/test_route_adapter_factory.py tests/contracts/test_route_retry_contracts.py
git commit -m "feat: bind trusted adapters to model routes"
```

### Task 5: Submit jobs through immutable logical-model routes

**Files:**
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/catalog.py`
- Modify: `server/ai_creation_canvas/app.py`
- Test: `tests/server/test_jobs_model_routes.py`
- Test: `tests/server/test_submission_unknown.py`
- Test: `tests/server/test_logical_model_catalog.py`

**Interfaces:**
- Produces job snapshot fields `logical_model_id`, `logical_model_revision`, `route_id`, `route_revision`, `pool_revision_digest`, `key_fingerprint`, `submission_state`.
- Produces terminal/intermediate `submission_state` values `reserved`, `submitted`, `submission_unknown`, `rejected`.
- Consumes Tasks 1–4 snapshots, selector, credential lease and route adapter factory.

- [ ] **Step 1: Write RED catalog and authorization tests**

Assert users receive one Nano Banana logical model even when it has three routes; public JSON has no route/provider/group/pool/key fields. Revocation, archive, no healthy route and modality mismatch reject before credential acquisition.

- [ ] **Step 2: Write RED immutable routing and retry tests**

Cover official route busy → T8 `gemini` route selected, first key explicit 429 → second key in the same pool, `cc` never used, and a request-body timeout produces `submission_unknown` with no second provider request. Updating routes/pools later must not mutate stored snapshot fields.

- [ ] **Step 3: Write RED concurrent idempotency tests**

Use two ASGI clients and the same user/key/payload. Assert one SQL job, one selected route snapshot and at most one uncertain provider submission. Same key/different logical request remains 409.

- [ ] **Step 4: Run RED tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_jobs_model_routes.py tests/server/test_submission_unknown.py tests/server/test_logical_model_catalog.py`

- [ ] **Step 5: Implement catalog and submission orchestration**

Return logical models from the catalog. In `POST /jobs`, reserve the platform job first, choose compatible candidates, acquire one credential lease, persist the immutable routing snapshot before network I/O, then submit. Retry only when the typed disposition permits it. Map `submission_unknown` to a non-terminal visible state that polling/recovery can inspect but users cannot manually duplicate with the same idempotency key.

- [ ] **Step 6: Preserve poll/result ownership**

Resolve polling and result adapters from the immutable route snapshot, never from the current route list. Keep current user ownership checks, GET-only recovery, bounded result proxy and source-job/result-node deduplication.

- [ ] **Step 7: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_jobs_model_routes.py tests/server/test_submission_unknown.py tests/server/test_logical_model_catalog.py tests/server/test_jobs_api.py tests/server/test_results_api.py`

Commit:

```bash
git add server/ai_creation_canvas/api/jobs.py server/ai_creation_canvas/storage/sqlite.py server/ai_creation_canvas/catalog.py server/ai_creation_canvas/app.py tests/server/test_jobs_model_routes.py tests/server/test_submission_unknown.py tests/server/test_logical_model_catalog.py
git commit -m "feat: route jobs by logical model"
```

### Task 6: Expose safe administrator CRUD and lifecycle APIs

**Files:**
- Modify: `server/ai_creation_canvas/api/admin.py`
- Test: `tests/server/test_admin_logical_models.py`
- Test: `tests/server/test_admin_model_routes.py`
- Test: `tests/server/test_admin_model_lifecycle.py`

**Interfaces:**
- Produces: `/api/v1/admin/logical-models` CRUD and lifecycle endpoints.
- Produces: `/api/v1/admin/logical-models/{model_id}/routes` CRUD and lifecycle endpoints.
- Produces: `/api/v1/admin/credential-pools` safe summaries.
- Consumes: Tasks 1–5 store and compatibility APIs.

- [ ] **Step 1: Write RED safe projection and ordinary-user isolation tests**

Assert administrator responses include pool ID, provider, group, families, total/available/busy/circuit counts and revision digest, but no key IDs/secrets. Ordinary users receive 404 for every admin route, even with invalid request bodies.

- [ ] **Step 2: Write RED edit/version/lifecycle tests**

Cover create/edit, stale revision 409, disable, archive, restore, unused delete 204, referenced delete 409 with safe reference categories, historical purge, Provider-with-routes deletion rejection, and audit action sequence.

- [ ] **Step 3: Write RED contract compatibility tests**

Attempt to attach T8 `cc` to Nano Banana, attach video operation to an image model, map an unsupported parameter and use an unknown adapter. Each must return safe 400 before writing.

- [ ] **Step 4: Run RED tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_admin_logical_models.py tests/server/test_admin_model_routes.py tests/server/test_admin_model_lifecycle.py`

- [ ] **Step 5: Implement strict APIs**

Use Pydantic strict request types with `extra="forbid"`. Separate create/update bodies; update always requires `revision`. Lifecycle endpoints are explicit POST actions (`disable`, `archive`, `restore`) plus conditional DELETE; do not overload a free-form status field.

- [ ] **Step 6: Run GREEN and commit**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_admin_logical_models.py tests/server/test_admin_model_routes.py tests/server/test_admin_model_lifecycle.py tests/server/test_admin_api.py`

Commit:

```bash
git add server/ai_creation_canvas/api/admin.py tests/server/test_admin_logical_models.py tests/server/test_admin_model_routes.py tests/server/test_admin_model_lifecycle.py
git commit -m "feat: administer logical model routes"
```

### Task 7: Build the React logical-model and route editor

**Files:**
- Modify: `web/src/api/admin.ts`
- Modify: `web/src/pages/admin/models.tsx`
- Create: `web/src/components/admin/model-editor.tsx`
- Create: `web/src/components/admin/model-route-editor.tsx`
- Create: `web/src/components/admin/object-lifecycle-actions.tsx`
- Test: `web/src/test/admin-logical-models.test.tsx`
- Test: `web/src/test/admin-model-routes.test.tsx`
- Test: `web/src/test/admin-model-lifecycle.test.tsx`

**Interfaces:**
- Consumes Task 6 API types and endpoints.
- Produces a three-level UI: logical-model list, selected model editor/routes, safe pool summaries.
- Produces callbacks `onSaved(updated)`, `onArchived(updated)`, `onRestored(updated)`, `onDeleted(id)` in focused components.

- [ ] **Step 1: Write RED logical-model edit tests**

Render two logical models. Select one, edit display name/introduction, save with revision, and assert the list updates. Assert image/video/text templates are separated and ordinary model assignment uses only logical IDs.

- [ ] **Step 2: Write RED route/pool tests**

Add an official Nano Banana route and a T8 `gemini` route. Assert pool choices are filtered by provider/family, `cc` is absent for Banana, safe health counts render, and no input named API Key exists.

- [ ] **Step 3: Write RED lifecycle and conflict tests**

Assert disable, archive, show archived, restore, unused-delete confirmation, referenced-delete blocked explanation and 409 refresh prompt. Confirm archived objects disappear from the default list without losing selection state incorrectly.

- [ ] **Step 4: Run RED tests**

Run: `npm test --prefix web -- --run src/test/admin-logical-models.test.tsx src/test/admin-model-routes.test.tsx src/test/admin-model-lifecycle.test.tsx`

- [ ] **Step 5: Implement focused components**

Keep `models.tsx` as orchestration only. Forms use controlled inputs, operation-template select and revision-bearing API calls. Pool cards display status summaries and group labels, never keys. Destructive actions require object-name confirmation and render safe server reference categories.

- [ ] **Step 6: Preserve canvas capability behavior**

Use existing `/models` logical projection. Ensure image-only, video-only and edit-only logical models enable only their corresponding canvas entries; retain the `image.edit` fallback added in commit `3e8d586`.

- [ ] **Step 7: Run GREEN and commit**

Run: `npm test --prefix web -- --run src/test/admin-logical-models.test.tsx src/test/admin-model-routes.test.tsx src/test/admin-model-lifecycle.test.tsx src/test/canvas-generation-page.test.tsx`

Run: `npm run typecheck --prefix web`

Commit:

```bash
git add web/src/api/admin.ts web/src/pages/admin/models.tsx web/src/components/admin/model-editor.tsx web/src/components/admin/model-route-editor.tsx web/src/components/admin/object-lifecycle-actions.tsx web/src/test/admin-logical-models.test.tsx web/src/test/admin-model-routes.test.tsx web/src/test/admin-model-lifecycle.test.tsx
git commit -m "feat: edit logical models and routes"
```

### Task 8: Complete migration, offline acceptance and user handoff

**Files:**
- Create: `tests/integration/test_model_centric_routing.py`
- Create: `server/config/credential-pools.example.yaml`
- Modify: `docs/operations.md`
- Modify: `docs/verification.md`
- Modify: `scripts/security-scan.sh`
- Create: `docs/superpowers/reports/2026-08-12-model-centric-routing-report.md`
- Test: `web/src/test/browser/canvas-responsive.browser.test.tsx`

**Interfaces:**
- Consumes all prior tasks.
- Produces a reproducible offline acceptance instance and final evidence report.

- [ ] **Step 1: Write RED full API integration**

Use real FastAPI, SQLite, an in-memory Redis fake, three credential pools and mock official/T8 transports. Through admin APIs create one Nano Banana logical model with official and T8 `gemini` routes plus a rejected T8 `cc` route. Grant a user, upload ordered references, submit two concurrent equal idempotency requests, induce a safe 429 key rotation, poll, and verify result GET/HEAD/Range, route snapshot, revoke and cross-user isolation.

- [ ] **Step 2: Write RED Chromium administrator workflow**

Cover creating/editing a logical image model, adding two routes, seeing safe pool health, archiving/restoring, blocked referenced deletion, user assignment and creating the corresponding canvas node. At 415 px and 240 px, ensure editors remain usable without hiding action buttons or overflowing the page.

- [ ] **Step 3: Run RED integration tests**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_model_centric_routing.py`

Run: `npm run test:browser --prefix web`

- [ ] **Step 4: Add example config and operations documentation**

The example uses fake values only and documents file mode, external/ignored location, pool/group/family isolation, atomic reload, Redis requirement, `submission_unknown`, lifecycle semantics and rollback. Security scan must reject `api_key:` literals outside the example fixture and tests, browser Key fields, dynamic imports and non-admin Base URL surfaces.

- [ ] **Step 5: Run full release gates sequentially**

Do not overlap Python release tests with frontend verification because release tests run `npm ci` in copied worktrees.

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q
npm ci --prefix web
npm run verify:release --prefix web
npm audit --prefix web --omit=dev --audit-level=high
scripts/security-scan.sh
git diff --check
```

Expected: all pass; audit reports zero high vulnerabilities.

- [ ] **Step 6: Verify both release build paths**

Run a full `scripts/build-release.sh` into a new `/private/tmp` directory, then `--skip-web-build` into another new directory. Verify both manifests and Python-only entry points.

- [ ] **Step 7: Start a fresh offline user-acceptance instance**

Use the next available port discovered read-only and a new Git-ignored data directory. Seed fake official, T8 `gemini` and T8 `cc` pools; never reuse 8997/9001/9002 state. Perform administrator and ordinary-user browser checks, leave the login or selected model page open, and provide one administrator plus one ordinary-user account to the user.

- [ ] **Step 8: Record evidence and commit**

Report exact test counts, Chromium widths, migration result, selected compatible pools, zero paid calls, known deferred durable-worker boundary and any existing bundle warnings. Do not record keys, prompts, media or result URLs.

Commit:

```bash
git add tests/integration/test_model_centric_routing.py server/config/credential-pools.example.yaml docs/operations.md docs/verification.md scripts/security-scan.sh docs/superpowers/reports/2026-08-12-model-centric-routing-report.md web/src/test/browser/canvas-responsive.browser.test.tsx
git commit -m "docs: verify model-centric routing"
```

## Execution checkpoints

- After Task 2: review migration and conditional-deletion semantics before routing work.
- After Task 5: review idempotency, `submission_unknown` and cross-group isolation before exposing admin controls.
- After Task 7: run focused browser/admin review before final acceptance.
- After Task 8: stop and hand the isolated instance to the user for personal acceptance; do not begin a new feature or paid call without new user approval.
