# Real Production Model Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect Banana, GPT-Image2, Seedream, and Seedance to their real production providers, prove twelve bounded real jobs through the infinite canvas with locally stored visible media, and record per-user estimated costs in the administrator statistics UI.

**Architecture:** Keep model identity separate from provider routes: Banana uses a dedicated Chiyun Gemini adapter, GPT-Image2 uses the existing Chiyun OpenAI Images adapter, and Seedream/Seedance use Ark. Server-owned presets freeze origins, model names, contracts, and parameter mappings; four isolated credential pools supply short-lived secrets. The existing guarded acceptance runner enforces a hard twelve-submission ceiling, while the real browser workflow proves canvas compilation, result nodes, downloads, ownership, idempotency, and usage/cost projections.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, httpx, Redis-compatible lease coordination, React 19, TypeScript, Vitest Browser/Chromium, pytest, ffprobe.

## Global Constraints

- Do not modify, restart, read runtime state from, or import code from `/Users/260413a/ai-generation-portable-apps`; protocol research remains read-only and excludes secrets, state, logs, outputs, and archives.
- Run real acceptance only on `127.0.0.1:9003` with a formal release build and a Git-ignored isolated data directory.
- Browser responses, logs, reports, process arguments, Git history, database projections, and screenshots must never contain real API keys.
- Use four isolated credential pools: `chiyun/banana`, `chiyun/gpt-image2`, `ark/seedream`, and `ark/seedance`; never rotate a key across model family or provider group.
- Fixed origins are `https://chiyun.work` and `https://ark.cn-beijing.volces.com`; administrators cannot supply arbitrary URLs or provider protocols.
- Hard maximum: twelve new provider submissions, three per logical model. No automatic replay after an ambiguous submission and no unbounded retry loop.
- Mocks and contract tests are safety regression evidence only. A model is enabled only after all three of its real jobs produce locally stored, decodable media visible in a canvas result node.
- Every accepted job must retain a fresh idempotency key, server-owned `user_id`, immutable route snapshot, result ownership, and one-time usage/cost snapshot.
- Cost statistics are internal estimates based on administrator-configured RMB-fen rates; do not describe them as provider invoice reconciliation.
- The user exposed credentials in the conversation; recommend rotating them after acceptance without reproducing them anywhere.

## File Structure

- `server/ai_creation_canvas/acceptance_models.py` — exact code-owned contracts for the four logical models.
- `server/ai_creation_canvas/trusted_routing.py` — fixed provider origins and route presets.
- `server/ai_creation_canvas/adapters/chiyun.py` — GPT-Image2 OpenAI Images transport and local result storage.
- `server/ai_creation_canvas/adapters/chiyun_gemini.py` — Banana Gemini `generateContent` transport and local result storage.
- `server/ai_creation_canvas/adapters/factory.py` — trusted protocol validation and per-lease adapter construction.
- `server/ai_creation_canvas/model_registry.py`, `server/ai_creation_canvas/model_routing.py` — adapter-type allowlists.
- `server/ai_creation_canvas/storage/sqlite.py` — existing jobs plus merged immutable cost snapshots and aggregate queries.
- `server/ai_creation_canvas/api/jobs.py`, `server/ai_creation_canvas/api/usage.py`, `server/ai_creation_canvas/api/admin.py` — billing quantities and owner/admin statistics APIs.
- `web/src/api/usage.ts`, `web/src/pages/usage/index.tsx` — owner and administrator statistics UI.
- `scripts/acceptance-real-media.sh` — secret boundary, clean-build gates, channel selection, and twelve-call guard.
- `scripts/acceptance_real_media.py` — isolated application, canonical projects, paid cases, media/result/statistics evidence, and redacted report.
- `tests/contracts/test_chiyun_gemini_adapter.py` — exact Banana request/response contract.
- `tests/contracts/test_chiyun_adapter.py` — GPT-Image2 request/response and MIME persistence contract.
- `tests/server/test_paid_acceptance_guard.py`, `tests/integration/test_paid_acceptance_client.py`, `tests/integration/test_real_media_runner.py` — paid boundary and execution orchestration.
- `web/src/test/browser/real-production-canvas.browser.test.tsx` — opt-in live 9003 browser assertions; excluded from the default mock suite.
- `docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md` — public redacted results.

