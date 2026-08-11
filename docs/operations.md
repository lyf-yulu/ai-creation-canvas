# 运行与运维

## 本地 Slice 1 验证

在源码仓库根目录运行一条命令：

```bash
bash scripts/run-local.sh
```

脚本会锁定前端依赖、构建真实 UI，并仅在 `127.0.0.1:8992` 启动本地服务。首次运行会在终端显示一次 `canvas-admin` 和 `canvas-user` 的随机初始密码，并打开 `http://127.0.0.1:8992/login`；后续启动不会再次显示旧密码。本地数据库与会话保存在已被 Git 忽略的 `.local-data/`。

Slice 1 只启用不联网、不收费的 `本地演示图片` 模型。浏览器没有 Key 输入框，真实模型 Key 仍应由管理员在未来部署的服务端配置和派发。本阶段不提供 Cloudflare Quick Tunnel 或生产部署命令；正式 Cloudflare、域名与多地区入口放在后续 Slice 6。

## 本地真实媒体验证（可选、会产生费用）

当管理员已经在启动服务的终端环境中设置了 `ARK_API_KEY` 时，可运行：

```bash
bash scripts/run-real-media-local.sh
```

该入口会使用 `127.0.0.1:8994` 和已忽略的 `.local-real-media-data/`，与默认离线演示数据隔离。它只从服务端环境读取 Key，并通过 `server/config/ark-models.example.json` 注册管理员可审核的模型声明；声明文件不包含 Key、URL、脚本或浏览器可执行内容。默认包括 Seedream 文生图和 5 秒 Seedance 文生视频。普通用户只会收到管理员派发的模型名称、能力与安全参数，不能输入或查看 Key。

真实任务结果会先被服务端下载到受限本地结果目录，再以同源、按用户归属的结果接口提供给画布。不要提交 `.local-real-media-data/`、终端输出、提示词、结果文件或任何环境变量。未设置 Key、模型未开通、或模型参数不支持时，界面只显示受控错误信息；不要通过反复重试来探测付费模型。

如遗失本地测试密码，可停止本地服务后执行：

```bash
PYTHONPATH=server python -m ai_creation_canvas reset-local-password \
  --data-dir .local-data --username canvas-user
```

命令只显示这一次的新密码，并撤销该账号的既有会话；再次登录必须修改初始密码。

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
  --max-image-upload-mib 10 \
  --max-video-upload-mib 64 \
  --max-audio-upload-mib 32 \
  --upload-concurrency 4 \
  --user-asset-quota-mib 2048 \
  --total-asset-quota-mib 10240 \
  --static-dir web/dist
```

三类单文件上传上限由管理员在本地或生产启动入口中显式设置，单位为 MiB，可选范围均为 1–2048。默认值适合本地和轻量代理测试：图片 10 MiB、视频 64 MiB、音频 32 MiB。服务端默认同时解析最多 4 个上传请求，并对单用户和全站本地资产分别执行 2048 MiB、10240 MiB 的原子配额检查；上调前应同时核对反向代理、Cloudflare、磁盘容量和服务器请求体限制。前端每个媒体集合最多保留 30 个已提交、排队或失败待重试的条目，并在整个页面共享最多 3 个实际上传连接。

这里的并发保护是轻量部署边界：前端的 3 个连接只覆盖一个浏览器页面，Python 信号量只覆盖一个服务进程。多进程或多副本部署若要保证全站并发上限，必须由反向代理、共享队列或集中式限流设施统一执行，不能依赖各进程自己的计数器。

人像资产在本地会先预留配额和记录；若进程在尚未取得可恢复的上游资产标识前退出，重启后该记录会明确标记为失败并保留本地文件，用户需重新上传。当前 Portal 上传契约没有幂等键，也不能按本地请求标识查询上游结果，因此尚不能承诺跨进程故障下的外部 exactly-once。真实 Key 里程碑不以人像外部调用为放行条件；生产强化前必须先扩展 Portal 契约，提供幂等上传或可恢复的请求标识查询。

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
- 资产返回 403，或任务/结果返回 404：确认使用同一用户；任务和结果会隐藏跨用户资源是否存在。
- 模型不可用：检查服务端数据目录中的受控服务声明和模型能力，不在浏览器增加自定义 URL 或脚本。
