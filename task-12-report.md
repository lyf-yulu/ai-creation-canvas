# Task 12 隔离验证报告

基线：`9bf9a7e`。本任务只使用进程内 `httpx.MockTransport`、FastAPI `TestClient`、临时 SQLite 数据目录和任务自有随机本地端口；未访问生产仓库、生产/既有测试端口、launchd、密钥、状态、日志、请求记录或真实模型。

## 已执行门禁

| 检查 | 结果 |
| --- | --- |
| `PYTHONPATH=.:server .venv/bin/pytest -q tests` | 254 passed（22.18s） |
| `PYTHONPATH=.:server .venv/bin/pytest -q tests/integration/test_core_flows.py` | 6 passed（19.19s） |
| `npm ci --prefix web` | 通过 |
| `npm test --prefix web` | 16 文件、88 passed |
| `npm run typecheck --prefix web` | 通过 |
| `npm run build --prefix web` | 通过 |
| `bash scripts/security-scan.sh` | 通过 |
| `git diff --check` | 通过 |
| 发布包 Node-free 运行 | 通过：临时包、受限 PATH、随机 localhost 端口；根路径和嵌套路由返回 SPA，未签名 session 为 401，进程与临时状态已清理 |

核心流程测试逐项经过公开 API、真实 Canvas 路由/SQLite/Portal 适配器和进程内服务模拟：图像生成、参考图编辑、文本视频、图片参考视频、人像上传激活后的视频生成。每项验证异步轮询、结果读取、一次底层用量事件，以及第二用户不能读取资产、任务或结果。

发布脚本从任意工作目录接受新目录，构建并校验静态资源，拒绝既有、符号链接和与源码重叠的目标，打包前扫描敏感/运行时文件，并生成不含时间、主机路径、用户名或密钥的确定性 SHA-256 清单。

## 已知非阻断提示

- `npm ci` 报告 20 个依赖审计项（2 low、9 moderate、9 high）；本任务未做依赖升级，避免偏离固定上游/锁定依赖验证范围。
- Vite 构建提示一个动态/静态重复导入以及一个超过 500 kB 的前端 chunk；构建成功，属于后续性能优化事项。

## 明确未执行

真实模型调用、真实 Portal/生成服务连接、生产端口或进程前后比较均为 **NOT RUN — awaiting explicit user approval**。没有创建、推送或公开 GitHub 仓库。
