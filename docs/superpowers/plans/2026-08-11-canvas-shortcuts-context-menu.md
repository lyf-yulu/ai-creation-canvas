# Canvas Shortcuts and Context Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为无限画布实现用户隔离的复制、剪切、粘贴、全选、删除、重命名，以及按右键世界坐标创建节点。

**Architecture:** 新增纯函数剪贴板模块，页面只负责把选择和项目快照交给它；剪贴板绑定当前 storage scope version，并在 scope clear 时清空。节点操作菜单与空白创建菜单分离，共用可视区域定位与键盘行为，页面节点工厂统一接受可选世界坐标。

**Tech Stack:** React 19、TypeScript、Zustand、Vitest/Testing Library、Vitest Browser/Chromium。

## Global Constraints

- 不写系统剪贴板或持久化存储；切换用户立即清空。
- 不复制旧任务 ID、运行状态、重试键或请求 ID。
- 只复制选中节点之间的连接；粘贴时生成全新节点/连接 ID。
- 单提示词、1000 节点、2000 连接边界 fail closed，不做部分粘贴。
- 输入控件和 contenteditable 中保留原生快捷键。
- 8995 验收实例在本轮开发期间不重启、不修改其数据。

---

### Task 1: Scoped canvas clipboard core

**Files:**
- Create: `web/src/features/graph/canvas-clipboard.ts`
- Create: `web/src/test/canvas-clipboard.test.ts`
- Modify: `web/src/stores/portal/use-session-store.ts`

**Interfaces:**
- Produces: `copyCanvasSelection(project, selectedIds)`, `pasteCanvasSelection(project, createId)`, `clearCanvasClipboard()` and typed success/error results.
- Consumes: `currentStorageScopeVersion`, `onStorageScopeCleared`, canonical `CanvasProject`, `CanvasNodeData`, `CanvasConnection`.

- [ ] **Step 1: Write failing pure-function tests**

Cover deep-copy isolation, internal connections only, new IDs, 32px repeated offsets, model task-field reset, prompt uniqueness, limits and scope clear.

- [ ] **Step 2: Run RED**

Run: `npm test --prefix web -- --run src/test/canvas-clipboard.test.ts`

Expected: module resolution failure because `canvas-clipboard.ts` does not exist.

- [ ] **Step 3: Implement the minimal bounded clipboard**

Use a module-local snapshot `{ scopeVersion, nodes, connections, pasteCount }`; subscribe once to `onStorageScopeCleared`. Clone plain project data, map known node-reference metadata IDs, clear paid-task lifecycle fields, reject invalid prompt/size conditions before changing the project.

- [ ] **Step 4: Run GREEN and typecheck**

Run: `npm test --prefix web -- --run src/test/canvas-clipboard.test.ts && npm run typecheck --prefix web`

- [ ] **Step 5: Commit**

Commit message: `feat: add scoped canvas clipboard`

### Task 2: Keyboard commands and node naming

**Files:**
- Create: `web/src/components/canvas/rename-node-dialog.tsx`
- Modify: `web/src/components/canvas/canvas-context-menu.tsx`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/components/canvas/canvas-zoom-controls.tsx`
- Modify: `web/src/test/canvas-node-editing.test.tsx`

**Interfaces:**
- Consumes Task 1 clipboard functions.
- Produces page commands `copySelection`, `cutSelection`, `pasteSelection`, `renameSelectedNode` and accessible dialog/menu actions.

- [ ] **Step 1: Write failing page tests**

Cover Ctrl/Cmd C/X/V/A, repeated paste offsets, copied internal edge, editable-target preservation, read-only no-op, busy model reset, F2 rename, right-click multi-selection preservation, trim/empty/max-length naming and Escape focus restore.

- [ ] **Step 2: Run RED**

Run: `npm test --prefix web -- --run src/test/canvas-node-editing.test.tsx`

Expected: copy/paste and rename assertions fail while existing delete tests remain green.

- [ ] **Step 3: Implement minimal keyboard router and dialog**

Use one window keydown listener that exits for `isEditableEventTarget`; require primary modifier for A/C/X/V; call `preventDefault` only when a canvas command is actually handled. F2 requires exactly one selected node. Node context actions operate on the preserved multi-selection when the clicked node is already selected.

- [ ] **Step 4: Run GREEN, related regression and typecheck**

Run: `npm test --prefix web -- --run src/test/canvas-node-editing.test.tsx src/test/canvas-connections.test.tsx src/test/canvas-generation-page.test.tsx && npm run typecheck --prefix web`

- [ ] **Step 5: Commit**

Commit message: `feat: add canvas editing shortcuts`

### Task 3: Blank-canvas creation menu and real browser verification

**Files:**
- Create: `web/src/components/canvas/canvas-create-context-menu.tsx`
- Modify: `web/src/types/canvas.ts`
- Modify: `web/src/pages/canvas/project.tsx`
- Modify: `web/src/test/canvas-node-editing.test.tsx`
- Modify: `web/src/test/studio-responsive.browser.test.tsx`

**Interfaces:**
- Produces a canvas context state with client coordinates plus frozen world position, and `onCreate(kind)` for six built-in node choices.
- Consumes page node factories with optional `Position` and current model availability.

- [ ] **Step 1: Write failing component/page/browser tests**

Cover exact world coordinate after pan/zoom, prompt/model disabled reasons, menu clamping, keyboard navigation/activation/Escape focus, background-only interception, read-only native menu, creation persistence and refresh.

- [ ] **Step 2: Run RED**

Run: `npm test --prefix web -- --run src/test/canvas-node-editing.test.tsx`

Expected: no canvas creation menu exists and no node is added at the requested position.

- [ ] **Step 3: Implement menu and coordinate-aware factories**

Add `type: "canvas"` context state, capture `clientToWorld(event.clientX,event.clientY)` at open time, and pass that immutable position to prompt/media/model factories. Keep prompt and unavailable model entries disabled with visible explanations.

- [ ] **Step 4: Run GREEN and complete release gates**

Run:

```bash
npm run verify:release --prefix web
npm audit --prefix web --omit=dev --audit-level=high
bash scripts/security-scan.sh
git diff --check
```

- [ ] **Step 5: Start an isolated acceptance instance**

Build current commit and serve on a new non-reserved port with a fresh ignored data directory. Do not stop or mutate the existing 8995 instance. Verify desktop plus 415px/240px layout and leave the new login tab open for the user.

- [ ] **Step 6: Commit and push**

Commit message: `feat: create canvas nodes from context menu`

