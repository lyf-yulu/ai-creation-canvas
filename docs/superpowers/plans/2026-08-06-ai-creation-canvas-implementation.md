# AI 创作画布 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建可独立维护、经 Portal 安全访问的 AI 无限画布验证版，覆盖图像、视频和人像资产视频工作流，并为新增模型、节点和工作流保留稳定接口。

**Architecture:** 前端从 Infinite Canvas 固定提交导入 React/Vite 画布能力，删除浏览器密钥、远程插件和动态模型脚本，改为调用同源 `/api/v1`。Python FastAPI 服务提供静态资源、Portal 身份验证、模型目录、资产与任务接口；具体 Portal 路由通过适配器注册，核心只依赖领域接口。生产访问经 Portal 薄代理，生成请求仍穿过 Portal，从而复用其服务端密钥、任务归属和用量统计。

**Tech Stack:** React 19.2.5、React Router 7.12、Vite 7.3、TypeScript 5、Ant Design 6.4.2、Zustand 5、localforage 1.10；Python 3.12、FastAPI、Uvicorn、httpx、python-multipart、Pydantic、SQLite；Vitest、Testing Library、pytest。

## Global Constraints

- Infinite Canvas 固定为 `9bccd0ff1a7057a835708a731644ab05371fea3b`，许可证为 AGPL-3.0。
- 新仓库为 `/Users/260413a/ai-creation-canvas`，运行时不得从原生产仓库导入源码或读取状态。
- 不修改生产目录，不操作 launchd，不重启或停止 `9090/8787/8797/8891`。
- 测试端口固定为画布 `8992`、Portal `9190`、图像 `8798`、视频 `8788`、人像 `8892`，只使用测试目录。
- 开发和发布阶段允许 Node；生产运行只使用 Python 与预构建静态文件，不依赖 Node/Bun。
- 浏览器不保存 API Key，不允许运行远程插件、动态模型脚本或指定任意后端 URL。
- 浏览器数据按“环境 + Portal 用户 ID”隔离；资产、任务、结果和用量校验用户归属。
- 服务边界是图像生成、视频生成和资产能力；Nano Banana、Seedance 不能写入核心分支。
- 人像是“上传资产 → `asset_id` → 通用视频任务”的受控工作流，不是第三个核心服务。
- 新模型、节点和工作流只通过注册表与稳定接口接入。
- 密钥、状态、输出、归档、上传、日志、真实请求和证书不得提交。
- 每个任务按 TDD 顺序执行并独立提交，不 amend、不 force-push。

## File Structure

```text
ai-creation-canvas/
├── LICENSE / UPSTREAM.md / README.md / CHANGELOG.md
├── pyproject.toml / requirements.lock
├── scripts/
│   ├── import-upstream.sh
│   ├── security-scan.sh
│   ├── prepare-portal-test-copy.sh
│   └── build-release.sh
├── web/
│   └── src/
│       ├── api/                       # 同源 API 契约与客户端
│       ├── features/nodes/            # 受控节点注册表
│       ├── features/workflows/        # 受控工作流注册表
│       ├── features/generation/       # 任务轮询和结果回填
│       ├── storage/                   # 用户作用域存储
│       └── stores/portal/             # 会话与模型状态
├── server/ai_creation_canvas/
│   ├── app.py / config.py / errors.py
│   ├── domain/                        # 数据类型、Protocol、注册表
│   ├── api/                           # `/api/v1` 路由
│   ├── adapters/portal/               # Portal 具体协议
│   └── storage/sqlite.py              # 最小任务映射
├── integrations/portal/               # 测试副本所用薄代理补丁
└── tests/{repo,server,contracts,integration}/
```

React 页面不实现 Portal 协议；API 客户端不操作节点；Python 路由不判断模型或服务名称；SQLite 不保存提示词、Cookie、密钥或媒体正文。

---

### Task 1: 导入固定上游并建立来源证明

**Boundary:** 此任务的原样上游快照只用于来源证明，且不可部署、不可运行。它保留的浏览器密钥、远程插件和动态脚本路径将在 Task 2 删除；Task 2 是首个可运行的安全门禁版本。

**Files:**
- Create: `scripts/import-upstream.sh`
- Create: `UPSTREAM.md`, `LICENSE`, `CHANGELOG.md`, `VERSION`, `web/**`
- Create: `tests/repo/test_upstream_snapshot.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: 本地只读上游副本路径和精确 Git 提交。
- Produces: 可复现的前端源码快照和 AGPL 来源记录。

- [ ] **Step 1: 写失败测试**

```python
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class UpstreamSnapshotTest(unittest.TestCase):
    def test_pinned_source_and_license(self):
        text = (ROOT / "UPSTREAM.md").read_text("utf-8")
        self.assertIn("9bccd0ff1a7057a835708a731644ab05371fea3b", text)
        self.assertIn("https://github.com/basketikun/infinite-canvas", text)
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text("utf-8"))
        self.assertFalse((ROOT / "web" / ".git").exists())
