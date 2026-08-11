# 多模型、图片参数与提示词 Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不暴露管理员 Key、不伪装供应商能力的前提下，为画布增加 Ark 官方多模型、完整可用的 Seedream 图片参数，以及可选择的一键提示词优化 Skill。

**Architecture:** 延续已批准的“模型是声明”架构：同一 Ark 协议的模型只增加受控目录声明，参数由安全 JSON Schema 生成控件并由映射编译器精确写入官方请求。T8Star、Chiyun、Dreamina 不塞进 Ark 适配器，分别留给后续供应商适配器。提示词 Skill 使用内置、版本化、纯数据清单和独立服务端执行边界，普通用户只能选择管理员启用的 Skill，不能上传代码或服务地址。

**Tech Stack:** Python 3.12、FastAPI、httpx、React、TypeScript、Vitest、Playwright/Chromium、JSON Schema 安全子集。

---

## 范围决定

- 本切片接入 Ark 官方当前模型：Seedream 5.0 Pro、5.0 Lite、4.5、4.0；Seedance 2.5、2.0、2.0 Fast、2.0 Mini。
- Portal 中的 T8Star Gemini、Chiyun、Dreamina 不是 Ark 模型；没有各自服务端凭据与协议适配前不显示，避免“可选但必失败”。
- 图片用户参数包括：尺寸、输出格式、水印、提示词优化模式；支持组图的模型另提供组图模式和最大图片数。`response_format=url` 由服务端固定，`stream=false` 由当前任务模式固定，不作为影响作品的用户控件。
- `size` 同时接受官方分辨率档位和合法的 `宽x高`；服务端按各模型像素范围和宽高比二次校验。
- Prompt Skill 第一批只接入许可证清晰、可归因的开源提示词方法；Skill 内容内置到仓库，运行由管理员配置的服务端文本模型完成。本地无文本模型凭据时仍可浏览与选择，但明确显示“管理员尚未启用”，不得伪装优化成功。

### Task 1: Ark 嵌套参数映射和按模型约束

**Files:**
- Modify: `server/ai_creation_canvas/adapters/ark.py`
- Modify: `server/ai_creation_canvas/api/jobs.py`
- Modify: `tests/contracts/test_ark_adapter.py`
- Modify: `tests/server/test_ark_config.py`

- [ ] **Step 1: 写失败合同测试**

覆盖扁平 UI 参数映射到 `optimize_prompt_options.mode`、`sequential_image_generation_options.max_images`，拒绝重复叶子、危险路径和对象污染键；覆盖每个模型的尺寸档位、自定义尺寸像素/比例边界、参考图数量和不支持参数。

- [ ] **Step 2: 运行聚焦测试确认 RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_ark_adapter.py tests/server/test_ark_config.py`

Expected: 新增嵌套映射和模型约束断言失败，既有测试保持通过。

- [ ] **Step 3: 实现最小安全映射器**

只允许预声明的最多两层 Ark 参数路径；从白名单参数重建新字典，不合并调用方对象。为 `size` 增加声明式 `x-ark-size` 约束，API 和适配器使用同一验证函数，避免前后两套规则漂移。

- [ ] **Step 4: 运行聚焦测试确认 GREEN**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/contracts/test_ark_adapter.py tests/server/test_ark_config.py`

Expected: 全部通过。

- [ ] **Step 5: 提交**

Commit: `feat: support declared Ark image parameters`

### Task 2: 官方 Ark 多模型目录与友好参数控件

**Files:**
- Modify: `server/config/ark-models.example.json`
- Modify: `tests/server/test_ark_config.py`
- Modify: `web/src/components/model-picker.tsx`
- Modify: `web/src/components/canvas/model-call-node.tsx`
- Modify: `web/src/test/model-picker.test.ts`
- Modify: `web/src/test/model-call-node.test.tsx`
- Modify: `web/src/test/canvas-generation-page.test.tsx`

- [ ] **Step 1: 写失败目录与页面测试**

断言 8 个官方模型均由目录返回且能力不按名称猜；Seedream Pro 最多 10 张参考图且无组图参数，Lite/4.5/4.0 最多 14 张并有组图参数；4 个 Seedance 模型具有各自时长、分辨率和输入上限。页面按 `title`/`description` 显示中文标签和帮助文本，切模型时清除上一个模型不支持的参数。

