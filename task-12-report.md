# Task 12 隔离验证报告

基线：`9bf9a7e`。本任务只使用进程内 `httpx.MockTransport`、FastAPI `TestClient`、临时 SQLite 数据目录和任务自有随机本地端口；未访问生产仓库、生产/既有测试端口、launchd、密钥、状态、日志、请求记录或真实模型。

## 已执行门禁

| 检查 | 结果 |
| --- | --- |
| Python 测试分组执行 | 257 passed：非集成 235、Portal 集成 13、核心隔离流 9 |
| `tests/integration/test_core_flows.py` | 9 passed；包括发布包专项、Node-free、失败清理与完整 dist 清单回归 |
| `npm ci --prefix web` | 通过 |
| `npm test --prefix web` | 16 文件、88 passed |
| `npm run typecheck --prefix web` | 通过 |
| `npm run build --prefix web` | 通过 |
| `bash scripts/security-scan.sh` | 通过 |
| `git diff --check` | 通过 |
| 发布包 Node-free 运行 | 通过：临时包、受限 PATH、随机 localhost 端口；验证的 `--skip-web-build` 包可启动，根路径和嵌套路由返回 SPA，未签名 session 为 401，进程与临时状态已清理 |

核心流程测试逐项经过公开 API、真实 Canvas 路由/SQLite/Portal 适配器和进程内服务模拟：图像生成、参考图编辑、文本视频、图片参考视频、人像上传激活后的视频生成。每项验证异步轮询、结果读取、一次底层用量事件，以及第二用户不能读取资产、任务或结果。人像结果由生产 `PortalPortraitAdapter` 仅接受不透明 `result_ref`，并通过受控 Portal 路由支持 GET、HEAD 和 Range；外部 URL 或畸形结果标识被拒绝。

发布脚本从任意工作目录接受新目录，构建并校验静态资源，拒绝既有、符号链接和与源码重叠的目标，打包前扫描敏感/运行时文件，并生成不含时间、主机路径、用户名或密钥的确定性 SHA-256 清单。正常构建以原子写入方式记录源码指纹及完整 `web/dist` 文件清单（规范相对路径与逐文件 SHA-256）；`--skip-web-build` 会重新枚举所有普通文件，拒绝缺失、增加、改动、符号链接、特殊文件和禁止文件。临时副本回归覆盖改动 `index.html`、改动带哈希 JavaScript、增加 `evil.js`、删除资源四种情形，均拒绝且清理新目标；未改产物可成功 skip。复制进发布包后再次验证静态清单。构建/复制失败只清理本次新建且带私有标记的目标，绝不删除预存目录。

## 已知非阻断提示

- `npm ci` 报告 20 个依赖审计项（2 low、9 moderate、9 high）；本任务未做依赖升级，避免偏离固定上游/锁定依赖验证范围。
- Vite 构建提示一个动态/静态重复导入以及一个超过 500 kB 的前端 chunk；构建成功，属于后续性能优化事项。

## 明确未执行

真实模型调用、真实 Portal/生成服务连接、生产端口或进程前后比较均为 **NOT RUN — awaiting explicit user approval**。没有创建、推送或公开 GitHub 仓库。
