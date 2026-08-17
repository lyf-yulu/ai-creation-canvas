# AI 创作画布

AI 创作画布将 Infinite Canvas 的无限画布交互，与受控 Portal 内核结合。它面向图像生成、参考图编辑、文本生成视频、图片参考视频，以及“上传虚拟人图片资产后生成视频”的人像工作流。当前版本 `v0.13.0`。

## 功能一览

| 能力 | 说明 | 需要的外部 Key / 配置 |
| --- | --- | --- |
| 无限画布 | 节点、连线、多项目、视角与缩放持久化 | 无 |
| 本地演示图片 | 完全离线的固定演示结果，不联网不付费 | 无 |
| Seedream 图片生成 / 编辑 | 4 个官方模型，支持参考图、组图 | 火山方舟 `ARK_API_KEY` |
| Seedance 视频生成 | 文本视频、图片参考视频 | 火山方舟 `ARK_API_KEY` |
| 提示词优化 Skill | 内置 6 个 Skill，“预览后应用” | 方舟文本模型 ID（`AICC_PROMPT_SKILL_MODEL`） |
| 多供应商调用线路 | Banana、GPT-Image2、Seedream、Seedance 凭据池 | 凭据池 JSON（管理页导入） |
| 人像资产库 | 虚拟人图片 → 方舟私域资产库 → Seedance 视频 | 方舟 OpenAPI AK/SK + TOS AK/SK |
| ComfyUI 工作流库 | 导入 / 预览 / 导出工作流并派发给用户 | ComfyUI 服务声明 JSON |
| 生产并发协调 | 提交租约、并发限额 | Redis + `AICC_CREDENTIAL_HMAC_KEY` |
| 生产身份接入 | Portal 已登录挂载 `/ai-canvas/` | Portal 内部令牌 + 服务声明 |

## 架构与安全边界

- 画布界面基于 Infinite Canvas 固定提交 `9bccd0ff1a7057a835708a731644ab05371fea3b`；来源和 AGPL-3.0 说明见 [UPSTREAM.md](UPSTREAM.md)。
- Python 服务提供预构建静态页面和同源 `/api/v1` 接口；Portal 负责经过验证的身份、服务端凭据代理、资产归属、任务与用量语义。
- 图像、视频和人像资产均通过受测试的服务/模型注册表接入。增加模型、节点或工作流不应修改通用任务、身份、用量或画布存储核心。
- **浏览器不会从服务端读取、展示或保存 API Key**，也没有 Key 输入框；管理员显式选择本地 JSON 时，浏览器只负责一次性原样上传，不解析、不保存、不回显。
- 普通用户不能配置任意后端、远程插件或动态模型脚本；日志不记录 Key、Cookie、完整提示词、上传内容与长期结果地址。

人像不是独立模型后端：流程为受控图片上传、取得内部资产标识、确认资产激活，再提交通用图片参考视频任务。

## 快速开始：离线体验

需要 Python 3.12（运行），以及 Node 与 npm（仅构建/测试时使用）：

```bash
git clone <你的仓库地址> ai-creation-canvas
cd ai-creation-canvas
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
bash scripts/run-local.sh
```

- 脚本会锁定前端依赖、构建真实 UI，并仅在 `127.0.0.1:8992` 启动本地服务，自动打开浏览器。
- 首次启动会在终端显示 `canvas-admin` 与 `canvas-user` 两个账号的一次性随机密码，**首次登录必须修改密码**；后续启动不再显示旧密码。
- 内置 `demo-image-v1` 是完全离线的固定演示结果，不调用外部模型，也不需要任何 Key。
- 本地数据保存在已被 Git 忽略的 `.local-data/`。忘记本地测试密码可停止服务后重置（只显示一次新密码，并撤销该账号既有会话）：

```bash
PYTHONPATH=server .venv/bin/python -m ai_creation_canvas reset-local-password \
  --data-dir .local-data --username canvas-user
```

