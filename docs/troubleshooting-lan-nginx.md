# 排障：局域网 / 反向代理下页面 400、改密 403

## 问题现象

在本机用 nginx 等反向代理把请求转发到本地服务（`127.0.0.1:8992`）后：

- 浏览器能打开登录页，但登录后的**修改密码、登出等写操作一律失败**，接口返回 `403 FORBIDDEN`（`code=FORBIDDEN`，`phase=authentication`）；
- 部分场景下页面直接打不开，返回 `400`；
- 不挂代理、本机直接访问 `http://127.0.0.1:8992` 则一切正常。

## 根本原因

本地服务有一层 Host/Origin 安全边界，启动时只信任声明的 public origin（默认只有 `http://127.0.0.1:8992`）：

1. **TrustedHost 校验**：请求的 `Host` 头必须属于启动时声明的信任主机。nginx 转发客户端的 `Host: <局域网IP>` 时，与默认信任的 `127.0.0.1` 不符 → **400**。若 nginx 使用默认的 `proxy_pass` 且未设置 `proxy_set_header Host`，转发的是 `127.0.0.1:8992`，同样不符（信任列表里已换成局域网地址时）→ **400**。
2. **CSRF/Origin 校验**：`/api/v1/*` 的 POST/PUT/PATCH/DELETE 要求请求头 `Origin` 在 allowed_origins 白名单内，且携带与登录会话匹配的 `X-CSRF-Token`。经代理后浏览器自报的 Origin 是 `http://<局域网IP>:<端口>`，不在默认白名单内 → **403**。这正是"页面能开、改密必失败"的原因（GET 不查 Origin）。

两者都由启动参数 `--public-origin` 决定（`run-local.sh` 环境变量 `AICC_LOCAL_ORIGIN` / `AICC_LOCAL_ORIGINS`），因此与"监听地址"无关：把监听地址从 `127.0.0.1` 改成 `0.0.0.0` 并不能解决 403。

## 解决方法

### 1. 启动时声明浏览器实际看到的完整地址

协议、IP、端口必须与浏览器地址栏完全一致：

```bash
# localhost 直连与局域网/nginx 地址同时可用（多个 origin 用空格分隔）
AICC_LOCAL_ORIGINS="http://127.0.0.1:8992 http://192.168.30.36:8992" \
  bash scripts/run-local.sh
```

- 每个 origin 只接受 `http` + 私有 IPv4 或 `.local` 主机名；域名、公网 IP、`https://` 会被启动参数校验拒绝。
- 不需要 localhost 与局域网并存时，也可以直接用 `run-lan-local.sh`（监听 `0.0.0.0`，无需 nginx）：`AICC_LAN_ORIGIN=http://192.168.30.36:8992 bash scripts/run-lan-local.sh`。

### 2. nginx 必须原样转发客户端 Host

否则转发的是 `127.0.0.1:8992`，仍会被 TrustedHost 拒绝（400）：

```nginx
location / {
    proxy_pass http://127.0.0.1:8992;
    proxy_set_header Host $host;
}
```

## 验证结果（2026-08-17 实测）

| 配置 | Host: 局域网 IP | 改密（Origin=局域网 URL） |
| --- | --- | --- |
| 默认（只信任 `http://127.0.0.1:8992`） | 400 | **403**（复现测试者问题） |
| `AICC_LOCAL_ORIGIN=http://192.168.30.36:8992` | 200 | **200**（登录→改密→新密码重登全通过） |
| 双 origin（`AICC_LOCAL_ORIGINS`） | 200，localhost 同样 200 | 两种 Origin 下写操作均通过 |

## 相关边界与限制

- **HTTPS 不支持**：本地/LAN 模式是 HTTP-only。服务本身没有 TLS，直接 https 访问无法连接；`--public-origin` 也拒绝 `https://` 协议。nginx 配了 SSL 证书也无效——浏览器自报的 `Origin: https://...` 不在白名单内，改密仍会 403。需要 HTTPS 或公网域名时，走生产路径：`Internet → HTTPS 反向代理 → Portal 已登录挂载 /ai-canvas/ → Canvas`（见 README 第 7 节与 [installation.md](installation.md)）。
- **重置密码是命令行操作**，不是网页功能。忘记本地测试密码时，停止服务后运行：
  ```bash
  PYTHONPATH=server .venv/bin/python -m ai_creation_canvas reset-local-password \
    --data-dir .local-data --username canvas-user
  ```
  只显示一次新密码，并撤销该账号既有会话。
- 排查时先区分两层拦截：页面 `400` 是 Host 问题（nginx 的 `proxy_set_header Host` / origin 声明），接口 `403 FORBIDDEN` 是 Origin/CSRF 问题（`--public-origin` 与浏览器地址不一致）。
