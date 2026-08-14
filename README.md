# AI 创作画布

AI 创作画布将 Infinite Canvas 的无限画布交互，与受控 Portal 内核结合。它面向图像生成、参考图编辑、文本生成视频、图片参考视频，以及“上传虚拟人图片资产后生成视频”的人像工作流。

## 架构与边界

- 画布界面基于 Infinite Canvas 固定提交 `9bccd0ff1a7057a835708a731644ab05371fea3b`；来源和 AGPL-3.0 说明见 [UPSTREAM.md](UPSTREAM.md)。
- Python 服务提供预构建静态页面和同源 `/api/v1` 接口；Portal 负责经过验证的身份、服务端凭据代理、资产归属、任务与用量语义。
- 图像、视频和人像资产均通过受测试的服务/模型注册表接入。增加模型、节点或工作流不应修改通用任务、身份、用量或画布存储核心。
- 浏览器不会从服务端读取、展示或保存 API Key，也不支持普通用户配置任意后端、远程插件或动态模型脚本。管理员可显式选择本地 JSON，浏览器只负责一次性原样上传。

人像不是独立模型后端：流程为受控图片上传、取得内部资产标识、确认资产激活，再提交通用图片参考视频任务。

## 隔离开发

需要 Python 3.12 和 Node（仅构建/测试时使用）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
npm ci --prefix web
PYTHONPATH=.:server .venv/bin/pytest -q tests
npm test --prefix web
npm run typecheck --prefix web
bash scripts/security-scan.sh
```

所有自动化验证使用进程内模拟 Portal/生成服务与临时数据目录。不得把配置指向生产仓库、生产状态目录或 `9090/8787/8797/8891` 等生产端口。

## 本地体验

完成 Python 依赖安装后，可用下面的脚本构建前端并启动本地验证版：

```bash
bash scripts/run-local.sh
```

服务只监听 `127.0.0.1:8992`，首次启动会在终端显示一次性管理员与普通用户密码。两类账号首次登录均需修改密码。内置 `demo-image-v1` 是完全离线的固定演示结果，不调用外部模型，也不需要 API Key。

如管理员已在**服务端环境**设置 `ARK_API_KEY`，可用下面的独立入口测试真实 Seedream 图片和 Seedance 视频。它使用另一份本地数据和 `8994` 端口，不会把 Key 发送给浏览器：

```bash
bash scripts/run-real-media-local.sh
```

首次创建的本地管理员可在管理界面把已声明的模型派发给普通用户；普通用户只能选择被派发的模型。示例目录包含 4 个 Seedream 和 4 个 Seedance 官方模型，界面参数、输入上限和组图能力均由各模型声明生成；管理员可通过受控、无密钥的模型声明文件增量调整模型能力。

提示词节点内置 4 种可选择的优化 Skill，并采用“预览后应用”流程。默认只展示 Skill，管理员设置 Ark 文本模型后才启用调用：`AICC_PROMPT_SKILL_MODEL=<文本模型ID> bash scripts/run-real-media-local.sh`。文本模型与媒体模型共用服务端 `ARK_API_KEY`，Key 不会发送或保存到浏览器。

## 可迁移安装

完整的源码安装、发布包构建、生产启动、Redis、后台 JSON 凭据导入、备份与回滚步骤见 [docs/installation.md](docs/installation.md)。凭据示例位于 `server/config/credential-pools.example.json`，只包含占位值；请复制到仓库外的受限配置目录后再替换 Key。

模型接入采用两层边界：JSON 只管理 Key、分组和并发；受信 Origin、调用协议、模型名和参数合同仍由发布代码维护。这样新增或轮换 Key 不需要改代码，也不会把任意 URL 或脚本引入生产调用链。

## 发布状态

当前版本可本地运行、构建 Python 静态发布包，并通过管理员 JSON 上传轮换服务端凭据池。正式部署仍需自行提供 Portal 身份边界、Redis、HTTPS 反向代理、数据备份和受限配置目录。运行语义见 [docs/operations.md](docs/operations.md)，验证证据要求见 [docs/verification.md](docs/verification.md)。