---

### Task 1: Merge immutable generation-cost statistics without regressing routing

**Files:**

- Merge: `codex/generation-cost-statistics`
- Resolve: `server/ai_creation_canvas/api/admin.py`
- Resolve: `server/ai_creation_canvas/api/jobs.py`
- Resolve: `server/ai_creation_canvas/storage/sqlite.py`
- Resolve: `tests/server/test_admin_api.py`
- Resolve: `tests/server/test_model_assignments.py`
- Resolve: `web/src/components/layout/product-shell.tsx`
- Resolve: `web/src/router.tsx`
- Add from branch: `server/ai_creation_canvas/api/usage.py`
- Add from branch: `web/src/api/usage.ts`
- Add from branch: `web/src/pages/usage/index.tsx`
- Add from branch: `web/src/test/usage-page.test.tsx`
- Test: `tests/server/test_task_store.py`
- Test: `tests/server/test_jobs_model_routes.py`

**Interfaces:**

- Consumes current `CanvasStore.reserve_job(...)`, managed submission states, server-owned `user_id`, and route snapshots.
- Produces `CanvasStore.usage_rates()`, `set_usage_rates(...)`, `usage_for_owner(user_id)`, and `usage_for_all_users()`.
- Produces `GET /api/v1/usage`, `GET /api/v1/admin/usage`, and administrator rate GET/PUT APIs.
- Preserves current managed-routing CAS tokens, `submission_unknown`, result reuse, and idempotency behavior.

- [ ] **Step 1: Record the merge conflict boundary**

Run:

```bash
git status --short
git merge-tree --write-tree HEAD codex/generation-cost-statistics
```

Expected: exactly the seven documented content conflicts; no pre-existing worktree changes.

- [ ] **Step 2: Merge the branch without committing**

Run:

```bash
git merge --no-ff --no-commit codex/generation-cost-statistics
```

Expected: conflicts only in the documented files. Do not choose either side wholesale in `jobs.py` or `sqlite.py`.

- [ ] **Step 3: Add failing integration assertions for routed costs**

Extend `tests/server/test_jobs_model_routes.py` with a real managed synchronous-success case that sets `image_price_fen=125`, submits once, replays the same idempotency key, and asserts:

```python
usage = store.usage_for_owner("user-a")
assert usage["summary"]["image_count"] == 1
assert usage["summary"]["total_cost_fen"] == "125"
assert len(usage["jobs"]) == 1
assert provider_submit_count == 1
assert usage["jobs"][0]["model_id"] == "banana"
assert usage["jobs"][0]["route_id"] == "banana-chiyun"
```

Add a video poll completion case with `duration=5`, `video_price_fen=20`, and expected `total_cost_fen == "100"`. Assert failed, cancelled, queued, running, duplicate poll, and `submission_unknown` jobs are not charged.

- [ ] **Step 4: Run the new tests and verify RED before conflict resolution is complete**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_jobs_model_routes.py tests/server/test_task_store.py tests/server/test_admin_api.py
```

Expected: collection or assertion failures because the unresolved/current APIs do not yet preserve both routing snapshots and billing fields.

- [ ] **Step 5: Resolve the server conflicts by composing both contracts**

In `api/jobs.py`, derive billing quantities only after model parameter validation, pass them into the existing reservation, and leave all managed-routing ownership/CAS logic unchanged. For image operations use the count of persisted successful result records when available, otherwise the validated requested output count; for video use the validated integer `duration`.

In `storage/sqlite.py`, preserve all current routing columns and additive migrations, then add `video_seconds`, `image_count`, price snapshots, `cost_fen`, and `charged_at`. Capture the rate and charge in the same transaction that first changes the job to `succeeded`, guarded by `charged_at IS NULL`. Include safe `model_id`, `route_id`, and `user_id` in statistics projections, but never upstream IDs, prompts, parameters, keys, or result URLs.

In `api/admin.py`, retain all trusted model/route administration and add the usage endpoints from the statistics branch. In `app.py`, register the owner usage router once.

- [ ] **Step 6: Resolve the React conflicts without dropping routes**

Keep all existing admin/canvas routes and append authenticated `/usage`. Keep the existing product navigation and add one “统计” entry. The usage page must render `model_id`, `route_id`, task status, media quantity, estimated cost, and charged time; title all money fields “估算费用” and state that values come from configured rates.

- [ ] **Step 7: Run focused merge verification**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_task_store.py tests/server/test_jobs_model_routes.py tests/server/test_admin_api.py tests/server/test_activity_api.py tests/server/test_model_assignments.py
npm test --prefix web -- --run web/src/test/usage-page.test.tsx web/src/test/admin-pages.test.tsx
npm run typecheck --prefix web
git diff --check
```

