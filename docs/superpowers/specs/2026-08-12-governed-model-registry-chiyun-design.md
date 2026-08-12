# 受控模型注册表与 Chiyun GPT-Image-2 设计

日期：2026-08-12

## 1. 目标与必要性判断

本设计解决三个上线前必须统一的问题：

1. 新增模型不能继续散落在前端条件、静态 JSON 和不同供应商分支中。
2. 普通用户的模型使用权必须可由管理员分配和立即收回，浏览器不能接触供应商凭据。
3. 多用户任务必须具备持久幂等、并发上限和可恢复状态，不能只依赖单进程内存。

采用“稳定适配器代码 + 动态模型对象”有必要；采用数据库中的任意 Python 类、脚本或自由请求模板没有必要且不安全。新增同协议模型只创建对象；新增全新协议仍需新增并审查适配器代码。

首个纵向切片是 Chiyun `gpt-image-2` 图生图：普通用户选择管理员分配的模型，将提示词和有序参考图提交到独立 Chiyun 适配器，结果回填画布。

## 2. 架构边界

系统采用模块化单体：

- React 只消费安全模型目录，按声明渲染节点、端口和参数。
- FastAPI 负责鉴权、授权、合同校验、幂等和任务生命周期。
- Provider Adapter 负责供应商协议、错误映射和结果物化。
- SQL 数据库是 Provider、模型、权限、任务和幂等记录的最终事实来源。
- Redis 用于跨进程任务队列、并发令牌、短期权限缓存和通知；不作为最终事实来源。
- 本地单机允许显式开发模式无 Redis；正式多用户部署启动时必须验证 Redis 可用，否则拒绝启动。

浏览器永远不直接访问供应商，也不接收 Base URL、Credential Reference、参数映射或原始供应商错误。

## 3. 数据模型

### 3.1 ProviderDefinition

字段：

- `provider_id`：稳定唯一标识。
- `display_name`：管理员可读名称。
- `adapter_type`：代码中已注册的适配器类型，例如 `ark`、`chiyun_openai_images`。
- `base_url`：仅允许 HTTPS Origin；禁止用户信息、查询、片段和任意路径。
- `credential_ref`：指向部署密钥存储；不保存明文 Key。
- `enabled`、`revision`、审计时间和操作者。

管理员只能选择服务端注册表中已有的 `adapter_type`，不能上传代码、动态模块或请求脚本。

### 3.2 ModelDefinition

字段：

- `model_id`、`provider_id`、`provider_model_name`。
- `display_name`、`introduction`。
- `modality`：`image`、`video`、`audio` 或 `text`。
- `operations`：明确的用途合同，例如 `image.generate`、`image.edit`、`video.generate`。
- `operation_contracts`：每个用途独立声明输入端口、输出类型、参数 Schema、参数映射和能力限制。
- `enabled`、`revision`、审计时间和操作者。

模型不能依靠名字推断用途。一个模型可以显式支持多个 Operation，但各 Operation 的端口和参数必须独立；图片参数不会出现在视频用途，视频素材也不能进入图片适配器。

### 3.3 ModelAccess

字段：

- `user_id`、`model_id`。
- `granted_by`、`granted_at`、`revoked_at`。
- 可选的用户级并发和用量策略引用。

数据库约束保证每个用户与模型只有一个当前关系。管理员分配或收回时先提交数据库，再使 Redis 权限缓存失效。收回后禁止创建新任务；已经提交的任务和结果仍按原归属保留。

## 4. 用途隔离与验证

用途合同在四层执行：

1. 目录层：图片节点只返回图片 Operation，视频节点只返回视频 Operation。
2. 画布层：只渲染当前 Operation 声明的命名输入、输出端口和参数。
3. API 层：重新解析模型对象，验证权限、Operation、端口、输入数量、媒体归属和参数，不信任浏览器目录快照。
4. 适配器层：再次限定支持的 Operation 和供应商字段，白名单重建请求体。

提交时保存不可变快照：模型 ID、模型 Revision、Provider ID、Adapter Type、Operation、参数、输入资产顺序和提示词。管理员之后修改模型对象不会改变已经提交任务的含义。

## 5. Chiyun GPT-Image-2 纵向切片

Provider：

```text
provider_id: chiyun
adapter_type: chiyun_openai_images
base_url: 管理员配置的 HTTPS Origin
credential_ref: chiyun-primary
```

模型：

```text
model_id: chiyun-gpt-image-2
provider_model_name: gpt-image-2
modality: image
operations: [image.edit]
inputs: prompt(1), reference_images(1..N, ordered)
output: image
parameters: size, output_count
```