也可以直接创建一个长期账号（无需改密、可指定角色；省略 `--password` 时会在终端无回显地询问）：

```bash
PYTHONPATH=server .venv/bin/python -m ai_creation_canvas create-local-user \
  --data-dir .local-data --username 新账号 --display-name "显示名" \
  --password 自定义密码 --role user
```

日常使用流程：管理员登录后在管理界面把模型派发给普通用户；普通用户登录后只能在画布中选择被派发的模型。

## 接入各种 Key 与功能

所有真实 Key 都只存在于服务端环境或仓库外的管理员受限 JSON 中。下表是总览，后面逐项说明：

| 功能 | Key / 配置放哪里 | 前端是否接触 Key |
| --- | --- | --- |
| Seedream / Seedance（本地验证） | 服务端环境变量 `ARK_API_KEY` | 否 |
| 提示词优化 Skill | 启动参数 `--prompt-skill-model`（复用 `ARK_API_KEY`） | 否 |
| 多供应商凭据池（生产） | 仓库外 `credential-pools.json`，管理页导入 | 否（只显示摘要） |
| 人像资产库 | 仓库外 `asset-library.json`，管理页导入 | 否（只显示 `has_*` 摘要） |
| ComfyUI 服务 | 仓库外 `comfyui-services.json` | 否 |
| Redis 租约 HMAC（生产） | 服务端环境变量 `AICC_CREDENTIAL_HMAC_KEY` | 否 |
| Portal 身份（生产） | 启动参数 `--portal-internal-token` 等 | 否 |

### 1. 火山方舟：真实 Seedream 图片与 Seedance 视频

本地验证真实生成（会产生费用）使用独立入口，端口 `8994`、数据目录 `.local-real-media-data/`，与离线演示完全隔离：

```bash
ARK_API_KEY=你的方舟APIKey bash scripts/run-real-media-local.sh
```

- 脚本要求 `ARK_API_KEY` 已设置在**启动服务的终端环境**中；Key 不会发送或保存到浏览器。
- 模型目录来自 `server/config/ark-models.example.json`（可用 `AICC_ARK_MODELS_CONFIG` 覆盖，声明文件不含 Key、URL 或脚本）：内置 4 个 Seedream（4.0 / 4.5 / 5.0 Lite / 5.0 Pro）与 4 个 Seedance（2.0 / 2.0 Fast / 2.0 Mini / 2.5）官方模型；界面参数、输入上限和组图能力均由各模型声明生成。
- 首次创建的管理员在管理界面把已声明的模型派发给普通用户；普通用户只能选择被派发的模型。
- 真实任务结果先由服务端下载到受限本地结果目录，再以同源、按用户归属的结果接口提供给画布。
- 生产环境不要用这个入口：改用下面第 3 节的凭据池 JSON，Key 同样只放服务端。

### 2. 提示词优化 Skill（方舟文本模型）

提示词节点内置 6 个优化 Skill（精准结构、摄影写实、商业产品、电影动态、角色连续性、平面海报），采用“预览后应用”流程。

- **默认只展示、不可执行**。管理员设置文本模型后才启用调用：

```bash
ARK_API_KEY=... \
AICC_PROMPT_SKILL_MODEL=<已开通的Ark文本模型ID> \
bash scripts/run-real-media-local.sh
```

- 文本模型与媒体模型共用服务端 `ARK_API_KEY`；一键优化只把仓库内置 Skill 指令和当前提示词发送给固定的方舟文本接口。
- 浏览器仍不接触 Key，也不能自定义指令、模型或服务地址。生产环境使用 `--prompt-skill-model` 参数。

### 3. 凭据池：多供应商调用线路（管理页导入）

生产环境把真实 Key 从代码和启动参数中剥离：管理员在页面上一键导入/替换，新任务立即使用新池，**不用改代码、不用重启**。

结构参考 `server/config/credential-pools.example.json`（只含占位值；复制到仓库外受限目录后再替换 Key）：

