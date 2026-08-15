# ComfyUI 受控工作流库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an administrator-only, versioned ComfyUI workflow library that safely imports, previews, assigns and round-trips ComfyUI JSON, plus a configured server-side ComfyUI adapter contract ready for a later execution slice.

**Architecture:** Parse editor-save and API workflow formats as inert data on the server, store immutable revisions and a safe node/edge projection in SQLite, and expose separate administrator and assigned-user projections. The browser uploads a file without reading it and renders only server-projected generic nodes. A separate trusted `ComfyWorkflowServicePort` registers configuration-defined ComfyUI services; it is never exposed to the browser or routed through the generic model core.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, httpx, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Follow [the approved design](/Users/260413a/ai-creation-canvas/docs/superpowers/specs/2026-08-14-comfyui-workflow-library-design.md) exactly; this slice does not dispatch a real ComfyUI generation task.
- Preserve the fixed Infinite Canvas baseline `9bccd0ff1a7057a835708a731644ab05371fea3b`, AGPL-3.0 notices, and existing node registry boundaries.
- Do not copy either user-supplied external workflow into Git. Automated tests read only repository-authored safe fixtures; validate user-supplied workflows only through the explicit Task 7 local acceptance command, and commit only the newly authored core-node fixture.
- Use the test port `8992` and an isolated temporary data directory. Do not read, write, restart, or probe `/Users/260413a/ai-generation-portable-apps` or ports `8991`, `9090`, `8787`, `8797`, `8891`.
- Browser code must not parse imported JSON, persist its content, see server credentials/base URLs, dynamically import workflow code, or connect directly to ComfyUI.
- Normal users may only see assigned, enabled templates; only administrators may import, preview, export, change lifecycle, or assign workflows.
- All migration work is additive. Existing user changes in the worktree are out of scope; stage only the files named by the current task.
- Every task begins with a failing test, verifies it is red, implements the minimum code, reruns the focused test, then makes one independent commit.

---

## File Structure

### Python

- `server/ai_creation_canvas/comfy/__init__.py` — explicit public ComfyUI-library surface.
- `server/ai_creation_canvas/comfy/workflow_json.py` — bounded JSON loading, format detection, topology validation, canonical checksum, safe preview projection and export encoding.
- `server/ai_creation_canvas/comfy/models.py` — immutable template, revision, assignment, preview and service-health types.
- `server/ai_creation_canvas/comfy/library.py` — template lifecycle and assignment service over `CanvasStore`.
- `server/ai_creation_canvas/comfy/service.py` — `ComfyWorkflowServicePort` and the trusted HTTP ComfyUI implementation.
- `server/ai_creation_canvas/api/comfy_workflows.py` — administrator and assigned-user HTTP routes.
- `server/ai_creation_canvas/domain/ports.py` and `server/ai_creation_canvas/domain/registry.py` — isolated service-port registration.
- `server/ai_creation_canvas/config.py` — trusted ComfyUI service configuration schema and path validation.
- `server/ai_creation_canvas/storage/sqlite.py` — additive workflow, revision and assignment tables plus atomic store methods.
- `server/ai_creation_canvas/app.py` — ComfyUI library/service assembly and router registration.
- `server/config/comfyui-services.example.json` — inert server-only configuration example with no secret.
- `scripts/verify-comfy-workflow-roundtrip.py` — explicit local acceptance command for user-provided JSON files; it reads only supplied paths and writes no output files.

### Web

- `web/src/api/comfy-workflows.ts` — typed same-origin clients; the import function sends `File` through `FormData` without reading it.
- `web/src/components/comfy/workflow-preview.tsx` — safe generic saved-graph and API-summary preview.
- `web/src/components/comfy/workflow-import.tsx` — administrator upload/confirmation interaction.
- `web/src/pages/admin/comfy-workflows.tsx` — library, revision, lifecycle, assignment and export interface.
- `web/src/features/nodes/comfy-workflow.tsx` — statically imported generic `comfy.workflow` node definition and card.
- `web/src/types/canvas.ts`, `web/src/features/graph/contracts.ts`, `web/src/features/graph/normalize-project.ts`, `web/src/features/nodes/builtins.tsx`, `web/src/pages/canvas/project.tsx` — persisted generic workflow-node metadata, ports and disabled execution presentation.
- `web/src/router.tsx`, `web/src/components/layout/product-shell.tsx` — administrator route and navigation.

### Tests and fixtures

