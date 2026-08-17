# 已配置 ComfyUI 执行与生产操作指引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员在服务器端安全配置 Key、内置 Prompt Skill、Portal 人像声明和 ComfyUI 后，能离线验证并使用已审核、已派发的 ComfyUI 工作流，同时保留 Portal 人像合同复用边界。

**Architecture:** ComfyUI 保持独立的受信服务端口。新的执行配置、运行记录和后台轮询服务只提交不可变 API revision，且只处理严格声明的文字与图片绑定；结果先安全拉回 Canvas 私有存储，再以 owner 受保护的同源 URL 提供。生产配置由仓库外的常规文件和服务账号环境变量组成，预检不发任何外部请求。

**Tech Stack:** Python 3.12、FastAPI、SQLite、httpx、React/TypeScript、Vitest、pytest。

## Global Constraints

- 不读取、修改或重启 `/Users/260413a/ai-generation-portable-apps`、生产状态、密钥、日志、输出或 `9090/8787/8797/8891` 等生产端口。
- 浏览器、数据库公开投影、日志、Git、错误体和命令行不得含真实 Key、Cookie、ComfyUI 地址、完整 API 工作流、完整提示词或长期结果地址。
- 所有自动化使用 `httpx.MockTransport`、仓库夹具、测试端口 `8992` 和临时数据目录；不连接真实 Ark、Portal 或 ComfyUI。
- ComfyUI 不实现任意 URL、插件、脚本、节点类型、文件路径或未审核 JSON 的用户输入；执行只基于已启用、已派发、不可变的 API revision 与白名单 profile。
- Portal 人像仅复用 `portal-virtual-v1` 的适配器/合同；不复制或导入 Portal 人像业务实现。
- 所有数据库迁移加法式；未知提交永久 `submission_unknown`，不得重放；跨用户资源返回 404。
- 新增生产操作必须能在发布包（无 Node/Bun）中运行，且通过 `--check-config` 不发起网络请求。

---

## File Structure

- `server/ai_creation_canvas/config.py`：服务器端 Comfy 认证环境解析、文件/秘密预检。
- `server/ai_creation_canvas/__main__.py`：生产 CLI 的安全令牌来源与离线检查输出。
- `server/ai_creation_canvas/comfy/service.py`：严格的 Comfy 图片上传、历史输出验证与受信文件读取。
- `server/ai_creation_canvas/comfy/execution.py`：不可变 execution profile 编译、运行服务与轮询。
- `server/ai_creation_canvas/storage/sqlite.py`：profile、运行、输出元数据和本地结果文件的原子持久化。
- `server/ai_creation_canvas/api/comfy_workflows.py`：管理员 profile 管理和准确的可运行性投影。
- `server/ai_creation_canvas/api/comfy_runs.py`：用户提交、读取、取消 Comfy 运行及同源结果。
- `server/ai_creation_canvas/app.py`：装配 resolver、执行服务、后台轮询与新 API。
- `web/src/api/comfy-workflows.ts`、`web/src/api/comfy-runs.ts`：安全的管理员/profile 与普通用户运行 API 客户端。
- `web/src/features/nodes/comfy-workflow.tsx`、`web/src/pages/canvas/project.tsx`：静态 Comfy 节点的配置输入、运行、恢复和结果节点回填。
- `docs/production-configuration.md`、`server/config/production-secrets.example.env`：唯一生产操作路径与零秘密模板。
- `scripts/verify-configured-services.py`：只启动 mock 服务的端到端离线冒烟。

## Task 1: 服务端秘密解析与离线生产预检

**Files:**
- Create: `server/ai_creation_canvas/config_secrets.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/__main__.py`
- Modify: `server/ai_creation_canvas/app.py`
- Test: `tests/server/test_config_secrets.py`
- Test: `tests/server/test_cli.py`

**Interfaces:**
- Produces `resolve_comfy_authorization(reference: str, environ: Mapping[str, str]) -> Mapping[str, str] | None`.
- Produces `read_secret_environment(name: str, environ: Mapping[str, str]) -> str | None`, rejecting unset, whitespace-only and control-character values.
- Extends `Settings` with `portal_internal_token` resolved from `AICC_PORTAL_INTERNAL_TOKEN` when an explicit compatibility argument is absent.
- `create_app(..., comfy_auth_header_resolver=...)` receives the resolver only from server wiring.

- [ ] **Step 1: Write failing secret-resolution and no-leak tests**

