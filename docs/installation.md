# AI 创作画布安装与迁移指南

本文面向拉取源码后自行部署的维护者。应用可以独立运行，不读取相邻 `ai-generation-portable-apps` 目录，也不要求把数据库、媒体或 Key 放进发布包。

## 1. 系统要求

- 构建机器：Git、Python 3.12、Node.js 与 npm。
- 运行发布包：Python 3.12；生产受管模型还需要 Redis。
- 生产入口前配置 HTTPS 反向代理，并只允许受信 Origin。
- 数据目录、配置目录和发布目录必须分离；配置目录只允许服务账号和管理员访问。

## 2. 五分钟离线体验

```bash
git clone <你的仓库地址> ai-creation-canvas
cd ai-creation-canvas
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
bash scripts/run-local.sh
```

打开 `http://127.0.0.1:8992/login`。首次启动会在终端显示一次性管理员与普通用户密码，首次登录必须改密。本模式仅使用离线演示模型，不调用外部服务。

## 局域网验证（测试模式）

仅在可信局域网中，用运行机器的实际私有 IPv4 地址显式声明浏览器 Origin；不要使用公网 IP、域名或 `*`：

```bash
AICC_LAN_ORIGIN=http://192.168.1.20:8992 \
  AICC_LOCAL_PORT=8992 \
  AICC_LOCAL_DATA="$(mktemp -d)/aicc-lan-data" \
  bash scripts/run-lan-local.sh
```

此命令会监听所有本机网卡，但只信任上述单一私有 Origin，并把测试数据放入临时目录。它不会自动打开浏览器。用同一局域网的第二台设备访问 `http://192.168.1.20:8992/login`，分别登录管理员和普通用户账号、按首次提示修改密码，再以普通用户完成一次“本地演示图片”生成。确认无误后，在启动终端按 `Ctrl-C` 停止进程，并删除该次临时数据目录。不得占用生产建议端口 `8991` 或复用生产数据目录。

## 3. 构建可迁移发布包

目标目录必须不存在且不得位于源码仓库内：

```bash
bash scripts/build-release.sh /srv/releases/ai-creation-canvas-next
cd /srv/releases/ai-creation-canvas-next
shasum -a 256 -c manifest.sha256
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

发布包包含 Python 服务和已构建静态页面，不包含 Node、数据库、上传媒体、结果或 Key。生产机器运行发布包时不需要 Node。

## 4. 准备运行目录

```bash
sudo install -d -m 0700 -o aicc -g aicc /srv/aicc/data /srv/aicc/config
sudo install -m 0600 -o aicc -g aicc server/config/credential-pools.example.json /srv/aicc/config/credential-pools.json
```

首次启动前，用文本编辑器或部署系统把占位 Key 替换为真实值。不得把 `/srv/aicc/config/credential-pools.json` 提交到 Git。

凭据 JSON 只允许这些字段：`version`、`pools`、`provider`、`group`、`allowed_families`、`keys`、`id`、`api_key`、`max_concurrency`。调用地址、模型名、适配器和参数合同由发布版本维护，不能通过 JSON 修改。

## 5. 生产启动

先为 Redis 租约生成独立 HMAC Key，再由秘密管理系统注入；它不是供应商 API Key：

```bash
export AICC_CREDENTIAL_HMAC_KEY="<至少32字节的独立随机值>"
PYTHONPATH=server .venv/bin/python -m ai_creation_canvas \
  --environment production \
  --host 127.0.0.1 \
  --port 8991 \
  --trusted-host 127.0.0.1 \
  --data-dir /srv/aicc/data \
  --portal-internal-token "<Portal内部签名令牌>" \
  --portal-base-url "https://portal.example.com" \
  --services-config server/config/services.example.json \
  --credential-pools /srv/aicc/config/credential-pools.json \
  --credential-pools-root /srv/aicc/config \
  --redis-url "redis://127.0.0.1:6379/0" \
  --static-dir web/dist
