# AI 创作画布

AI 创作画布将 Infinite Canvas 的无限画布交互，与受控 Portal 内核结合。它面向图像生成、参考图编辑、文本生成视频、图片参考视频，以及“上传虚拟人图片资产后生成视频”的人像工作流。

## 架构与边界

- 画布界面基于 Infinite Canvas 固定提交 `9bccd0ff1a7057a835708a731644ab05371fea3b`；来源和 AGPL-3.0 说明见 [UPSTREAM.md](UPSTREAM.md)。
- Python 服务提供预构建静态页面和同源 `/api/v1` 接口；Portal 负责经过验证的身份、服务端凭据代理、资产归属、任务与用量语义。
- 图像、视频和人像资产均通过受测试的服务/模型注册表接入。增加模型、节点或工作流不应修改通用任务、身份、用量或画布存储核心。
- 浏览器不会保存或接收 API Key，也不支持普通用户配置任意后端、远程插件或动态模型脚本。

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

## 发布状态

当前是隔离验证版本，不代表生产部署，也未执行真实模型调用。公开 GitHub 发布须在隔离验证完成后另行获得用户书面批准。运行、回滚和 Portal 补丁流程见 [docs/operations.md](docs/operations.md)，验证证据要求见 [docs/verification.md](docs/verification.md)。
