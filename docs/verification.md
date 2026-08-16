# 隔离验证记录

## 自动化矩阵

| 项目 | 证据字段 | 当前执行方式 |
| --- | --- | --- |
| 图像生成 | 测试名、状态、任务 ID（临时）、用量事件数 | 进程内模拟服务 + 真实 Canvas API/SQLite/Portal 适配器 |
| 参考图编辑 | 测试名、状态、资产归属、用量事件数 | 同上 |
| 文本视频 | 测试名、状态、结果读取、用量事件数 | 同上 |
| 图片参考视频 | 测试名、状态、资产归属、结果读取、用量事件数 | 同上 |
| 人像资产视频 | 测试名、资产激活、状态、结果读取、用量事件数 | 同上 |
| 发布包 | manifest 校验、禁止文件扫描、Python-only 启动 | 临时发布目录与随机本地端口 |
| 前端安全 | 测试、类型检查、构建、安全扫描 | 锁定依赖安装后执行 |
| Slice 1 本地产品 | 双角色、模型派发、项目持久化、离线任务、结果 Range | `tests/integration/test_slice1_product.py` + 本地浏览器冒烟 |
| 多模型与提示词 Skill | 8 个 Ark 模型目录、差异化图片参数、4 类 Skill 预览/应用 | 合同测试 + Chromium 页面验收 + `9001` 隔离实例 |
| 管理员模型对象与 Chiyun | Provider/模型创建、权限派发、严格图片操作、并发同键一次提交、结果 Range、撤权与跨用户隔离 | 真实 FastAPI/SQLite + 受控 Chiyun HTTP 模拟 + React/Chromium |
| 模型中心路由 | 逻辑模型、官方/T8 gemini 兼容线路、T8 cc 负例、Redis 协议租约、429 换 Key、幂等、unknown、结果 Range、生命周期与窄屏 | `tests/integration/test_model_centric_routing.py` + Chromium `canvas-responsive.browser.test.tsx` |
| 后台任务重启恢复 | 双用户后台直连/受管任务、SQLite 重启接管、原 Key 指纹、两项有序结果、后台 GET 只读、用量仅一次 | `tests/integration/test_background_job_recovery.py` |
| Portal 请求内轮询兼容 | 图像/视频/人像完成模式、owner 带 Cookie GET、无 Cookie 持久化、worker 隔离、401/403 重登录恢复、旧行迁移、不重放模糊提交 | `tests/integration/test_background_job_recovery.py` + Portal/人像合同测试 |

记录只保存命令、测试计数、退出状态、临时测试 ID、错误码和版本提交。不保存提示词、Cookie、密钥、资产内容、真实结果 URL 或原始请求/响应。

## 无限画布核心

桌面宽度和窄屏宽度分别执行以下验收；窄屏至少覆盖 415 px 和 240 px：

1. 创建一个画布，并添加至少两个节点。
2. 平移画布，将缩放调整到非 100%，并在不同缩放比例下拖动两个节点。
3. 刷新页面，然后重新进入该项目。
4. 确认视角、缩放比例和两个节点的位置均已恢复。
5. 确认右侧生成面板、底部任务托盘和左下角导航控件互不遮挡。
6. 确认浏览器控制台没有未处理错误。
7. 确认项目保存经过 400 ms 防抖，没有按每次指针移动形成请求风暴。

普通开发环境不要求安装浏览器，JSDOM 回归仍使用：

```bash
npm test --prefix web
```

发布前的正式前端门禁使用：

```bash
npm run verify:release --prefix web
```

该命令依次运行 JSDOM 全量、类型检查、生产构建和 2 个真实 Chrome 响应式用例。它要求本机已安装 Google Chrome；缺少 Chrome 时会明确失败，但不影响普通测试、构建、Python 静态运行或生产启动。

## 真实服务冒烟