```json
{
  "version": 1,
  "pools": {
    "seedream-ark": {
      "provider": "ark",
      "group": "official",
      "allowed_families": ["seedream"],
      "keys": [
        {"id": "seedream-primary", "api_key": "替换为真实Key", "max_concurrency": 1}
      ]
    }
  }
}
```

允许的字段只有 `version`、`pools`、`provider`、`group`、`allowed_families`、`keys`、`id`、`api_key`、`max_concurrency`。受信 Origin、调用协议、模型名和参数合同仍由发布代码维护，JSON 不能引入任意 URL 或脚本。

逻辑模型与池的对应关系（四元组必须精确匹配，同一供应商的不同分组不互借 Key）：

| 逻辑模型 | provider | group | family |
| --- | --- | --- | --- |
| Banana | `chiyun-banana` | `banana` | `nano-banana` |
| GPT-Image2 | `chiyun-gpt-image2` | `gpt-image` | `gpt-image2` |
| Seedream | `ark` | `official` | `seedream` |
| Seedance | `ark` | `official` | `seedance` |

导入步骤：管理员登录 →「模型与调用线路」→「导入凭据池 JSON」→ 选择本地文件 → 勾选替换确认 →「导入并替换凭据池」。页面只显示池 ID、Provider、分组、Key 数量和并发摘要，**绝不显示 Key**。

行为语义：

- `max_concurrency` 为每把 Key 的并发限额（1–32）；默认全站 8、每 Provider 4、每用户 2，可按容量调整。
- 服务端先完整验证，再以 `0600` 权限原子替换；失败时旧文件和上一份有效内存快照继续使用。
- 生产受管线路必须配置 Redis 与 `AICC_CREDENTIAL_HMAC_KEY`（见第 6 节）。
- **轮换 Key 不会重放任务**：新任务用新池；已提交任务保留创建时的不可变快照和原 Key 指纹。原 Key 被移除后，未终态任务的 worker 会失败关闭并延迟重试，不借用其他 Key、不切换线路、不重新提交——因此轮换后请保留旧池配置，直到对应任务全部终态（详见“升级注意事项”）。
- 明确收到 429 且确认未创建上游任务时，才可在同池内换 Key 重试；发送后响应不明进入 `submission_unknown`，禁止自动换 Key 或跨线路重发，避免重复计费。

### 4. 人像资产库（方舟 OpenAPI AK/SK + TOS）

人像工作流把上传的人像交给火山方舟私域虚拟人像素材资产库（AIGC 分组）：服务端用方舟 OpenAPI AK/SK 与 TOS 凭据完成签名上传与 `CreateAsset` 入库；资产审核通过后，Seedance 视频任务以 `asset://<id>` 引用生成——这是使用私域人像的官方通道。

结构参考 `server/config/asset-library.example.json`，允许字段只有：`version`、`ark_access_key`、`ark_secret_key`、`tos_access_key`、`tos_secret_key`、`tos_bucket`、`tos_region`、`project_name`。

- 方舟控制台发放的 SK 若为 base64 编码，导入时会自动解码；bucket 只允许小写字母、数字、点和连字符。
- 导入：管理页「人像资产库」→ 导入 JSON；页面只显示 `has_*` 布尔、存储桶、区域与默认分组，绝不回显密钥。
- **未配置时，画布中的人像资产库上传返回 503，不会回退第三方图床。**
- 换入新配置后对后续上传立即生效，无需重启。此文件不得提交到 Git。

### 5. ComfyUI 服务声明

工作流库允许管理员导入、预览、导出 ComfyUI 工作流，并派发给指定用户；仓库自写模板与外部工作流同等处理。

结构参考 `server/config/comfyui-services.example.json`：

```json
{
  "services": [
    {
      "service_id": "comfy-local",
      "base_url": "http://127.0.0.1:8188",
      "timeout_seconds": 10,
      "auth_header_ref": null
    }
  ]
}
```

