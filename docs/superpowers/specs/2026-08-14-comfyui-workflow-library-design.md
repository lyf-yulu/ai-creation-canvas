# ComfyUI 受控工作流库与服务接入设计

日期：2026-08-14

状态：已获用户确认（2026-08-14）

适用仓库：`ai-creation-canvas`

实施方式：RAE 独立垂直切片。先交付可导入、可预览、可保真导出和可验证的服务适配器契约；不在本切片对真实 ComfyUI 或第三方模型发起任务。

## 1. 目标与范围

本切片让管理员将来自网络或本地 ComfyUI 的 JSON 工作流导入为平台内的、受控且可版本化的工作流模板。管理员可以在无限画布产品中查看只读预览、检查依赖、启停、派发给用户及导出。普通用户只能看到被派发且启用的模板，不能提交自己的工作流 JSON、服务 URL、节点 ID 或密钥。

同时，本切片定义并实现服务端 `ComfyWorkflowService` 适配器契约、配置校验和 mock 合同。实际部署时，管理员只需在服务器受保护配置中登记已安装的受信 ComfyUI 服务，即可由后续已实现的任务执行切片使用；浏览器不直接连接 ComfyUI。

本切片的自动化验收对象仅为仓库内新建的、安全且可提交的 ComfyUI 核心节点夹具。用户提供的下列两个文件只可由 Task 7 的显式本地 CLI 验收命令读取；它们不是 pytest 或其他自动化门禁的输入，也不会复制或提交到仓库：

1. `/Users/260413a/Downloads/▶▷MiniMaxH3-加速视频流整合.json`；
2. `/Users/260413a/Downloads/贝尔尼尼Bernini+Studio工作流.json`；
3. 仓库内新建的、只使用 ComfyUI 核心节点的最小保存格式工作流夹具。

不在本切片实现：嵌入或复刻 ComfyUI 编辑器、将每一种 ComfyUI 节点转换为画布节点、自动安装自定义节点/模型、从用户给出的 URL 下载工作流、真实付费或耗 GPU 的生成任务、用户自定义远程服务。

## 2. 调研结论与选型

ComfyUI 官方将浏览器可编辑的“保存格式”与提交给服务端执行的“API 格式”明确分开：保存格式包含节点位置、尺寸、分组和颜色；API 格式不含这些 UI 信息。两者都可能使用 `.json` 后缀，不能靠扩展名判断。