真实服务冒烟必须经用户书面批准后，使用独立数据目录、非生产端口与一次性测试账号执行。交互式人工检查使用 `scripts/run-real-media-local.sh`；有界的自动付费验收只能使用 `scripts/acceptance-real-media.sh`。后者还要求精确设置 `AICC_ALLOW_PAID_ACCEPTANCE=YES`，默认隔离到一个全新数据目录和 `127.0.0.1:8998`，并锁定一次 Seedream 参考图编辑及一次复用该结果的 Seedance 5 秒 480p 视频。

获批后的单次代表模型只允许记录到 Git 忽略的本地验证日志：时间、模型 ID、状态、短任务 ID、耗时、成本级别与安全错误码。禁止记录提示词、Cookie、密钥、资产内容、结果 URL、原始请求/响应；不得把该日志提交到仓库。

每次验收至少覆盖：

1. 普通用户只能看到管理员派发的图片/视频模型；浏览器没有 Key 输入或保存路径。
2. 一次图片任务和一次最短支持时长的视频任务分别经过提交、轮询和画布结果节点回填。
3. 刷新画布后，未完成任务能够恢复轮询；完成结果只能经同源、已登录的结果接口读取。
4. 结果节点按类型显示图片预览或带控制项的视频播放器；结果接口的 GET、HEAD、Range 由合同测试覆盖。

本地 Slice 1 的 `demo-image-v1` 不属于真实服务冒烟：它只读取仓库内固定 PNG，不连接外网、不读取 Key，也不产生用量。

## 多模型与提示词 Skill 验收

本地验收实例使用全新的 Git 忽略数据目录和非生产端口。它提供完整目录与页面交互，但媒体结果和提示词优化均由离线模拟服务返回，不读取真实 Key、不连接供应商、不产生费用。

验收至少覆盖：

1. 图片节点显示 Seedream 4.0、4.5、5.0 Lite、5.0 Pro，视频节点显示 Seedance 2.0、2.0 Fast、2.0 Mini、2.5。
2. 切换图片模型时只显示该模型声明支持的尺寸、格式、水印、提示词优化和组图参数；非默认值会进入白名单重建后的任务请求。
3. 提示词节点可展开 Skill，选择“精准结构、摄影写实、商业产品、电影动态”，生成预览后应用或放弃；未配置管理员文本模型时必须明确不可用。
4. 浏览器不接收 Ark Key、Skill 指令或自定义服务地址，普通用户不能增加远程 Skill。

## 受控模型注册表验收

`tests/integration/test_chiyun_model_registry.py` 使用全新 SQLite 数据、真实 FastAPI 路由、本地并发协调器和受控 Chiyun HTTP 模拟。它必须证明管理员创建 Provider/模型并授权后，普通用户上传的参考图片会按顺序进入唯一一次 `image.edit` multipart 请求；两个并发的相同幂等键返回同一个任务且只产生一个提供方请求。随后还要验证任务轮询、结果 GET/HEAD/Range、管理员对普通用户任务和结果的 404 隐藏，以及撤权后新提交在接触提供方前被拒绝。

这个验收实例是离线的：使用占位凭据和本地固定 PNG，不连接 Chiyun，也不产生费用。真实 Chiyun Key、提供方错误格式、计费和区域网络仍需管理员单独批准的一次性小额验收；在此之前不得把离线结果描述成真实模型调用成功。

## 模型中心路由离线验收