- `timeout_seconds` 范围 1–60；`auth_header_ref` 是部署系统的凭据引用名（可选，≤128 字符），由服务端解析，浏览器不可见。
- 本地验证：`AICC_COMFYUI_SERVICES=/绝对路径/comfyui-services.json bash scripts/run-local.sh`。声明必须是**新数据目录 `config/` 内的普通非符号链接文件**，测试模式只允许 `127.0.0.1` / `::1` 数值回环 URL。生产使用 `--comfyui-services` 参数。
- 工作流 JSON 中的控制 URL（base/service/callback/webhook/server 及各类 endpoint）、认证、凭据、密钥和脚本字段会被递归拒绝；导入导出只做本地解析与重新编码。

### 6. Redis 与 HMAC Key（生产）

- 生产环境只要启用了受管模型，就必须配置 `--redis-url`。
- 同时为 Redis 租约生成独立 HMAC Key：`export AICC_CREDENTIAL_HMAC_KEY="<至少32字节的独立随机值>"`，由秘密管理系统注入。它不是供应商 API Key。
- Redis 只保存带过期时间的匿名执行许可与计数，不保存 Key、池名、分组、用户 ID、提示词、Cookie 或媒体；SQLite 仍是幂等键、任务快照、授权与审计的最终依据。
- **Redis 是跨进程提交租约，不是持久化任务队列**：当前只支持单个 Python Canvas 服务副本，不能据此启用多副本故障接管。

### 7. Portal 生产接入

公网架构固定为：`Internet → HTTPS 反向代理 → Portal 已登录挂载 /ai-canvas/ → Canvas（仅监听 127.0.0.1）`。公开域名、TLS、HSTS、限流与日志脱敏由反向代理与 Portal 处理；Canvas 不直接对公网提供端口。

- `--portal-internal-token`：Portal 与 Canvas 之间的内部签名令牌。
- `--portal-base-url`：受信 Portal 地址。
- `--services-config`：无密钥服务声明模板（`server/config/services.example.json`），mount 与服务标识必须替换并经审批。
- `--trusted-host 127.0.0.1`：Canvas 只信任 Portal 上游实际使用的回环 Host；用户身份必须来自 Portal 服务端会话或经验证的签名身份，浏览器自报的用户/管理员字段一律忽略。
- 完整启动命令见“生产部署概要”与 [docs/installation.md](docs/installation.md)。

## 部署给局域网的人使用

按用途分为两个入口。

### 用途 A：局域网内验证 / 离线演示（官方测试入口）

只应在可信局域网中使用，并且必须使用测试端口和全新的临时数据目录。示例 IP 请替换为运行机器的实际私有 IPv4 地址：

```bash
# 1. 查本机私有 IP（macOS 示例；Linux 用 ip -4 addr show）
ipconfig getifaddr en0

# 2. 启动
AICC_LAN_ORIGIN=http://192.168.1.20:8992 \
  AICC_LOCAL_PORT=8992 \
  AICC_LOCAL_DATA="$(mktemp -d)/aicc-lan-data" \
  bash scripts/run-lan-local.sh
```

- 脚本默认监听 `0.0.0.0`，但把 CSRF/Origin 边界锁定为 `AICC_LAN_ORIGIN`（只接受 `http` + 私有 IPv4 或 `.local` 主机名，不接受公网 IP、域名或 `*`）；**不会自动打开浏览器**。
- 用同一局域网的第二台设备访问 `http://192.168.1.20:8992/login`：先以 `canvas-admin` 登录并按提示修改初始密码，再以 `canvas-user` 登录并修改初始密码；确认普通用户只看到被授权内容后，完成一次离线“本地演示图片”生成。
- 脚本会拒绝 `8991/9090/8787/8797/8891` 等保留端口；不要把 LAN 验证指向生产数据、生产 Portal 或任何生产服务端口。
- macOS 首次监听 `0.0.0.0` 可能弹出防火墙授权；若第二台设备打不开页面，检查系统防火墙是否放行对应 Python 进程、两台设备是否在同一网段、Origin 里的 IP 是否与浏览器地址完全一致。
- 结束：在启动终端按 `Ctrl-C`，确认监听停止后删除该次 `AICC_LOCAL_DATA` 临时目录。
- 此入口是验证/测试模式（只有 bootstrap 的一管理员一普通用户两个账号），不用于互联网发布，也不作为长期共享服务。