```

- [ ] **Step 2: 运行并确认失败**

Run: `python3 -m unittest discover -s tests/repo -p 'test_*.py' -v`
Expected: FAIL，来源文件和 `web/` 尚不存在。

- [ ] **Step 3: 实现导入脚本并执行**

```bash
#!/usr/bin/env bash
set -euo pipefail
source_dir="${1:?usage: import-upstream.sh /path/to/infinite-canvas}"
expected="9bccd0ff1a7057a835708a731644ab05371fea3b"
test "$(git -C "$source_dir" rev-parse HEAD)" = "$expected"
rsync -a --delete --exclude='.git/' "$source_dir/web/" web/
cp "$source_dir/LICENSE" LICENSE
cp "$source_dir/CHANGELOG.md" CHANGELOG.md
cp "$source_dir/VERSION" VERSION
```

`UPSTREAM.md` 写明 URL、提交、导入范围和 AGPL-3.0 来源；明确原样快照不可部署、不可运行，以及 Task 2 删除浏览器密钥、远程插件和动态脚本后才形成首个可运行安全版本。

- [ ] **Step 4: 验证并提交**

Run: `python3 -m unittest discover -s tests/repo -p 'test_*.py' -v && npm install --prefix web --legacy-peer-deps --package-lock=false && npm run typecheck --prefix web && npm run build --prefix web`
Expected: 来源测试、兼容安装、类型检查和构建通过，且 `git status --short` 无受跟踪文件改动。Task 1 不使用 `npm ci`：原样上游 lockfile 在 npm 11 下不兼容，且本任务不得修改该 lockfile。

```bash
git add LICENSE UPSTREAM.md CHANGELOG.md VERSION web scripts/import-upstream.sh tests/repo .gitignore
git commit -m "chore: import pinned infinite canvas source"
```

---

### Task 2: 建立前端测试门禁并切换安全同源 API

**Runnable security gate:** Task 2 is the first deployable and runnable frontend version. Before its commit, it must remove every browser key path, remote plugin facility, and dynamic-script entry point inherited from Task 1; it must regenerate the frontend lockfile and pass `npm ci`, the security scan, type checking, and production build.

**Files:**
- Modify: `web/package.json`, `web/package-lock.json`, `web/vite.config.ts`, `web/src/router.tsx`
- Create: `web/src/constant/security-policy.ts`, `web/src/test/setup.ts`, `web/src/test/security-policy.test.ts`
- Create: `web/src/api/{contracts,client,session,models,assets,jobs}.ts`
- Create: `web/src/features/nodes/types.ts`
- Modify: `web/src/services/api/image.ts`, `video.ts`
- Modify: `web/src/stores/use-config-store.ts`
- Delete: `web/src/services/api/model-plugin.ts`
- Delete: `web/src/lib/canvas/plugin-loader.ts`, `plugin-runtime.ts`
- Delete: `web/src/components/canvas/canvas-plugin-manager-modal.tsx`
- Delete: `web/src/components/layout/app-config-modal.tsx`, `channel-editor-drawer.tsx`, `model-script-editor.tsx`
- Delete: `web/src/components/layout/config-prompt-sources.tsx`, `prompt-source-editor-drawer.tsx`, `prompt-source-content-modal.tsx`
- Delete: `web/src/stores/canvas/use-plugin-store.ts`
- Delete: `web/src/stores/use-prompt-source-store.ts`
- Delete: `web/src/pages/canvas/hooks/use-plugin-host.tsx`
- Delete: `web/src/pages/config/index.tsx`
- Delete: `web/src/services/config-file.ts`, `webdav-sync.ts`
- Delete: `web/src/services/api/prompt-source-presets.ts`, `prompt-source-runtime.ts`
- Delete: `web/src/hooks/use-prompt-source-scheduler.ts`
- Create: `scripts/security-scan.sh`

**Interfaces:**
- Consumes: Task 1 的上游快照。
- Produces: `SECURITY_POLICY`、同源 `/api/v1` 客户端和 `npm test`；构建产物没有浏览器密钥或远程代码执行入口。

- [ ] **Step 1: 安装并锁定测试依赖**

Run: `npm install --prefix web --save-dev vitest @testing-library/react @testing-library/jest-dom jsdom --legacy-peer-deps && npm ci --prefix web`
Expected: `package-lock.json` 重新生成并锁定兼容解析版本，`package.json` 增加 `test: "vitest run"`，并且全新 `npm ci` 成功。

- [ ] **Step 2: 写失败测试并运行**

```ts
import { expect, it } from "vitest";
import { SECURITY_POLICY } from "@/constant/security-policy";

