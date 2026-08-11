# Connected Media Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every task. Complete tasks in order. After each implementation task, request a specification review and then a code-quality review before continuing.

**Goal:** Turn the current draggable canvas into a connected, editable media workflow where ordered prompt/media inputs feed capability-driven Seedream and Seedance model nodes, every visible parameter reaches the official provider request, and successful results can be previewed, downloaded, deleted, and reused.

**Architecture:** Keep the existing server-owned job, asset, authentication, and project-sync boundaries. Persist a versioned provider-neutral graph in each `CanvasProject`; compile that graph into a frozen job submission snapshot only when the user runs a model node. Model declarations describe ports, media limits, parameter schema, and provider mappings as administrator-owned data. The browser never sees a key and never constructs provider payloads. Ark adapters validate the same declaration again, resolve owned local assets into bounded provider inputs, submit official payloads, and store results behind same-origin URLs.

**Tech Stack:** React 19, TypeScript, Zustand, Vitest/Testing Library, Playwright, FastAPI, Pydantic, SQLite-backed store, httpx, pytest.

---

## Non-negotiable contracts

- A model node accepts at most one incoming prompt edge; one prompt node may feed many model nodes.
- Media inputs are collection nodes. Their item order is persisted and is the order used for `@图片1`, `@视频1`, and `@音频1` when a job starts.
- Connection edges name the target port. Ports are derived from the selected model declaration, never inferred from display names.
- Parameters are declaration-driven end to end: UI control -> persisted model node -> `/api/v1/jobs.params` -> server validation -> explicit provider payload mapping. A parameter without a supported mapping is not displayed.
- Seed is shown only for models whose declarations and adapters explicitly support it. Current Seedream and Seedance 2.x/2.5 declarations must not invent a seed control.
- The browser submits only owned asset IDs and same-origin API paths. Keys and short-lived provider URLs stay server-side.
- A submit freezes prompt text, ordered media IDs, port assignment, model, operation, and parameters. Editing the graph afterward cannot mutate the in-flight request.
- Existing projects migrate without data loss. Legacy connections without a port remain visible only when they can be normalized unambiguously; otherwise they are omitted safely.
- Keep `127.0.0.1:8992` and production services untouched. Use isolated test data and a separate acceptance port.

## Task 1: Version the graph schema and normalize legacy projects

**Files:**

- Modify: `web/src/types/canvas.ts`
- Modify: `web/src/stores/canvas/use-canvas-store.ts`
- Modify: `web/src/lib/canvas/canvas-node-geometry.ts`
- Create: `web/src/features/graph/contracts.ts`
- Create: `web/src/features/graph/normalize-project.ts`
- Test: `web/src/test/graph-contracts.test.ts`
- Test: `web/src/test/canvas-store.test.ts`

**Step 1: Write failing contract tests.** Cover typed node roles (`prompt`, `media-collection`, `model`, `result`), media item fields, named ports, connection source/target port IDs, model-node parameters, and immutable submission snapshots. Cover migration of existing Text/Config/Image/Video nodes and legacy connections, rejection of dangling/self/duplicate edges, one-prompt-per-model enforcement, and preservation of unknown plugin nodes.

Run: `npm test --prefix web -- --run src/test/graph-contracts.test.ts src/test/canvas-store.test.ts`

Expected: FAIL because the versioned graph metadata and port-aware connection contract do not exist.

**Step 2: Implement the minimal typed schema and pure normalization.** Add discriminated, bounded metadata without deleting the legacy optional fields needed for migration. Add `fromPortId` and `toPortId` to connections. Make normalization deterministic and idempotent, and never fetch or mutate external state.

**Step 3: Bump persisted store version and normalize on import/rehydration/server replacement.** Ensure old projects open, sync, and save once in canonical form. Do not change project IDs or timestamps solely because normalization runs.

**Step 4: Re-run focused tests and typecheck.**

Run: `npm test --prefix web -- --run src/test/graph-contracts.test.ts src/test/canvas-store.test.ts && npm run typecheck --prefix web`

Expected: PASS.

**Step 5: Commit.**