### 用途 B：局域网内长期使用真实模型（可信局域网）

“真实模型入口”与“LAN Origin”可以组合使用（两者基于同一 `serve-local` 命令，只是把脚本里的参数显式写出来）：

```bash
ARK_API_KEY=你的方舟APIKey \
PYTHONPATH=server .venv/bin/python -m ai_creation_canvas serve-local \
  --host 0.0.0.0 \
  --port 8994 \
  --public-origin http://192.168.1.20:8994 \
  --data-dir /受控/局域网数据目录 \
  --static-dir web/dist \
  --ark-models server/config/ark-models.example.json \
  --prompt-skill-model "<已开通的Ark文本模型ID>" \
  --bootstrap-if-empty
```

- `--public-origin` 与用途 A 一样只接受 `http` + 私有 IPv4 / `.local` 主机名，只应在可信局域网内使用，并注意保护方舟 Key 与账单。
- 局域网内长期多用户正式使用，建议按“生产部署概要”走 Portal + HTTPS 反向代理的路径，而不是长期挂 LAN 测试入口。

### 用途 C：本机反向代理（nginx 等，localhost 与局域网并存）

在同一台机器上挂 nginx 转发到 `127.0.0.1:8992` 时，浏览器看到的是 nginx 的地址，而服务默认只信任 `http://127.0.0.1:8992`：页面能打开，但改密等写操作会因 Host/Origin 不符被拒（400/403）。两个必要条件：

1. 启动时把**浏览器实际看到的完整地址**声明为 public origin（协议、IP、端口必须与浏览器地址栏一致）：

```bash
# localhost 直连与局域网/nginx 地址同时可用
AICC_LOCAL_ORIGINS="http://127.0.0.1:8992 http://192.168.1.20:8992" \
  bash scripts/run-local.sh
```

2. nginx 必须把客户端的 Host 原样转发，否则转发的是 `127.0.0.1:8992`，仍会被拒（400）：

```nginx
location / {
    proxy_pass http://127.0.0.1:8992;
    proxy_set_header Host $host;
}
```

- 每个 origin 只接受 `http` + 私有 IPv4 / `.local` 主机名；域名、公网 IP、`https://` 均不被接受。需要 HTTPS 或公网域名时，请走第 7 节“Portal 生产接入”（TLS 由反向代理终结，身份由 Portal 签名提供）。
- 反向代理不是必需的：不需要 localhost 与 LAN 并存时，直接用“用途 A”的 `run-lan-local.sh`（监听 `0.0.0.0`）即可，局域网设备无需 nginx 也能访问。

### 用户注册与审核（仅本地/LAN 模式）

本地/LAN 模式开放自助注册，账号需管理员审核通过后才能登录：

1. 用户在登录页点“没有账号？注册”，提交用户名、显示名称和自定义密码（密码至少 12 个字符）。
2. 注册成功后账号进入**待审核**状态，此时登录会得到与错误密码相同的通用提示，不会泄露账号是否存在。
3. 管理员登录后在「账号管理 → 待审核注册」看到申请列表，可**通过**或**拒绝**：通过后账号立即可登录；拒绝会删除该注册（含密码哈希），操作记入管理员审计。
4. **审核通过只开通登录，不派发任何模型**——管理员仍需在「模型派发」中为该用户分配模型。
5. 注册端点有进程内按 IP 限流（每 IP 每小时 10 次，超出返回 429）；该限制是单进程尽力而为的，不适用于多实例部署。

说明：bootstrap 的 `canvas-admin` / `canvas-user` 不受影响；生产 Portal 模式下注册接口与审核接口均返回 404（账号由 Portal 管理）。