```

上线前可在同一命令末尾追加 `--check-config`。该检查验证配置并退出，不启动 HTTP 服务。

### 公网入口边界

公网架构固定为：`Internet → HTTPS 反向代理 → Portal 已登录挂载 /ai-canvas/ → Canvas 127.0.0.1`。公开域名由反向代理与 Portal 处理；Portal 完成已登录会话校验并签发身份。其受控回环代理会过滤外部 `Host`，Canvas 只信任 Portal 上游实际使用的回环 Host，因此生产启动使用 `--trusted-host 127.0.0.1`。Canvas 只验证来自受信 Portal 的签名身份，绝不接受浏览器自报用户或管理员字段，且本身仍只绑定回环地址，**不得暴露 Canvas 监听端口** 到公网，防火墙也应拒绝从互联网访问该端口。

HTTPS 反向代理负责 TLS 与 HSTS、请求体大小限制、超时、速率限制以及日志脱敏；不得记录 Cookie、签名头、完整提示词、上传内容、Key 或长期结果地址。代理只将 `/ai-canvas/` 交给 Portal 的已登录挂载，不能把请求直接转发给 Canvas。当前运行模型只支持一个 Python Canvas 服务副本；Redis 仅用于提交租约，不是持久化任务队列，不能据此启用多副本故障接管。

本仓库本次改动只提供上述接口和运行契约，**尚未配置真实服务器、域名或 DNS**。在完成独立服务器、受控 Portal、HTTPS 反代、防火墙和备份演练前，不得把发布包暴露到互联网。

## 6. 后台导入新 Key

1. 登录管理员账号，进入“模型与调用线路”。
2. 在“导入凭据池 JSON”选择本地 `.json` 文件。
3. 勾选替换确认，再点击“导入并替换凭据池”。
4. 页面只显示池 ID、Provider、分组、Key 数量和并发摘要，不会显示 Key。

浏览器不会解析文件正文或保存 Key，只把管理员主动选择的文件原样发送到同源服务。服务端先完整验证，再以 `0600` 权限原子替换 `/srv/aicc/config/credential-pools.json`。失败时旧文件和上一份有效内存快照继续使用。新任务使用新池；已提交任务保留原不可变快照，不会因换 Key 自动重放。

## 7. 模型与凭据池对应关系

| 逻辑模型 | Provider | group | family |
| --- | --- | --- | --- |
| Banana | `chiyun-banana` | `banana` | `nano-banana` |
| GPT-Image2 | `chiyun-gpt-image2` | `gpt-image` | `gpt-image2` |
| Seedream | `ark` | `official` | `seedream` |
| Seedance | `ark` | `official` | `seedance` |

同一供应商的不同分组不会互借 Key。管理员只调整 Key 和 `max_concurrency`；受信 Origin、调用协议、模型名和参数合同由代码固定。

## 8. 上线检查

```bash
bash scripts/security-scan.sh
curl -I https://portal.example.com/ai-canvas/login
```

- 管理员能登录、改密并导入占位测试 JSON。
- 普通用户访问管理员 API 得到 404。
- 管理员派发模型后，普通用户画布只看到获授权模型。
- 上传资产、生成任务、结果 HEAD/Range/完整下载均使用同一用户验证。
- 任务统计按用户 ID 出现在管理员统计页。

## 9. 备份、升级和回滚

备份 `/srv/aicc/data` 和 `/srv/aicc/config/credential-pools.json`，不要备份会话、构建缓存或测试结果。升级时构建新发布目录、校验 `manifest.sha256`、停止旧进程，再让新版本复用原数据和配置目录。回滚只切回上一发布包和匹配配置；不要手工回滚或删除 SQLite 文件。

更多并发、配额、任务恢复和故障语义见 [operations.md](operations.md)，发布验证见 [verification.md](verification.md)。

## 10. 常见问题

- 后台提示“凭据导入未配置”：启动命令缺少 `--credential-pools` 或 `--credential-pools-root`。
- JSON 被拒绝：检查扩展名、UTF-8、字段拼写、Provider/group/family 对应关系和 `max_concurrency`（1–32）。
- 模型无健康线路：确认逻辑模型与线路已启用、Redis 可用、对应池至少有一把 Key。
- `submission_unknown`：不要换 Key 重提；使用原任务查询，避免重复计费。
- 页面资源 404：确认 `--static-dir` 指向发布包内 `web/dist`。