`git add web/src/types/canvas.ts web/src/stores/canvas/use-canvas-store.ts web/src/lib/canvas/canvas-node-geometry.ts web/src/features/graph/contracts.ts web/src/features/graph/normalize-project.ts web/src/test/graph-contracts.test.ts web/src/test/canvas-store.test.ts && git commit -m "feat: version canvas graph contracts"`

## Task 2: Add selection, deletion, context menus, and editable prompt nodes

**Files:**

- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/components/canvas/draggable-canvas-node.tsx`
- Modify: `web/src/components/canvas/canvas-context-menu.tsx`
- Create: `web/src/components/canvas/prompt-node-card.tsx`
- Create: `web/src/features/graph/selection.ts`
- Test: `web/src/test/canvas-node-editing.test.tsx`
- Test: `web/src/test/canvas-generation-page.test.tsx`

**Step 1: Write failing interaction tests.** Cover click selection, additive multi-selection, background clear, Delete/Backspace deletion, context-menu deletion, cascading edge deletion, and no shortcut deletion while an input/textarea/select/content-editable control has focus. Cover creating a blank prompt node, editing it inline, importing one local UTF-8 `.txt` file with size/type limits, and persisting the text without starting a job or showing a spinner.

Run: `npm test --prefix web -- --run src/test/canvas-node-editing.test.tsx src/test/canvas-generation-page.test.tsx`

Expected: FAIL because prompt nodes are static generation cards and the page does not own selection.

**Step 2: Implement project-scoped selection and deletion.** Keep transient selection outside the persisted project. Give selected nodes a visible, high-contrast state. Delete selected nodes and their incident connections in one store update.

**Step 3: Implement the prompt editor.** New nodes start with an empty textarea. TXT import reads locally only, rejects non-text/oversized files with a visible message, and updates the same persisted field as typing. Stop pointer propagation inside controls so editing never drags the node.

**Step 4: Verify and commit.**

Run: `npm test --prefix web -- --run src/test/canvas-node-editing.test.tsx src/test/canvas-generation-page.test.tsx && npm run typecheck --prefix web`

Expected: PASS.

Commit: `git commit -m "feat: edit and delete canvas nodes"`

## Task 3: Draw and edit named-port connections

**Files:**

- Modify: `web/src/components/canvas/canvas-connections.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Create: `web/src/components/canvas/node-port.tsx`
- Create: `web/src/features/graph/connect.ts`
- Modify: `web/src/lib/canvas/canvas-node-geometry.ts`
- Test: `web/src/test/canvas-connections.test.tsx`
- Test: `web/src/test/canvas-generation-page.test.tsx`

**Step 1: Write failing tests.** Cover pointer connection from output to a compatible named input; rejecting incompatible types, duplicate edges, self edges, and a second prompt edge; selecting/deleting an edge; connection geometry under pan/zoom; and persistent reconnect after reload.

**Step 2: Implement pure compatibility rules and named ports.** Use stable port IDs (`prompt`, `reference_images`, `first_frame`, `last_frame`, `reference_video`, `reference_audio`, `result`). A model node exposes only ports declared by its selected model. Result nodes expose media outputs by MIME category.

**Step 3: Wire connection gestures and rendering.** Use accessible buttons as handles, support click-to-connect as the keyboard-friendly path, and use the existing SVG layer for curves. Never start node dragging from a handle.

**Step 4: Verify and commit.**

Run: `npm test --prefix web -- --run src/test/canvas-connections.test.tsx src/test/canvas-generation-page.test.tsx && npm run typecheck --prefix web`

Expected: PASS.

Commit: `git commit -m "feat: connect canvas nodes by named ports"`

## Task 4: Add owned media upload and ordered collection nodes

**Files:**