Expected: all pass; routed jobs charge exactly once and all statistics remain owner-scoped.

- [ ] **Step 8: Commit the merge**

```bash
git add server tests web docs/superpowers/specs/2026-08-13-generation-cost-statistics-design.md docs/superpowers/plans/2026-08-13-generation-cost-statistics.md
git commit -m "merge: add generation cost statistics"
```

---

### Task 2: Split the trusted Chiyun protocols and contracts

**Files:**

- Modify: `server/ai_creation_canvas/model_registry.py`
- Modify: `server/ai_creation_canvas/model_routing.py`
- Modify: `server/ai_creation_canvas/acceptance_models.py`
- Modify: `server/ai_creation_canvas/trusted_routing.py`
- Modify: `server/ai_creation_canvas/adapters/factory.py`
- Modify: `server/ai_creation_canvas/api/admin.py`
- Modify: `web/src/api/admin.ts`
- Modify: `web/src/components/admin/model-templates.ts`
- Test: `tests/server/test_trusted_routing_boundary.py`
- Test: `tests/server/test_route_adapter_factory.py`
- Test: `web/src/test/admin-model-routes.test.tsx`

**Interfaces:**

- Produces adapter type `chiyun_gemini_images` for Banana and retains `chiyun_openai_images` for GPT-Image2.
- Produces separate provider IDs `chiyun-banana` and `chiyun-gpt-image2`, both pinned to `https://chiyun.work`.
- Produces exact credential-pool families `nano-banana` and `gpt-image`; a pool can satisfy only its matching family.

- [ ] **Step 1: Write failing trust-boundary tests**

Add cases asserting that `trusted_route_presets()` contains:

```python
assert presets[("banana", "chiyun")].adapter_type == "chiyun_gemini_images"
assert presets[("banana", "chiyun")].provider_id == "chiyun-banana"
assert presets[("gpt_image2", "chiyun")].adapter_type == "chiyun_openai_images"
assert presets[("gpt_image2", "chiyun")].provider_id == "chiyun-gpt-image2"
```

Assert both exact origins are accepted, any other HTTPS origin is rejected, Banana keys cannot satisfy GPT-Image2 routes, GPT-Image2 keys cannot satisfy Banana routes, and modifying provider model name, adapter, family, ports, schema, or mappings returns 400 with zero audit mutation.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_trusted_routing_boundary.py tests/server/test_route_adapter_factory.py
```

Expected: failures because Banana and GPT-Image2 currently share one adapter/provider identity.

- [ ] **Step 3: Define exact code-owned profiles**

Change `_chiyun` into two constructors. Banana uses operation `image.edit`, ports `prompt` and ordered `reference_images`, adapter `chiyun_gemini_images`, model `gemini-2.5-flash-image`, and visible parameters `aspect_ratio` plus `image_size` mapped to Gemini `generationConfig.imageConfig`. GPT-Image2 keeps adapter `chiyun_openai_images`, model `gpt-image-2`, multipart `image[]`, and parameters `size` plus `output_count -> n`.

Add `chiyun_gemini_images` to strict adapter-type allowlists and API types. Add these exact origins:

```python
_TRUSTED_PROVIDER_ORIGINS = MappingProxyType({
    ("chiyun-banana", "chiyun_gemini_images"): "https://chiyun.work",
    ("chiyun-gpt-image2", "chiyun_openai_images"): "https://chiyun.work",
    ("ark", "ark"): "https://ark.cn-beijing.volces.com",
})
```

- [ ] **Step 4: Make the factory fail closed per adapter**

`ProviderProtocol.__post_init__`, `RouteAdapterFactory.validate_route`, `build`, `build_result_reader`, and `_validate_parameter_contract` must recognize each adapter separately. Banana accepts only its Gemini schema and GPT-Image2 only the OpenAI Images schema. No generic “Chiyun” branch may infer behavior from model display names.

- [ ] **Step 5: Align the non-technical admin UI**

Keep the user-facing controls at model level. The Banana settings card offers “Chiyun” and the GPT-Image2 card offers “Chiyun”; hide provider model, adapter, family, and contract internals. Show only credential pool, priority, concurrency, and enabled state. Serialize the exact server-owned preset fields without allowing edits.

- [ ] **Step 6: Verify and commit**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_trusted_routing_boundary.py tests/server/test_route_adapter_factory.py tests/server/test_admin_model_routes.py
npm test --prefix web -- --run web/src/test/admin-model-routes.test.tsx
npm run typecheck --prefix web
git diff --check
```