- [ ] **Step 2: 运行测试确认 RED**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_ark_config.py && npm test --prefix web -- --run src/test/model-picker.test.ts src/test/model-call-node.test.tsx src/test/canvas-generation-page.test.tsx`

Expected: 模型数、参数、标签或切换清理断言失败。

- [ ] **Step 3: 完成目录和安全 UI 元数据**

目录仅使用官方 Model ID 和精确能力。前端只读取 `title`、`description`、`enum`、范围、默认值、`x-ui-control` 等纯数据字段；未知字段继续忽略。运行请求仍由 `compileGraphJob` 白名单重建。

- [ ] **Step 4: 运行测试确认 GREEN 并验证请求体**

运行同一聚焦命令，并在页面测试中断言非默认值进入 `/api/v1/jobs`；适配器测试断言官方 JSON 叶子值一致。

- [ ] **Step 5: 提交**

Commit: `feat: add current Ark image and video models`

### Task 3: 内置提示词优化 Skill

**Files:**
- Create: `server/ai_creation_canvas/prompt_skills.py`
- Create: `server/config/prompt-skills.example.json`
- Create: `server/ai_creation_canvas/api/prompt_skills.py`
- Modify: `server/ai_creation_canvas/app.py`
- Modify: `server/ai_creation_canvas/config.py`
- Modify: `server/ai_creation_canvas/__main__.py`
- Modify: `web/src/api/contracts.ts`
- Create: `web/src/api/prompt-skills.ts`
- Modify: `web/src/components/canvas/prompt-node-card.tsx`
- Create: `tests/server/test_prompt_skills.py`
- Create: `web/src/test/prompt-skills.test.tsx`
- Modify: `THIRD_PARTY_NOTICES.md`

- [ ] **Step 1: 研究并固定来源/许可证**

从 GitHub 高星项目中选择 3–5 个不同创作主题的方法，记录仓库、固定提交、许可证和仅作方法参考的范围。不能复制许可证不兼容的大段提示词。

- [ ] **Step 2: 写失败服务端测试**

覆盖 Skill 清单有界解析、管理员启停过滤、普通用户不可自定义指令/URL、服务端 Key 不下发、优化请求长度限制、超时/拒绝的安全错误、原提示词不落日志。

- [ ] **Step 3: 运行服务端测试确认 RED 并实现最小执行边界**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q tests/server/test_prompt_skills.py`

执行器只向管理员配置的同一文本服务发送“内置 Skill 指令 + 当前提示词”，返回纯文本；无配置时返回明确的 `SKILL_SERVICE_UNAVAILABLE`，不做本地假改写。

- [ ] **Step 4: 写失败前端交互测试并实现**

提示词节点新增“优化”菜单：选择 Skill、查看用途、点击一次优化、比较原文/新文本、应用或撤销。请求期间禁止重复提交；失败保留原文；只允许修改当前节点。

Run: `npm test --prefix web -- --run src/test/prompt-skills.test.tsx`

Expected: RED 后实现至 GREEN。

- [ ] **Step 5: 提交**

Commit: `feat: add governed prompt optimization skills`

### Task 4: 收口验证与用户验收实例

**Files:**
- Modify: `docs/verification.md`
- Create: `docs/superpowers/reports/2026-08-12-multi-model-image-parameters-prompt-skills.md`

- [ ] **Step 1: 全量门禁**

Run: `PYTHONPATH=.:server .venv/bin/pytest -q`

Run: `npm run verify:release --prefix web`

Run: `npm audit --prefix web --omit=dev --audit-level=high`

Run: `scripts/security-scan.sh`

Run: `git diff --check`

- [ ] **Step 2: 隔离浏览器验收**

使用新的测试端口和 Git 忽略数据目录，不接触 8997。验证管理员可看到全部目录并派发，普通用户只看到获派模型；逐个模型离线 mock 提交至少一个非默认参数；Skill 无服务时显示明确禁用，有测试服务时可应用/撤销；页面刷新后参数与提示词保持。

- [ ] **Step 3: 生成报告并提交**

报告列出“已接入”“声明可用但未付费验证”“因独立供应商协议暂缓”三张表，并给出用户上手测试账号/地址（凭据只在终端一次性显示，不写文档）。

Commit: `docs: report multi-model and prompt skill verification`