- Modify: `server/ai_creation_canvas/domain/models.py`
- Modify: `server/ai_creation_canvas/api/assets.py`
- Modify: `web/src/api/contracts.ts`
- Modify: `web/src/api/assets.ts`
- Create: `web/src/components/canvas/media-collection-node.tsx`
- Create: `web/src/features/graph/media-collection.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `tests/server/test_assets_api.py`
- Test: `web/src/test/media-collection-node.test.tsx`

**Step 1: Write failing server tests.** Cover owned image/video/audio upload with explicit MIME and byte limits, signature checks where practical, streaming size enforcement without relying on `Content-Length`, owner isolation, safe metadata, same-origin content streaming with GET/HEAD/Range, and reliable temporary-file cleanup. Keep current portrait behavior unchanged.

**Step 2: Implement provider-neutral media assets.** Extend reference assets to record media category while preserving owner checks. Use separate bounded limits suitable for light local testing and configurable deployment limits. Never expose local paths.

**Step 3: Write failing UI tests.** Cover one collection node containing many items, add/remove/preview, drag reorder, keyboard reorder, stable labels `图片1…`, `视频1…`, `音频1…`, mixed upload progress/failure, and persistence of ordered asset IDs. Verify reorder changes the compiled order.

**Step 4: Implement collection UI and upload client.** Allow image, video, and audio collection node types. Upload immediately to the owned asset endpoint; persist only active asset IDs and safe metadata. Object URLs are transient and revoked.

**Step 5: Verify and commit.**

Run sequentially: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_assets_api.py`; then `npm test --prefix web -- --run src/test/media-collection-node.test.tsx`; then `npm run typecheck --prefix web`.

Expected: PASS.

Commit: `git commit -m "feat: add ordered media input collections"`

## Task 5: Declare model capabilities and compile a frozen job request

**Files:**

- Modify: `server/ai_creation_canvas/domain/models.py`
- Modify: `server/ai_creation_canvas/api/models.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/ai_creation_canvas/adapters/ark.py`
- Modify: `server/config/ark-models.example.json`
- Modify: `web/src/api/contracts.ts`
- Create: `web/src/features/graph/model-capabilities.ts`
- Create: `web/src/features/graph/compile-job.ts`
- Create: `web/src/components/canvas/model-call-node.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `tests/server/test_models_api.py`
- Test: `tests/contracts/test_ark_adapter.py`
- Test: `web/src/test/model-call-node.test.tsx`
- Test: `web/src/test/compile-job.test.ts`

**Step 1: Write failing declaration tests.** Model specs must declare supported input ports, per-port category/count limits, parameter schema, and a finite allowlisted provider mapping. Reject unknown fields, unsupported mappings, impossible limits, unsafe schema, and declarations that advertise a parameter the adapter cannot map.

**Step 2: Extend the provider-neutral catalog.** Serialize safe capabilities to users; keep provider endpoint/key details server-only. Configure current Seedream and Seedance models from official limits. Do not declare seed for current models unless official support is verified for that exact model.

**Step 3: Write failing compiler/UI tests.** Compile only connected inputs; enforce one prompt; freeze ordered assets and port assignments; enforce exact model limits; keep first/last frame distinct; omit disconnected nodes; persist parameter edits; block submit with actionable errors. Prove that string, number, integer, boolean, enum, empty string, false, and zero values survive unchanged.

**Step 4: Implement the model-call node.** Select model and operation inside the node, render only declared controls/ports, show limits, and expose a deliberate Run button. Remove the separate global inspector as the authoritative source after migration.

**Step 5: Extend the job contract with ordered typed inputs.** Add a bounded `inputs` object whose values are owned asset IDs. Preserve `asset_ids` compatibility during migration, but new graph submissions use typed inputs. Validate model limits and ownership before adapter submission.

**Step 6: Add a parameter-forwarding contract test.** Use a deliberately distinctive value for every displayed parameter and assert the exact outbound provider JSON. This test is the gate preventing decorative controls.

**Step 7: Verify and commit.**

Run sequentially: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_models_api.py tests/contracts/test_ark_adapter.py`; then `npm test --prefix web -- --run src/test/model-call-node.test.tsx src/test/compile-job.test.ts`; then `npm run typecheck --prefix web`.

Expected: PASS.

Commit: `git commit -m "feat: compile capability driven generation jobs"`

## Task 6: Implement Seedream multi-reference image generation

**Files:**

- Modify: `server/ai_creation_canvas/adapters/ark.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/config/ark-models.example.json`
- Modify: `web/src/features/graph/compile-job.ts`
- Modify: `web/src/components/canvas/model-call-node.tsx`
- Test: `tests/contracts/test_ark_adapter.py`
- Test: `tests/server/test_ark_real_contract.py`
- Test: `web/src/test/seedream-graph-flow.test.tsx`