Then:

```bash
git add server web tests
git commit -m "feat: isolate trusted Chiyun model channels"
```

---

### Task 3: Implement the Banana Gemini adapter and harden image result storage

**Files:**

- Create: `server/ai_creation_canvas/adapters/chiyun_gemini.py`
- Modify: `server/ai_creation_canvas/adapters/chiyun.py`
- Modify: `server/ai_creation_canvas/adapters/factory.py`
- Modify: `server/ai_creation_canvas/adapters/retry.py`
- Create: `tests/contracts/test_chiyun_gemini_adapter.py`
- Modify: `tests/contracts/test_chiyun_adapter.py`
- Modify: `tests/contracts/test_route_retry_contracts.py`

**Interfaces:**

- Produces `ChiyunGeminiGenerationAdapter.submit/poll/open_result` with the existing generation adapter contract.
- Consumes ordered local asset bytes from `asset_loader(asset_id) -> tuple[bytes, str]`.
- Stores every result as private local media plus trusted MIME metadata; result readers support GET, HEAD, one byte range, and full download.

- [ ] **Step 1: Write the exact Gemini request test**

Create a mocked transport test with two reference assets and assert one request:

```python
assert request.method == "POST"
assert request.url.path == "/v1beta/models/gemini-2.5-flash-image:generateContent"
assert request.headers["authorization"] == "Bearer test-only-secret"
body = json.loads(request.content)
parts = body["contents"][0]["parts"]
assert parts[0] == {"text": "keep subject one, use lighting from image two"}
assert [part["inline_data"]["mime_type"] for part in parts[1:]] == ["image/png", "image/jpeg"]
assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "1:1", "imageSize": "2K"}
```

Return one `inlineData` image and assert stored bytes, MIME, dimensions, polling success, and result streaming. Add response variants for `inline_data`, bounded HTTPS `fileData`, invalid host/redirect, excessive body, invalid base64, wrong signature, zero candidates, and multiple results over the declared maximum.

- [ ] **Step 2: Run the new contract and verify RED**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_chiyun_gemini_adapter.py
```

Expected: import failure because `chiyun_gemini.py` does not exist.

- [ ] **Step 3: Implement the Gemini adapter**

Build the request from the ordered asset list without sorting. Quote only the code-owned model path segment. Use `httpx.AsyncClient(base_url="https://chiyun.work", follow_redirects=False, trust_env=False)` through the trusted protocol. Bound each input, total request, response JSON, decoded result count, result bytes, and URL-download bytes. Classify business 4xx as rejected, explicit 429/5xx as temporary unavailable, transport ambiguity as submission unknown, and never include provider body text in user errors.

Persist results atomically under a dedicated `chiyun-gemini-results` directory with `0600` files and an atomic pending index. Store MIME alongside each result so JPEG/WebP are not mislabeled as PNG.

- [ ] **Step 4: Harden GPT-Image2 result MIME handling**

Update `_decode_results` in `chiyun.py` to return `(body, mime)` after signature validation. Store a sidecar or typed index entry for every result, return the actual MIME in `AssetRef`, and make `_LocalRouteResultReader` serve it. Reject content that cannot be decoded as an allowed image even when provider JSON says success.

- [ ] **Step 5: Verify retries and local cleanup**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_chiyun_gemini_adapter.py tests/contracts/test_chiyun_adapter.py tests/contracts/test_route_retry_contracts.py tests/server/test_route_adapter_factory.py
```

Expected: all pass, including temporary-file cleanup after OSError and no retry after ambiguous acceptance.

- [ ] **Step 6: Commit**