it("keeps executable extensions and keys server-side", () => {
  expect(SECURITY_POLICY).toEqual({
    browserApiKeys: false,
    remotePlugins: false,
    dynamicModelScripts: false,
    arbitraryBackendUrls: false,
  });
});
```

Run: `npm test --prefix web -- --run web/src/test/security-policy.test.ts`
Expected: FAIL，策略模块不存在。

- [ ] **Step 3: 实现策略并删除入口**

```ts
export const SECURITY_POLICY = Object.freeze({
  browserApiKeys: false,
  remotePlugins: false,
  dynamicModelScripts: false,
  arbitraryBackendUrls: false,
});
```

从路由、工具栏和画布页面移除插件管理、模型脚本编辑和浏览器渠道配置。将安全的 `CanvasNodeDefinition` 移到 `web/src/features/nodes/types.ts`，节点注册表不再依赖插件运行时。`use-config-store` 只保留模型 ID 和尺寸、数量、时长等生成偏好，删除 Base URL、API Key、渠道和脚本字段。

创建只接受 `/api/v1/` 相对路径的 `apiFetch`。把原图像和视频服务改成同源任务兼容层，使现有画布在 Task 10 完成细粒度状态 UI 前也不会直连提供商。

- [ ] **Step 4: 实现源码扫描并验证**

```bash
#!/usr/bin/env bash
set -euo pipefail
! rg -n 'new Function|VITE_PLUGIN_REGISTRY_URL|runModelPlugin|apiKey:\s*string|Authorization:\s*`Bearer' web/src
```

Run: `npm ci --prefix web && npm test --prefix web && npm run typecheck --prefix web && npm run build --prefix web && bash scripts/security-scan.sh`
Expected: 全部通过；这是首个可运行版本的依赖、测试、类型、构建和浏览器安全门禁。

- [ ] **Step 5: 提交**

```bash
git add web scripts/security-scan.sh
git commit -m "security: remove browser execution and key paths"
```

---

### Task 3: 完成 API 契约和用户作用域存储

**Files:**
- Modify: `web/src/api/{contracts,client,session,models,assets,jobs}.ts`
- Create: `web/src/storage/scope.ts`
- Create: `web/src/stores/portal/use-session-store.ts`
- Create: `web/src/test/storage-scope.test.ts`, `api-client.test.ts`
- Modify: `web/src/services/image-storage.ts`, `file-storage.ts`
- Modify: `web/src/stores/canvas/use-canvas-store.ts`

**Interfaces:**
- Produces: 完整 `PortalSession`, `ModelSpec`, `AssetRef`, `JobRequest`, `JobState`, `ApiError`；`setStorageScope(scope)`、`clearStorageScope()`。

- [ ] **Step 1: 写作用域失败测试并运行**

```ts
it("separates users and environments", () => {
  expect(storageDatabaseName({ environment: "test", userId: "u-a" })).toBe("ai-creation-canvas:test:u-a");
  expect(storageDatabaseName({ environment: "test", userId: "u-b" }))
    .not.toBe(storageDatabaseName({ environment: "test", userId: "u-a" }));
});
```

Run: `npm test --prefix web -- --run web/src/test/storage-scope.test.ts web/src/test/api-client.test.ts`
Expected: FAIL，模块不存在。

- [ ] **Step 2: 定义稳定契约**

```ts
export type ModelOperation = "image.generate" | "image.edit" | "video.generate" | "video.image_to_video";
export type PortalSession = { user_id: string; username: string; role: "admin" | "user" | "viewer" };
export type ModelSpec = { id: string; service_id: string; display_name: string; operations: ModelOperation[]; input_media: ("text" | "image")[]; parameter_schema: Record<string, unknown>; requires_asset_kind?: "portrait" };
export type AssetRef = { id: string; kind: "reference" | "portrait"; status: "processing" | "active" | "failed"; mime_type: string };
export type JobRequest = { operation: ModelOperation; model_id: string; prompt: string; params: Record<string, unknown>; asset_ids: string[]; idempotency_key: string };
export type JobState = { id: string; status: "uploading" | "submitting" | "queued" | "running" | "succeeded" | "failed"; result_url?: string; error?: ApiError };
export type ApiError = { code: string; message: string; retryable: boolean; request_id: string; phase: string };
```

- [ ] **Step 3: 实现同源客户端和存储切换**

`apiFetch<T>` 只接受 `/api/v1/` 相对路径并设置 `credentials: "same-origin"`。登录后创建作用域 localforage 实例；用户切换先释放旧实例引用，再加载目标用户画布。

- [ ] **Step 4: 验证并提交**

Run: `npm test --prefix web && npm run typecheck --prefix web`
Expected: 双用户数据库名隔离；401、403、429、500 均解析为 `ApiError`。

```bash
git add web/src/api web/src/storage web/src/stores web/src/services web/src/test
git commit -m "feat: add scoped portal client contracts"
```

---

### Task 4: 建立受控节点和工作流注册表

**Files:**
- Modify: `web/src/features/nodes/types.ts`
- Create: `web/src/features/nodes/registry.ts`, `builtins.tsx`
- Create: `web/src/features/workflows/{types,registry,portrait-video}.ts`
- Create: `web/src/test/extension-registry.test.ts`
- Modify: `web/src/lib/canvas/node-registry.ts`
- Modify: `web/src/components/canvas/canvas-create-menus.tsx`

**Interfaces:**
- Produces: `registerNode`, `listNodes`, `registerWorkflow`, `getWorkflow`；内置节点和 `portrait.video` 工作流。

- [ ] **Step 1: 写扩展失败测试并运行**

```ts
it("adds a node and workflow only through registration", () => {
  registerNode({ id: "test.note", version: 1, title: "测试", inputs: [], outputs: ["text"], createMetadata: () => ({}), render: () => null });
  registerWorkflow({ id: "test.flow", version: 1, run: async () => ({ jobId: "job-1" }) });
  expect(listNodes().some((node) => node.id === "test.note")).toBe(true);
  expect(getWorkflow("test.flow")?.id).toBe("test.flow");
});
```

Run: `npm test --prefix web -- --run web/src/test/extension-registry.test.ts`
Expected: FAIL，注册表不存在。

- [ ] **Step 2: 实现拒绝重复 ID 的注册表**

```ts
const nodes = new Map<string, NodeDefinition>();
export function registerNode(definition: NodeDefinition) {
  if (nodes.has(definition.id)) throw new Error(`duplicate node: ${definition.id}`);
  nodes.set(definition.id, definition);
}
export const listNodes = () => [...nodes.values()];
```

创建菜单从 `listNodes()` 渲染，不再手写类型菜单分支。工作流注册表使用同一重复 ID 规则。

- [ ] **Step 3: 实现人像工作流组合**

`portraitVideoWorkflow.run` 依次调用 `uploadAsset(file, "portrait")`、轮询资产到 `active`、提交 `video.image_to_video`。代码不调用名为 Seedance 的函数，不传伪造的人物类型标记。

- [ ] **Step 4: 验证并提交**

Run: `npm test --prefix web && npm run typecheck --prefix web && npm run build --prefix web`
Expected: 测试节点和工作流只靠注册出现；重复 ID 被拒绝；构建通过。

```bash
git add web/src/features web/src/lib/canvas/node-registry.ts web/src/components/canvas/canvas-create-menus.tsx web/src/test
git commit -m "feat: add controlled extension registries"
```

---

### Task 5: 建立 Python 项目、领域类型和适配器端口

**Files:**
- Create: `pyproject.toml`, `requirements.lock`
- Create: `server/ai_creation_canvas/__init__.py`
- Create: `server/ai_creation_canvas/domain/{models,ports,registry}.py`
- Create: `server/ai_creation_canvas/errors.py`
- Create: `tests/server/test_registry.py`, `test_errors.py`

**Interfaces:**
- Produces: `PortalUser`, `ModelSpec`, `AssetRef`, `JobRequest`, `UpstreamJob`, `JobState`；`GenerationPort`, `AssetPort`, `UsagePort`, `AdapterRegistry`。

- [ ] **Step 1: 锁定 Python 环境**

`pyproject.toml` 要求 Python `>=3.12,<3.13`；运行依赖 FastAPI、Uvicorn、httpx、python-multipart，测试依赖 pytest。使用项目虚拟环境安装后，把实际解析版本完整写入 `requirements.lock`，后续使用 `python -m pip install -r requirements.lock`。

- [ ] **Step 2: 写注册失败测试并运行**

```python
def test_registers_adapter_without_core_branch():
    registry = AdapterRegistry()
    fake = FakeGenerationAdapter(service_id="fake-image")
    registry.register_generation(fake)
    assert registry.generation("fake-image") is fake
    with pytest.raises(ValueError, match="duplicate service_id"):
        registry.register_generation(fake)