**Step 1: Write failing adapter contract tests from official Seedream requests.** Cover text-to-image, one reference, ordered multiple references, per-model limits (14 for 4.x/4.5/5 lite and 10 for 5 pro), supported resolution/ratio/quality/count fields, exact outbound JSON, non-success errors, malformed JSON, download MIME/size checks, and multiple returned images when the selected model supports them.

**Step 2: Resolve owned assets safely.** Stream or encode bounded local image inputs according to the official API contract without putting base64 or provider URLs into projects. Maintain collection order exactly.

**Step 3: Map image operations explicitly.** Use `image.generate` without references and `image.edit` with references. Reject unsupported combinations before any paid request. Store each successful image behind same-origin result access; create one result node per output with common source job provenance.

**Step 4: Write and pass page-flow tests.** Connect prompt + ordered image collection -> Seedream node, submit, assert exact request snapshot, render all results, reconnect a result as an image input, and verify download.

**Step 5: Verify and commit.**

Run sequentially: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_ark_adapter.py tests/server/test_ark_real_contract.py`; then `npm test --prefix web -- --run src/test/seedream-graph-flow.test.tsx`; then full typecheck.

Expected: PASS with network calls mocked; real paid call remains a separately gated acceptance step.

Commit: `git commit -m "feat: generate Seedream images from graph inputs"`

## Task 7: Implement Seedance multimodal video generation

**Files:**

- Modify: `server/ai_creation_canvas/adapters/ark.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `server/config/ark-models.example.json`
- Modify: `web/src/features/graph/compile-job.ts`
- Modify: `web/src/components/canvas/model-call-node.tsx`
- Test: `tests/contracts/test_ark_adapter.py`
- Test: `tests/server/test_ark_real_contract.py`
- Test: `web/src/test/seedance-graph-flow.test.tsx`

**Step 1: Write failing official-payload tests.** Cover prompt-only, first frame, first+last frame, ordered image/video/audio reference collections, and exact `content` item ordering/roles. Enforce Seedance 2.0 limits (9 images, 3 videos, 3 audio; no audio-only) and 2.5 limits (30 images, 10 videos, 10 audio; audio-only allowed). Cover declared ratio/resolution/duration/audio/watermark/camera options and ensure every visible option appears exactly in the outbound request format documented for that model.

**Step 2: Implement explicit multimodal mapping.** Preserve named-port semantics and collection order. Reject invalid combinations before any provider call. Do not synthesize seed support for 2.x/2.5.

**Step 3: Harden asynchronous behavior.** Cover submit timeout, retryable/non-retryable response mapping, polling, cancellation, browser reload resume without another POST, result streaming, and owner isolation.

**Step 4: Pass the graph flow test.** Connect prompt and media collections to a video model node, edit supported parameters, run, resume polling, preview/download the result, and reconnect it as a reference video.

**Step 5: Verify and commit.**

Run sequentially: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_ark_adapter.py tests/server/test_ark_real_contract.py`; then `npm test --prefix web -- --run src/test/seedance-graph-flow.test.tsx`; then typecheck.

Expected: PASS.

Commit: `git commit -m "feat: generate Seedance video from graph inputs"`

## Task 8: Complete result nodes, downloads, retries, and job observability

**Files:**

- Modify: `web/src/components/canvas/generation-node-card.tsx`
- Create: `web/src/components/canvas/result-node-card.tsx`
- Modify: `web/src/features/generation/result-node.ts`
- Modify: `web/src/features/generation/use-generation-job.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/result-node-card.test.tsx`
- Test: `web/src/test/generation-job.test.tsx`

**Step 1: Write failing tests.** Cover image/video preview, same-origin download with a meaningful filename, delete, reuse output port, progress and safe error messages, retry with the same idempotency key only when explicitly requested, independent concurrent nodes, reload resume, scope change cancellation, and no result written into another project/user scope.

**Step 2: Implement dedicated result cards and model-node status.** Results are immutable outputs. The model node shows the active/last job and remains editable for the next run. A failed run does not delete its connected inputs or parameter choices.

**Step 3: Verify and commit.**

Run: `npm test --prefix web -- --run src/test/result-node-card.test.tsx src/test/generation-job.test.tsx && npm run typecheck --prefix web`

Expected: PASS.

Commit: `git commit -m "feat: manage reusable generation results"`

## Task 9: Browser usability, responsive layout, and graph persistence acceptance

**Files:**

- Modify: `web/src/test/browser/canvas-responsive.spec.ts`
- Create: `web/src/test/browser/connected-media-graph.spec.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: graph components only as failures require

