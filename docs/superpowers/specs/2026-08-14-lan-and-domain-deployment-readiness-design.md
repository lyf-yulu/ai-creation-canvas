# 局域网与域名部署准备设计

日期：2026-08-14  
状态：已获用户确认

## 目标

在不连接真实 Portal、生成服务、服务器或域名的前提下，为 AI 创作画布补齐两条安全、可验证的部署入口：

1. 维护者可以显式将离线本地模式开放给同一局域网的设备使用。
2. 未来服务器可把 Canvas 保持在回环地址，由 HTTPS 反向代理和已登录 Portal 挂载到域名；仓库提供明确的启动、健康检查和反代契约。

默认本地体验仍只监听 `127.0.0.1`。本切片不创建服务器、不申请域名、不修改 Portal 生产实例、不探测生产端口，也不增加跨域浏览器调用。

## 已确认架构

```text
局域网设备 ── HTTP（显式 LAN Origin） ──> Canvas 本地身份服务

互联网 ── HTTPS/域名 ──> 反向代理 ──> Portal 已登录挂载 /ai-canvas/
                                               │ 重新签发 Portal v2 身份
                                               ▼
                                      Canvas（仅 127.0.0.1）
```

公网入口由 Portal 负责登录会话和签名身份。Canvas 不接受浏览器伪造的 `X-Portal-*` 头，也不应直接暴露 Uvicorn 端口。Canvas 与前端始终使用同源相对 `/api/v1`，因此不加入 `CORSMiddleware`；Cookie + 精确 Origin + CSRF 校验继续作为本地身份模式的状态变更防护。

## 方案比较

### 方案 A：默认绑定所有网卡并放宽 Origin

实现看似最短，但局域网访问会让所有可达来源获得写接口机会，且容易以通配 CORS 破坏 Cookie/CSRF 边界。拒绝。

### 方案 B：显式 LAN 启动参数与精确公共 Origin（采用）

`serve-local` 默认回环监听；只有维护者明确指定非回环监听地址及一个或多个 `--public-origin` 时才开放 LAN。应用只接受这些完整 Origin 的变更请求。优点是默认安全、直接可测试且不依赖反向代理；代价是维护者需要填写设备实际访问的 IP 或 `.local` 名称。

### 方案 C：直接将 Canvas 公网化

会绕开 Portal 的登录、身份重签名和单次用量语义，也使 Python 服务直接承担 TLS、入口限流和攻击面。拒绝。

## 组件与接口

### 1. 监听与 Origin 配置

`serve-local` 增加：

- `--host`：Uvicorn 绑定地址，默认 `127.0.0.1`。
- 可重复的 `--public-origin`：浏览器实际访问的完整 `http(s)://host[:port]` Origin。

当 `--host` 为回环地址且未指定 `--public-origin` 时，应用只信任默认 `http://127.0.0.1:<port>`。当绑定地址为 `0.0.0.0` 或非回环 IPv4 地址时，至少一个 `--public-origin` 为必填；拒绝 `*`、用户信息、路径、查询、片段和不安全格式。首版 LAN 入口只支持 IPv4 监听，且只接受私有/回环 IPv4 或 `.local` 设备名的 HTTP Origin，避免把本地身份模式误配置为公网入口。

监听地址与浏览器 Origin 绝不互相推断：`0.0.0.0` 只能表示监听所有接口，不能成为浏览器 Origin。

`Settings` 新增规范化后的 `trusted_hosts`。本地模式从已批准的 public origin 提取；通用/生产 CLI 则由重复 `--trusted-host` 显式提供，生产启动缺失时必须失败。应用启用可信 Host 检查，拒绝 Host 注入。不得相信来自互联网直连客户端的 `X-Forwarded-*`；反向代理只可在网络层向回环 Canvas 转发标准 Host。

### 2. 公网/Portal 部署契约

通用 `python -m ai_creation_canvas` 继续默认绑定 `127.0.0.1`，新增 `--host` 与重复 `--trusted-host`，为反向代理环境提供受控接口。生产模式必须显式提供可信 Host，且部署指南说明：