## 生产部署概要

1. 构建可迁移发布包（目标目录必须不存在且不在源码仓库内）：

```bash
bash scripts/build-release.sh /srv/releases/ai-creation-canvas-next
cd /srv/releases/ai-creation-canvas-next
shasum -a 256 -c manifest.sha256
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

   发布包包含 Python 服务和已构建静态页面，不含 Node、数据库、上传媒体、结果或 Key；生产机器运行发布包不需要 Node。

2. 准备运行目录：数据目录、配置目录与发布目录分离；把凭据示例复制到仓库外并替换为真实值，`chmod 0600`，只允许服务账号访问：

```bash
sudo install -m 0600 -o aicc -g aicc server/config/credential-pools.example.json /srv/aicc/config/credential-pools.json
sudo install -m 0600 -o aicc -g aicc server/config/asset-library.example.json /srv/aicc/config/asset-library.json
```

3. 生产启动（HTTPS 反向代理与 Portal 在前，Canvas 只绑定回环地址）：

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
  --asset-library-config /srv/aicc/config/asset-library.json \
  --asset-library-config-root /srv/aicc/config \
  --redis-url "redis://127.0.0.1:6379/0" \
  --static-dir web/dist
```

   上线前可在同一命令末尾追加 `--check-config`：验证配置后退出，不启动 HTTP 服务。

4. 上传/配额参数（本地与生产同接口，均为管理员显式设置）：单文件上限图片 10 / 视频 64 / 音频 32 MiB（1–2048），`--upload-concurrency` 默认 4（1–32），单用户资产配额 2048 MiB、全站 10240 MiB。

5. 上线检查：`bash scripts/security-scan.sh`；管理员能登录改密并导入测试 JSON、普通用户访问管理员 API 得到 404、派发模型后普通用户只看到获授权模型、任务与结果按用户归属。

完整安装、Portal 边界、Redis 语义与备份回滚步骤见 [docs/installation.md](docs/installation.md)；运行语义见 [docs/operations.md](docs/operations.md)。当前版本尚未配置真实服务器、域名或 DNS。

## 后续升级注意事项

### 升级流程

1. **先备份**（见下），并阅读新版本说明与 CHANGELOG。
2. 用 `scripts/build-release.sh` 构建到**全新的发布目录**，不要覆盖正在运行的旧目录。
3. `shasum -a 256 -c manifest.sha256` 校验发布包。
4. 在新目录内建 venv 并安装 `requirements.lock`。
5. 用生产真实参数追加 `--check-config` 预检配置。
6. 正常停止旧进程（涉及模型变更时先停用受影响的逻辑模型或线路，再停服务）。
7. 启动新版本，**复用原数据目录与配置目录**（不要迁移或重建 SQLite）。
8. 验证：管理员登录与导入摘要、普通用户可见模型、既有任务继续恢复、`/healthz` 与 `/readyz` 正常。

### 回滚

- 顺序：先停用受影响的逻辑模型或线路 → 恢复上一发布包和上一份已验证的凭据池配置 → 确认 Redis 与 SQLite 健康。
- **不得手工回滚或删除 SQLite 文件**；不得让两个实例同时挂载同一 SQLite 数据目录。
- 回滚时把与未完成任务匹配的旧凭据池配置一并保留，直到任务终态并确认提供方清理完成。

### Key 轮换与进行中任务

- 轮换通过管理页导入新 JSON 完成，新任务立即使用新池，无需重启。
- 已提交任务保留创建时的不可变快照与原 Key 指纹；原 Key 被移除后 worker 失败关闭并延迟重试，**不借用其他 Key、不切换线路、不重新提交**。轮换后请保留旧池配置，直到所有未终态任务结束。
- `submission_unknown` 任务不要换 Key 重提（可能重复计费）：用相同幂等键查询原任务，等待提供方恢复查询或管理员处置；只有明确得到 `NOT_SUBMITTED` 证据才能进入新的受控重试。