```bash
git add server/ai_creation_canvas/adapters tests/contracts tests/server/test_route_adapter_factory.py
git commit -m "feat: add trusted Chiyun Gemini image generation"
```

---

### Task 4: Extend the guarded real-production acceptance runner

**Files:**

- Modify: `scripts/acceptance-real-media.sh`
- Modify: `scripts/acceptance_real_media.py`
- Modify: `tests/server/test_paid_acceptance_guard.py`
- Modify: `tests/integration/test_paid_acceptance_client.py`
- Modify: `tests/integration/test_real_media_runner.py`

**Interfaces:**

- Consumes three secret environment variables: `CHIYUN_BANANA_API_KEY`, `CHIYUN_GPT_IMAGE2_API_KEY`, and `ARK_API_KEY`.
- Produces four isolated pool records and exactly twelve `PaidCase` entries.
- Produces a redacted per-case evidence manifest plus public summary; never outputs credentials or full prompts.

- [ ] **Step 1: Write failing guard tests for key and budget separation**

Assert the guard requires all three environment variables for the full matrix, writes them to a `0600` inode-owned temporary file, unsets them before every offline subprocess, and maps them only to the expected pools. Assert the plan is exactly:

```python
assert [(case.model_id, case.case_id) for case in plan] == [
    ("banana", "single-reference"),
    ("banana", "multi-reference"),
    ("banana", "reordered-reference"),
    ("gpt-image2", "single-reference"),
    ("gpt-image2", "multi-reference"),
    ("gpt-image2", "alternate-size"),
    ("seedream", "single-reference"),
    ("seedream", "multi-reference"),
    ("seedream", "alternate-output"),
    ("seedance", "text-to-video"),
    ("seedance", "first-frame"),
    ("seedance", "multi-reference"),
]
assert maximum_provider_submissions == 12
```

Add negative tests for 11/13 limits, duplicate case IDs, reused project/job/idempotency IDs, wrong key-to-pool binding, a dirty worktree, non-ignored data path, symlinked key/data/output directories, signal cleanup, and direct runner invocation without the shell-owned locator.

- [ ] **Step 2: Verify RED**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_paid_acceptance_guard.py tests/integration/test_paid_acceptance_client.py tests/integration/test_real_media_runner.py
```

Expected: failures because the current runner shares `CHIYUN_API_KEY` and creates fewer than twelve cases.

- [ ] **Step 3: Introduce immutable paid cases**

Replace the smoke/sample plan with:

```python
class PaidCase(NamedTuple):
    case_id: str
    channel_id: str
    model_id: str
    media_type: str
    expected_reference_order: tuple[str, ...]
    params: Mapping[str, object]
```

Validate case IDs, model/channel pairing, unique project/job/idempotency IDs, maximum count 12, and exactly three cases per model before starting the server. Keep `ProviderSubmissionBudget(12)` in the application so bypassing the client cannot exceed the ceiling.

- [ ] **Step 4: Build the isolated production configuration**

Create four providers/routes/pools with distinct opaque key references. Read each secret only after all offline gates pass; inject it into the server process environment under its credential reference, then unlink the key file. Disable demo adapters and mocks. Build and serve the formal static release on 9003.

- [ ] **Step 5: Capture complete redacted evidence**

For each case record only case ID, model ID, route ID, status, local MIME, bytes, dimensions or ffprobe duration, job ID, project ID, user ID hash, estimated cost in fen, and GET/HEAD/Range/full checks. A `PaidRunRecorder` must emit one complete summary on success or failure while preserving the original exception identity even if summary output fails.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_paid_acceptance_guard.py tests/integration/test_paid_acceptance_client.py tests/integration/test_real_media_runner.py
scripts/security-scan.sh
git diff --check
```

Then:

```bash
git add scripts tests/server/test_paid_acceptance_guard.py tests/integration/test_paid_acceptance_client.py tests/integration/test_real_media_runner.py
git commit -m "test: guard twelve real production model jobs"
```

---

### Task 5: Implement and dry-verify the nine real image canvas cases

**Files:**

- Create: `web/src/test/browser/real-production-canvas.browser.test.tsx`
- Modify: `scripts/acceptance_real_media.py`
- Modify: `tests/integration/test_paid_acceptance_client.py`
- Create: `docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md`

