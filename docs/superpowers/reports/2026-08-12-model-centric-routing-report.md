# 模型中心路由与分组凭据池离线验收报告

**日期：** 2026-08-12
**结论：** 离线自动验收通过，可进入独立实例的用户手工验收；尚未授权或执行任何真实 Provider/付费调用。

## 验收范围与结果

- Python 全量：`661 passed`，用时 65.80 秒。覆盖真实 FastAPI、SQLite、迁移幂等、管理 API、模型目录、路由筛选、Redis Lua 协议 fake、任务幂等、结果代理及安全边界。
- Task8 后端聚焦：`3 passed`。管理员创建 Nano Banana 逻辑模型、官方线路和 T8Star `gemini` 线路；T8Star `cc` 线路被拒绝。验证同池 `official-a → official-b` 明确 429 安全轮换，以及官方池耗尽后进入 T8 `gemini`；`cc` 从未成为候选或调用目标。
- 前端 JSDOM：38 个测试文件、381 项测试通过；TypeScript 类型检查通过。
- Chromium：2 个测试文件、7 项测试通过。Task8 新增桌面 1280 px、窄屏 415 px 和 240 px；覆盖管理员创建/编辑逻辑模型、两条线路、安全池摘要、派发、归档/恢复、引用删除阻塞，以及普通用户创建对应画布模型节点。窄屏无页面级横向溢出，关键按钮保持可见。
- 生产构建：Vite 构建通过；生产依赖审计为 `0 vulnerabilities`，高危漏洞为零。
- 安全：`scripts/security-scan.sh` 与 `git diff --check` 通过。浏览器无 Key、凭据引用或任意 Base URL 控件；提交文件中无运行数据、SQLite、密钥或输出目录。

## 行为证据

1. SQLite 首次打开会增加逻辑模型、线路、审计、授权与不可变任务快照结构；迁移测试证明重复启动幂等，旧表不被破坏。
2. 普通用户目录只暴露一个逻辑模型，不暴露 Provider、线路、分组、池、Key 或指纹。
3. 两个相同用户、相同请求、相同幂等键的并发提交返回同一平台任务；提供方提交不重复。
4. 两张参考图按上传及请求顺序进入 multipart，保持画布 `@图片1`、`@图片2` 的语义。
5. 明确 429 只在确认未创建上游任务时换 Key/线路；模糊读取超时只调用一次并进入 `submission_unknown`，不会跨 Key 或跨线路重放。
6. 路由、路由版本、池版本摘要与 HMAC Key 指纹写入不可变快照；快照和 Redis 命令不含 Key、Key ID、提示词、媒体或分组原文。
7. 成功结果验证 GET、HEAD 与单段 Range；跨用户任务/结果均隐藏为 404。撤权立即阻止新任务，但不破坏已归属结果读取。
8. 管理生命周期验证 revision、归档/恢复和有引用删除的安全类别计数；不返回具体任务、账号或凭据值。

## 发布包

- 完整构建：`/private/tmp/aicc-task8-full-fixed.sTKG0z/release`
- 已验证静态产物构建：`/private/tmp/aicc-task8-skip.V6LfAW/release`
- 两条路径均包含 `manifest.sha256` 与 Python-only 入口，且 `shasum -a 256 -c manifest.sha256` 完整通过。
- 验收过程中发现并修复发布清单曾包含随后删除的临时 nonce 标记；新增回归测试，确保发布包自校验不再缺文件。

## 隔离与费用

- Provider 请求全部由 `httpx.MockTransport` 在进程内响应；外部 Provider 请求：0。
- Redis 使用执行生产 Lua 合同的内存协议 fake，没有打开 Redis 网络端口；不将它描述为真实 Redis 集成。
- 真实 Key 读取：0；真实提示词/媒体/结果 URL 记录：0；付费调用：0。
- 未使用或修改 8997、9001、9002 及其数据；自动测试只使用临时目录和进程内 ASGI。

## 已知边界

- Redis 当前提供跨进程的提交租约和容量控制，不是持久化任务队列。进程崩溃后的自动接管、多区域 durable worker/Redis Streams 尚未实现。
- Vite 仍报告既有的静态/动态导入重叠提示，主 JavaScript chunk 为 882.38 kB（gzip 285.91 kB），超过 500 kB 建议阈值；不影响当前轻量验收，但上线前可按页面拆分优化首屏加载。
- 本报告只证明离线合同与 UI；真实 Provider 错误格式、计费、地域网络和模型权限必须由用户另行批准一次有界小额验收。