- `tests/fixtures/comfy/core-load-save-workflow.json` — newly authored two-core-node saved workflow, with no user content or external assets.
- `tests/server/test_comfy_workflow_json.py` — format, checksum, limits, topology and sensitive-field contract tests.
- `tests/server/test_comfy_workflow_store.py` — immutable revision, lifecycle, audit and assignment tests.
- `tests/server/test_comfy_service.py` — config loader, registry and HTTP adapter mock-contract tests.
- `tests/server/test_comfy_workflow_api.py` — CSRF, RBAC, import/export, projection and two-user isolation tests.
- `tests/integration/test_comfy_workflow_library.py` — local product flow using the authored fixture.
- `web/src/test/admin-comfy-workflows.test.tsx`, `web/src/test/comfy-workflow-preview.test.tsx`, `web/src/test/comfy-workflow-node.test.tsx` — browser file-handling, safe rendering, navigation and static-node tests.

---

### Task 1: Parse and Safely Project Both ComfyUI JSON Formats

**Files:**

- Create: `server/ai_creation_canvas/comfy/__init__.py`
- Create: `server/ai_creation_canvas/comfy/models.py`
- Create: `server/ai_creation_canvas/comfy/workflow_json.py`
- Create: `tests/fixtures/comfy/core-load-save-workflow.json`
- Test: `tests/server/test_comfy_workflow_json.py`

**Interfaces:**

- Produces: `WorkflowFormat.EDITOR | WorkflowFormat.API`, `ParsedWorkflow`, `parse_workflow_json(raw: bytes) -> ParsedWorkflow`, `canonical_checksum(value: object) -> str`, and `export_workflow(parsed: ParsedWorkflow, format: WorkflowFormat) -> bytes`.
- `ParsedWorkflow` contains only immutable raw JSON data, checksum, format availability, `node_count`, `link_count`, `node_types`, and `PreviewGraph(nodes, edges, has_editor_layout)`.
- Rejects: duplicate JSON keys, invalid UTF-8, non-finite values, more than 4 MiB/500 nodes/2,000 links/depth 64/string 64 KiB, dangling editor links, and case-insensitive forbidden field names.

- [ ] **Step 1: Write red parser tests and the authored fixture**

```json
{
  "last_node_id": 2,
  "last_link_id": 1,
  "nodes": [
    {"id": 1, "type": "LoadImage", "pos": [0, 0], "size": [220, 100], "inputs": [], "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [1]}], "properties": {}, "widgets_values": ["example.png"]},
    {"id": 2, "type": "SaveImage", "pos": [320, 0], "size": [220, 100], "inputs": [{"name": "images", "type": "IMAGE", "link": 1}], "outputs": [], "properties": {}, "widgets_values": ["aicc"]}
  ],
  "links": [[1, 1, 0, 2, 0, "IMAGE"]],
  "groups": [],
  "config": {},
  "extra": {},
  "version": 0.4
}
```

```python
def test_editor_workflow_round_trips_by_canonical_checksum(core_workflow: bytes) -> None:
    parsed = parse_workflow_json(core_workflow)
    assert parsed.formats == frozenset({WorkflowFormat.EDITOR})
    assert parsed.node_count == 2 and parsed.link_count == 1
    assert parsed.preview.has_editor_layout is True
    assert canonical_checksum(json.loads(export_workflow(parsed, WorkflowFormat.EDITOR))) == parsed.checksum

def test_rejects_dangling_link_and_sensitive_key() -> None:
    with pytest.raises(WorkflowValidationError, match="WORKFLOW_TOPOLOGY_INVALID"):
        parse_workflow_json(b'{"nodes":[{"id":1,"type":"LoadImage"}],"links":[[1,1,0,2,0,"IMAGE"]]}')
    parsed = parse_workflow_json(b'{"1":{"class_type":"LoadImage","inputs":{"resource_url":"https://metadata.example"}}}')
    assert "https://metadata.example" not in repr(parsed.preview)

    with pytest.raises(WorkflowValidationError, match="WORKFLOW_FIELD_REJECTED"):
        parse_workflow_json(b'{"1":{"class_type":"LoadImage","inputs":{"callback_url":"https://bad.example"}}}')
```

- [ ] **Step 2: Run the focused test to confirm red**

Run: `pytest -q tests/server/test_comfy_workflow_json.py`

Expected: collection fails because `ai_creation_canvas.comfy` does not exist.

- [ ] **Step 3: Implement the bounded decoder and deterministic projection**