**Interfaces:**

- Consumes live 9003, the ordinary test-user session, canonical graph APIs, and the nine image `PaidCase` records.
- Produces nine fully specified browser cases and offline evidence validators. It does not perform provider I/O; the single bounded real execution happens in Task 6 after all twelve cases exist.

- [ ] **Step 1: Add an opt-in live-browser contract that fails against incomplete UI results**

Guard the test with `AICC_REAL_PRODUCTION_ACCEPTANCE=YES`; otherwise skip. For each image case, create a fresh canvas project, upload valid owned references, connect prompt/reference ports, select the exact logical model and parameters, click “运行模型”, and wait for terminal status. Assert:

```ts
expect(resultNode).toBeVisible()
expect(await resultNode.locator("img").evaluate((img) => img.naturalWidth)).toBeGreaterThan(0)
expect(modelToResultEdge).toHaveAttribute("data-active", "true")
expect(modelNode).toHaveTextContent("已完成")
```

The reordered Banana case must reorder the collection before submission and assert the saved submission snapshot lists the reversed asset IDs.

- [ ] **Step 2: Add deterministic server-side evidence checks**

After every browser submission, query as the ordinary user and verify result GET 200, HEAD 200, `Range: bytes=0-1023` 206, full download 200, MIME/signature/decode/dimensions, project graph result node and edge, terminal job state, route snapshot, and one usage row. Query the same project/job/asset/result as administrator and a second ordinary user and require the existing hidden 404/403 contract.

- [ ] **Step 3: Verify idempotency without another provider call**

Replay each exact job body and idempotency key through the application API. Assert identical job ID/result IDs, provider submission counter unchanged, and cost row count unchanged. Do not click “运行模型” again with a new idempotency key.

- [ ] **Step 4: Run all offline gates before paid I/O**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q
npm ci --prefix web
npm run verify:release --prefix web
npm audit --prefix web --omit=dev --audit-level=high
scripts/security-scan.sh
git diff --check
```

Expected: all pass and `git status --short` is empty. Commit any implementation/test changes before the paid run.

- [ ] **Step 5: Dry-run all nine image cases without provider I/O**

Run the acceptance client against the existing protocol-level fake transports and temporary application with `ProviderSubmissionBudget(12)`. Assert all nine projects compile, each planned provider request matches its exact contract, each fake result passes the same local media/result/statistics validators, and the planned counter remains nine. This is only a dry safety check and must be labeled `OFFLINE_NOT_MODEL_ACCEPTANCE`.

Expected: all contract assertions pass and zero real provider request is sent. Do not write `PASS` for any model from this step.

- [ ] **Step 6: Prepare the image evidence schema without claiming success**

Append a nine-row pending table to the public report with status `NOT RUN`. The real run in Task 6 may change a row to `PASS` only when all ten success criteria in the design are true. Label the cost column “内部估算费用（分）”. Keep detailed file hashes and local paths only in the ignored private manifest.

- [ ] **Step 7: Commit the non-secret report and browser contract**

```bash
git add web/src/test/browser/real-production-canvas.browser.test.tsx scripts/acceptance_real_media.py tests/integration/test_paid_acceptance_client.py docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md
git commit -m "test: verify real image models through canvas"
```

---

### Task 6: Implement video validation and execute one bounded twelve-job real run

**Files:**

- Modify: `web/src/test/browser/real-production-canvas.browser.test.tsx`
- Modify: `scripts/acceptance_real_media.py`
- Modify: `tests/integration/test_paid_acceptance_client.py`
- Modify: `docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md`

**Interfaces:**

- Consumes live Seedance 2.5 model declaration and the last three `PaidCase` entries.
- Produces three private local video results and corresponding canvas/statistics evidence.

- [ ] **Step 1: Add the three live browser scenarios**

Add text-to-video, first-frame, and ordered multi-reference projects. Use only parameters declared by the formal Ark config; default to 720p, five seconds, MP4, and the approved audio setting. Assert the browser result node contains a video element with non-empty source and the model node reaches “已完成”, not “排队中”.

- [ ] **Step 2: Add video evidence validation**

For every result require valid MIME, MP4/MOV signature, GET/HEAD/Range/full behavior, and:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height -show_entries format=duration,format_name -of json RESULT_FILE
```