```python
def test_comfy_authorization_uses_only_a_server_environment_reference():
    env = {"AICC_COMFY_AUTH_RENDER_NODE": "Bearer secret-never-returned"}
    assert resolve_comfy_authorization("render-node", env) == {
        "Authorization": "Bearer secret-never-returned"
    }

def test_preflight_rejects_missing_comfy_reference_without_echoing_value(tmp_path, capsys):
    code = main_for_test(production_args(tmp_path, auth_ref="render-node"), environ={})
    assert code != 0
    assert "render-node" not in capsys.readouterr().err
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_config_secrets.py tests/server/test_cli.py`

Expected: FAIL because no secret resolver or environment-backed production preflight exists.

- [ ] **Step 3: Implement a fixed environment-name mapping and preflight**

```python
def comfy_authorization_environment_name(reference: str) -> str:
    if _AUTH_REFERENCE.fullmatch(reference) is None:
        raise ValueError("ComfyUI authentication reference is invalid")
    return "AICC_COMFY_AUTH_" + re.sub(r"[^A-Za-z0-9]", "_", reference).upper()

def resolve_comfy_authorization(reference: str, environ: Mapping[str, str]) -> Mapping[str, str] | None:
    value = read_secret_environment(comfy_authorization_environment_name(reference), environ)
    return {"Authorization": value} if value is not None else None
```

Use `os.environ` only in the production composition root. Keep existing explicit `--portal-internal-token` support, but prefer `AICC_PORTAL_INTERNAL_TOKEN`; reject the ambiguous case where both have distinct values. During `--check-config`, instantiate services and validate every declared `auth_header_ref`, `ARK_API_KEY`/`--prompt-skill-model` pair and static directory without calling health, prompt, Comfy, Ark or Portal endpoints. Do not print variable values or references.

- [ ] **Step 4: Run focused checks**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_config_secrets.py tests/server/test_cli.py tests/server/test_comfy_service.py`

Expected: PASS; malformed names, missing values, conflicting token sources and weak/unsafe secret values fail safely.

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/config_secrets.py server/ai_creation_canvas/config.py server/ai_creation_canvas/__main__.py server/ai_creation_canvas/app.py tests/server/test_config_secrets.py tests/server/test_cli.py
git commit -m "feat: preflight configured service secrets"
```

## Task 2: 严格 ComfyUI 输入上传与输出文件端口

**Files:**
- Modify: `server/ai_creation_canvas/comfy/service.py`
- Modify: `server/ai_creation_canvas/domain/ports.py`
- Test: `tests/server/test_comfy_service.py`
- Test: `tests/contracts/test_comfy_execution_adapter.py`

**Interfaces:**
- Extends `ComfyWorkflowServicePort` with `upload_image(context, source: Path, size_bytes: int, mime_type: str, upload_name: str) -> str` and `read_output(context, descriptor: ComfyOutputDescriptor) -> AsyncByteStream`.
- Produces `ComfyOutputDescriptor(filename: str, subfolder: str, kind: Literal["output", "temp"], mime_type: str)` after exact history validation.
- `ComfyHttpWorkflowService.poll_outputs(context, prompt_id) -> tuple[ComfyOutputDescriptor, ...]` returns only terminal, bounded standard output descriptors.

- [ ] **Step 1: Write failing Comfy protocol and path-safety tests**

```python
async def test_history_success_yields_only_safe_output_descriptors(service, context):
    descriptors = await service.poll_outputs(context, "prompt-1")
    assert [(item.filename, item.subfolder, item.kind) for item in descriptors] == [("result.png", "", "output")]

async def test_upload_rejects_symlink_and_never_sends_a_user_filename(service, context, tmp_path):
    source = tmp_path / "input.png"; source.write_bytes(PNG)
    uploaded = await service.upload_image(context, source, len(PNG), "image/png", "run-a.png")
    assert uploaded == "run-a.png"
```

Cover `..`, slashes, percent encodings, invalid MIME, more than 16 outputs, oversized streamed response, non-image history fields, response URLs and an output from another prompt. Assert request paths are only `/upload/image`, `/history/{id}` and `/view` with encoded safe query values.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_comfy_execution_adapter.py tests/server/test_comfy_service.py`

Expected: FAIL because image upload/output reader methods do not exist.

- [ ] **Step 3: Implement narrow transfer methods**

```python
@dataclass(frozen=True, slots=True)
class ComfyOutputDescriptor:
    filename: str
    subfolder: str
    kind: str
    mime_type: str