- 公网防火墙不得暴露 Canvas 监听端口。
- TLS、HTTP 到 HTTPS 跳转、HSTS、请求体上限、速率限制和代理访问日志脱敏由公开入口反向代理负责。
- 反向代理先把请求交给 Portal；Portal 删除浏览器给出的 `X-Portal-*`、从已认证会话重新签名，然后才把 `/ai-canvas/` 转发至 Canvas 回环地址。
- Portal 薄代理的 32 MiB 上限、流式转发、hop-by-hop 头过滤和唯一用量记录约束保持不变。大文件不经该薄代理，待专用受控上传流程接入。
- 第一阶段生产限制为一个 Canvas Python 副本配合 Redis 协调；Redis 不是持久工作队列，不能宣称任意横向扩容。

本仓库不提供会绕过 Portal 的公网反代配置。`integrations/portal/` 仍只保存无密钥、可审阅的 Portal 集成材料。

### 3. 健康检查

新增未认证 `GET /healthz` 与 `GET /readyz`，仅返回固定、无拓扑信息的状态 JSON：

- `/healthz` 表示 Python 进程可响应。
- `/readyz` 仅在已配置的静态入口 `index.html` 为安全常规文件时返回成功；不连接 Portal、Redis 或模型服务，避免健康检查泄露依赖地址或触发外部动作。

这些端点不属于 `/api/v1`，不返回版本、路径、账号、密钥、连接字符串或上游状态。

### 4. 文档和本地验证入口

`scripts/run-local.sh` 保持回环默认。新增一个显式、参数检查严格的 LAN 包装入口，使用测试端口和独立 Git 忽略数据目录，并且不自动打开浏览器。安装与运维文档分别说明 LAN 启动、另一设备验收、生产 Portal/反代拓扑、失败回滚和“不得直连公网”的限制。

## 错误处理与安全边界

- 错误 Origin、缺失 CSRF、未知 Host、无效 LAN 参数均返回受控错误或在启动前失败；不回显有效 Origin、配置路径或密钥。
- 不新增宽松 CORS、动态 Origin 回显、任意 Host、任意反向代理信任或浏览器端 API URL 配置。
- Local HTTP Cookie 仅限开发/LAN；生产环境已有的 `Secure` Cookie 策略不变。公网生产身份优先由 Portal 会话与签名身份维护。
- LAN 验收只能使用 `8992` 或其它非生产测试端口、临时目录和离线 Demo；测试结束核对生产端口未变化。

## 测试策略

先按 TDD 增加以下行为测试：

1. `Settings` 拒绝不合法 public origin/trusted host，生产 CLI 缺少 trusted host 不能启动。
2. 默认 local CLI 仍绑定回环；LAN 绑定没有显式 public origin 失败；合法 private-IP 或 `.local` origin 能传入应用。
3. LAN Origin 的登录、会话、CSRF 写请求成功；端口不同、`null`、通配或非白名单 Origin 的写请求为 403。
4. 可信 Host 通过、伪造 Host 为 400；直接客户端伪造的转发头不改变该结果。
5. `/healthz` 可用且无敏感字段；`/readyz` 在安全静态入口存在时成功、缺失时受控失败；既有 API 身份和静态路径安全测试继续通过。
6. Shell 包装脚本的参数校验使用测试桩验证，不启动真实网络服务。

实现后运行新增定向 pytest、既有本地身份/应用安全/CLI 测试、Python 全量测试、前端测试和构建、安全扫描。局域网实机验收只在用户启动的隔离环境进行；当前切片不将实机外部网络可达性作为自动化前提。

## 非目标

- 不部署域名、证书、Caddy/Nginx、Portal 或云服务器。
- 不修改 Portal 生产源码或实例，也不读取其密钥、状态、日志或结果。
- 不实现跨域前端/API、WebSocket、负载均衡、多地区、多副本 durable worker 或公网自注册。
- 不变更模型、任务、资产、用量和凭据池的业务核心。
