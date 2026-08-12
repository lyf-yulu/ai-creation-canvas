# RunningHub / ComfyUI 受控接入提案

状态：待用户验收当前模型线路切片后审批。本文只定义下一切片边界，不在当前版本开放远程执行。

## 目标

允许管理员把自己或他人的 RunningHub 工作流复制为平台内的“工作流模板”，普通用户在无限画布中选择该模板、填写管理员公开的参数并执行。浏览器不接触 RunningHub API Key、任意远程地址或完整工作流内部配置。

RunningHub 官方高级创建接口支持 `workflowId`，也支持提交完整 `workflow` JSON 字符串；完整 JSON 会覆盖 `workflowId`。因此平台应把“复制工作流”做成管理员导入、审核和版本化，而不是普通用户每次请求时直接传入任意 JSON。参考：

- [RunningHub API 文档](https://www.runninghub.ai/runninghub-api-doc-en/doc-8287465)
- [高级创建任务](https://www.runninghub.ai/runninghub-api-doc-en/api-425761093)
- [获取工作流 JSON](https://www.runninghub.ai/runninghub-api-doc-en/api-425761094)
- [取消任务](https://www.runninghub.ai/runninghub-api-doc-en/api-425761095)

## 数据对象

`WorkflowTemplate` 至少包含：

- `workflow_id`：平台稳定 ID，不使用展示名做路由。
- `provider=runninghub` 与受控 `credential_pool_ref`。
- `source_workflow_id` 或审核后的 `workflow_json`，二者不能由普通用户修改。
- `revision`、`checksum`、`enabled`、`archived_at`。
- 公开的输入端口、输出媒体类型、参数 JSON Schema。
- 参数到 RunningHub `nodeInfoList` 的固定映射。
- 最大执行时间、是否可取消、允许的实例类型。

完整工作流 JSON、API Key、内部节点标题和调试信息不进入普通用户目录。

## 管理员导入流程

1. 管理员输入 RunningHub 工作流 ID，或上传导出的 JSON 文件。
2. 服务端限制 JSON 字节数、嵌套深度、节点数量和字符串长度，并拒绝访问器、非 JSON 值与任意 URL 字段。
3. 服务端生成规范化 checksum；管理员明确选择哪些节点参数对普通用户公开。
4. 保存为停用模板；离线合同测试通过后才允许启用。
5. 更新创建新 revision，旧任务继续引用不可变快照，不原地篡改历史任务。

## 用户执行与统计

1. 画布工作流节点只提交平台 `workflow_id`、revision、受控参数和 owned asset ID。
2. 服务端从会话确定 `user_id`，校验工作流授权与资产归属，生成用户范围幂等键。
3. 服务端读取凭据池并调用 RunningHub；返回的 `taskId` 先持久化，再释放租约。
4. 轮询、取消、结果下载沿用现有任务状态机；未知提交结果不自动重放。
5. `canvas_jobs.user_id`、逻辑 workflow ID、route snapshot 一起保存，管理员统计按服务端 owner 聚合。

## 安全与上线门禁

- API Key 只存在部署凭据文件或秘密管理器中，不能进入数据库投影、浏览器、日志和 Git。
- 普通用户不能指定 base URL、workflow JSON、callback URL、实例类型或任意节点 ID。
- 上传素材先进入本平台 owned asset，再由受控适配器转交；禁止服务端抓取用户任意 URL。
- RunningHub 消费级接口的并发和稳定性不能视为生产保证；多用户上线需服务端全局并发、每用户配额、超时、取消和供应商熔断。
- 离线 mock 合同、双用户归属、幂等/未知提交、取消竞态和结果 Range 测试全部通过后，才做一次最小真实工作流付费冒烟。

## 下一切片建议

先实现“管理员导入/导出 + 只读预览 + mock 执行合同”，让用户验收工作流复制和参数公开方式；批准后再实现 RunningHub 真实适配器和付费冒烟。这样不会把任意 ComfyUI 执行权限混入当前已验证的模型任务核心。
