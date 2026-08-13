# Simplified Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-form route editing with preset calling settings, fix right-click image-node creation, and prove offline and guarded paid jobs return decodable results.

**Architecture:** Keep existing server route records, credential isolation, selector, idempotency, and result APIs. React compiles code-owned channel presets into the current admin route API; administrators edit only pool, priority, concurrency, and enabled state. Paid validation runs only after the offline same-origin result flow passes.

**Tech Stack:** React 19, TypeScript, Vitest/Chromium, FastAPI, SQLite, httpx MockTransport.

## Global Constraints

- User-visible models are Banana, GPT-Image2, Seedream, and Seedance.
- Provider, family, adapter, provider model name, contracts, and API keys are not browser-editable.
- Rate limit, timeout, transport, and 5xx may advance to another key/channel; business 4xx stops without cross-channel retry.
- Keys remain server-only and never enter Git, browser storage, logs, prompts, or reports.
- Paid validation requires explicit model and call-count allowlists after offline tests pass.

---

### Task 1: Preset Model Calling Settings

**Files:**
- Create: `web/src/components/admin/model-call-settings.tsx`
- Modify: `web/src/components/admin/model-templates.ts`
- Modify: `web/src/pages/admin/models.tsx`
- Test: `web/src/test/admin-model-routes.test.tsx`
- Test: `web/src/test/browser/canvas-responsive.browser.test.tsx`

**Interfaces:**
- Produces `AdminCallingPreset` and `callingPresetsForModel(model)`.
- `ModelCallSettings` consumes a logical model, existing routes, safe pool summaries, and existing create/update/lifecycle API calls.

- [ ] **Step 1: Write failing UI tests**

Assert Banana shows only Chiyun and T8Star cards; GPT-Image2 only Chiyun; Seedream/Seedance only Ark. Assert `线路 ID`, `线路模板`, `Provider`, `模型族`, and `供应商模型名` are absent. Saving may submit only selected pool, priority, concurrency, and enabled state from the UI; trusted identity and contracts must come from the preset.

- [ ] **Step 2: Run RED**

Run: `npm test --prefix web -- --run src/test/admin-model-routes.test.tsx`

Expected: FAIL because the old free-form route editor remains.

- [ ] **Step 3: Implement trusted presets and cards**

Each preset owns `providerId`, `providerModelName`, `adapterType`, `family`, and contract template. Generate a deterministic hidden route ID `${model.model_id}-${preset.id}` for new settings. Only pools exactly matching preset provider, adapter, and family are selectable. A duplicate route for one preset displays an error instead of selecting silently.

The existing route write body remains unchanged, but only `credential_pool_ref`, `priority`, `max_concurrency`, and lifecycle state originate from controls.

- [ ] **Step 4: Replace normal route editing**

Render “调用设置” cards in `models.tsx`. Keep purged/unsupported historical records in a collapsed read-only audit area. Preserve revision-conflict reload and pending-request/unmount guards.

- [ ] **Step 5: Verify and commit**

Run component tests, typecheck, and the 1280/415/240 Chromium admin workflow; then commit `feat: simplify model calling settings`.

### Task 2: Right-click Image Model Creation

**Files:**
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/canvas-node-editing.test.tsx`
- Test: `web/src/test/studio-responsive.browser.test.tsx`

**Interfaces:**
- Consumes existing `imageCreateOperation` / `videoCreateOperation`.
- Produces context-menu disabled state from those resolved operations.

- [ ] **Step 1: Write the failing regression**

With an assigned model that supports only `image.edit`, right-click the canvas and assert “图片生成” is enabled. Activate it and assert a visible model card with `graph.role="model"` and `operation="image.edit"`.

- [ ] **Step 2: Run RED**

Run: `npm test --prefix web -- --run src/test/canvas-node-editing.test.tsx`

Expected: FAIL because the menu checks only `image.generate`.

- [ ] **Step 3: Apply the single-source fix**

Use:

```tsx
imageModelDisabled={imageCreateOperation === null}
videoModelDisabled={videoCreateOperation === null}
```

Do not add model-name checks or a second capability resolver.

- [ ] **Step 4: Verify and commit**

Run focused component and Chromium tests; commit `fix: enable right click image model creation`.

### Task 3: Decodable Offline Result Closure

**Files:**
- Modify: `tests/integration/test_model_centric_routing.py`
- Modify: `.acceptance-model-routing/run_acceptance.py`
- Test: `web/src/test/studio-responsive.browser.test.tsx`

**Interfaces:**
- Keeps `/api/v1/results/{job}/{index}` GET/HEAD/Range unchanged.
- Produces a deterministic valid 64×64 PNG with IHDR, IDAT, and IEND.

- [ ] **Step 1: Write failing validity checks**

Assert PNG signature, IHDR dimensions greater than zero, IDAT, and IEND. In Chromium assert the result image has `naturalWidth > 0`, download returns 200 `image/png`, and the result node is connected to its model node.

- [ ] **Step 2: Run RED**

Run the model-routing integration test and browser suite. Expected: the current 48-byte mock fails decode because it has no IHDR/IDAT.

- [ ] **Step 3: Replace only the malformed fixture**

Reuse the deterministic 64×64 PNG chunk builder in `scripts/acceptance_real_media.py`. Keep provider selection, ownership, storage, and result APIs unchanged.

- [ ] **Step 4: Run an isolated 9003 closure**

Rebuild, restart only the managed 9003 test service, then use the ordinary test user to submit one Banana reference-image job. Verify succeeded status, result edge, decoded preview, HEAD 200, Range 206, full download 200, and admin access 404. Remove the temporary project and leave 9003 running.

- [ ] **Step 5: Commit**

Commit `test: serve decodable offline generation results`.

### Task 4: Guarded Real-key Acceptance

**Files:**
- Modify: `scripts/acceptance-real-media.sh`
- Modify: `scripts/acceptance_real_media.py`
- Test: `tests/server/test_paid_acceptance_guard.py`
- Create: `docs/superpowers/reports/2026-08-13-real-model-smoke-report.md`

**Interfaces:**
- Consumes server-only key variables and explicit model/channel allowlists.
- Produces redacted records: logical model, selected channel, status, MIME, bytes, duration, and user ID.

- [ ] **Step 1: Write failing safety guards**

Require `AICC_RUN_PAID_ACCEPTANCE=YES`, a new ignored data directory, explicit model IDs, and `AICC_MAX_PAID_CALLS` between 1 and 20. Any missing/extra model, count above 20, dirty tracked file, failed offline gate, or missing key must stop before provider I/O.

- [ ] **Step 2: Implement and verify guards without provider traffic**

Run `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_paid_acceptance_guard.py`; prove the guard-only mode issues zero provider requests.

- [ ] **Step 3: Run one paid smoke per configured channel**

After offline gates pass, run one job for each configured representative channel. Stop on ownership, decode, MIME, download, idempotency, or business-4xx failure. Only retry the approved retryable classes.

- [ ] **Step 4: Run the bounded Banana sample**

Only after smoke success, run up to 20 Banana images through the logical-model endpoint. Report channel distribution, success/failure class, latency, bytes, and user ID without prompts, keys, or durable result URLs.

- [ ] **Step 5: Verify and commit tooling/report**

Run guard tests, security scan, and diff check; commit only scripts, tests, and the redacted report. Never commit paid data or credentials.

## Final Verification

Run full Python tests, full frontend tests, typecheck, build, Chromium tests, production audit, security scan, and diff check. Leave isolated 9003 running for user verification; do not modify production ports or services.