### 备份

- 备份内容：数据目录（`/srv/aicc/data`）、`credential-pools.json`、`asset-library.json`；不要备份会话、构建缓存或测试临时目录。
- SQLite 必须用**在线备份 API**，或先正常停止唯一实例再复制；数据库与其引用的本地资产、结果文件必须作为**同一恢复点**处理，不能只复制正在写入的数据库文件。
- 凭据配置单独进入受限的部署密钥备份，不能混入数据目录或发布包。

### 数据库迁移

- 应用启动时执行增量迁移，不提供手工迁移脚本；升级前备份，升级后不手工改表。
- 历史未终态行的完成模式（`background` / `request`）由服务端受信适配器注册表在启动时分类；无法分类的行保持不可领取，不猜测也不重放。

### 前端上游

- Infinite Canvas 基线固定提交 `9bccd0ff1a7057a835708a731644ab05371fea3b`；未经差异审阅和回归验证不得升级上游基线（AGPL-3.0，见 [UPSTREAM.md](UPSTREAM.md)）。

## 开发与验证

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm ci --prefix web
PYTHONPATH=.:server .venv/bin/pytest -q tests
npm test --prefix web
npm run typecheck --prefix web
bash scripts/security-scan.sh
```

- 发布前的正式前端门禁：`npm run verify:release --prefix web`（依次运行 JSDOM 全量、类型检查、生产构建与 2 个真实 Chrome 响应式用例，要求本机已安装 Google Chrome）。
- 所有自动化验证使用进程内模拟 Portal/生成服务与临时数据目录；不得把配置指向生产仓库、生产状态目录或 `8991/9090/8787/8797/8891` 等保留端口。
- 正式的单次付费验收只能使用 `scripts/acceptance-real-media.sh`，且必须显式设置 `AICC_ALLOW_PAID_ACCEPTANCE=YES`；默认只跑离线门禁，不提交任务。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 后台提示“凭据导入未配置” | 启动命令缺少 `--credential-pools` 或 `--credential-pools-root` |
| 导入 JSON 被拒绝 | 检查扩展名、UTF-8、字段拼写、Provider/group/family 对应关系和 `max_concurrency`（1–32） |
| 模型无健康线路 | 确认逻辑模型与线路已启用、Redis 可用、对应池至少有一把 Key |
| 任务显示 `submission_unknown` | 不要换 Key 重提；用原任务查询，避免重复计费 |
| 页面资源 404 | 确认 `--static-dir` 指向发布包内 `web/dist` |
| 忘记本地测试密码 | 停止服务后运行 `reset-local-password`（见“快速开始”） |
| 注册后无法登录 | 账号处于“待审核”状态，需管理员在「账号管理 → 待审核注册」中通过后方可登录 |
| 局域网设备打不开 | 确认 Origin IP 是私有地址且与浏览器地址一致、脚本监听 `0.0.0.0`、系统防火墙放行 |
| 经 nginx 后登录页 400、改密 403 | 启动时用 `AICC_LOCAL_ORIGINS` 声明浏览器实际地址，且 nginx 配置 `proxy_set_header Host $host;`（见“用途 C”） |
| 人像上传返回 503 | 未导入资产库配置 JSON，或配置尚未生效（导入后无需重启） |

## 文档索引

- [docs/installation.md](docs/installation.md) — 源码安装、发布包构建、生产启动、后台导入、备份回滚
- [docs/operations.md](docs/operations.md) — 运行语义、并发与配额、任务完成模式与恢复边界、排障
- [docs/troubleshooting-lan-nginx.md](docs/troubleshooting-lan-nginx.md) — 局域网/反向代理下 400 与改密 403 的根因、解法与实测验证
- [docs/verification.md](docs/verification.md) — 自动化验证矩阵与验收记录
- [UPSTREAM.md](UPSTREAM.md)、[LICENSE](LICENSE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — 上游来源与许可证
