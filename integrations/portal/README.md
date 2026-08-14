# Portal 薄代理测试集成

此目录是 **AI 创作画布** 与 Portal 的最小、可审阅集成材料。它不包含
Portal、图像、视频或人像服务的源码、配置、密钥、状态或输出。

## 边界

- 仅允许 Portal 的已登录会话访问 `/ai-canvas/`；生产 Canvas 上游只能配置为
  回环变量/Host（例如 `AICC_CANVAS_UPSTREAM_HOST=127.0.0.1` 与受控端口），
  浏览器不能选择上游地址，也不能把 Canvas 配为公开域名。Portal 过滤传入的
  外部 `Host`，并以该受控回环 Host 访问 Canvas；因此 Canvas 使用
  `--trusted-host 127.0.0.1`，不信任公开域名。
- 公网路径固定为 `Internet → HTTPS 反向代理 → Portal 已登录挂载 /ai-canvas/ →
  Canvas 127.0.0.1`。Portal 完成会话校验后签发身份，Canvas 不接受外部浏览器
  直接访问。**不得暴露 Canvas 监听端口**；后端防火墙应拒绝公网访问该端口。
- 公开反向代理必须终止 TLS、启用 HSTS、限制请求体、设置上游超时和速率限制，
  并对访问/错误日志做脱敏，不能记录 Cookie、签名、Key、完整提示词或上传内容。
  当前仅支持单个 Python Canvas 副本；Redis 只提供提交租约，不能作为多副本的
  持久化任务队列或故障接管依据。本仓库尚未配置真实服务器、域名或 DNS。
- Portal 在转发前删除所有浏览器提供的 `X-Portal-*` 身份和签名头，再从
  已认证会话中的 `user_id`、`role` 与 `username` 签发 v2 身份。v2 的规范
  载荷为 `v2\\n{timestamp}\\n{user_id}\\n{role}\\n{rfc3986(username)}`，用
  HMAC-SHA256 计算，验证端须使用常量时间比较和过期窗口。
- 生成请求仍通过 Portal 的受控服务。画布不产生 `X-Job-Id` 或第二份用量
  事件，因此每个底层生成任务只记一次 Portal 用量。
- `signed-identity-v2.patch` 是针对其文件上下文精确匹配的 Portal `app.py`
  形状的窄补丁；任何上下文或版本不匹配都必须失败，不能手工模糊套用。
- 该合成固定基线明确提供 portal_authenticated_user(request) 与
  app.state.portal_internal_token。补丁在启动和每次代理前验证前者可调用、
  后者至少 16 个字符；缺失或不合格会明确失败，绝不在首次请求时以
  NameError 方式退化。真实 Portal 若不具备相同受审阅接口，补丁必须拒绝
  应用或启动，不能靠浏览器头、环境变量或用户注入隐藏依赖。
- 请求会按大小写删除全部 X-Portal-*，并过滤 Connection 及其列出的
  token、全部 RFC hop-by-hop 头、Host 与客户端 Content-Length；响应也过滤
  hop-by-hop、X-Job-Id 与 X-Usage。Portal 的既有下游生成计量路径才是唯一
  用量记录方，Canvas 薄代理不自行计量。
- 代理对请求体采用 32 MiB 硬上限：先预检 Content-Length，再逐块累计至临时
  spool 文件；缺失或伪造长度同样受限，超限返回 413 且不会连接上游。该限额足以
  覆盖验证阶段的常规资产提交；更大的资产应经专用受控上传接口，而非薄代理。
  上游响应采用流式转发并在正常、异常或客户端中断时关闭上游响应与客户端；
  HEAD、204 和 304 不发送正文，且不跟随重定向。

## 仅夹具验证

在本仓库根目录执行自动测试即可创建临时的合成 Portal 源目录并检查复制、
补丁、身份与隔离契约：

```bash
PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_portal_contract.py
```

脚本要求明确给出 **绝对路径** 的源与目标。目标只能是本仓库
`work/portal-test-*` 下尚不存在的目录。它只复制允许的 Python、静态文件、
`app_spec.py` 和去敏配置骨架，递归排除 `.git`、`.env*`、密钥/证书、状态、
输出、归档、上传、日志、缓存、数据库、请求记录及生成子应用。

```bash
bash scripts/prepare-portal-test-copy.sh /absolute/path/to/portal-source \
  /absolute/path/to/ai-creation-canvas/work/portal-test-example
```

脚本写入 `ai-canvas-test.json`，其中只含测试端口 `9190`、画布 `8992` 和新建
`test-data` 路径。若复制或补丁失败，会移除本次新建的目标；它永不覆盖已有路径。

## 真实测试、回滚与许可

真实烟雾测试仅能在用户另行启动并确认的测试实例（Canvas `8992`、Portal
`9190`、代表图像 `8798`、视频 `8788`、人像 `8892`）上进行。本任务不启动、
连接或探测这些端口，更不触碰生产目录、生产端口、launchd 或生产数据。

在用户批准前不得将补丁应用到生产 Portal。回滚方式是停止使用并删除该次新建
的 `work/portal-test-*` 副本；不要修改原始 Portal。此集成材料继承并遵守本仓库
AGPL-3.0-or-later 发布义务；应用时还须保留 Portal 自身的许可证和版权声明。