async def upload_image(...):
    # fstat regular no-follow input, exact declared size, fixed generated filename
    response = await self._request("POST", "/upload/image", files={"image": (...)})
    return self._validated_uploaded_name(self._json_object(response), upload_name)
```

Accept history output only from `outputs` mappings for the owned prompt, only `images`/`gifs` arrays of exact descriptor keys, and only fixed image/video MIME extensions. Stream `/view` to the caller; do not buffer an unbounded file, return an upstream URL or follow redirects. Keep prompt-owner verification before every history/read/cancel request.

- [ ] **Step 4: Run focused checks**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_comfy_execution_adapter.py tests/server/test_comfy_service.py tests/server/test_registry.py`

Expected: PASS; service remains outside `GenerationPort`.

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/comfy/service.py server/ai_creation_canvas/domain/ports.py tests/contracts/test_comfy_execution_adapter.py tests/server/test_comfy_service.py
git commit -m "feat: add bounded ComfyUI media transfer"
```

## Task 3: 不可变执行配置与 SQLite Comfy 运行记录

**Files:**
- Create: `server/ai_creation_canvas/comfy/execution.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Modify: `server/ai_creation_canvas/comfy/library.py`
- Test: `tests/server/test_comfy_execution_store.py`
- Test: `tests/server/test_comfy_execution_compile.py`

**Interfaces:**
- Produces `ExecutionProfile(workflow_id, workflow_revision, profile_revision, text_bindings, image_bindings, output_limit)`.
- Produces `compile_comfy_request(api_workflow, profile, public_inputs, owned_assets) -> Mapping[str, object]` without mutating the saved workflow.
- Produces `ComfyRunService.submit(context, workflow_id, revision, profile_revision, inputs, idempotency_key) -> ComfyRun`.
- `CanvasStore` provides `set_comfy_execution_profile`, `reserve_comfy_run`, `mark_comfy_run_submitted`, `claim_comfy_runs`, `complete_comfy_run`, `cancel_comfy_run`, and owner-filtered getters.

- [ ] **Step 1: Write failing compiler and persistence tests**

```python
def test_profile_compiler_changes_only_reviewed_text_and_image_inputs():
    compiled = compile_comfy_request(API, profile(prompt_node="6", image_node="4"), {"prompt": "cat", "image_asset_id": "asset-a"}, {"asset-a": "run.png"})
    assert compiled["6"]["inputs"]["text"] == "cat"
    assert compiled["4"]["inputs"]["image"] == "run.png"
    assert API["6"]["inputs"]["text"] == "original"

def test_two_users_cannot_share_or_replay_comfy_runs(store):
    first, created = store.reserve_comfy_run(user_id="a", idempotency_key="same", ...)
    second, reused = store.reserve_comfy_run(user_id="b", idempotency_key="same", ...)
    assert created and not reused and first["run_id"] != second["run_id"]
```

Cover profile target-node existence/type, disallowed value kinds, API revision absence, disabled/archived templates, duplicate bindings, oversized text, non-owner asset, concurrent same owner key, completed re-read, submission unknown, crash after upload but before `/prompt`, and profile changes after submission.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_comfy_execution_store.py tests/server/test_comfy_execution_compile.py`

Expected: FAIL because profiles, compiler and run records do not exist.

- [ ] **Step 3: Add additive schema and pure compiler**

```sql
CREATE TABLE IF NOT EXISTS canvas_comfy_execution_profiles (
  workflow_id TEXT NOT NULL, workflow_revision INTEGER NOT NULL,
  profile_revision INTEGER NOT NULL, profile_json TEXT NOT NULL,
  enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (workflow_id, workflow_revision, profile_revision)
);
CREATE TABLE IF NOT EXISTS canvas_comfy_runs (
  run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
  workflow_revision INTEGER NOT NULL, profile_revision INTEGER NOT NULL,
  service_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, status TEXT NOT NULL,
  prompt_id TEXT, submission_state TEXT NOT NULL, request_digest TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(user_id, idempotency_key)
);
```

Store public inputs only as a bounded hashed summary, not full prompts. Store output identifiers only after they have been copied into private Canvas files. The compiler constructs a deep fresh API object from the stored revision, accepts text (`1..8000`) and image asset references only, and applies values exactly once to explicitly listed nodes/keys. It must never merge caller JSON.

- [ ] **Step 4: Run focused checks**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_comfy_execution_store.py tests/server/test_comfy_execution_compile.py tests/server/test_comfy_workflow_store.py`