```

Run: `pytest tests/server/test_registry.py tests/server/test_errors.py -v`
Expected: FAIL，领域模块不存在。

- [ ] **Step 3: 实现领域类型和 Protocol**

```python
@dataclass(frozen=True)
class PortalUser:
    user_id: str
    username: str
    role: Literal["admin", "user", "viewer"]

class GenerationPort(Protocol):
    service_id: str
    async def list_models(self, context: RequestContext) -> tuple[ModelSpec, ...]: ...
    async def submit(self, context: RequestContext, request: JobRequest) -> UpstreamJob: ...
    async def poll(self, context: RequestContext, upstream_job_id: str) -> JobState: ...
```

注册表只按稳定 `service_id` 工作；模型 ID 与服务 ID 的关系来自目录。

- [ ] **Step 4: 验证并提交**

Run: `pytest tests/server/test_registry.py tests/server/test_errors.py -v`
Expected: PASS。

```bash
git add pyproject.toml requirements.lock server tests/server
git commit -m "feat: define backend domain ports"
```

---

### Task 6: 实现 Portal v2 身份与安全服务外壳

**Files:**
- Create: `server/ai_creation_canvas/config.py`
- Create: `server/ai_creation_canvas/adapters/portal/identity.py`
- Create: `server/ai_creation_canvas/app.py`
- Create: `server/ai_creation_canvas/api/session.py`
- Create: `tests/server/test_identity.py`, `test_app_security.py`

**Interfaces:**
- Consumes: `PORTAL_INTERNAL_TOKEN` 和 Portal v2 身份头。
- Produces: `verify_portal_identity(headers, now) -> PortalUser`、`GET /api/v1/session`、静态 SPA fallback。

- [ ] **Step 1: 写签名和环境守卫失败测试**

```python
def test_signature_binds_user_id_username_and_role():
    headers = signed_headers("u-a", "alice", "user", "test-secret", now=1000)
    assert verify_portal_identity(headers, "test-secret", now=1000).user_id == "u-a"
    headers["X-Portal-User-Id"] = "u-b"
    with pytest.raises(AuthRequired):
        verify_portal_identity(headers, "test-secret", now=1000)

