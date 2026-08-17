# 火山方舟私域虚拟人像资产库（AIGC）Implementation Plan

**Goal:** 管理员在服务端配置方舟 OpenAPI AK/SK 与 TOS AK/SK（仅 `has_*` 摘要暴露给浏览器），用户上传人像图（png/jpeg/webp ≤10MB）→ 服务端 TOS 签名 PUT 得到公网 URL → 方舟 OpenAPI `CreateAsset`（`asset-xxx` 上游 ID）→ `GetAsset` 轮询 → 存入 `canvas_assets`（新 kind `library`）；Seedance 视频模型 `reference_images` 端口接上资产库人像后，作业绑定转发上游 ID，Ark 适配器以 `{"type":"image_url","image_url":{"url":"asset://<id>"},"role":"reference_image"}` 原样渲染（官方私有库人像用法，绕过 Seedance 2.0 真人脸限制）。

**用户已确认范围：** ① 功能落在本仓库（无限画布应用内部）；② 只做 AIGC 虚拟人像（无 `CreateVisualValidateSession` 扫脸建档）；③ 只做 Image 资产。

## Global Constraints

- 不读取、修改或重启 `/Users/260413a/ai-generation-portable-apps`（上游仅作只读算法参考）；不连真实方舟/TOS；自动化全部使用 `httpx.MockTransport`、仓库夹具、测试端口 `8992` 和临时数据目录。
- 浏览器、数据库公开投影、日志、Git、错误体与命令行不得含 AK/SK、TOS Key、`ARK_API_KEY`；管理员界面只显示 `has_*` 布尔与桶/区域/项目名等非秘密字段。
- 签名函数使用注入时钟（`now`），签名向量测试确定性可复现；SK 永不进入日志/断言/URL/错误体。
- 用户不可提供任意 URL：CreateAsset 的 URL 只由服务端 TOS 预签名生成；对象 key 服务端生成 `refmedia/<uuid><ext>`。
- 所有数据库迁移加法式；跨用户资源 403/404；`asset://` 前缀只允许在 jobs.py 从已校验的上游 ID 注入。
- 新配置启用仅当 `Settings.asset_library_config_path` 提供；未配置时库上传返回 503（`LIBRARY_ASSETS_UNAVAILABLE`）、管理导入返回 409。
- 发布包（无 Node/Bun）中可运行；适配器构造不发请求（`--check-config` 安全）。
- TOS 为硬前置：未配置时不回退第三方图床。

## Architecture

- **复用通用上游资产机制**：`AssetKind.LIBRARY` + 注册在 `service_id="ark-video"`（与 Seedance 模型声明一致）下的资产适配器，镜像 portrait 分支骨架（bounded multipart、magic-byte、配额、finalize/恢复 journal），但无 cookie（公司级凭据在服务端）。
- **凭据形态**：`asset_library_config.py` + `asset_library_import.py`（credential_pool_import 式原子 JSON 导入：temp+fsync+os.replace、0o600、`safe_summary()`）。配置含 ark AK/SK、tos AK/SK、bucket、region、project_name（默认 `Seedance2.0`）。不走现有凭据池（单 secret 模型与 AK+SK 不匹配）。适配器经 `config_getter` 读取配置，导入轮换无需重启。
- **生成转发**：jobs.py 绑定层是唯一注入 `asset://` 前缀的地方（上游 ID 先经 `asset-[A-Za-z0-9_-]{1,100}` 校验）；Ark 适配器对 `asset://asset-*` 全匹配值原样渲染，其余走既有 data-URL 路径。端口级门控：只有声明 `asset_kind: "library"` 的端口（seedance `reference_images`）接受库资产。
- **默认分组**：AIGC 默认分组按需创建一次，持久化在 `canvas_meta`（key `ark_library_default_group_id`）。

## File Structure

- `server/ai_creation_canvas/asset_library_config.py`：严格配置模型、`parse_asset_library_config_json`、`AssetLibraryConfigLoader`（带锁 reload 保旧快照）、`safe_summary`、SK base64 归一化。
- `server/ai_creation_canvas/asset_library_import.py`：`import_asset_library_config`（原子替换）。
- `server/ai_creation_canvas/adapters/ark_assets.py`：`openapi_v4_sign`、`tos_sign_put`、`tos_presigned_get_url`（注入时钟）+ `ArkAssetLibraryAdapter`（TOS PUT → CreateAsset → 有界 GetAsset 轮询 → 默认分组按需创建；`_ARK_ASSET_ID` 供绑定层复用）。
- `server/ai_creation_canvas/domain/models.py`：`AssetKind.LIBRARY`；`ModelInputPort.asset_kind`。
- `server/ai_creation_canvas/adapters/ark.py`：端口加载器允许可选 `asset_kind`（仅 `"library"`）；`_LIBRARY_REF` 渲染。
- `server/ai_creation_canvas/storage/sqlite.py`：`finalize_library_asset`、`delete_reserved_library_asset`、`record_library_finalize_recovery`、启动恢复/失败清理、`ark_library_group_id`/`set_ark_library_group_id`、`list_library_assets_for_owner`。
- `server/ai_creation_canvas/api/assets.py`：`kind="library"` 上传分支、GET 再轮询、`GET /api/v1/library-assets`。
- `server/ai_creation_canvas/api/admin.py`：`GET /api/v1/admin/asset-library`、`POST /import`、`GET /groups`。
- `server/ai_creation_canvas/api/jobs.py`：端口级 library 绑定与 `asset://` 注入。
- `server/ai_creation_canvas/app.py`、`config.py`、`__main__.py`：装配与 `--asset-library-config/--asset-library-config-root`。
- `server/config/asset-library.example.json`、`server/config/ark-models.example.json`（seedance `reference_images` 加 `asset_kind`）。
- `web/src/api/contracts.ts`、`assets.ts`、`admin.ts`、`features/graph/contracts.ts`、`normalize-project.ts`、`components/canvas/asset-library-panel.tsx`、`pages/canvas/project.tsx`、`pages/admin/asset-library.tsx`、`router.tsx`、`product-shell.tsx`、`components/admin/model-templates.ts`。
- 测试：`tests/server/test_asset_library_config.py`、`test_asset_library_store.py`、`test_asset_library_api.py`、`test_jobs_asset_library.py`、`tests/contracts/test_ark_asset_signing.py`、`test_ark_library_adapter.py`、`test_ark_adapter.py`（增补）、`tests/integration/test_ark_library_flow.py`、`web/src/test/asset-library-panel.test.tsx`、`admin-asset-library.test.tsx`、`compile-job.test.ts`（增补）。
- `scripts/verify_asset_library.py`：离线 mock 冒烟。

## 明确未做 / 风险

- 无真实 AK/SK/TOS 调用（真实连通验证需用户批准后在部署环境手工验收）。
- 无扫脸建档；video/audio 资产类型、DeleteAsset、多分组 UI 推迟。
- TOS 未配置 → 上传 503；ProjectName 默认 `Seedance2.0`；SK base64 归一化为启发式（恰好是合法 base64 的明文 SK 会被解码——文档注明）。
- 多实例部署下默认分组 check-then-create 可能重复创建（后者覆盖 canvas_meta，可接受）。
- 上游资产 ID 只在 `canvas_assets.upstream_asset_id` 持久化；DB 丢失则方舟侧资产成孤儿（与上游一致）。
- 门禁修复：`scripts/security-scan.sh` 原用 `! rg` 取反导致命中也不失败（errexit 豁免），已改为显式 `reject` helper；`regex.exec(` 方法调用豁免于 eval/exec 检查。