**Step 1: Add real-browser acceptance tests.** At desktop, 415 px, and 240 px widths, verify controls remain readable, no inspector/canvas/footer overlap, context menus remain reachable, ports can be connected, inputs can be edited, collections can be reordered, nodes can be deleted, and result downloads start. Verify pan/zoom and node drag do not edit/reorder accidentally.

**Step 2: Add persistence acceptance.** Create a graph, refresh, and prove node contents, positions, ordered media, named connections, model, and parameters restore. Simulate a remote project conflict and confirm the existing recovery behavior still works.

**Step 3: Fix only evidence-backed failures and rerun.**

Run: `npm run test:browser --prefix web -- connected-media-graph.spec.ts canvas-responsive.spec.ts`

Expected: PASS.

**Step 4: Commit.**

`git commit -m "test: cover connected canvas workflows in browser"`

## Task 10: Real-key light acceptance, release gates, and operator documentation

**Files:**

- Create: `scripts/acceptance-real-media.sh`
- Modify: `docs/operations.md`
- Modify: `README.md`
- Modify: `server/config/ark-models.example.json`
- Test: `tests/server/test_real_media_acceptance_guard.py`

**Step 1: Build a paid-call safety gate.** Require an explicit environment opt-in, exact model allowlist, one image call and one short/low-cost video call maximum, unique acceptance data directory, request summary without secrets or raw media, and automatic refusal when configuration exceeds the cap. Never discover or print keys.

**Step 2: Run all offline gates before spending.** Run Python and frontend suites sequentially, typecheck, production build, production dependency audit, security scan, diff check, and both release build paths.

Expected: every offline gate passes before real calls are enabled.

**Step 3: Run the minimal real flow only with the already configured administrator-owned server key.** Upload one small reference image; make one inexpensive Seedream generation proving reference order and visible parameter forwarding; make one shortest supported Seedance generation proving prompt/media/parameter forwarding; poll to success; stream/download results; record only job IDs, model IDs, durations, statuses, MIME, and byte counts. If either call fails, diagnose from sanitized status and stop before repeating paid requests unless the failure is proven local and the retry remains within the approved small spend.

**Step 4: Perform two-account owner-isolation acceptance.** Administrator configures models/key; normal user can select models but cannot see/change keys. Confirm assets, jobs, results, projects, and browser storage do not cross accounts.

**Step 5: Document deployment expectations.** Explain administrator key/model declaration setup, media limits, storage sizing, reverse-proxy streaming/Range requirements, multi-region stateless API expectations, shared persistent storage/database requirements, Cloudflare light-test limitations, backup, and upgrade/migration behavior.

**Step 6: Final review and release verification.** Request independent specification and code-quality reviews; resolve Critical/Important issues. Repeat the full offline gates after fixes. Build a release artifact with and without `--skip-web-build`, start it on an isolated port/data directory, and smoke-test login, graph edit, upload, submit, polling, preview, download, and logout.

**Step 7: Commit and push the feature branch.**

`git commit -m "docs: document connected media graph operations"`

Push only after the working tree contains no accidental secrets, state, outputs, archives, or request captures.

## Completion evidence

The feature is complete only when all of the following are recorded in the final report:

- Exact outbound JSON tests prove every displayed Seedream/Seedance parameter is submitted.
- Official media-count and port-combination limits are enforced before provider calls.
- Ordered collections deterministically control `@图片N`/`@视频N`/`@音频N` semantics.
- Prompt, media, model, and result nodes can be edited/connected/deleted as designed.
- Results preview, download, and reconnect through same-origin owned endpoints.
- Reload, concurrent jobs, retries, cancellation, conflict recovery, and two-user isolation pass.
- Full Python, frontend, typecheck, browser, build, audit, security, release, and diff gates pass.
- Minimal real Seedream and Seedance acceptance succeeds without exposing or persisting the key in the browser, repository, logs, or report.
