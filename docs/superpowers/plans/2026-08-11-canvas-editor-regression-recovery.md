# Canvas Editor Regression Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复媒体节点高度、画布导航、多提示词、成功结果图关系和连续背景平移，并证明多个模型任务可以独立并行。

**Architecture:** 保留现有 CanvasProject 图文档和按 job ID 的任务 hook。导航状态留在当前 ProductShell 会话；媒体高度只做瞬时 DOM 测量；成功结果使用纯函数一次产生节点和命名端口连接；InfiniteCanvas 用 callback ref 隔离父层重渲染。

**Tech Stack:** React 18、TypeScript、Zustand、React Router、Vitest/Testing Library、Vitest Browser Chromium、FastAPI/Pytest（回归门禁）。

---

### Task 1: 恢复当前画布导航并增加返回入口

**Files:**
- Modify: `web/src/components/layout/product-shell.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/product-shell.test.tsx`
- Test: `web/src/test/studio-page.test.tsx`

- [ ] **Step 1: 写失败测试**

测试在 `/canvas/project-a` 渲染 ProductShell，进入 `/assets` 后断言“项目”链接仍为 `/canvas/project-a`；用不同 `session.user_id` 重渲染后断言链接重置为 `/canvas`。项目页测试断言存在“返回项目列表”链接且目标为 `/canvas`。

```tsx
expect(screen.getAllByRole("link", { name: "项目" })[0]).toHaveAttribute("href", "/canvas/project-a");
expect(screen.getByRole("link", { name: "返回项目列表" })).toHaveAttribute("href", "/canvas");
```

- [ ] **Step 2: 运行 RED**

Run: `npm test --prefix web -- --run src/test/product-shell.test.tsx src/test/studio-page.test.tsx`

Expected: 项目链接仍为 `/canvas`，返回链接不存在。

- [ ] **Step 3: 最小实现**

在 ProductShell 中用当前用户绑定的内存 ref/state 记录最后一个匹配 `/canvas/:id` 的路径。项目导航项动态使用该路径；用户 ID 变化立即清空。项目页标题区域增加 React Router `Link`：

```tsx
<Link to="/canvas" aria-label="返回项目列表"><ArrowLeft />返回项目列表</Link>
```

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `npm test --prefix web -- --run src/test/product-shell.test.tsx src/test/studio-page.test.tsx && npm run typecheck --prefix web`

Commit: `fix: preserve active canvas navigation`

### Task 2: 媒体集合节点按文件数量自适应

**Files:**
- Modify: `web/src/components/canvas/draggable-canvas-node.tsx`
- Modify: `web/src/components/canvas/media-collection-node.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/media-collection-node.test.tsx`
- Test: `web/src/test/canvas-node-editing.test.tsx`

- [ ] **Step 1: 写失败测试**

渲染 1、8、9 个媒体项，断言前 8 项列表没有滚动高度限制，第 9 项开始出现有界滚动；项目页面断言媒体节点 wrapper 不应用文档中的固定 `minHeight`，普通模型/结果节点仍应用。

```tsx
expect(screen.getByRole("list")).not.toHaveClass("max-h-80", "overflow-y-auto");
expect(screen.getByRole("list")).toHaveAttribute("data-overflowing", "true");
```

- [ ] **Step 2: 运行 RED**

Run: `npm test --prefix web -- --run src/test/media-collection-node.test.tsx src/test/canvas-node-editing.test.tsx`

Expected: 当前列表始终 `max-h-80 overflow-y-auto`，wrapper 始终固定最小高度。

- [ ] **Step 3: 最小实现**

给 DraggableCanvasNode 增加 `contentSized`，媒体集合传 `true`。contentSized 时不写 `minHeight`；媒体列表根据 `items.length + pending.length > 8` 才启用最大高度和滚动。继续使用已有 ResizeObserver 更新端口几何，不把测量值写回项目。

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `npm test --prefix web -- --run src/test/media-collection-node.test.tsx src/test/canvas-node-editing.test.tsx src/test/canvas-connections.test.tsx && npm run typecheck --prefix web`

Commit: `fix: size media nodes from their contents`

### Task 3: 允许多个提示词但保留每模型单提示词规则

**Files:**
- Modify: `web/src/features/graph/canvas-clipboard.ts`
- Modify: `web/src/components/canvas/canvas-create-context-menu.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/canvas-clipboard.test.ts`
- Test: `web/src/test/canvas-node-editing.test.tsx`
- Test: `web/src/test/canvas-connections.test.tsx`

- [ ] **Step 1: 写失败测试**

断言按钮和空白右键菜单可连续创建两个提示词；复制一个提示词后可粘贴到同一画布。另保留已有测试：同一模型第二条有效提示词连接得到 `prompt-occupied`，不同模型各自连接提示词成功。

- [ ] **Step 2: 运行 RED**

Run: `npm test --prefix web -- --run src/test/canvas-clipboard.test.ts src/test/canvas-node-editing.test.tsx src/test/canvas-connections.test.tsx`

Expected: 第二次创建被禁用，粘贴返回 `prompt-conflict`。

- [ ] **Step 3: 最小实现**

删除 clipboard 的全局 prompt conflict 分支、项目页 addPromptNode 唯一性判断，以及 palette/context menu 的 promptDisabled 属性。不要修改 `connectGraphPorts` 对目标模型提示词配额的判断。

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `npm test --prefix web -- --run src/test/canvas-clipboard.test.ts src/test/canvas-node-editing.test.tsx src/test/canvas-connections.test.tsx && npm run typecheck --prefix web`

