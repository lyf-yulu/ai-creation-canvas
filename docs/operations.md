# 运行与运维

## 构建发布包

在源码树任意子目录或其他工作目录执行：

```bash
bash /绝对路径/ai-creation-canvas/scripts/build-release.sh /新的/空/发布目录
```

输出目录必须是新建且非符号链接的目录，脚本不会删除既有目录。构建阶段使用锁定的 Node 依赖生成 `web/dist`；发布包仅包含 Python 服务、静态资源、锁定依赖说明、许可证、来源说明和运行文档。`manifest.sha256` 是按相对路径排序的文件校验清单，不包含构建时间、主机路径或用户名。

`--skip-web-build` 只用于已验证的 `web/dist`，并会检查入口页和 JavaScript 资源是否存在；缺失或不完整时会失败。

## 运行发布包

从发布目录安装 `requirements.lock` 中的 Python 依赖，并显式提供服务声明、数据目录和静态文件路径。生产运行只需要 Python 和预构建静态文件，**不需要 Node 或 Bun**。Node/Bun 仅允许用于源码构建和前端测试。

```bash
PYTHONPATH=server python -m ai_creation_canvas \
  --environment production --port 8991 \
  --data-dir /受控/画布数据 \
  --portal-internal-token "由部署系统注入" \
  --portal-base-url "https://受信-portal.example" \
  --services-config server/config/services.example.json \
  --static-dir web/dist
```

`server/config/services.example.json` 仅是无密钥的声明模板，必须替换 mount 与服务标识并经审批。可使用同样的显式参数追加 `--check-config`，在启动 HTTP 服务前验证服务声明是否能被加载；该检查不会连接模型服务。

`/api/v1/session` 必须由 Portal 已验证身份调用；无身份请求返回 `401`。根路径和前端嵌套路由由静态 SPA 返回。当前版本没有未认证健康检查接口，运行监控应使用平台进程健康机制和经认证的 API 检查。

## 环境、端口和数据隔离

建议生产画布端口为 `8991`，隔离测试画布端口为 `8992`；Portal/图像/视频/人像测试端口分别为 `9190/8798/8788/8892`。测试数据必须位于专用临时目录，绝不能复用生产 state、outputs、uploads、logs 或数据库。

配置只声明环境、端口、数据目录、受信 Portal 地址、允许挂载点、服务声明和密钥引用。不要把密钥、Cookie、提示词、媒体内容、结果地址或请求记录写进配置、日志或发布包。浏览器端没有 API Key 配置。

## 备份、回滚与日志

备份只在受控生产环境中处理经审计的数据目录；发布包本身可由 manifest 校验后重新构建。回滚采用上一个已验证发布包和对应的服务配置，不复制或恢复测试临时目录。日志只保留最少的错误码、请求 ID、时间和服务标识；禁止记录密钥、Cookie、完整提示词、上传内容、长期结果地址与原始请求体。

## Portal 补丁审批

Portal 薄代理补丁必须先在隔离测试副本中审查：验证挂载路径、签名身份替换、Cookie 转发、响应头过滤和双用户归属隔离。任何涉及生产 Portal、密钥、端口、状态或守护进程的操作均需要单独书面批准；本项目不会自动重启服务或操作 launchd。

## 排障

- 静态页面返回 404：确认发布包包含 `web/dist/index.html`，并以带 `Accept: text/html` 的请求访问 SPA 路由。
- API 返回 401：检查 Portal 签名身份是否由受信边界注入，不要在浏览器伪造用户字段。
- 资产或任务返回 403：确认使用同一 Portal 用户；跨用户访问按设计被拒绝。
- 模型不可用：检查服务端数据目录中的受控服务声明和模型能力，不在浏览器增加自定义 URL 或脚本。