```python
class WorkflowFormat(StrEnum):
    EDITOR = "editor"
    API = "api"

def parse_workflow_json(raw: bytes) -> ParsedWorkflow:
    value = _decode_json_object(raw, max_bytes=4 * 1024 * 1024)
    _assert_value_limits(value, depth=0)
    if isinstance(value.get("nodes"), list) and isinstance(value.get("links"), list):
        return _parse_editor(value)
    if value and all(isinstance(key, str) and key.isdecimal() for key in value):
        return _parse_api(value)
    raise WorkflowValidationError("WORKFLOW_FORMAT_UNSUPPORTED")
```

Use `json.loads(..., object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_constant)`. Build the preview from node IDs/types/titles and integer editor positions only; omit widget values, prompts, arbitrary nested values and HTML. Encode exports from the stored parsed object with `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")` for checksums and two-space indentation for download bytes.

- [ ] **Step 4: Run parser tests and static checks**

Run: `pytest -q tests/server/test_comfy_workflow_json.py && python -m compileall -q server/ai_creation_canvas/comfy`

Expected: all parser cases pass and compilation prints no errors.

- [ ] **Step 5: Commit Task 1**

```bash
git add server/ai_creation_canvas/comfy tests/fixtures/comfy/core-load-save-workflow.json tests/server/test_comfy_workflow_json.py
git commit -m "feat: validate ComfyUI workflow JSON"
```

### Task 2: Store Immutable Workflow Revisions and User Assignments

**Files:**

- Create: `server/ai_creation_canvas/comfy/library.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Test: `tests/server/test_comfy_workflow_store.py`

**Interfaces:**

- Consumes: `ParsedWorkflow`, `WorkflowFormat`, `WorkflowValidationError` from Task 1.
- Produces: `ComfyWorkflowLibrary.create_template(...)`, `add_revision(...)`, `set_lifecycle(...)`, `replace_assignments(...)`, `admin_list(...)`, `assigned_list(user_id)`, and `export_revision(...)`.
- `CanvasStore` provides atomic workflow-template/revision/assignment methods and writes `canvas_admin_audit` events under `comfy_workflow.*` action names.

- [ ] **Step 1: Write red store tests**

```python
def test_revisions_are_immutable_and_assignment_is_owner_scoped(tmp_path: Path) -> None:
    store = CanvasStore(tmp_path)
    library = ComfyWorkflowLibrary(store)
    template = library.create_template("Core image", "comfy-local", parse_workflow_json(FIXTURE.read_bytes()), actor_user_id="admin")
    revised = library.add_revision(template.workflow_id, parse_workflow_json(API_BYTES), expected_revision=1, actor_user_id="admin")
    assert template.revision == 1 and revised.revision == 2
    library.replace_assignments("user-a", (template.workflow_id,), actor_user_id="admin")
    assert [item.workflow_id for item in library.assigned_list("user-a")] == [template.workflow_id]
    assert library.assigned_list("user-b") == ()
    assert library.export_revision(template.workflow_id, 1, WorkflowFormat.EDITOR) == export_workflow(parse_workflow_json(FIXTURE.read_bytes()), WorkflowFormat.EDITOR)