Expected: PASS; schema migration retains existing workflow and prompt-owner rows.

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/comfy/execution.py server/ai_creation_canvas/comfy/library.py server/ai_creation_canvas/storage/sqlite.py tests/server/test_comfy_execution_store.py tests/server/test_comfy_execution_compile.py
git commit -m "feat: persist immutable ComfyUI executions"
```

## Task 4: Comfy 运行 API、后台轮询与受保护结果

**Files:**
- Create: `server/ai_creation_canvas/api/comfy_runs.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/comfy/execution.py`
- Modify: `server/ai_creation_canvas/storage/sqlite.py`
- Test: `tests/server/test_comfy_run_api.py`
- Test: `tests/integration/test_comfy_workflow_execution.py`

**Interfaces:**
- `POST /api/v1/comfy-workflows/{workflow_id}/runs` accepts `{revision, profile_revision, inputs, idempotency_key}` and returns safe `ComfyRunProjection`.
- `GET /api/v1/comfy-runs/{run_id}`, `POST /api/v1/comfy-runs/{run_id}/cancel`, `GET|HEAD /api/v1/comfy-runs/{run_id}/results/{index}` enforce owner-before-lookup.
- `ComfyRunPollingService.run_once() -> int` processes only durable submitted prompt IDs and writes private result files before `succeeded`.

- [ ] **Step 1: Write failing API/integration tests**

```python
def test_enabled_assigned_profile_runs_once_and_returns_private_result(local_clients, mock_comfy):
    run = local_clients.user_a.post(f"/api/v1/comfy-workflows/{workflow_id}/runs", json=run_body()).json()
    assert local_clients.user_a.post(f"/api/v1/comfy-workflows/{workflow_id}/runs", json=run_body()).json()["id"] == run["id"]
    app.state.comfy_run_poller.run_once()
    assert local_clients.user_a.get(run["results"][0]["url"]).status_code == 200
    assert local_clients.user_b.get(f"/api/v1/comfy-runs/{run['id']}").status_code == 404
```

Cover CSRF, unassigned/disabled/archived/not-profiled workflows, bad input shape, non-owned asset, missing/incompatible service, duplicate and collision prompt IDs, restart after submit, failed/completed history, cancellation, unsafe view descriptor, oversized output, range/HEAD, and a transport failure after `/prompt` that is terminal `submission_unknown` with exactly one POST.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_comfy_run_api.py tests/integration/test_comfy_workflow_execution.py`

Expected: FAIL because no run endpoints or polling service exist.

- [ ] **Step 3: Implement request-safe orchestration**

```python
@router.post("/comfy-workflows/{workflow_id}/runs", status_code=201)
async def submit_run(workflow_id: str, body: ComfyRunSubmission, request: Request):
    context = context_for(request)
    return await request.app.state.comfy_run_service.submit(
        context, workflow_id, body.revision, body.profile_revision, body.inputs, body.idempotency_key
    )
```

Construct the service in `create_app`, include the router, and start/stop its bounded poller alongside the app lifespan. The poller must claim a run token before I/O, load the service only from trusted `service_id`, require prompt ownership, stream every accepted output to a 0700 private results directory via `O_NOFOLLOW`, validate header/MIME/size, atomically finalize rows, and release/fail leases on error. The HTTP result route only streams local files with the owner check; it never asks ComfyUI during user result reads.

- [ ] **Step 4: Run focused checks**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_comfy_run_api.py tests/integration/test_comfy_workflow_execution.py tests/server/test_results_api.py tests/server/test_app_security.py`

Expected: PASS; no external network is contacted.

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/api/comfy_runs.py server/ai_creation_canvas/app.py server/ai_creation_canvas/comfy/execution.py server/ai_creation_canvas/storage/sqlite.py tests/server/test_comfy_run_api.py tests/integration/test_comfy_workflow_execution.py
git commit -m "feat: execute assigned ComfyUI workflows safely"
```

## Task 5: 管理员执行配置与用户画布节点

