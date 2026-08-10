# Seedream 与 Seedance 真实画布闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不暴露密钥的前提下，使无限画布通过同源任务 API 完成一次 Seedream 图片和一次 Seedance 视频真实生成、轮询、受保护结果回填。

**Architecture:** 以已完成的 `agent/infinite-canvas-core` 为基线。新增受控 Ark 适配器和数据模型配置；浏览器始终只看到服务端派发的 `ModelSpec`。图片结果下载至本地受控资产缓存；视频只保存不透明任务/结果引用并由服务端流式代理，画布依据 `operation` 生成图片或视频结果节点。

**Tech Stack:** Python 3.12、FastAPI、httpx、SQLite、React 19、TypeScript、Vitest、真实 Chrome、火山方舟 Ark API。

## Global Constraints

- 继承 `AGENTS.md`、`docs/superpowers/specs/2026-08-10-ai-creation-canvas-product-design.md` 的安全、许可证、模型声明与生产隔离规则。
- `ARK_API_KEY` 只能由服务端进程读取；不得记录、回显、提交或向浏览器发送其值。
- 使用隔离数据目录和非生产端口；不得读取、修改或重启 `ai-generation-portable-apps`、8991/8992/8787/8797/8891 等生产资源。
- 首次真实调用仅限 1 张低成本图片与 1 个最短支持时长的视频；任务不确定时只查询，绝不自动重复提交。
- 模型 ID、显示名、能力和参数结构来自受控服务器配置；画布核心不得按 `seedream`/`seedance` 名称分支。
- 所有新增行为先写失败测试并确认 RED，再写最小实现并确认 GREEN。

## Task 1: 画布同步与视频提交入口

**Files:** `web/src/pages/canvas/project.tsx`、`web/src/components/canvas/generation-inspector.tsx`、`web/src/test/canvas-generation-page.test.tsx`。

- [ ] 写失败页面测试：服务目录同时返回图片和视频模型时，用户可以切换“图片 / 视频”动作；提交视频请求必须是 `video.generate`，成功回填 `CanvasNodeType.Video`，不能影响图片路径。
- [ ] 运行定向 Vitest，确认因缺少视频动作选择而失败。
- [ ] 用服务端 `operations` 过滤模型并以操作值驱动检查器标题、提交和源节点；保留参数白名单及同源 jobs API。
- [ ] 运行定向测试与 typecheck，提交 `feat: add canvas video generation entry`。

## Task 2: Ark 声明与适配器合同

**Files:** `server/ai_creation_canvas/adapters/ark.py`、`server/ai_creation_canvas/config.py`、`server/ai_creation_canvas/app.py`、`server/ai_creation_canvas/__main__.py`、`server/config/ark-models.example.json`、`tests/contracts/test_ark_adapter.py`、`tests/server/test_ark_config.py`。

- [ ] 写 httpx MockTransport 合同 RED：Seedream 图片请求采用官方 `/api/v3/images/generations`，Seedance 创建/查询采用 `/api/v3/contents/generations/tasks`；Bearer key 仅在服务器请求头；响应被严格验证并安全映射为 `JobState`。
- [ ] 写配置 RED：模型声明仅允许固定 `image`/`video` 能力、受限 JSON Schema 和官方模型 ID/endpoint；密钥不在 JSON 配置；缺失 `ARK_API_KEY` 时适配器不注册且不影响 demo。
- [ ] 实现最小 Ark 适配器：图片以确定性上游 ID 保存结果 URL 的受控短期引用；视频以 `cgt-*` ID 轮询；结果下载/流式读取通过 allowlisted Ark HTTPS URL，限制 MIME/长度并不暴露 URL。
- [ ] 运行合同、配置和安全扫描；提交 `feat: add controlled Ark media adapter`。

## Task 3: 本地运行、真实最小烟测与画布回填

**Files:** `scripts/run-real-media-local.sh`、`docs/verification.md`、`docs/operations.md`、`tests/integration/test_ark_media_loop.py`、`web/src/test/studio-page.test.tsx`。

- [ ] 写本地配置/启动 RED：缺失模型声明或 Key 时安全失败；启用时只在显式 `--real-media`/环境开关下加载 Ark，默认 `run-local.sh` 仍离线。
- [ ] 实现启动脚本，用新临时数据目录、随机高位本地端口、最短超时/轮询上限；输出只含状态、任务短 ID、耗时和安全错误码。
- [ ] 用一张低成本 Seedream 调用验证服务端任务→结果下载→受保护 `/api/v1/results`→画布图片节点；再用最短 Seedance 文生视频验证 queued/running/succeeded 或明确的安全失败，并在成功时检查 Range 播放与视频节点。
- [ ] 遇到外部失败时，先记录 HTTP 状态、稳定错误码、阶段、request ID 是否存在及是否已创建上游 ID；没有上游 ID 不自动重提。
- [ ] 完成双用户隔离、Python/前端/Chrome/构建/发布包门禁；将真实调用记录写入 Git 忽略的验证日志，仅记录时间、模型、状态、短任务 ID、耗时和成本级别。

## Task 4: 收口

- [ ] 检查 `git diff --check`、安全扫描、密钥扫描和提交内容。
- [ ] 运行定向的真实浏览器手工验收：创建项目、图片/视频切换、任务托盘、刷新恢复、结果节点、播放/下载授权与失败提示。
- [ ] 提交和推送分支；若真实服务未授权、余额不足或模型不可用，保留全部合同/模拟验证，报告精确失败阶段而不伪称真实闭环完成。