后端验收使用真实 FastAPI、真实 SQLite 文件和生产 `RedisExecutionCoordinator`，Redis 服务器由协议级内存 fake 执行同一 Lua 合同；没有打开 Redis 网络端口。Provider 使用 `httpx.MockTransport`，因此所有官方/T8 响应均为确定性模拟，外部调用和付费调用均为零。

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_model_centric_routing.py
npm run test:browser --prefix web -- --run src/test/browser/canvas-responsive.browser.test.tsx
```

后端验证管理员经 API 创建 Nano Banana 逻辑模型、官方与 T8 `gemini` 线路，并拒绝 T8 `cc` 负例；上传两张有序参考图；相同用户、相同幂等键并发提交只形成一个平台任务；明确 429 可在同池换 Key，官方池耗尽后才进入兼容 T8 `gemini`，模糊响应只形成一次调用并进入 `submission_unknown`。还验证不可变路由快照、普通目录不泄露线路/池、撤权、跨用户隐藏，以及结果 GET/HEAD/单段 Range。

Chromium 验收宽度为桌面 1280 px、窄屏 415 px 和 240 px。桌面流程通过真实 React 管理界面创建并编辑逻辑模型、添加两条线路、读取安全池容量、派发普通用户、归档/恢复、展示引用删除阻塞，再切换到普通用户画布创建对应模型节点。两档窄屏检查页面无横向溢出且主要操作按钮保持可见。

## 后台任务重启恢复验收

该验收只使用真实 FastAPI、临时 SQLite 数据目录和进程内伪传输，不打开服务端口、不读取真实 Key，也不访问外网：

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_background_job_recovery.py
```

验收先由两个不同用户分别提交明确声明无 Cookie 后台轮询能力的直连任务和受管任务，在提供方完成前关闭第一应用，再用同一数据目录、等价受控配置和原凭据指纹重建应用。只运行后台 worker，不先请求浏览器模型目录，也不借助任务 GET 触发轮询；两项任务都必须完成，受管任务的两个结果顺序保持不变，且 owner 的任务 GET、结果 HEAD、单段 Range 与完整读取均可用。这项结论不适用于 Cookie Portal 任务，也不得表述成所有提供方的离线恢复承诺。

负例必须证明其他用户和管理员都不能越过当前任务归属合同，任务 GET 保持只读，每个成功任务只产生一条用量/成本记录，不可变线路快照不变化，提供方提交不重放，`submission_unknown` 不被 worker 接管。受管凭据缺失或指纹不匹配时只能延迟重试并失败关闭，不得轮换 Key；恢复原凭据后才能继续。终态之后还要确认待确认记录已由 worker 清理。

Cookie 认证的 Portal 图像、视频和人像验收必须单独证明：新任务记录为 `request` 模式且 worker 不可领取；只有当前 owner 的带新鲜 Cookie GET 会执行一次上游轮询，其他用户在任何提供方 I/O 之前被隐藏；关闭页面或重启应用后，必须等 owner 重新登录并查询才继续。Portal `401`/`403` 应返回 `AUTH_REQUIRED`、释放租约并保留原 `queued`/`running` 任务，然后用新 Cookie 查询可进入终态。验收还要扫描 SQLite、快照和日志，确认 Cookie 不持久化、不进入 worker，且直连模糊提交不会因同一幂等键而第二次调用提供方。只有适配器明确分类为 `NOT_SUBMITTED` 的提交失败才允许受控重试。

迁移验收必须从无 `completion_mode` 的历史未终态行开始，证明启动只按受信服务端能力分类可识别行，无法分类的行保持不可领取。备份/恢复验收仅覆盖单实例 SQLite：必须使用 SQLite 在线备份，或停止唯一实例后一致备份数据库与资产/结果；不验收多实例共享一个 SQLite 目录。

本验收产生的媒体只存在于 pytest 临时目录。正式门禁前确认仓库工作树没有 `.local-real-media-data/`、结果文件、凭据或本地验证日志；这些内容已被忽略且不得提交。
## ComfyUI 工作流库验收

工作流 round-trip 只做本地解析与重新编码，不向 ComfyUI、模型服务或第三方网络发送请求，也不会证明自定义节点或模型在任何 ComfyUI 实例中可用。它不创建任务、不产生费用，并且不会将外部工作流复制进仓库。

自动化测试只读取仓库内受版本控制的安全夹具。下面的两个 Downloads 文件只可作为 Task 7 的显式本地验收输入：它们不得被 pytest、前端测试或发布门禁自动读取，也不得复制或提交。资源 URL 元数据在导入/导出中作为惰性数据保留；该命令和产品均不会抓取、执行或渲染 URL。控制 URL（base/service/callback/webhook/server 与全部 endpoint 形式）以及认证、凭据、密钥和脚本字段仍会被递归拒绝。