**Files:**
- Modify: `server/ai_creation_canvas/api/comfy_workflows.py`
- Modify: `web/src/api/comfy-workflows.ts`
- Create: `web/src/api/comfy-runs.ts`
- Modify: `web/src/pages/admin/comfy-workflows.tsx`
- Modify: `web/src/features/nodes/comfy-workflow.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `tests/server/test_comfy_workflow_api.py`
- Test: `web/src/test/admin-comfy-workflows.test.tsx`
- Test: `web/src/test/comfy-workflow-node.test.tsx`
- Test: `web/src/test/comfy-workflow-run.test.tsx`

**Interfaces:**
- `PUT /api/v1/admin/comfy-workflows/{workflow_id}/revisions/{revision}/execution-profile` accepts only strict `{text_inputs, image_inputs, output_limit, expected_profile_revision}`.
- Public workflow projection includes `execution_available`, `execution_unavailable_reason`, `profile_revision` and safe input descriptors, never bindings/API JSON/service address.
- `createComfyWorkflowNode()` accepts only `workflowId`, `workflowRevision`, `profileRevision`, public input schema and `runId` metadata.

- [ ] **Step 1: Write failing admin and canvas tests**

```tsx
it("runs an assigned configured workflow and restores its private result", async () => {
  renderProject({ workflows: [configuredWorkflow] });
  await user.click(screen.getByRole("button", { name: "运行 ComfyUI 工作流" }));
  expect(await screen.findByTestId("result-node-comfy-run-1")).toBeVisible();
});

it("does not render raw API JSON, endpoint, credential or bindings", async () => {
  render(<AdminComfyWorkflowsPage />);
  expect(screen.queryByText(/api_key|endpoint|class_type/i)).toBeNull();
});
```

Cover disable while running, conflict revisions, profile edits rejected for enabled revision until disable, assignment filtering, portal assignment-unavailable behavior, no admin requests/navigation for normal users, node `runId` restore causes GET only, and an unassigned injected node cannot submit.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `npm test --prefix web -- --run src/test/admin-comfy-workflows.test.tsx src/test/comfy-workflow-node.test.tsx src/test/comfy-workflow-run.test.tsx`

Expected: FAIL because profiles/runs are not exposed or handled by the UI.

- [ ] **Step 3: Implement safe projections and static UI**

```ts
export type AssignedComfyWorkflow = {
  workflow_id: string;
  revision: number;
  profile_revision?: number;
  execution_available: boolean;
  inputs: ReadonlyArray<{ id: string; kind: "text" | "image"; required: boolean }>;
};
```

Admin UI edits only declarative labels/types bound to existing server-confirmed inputs; it never reads uploaded workflow content or submits node IDs that the server has not independently validated. Canvas creates no dynamic node component. It uses the new same-origin API client, stores only safe run metadata, polls by `runId`, and creates a regular protected result node only after a successful projection supplies a same-origin URL.

- [ ] **Step 4: Run focused checks**

Run: `npm test --prefix web -- --run src/test/admin-comfy-workflows.test.tsx src/test/comfy-workflow-node.test.tsx src/test/comfy-workflow-run.test.tsx src/test/canvas-connections.test.tsx && npm run lint --prefix web && npm run build --prefix web`

Expected: PASS; static node security/connection behavior remains unchanged outside the new run path.

- [ ] **Step 5: Commit**

```bash
git add server/ai_creation_canvas/api/comfy_workflows.py web/src/api/comfy-workflows.ts web/src/api/comfy-runs.ts web/src/pages/admin/comfy-workflows.tsx web/src/features/nodes/comfy-workflow.tsx web/src/pages/canvas/project.tsx tests/server/test_comfy_workflow_api.py web/src/test/admin-comfy-workflows.test.tsx web/src/test/comfy-workflow-node.test.tsx web/src/test/comfy-workflow-run.test.tsx
git commit -m "feat: run configured ComfyUI workflows from canvas"
```

## Task 6: 人像 Portal 合同对标与生产操作手册

**Files:**
- Create: `docs/production-configuration.md`
- Create: `server/config/production-secrets.example.env`
- Create: `scripts/verify-configured-services.py`
- Modify: `README.md`
- Modify: `docs/installation.md`
- Modify: `docs/operations.md`
- Modify: `docs/verification.md`
- Modify: `tests/contracts/test_portrait_adapter.py`
- Modify: `tests/server/test_installation_docs.py`
- Test: `tests/integration/test_configured_services_smoke.py`

**Interfaces:**
- `python scripts/verify-configured-services.py --data-dir <temporary-directory>` exits zero only after mock Skill, Comfy execution, two-user isolation and Portal portrait contract paths pass.
- `production-secrets.example.env` contains only variable names and placeholders; it is never loaded by the browser or committed with a value.

- [ ] **Step 1: Write failing documentation, mock-smoke and portrait-contract tests**

```python
def test_production_guide_contains_all_required_server_only_inputs():
    text = (ROOT / "docs/production-configuration.md").read_text()
    for value in ("AICC_PORTAL_INTERNAL_TOKEN", "ARK_API_KEY", "AICC_CREDENTIAL_HMAC_KEY", "AICC_COMFY_AUTH_"):
        assert value in text