def test_test_mode_rejects_production_port(tmp_path):
    with pytest.raises(ValueError, match="production port"):
        Settings(environment="test", port=9090, data_dir=tmp_path)
```

Run: `pytest tests/server/test_identity.py tests/server/test_app_security.py -v`
Expected: FAIL，模块不存在。

- [ ] **Step 2: 实现 v2 HMAC**

签名载荷固定为 `v2\n{ts}\n{user_id}\n{role}\n{percent_encoded_username}`，使用 HMAC-SHA256 和 `compare_digest`。要求 `X-Portal-Sig-Version: 2`，默认时间窗 60 秒；不接受未绑定用户 ID 的旧签名。

- [ ] **Step 3: 实现 FastAPI、安全头和错误映射**

所有 `/api/v1/*` 先验证身份。静态响应设置 CSP、`nosniff` 和 `Referrer-Policy: no-referrer`。测试环境若使用生产端口或生产路径立即拒绝启动。领域错误统一为 `{code,message,retryable,request_id,phase}`。

- [ ] **Step 4: 验证并提交**

Run: `pytest tests/server/test_identity.py tests/server/test_app_security.py -v`
Expected: 篡改身份、过期签名、缺失签名和生产配置均被拒绝，合法会话为 200。

```bash
git add server tests/server
git commit -m "feat: add signed portal identity and app shell"
```

---

### Task 7: 实现 Portal 客户端和能力驱动模型目录

**Files:**
- Create: `server/ai_creation_canvas/adapters/portal/{client,catalog}.py`
- Create: `server/ai_creation_canvas/api/models.py`
- Create: `server/config/services.example.json`
- Create: `tests/contracts/test_model_catalog.py`, `test_portal_client.py`

**Interfaces:**
- Consumes: 当前请求的 Portal Cookie、服务白名单和 `GenerationPort.list_models`。
- Produces: `PortalClient.request(context, method, path, ...)`、`GET /api/v1/models`。

- [ ] **Step 1: 写多模型与白名单失败测试**

```python
async def test_catalog_merges_capabilities_without_name_checks():
    registry = registry_with(
        FakeAdapter("image-service", [image_model("a"), image_model("b")]),
        FakeAdapter("video-service", [video_model("c")]),
    )
    models = await ModelCatalog(registry).list_models(context_for("u-a"))
    assert [model.id for model in models] == ["a", "b", "c"]

async def test_client_rejects_unconfigured_host():
    with pytest.raises(ValueError, match="not allowlisted"):
        await client.request(context, "GET", "https://example.invalid/api/config")
```

Run: `pytest tests/contracts/test_model_catalog.py tests/contracts/test_portal_client.py -v`
Expected: FAIL，客户端和目录不存在。

- [ ] **Step 2: 实现安全 PortalClient**

只接受配置中的 Portal 基地址和相对 mount，逐请求转发当前 Session Cookie，不持久化、不记录 Cookie。TLS 默认验证；测试自签证书通过显式 CA 文件信任，生产禁用 `verify=False`。

- [ ] **Step 3: 实现参数化目录适配器**

`PortalJobsAdapter` 读取服务 `/api/config` 并映射 `ModelSpec`。配置只声明 `service_id`、Portal mount、服务类型和操作；模型名字与数量由服务返回。单个服务失败不阻断其他目录项，但产生结构化诊断。

- [ ] **Step 4: 验证并提交**

Run: `pytest tests/contracts/test_model_catalog.py tests/contracts/test_portal_client.py -v`
Expected: 多模型、部分失败、未知主机拒绝和 Cookie 不落盘均通过。

```bash
git add server tests/contracts
git commit -m "feat: add portal model catalog adapters"
```

---

### Task 8: 实现资产、任务、幂等和结果代理

**Files:**
- Create: `server/ai_creation_canvas/adapters/portal/jobs.py`
- Create: `server/ai_creation_canvas/storage/sqlite.py`
- Create: `server/ai_creation_canvas/api/{assets,jobs,results}.py`
- Create: `tests/server/test_task_store.py`, `test_asset_security.py`
- Create: `tests/contracts/test_generation_flow.py`

**Interfaces:**
- Produces: `POST /api/v1/assets`、`GET /api/v1/assets/{id}`、`POST /api/v1/jobs`、`GET /api/v1/jobs/{id}`、`GET /api/v1/results/{id}`。

- [ ] **Step 1: 写归属与幂等失败测试**

```python
async def test_same_user_and_key_reuse_job(client_a):
    payload = job_payload(idempotency_key="idem-1")
    first = await client_a.post("/api/v1/jobs", json=payload)
    second = await client_a.post("/api/v1/jobs", json=payload)
    assert first.json()["id"] == second.json()["id"]
    assert fake_generation.submit_count == 1

async def test_other_user_cannot_read_job(client_a, client_b):
    job_id = (await client_a.post("/api/v1/jobs", json=job_payload())).json()["id"]
    assert (await client_b.get(f"/api/v1/jobs/{job_id}")).status_code == 403
```

Run: `pytest tests/server/test_task_store.py tests/contracts/test_generation_flow.py tests/server/test_asset_security.py -v`
Expected: FAIL，存储和路由不存在。

- [ ] **Step 2: 实现最小 SQLite 映射**

表 `canvas_jobs` 保存 `id,user_id,service_id,upstream_job_id,operation,status,idempotency_key,request_hash,error_code,created_at,updated_at`，唯一索引为 `(user_id,idempotency_key)`，使用事务和 WAL；不保存提示词、Cookie、密钥、媒体正文或签名结果 URL。

- [ ] **Step 3: 实现上传、任务和结果路由**

上传先检查长度，再验证 PNG/JPEG/WebP 文件头和 MIME，拒绝外部 URL。任务从模型目录取得 `service_id` 后查注册表；未知模型返回 `MODEL_UNAVAILABLE`。每次查询先按当前用户读取映射。结果只暴露 `/api/v1/results/{id}`，代理支持 Range。

- [ ] **Step 4: 验证并提交**

Run: `pytest tests/server/test_task_store.py tests/contracts/test_generation_flow.py tests/server/test_asset_security.py -v`
Expected: 幂等、双用户 403、非法文件 415、超大文件 413、未知模型和结果归属测试通过。

```bash
git add server tests
git commit -m "feat: add owned asset and generation jobs"
```

---

### Task 9: 接入人像资产作为可注册视频能力

**Files:**
- Create: `server/ai_creation_canvas/adapters/portal/portrait.py`
- Create: `tests/contracts/test_portrait_adapter.py`
- Create: `web/src/test/portrait-workflow.test.ts`
- Modify: `server/config/services.example.json`
- Modify: `web/src/features/workflows/portrait-video.ts`

**Interfaces:**
- Consumes: Portal `virtual/groups`、`virtual/assets`、`virtual/jobs` 契约。
- Produces: `service_id="portal-portrait"` 的资产和视频适配器，模型能力 `requires_asset_kind="portrait"`。

- [ ] **Step 1: 写步骤顺序失败测试**

```ts
it("waits for an active portrait asset before video submission", async () => {
  await portraitVideoWorkflow.run({ file, modelId: "portrait-model", prompt: "挥手" }, deps);
  expect(deps.calls).toEqual([
    "upload:portrait",
    "poll-asset:asset-1",
    "submit:video.image_to_video:asset-1",
  ]);
});
```

```python
async def test_portrait_adapter_is_video_capability():
    model = (await adapter.list_models(context))[0]
    assert model.requires_asset_kind == "portrait"
    assert "video.image_to_video" in model.operations
```

Run: `npm test --prefix web -- --run web/src/test/portrait-workflow.test.ts && pytest tests/contracts/test_portrait_adapter.py -v`
Expected: 至少一项 FAIL，人像适配器尚不存在。

- [ ] **Step 2: 实现参数化适配器**

适配器使用配置中的 mount，核心路由不出现 `volcengine-portrait`。资产状态映射 `Processing/Active/Failed` 到统一状态，任务映射到统一视频状态，资产 ID 始终视为不透明字符串。

- [ ] **Step 3: 实现可恢复工作流**

资产上传成功而视频提交失败时，错误保留 `assetId`；再次执行复用当前用户拥有且为 `active` 的资产。代码不创建或伪造真人/虚拟人分类字段。

- [ ] **Step 4: 验证并提交**

Run: `npm test --prefix web -- --run web/src/test/portrait-workflow.test.ts && pytest tests/contracts/test_portrait_adapter.py -v`
Expected: 正常顺序、处理中轮询、失败复用和跨用户拒绝全部通过。

```bash
git add server web/src/features/workflows web/src/test tests/contracts
git commit -m "feat: add portrait asset video workflow"
```

---

### Task 10: 将画布生成切换到统一任务并回填节点

**Files:**
- Create: `web/src/features/generation/{use-generation-job,result-node,error-message}.ts`
- Create: `web/src/test/generation-job.test.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/components/canvas/canvas-node-generation.ts`
- Modify: `web/src/components/model-picker.tsx`
- Modify: `web/src/stores/use-config-store.ts`

**Interfaces:**
- Consumes: Tasks 3-9 的 API、注册表、模型目录和任务。
- Produces: `useGenerationJob()`、`createResultNode(job, sourceNode)`、模型能力驱动控件和刷新恢复。

- [ ] **Step 1: 写恢复与结果去重失败测试**

```tsx
it("resumes an existing job and creates one result node", async () => {
  const { result } = renderHook(() => useGenerationJob(), { wrapper });
  await act(() => result.current.resume("j-1"));
  await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
  expect(canvasStore.getState().nodes.filter((n) => n.metadata?.sourceJobId === "j-1")).toHaveLength(1);
});
```

Run: `npm test --prefix web -- --run web/src/test/generation-job.test.tsx`
Expected: FAIL，统一任务 hook 不存在。

- [ ] **Step 2: 实现轮询、恢复和错误呈现**

提交前生成幂等键并保存最小任务引用。刷新只查询原任务。成功结果按 `sourceJobId` 去重；失败节点保留提示词、参数、资产 ID、请求 ID和阶段。429 或超时只提示重试，不自动换模型。

- [ ] **Step 3: 删除浏览器提供商调用**

从画布生成路径移除 `requestImageGeneration`、`createVideoGenerationTask` 等直连调用，只调用 jobs API。模型选择器读取 `operations` 与 `parameter_schema`，不再根据名字猜能力。

- [ ] **Step 4: 验证并提交**

Run: `npm test --prefix web && npm run typecheck --prefix web && npm run build --prefix web && bash scripts/security-scan.sh`
Expected: 生成、恢复、失败、结果去重和能力控件测试通过；扫描无浏览器密钥和远程执行入口。

```bash
git add web
git commit -m "feat: route canvas generation through portal jobs"
```

---

### Task 11: 准备不触碰生产的 Portal 薄代理测试集成

**Files:**
- Create: `integrations/portal/README.md`, `app-spec.json`, `signed-identity-v2.patch`
- Create: `scripts/prepare-portal-test-copy.sh`
- Create: `tests/integration/test_portal_contract.py`

**Interfaces:**
- Consumes: 原 Portal 只读源码、全新测试副本、画布 `8992`。
- Produces: Portal `9190` 下 `/ai-canvas/` 薄代理、绑定完整用户身份的 v2 签名、单次用量链路。

- [ ] **Step 1: 写 Portal 合约失败测试**

```python
def test_proxy_strips_forged_identity_and_signs_v2(portal_client):
    response = portal_client.get("/ai-canvas/api/v1/session", headers={"X-Portal-User-Id": "forged"})
    assert response.status_code == 200
    assert response.json()["user_id"] == portal_client.logged_in_user_id

def test_one_generation_is_counted_once(portal_client):
    job = portal_client.submit_canvas_job()
    portal_client.wait_for(job)
    assert portal_client.usage_delta(job_type="image") == 1
```

- [ ] **Step 2: 实现安全测试副本脚本**

脚本要求显式源目录和全新目标目录；目标必须匹配项目 `work/portal-test-*`，存在时拒绝覆盖。只复制 Portal 的 Python、静态前端、`app_spec.py` 和去敏配置骨架，不复制 Seedance、Nano Banana、Dreamina、人像子应用源码。复制时排除 `.git/`、`.env*`、`state/`、`outputs/`、`archives/`、`uploads/`、`logs/`、证书和密钥，再应用补丁并写入测试端口及全新测试目录。自动合约测试连接仓库内模拟服务；真实冒烟只连接用户另行启动并确认的 `8798/8788/8892` 测试实例。

- [ ] **Step 3: 实现最小 Portal 补丁**

补丁只注册 `/ai-canvas/ -> 127.0.0.1:8992`，生成绑定 `user_id + role + username` 的 v2 签名，并清除浏览器提供的全部身份头。Canvas 响应不发 `X-Job-Id`；底层生成仍经 Portal，因此用量只统计底层任务一次。

- [ ] **Step 4: 运行并提交**

Run: `pytest tests/integration/test_portal_contract.py -v`
Expected: 双用户隔离、伪造身份拒绝、任务归属和一次用量统计通过；只连接测试端口。

```bash
git add integrations scripts/prepare-portal-test-copy.sh tests/integration/test_portal_contract.py
git commit -m "test: add isolated portal canvas integration"
```

---

### Task 12: 完成隔离验收、发布包和运行文档

**Files:**
- Create: `scripts/build-release.sh`
- Create: `tests/integration/test_core_flows.py`
- Create: `docs/operations.md`, `docs/verification.md`, `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 1-11 的完整系统。
- Produces: 不含用户数据的 Python + 静态资源发布包、验证报告和运行说明。

- [ ] **Step 1: 写五条模拟端到端测试**

```python
@pytest.mark.parametrize("operation", [
    "image.generate", "image.edit", "video.generate", "video.image_to_video",
])
def test_generation_flow(operation, isolated_stack):
    assert isolated_stack.wait(isolated_stack.submit(operation))["status"] == "succeeded"

def test_portrait_asset_video_flow(isolated_stack):
    asset = isolated_stack.upload_portrait()
    assert isolated_stack.wait_asset(asset)["status"] == "active"
    assert isolated_stack.wait(isolated_stack.submit_portrait_video(asset))["status"] == "succeeded"
```

- [ ] **Step 2: 运行自动化验收**

```bash
pytest tests -v
npm test --prefix web
npm run typecheck --prefix web
npm run build --prefix web
bash scripts/security-scan.sh
git diff --check
```

Expected: 所有测试通过，Vite 构建成功，安全扫描和格式检查无错误。

- [ ] **Step 3: 打包并验证无 Node 运行**

`build-release.sh` 构建 `web/dist` 后，把 `server/`、锁定依赖、许可证、来源信息和静态产物复制到临时发布目录。在不含 `node`/`bun` 的受控 PATH 启动 Uvicorn `8992`，确认静态页可访问且未认证 API 返回 401。

- [ ] **Step 4: 执行真实模型冒烟门禁**

记录生产端口 `9090/8787/8797/8891` 的 PID、命令和启动时间。只有用户明确批准本次真实调用后，才在测试账户和测试目录执行代表图像、普通视频、人像视频各一条；完成后再次记录并逐项比对生产进程。

```bash
lsof -nP -iTCP:9090 -iTCP:8787 -iTCP:8797 -iTCP:8891 -sTCP:LISTEN > work/production-before.txt
# 只在用户批准后运行三个测试端口的真实冒烟测试。
lsof -nP -iTCP:9090 -iTCP:8787 -iTCP:8797 -iTCP:8891 -sTCP:LISTEN > work/production-after.txt
diff -u work/production-before.txt work/production-after.txt
```

Expected: `diff` 无输出；任何差异都停止验收并报告，不执行恢复或重启操作。

- [ ] **Step 5: 写文档并扫描提交范围**

`docs/verification.md` 只记录测试编号、时间、模型 ID、状态、用量增量和请求 ID，不记录提示词、Cookie、密钥、资产内容或结果 URL。

Run:

```bash
git ls-files | rg '(^|/)(state|outputs|archives|uploads|logs|secrets)/|\.env|\.(pem|key|p12)$' && exit 1 || true
git grep -n -E 'AKLT[A-Za-z0-9]{12,}|sk-[A-Za-z0-9]{16,}|BEGIN [A-Z ]*PRIVATE KEY' && exit 1 || true
```

Expected: Git 中无运行数据、密钥和证书。

- [ ] **Step 6: 提交**

```bash
git add README.md CHANGELOG.md docs scripts/build-release.sh tests/integration/test_core_flows.py
git commit -m "docs: add validation and release workflow"
```

---

## Final Verification Gate

```bash
pytest tests -v
npm ci --prefix web
npm test --prefix web
npm run typecheck --prefix web
npm run build --prefix web
bash scripts/security-scan.sh
git diff --check
git status --short --branch
```

验收报告必须证明：五条核心流程、双用户隔离、任务归属、用量不重复、浏览器无密钥、扩展注册测试、Python 静态运行、测试端口隔离和生产进程未受影响。验证通过后仍需用户明确批准，才能创建公开 GitHub 仓库。