Commit: `fix: allow multiple prompt nodes per canvas`

### Task 4: 修复连续背景平移

**Files:**
- Modify: `web/src/components/canvas/infinite-canvas.tsx`
- Test: `web/src/test/infinite-canvas.test.tsx`

- [ ] **Step 1: 写失败测试**

测试第一次 pointermove 调用 onViewportChange 后，用新 callback identity rerender 组件，再发送第二、第三次 pointermove；最终 viewport 必须累计到当前指针位置，而不是停在第一段。

```tsx
fireEvent.pointerMove(window, { pointerId: 1, clientX: 60, clientY: 60 });
rerender(<InfiniteCanvas onViewportChange={nextCallback} ... />);
fireEvent.pointerMove(window, { pointerId: 1, clientX: 160, clientY: 180 });
expect(latest).toEqual({ x: 140, y: 150, k: 1 });
```

- [ ] **Step 2: 运行 RED**

Run: `npm test --prefix web -- --run src/test/infinite-canvas.test.tsx`

Expected: effect cleanup 在 rerender 时结束 pan，后续 move 不更新。

- [ ] **Step 3: 最小实现**

增加 `onViewportChangeRef` 与 `onCanvasDeselectRef`，每次 render 更新 ref。窗口事件 effect 不再依赖 callback identity，RAF 和 finishPan 读取 ref 当前值；保留 pointer ID、cancel、blur、unmount 和 cursor 清理。

- [ ] **Step 4: 运行 GREEN 并提交**

Run: `npm test --prefix web -- --run src/test/infinite-canvas.test.tsx && npm run typecheck --prefix web`

Commit: `fix: keep canvas panning across rerenders`

### Task 5: 成功任务写入完成状态和结果图关系

**Files:**
- Modify: `web/src/features/generation/result-node.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Test: `web/src/test/generation-job.test.tsx`
- Test: `web/src/test/canvas-generation-page.test.tsx`

- [ ] **Step 1: 写纯函数失败测试**

为 `appendJobResults(nodes, connections, job, source, createId)` 定义期望：创建每个缺失结果节点和 `source.result -> result.result` 连接；重复调用不新增；已有结果节点但缺边时只修复连接。

```ts
expect(next.connections).toContainEqual(expect.objectContaining({
  fromNodeId: "model-a", fromPortId: "result", toNodeId: result.id, toPortId: "result",
}));
```

- [ ] **Step 2: 写页面失败测试**

模拟两个模型节点分别提交并返回 queued/running/succeeded。断言两个 POST 均发生、状态不互相覆盖；成功模型为 `status=success`、`jobStatus=succeeded`，没有“排队中”文案，且结果节点与来源模型自动连接。

- [ ] **Step 3: 运行 RED**

Run: `npm test --prefix web -- --run src/test/generation-job.test.tsx src/test/canvas-generation-page.test.tsx`

Expected: 当前只新增节点、不新增边，模型 `jobStatus` 仍为 queued/running。

- [ ] **Step 4: 最小实现**

纯函数从 job 的有界 result 列表生成缺失节点，并使用端点元组去重连接。项目成功回调基于最新 store snapshot 一次更新 nodes/connections：

```ts
const sourceReady = { ...source, metadata: { ...source.metadata, status: "success", jobStatus: "succeeded", idempotencyKey: undefined } };
const graph = appendJobResults(updatedNodes, current.connections, job, sourceReady, nanoid);
updateProject(projectId, graph);
```

- [ ] **Step 5: 运行 GREEN、相关回归并提交**

Run: `npm test --prefix web -- --run src/test/generation-job.test.tsx src/test/canvas-generation-page.test.tsx src/test/canvas-connections.test.tsx && npm run typecheck --prefix web`

Commit: `fix: connect completed jobs to result nodes`

### Task 6: 浏览器验收与完整门禁

**Files:**
- Modify: `web/src/test/studio-responsive.browser.test.tsx`
- Create: `docs/superpowers/reports/2026-08-11-canvas-editor-regression-recovery.md`

- [ ] **Step 1: 扩展 Chromium 行为测试并先观察 RED**

桌面流程加入：连续背景平移、两个提示词、媒体节点 1/8/9 项高度策略、项目到资产再返回、返回项目列表、成功结果自动连线与完成状态。保留 415px/240px 无横向溢出测试。

- [ ] **Step 2: 运行完整前端门禁**

Run:

```bash
npm test --prefix web -- --run
npm run typecheck --prefix web
npm run build --prefix web
npm run test:browser --prefix web
npm audit --prefix web --omit=dev --audit-level=high
```

Expected: 所有测试通过、audit 为 0；只允许记录既有 Vite chunk 警告。

- [ ] **Step 3: 运行仓库门禁**

Run:

```bash
PYTHONPATH=.:server .venv/bin/pytest -q
bash scripts/security-scan.sh
git diff --check
```

Expected: Python 全量、security scan、diff check 全部通过。

- [ ] **Step 4: 提交、推送和隔离验收**

提交浏览器测试与报告，推送 `agent/graph-media-nodes`。用新的 `.paid-acceptance/<name>` 数据目录和未占用端口启动当前构建；不得停止或修改 8995/8996。验证 HTML 登录页、两个隔离账号和模型声明后，把地址与账号交给用户验收。本切片不自动发起付费任务。