- 官方格式说明：[Workflow API Format](https://docs.comfy.org/development/api-development/workflow-api-format)
- 官方工作流模板库：[Comfy-Org/workflow_templates](https://github.com/Comfy-Org/workflow_templates)
- 已有产品设计中记录的参考项目：[T8-penguin-canvas](https://github.com/T8mars/T8-penguin-canvas)

官方模板库采用工作流模板、兼容性验证和审核发布的模式。本项目复用该模式，而不复制其前端或把任意第三方代码带入仓库。这样既避免重造完整节点编辑器，也避免把未知自定义节点的 JavaScript、任意 URL 或凭据引入浏览器。

选择“受控工作流库 + 服务端适配器”，而不选择以下方案：

- 每个 ComfyUI 节点映射成无限画布节点：需要长期兼容动态端口和大量第三方节点；给定样例已分别使用 38 和 15 种节点类型，其中存在 `rgthree`、`VHS`、`BerniniStudio`、`MiniMaxH3` 等自定义节点。
- iframe 或嵌入完整 ComfyUI 前端：会把浏览器直接连接、跨域、认证、用户归属和版本兼容问题带入产品边界。

## 3. 工作流数据模型

### 3.1 版本化模板

`ComfyWorkflowTemplate` 是平台稳定对象，包含：

- `workflow_id`：服务端生成的稳定、不透明 ID；展示名不能作为路由键。
- `display_name`、`description`、`enabled`、`archived_at`、`latest_revision`。
- `service_id`：管理员从已配置的受信 ComfyUI 服务中选择的稳定 ID；不保存可供浏览器读取的 URL。
- `execution_enabled`：仅当工作流具有已审核 API 格式、服务健康且管理员显式启用时为真。

`ComfyWorkflowRevision` 为不可变快照，包含：

- `revision`、`created_at`、`created_by`、`source_filename`、`source_format`、`canonical_checksum`。
- 可选的 `editor_workflow_json`：原始 ComfyUI 保存格式，供保真导出和带布局的预览。
- 可选的 `api_workflow_json`：原始 ComfyUI API 格式，供未来服务端提交。
- `node_inventory_json`：仅含节点 ID、节点类型、显示标题和计数的受限投影。
- `dependency_inventory_json`：去重后的节点类型列表和“核心/需确认”标记；它不执行、下载或加载任何节点。
- `input_schema_json`、`output_schema_json`、`parameter_bindings_json`：管理员审核后才可设置的公开输入/输出和 API 节点参数映射。首版导入的默认值为空，不能从节点 widget 自动推断为可公开参数。

同一 revision 至少有一种格式。若希望执行，必须提供已审核的 `api_workflow_json`；保存格式不能由本项目猜测转换为 API 格式。管理员应在 ComfyUI 中加载保存格式，再使用“Export Workflow (API)”生成对应 API 文件。此规则避免因丢失布局或自定义节点语义而生成错误任务。

`ComfyWorkflowAssignment` 把 `workflow_id` 派发到一个已验证的 `user_id`。管理员始终可见；普通用户的列表和画布只返回自己的已启用派发项。

### 3.2 格式判定与限制

导入端点只接受 `application/json` 的本地文件上传；服务端不抓取用户提供的 URL。文件名只用于审计显示，保存时不作为路径。

- 保存格式必须是对象，根部包含 `nodes` 数组和 `links` 数组；每个节点有唯一 ID 和非空 `type`，每条连线两端节点必须存在。
- API 格式必须是对象，根键为唯一的数字字符串，每个节点包含非空 `class_type` 和对象类型的 `inputs`。
- 每个文件最大 4 MiB、最多 500 节点、2,000 条连线、最大 JSON 嵌套深度 64、单个字符串最大 64 KiB；拒绝重复 JSON 键、非有限数字和无效 UTF-8。
- 通用资源 URL 元数据（例如 `url`、`resource_url` 或其大小写/分隔符变体）可作为原始工作流 JSON 的惰性数据保存和保真导出；本切片绝不抓取、执行、加载、渲染或在预览中投影这些值。
- 导入时仍递归拒绝所有控制 URL：`base`、`service`、`callback`、`webhook`、`server` 相关 URL，以及所有 `endpoint` 形式（包括大小写、空白和分隔符混淆的变体）。同样递归拒绝认证、凭据、密钥、请求头、令牌、密码、密钥引用、脚本、插件和代码字段。错误响应不回显原文件、提示词、URL 或节点参数。
- 规范化 checksum 对解析后的 JSON 采用 Unicode UTF-8、对象键排序、无多余空白的确定性编码并计算 SHA-256。导出文件可使用固定缩进；验收比较规范化 JSON 和 checksum，而不是字节空白。

导入完成时服务端保存经限制验证后的原始结构，并生成受限投影；不会加载其中引用的文件、模型、插件或远程资源。

## 4. API 与权限边界

对外 API 位于同源 `/api/v1`，所有请求使用当前 Portal 或本地会话解析的身份，忽略客户端自报的 user、角色、模板 owner、revision 或 service URL。

管理员端点：

- `POST /admin/comfy-workflows/import`：上传一个 JSON 文件和受控元数据，创建停用 revision。
- `GET /admin/comfy-workflows`、`GET /admin/comfy-workflows/{workflow_id}`：列出模板与受限 revision 投影。
- `GET /admin/comfy-workflows/{workflow_id}/revisions/{revision}/preview`：返回只读预览数据；不返回 API 节点的敏感 widget 文本。
- `GET /admin/comfy-workflows/{workflow_id}/revisions/{revision}/export?format=editor|api`：按请求导出该 revision 保存的原始格式；不存在的格式返回 `WORKFLOW_FORMAT_UNAVAILABLE`。
- `POST /admin/comfy-workflows/{workflow_id}/revisions`、生命周期和派发端点：创建新 revision、启停、归档、恢复和更改派发。历史 revision 永不原地改写。

普通用户端点仅返回已派发、已启用工作流的公开投影：`GET /comfy-workflows` 和 `GET /comfy-workflows/{workflow_id}`。在实际执行切片交付前，普通用户没有提交原始 JSON、查看 API JSON、配置服务或运行模板的端点。

所有变更端点维持现有本地身份档的同源 Origin 与 CSRF 要求。导出使用安全的 `Content-Disposition` 文件名、`application/json; charset=utf-8`，不反射管理员提交的源文件名。

## 5. 无限画布与管理界面

管理员在“ComfyUI 工作流库”页面完成导入、查看版本、节点依赖、格式可用性、服务健康、启停、派发和导出。依赖列表只报告兼容性，不提供一键安装插件或模型的操作。

预览使用仓库内实现的只读通用图形视图，而不执行或动态导入节点渲染代码：

- 保存格式按其保存的坐标显示通用节点框和连线；每个框只显示节点类型和安全标题。
- API 格式没有布局时显示稳定排序的节点/连线摘要，不伪造或声称还原其编辑器布局。
- 预览限制节点数，长 widget 值、提示词、潜在令牌和未知嵌套数据不渲染或记录到日志。

无限画布通过注册表新增一个通用 `comfy.workflow` 节点，而不是按外部 JSON 动态注册节点。节点元数据包含平台 `workflow_id`、固定 `revision`、公开输入/输出端口、显示名和执行可用性；它只调用将来的受控工作流 API。未配置或不健康的服务会在节点上显示不可运行原因，不能退化为浏览器直接访问 ComfyUI。

## 6. ComfyUI 服务适配器

新增独立的 `ComfyWorkflowService` 端口并注册到受信服务注册表，不修改现有通用图片/视频模型核心。端口至少定义：

- `health(context) -> ComfyServiceHealth`：使用受控服务的只读探测，报告 `healthy`、`unavailable` 或 `misconfigured`；启动失败不导致画布服务无法启动。
- `list_node_types(context) -> frozenset[str]`：从 ComfyUI 的受控节点定义端点取得可用节点类型，用于把 revision 的依赖清单标记为兼容或缺失。
- `submit(context, request) -> UpstreamJob`、`poll(context, upstream_job_id) -> JobState`、`cancel(context, upstream_job_id) -> None`：为后续任务执行切片保留的 API 格式提交、查询、取消契约；它们必须沿用现有 owner、幂等、未知提交不重放、任务快照与用量边界。

`ComfyWorkflowRequest` 只能包含服务端读取的 `workflow_id`、不可变 revision、经 schema 验证的公开参数、属于当前用户的 `asset_id` 和服务端生成的幂等键。参数到 API JSON 的映射由 `parameter_bindings_json` 的固定白名单完成；用户绝不能指定任意 `class_type`、节点 ID、输入名、URL、队列优先级或凭据。

服务配置采用新的、仅服务器可读的 `server/config/comfyui-services.example.json` 格式。每个条目有稳定 `service_id`、管理员配置的 `base_url`、超时和可选的服务器端认证引用。加载器必须要求常规文件、限制大小、解析后位于配置根目录、拒绝符号链接和未知字段；该配置及认证引用永不进入 API、前端状态、日志或 Git。开发和测试只允许测试端口与临时目录，绝不探测本项目指定的生产端口。

真实 ComfyUI 协议适配实现时，API 格式仅通过其受信服务端 API 提交；结果轮询、取消和媒体读取继续通过本平台的任务/资产边界，不把 ComfyUI 结果 URL 原样发给浏览器。

## 7. 错误处理与安全

- 格式、大小、深度、拓扑和敏感字段错误返回稳定错误码，不返回原 JSON、完整提示词、URL 或密钥片段。
- 重复导入相同格式和 checksum 不会创建重复 revision；同一模板的新内容只能创建下一 revision。
- API JSON 与保存 JSON 不能共享 checksum 时，管理员必须明确创建新 revision 或将两者作为同一 revision 的两个审核附件；后者只在节点库存与管理员确认一致时允许。
- 自定义节点缺失只影响兼容性和执行可用性，不阻止作为“仅预览/导出”的归档模板保存。
- 任何正常用户对未派发的模板、历史 revision 或导出端点都得到与现有资源隔离一致的拒绝结果。
- 导入文件、预览 payload、配置和测试夹具都不能包含真实密钥、真实用户资产、真实请求记录或生产目录引用。自动化测试只读取仓库内受版本控制的安全夹具；用户本地工作流仅能通过第 8.2 节的 Task 7 命令手工验证。

## 8. 验收与测试

### 8.1 自动化合同与安全测试

1. 保存格式、API 格式、重复键、非法 UTF-8、过深 JSON、超限节点/连线、断开连线和敏感字段分别有测试。
2. 自动化 round-trip 只使用仓库新建的最小核心节点工作流；用户本地样例只在下面的显式 CLI 人工验收中验证。
3. 资源 URL 元数据可保真导出但不进入预览；控制 URL、endpoint 形式、认证/凭据/密钥/脚本字段（含混淆变体）必须在任意嵌套层级被拒绝。
4. API 格式只能以 API 格式导出；保存格式只能以保存格式导出；缺少目标格式时不合成 JSON。
5. 两个测试普通用户之间、普通用户与管理员之间的列表、详情、导出和画布节点元数据严格隔离。
6. 浏览器测试验证上传过程不解析或持久化 JSON 内容，普通用户没有导入/导出管理入口，预览不注入 HTML/脚本，画布只注册一个静态 `comfy.workflow` 节点。
7. `ComfyWorkflowService` mock 合同覆盖健康、节点兼容、提交、查询、取消、幂等键和未知提交不重放；不调用真实 ComfyUI。
8. 配置加载测试拒绝符号链接、越界路径、生产端口、未知字段和非服务器端地址；服务不可用时保留工作流库功能并禁用执行。

### 8.2 人工验收

在测试端口 `8992` 与专用临时数据目录中，以管理员账号：

1. 仅通过 Task 7 的显式本地 CLI 命令读取两个给定 JSON，先检查其 round-trip 摘要；不将它们作为 pytest 输入、不复制到仓库。随后可导入它们，检查其依赖清单分别显示 MiniMaxH3/Bernini 等自定义节点为“需确认”。
2. 打开保存格式预览，确认画布图的节点数量和连线数量分别为 145/152 与 24/28。
3. 下载两份导出 JSON，使用 `jq` 解析，并验证规范化 checksum、节点数、连线数和节点类型列表与导入前一致。
4. 导入、预览和导出仓库自写的最小核心节点样例；检查其 JSON 在 ComfyUI 前端可加载（若已安装测试 ComfyUI）或至少通过保存格式结构合同。
5. 以两个普通用户分别登录，确认只看到各自被派发的模板，不能访问管理员 URL 或另一个用户的模板。
6. 在未登记服务和登记不可用测试服务两种状态下确认工作流节点均不可运行且没有浏览器直连；在 mock 健康服务下确认兼容性状态可见。

测试结束后确认 `8991`、`9090`、`8787`、`8797`、`8891` 的生产进程与端口未发生变化。

## 9. 交付边界与后续切片

本切片的可回滚交付只增加工作流库、服务适配器契约、配置示例、通用画布节点和测试；数据库迁移均为加法式。撤回时停用所有 ComfyUI 模板和服务装配即可，历史 revision 不删除。

后续“真实 ComfyUI 执行”切片必须在已隔离的测试实例上验证 API 格式、资产上传映射、结果流式回库、取消、用量、双用户隔离和未知提交不重放，再由管理员明确批准真实服务配置。它不得通过扩大本切片权限或让浏览器取得服务地址来实现。