请求由服务端构造 `POST /v1/images/edits` multipart：所有参考图按画布集合顺序作为 `image[]` 上传；只发送声明字段。第一版不宣称文生图，不支持无参考图请求。响应只接受有界 JSON 对象和有界图片结果，远程 URL 必须经现有安全下载器物化到本地私有结果存储。

Base URL 和 Key 由管理员/部署者配置，普通用户创建的模型对象不能覆盖。上游 401/403、429、5xx、超时和无效结果分别映射为受控错误，不向浏览器泄漏响应体。

## 6. 幂等、并发和任务恢复

### 6.1 幂等

- 客户端每次明确运行生成一个 Idempotency Key；网络结果不确定时重试复用原 Key，用户再次点击新任务使用新 Key。
- SQL 对 `(user_id, service_id, idempotency_key)` 建唯一约束，并保存规范化请求摘要。
- 同 Key、同摘要返回原任务；同 Key、不同摘要拒绝。
- 供应商不支持原生幂等时，队列消费者先通过数据库状态机取得唯一执行权，避免应用层重复提交。

### 6.2 并发

- Redis 队列将 HTTP 接收与供应商调用解耦。
- 令牌按全局、Provider、模型和用户四级限制，最小有效额度决定是否执行。
- SQL 条件更新控制 `queued -> running -> terminal`，Redis 锁只用于协调，锁丢失不能绕过数据库状态。
- Worker 使用租约和心跳；租约过期的任务只在确认尚未记录供应商提交标识时重新执行。

### 6.3 恢复

- 任务、不可变提交快照、供应商任务 ID 和结果记录持久化。
- 浏览器刷新只执行 GET 恢复，不重新 POST。
- 同步完成的 Chiyun 图片请求也先保存供应商结果，再以条件更新提交成功状态；异常退出后可从持久记录继续物化或明确失败。

## 7. 安全和运维

- Key 使用环境变量或部署密钥存储，由 `credential_ref` 间接引用；禁止写入模型表、日志、浏览器或 Git。
- 管理 API 仅管理员可用，所有创建、修改、启停、分配和收回操作写审计记录。
- Provider Base URL 只允许管理员修改，并采用固定 HTTPS Origin、禁重定向和受控 DNS/出口策略。
- 参数 Schema 使用现有安全子集；数量、字符串长度、嵌套深度和媒体总字节均有界。
- Redis 不保存原始提示词、Key 或媒体内容，只保存不透明任务 ID、令牌和短期状态。
- 正式部署健康检查同时验证数据库、Redis和配置的凭据引用；缺失时对应 Provider 不进入可分配目录。

## 8. React 管理与普通用户界面

管理员界面提供：

- Provider 状态查看和受控配置引用。
- 创建/编辑/启停模型对象。
- 选择代码内注册的适配器和 Operation 模板，再填写受控参数；不提供自由 JSON 请求编辑器。
- 给用户分配或收回模型，并查看当前并发/可用状态。

普通用户接口只返回已授权、已启用且 Provider 配置健康的安全 `ModelSpec`。React 继续使用统一模型节点，不增加 Chiyun 专用页面；画布只根据 Operation Contract 渲染端口和参数。

## 9. 错误处理

- 无权限或权限已收回：提交前返回受控 403，目录刷新后模型消失。
- 模型被停用或 Revision 不可用：新任务拒绝；旧任务按快照继续查询。
- Redis 不可用：开发模式可使用有界进程内队列；生产模式拒绝接受新生成任务并报告服务暂不可用。
- 上游拒绝或异常：保存安全错误码和重试属性，不保存/返回原始响应体。
- 结果物化失败：任务保持可诊断终态，不生成指向供应商临时 URL 的画布节点。

## 10. 验证与阶段边界

第一阶段完成条件：

1. 管理员能创建 Chiyun Provider 和 `gpt-image-2` 模型对象，并分配/收回普通用户权限。
2. 普通用户只在图片节点看到该模型，视频节点不可见；绕过 UI 的错误 Operation 同样被拒绝。
3. 有序多参考图、提示词、尺寸和数量准确进入离线模拟的 multipart 请求。
4. 同 Key 并发提交只产生一个供应商调用；权限收回立即阻止新任务。
5. 任务成功后图片结果回填画布，刷新后可恢复；跨用户不可读。
6. React、FastAPI、SQL和Redis边界具备自动化合同测试，并提供隔离本地实例供用户手工验收。

本阶段不读取生产 Key、不调用付费服务。用户验收离线链路后，再单独决定是否配置 Chiyun凭据并进行一次有界真实调用。