Reject missing video stream, zero dimensions, invalid container, unreadable file, and duration outside the declared tolerance. Assert result node/edge, owner isolation, route snapshot, `video_seconds == 5`, one cost snapshot, and idempotent replay.

- [ ] **Step 3: Execute all twelve paid cases once in one guarded process**

Start one fresh guarded process with all three credentials supplied through the parent environment, `AICC_REAL_PRODUCTION_ACCEPTANCE=YES`, `AICC_MAX_PROVIDER_SUBMISSIONS=12`, a Git-ignored `.paid-acceptance/<direct-child>` data directory, and port 9003. Never put secret values in the command, shell history, plan, report, or test output. The same server process and `ProviderSubmissionBudget(12)` must execute cases 1–12 in order; it may not be restarted to reset the counter.

Expected: Banana 3/3, GPT-Image2 3/3, Seedream 3/3, and Seedance 3/3, with twelve locally stored media files and twelve visible result nodes. On the first failure for a model, stop the run, keep that route disabled, preserve redacted evidence, and do not substitute mock output or start a second paid run without a new reviewed plan.

- [ ] **Step 4: Verify statistics reflect all successful real outputs**

As the ordinary user, `GET /api/v1/usage` must contain exactly the successful real cases and no failed/pending duplicates. As administrator, `GET /api/v1/admin/usage` and the `/usage` page must show the same user, nine image counts, fifteen video seconds, twelve job details, model/route identity, and the sum of immutable estimated costs. A second user must see zero of these rows.

- [ ] **Step 5: Update the report and commit**

Append the three video rows and aggregate statistics. Record actual provider submission count, succeeded/failed/unknown counts, media hashes, MIME, bytes, dimensions/duration, and estimated cost; omit prompts, provider bodies, keys, and long-lived URLs.

```bash
git add web/src/test/browser/real-production-canvas.browser.test.tsx scripts/acceptance_real_media.py tests/integration/test_paid_acceptance_client.py docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md
git commit -m "test: verify real Seedance through canvas"
```

---

### Task 7: Final release verification and 9003 handoff

**Files:**

- Modify: `docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md`
- Verify: `scripts/build-release.sh`
- Verify: `scripts/security-scan.sh`

**Interfaces:**

- Produces a clean release tree, a running isolated 9003 instance, a redacted public report, and retained private media evidence.
- Leaves failed model routes disabled and enables only models with three complete real results.

- [ ] **Step 1: Run fresh complete verification**

```bash
PYTHONPATH=.:server .venv/bin/pytest -q
npm run verify:release --prefix web
npm audit --prefix web --omit=dev --audit-level=high
scripts/security-scan.sh
git diff --check
```

Expected: Python, frontend unit, typecheck, production build, and default Chromium suites all pass; audit has zero high vulnerabilities.

- [ ] **Step 2: Build both release paths and verify manifests**

Run `scripts/build-release.sh` once with the full web build and once with the documented skip-web-build path into two fresh real directories beneath `/private/tmp`. Verify each manifest with `shasum -a 256 -c`, and confirm no key, paid data, provider response, or ignored private evidence is present.

- [ ] **Step 3: Start the final isolated service**

Start the formal release on `127.0.0.1:9003` using the validated isolated data directory and four pool definitions. Keep demo/mock adapters disabled. Confirm the old Portal production ports and processes are unchanged.

- [ ] **Step 4: Perform final human-visible checks**

Using the in-app browser at desktop, 415px, and 240px widths, sign in as the ordinary test user and open representative image and video projects. Confirm media is visibly rendered, downloadable, connected, terminal, and restored after refresh. Open `/usage` as the user and administrator and confirm task/cost totals. Test admin cannot retrieve the user's private project/result despite seeing aggregate statistics.

- [ ] **Step 5: Finalize the report**

State separately for every model: real calls attempted, succeeded, failed, unknown, local files verified, visible canvas results, estimated cost rows, and route enabled/disabled status. Include the exact commit SHA and verification counts. Add the credential-rotation recommendation and explicitly say internal estimated costs are not provider invoice reconciliation.

- [ ] **Step 6: Commit final report changes**

```bash
git add docs/superpowers/reports/2026-08-13-real-production-model-acceptance.md
git commit -m "docs: report real production model acceptance"
```