def test_mock_smoke_never_uses_real_network_or_production_paths(tmp_path):
    result = run_smoke(tmp_path)
    assert result.returncode == 0
    assert "secret" not in result.stdout.lower()
```

Add portrait assertions that malformed group/asset/job responses, Cookie absence, 401/403 and recovery records retain existing request-scoped/no-replay semantics. The test must not import Portal application code.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_installation_docs.py tests/contracts/test_portrait_adapter.py tests/integration/test_configured_services_smoke.py`

Expected: FAIL because the unified guide, template and mock smoke do not exist.

- [ ] **Step 3: Write executable operator instructions and isolated smoke**

Document exact commands to build a release, create `/srv/aicc/{data,config,secrets}` with safe permissions, copy JSON templates outside Git, place environment data in a 0600 service-manager file, run `--check-config`, start loopback Canvas, use admin import/profile/enable, run a core workflow as user A, prove user B receives 404, backup, disable and roll back. Clearly label every external service instruction as deployment preparation, not a real call in this repository.

The smoke script uses a fresh `TemporaryDirectory`, mock httpx transport, fixture API workflow and two local identities. It verifies only loopback/test service behavior, then removes its data directory. It must not accept a URL, secret argument or production path.

- [ ] **Step 4: Run focused checks**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_installation_docs.py tests/contracts/test_portrait_adapter.py tests/integration/test_configured_services_smoke.py && PYTHONPATH=.:server .venv/bin/python scripts/verify-configured-services.py --data-dir "$(mktemp -d)/aicc-configured-smoke"`

Expected: PASS; output contains only safe summaries and temporary data is removed.

- [ ] **Step 5: Commit**

```bash
git add docs/production-configuration.md server/config/production-secrets.example.env scripts/verify-configured-services.py README.md docs/installation.md docs/operations.md docs/verification.md tests/contracts/test_portrait_adapter.py tests/server/test_installation_docs.py tests/integration/test_configured_services_smoke.py
git commit -m "docs: add configured service production guide"
```

## Task 7: 完整离线发布验收与完成审计

**Files:**
- Create: `docs/superpowers/reports/2026-08-17-configured-comfy-execution-report.md`
- Modify: `docs/verification.md`
- Test: `tests/integration/test_comfy_workflow_library.py`

**Interfaces:**
- The report records commands, safe counts/statuses and each excluded boundary; it contains no secret, prompt, media, URL or user data.

- [ ] **Step 1: Add the final integration regression**

```python
def test_core_and_explicit_user_workflows_round_trip_without_external_io(tmp_path):
    summaries = verify_paths(CORE_FIXTURE, minimax_path, bernini_path)
    assert [(s.format, s.nodes, s.links) for s in summaries] == [
        ("editor", 2, 1), ("editor", 145, 152), ("editor", 24, 28)
    ]
```

The explicit local paths are accepted only from a manual CLI invocation; pytest retains only the core fixture. Check that no test has a Downloads dependency or skip branch.

- [ ] **Step 2: Run all required gates**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests
npm test --prefix web
npm run lint --prefix web
npm run typecheck --prefix web
npm run build --prefix web
bash scripts/security-scan.sh
PYTHONPATH=.:server .venv/bin/python scripts/verify-comfy-workflow-roundtrip.py tests/fixtures/comfy/core-load-save-workflow.json '/Users/260413a/Downloads/▶▷MiniMaxH3-加速视频流整合.json' '/Users/260413a/Downloads/贝尔尼尼Bernini+Studio工作流.json'
PYTHONPATH=.:server .venv/bin/python scripts/verify-configured-services.py --data-dir "$(mktemp -d)/aicc-configured-smoke"
git diff --check
```

Expected: every available gate passes; existing non-fatal bundle-size warnings are recorded separately from failures.

- [ ] **Step 3: Write the completion audit**

Map every requirement in `2026-08-17-configured-services-and-comfy-execution-design.md` to a test, command, source path or explicitly excluded boundary. State separately that real ComfyUI/Portal/Ark connectivity, actual Key validation, public deployment and cross-service integration were not attempted by design.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/reports/2026-08-17-configured-comfy-execution-report.md docs/verification.md tests/integration/test_comfy_workflow_library.py
git commit -m "test: verify configured ComfyUI delivery"
```