```

- [ ] **Step 2: Run the focused test to confirm red**

Run: `pytest -q tests/server/test_comfy_workflow_store.py`

Expected: collection fails because `ComfyWorkflowLibrary` is not defined.

- [ ] **Step 3: Add additive schema and typed store operations**

Create these tables inside `CanvasStore._migrate_schema()` before any read method:

```sql
CREATE TABLE IF NOT EXISTS canvas_comfy_workflows (
  workflow_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, description TEXT NOT NULL,
  service_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  archived_at TEXT, revision INTEGER NOT NULL CHECK(revision >= 1),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_comfy_workflow_revisions (
  workflow_id TEXT NOT NULL REFERENCES canvas_comfy_workflows(workflow_id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL, source_filename TEXT NOT NULL, editor_json TEXT, api_json TEXT,
  editor_checksum TEXT, api_checksum TEXT, node_inventory_json TEXT NOT NULL,
  dependency_inventory_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(workflow_id, revision)
);
CREATE TABLE IF NOT EXISTS canvas_comfy_workflow_access (
  user_id TEXT NOT NULL, workflow_id TEXT NOT NULL REFERENCES canvas_comfy_workflows(workflow_id) ON DELETE RESTRICT,
  granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT,
  PRIMARY KEY(user_id, workflow_id)
);
```

`add_revision` inserts a new `revision=current+1` row and advances the parent revision only in the same `BEGIN IMMEDIATE` transaction. Lifecycle updates require an exact expected revision. Replace assignments using one delete/insert transaction after verifying each referenced non-archived workflow exists. Return copied dictionaries or frozen dataclasses; never return mutable stored JSON.

- [ ] **Step 4: Implement library projections and lifecycle rules**

```python
class ComfyWorkflowLibrary:
    def create_template(self, display_name: str, service_id: str, parsed: ParsedWorkflow, *, actor_user_id: str) -> ComfyWorkflowTemplate: ...
    def add_revision(self, workflow_id: str, parsed: ParsedWorkflow, *, expected_revision: int, actor_user_id: str) -> ComfyWorkflowTemplate: ...
    def export_revision(self, workflow_id: str, revision: int, format: WorkflowFormat) -> bytes: ...

    def assigned_list(self, user_id: str) -> tuple[ComfyWorkflowProjection, ...]:
        return self._store.assigned_comfy_workflows(user_id)
```

New templates and revisions start disabled. Same-format same-checksum uploads return `WORKFLOW_DUPLICATE_REVISION` and do not change audit/history. API and editor JSON may coexist only after both inventories have equal node IDs/types; otherwise return `WORKFLOW_PAIR_MISMATCH`.

- [ ] **Step 5: Run focused persistence tests**

Run: `pytest -q tests/server/test_comfy_workflow_store.py tests/server/test_task_store.py`

Expected: all pass; the existing task-store test confirms additive migrations did not change job behavior.

- [ ] **Step 6: Commit Task 2**

```bash
git add server/ai_creation_canvas/comfy/library.py server/ai_creation_canvas/storage/sqlite.py tests/server/test_comfy_workflow_store.py
git commit -m "feat: persist versioned ComfyUI workflows"
```

### Task 3: Add the Trusted ComfyUI Service Contract and Configuration Loader

**Files:**

- Create: `server/ai_creation_canvas/comfy/service.py`
- Create: `server/config/comfyui-services.example.json`
- Modify: `server/ai_creation_canvas/domain/ports.py`
- Modify: `server/ai_creation_canvas/domain/registry.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/app.py`
- Test: `tests/server/test_comfy_service.py`
- Test: `tests/server/test_config.py`
- Test: `tests/server/test_registry.py`

**Interfaces:**

- Produces: `ComfyWorkflowServicePort` with `health`, `list_node_types`, `submit`, `poll`, `cancel`; `AdapterRegistry.register_comfy_workflow`, `comfy_workflow`, `comfy_workflow_adapters`; and `load_comfyui_service_declarations`.
- Config entry schema: `{service_id, base_url, timeout_seconds, auth_header_ref}`; `auth_header_ref` is nullable and the example uses `null`.
- `create_app` stores `app.state.comfy_workflow_library` and `app.state.comfy_workflow_services`; unavailable services do not prevent the app from starting.

- [ ] **Step 1: Write red configuration/adapter tests**

```python
async def test_adapter_reports_node_inventory_and_never_accepts_callers_url() -> None:
    adapter = ComfyHttpWorkflowService(ComfyServiceDeclaration("comfy-local", "http://127.0.0.1:8188", 3, None), transport=mock_transport())
    assert await adapter.list_node_types(context()) == frozenset({"LoadImage", "SaveImage"})
    with pytest.raises(TypeError):
        await adapter.submit(context(), workflow=API_WORKFLOW, base_url="https://attacker.example")  # type: ignore[call-arg]

def test_comfy_config_rejects_symlink_unknown_key_and_production_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ComfyUI services configuration is invalid"):
        load_comfyui_service_declarations(tmp_path / "missing.json", tmp_path)
```

- [ ] **Step 2: Run the focused tests to confirm red**

Run: `pytest -q tests/server/test_comfy_service.py tests/server/test_config.py tests/server/test_registry.py`

Expected: collection fails because the ComfyUI service port and loader are absent.

- [ ] **Step 3: Implement isolated port registration and safe config loading**

```python
class ComfyWorkflowServicePort(Protocol):
    service_id: str
    async def health(self, context: RequestContext) -> ComfyServiceHealth: ...
    async def list_node_types(self, context: RequestContext) -> frozenset[str]: ...
    async def submit(self, context: RequestContext, request: ComfyWorkflowRequest) -> UpstreamJob: ...
    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState: ...
    async def cancel(self, context: RequestContext, upstream_job_id: str) -> None: ...
```

Mirror the existing adapter registry's async-signature validation in a dedicated `_comfy_workflow` map; do not force this protocol through `GenerationPort`. Make the loader verify regular non-symlink file, configured-root containment, 64 KiB maximum, exact key set, unique ASCII service IDs, HTTP(S) origin with no path/query/credentials, timeout 1–60 seconds, and a non-production port not in `_PRODUCTION_PORTS`.

- [ ] **Step 4: Implement the HTTP adapter without enabling execution UI**

Use a fixed `httpx.AsyncClient(base_url=declaration.base_url, timeout=...)`. `health` and `list_node_types` only issue `GET /object_info`; `submit` posts the server-built API object to `/prompt`, `poll` reads `/history/{prompt_id}`, and `cancel` posts the server-held prompt ID to `/queue`. Validate all response identifiers before returning them and map transport failures to the existing typed retryable upstream error. Resolve `auth_header_ref` only through an injected server resolver; never include it in a dataclass repr, exception, or API projection.

- [ ] **Step 5: Assemble services and rerun focused tests**

Run: `pytest -q tests/server/test_comfy_service.py tests/server/test_config.py tests/server/test_registry.py tests/server/test_app_security.py`

Expected: all pass; `test_app_security.py` verifies the service base URL and auth reference are absent from responses.

- [ ] **Step 6: Commit Task 3**

```bash
git add server/ai_creation_canvas/comfy/service.py server/config/comfyui-services.example.json server/ai_creation_canvas/domain/ports.py server/ai_creation_canvas/domain/registry.py server/ai_creation_canvas/config.py server/ai_creation_canvas/app.py tests/server/test_comfy_service.py tests/server/test_config.py tests/server/test_registry.py
git commit -m "feat: add trusted ComfyUI service contract"
```

### Task 4: Expose RBAC-Protected Workflow Library APIs

**Files:**

- Create: `server/ai_creation_canvas/api/comfy_workflows.py`
- Modify: `server/ai_creation_canvas/app.py`
- Test: `tests/server/test_comfy_workflow_api.py`
- Test: `tests/integration/test_comfy_workflow_library.py`

**Interfaces:**

- Consumes: `ComfyWorkflowLibrary` and `parse_workflow_json` from Tasks 1–2.
- Produces: the administrator and user read APIs in the approved design; `POST /api/v1/admin/comfy-workflows/import` accepts a single `file` multipart field plus `display_name` and `service_id`.
- Browser-facing list/detail/preview payloads contain only IDs, names, lifecycle, revision, safe node/edge projection, dependency state and execution availability.

- [ ] **Step 1: Write red API and isolation tests**

```python
def test_admin_imports_exports_and_assigns_without_exposing_raw_api_json(local_clients) -> None:
    app, admin, user_a, user_b, headers = local_clients
    response = admin.post("/api/v1/admin/comfy-workflows/import", headers=headers.admin, files={"file": ("core.json", FIXTURE.read_bytes(), "application/json")}, data={"display_name": "Core", "service_id": "comfy-local"})
    assert response.status_code == 201
    workflow_id = response.json()["workflow_id"]
    assert "widgets_values" not in response.text
    assert admin.get(f"/api/v1/admin/comfy-workflows/{workflow_id}/revisions/1/export?format=editor").content
    assert user_a.get("/api/v1/comfy-workflows").json()["workflows"] == []
    assert admin.put(f"/api/v1/admin/users/{headers.user_a_id}/comfy-workflows", headers=headers.admin, json={"workflow_ids": [workflow_id]}).status_code == 200
    assert [item["workflow_id"] for item in user_a.get("/api/v1/comfy-workflows").json()["workflows"]] == [workflow_id]
    assert user_b.get(f"/api/v1/comfy-workflows/{workflow_id}").status_code in {403, 404}
```

- [ ] **Step 2: Run the focused test to confirm red**

Run: `pytest -q tests/server/test_comfy_workflow_api.py tests/integration/test_comfy_workflow_library.py`

Expected: the administrator import route returns `API_NOT_FOUND`.

- [ ] **Step 3: Implement strict multipart parsing and route projections**

```python
@router.post("/admin/comfy-workflows/import", status_code=201)
async def import_workflow(request: Request) -> dict[str, object]:
    form = await _single_workflow_form(request, max_bytes=4 * 1024 * 1024)
    parsed = parse_workflow_json(await _bounded_upload_bytes(form.file))
    item = request.app.state.comfy_workflow_library.create_template(
        form.display_name, form.service_id, parsed, actor_user_id=context_for(request).user_id,
    )
    return admin_workflow_projection(item)
```

Reject non-JSON media types, multiple files, missing/unknown multipart keys and oversize streams before parsing. Set export headers from a server-generated `comfy-workflow-{workflow_id}-r{revision}-{format}.json` name. Keep raw JSON exclusively in the export response; all other handlers use the safe projection. Call `problem(..., status=404)` for non-admin callers through existing middleware and do owner checks before lookup-sensitive response data.

- [ ] **Step 4: Implement lifecycle, revision and assignment endpoints**

Use a strict body `{revision: StrictInt}` for enable/disable/archive/restore and `{workflow_ids: list[str]}` for assignment replacement. Require disabled templates before accepting a new revision; enabling requires a non-archived revision and a configured service ID. The execution status remains false until the later execution slice registers a real job submission path.

- [ ] **Step 5: Run API and existing security tests**

Run: `pytest -q tests/server/test_comfy_workflow_api.py tests/integration/test_comfy_workflow_library.py tests/server/test_local_auth.py tests/server/test_app_security.py tests/server/test_model_assignments.py`

Expected: all pass, including CSRF rejection, admin-route hiding and two-user isolation.

- [ ] **Step 6: Commit Task 4**

```bash
git add server/ai_creation_canvas/api/comfy_workflows.py server/ai_creation_canvas/app.py tests/server/test_comfy_workflow_api.py tests/integration/test_comfy_workflow_library.py
git commit -m "feat: add ComfyUI workflow library API"
```

### Task 5: Build the Administrator Library and Safe Read-Only Preview

**Files:**

- Create: `web/src/api/comfy-workflows.ts`
- Create: `web/src/components/comfy/workflow-import.tsx`
- Create: `web/src/components/comfy/workflow-preview.tsx`
- Create: `web/src/pages/admin/comfy-workflows.tsx`
- Modify: `web/src/router.tsx`
- Modify: `web/src/components/layout/product-shell.tsx`
- Test: `web/src/test/admin-comfy-workflows.test.tsx`
- Test: `web/src/test/comfy-workflow-preview.test.tsx`

**Interfaces:**

- Consumes: Task 4 endpoints and only their safe projections.
- Produces: `importAdminComfyWorkflow(file, {displayName, serviceId})`, `fetchAdminComfyWorkflows()`, `exportAdminComfyWorkflow(...)`, `WorkflowImport`, `WorkflowPreview`, and administrator route `/admin/comfy-workflows`.
- File selection retains only the `File` object until upload; preview consumes `PreviewGraph`, not raw JSON.

- [ ] **Step 1: Write red browser tests**

```tsx
it("uploads workflow JSON without reading it in the browser", async () => {
  const file = new File(['{"api_key":"must-not-be-read"}'], "workflow.json", { type: "application/json" });
  const text = vi.spyOn(file, "text");
  render(<WorkflowImport onImport={vi.fn().mockResolvedValue(workflow)} />);
  fireEvent.change(screen.getByLabelText("选择工作流 JSON"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "导入工作流" }));
  await waitFor(() => expect(text).not.toHaveBeenCalled());
});

it("renders only generic node labels and never injects a widget value", () => {
  render(<WorkflowPreview preview={{ has_editor_layout: true, nodes: [{ id: "1", type: "LoadImage", title: "<img src=x>" }], edges: [] }} />);
  expect(screen.getByText("LoadImage")).toBeVisible();
  expect(document.querySelector("img")).toBeNull();
});
```

- [ ] **Step 2: Run the focused tests to confirm red**

Run: `npm test --prefix web -- --run src/test/admin-comfy-workflows.test.tsx src/test/comfy-workflow-preview.test.tsx`

Expected: compilation fails because the ComfyUI library components do not exist.

- [ ] **Step 3: Implement clients and import UI**

```ts
export function importAdminComfyWorkflow(file: File, metadata: { displayName: string; serviceId: string }) {
  const body = new FormData();
  body.set("file", file, file.name);
  body.set("display_name", metadata.displayName);
  body.set("service_id", metadata.serviceId);
  return apiFetch<AdminComfyWorkflow>("/api/v1/admin/comfy-workflows/import", { method: "POST", body });
}
```

Do not call `file.text()`, `FileReader`, `URL.createObjectURL` or browser storage. Disable duplicate submits, clear the input only after success, and render generic error copy rather than server detail. Trigger export through the typed same-origin endpoint and a Blob download using the server’s safe filename.

- [ ] **Step 4: Implement the preview, page and route**

For `has_editor_layout`, render an SVG with escaped text nodes, clamped coordinates and a maximum 500 rendered boxes/2,000 edges. Otherwise render a semantic node/edge summary table. Add “工作流库” to the admin navigation, route it through `RoleGate`, and show formats, checksum prefix, dependency state, revision, lifecycle and user assignments without API JSON or endpoint configuration.

- [ ] **Step 5: Run focused web tests and typecheck**

Run: `npm test --prefix web -- --run src/test/admin-comfy-workflows.test.tsx src/test/comfy-workflow-preview.test.tsx src/test/admin-pages.test.tsx && npm run lint --prefix web && npm run build --prefix web`

Expected: all tests, lint and production build pass.

- [ ] **Step 6: Commit Task 5**

```bash
git add web/src/api/comfy-workflows.ts web/src/components/comfy web/src/pages/admin/comfy-workflows.tsx web/src/router.tsx web/src/components/layout/product-shell.tsx web/src/test/admin-comfy-workflows.test.tsx web/src/test/comfy-workflow-preview.test.tsx
git commit -m "feat: add ComfyUI workflow library UI"
```

### Task 6: Register One Static Canvas Workflow Node

**Files:**

- Create: `web/src/features/nodes/comfy-workflow.tsx`
- Modify: `web/src/types/canvas.ts`
- Modify: `web/src/features/graph/contracts.ts`
- Modify: `web/src/features/graph/normalize-project.ts`
- Modify: `web/src/features/nodes/builtins.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/comfy-workflow-node.test.tsx`
- Test: `web/src/test/extension-registry.test.ts`
- Test: `web/src/test/canvas-connections.test.tsx`

**Interfaces:**

- Produces: persisted `GraphComfyWorkflowMetadata` with `workflowId`, `workflowRevision`, declared input ports, output port and `executionEnabled`.
- Produces exactly one static node definition: `{ id: "comfy.workflow", version: 1, showInCreateMenu: true }`.
- The node can be placed and connected as data, but its run control is disabled with a clear “执行将在 ComfyUI 执行切片启用” explanation.

- [ ] **Step 1: Write red node/connection tests**

```tsx
it("registers one generic ComfyUI node without registering imported node types", () => {
  expect(nodeRegistry.getNode("comfy.workflow")).toMatchObject({ title: "ComfyUI 工作流", version: 1 });
  expect(nodeRegistry.getNode("MiniMaxH3ImageToVideo")).toBeUndefined();
});

it("preserves a selected template revision and leaves execution disabled", () => {
  const node = createComfyWorkflowNode({ workflowId: "wf-1", revision: 2, title: "Core", inputs: [], executionEnabled: false });
  expect(node.metadata?.graph).toMatchObject({ role: "comfy-workflow", workflowId: "wf-1", workflowRevision: 2, executionEnabled: false });
});
```

- [ ] **Step 2: Run focused tests to confirm red**

Run: `npm test --prefix web -- --run src/test/comfy-workflow-node.test.tsx src/test/extension-registry.test.ts src/test/canvas-connections.test.tsx`

Expected: tests fail because `comfy.workflow` and `GraphComfyWorkflowMetadata` are absent.

- [ ] **Step 3: Add static metadata, ports and renderer**

```ts
export type GraphComfyWorkflowMetadata = {
  schemaVersion: typeof GRAPH_SCHEMA_VERSION;
  role: "comfy-workflow";
  workflowId: string;
  workflowRevision: number;
  inputPorts: GraphInputPortDescriptor[];
  outputPortId: string;
  executionEnabled: boolean;
};
```

Extend the discriminated `CanvasGraphNodeMetadata` union and its normalizer without changing existing model-node serialization. Define `comfy.workflow` in a statically imported local module using only validated port descriptors. Add a card to `CanvasProjectPage` that shows the fixed template title/revision and does not fetch raw JSON, resolve URLs or submit `/api/v1/jobs`.

- [ ] **Step 4: Run focused canvas tests and build**

Run: `npm test --prefix web -- --run src/test/comfy-workflow-node.test.tsx src/test/extension-registry.test.ts src/test/canvas-connections.test.tsx src/test/canvas-store.test.ts && npm run build --prefix web`

Expected: all pass; existing project documents still normalize and render.

- [ ] **Step 5: Commit Task 6**

```bash
git add web/src/features/nodes/comfy-workflow.tsx web/src/types/canvas.ts web/src/features/graph/contracts.ts web/src/features/graph/normalize-project.ts web/src/features/nodes/builtins.tsx web/src/pages/canvas/project.tsx web/src/test/comfy-workflow-node.test.tsx web/src/test/extension-registry.test.ts web/src/test/canvas-connections.test.tsx
git commit -m "feat: add generic ComfyUI canvas node"
```

### Task 7: Verify the Complete Slice and the Three Workflow Round Trips

**Files:**

- Create: `scripts/verify-comfy-workflow-roundtrip.py`
- Modify: `docs/verification.md`
- Test: `tests/integration/test_comfy_workflow_library.py`

**Interfaces:**

- Consumes: `parse_workflow_json`, `export_workflow`, the Task 4 API, and the authored core fixture.
- Produces: an exit-0 command that prints only file basename, detected format, checksum prefix, node count and link count; it never prints widgets, prompts or full JSON.

- [ ] **Step 1: Write a failing integration/CLI test**

```python
def test_roundtrip_cli_reports_only_safe_summary(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/verify-comfy-workflow-roundtrip.py", str(FIXTURE)],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0
    assert "core-load-save-workflow.json" in completed.stdout
    assert "LoadImage" not in completed.stdout
    assert "widgets_values" not in completed.stdout
```

- [ ] **Step 2: Run the test to confirm red**

Run: `pytest -q tests/integration/test_comfy_workflow_library.py`

Expected: fails because the verification script does not exist.

- [ ] **Step 3: Implement the no-write acceptance script and verification instructions**

```python
for path in map(Path, arguments.paths):
    parsed = parse_workflow_json(path.read_bytes())
    for format in sorted(parsed.formats):
        reloaded = parse_workflow_json(export_workflow(parsed, format))
        if reloaded.checksum != parsed.checksum:
            raise SystemExit(1)
    print(f"{path.name}: format={','.join(sorted(parsed.formats))} checksum={parsed.checksum[:12]} nodes={parsed.node_count} links={parsed.link_count}")
```

Document the command with the repository-authored fixture plus the two exact user paths. State that the two user files are manual local acceptance inputs only: no automated test may read them, and they must not be copied or committed. Also state that successful local format verification does not prove custom node/model availability or run a ComfyUI task.

- [ ] **Step 4: Run all release-gate checks**

Run:

```bash
pytest -q tests/server/test_comfy_workflow_json.py tests/server/test_comfy_workflow_store.py tests/server/test_comfy_service.py tests/server/test_comfy_workflow_api.py tests/integration/test_comfy_workflow_library.py tests/server/test_app_security.py tests/server/test_model_assignments.py
npm test --prefix web -- --run src/test/admin-comfy-workflows.test.tsx src/test/comfy-workflow-preview.test.tsx src/test/comfy-workflow-node.test.tsx src/test/extension-registry.test.ts src/test/canvas-connections.test.tsx
npm run lint --prefix web
npm run build --prefix web
python scripts/verify-comfy-workflow-roundtrip.py tests/fixtures/comfy/core-load-save-workflow.json '/Users/260413a/Downloads/▶▷MiniMaxH3-加速视频流整合.json' '/Users/260413a/Downloads/贝尔尼尼Bernini+Studio工作流.json'
```

Expected: every command exits 0; the final command reports the two supplied files as editor format with 145/152 and 24/28 node/link counts, and reports only safe summaries.

- [ ] **Step 5: Perform isolated manual smoke verification**

Start only the Canvas test service on `8992` using an empty temporary data directory. Log in as administrator, import/export all three workflows, preview both supplied saved graphs, assign the authored workflow to one test user, and confirm another test user cannot list or fetch it. Record the commands/results in `docs/verification.md`; do not copy the external JSON files into the repository.

- [ ] **Step 6: Commit Task 7**

```bash
git add scripts/verify-comfy-workflow-roundtrip.py docs/verification.md tests/integration/test_comfy_workflow_library.py
git commit -m "test: verify ComfyUI workflow round trips"
```

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 implement format protection, immutable revisions and assignments; Task 3 implements the server-only service boundary; Task 4 adds RBAC APIs; Task 5 provides library import/preview/export; Task 6 integrates a single static canvas node; Task 7 verifies the authored and user-provided workflows without committing third-party assets.
- **Placeholder scan:** No task contains unresolved implementation markers, implicit test work or an unspecified error-handling step; every task names concrete files, interfaces, commands and expected outcomes.
- **Type consistency:** `ParsedWorkflow` flows from parser to library; `ComfyWorkflowLibrary` is owned by app state and consumed only by routes; `ComfyWorkflowServicePort` remains separate from `GenerationPort`; the front end consumes only workflow projections and `GraphComfyWorkflowMetadata`.
