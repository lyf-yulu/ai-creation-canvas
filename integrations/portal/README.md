# Portal 薄代理测试集成

此目录是 **AI 创作画布** 与 Portal 的最小、可审阅集成材料。它不包含
Portal、图像、视频或人像服务的源码、配置、密钥、状态或输出。

## 边界

- 仅允许 Portal 的已登录会话访问 `/ai-canvas/`；目标固定为
  `http://127.0.0.1:8992`，浏览器不能选择上游地址。
- Portal 在转发前删除所有浏览器提供的 `X-Portal-*` 身份和签名头，再从
  已认证会话中的 `user_id`、`role` 与 `username` 签发 v2 身份。v2 的规范
  载荷为 `v2\\n{timestamp}\\n{user_id}\\n{role}\\n{rfc3986(username)}`，用
  HMAC-SHA256 计算，验证端须使用常量时间比较和过期窗口。
- 生成请求仍通过 Portal 的受控服务。画布不产生 `X-Job-Id` 或第二份用量
  事件，因此每个底层生成任务只记一次 Portal 用量。
- `signed-identity-v2.patch` 是针对其文件上下文精确匹配的 Portal `app.py`
  形状的窄补丁；任何上下文或版本不匹配都必须失败，不能手工模糊套用。

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