在已安装项目锁定依赖的工作树中执行：

```bash
.venv/bin/python scripts/verify-comfy-workflow-roundtrip.py \
  tests/fixtures/comfy/core-load-save-workflow.json \
  '/Users/260413a/Downloads/▶▷MiniMaxH3-加速视频流整合.json' \
  '/Users/260413a/Downloads/贝尔尼尼Bernini+Studio工作流.json'
```

成功时每个输入只输出其 basename、检测到的格式、校验和前缀、节点数和连线数。命令绝不输出节点类型、widgets、提示词或原始 JSON；非零退出也只输出 basename 和稳定错误码。

相应的自动化发布门禁为：

```bash
PYTHONPATH=.:server .venv/bin/pytest -q \
  tests/server/test_comfy_workflow_json.py \
  tests/server/test_comfy_workflow_store.py \
  tests/server/test_comfy_service.py \
  tests/server/test_comfy_workflow_api.py \
  tests/integration/test_comfy_workflow_library.py \
  tests/server/test_app_security.py \
  tests/server/test_model_assignments.py
npm test --prefix web -- --run \
  src/test/admin-comfy-workflows.test.tsx \
  src/test/comfy-workflow-preview.test.tsx \
  src/test/comfy-workflow-node.test.tsx \
  src/test/extension-registry.test.ts \
  src/test/canvas-connections.test.tsx
npm run lint --prefix web
npm run build --prefix web
```

`npm run lint --prefix web` 是发布门禁；当前它执行前端 TypeScript typecheck，必须与测试和构建一起成功退出。

手工冒烟只可使用 Canvas 测试端口 `127.0.0.1:8992` 和 Git 忽略的全新数据目录：

```bash
mkdir -p .local-data
AICC_LOCAL_DATA="$(mktemp -d "$PWD/.local-data/task7-comfy.XXXXXX")" \
  AICC_LOCAL_PORT=8992 \
  bash scripts/run-local.sh
```

若要验证启用和普通用户可见性，管理员可创建一个仅位于上述临时目录内、普通文件且非符号链接的 ComfyUI 服务声明，然后使用：

```bash
AICC_LOCAL_DATA="/absolute/new-temporary-data-dir" \
  AICC_COMFYUI_SERVICES="/absolute/new-temporary-data-dir/config/comfyui-services.json" \
  AICC_LOCAL_PORT=8992 \
  bash scripts/run-local.sh
```

声明必须是该新数据目录 `config/` 内的普通非符号链接文件，只能指定测试用服务 ID 与数值回环 URL（`127.0.0.1` 或 `::1`）；本验收不启动、连接或请求真实 ComfyUI。启动前须确认脚本仅传入临时数据目录、该受信声明、`web/dist`、回环地址和端口 `8992`，绝不传入生产目录、生产端口或生产配置。

管理员应原样导入、预览并导出三个输入，启用仓库自写模板并派发给测试用户 A；测试用户 A 必须可列出并读取该模板，测试用户 B 的列表必须为空且读取该模板必须为 404。测试结束后确认 `8992` 已停止且生产端口/进程未发生变化。

本切片的最近一次隔离记录如下：三份输入均完成 editor 格式 round-trip；核心样例为 2 个节点/1 条连线，两个外部输入分别为 145/152 和 24/28。Python 门禁为 143 项通过（含本地 Comfy 服务声明启动保护与双用户目录隔离回归），前端定向门禁为 5 个文件、70 项通过，`lint` typecheck 与生产构建均成功（保留既有 chunk 警告）。隔离实例仅使用 8992、全新忽略数据目录内的 `config/` 声明以及无监听的数值回环服务 URL；三份工作流完成原样导入、预览和 editor 导出，核心样例完成启用与用户 A 派发，用户 A 可列出/读取，用户 B 列表为空且详情为 404。测试服务停止后，8992 未监听；9090、8787、8797、8891 保持监听，未被本次验证操作。
